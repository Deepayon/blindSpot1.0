"""Coverage and gap-type classification.

Every threshold in this module is a named constant with a stated rationale, and
every decision is reachable from the structured comparison alone. No LLM is
involved: the classification must be reproducible and defensible.

Coverage decision order (first match wins):

  1. no candidate clears the relevance floor        -> NOT_COVERED
  2. the pieces are tested but never together       -> NOT_COVERED  (combination)
  3. one test matches every condition and signal    -> COVERED
  4. a related test exists but misses the decisive
     production condition                           -> PARTIAL
  5. otherwise                                       -> NOT_COVERED
"""
from __future__ import annotations

from ..domain.enums import Coverage, GapType, TestEffectiveness
from .comparator import SEQUENCE_SIGNALS, ScenarioComparison, TestComparison

#: A candidate below this blended retrieval score is not treated as related at
#: all. Set low because retrieval already filters; this only rejects noise.
RELEVANCE_FLOOR = 0.15

#: Coverage strength above which a test counts as meaningfully related even
#: without an exact condition match.
PARTIAL_STRENGTH_FLOOR = 0.35

#: With no structured conditions or signals to compare, COVERED requires strong
#: topical agreement, otherwise we would be guessing.
TOPICAL_COVERED_SCORE = 0.55
TOPICAL_COVERED_OVERLAP = 0.45

#: Unmatched signal -> gap type, in priority order. The first unmatched signal
#: in this sequence decides the gap.
SIGNAL_GAP_TYPES: tuple[tuple[str, GapType], ...] = (
    ("concurrency", GapType.CONCURRENCY),
    ("retry", GapType.RETRY_BEHAVIOR),
    ("timeout", GapType.TIMEOUT_BEHAVIOR),
    ("permission", GapType.PERMISSION),
    ("unicode", GapType.UNICODE_ENCODING),
    ("null", GapType.NULL_HANDLING),
    ("empty", GapType.EMPTY_INPUT),
    ("missing_field", GapType.MISSING_FIELD),
    ("invalid", GapType.INVALID_INPUT),
    ("boundary_max", GapType.BOUNDARY_CONDITION),
    ("boundary_min", GapType.BOUNDARY_CONDITION),
    ("error_handling", GapType.ERROR_HANDLING),
    # Ranked last: both are real gaps, but when a more specific signal is also
    # missing that one names the problem better.
    ("environment", GapType.ENVIRONMENT_SPECIFIC),
    ("state_transition", GapType.STATE_TRANSITION),
)

#: Supporting signals that still name a gap when nothing decisive is missing.
SUPPORTING_GAP_TYPES: tuple[tuple[str, GapType], ...] = (
    ("data_format", GapType.DATA_FORMAT),
)


class CoverageClassifier:
    def classify(self, comparison: ScenarioComparison) -> tuple[Coverage, TestEffectiveness]:
        best = comparison.best

        # 1. Nothing relevant was retrieved.
        if best is None or best.score < RELEVANCE_FLOOR:
            return Coverage.NOT_COVERED, TestEffectiveness.NO_COVERAGE

        # 2. Every piece is tested somewhere, but no single test combines them.
        if comparison.has_combination_gap():
            return Coverage.NOT_COVERED, TestEffectiveness.NO_COVERAGE

        has_structure = bool(best.conditions) or bool(comparison.decisive_signals)

        # 3. One test represents the production scenario completely.
        if has_structure and best.feature_match:
            conditions_ok = best.covers_all_conditions or not best.conditions
            signals_ok = best.covers_all_decisive_signals
            if conditions_ok and signals_ok:
                # The scenario *was* covered, yet production still failed, so the
                # test's data or assertions did not actually protect the behaviour.
                return Coverage.COVERED, TestEffectiveness.POTENTIALLY_INEFFECTIVE

        if not has_structure:
            return self._classify_topical(best, comparison)

        # 4. A related test exists but misses the decisive condition.
        if self._is_partial(best, comparison):
            return Coverage.PARTIAL, TestEffectiveness.INSUFFICIENT_COVERAGE

        # 5. Related in topic only.
        return Coverage.NOT_COVERED, TestEffectiveness.NO_COVERAGE

    def _is_partial(self, best: TestComparison, comparison: ScenarioComparison) -> bool:
        """A gap is *partial* when the area is tested but the trigger is not."""
        if not comparison.any_feature_match:
            return False
        if best.matched_conditions or best.matched_signals:
            return True
        # The test varies the same input, just never at the production value , 
        # the canonical "10% and 20% but never 100%" case.
        if comparison.union_present_condition_keys() & set(comparison.incident.conditions):
            return True
        return best.coverage_strength() >= PARTIAL_STRENGTH_FLOOR

    def _classify_topical(
        self, best: TestComparison, comparison: ScenarioComparison
    ) -> tuple[Coverage, TestEffectiveness]:
        """No conditions and no decisive signals, judge on topic alone."""
        if (
            best.feature_match
            and best.score >= TOPICAL_COVERED_SCORE
            and best.scenario_overlap >= TOPICAL_COVERED_OVERLAP
        ):
            return Coverage.COVERED, TestEffectiveness.POTENTIALLY_INEFFECTIVE
        if comparison.any_feature_match:
            return Coverage.PARTIAL, TestEffectiveness.INSUFFICIENT_COVERAGE
        return Coverage.NOT_COVERED, TestEffectiveness.NO_COVERAGE


class GapClassifier:
    """Names the missing behaviour once coverage is known."""

    def classify(self, comparison: ScenarioComparison, coverage: Coverage) -> GapType:
        if coverage is Coverage.COVERED:
            # Covered yet still broken in production: the test itself is suspect.
            return GapType.WEAK_ASSERTION

        best = comparison.best

        # Tested separately, never together.
        if comparison.has_combination_gap():
            if set(comparison.decisive_signals) & SEQUENCE_SIGNALS:
                return GapType.STATE_TRANSITION
            return GapType.INPUT_COMBINATION

        # A production value outside every tested value is a boundary gap, and
        # outranks a generic signal because it is the more specific finding.
        if best is not None and self._is_boundary_gap(best, comparison):
            return GapType.BOUNDARY_CONDITION

        unmatched = set(comparison.unmatched_signals())
        if best is not None:
            unmatched |= set(best.missing_signals)
        for signal, gap_type in SIGNAL_GAP_TYPES:
            if signal in unmatched:
                return gap_type

        # Nothing decisive missing, but a condition is entirely untested.
        if best is not None and best.absent_conditions:
            return GapType.MISSING_TEST

        incident_signals = set(comparison.incident.signals)
        for signal, gap_type in SUPPORTING_GAP_TYPES:
            if signal in incident_signals:
                return gap_type

        if best is None or not comparison.any_feature_match:
            return GapType.MISSING_TEST
        return GapType.TEST_DATA_MISMATCH

    def _is_boundary_gap(self, best: TestComparison, comparison: ScenarioComparison) -> bool:
        """True when a numeric production value lies outside the tested range.

        Requires the input to be tested at *some* value, an untested input is a
        missing test, not a boundary gap.
        """
        for condition in best.differing_conditions + best.absent_conditions:
            production = condition.production_number
            if production is None:
                continue
            tested = comparison.tested_numbers_for(condition.key)
            if not tested:
                continue
            if production > max(tested) or production < min(tested):
                return True
            # A percentage extreme is a boundary even if bracketed by tests.
            if production in (0.0, 100.0) and production not in tested:
                return True
        return False
