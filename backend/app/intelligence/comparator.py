"""Structured comparison between a production scenario and candidate tests.

This is deliberately deterministic: every fact the classifier later relies on is
computed here from extracted values, never from model prose. That is what makes
the "Evidence" section of a result trustworthy — and what stops BlindSpot from
ever hallucinating that a test covers something.

The key subtlety is **per-test** versus **union** coverage. Three tests that
separately cover `timeout`, `retry` and `payment failure` do *not* cover
"immediate retry after timeout". Coverage is therefore decided against the best
single test; the union is tracked only to detect exactly that combination gap.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..domain.models import NormalizedIncident, NormalizedTest, RetrievedTest
from ..domain.text import as_number, normalize, values_equivalent
from .extraction import derive_test_signals

#: Signals that on their own justify a gap type, i.e. a test that does not
#: exercise one is not coverage for an incident that did.
#:
#: `large_input` and `data_format` stay out: they fire on common words
#: ("format", "long") and would manufacture gaps from ordinary prose.
DECISIVE_SIGNALS: tuple[str, ...] = (
    "null",
    "empty",
    "missing_field",
    "invalid",
    "boundary_max",
    "boundary_min",
    "timeout",
    "retry",
    "concurrency",
    "permission",
    "unicode",
    "error_handling",
    # Both describe a condition a test must actually set up to count as
    # coverage, so an untested ordering or environment is a real gap rather
    # than a footnote on an otherwise "partial" verdict.
    "state_transition",
    "environment",
)

#: Signals implying an ordering of events rather than a single input value.
SEQUENCE_SIGNALS: frozenset[str] = frozenset(
    {"retry", "timeout", "concurrency", "state_transition"}
)

#: Condition values that *are* a signal. When production says `name = null`, the
#: null-handling signal belongs to `name` specifically — a test that passes a
#: null *email* is not coverage for a null *name*.
VALUE_SIGNALS: dict[str, str] = {
    "null": "null",
    "none": "null",
    "nil": "null",
    "undefined": "null",
    "empty": "empty",
    "blank": "empty",
    "missing": "missing_field",
    "absent": "missing_field",
    "invalid": "invalid",
    "malformed": "invalid",
}


@dataclass
class ConditionComparison:
    """How one production condition fared against one test."""

    key: str
    production_value: str
    test_value: str | None = None
    status: str = "ABSENT"  # MATCHED | DIFFERENT | ABSENT
    production_number: float | None = None
    test_number: float | None = None
    #: Every numeric value the test exercises for this input. A parametrized
    #: test contributes several, and all of them matter to boundary analysis.
    test_numbers: list[float] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.status == "MATCHED"


@dataclass
class TestComparison:
    """The full comparison of the incident against a single candidate test."""

    test: NormalizedTest
    score: float
    feature_match: bool = False
    scenario_overlap: float = 0.0
    conditions: list[ConditionComparison] = field(default_factory=list)
    matched_signals: list[str] = field(default_factory=list)
    missing_signals: list[str] = field(default_factory=list)

    @property
    def matched_conditions(self) -> list[ConditionComparison]:
        return [c for c in self.conditions if c.status == "MATCHED"]

    @property
    def differing_conditions(self) -> list[ConditionComparison]:
        return [c for c in self.conditions if c.status == "DIFFERENT"]

    @property
    def absent_conditions(self) -> list[ConditionComparison]:
        return [c for c in self.conditions if c.status == "ABSENT"]

    @property
    def covers_all_conditions(self) -> bool:
        return bool(self.conditions) and all(c.matched for c in self.conditions)

    @property
    def covers_all_decisive_signals(self) -> bool:
        return not self.missing_signals

    def coverage_strength(self) -> float:
        """0..1 — how completely this one test represents the incident."""
        parts: list[float] = []
        if self.conditions:
            parts.append(sum(1.0 for c in self.conditions if c.matched) / len(self.conditions))
        decisive_total = len(self.matched_signals) + len(self.missing_signals)
        if decisive_total:
            parts.append(len(self.matched_signals) / decisive_total)
        if not parts:
            # Nothing structured to compare — fall back to topical similarity.
            return (0.6 if self.feature_match else 0.2) * min(1.0, self.score + self.scenario_overlap)
        strength = sum(parts) / len(parts)
        return strength * (1.0 if self.feature_match else 0.7)


@dataclass
class ScenarioComparison:
    """Aggregate view across every candidate test."""

    incident: NormalizedIncident
    comparisons: list[TestComparison] = field(default_factory=list)
    decisive_signals: list[str] = field(default_factory=list)

    @property
    def judged(self) -> list[TestComparison]:
        """The candidates coverage is actually decided against.

        Same-feature tests when any were retrieved, otherwise everything. A
        timeout test in Authentication is not timeout coverage for a Profile
        incident, and counting it as such was silently turning genuinely
        uncovered scenarios into "partially covered".
        """
        same_feature = [c for c in self.comparisons if c.feature_match]
        return same_feature or self.comparisons

    @property
    def best(self) -> TestComparison | None:
        return max(self.judged, key=lambda c: (c.coverage_strength(), c.score), default=None)

    @property
    def any_feature_match(self) -> bool:
        return any(c.feature_match for c in self.comparisons)

    def union_matched_condition_keys(self) -> set[str]:
        return {c.key for comp in self.judged for c in comp.matched_conditions}

    def union_present_condition_keys(self) -> set[str]:
        """Condition keys any judged test exercises at all, even at another value."""
        return {
            c.key
            for comp in self.judged
            for c in comp.conditions
            if c.status in {"MATCHED", "DIFFERENT"}
        }

    def union_matched_signals(self) -> set[str]:
        return {s for comp in self.judged for s in comp.matched_signals}

    def unmatched_signals(self) -> list[str]:
        """Decisive signals no candidate test exercises at all."""
        covered = self.union_matched_signals()
        return [s for s in self.decisive_signals if s not in covered]

    def has_combination_gap(self) -> bool:
        """True when the pieces are tested separately but never together.

        This is the "timeout + immediate retry" case: the union covers
        everything, yet no single test does.
        """
        if len(self.decisive_signals) < 2 or not self.judged:
            return False
        if self.unmatched_signals():
            return False  # something is missing outright; not a combination gap
        return all(comp.missing_signals for comp in self.judged)

    def tested_values_for(self, key: str) -> list[str]:
        """Every value the judged tests exercise for a condition key."""
        values: list[str] = []
        for comp in self.judged:
            for condition in comp.conditions:
                if condition.key == key and condition.test_value is not None:
                    values.append(condition.test_value)
        return list(dict.fromkeys(values))

    def tested_numbers_for(self, key: str) -> list[float]:
        numbers: list[float] = []
        for comp in self.judged:
            for condition in comp.conditions:
                if condition.key == key:
                    numbers.extend(condition.test_numbers)
        return sorted(set(numbers))


class ScenarioComparator:
    """Builds a `ScenarioComparison` from an incident and its candidate tests."""

    def compare(
        self, incident: NormalizedIncident, candidates: list[RetrievedTest]
    ) -> ScenarioComparison:
        decisive = [s for s in incident.signals if s in DECISIVE_SIGNALS]
        result = ScenarioComparison(incident=incident, decisive_signals=decisive)

        incident_feature = normalize(incident.feature)
        scenario_tokens = set(normalize(incident.scenario).split()) | set(
            normalize(incident.title).split()
        )
        key_bound = self._key_bound_signals(incident)

        for candidate in candidates:
            test = candidate.test
            # Normally precomputed at index time; recomputed here so the
            # comparator is correct even for a test that never went through one.
            test_signals = set(test.extra.get("signals") or []) or set(
                derive_test_signals(test.name, test.scenario, test.inputs)
            )

            conditions = self._compare_conditions(incident, test)
            matched = [
                s
                for s in decisive
                if s in test_signals and self._signal_applies(s, key_bound, conditions)
            ]
            comparison = TestComparison(
                test=test,
                score=candidate.score,
                feature_match=normalize(test.feature) == incident_feature and bool(incident_feature),
                scenario_overlap=self._overlap(scenario_tokens, test),
                conditions=conditions,
                matched_signals=matched,
                missing_signals=[s for s in decisive if s not in matched],
            )
            result.comparisons.append(comparison)

        return result

    # -- helpers ------------------------------------------------------------

    def _compare_conditions(
        self, incident: NormalizedIncident, test: NormalizedTest
    ) -> list[ConditionComparison]:
        """Compare every production condition against the test's inputs."""
        comparisons: list[ConditionComparison] = []
        test_inputs = {normalize(k): v for k, v in test.inputs.items()}

        for raw_key, production_value in incident.conditions.items():
            key = normalize(raw_key)
            comparison = ConditionComparison(
                key=raw_key,
                production_value=str(production_value),
                production_number=as_number(production_value),
            )

            aligned = self._align_key(key, test_inputs)
            if aligned is not None:
                test_value = test_inputs[aligned]
                comparison.test_value = str(test_value)
                # A parametrized test may list several values for one input.
                candidates = self._split_values(str(test_value))
                comparison.test_numbers = [
                    number for number in (as_number(v) for v in candidates) if number is not None
                ]
                comparison.test_number = (
                    comparison.test_numbers[0] if comparison.test_numbers else None
                )
                if any(values_equivalent(production_value, v) for v in candidates):
                    comparison.status = "MATCHED"
                else:
                    comparison.status = "DIFFERENT"
            comparisons.append(comparison)

        return comparisons

    def _key_bound_signals(self, incident: NormalizedIncident) -> dict[str, str]:
        """Signals that belong to one specific production input.

        Returns `{signal: normalised condition key}`. Only these signals are
        subject to the key check in `_signal_applies`; behavioural signals such
        as `retry` or `concurrency` are not tied to a single input.
        """
        bound: dict[str, str] = {}
        for key, value in incident.conditions.items():
            signal = VALUE_SIGNALS.get(normalize(str(value)).strip())
            if signal:
                bound.setdefault(signal, normalize(key))
        return bound

    def _signal_applies(
        self,
        signal: str,
        key_bound: dict[str, str],
        conditions: list[ConditionComparison],
    ) -> bool:
        """Whether a test's signal actually covers the production one.

        A key-bound signal only counts when the test exercises that qualifier on
        the *same* input. Without this, a "checkout without a coupon" test
        counted as coverage for a missing `quantity`.
        """
        bound_key = key_bound.get(signal)
        if bound_key is None:
            return True
        return any(c.matched for c in conditions if normalize(c.key) == bound_key)

    def _align_key(self, production_key: str, test_inputs: dict[str, object]) -> str | None:
        """Find the test input that means the same thing as a production key.

        Incident prose and test code name the same input differently —
        `tax_rate` versus `tax`, `page_size` versus `size`. An exact match wins;
        otherwise a shared significant token is enough. Without this, a tested
        input looks untested purely because of wording.
        """
        if production_key in test_inputs:
            return production_key

        production_tokens = {t for t in re.split(r"[_\s]+", production_key) if len(t) >= 3}
        if not production_tokens:
            return None

        for test_key in test_inputs:
            test_tokens = {t for t in re.split(r"[_\s]+", test_key) if len(t) >= 3}
            if production_tokens & test_tokens:
                return test_key
        return None

    def _split_values(self, value: str) -> list[str]:
        """`"0, 10, 100"` (from parametrize) -> ["0", "10", "100"]."""
        parts = [p.strip() for p in value.split(",")]
        return [p for p in parts if p] or [value]

    def _overlap(self, scenario_tokens: set[str], test: NormalizedTest) -> float:
        test_tokens = set(normalize(test.searchable_text()).split())
        if not scenario_tokens or not test_tokens:
            return 0.0
        return len(scenario_tokens & test_tokens) / len(scenario_tokens)
