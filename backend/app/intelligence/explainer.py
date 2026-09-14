"""Evidence and explanation.

Spec §5 and §64: BlindSpot must never just say "Coverage = Partial". Every
verdict ships with the facts that produced it, and every fact is traceable to an
extracted value on one side and an indexed test on the other.

The narrative here is *generated from those facts by template*, so the wording
can never drift away from what was actually compared. An LLM may afterwards
rewrite the prose (see `llm_enrichment.py`) but cannot introduce new claims.
"""
from __future__ import annotations

from ..domain.enums import Coverage, GapType, family_for_gap_type
from ..domain.models import Evidence, Gap
from .comparator import ScenarioComparison

#: Human phrasing for each decisive signal, used in explanations.
SIGNAL_PHRASES: dict[str, str] = {
    "null": "null input handling",
    "empty": "empty input handling",
    "missing_field": "a missing required field",
    "invalid": "invalid input handling",
    "boundary_max": "the upper boundary value",
    "boundary_min": "the lower boundary value",
    "timeout": "timeout behaviour",
    "retry": "retry behaviour",
    "concurrency": "concurrent access",
    "permission": "permission / authorisation handling",
    "unicode": "Unicode / non-ASCII input",
    "error_handling": "error handling",
    "state_transition": "a state transition",
    "data_format": "data format handling",
    "environment": "environment-specific behaviour",
}


def phrase(signal: str) -> str:
    return SIGNAL_PHRASES.get(signal, signal.replace("_", " "))


def _cite(test) -> dict[str, object]:
    """Location fields for an evidence item that names a specific test.

    Included so a reader can open the test and check the claim rather than
    trusting the sentence.
    """
    return {
        "test_id": test.id,
        "test_source": test.source or None,
        "test_line": test.line_number,
    }


def _join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


