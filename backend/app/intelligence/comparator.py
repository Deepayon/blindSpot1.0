"""Structured comparison between a production scenario and candidate tests.

This is deliberately deterministic: every fact the classifier later relies on is
computed here from extracted values, never from model prose. That is what makes
the "Evidence" section of a result trustworthy, and what stops BlindSpot from
ever hallucinating that a test covers something.

The key subtlety is **per-test** versus **union** coverage. Three tests that
separately cover `timeout`, `retry` and `payment failure` do *not* cover
"immediate retry after timeout". Coverage is therefore decided against the best
single test; the union is tracked only to detect exactly that combination gap.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ..domain.models import NormalizedIncident, NormalizedTest, RetrievedTest
from ..domain.text import as_number, normalize, tokenize, values_equivalent
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

#: A candidate below this blended retrieval score is not treated as related at
#: all. Lives here because `ScenarioComparison.best` needs it to avoid reporting
#: an unrelated test as the closest one; the classifier imports it from here so
#: both use one definition.
RELEVANCE_FLOOR = 0.15

#: Signals implying an ordering of events rather than a single input value.
SEQUENCE_SIGNALS: frozenset[str] = frozenset(
    {"retry", "timeout", "concurrency", "state_transition"}
)

#: Condition values that *are* a signal. When production says `name = null`, the
#: null-handling signal belongs to `name` specifically, a test that passes a
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
    #: MATCHED     the test drives this input at the production value
    #: DIFFERENT   the test drives this input at some other value
    #: MENTIONED   the test is about this input but states no value
    #: ABSENT      nothing in the test refers to this input
    #:
    #: MENTIONED exists because most manual test exports carry prose and no
    #: structured inputs. "Changing the account email address" plainly exercises
    #: `email`; without this state that dimension reads as untested, which
    #: understates coverage on exactly the corpora real customers have.
    status: str = "ABSENT"
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
    #: False when neither side carries usable feature information, so a
    #: non-match must not be scored as a mismatch.
    feature_applicable: bool = True
    scenario_overlap: float = 0.0
    conditions: list[ConditionComparison] = field(default_factory=list)
    matched_signals: list[str] = field(default_factory=list)
    missing_signals: list[str] = field(default_factory=list)
    #: Terms shared with the incident that are rare enough in the corpus to
    #: identify a behaviour. Empty means the only thing this test and the
    #: incident have in common is vocabulary every test uses.
    specific_terms: list[str] = field(default_factory=list)

    @property
    def matched_conditions(self) -> list[ConditionComparison]:
        return [c for c in self.conditions if c.status == "MATCHED"]

    @property
    def differing_conditions(self) -> list[ConditionComparison]:
        return [c for c in self.conditions if c.status == "DIFFERENT"]

    @property
    def mentioned_conditions(self) -> list[ConditionComparison]:
        return [c for c in self.conditions if c.status == "MENTIONED"]

    @property
    def absent_conditions(self) -> list[ConditionComparison]:
        return [c for c in self.conditions if c.status == "ABSENT"]

    @property
    def covers_all_conditions(self) -> bool:
        """Every production condition is driven at its production value.

        MENTIONED is deliberately not enough. A test known only to be *about*
        an input cannot establish that it used the value production used.
        """
        return bool(self.conditions) and all(c.matched for c in self.conditions)

    @property
    def covers_all_decisive_signals(self) -> bool:
        return not self.missing_signals

    @property
    def feature_ok(self) -> bool:
        """Same feature, or no feature information to contradict it."""
        return self.feature_match or not self.feature_applicable

    def coverage_strength(self) -> float:
        """0..1, how completely this one test represents the incident."""
        parts: list[float] = []
        if self.conditions:
            parts.append(sum(1.0 for c in self.conditions if c.matched) / len(self.conditions))
        decisive_total = len(self.matched_signals) + len(self.missing_signals)
        if decisive_total:
            parts.append(len(self.matched_signals) / decisive_total)
        if not parts:
            # Nothing structured to compare, fall back to topical similarity.
            return (0.6 if self.feature_ok else 0.2) * min(1.0, self.score + self.scenario_overlap)
        strength = sum(parts) / len(parts)
        return strength * (1.0 if self.feature_ok else 0.7)


@dataclass
class ScenarioComparison:
    """Aggregate view across every candidate test."""

    incident: NormalizedIncident
    comparisons: list[TestComparison] = field(default_factory=list)
    decisive_signals: list[str] = field(default_factory=list)
    #: `{signal: condition key}` for signals that belong to one production
    #: input, such as `null` when production reported `email = null`.
    key_bound_signals: dict[str, str] = field(default_factory=dict)

    @property
    def feature_known(self) -> bool:
        """Whether feature names are usable evidence for this comparison.

        A suite with no module column, or an incident whose area could not be
        resolved, gives us nothing to compare. Absence of feature information is
        not evidence of a feature mismatch, so in that case feature agreement is
        treated as not applicable rather than as failed.
        """
        if not self.incident.feature or self.incident.feature == "Unknown":
            return False
        return any(
            c.test.feature and c.test.feature != "Unknown" for c in self.comparisons
        )

    @property
    def judged(self) -> list[TestComparison]:
        """The candidates coverage is actually decided against.

        A timeout test in Authentication is not timeout coverage for a Profile
        incident, and counting it as such silently turns genuinely uncovered
        scenarios into "partially covered".

        Where feature names are usable, only same-feature tests are judged,
        including when that leaves nothing: a Checkout incident with no Checkout
        test retrieved is not answered by the one Payments test that happens to
        exercise concurrency. Falling back to every candidate in that case
        reintroduced exactly the confusion this filter exists to prevent.
        Everything is judged only when feature names carry no information at
        all, so that a suite without them still gets a verdict.
        """
        if self.feature_known:
            return [c for c in self.comparisons if c.feature_match]
        return self.comparisons

    @property
    def best(self) -> TestComparison | None:
        """The candidate the verdict is reported against.

        Chosen among candidates that clear the relevance floor. Ranking by
        coverage strength alone let a barely-related test win on a single shared
        signal: a tax-rate test scoring 0.08 outranked the discount tests the
        incident was actually about, purely because it happened to carry the
        same boundary signal. Falls back to the full set so a weak-but-best
        candidate is still reported when nothing clears the floor.
        """
        pool = [c for c in self.judged if c.score >= RELEVANCE_FLOOR] or self.judged
        return max(pool, key=lambda c: (c.coverage_strength(), c.score), default=None)

    @property
    def top_score(self) -> float:
        """The best retrieval score among judged candidates."""
        return max((c.score for c in self.judged), default=0.0)

    @property
    def any_feature_match(self) -> bool:
        """True when a candidate shares the incident's feature, or when feature
        information is unavailable and therefore cannot count against it."""
        if not self.feature_known:
            return bool(self.comparisons)
        return any(c.feature_match for c in self.comparisons)

    def union_matched_condition_keys(self) -> set[str]:
        return {c.key for comp in self.judged for c in comp.matched_conditions}

    def union_present_condition_keys(self) -> set[str]:
        """Condition keys a judged test drives at a known value."""
        return {
            c.key
            for comp in self.judged
            for c in comp.conditions
            if c.status in {"MATCHED", "DIFFERENT"}
        }

    def union_mentioned_condition_keys(self) -> set[str]:
        """Condition keys a judged test is about but records no value for.

        Weaker evidence than `union_present_condition_keys`, and treated as
        such: it supports PARTIAL only when no decisive behaviour is missing.
        A test titled for `query` says nothing about whether an *empty* query
        was ever exercised.
        """
        return {
            c.key for comp in self.judged for c in comp.conditions if c.status == "MENTIONED"
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
    """Builds a `ScenarioComparison` from an incident and its candidate tests.

    `is_distinctive` reports whether a term is rare enough in the indexed corpus
    to carry information (see `TestIndex.is_distinctive_term`). When it is not
    supplied every term counts as distinctive, which keeps the comparator usable
    standalone without silently weakening any verdict.
    """

    def __init__(self, is_distinctive: Callable[[str], bool] | None = None) -> None:
        self.is_distinctive = is_distinctive

    def compare(
        self, incident: NormalizedIncident, candidates: list[RetrievedTest]
    ) -> ScenarioComparison:
        decisive = [s for s in incident.signals if s in DECISIVE_SIGNALS]
        result = ScenarioComparison(
            incident=incident,
            decisive_signals=decisive,
            key_bound_signals=self._key_bound_signals(incident),
        )

        incident_feature = normalize(incident.feature)
        scenario_tokens = set(normalize(incident.scenario).split()) | set(
            normalize(incident.title).split()
        )
        key_bound = result.key_bound_signals
        incident_terms = set(tokenize(incident.searchable_text()))

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
                specific_terms=self._specific_terms(incident_terms, test),
            )
            result.comparisons.append(comparison)

        # Decided once the full candidate set is known: feature agreement only
        # counts where both sides actually carry a feature name.
        applicable = result.feature_known
        for comparison in result.comparisons:
            comparison.feature_applicable = applicable

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
            elif self._names_the_input(key, test, incident.feature):
                comparison.status = "MENTIONED"
            comparisons.append(comparison)

        return comparisons

    def _specific_terms(self, incident_terms: set[str], test: NormalizedTest) -> list[str]:
        """Shared terms rare enough in the corpus to identify a behaviour.

        The feature name itself is excluded: it is already compared directly,
        and counting it here would let "this is an Orders incident and that is
        an Orders test" masquerade as evidence about the failure.
        """
        if not incident_terms:
            return []
        feature_terms = set(tokenize(test.feature, keep_stopwords=True))
        shared = (incident_terms & set(tokenize(test.searchable_text()))) - feature_terms
        if self.is_distinctive is None:
            return sorted(shared)
        return sorted(term for term in shared if self.is_distinctive(term))

    def _names_the_input(self, key: str, test: NormalizedTest, feature: str = "") -> bool:
        """Whether the test's own wording is about this input.

        Deliberately strict: a whole-word match on a significant token of the
        key, against the test name and scenario only. Matching loosely here
        would manufacture coverage, which is the one error this product must
        never make.

        Tokens repeating the feature name are dropped. For `search_query` the
        token "search" occurs in nearly every Search test, so keeping it made
        "filtering search results by category" look like it exercised the query
        input. What remains, "query", is the part that actually names the input.
        """
        tokens = {token for token in re.split(r"[_\s]+", key) if len(token) >= 4}
        feature_tokens = {t for t in re.split(r"[_\s]+", normalize(feature)) if t}
        tokens -= feature_tokens
        if not tokens:
            return False
        haystack = set(re.split(r"[^a-z0-9]+", normalize(f"{test.name} {test.scenario}")))
        return bool(tokens & haystack)

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

        Incident prose and test code name the same input differently , 
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
