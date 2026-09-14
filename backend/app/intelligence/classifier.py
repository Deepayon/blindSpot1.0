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
from ..domain.text import normalize
from .comparator import (
    RELEVANCE_FLOOR,
    SEQUENCE_SIGNALS,
    ScenarioComparison,
    TestComparison,
)

__all__ = ["RELEVANCE_FLOOR", "CoverageClassifier", "GapClassifier"]

#: Coverage strength above which a test counts as meaningfully related even
#: without an exact condition match.
PARTIAL_STRENGTH_FLOOR = 0.35

#: With no structured conditions or signals to compare, COVERED requires strong
#: topical agreement, otherwise we would be guessing.
TOPICAL_COVERED_SCORE = 0.55
TOPICAL_COVERED_OVERLAP = 0.45

#: Distinctive shared terms that, together with a strong score, stand in for
#: raw overlap. Two is deliberate: one rare word in common is a coincidence,
#: two describing the same behaviour is not.
TOPICAL_COVERED_TERMS = 2

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

        # 1. Nothing relevant was retrieved. Measured across every candidate:
        # `best` is ranked by coverage strength, so its own score can be low
        # even when a genuinely related test was retrieved alongside it.
        if best is None or comparison.top_score < RELEVANCE_FLOOR:
            incident = comparison.incident
            if (
                not incident.conditions
                and not incident.signals
                and not comparison.feature_known
            ):
                # No area, no condition, no behaviour, and nothing retrieved:
                # there was nothing to search the suite with. "Not covered"
                # would claim a search happened and came back empty, when
                # nothing was ever asked.
                #
                # A known feature is what separates this from an honest
                # negative. "A chargeback was recorded against the wrong order"
                # names its area; finding no Payments test for it is a real
                # finding, not an absence of information.
                return Coverage.INSUFFICIENT_EVIDENCE, TestEffectiveness.NO_COVERAGE
            return Coverage.NOT_COVERED, TestEffectiveness.NO_COVERAGE

        # 2. Every piece is tested somewhere, but no single test combines them.
        if comparison.has_combination_gap():
            return Coverage.NOT_COVERED, TestEffectiveness.NO_COVERAGE

        has_structure = bool(best.conditions) or bool(comparison.decisive_signals)

        # 3. One test represents the production scenario completely.
        if has_structure and best.feature_ok:
            conditions_ok = best.covers_all_conditions or not best.conditions
            signals_ok = best.covers_all_decisive_signals
            # With no conditions to pin it down, a shared signal alone is too
            # weak for the strongest verdict: "refund before capture" and
            # "retry after a failure" are both state transitions, and calling
            # the second coverage for the first would tell a reader the
            # scenario is tested when no test goes near it. Something specific
            # must also be shared.
            if not best.conditions and not best.specific_terms:
                signals_ok = False
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
        # The test varies the same input, just never at the production value:
        # the canonical "10% and 20% but never 100%" case.
        incident_keys = set(comparison.incident.conditions)
        if comparison.union_present_condition_keys() & incident_keys:
            return True

        # A test merely *named* for the input is weaker evidence, so it only
        # counts when no decisive behaviour is missing. Otherwise a Checkout
        # test mentioning "cart" would upgrade an untested concurrency failure
        # to partially covered, which is the behaviour that matters being
        # outvoted by a noun.
        mentioned_keys = comparison.union_mentioned_condition_keys() & incident_keys
        if not comparison.unmatched_signals() and mentioned_keys:
            return True

        # The unmatched signal *is* the production value of a field some test is
        # about: production reported `email = null`, and a test drives `email`
        # but records no value. The dimension is exercised, the value is not,
        # which is the definition of partial. Narrow on purpose: it needs a
        # condition whose value is itself a signal, and a test naming that same
        # field, so it cannot upgrade an unrelated untested behaviour.
        if mentioned_keys and any(
            comparison.key_bound_signals.get(signal) in {normalize(k) for k in mentioned_keys}
            for signal in comparison.unmatched_signals()
        ):
            return True

        # Nothing about the incident is represented: no condition matched, no
        # signal matched, and some decisive behaviour is exercised by no
        # candidate at all. Topical closeness alone is then not partial
        # coverage. A suite full of Search tests, none of which touches
        # permissions, is not partial coverage for a permissions failure in
        # Search, and calling it that would point the reader at tests that
        # could never have caught the bug.
        #
        # Shared wording is deliberately not an escape hatch here. Allowing a
        # rare shared term to justify PARTIAL was tried and reclassified
        # nineteen genuinely uncovered scenarios as partially covered: incidents
        # routinely share an uncommon word with some test in their own feature
        # without that test going anywhere near the failure.
        if comparison.unmatched_signals():
            return False

        return best.coverage_strength() >= PARTIAL_STRENGTH_FLOOR

    def _classify_topical(
        self, best: TestComparison, comparison: ScenarioComparison
    ) -> tuple[Coverage, TestEffectiveness]:
        """No conditions and no decisive signals, judge on topic alone.

        With no structured facts the only remaining evidence is shared
        vocabulary, and that is evidence only where the shared words are
        specific enough to mean something. An incident reading "the orders page
        looked wrong" shares "orders" and "page" with half the suite; concluding
        "partially covered" from that is a guess dressed as a verdict, so it is
        reported as insufficient evidence instead.
        """
        # Checked first: when nothing retrieved is even in the incident's area,
        # "not covered" is a positive finding, not a guess. Insufficient
        # evidence is reserved for the case where we would otherwise have
        # claimed coverage without anything to base it on.
        if not comparison.any_feature_match:
            return Coverage.NOT_COVERED, TestEffectiveness.NO_COVERAGE
        if not any(comp.specific_terms for comp in comparison.judged):
            return Coverage.INSUFFICIENT_EVIDENCE, TestEffectiveness.NO_COVERAGE
        # Two routes to COVERED, both requiring the same feature and a strong
        # retrieval score. Raw token overlap is diluted by incident prose: an
        # incident describing the browser, the back button and cached data
        # scores 0.23 against the test that names the exact behaviour it
        # exercised. Several distinctive shared terms are the better evidence,
        # since each one is a word almost no other test in the suite uses.
        if best.feature_match and best.score >= TOPICAL_COVERED_SCORE and (
            best.scenario_overlap >= TOPICAL_COVERED_OVERLAP
            or len(best.specific_terms) >= TOPICAL_COVERED_TERMS
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