class Explainer:
    """Builds evidence lists, gap records and the human explanation."""

    # -- evidence -----------------------------------------------------------

    def build_evidence(
        self, comparison: ScenarioComparison, coverage: Coverage
    ) -> list[Evidence]:
        incident = comparison.incident
        evidence: list[Evidence] = []

        if coverage is Coverage.INSUFFICIENT_EVIDENCE and comparison.comparisons:
            # Naming a "closest test" here would imply a relationship the
            # analysis could not establish. State the absence instead.
            best = comparison.best
            evidence.append(
                Evidence(
                    kind="insufficient_evidence",
                    statement=(
                        "No input values, affected fields or behavioural conditions could be "
                        "extracted from this report, so there is nothing to compare against "
                        "the test suite."
                    ),
                )
            )
            if best is not None:
                evidence.append(
                    Evidence(
                        kind="insufficient_evidence",
                        statement=(
                            f"The closest test, {best.test.id}, shares only wording common "
                            f"across the suite with this report. That is not evidence that "
                            f"the scenario was covered, nor that it was missed."
                        ),
                        **_cite(best.test),
                    )
                )
            return evidence

        for key, value in incident.conditions.items():
            evidence.append(
                Evidence(
                    kind="production_condition",
                    statement=f"Production condition: {key} = {value}",
                    production_value=str(value),
                )
            )

        for signal in comparison.decisive_signals:
            evidence.append(
                Evidence(
                    kind="production_signal",
                    statement=f"Production behaviour involved {phrase(signal)}.",
                )
            )

        best = comparison.best
        if best is None:
            evidence.append(
                Evidence(
                    kind="retrieval",
                    statement="No indexed test was relevant to this scenario.",
                )
            )
            return evidence

        evidence.append(
            Evidence(
                kind="feature",
                statement=(
                    f"Closest test {best.test.id} belongs to the same feature "
                    f"({best.test.feature})."
                    if best.feature_match
                    else f"Closest test {best.test.id} belongs to a different feature "
                    f"({best.test.feature} vs {incident.feature})."
                ),
                **_cite(best.test),
            )
        )

        # Per-condition evidence, gathered across every candidate so the reader
        # sees the full tested range rather than one arbitrary test.
        for key in incident.conditions:
            tested_values = comparison.tested_values_for(key)
            production_value = str(incident.conditions[key])
            mentioning = [
                comp
                for comp in comparison.judged
                for c in comp.conditions
                if c.key == key and c.status == "MENTIONED"
            ]
            matched = any(
                c.matched
                for comp in comparison.judged
                for c in comp.conditions
                if c.key == key
            )

            if not tested_values:
                if mentioning:
                    # The dimension is touched, but the export records no value,
                    # so it cannot be compared. Saying so is more accurate than
                    # claiming nothing exercises it.
                    cited = mentioning[0].test
                    evidence.append(
                        Evidence(
                            kind="condition_mentioned",
                            statement=(
                                f"{cited.id} covers '{key}', but its source records no value, "
                                f"so it cannot be compared against {production_value}."
                            ),
                            production_value=production_value,
                            **_cite(cited),
                        )
                    )
                else:
                    evidence.append(
                        Evidence(
                            kind="condition_absent",
                            statement=f"No related test exercises '{key}' at any value.",
                            production_value=production_value,
                        )
                    )
                continue

            if matched:
                evidence.append(
                    Evidence(
                        kind="condition_matched",
                        statement=f"'{key}' is tested at the production value {production_value}.",
                        production_value=production_value,
                        test_value=production_value,
                    )
                )
            else:
                # Name the test that actually varies this input, so the reader
                # can see which one to extend rather than hunting for it.
                citing = next(
                    (
                        comp
                        for comp in comparison.judged
                        for c in comp.conditions
                        if c.key == key and c.status == "DIFFERENT"
                    ),
                    None,
                )
                evidence.append(
                    Evidence(
                        kind="condition_difference",
                        statement=(
                            f"'{key}' is tested at {_join(tested_values)}, "
                            f"but production used {production_value}."
                        ),
                        production_value=production_value,
                        test_value=", ".join(tested_values),
                        **(_cite(citing.test) if citing else {}),
                    )
                )

        for signal in comparison.unmatched_signals():
            evidence.append(
                Evidence(
                    kind="signal_absent",
                    statement=f"No related test exercises {phrase(signal)}.",
                )
            )

        if comparison.has_combination_gap():
            covered = _join([phrase(s) for s in comparison.decisive_signals])
            evidence.append(
                Evidence(
                    kind="combination",
                    statement=(
                        f"{covered} are each tested separately, but no single test "
                        f"combines them as production did."
                    ),
                )
            )

        if coverage is Coverage.COVERED:
            evidence.append(
                Evidence(
                    kind="effectiveness",
                    statement=(
                        f"Test {best.test.id} represents this scenario, yet production still "
                        f"failed, so its assertions or data may not protect the behaviour."
                    ),
                    **_cite(best.test),
                )
            )

        return evidence

    # -- gap ----------------------------------------------------------------

    def build_gap(
        self, comparison: ScenarioComparison, coverage: Coverage, gap_type: GapType
    ) -> Gap | None:
        if coverage is Coverage.INSUFFICIENT_EVIDENCE:
            return None

        family_key, family_label = family_for_gap_type(gap_type)
        missing = self._missing_conditions(comparison, gap_type)
        summary = self._gap_summary(comparison, coverage, gap_type)
        detail = self._gap_detail(comparison, coverage, gap_type)

        return Gap(
            gap_type=gap_type,
            summary=summary,
            detail=detail,
            missing_conditions=missing,
            family_key=family_key,
            family_label=family_label,
        )

    def _missing_conditions(
        self, comparison: ScenarioComparison, gap_type: GapType
    ) -> dict[str, str]:
        """The specific production facts no test represents."""
        incident = comparison.incident
        missing: dict[str, str] = {}

        for key, value in incident.conditions.items():
            matched = any(
                c.matched
                for comp in comparison.judged
                for c in comp.conditions
                if c.key == key
            )
            if not matched:
                missing[key] = str(value)

        for signal in comparison.unmatched_signals():
            missing.setdefault(f"signal:{signal}", phrase(signal))

        if comparison.has_combination_gap():
            missing["combination"] = " + ".join(
                phrase(s) for s in comparison.decisive_signals
            )
        return missing

    def _gap_summary(
        self, comparison: ScenarioComparison, coverage: Coverage, gap_type: GapType
    ) -> str:
        incident = comparison.incident

        if comparison.has_combination_gap():
            return (
                f"No test combines "
                f"{_join([phrase(s) for s in comparison.decisive_signals])} "
                f"in the {incident.feature} flow."
            )

        if gap_type is GapType.WEAK_ASSERTION:
            best = comparison.best
            reference = f" ({best.test.id})" if best else ""
            return (
                f"The {incident.feature} scenario is covered{reference}, but the existing "
                f"test did not prevent the production failure."
            )

        unmatched_conditions = [
            (key, str(value))
            for key, value in incident.conditions.items()
            if not any(
                c.matched
                for comp in comparison.judged
                for c in comp.conditions
                if c.key == key
            )
        ]

        if gap_type is GapType.BOUNDARY_CONDITION and unmatched_conditions:
            key, value = unmatched_conditions[0]
            return f"The {value} {key} boundary condition is not covered by any existing test."

        if unmatched_conditions:
            rendered = _join([f"{key} = {value}" for key, value in unmatched_conditions])
            return f"No existing test represents {rendered} in the {incident.feature} flow."

        unmatched_signals = comparison.unmatched_signals()
        if unmatched_signals:
            return (
                f"No existing {incident.feature} test exercises "
                f"{_join([phrase(s) for s in unmatched_signals])}."
            )

        if coverage is Coverage.NOT_COVERED:
            return f"No meaningful test coverage exists for this {incident.feature} scenario."
        return f"Existing {incident.feature} tests do not represent the production scenario."

    def _gap_detail(
        self, comparison: ScenarioComparison, coverage: Coverage, gap_type: GapType
    ) -> str:
        best = comparison.best
        if best is None:
            return (
                "Retrieval found no test related to this scenario, so there is nothing "
                "in the index that could have caught it."
            )
        tested = ", ".join(
            f"{c.key} = {c.test_value}" for c in best.conditions if c.test_value is not None
        )
        parts = [f"Closest test: {best.test.describe()}."]
        if tested:
            parts.append(f"It exercises {tested}.")
        if best.missing_signals:
            parts.append(
                f"It does not exercise {_join([phrase(s) for s in best.missing_signals])}."
            )
        return " ".join(parts)

    # -- narrative ----------------------------------------------------------

    def build_explanation(
        self, comparison: ScenarioComparison, coverage: Coverage, gap_type: GapType
    ) -> str:
        incident = comparison.incident
        best = comparison.best

        if coverage is Coverage.INSUFFICIENT_EVIDENCE:
            if not comparison.comparisons:
                return (
                    "There are not enough indexed tests to judge whether this scenario was "
                    "covered. Index a test source before drawing a conclusion."
                )
            return (
                "This report does not describe the conditions that triggered the failure, so "
                "BlindSpot cannot determine whether a test covered it. The related tests below "
                "share only general wording with the report, which is not evidence either way. "
                "Add the input values, the affected field or the sequence of events, then "
                "re-analyse."
            )

        if best is None or coverage is Coverage.NOT_COVERED and not comparison.any_feature_match:
            return (
                f"No indexed test meaningfully represents this {incident.feature} scenario. "
                f"Nothing in the current suite would have caught it."
            )

        if comparison.has_combination_gap():
            pieces = _join([phrase(s) for s in comparison.decisive_signals])
            return (
                f"Existing tests cover {pieces} individually, but production failed on the "
                f"combination. No single test exercises them together, so the interaction "
                f"was never verified."
            )

        if coverage is Coverage.COVERED:
            return (
                f"Test {best.test.id} does represent this scenario, including the production "
                f"conditions. Because production still failed, the gap is in the test's "
                f"assertions or data rather than in scenario coverage, review what it "
                f"actually verifies."
            )

        # PARTIAL / NOT_COVERED with a related test present.
        differing = [
            c
            for c in best.conditions
            if c.status == "DIFFERENT" or (c.status == "ABSENT" and comparison.tested_values_for(c.key))
        ]
        if differing:
            condition = differing[0]
            tested = comparison.tested_values_for(condition.key) or [str(condition.test_value)]
            return (
                f"Existing tests validate {condition.key} behaviour "
                f"({_join(tested)}) but do not represent the {condition.production_value} "
                f"value observed in production, which is what triggered the failure."
            )

        unmatched = comparison.unmatched_signals()
        if unmatched:
            return (
                f"Existing tests cover the {incident.feature} flow but none exercises "
                f"{_join([phrase(s) for s in unmatched])}, which is the condition production hit."
            )

        absent = [c.key for c in best.absent_conditions]
        if absent:
            return (
                f"Related {incident.feature} tests exist, but none sets "
                f"{_join(absent)}, so the production condition was never represented."
            )

        return (
            f"Related {incident.feature} tests exist, but none reproduces the specific "
            f"production scenario closely enough to have caught this failure."
        )
