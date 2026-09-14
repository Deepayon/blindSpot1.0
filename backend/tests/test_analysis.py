"""Retrieval, coverage classification, gaps, recommendations and patterns.

Covers spec §52 items 6-10, including the acceptance test in §68 and the
worked example in §56.
"""
from __future__ import annotations

import pytest

from app.domain.enums import Coverage, GapType, Risk

# Aliased away from the `Test*` prefix so pytest does not try to collect these
# application classes as test cases.
from app.domain.enums import TestEffectiveness as Effectiveness
from app.intelligence.engine import GapAnalysisEngine
from app.intelligence.incident_normalizer import IncidentNormalizer
from app.intelligence.pattern_detector import GapObservation, PatternDetector
from app.retrieval.index import TestIndex as SearchIndex
from app.retrieval.retriever import TestRetriever as Retriever

from .conftest import make_test


@pytest.fixture
def engine(indexed_state):
    return GapAnalysisEngine(indexed_state.index)


def analyse(engine, text: str, incident_id: str = "INC-1", **structured):
    # The vocabulary comes from the index, exactly as the service supplies it,
    # so the incident is classified against the features this corpus uses.
    incident = IncidentNormalizer().normalize(
        text,
        incident_id=incident_id,
        structured=structured,
        vocabulary=engine.index.vocabulary,
    )
    return engine.analyze(incident)


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------


class TestRetrieval:
    def test_retrieves_only_topically_related_tests(self, indexed_state):
        incident = IncidentNormalizer().normalize(
            "Checkout failed when a 100% discount coupon was applied.", incident_id="INC-1"
        )
        retrieved, debug = Retriever(indexed_state.index).retrieve(incident)

        ids = [item.test.id for item in retrieved]
        assert "TC-182" in ids and "TC-201" in ids
        assert "TC-300" not in ids, "an Authentication test is not relevant to a checkout incident"
        assert debug.candidate_count > 0
        assert debug.top_score > 0

    def test_empty_index_returns_nothing(self, app_state):
        app_state.index.clear()
        incident = IncidentNormalizer().normalize("Anything at all", incident_id="INC-1")
        retrieved, _ = Retriever(app_state.index).retrieve(incident)
        assert retrieved == []

    def test_results_are_deterministic(self, indexed_state):
        incident = IncidentNormalizer().normalize(
            "Checkout failed with a 100% discount", incident_id="INC-1"
        )
        retriever = Retriever(indexed_state.index)
        first = [item.test.id for item in retriever.retrieve(incident)[0]]
        second = [item.test.id for item in retriever.retrieve(incident)[0]]
        assert first == second

    def test_index_round_trips_through_disk(self, sample_tests, tmp_path):
        original = SearchIndex()
        original.build(sample_tests)
        original.save(tmp_path / "index")

        restored = SearchIndex()
        assert restored.load(tmp_path / "index") is True
        assert len(restored) == len(sample_tests)

        incident = IncidentNormalizer().normalize("Checkout with a discount", incident_id="INC-1")
        assert Retriever(restored).retrieve(incident)[0], "restored index must be searchable"


# --------------------------------------------------------------------------
# Coverage classification
# --------------------------------------------------------------------------


class TestCoverageClassification:
    def test_partial_coverage_boundary_condition(self, engine):
        """Spec §68 acceptance test."""
        result = analyse(
            engine,
            "Checkout failed when a customer applied a 100% discount coupon.\n"
            "Root cause: division by zero in the discount calculation.",
        )

        assert result.coverage is Coverage.PARTIAL
        assert result.effectiveness is Effectiveness.INSUFFICIENT_COVERAGE
        assert result.gaps[0].gap_type is GapType.BOUNDARY_CONDITION

        related = {item.test.id for item in result.relevant_tests}
        assert {"TC-182", "TC-201"} <= related

        assert "100" in result.gaps[0].summary
        assert "10%" in result.explanation and "100%" in result.explanation

    def test_covered_when_the_production_value_is_tested(self, engine):
        result = analyse(engine, "Checkout with a 10% discount coupon produced the wrong total.")

        assert result.coverage is Coverage.COVERED
        # Covered yet still broken: the test itself is the suspect.
        assert result.effectiveness is Effectiveness.POTENTIALLY_INEFFECTIVE
        assert result.gaps[0].gap_type is GapType.WEAK_ASSERTION

    def test_not_covered_when_the_behaviour_is_absent(self, engine):
        result = analyse(
            engine, "User profile update fails when the display name contains unicode emoji."
        )

        assert result.coverage is Coverage.NOT_COVERED
        assert result.effectiveness is Effectiveness.NO_COVERAGE
        assert result.gaps[0].gap_type is GapType.UNICODE_ENCODING

    def test_combination_gap_is_not_covered(self, engine):
        """Spec §56: timeout and retry are each tested, the combination is not."""
        result = analyse(
            engine,
            "Payment failed when an immediate retry occurred after a gateway timeout.",
        )

        assert result.coverage is Coverage.NOT_COVERED
        assert result.gaps[0].gap_type is GapType.STATE_TRANSITION
        assert "combin" in result.explanation.lower()

        related = {item.test.id for item in result.relevant_tests}
        assert {"TC-100", "TC-142"} <= related

    def test_insufficient_evidence_when_nothing_is_indexed(self, app_state):
        """Reporting "not covered" against an empty index would be a fabrication."""
        app_state.index.clear()
        engine = GapAnalysisEngine(app_state.index)
        result = analyse(engine, "Checkout failed with a 100% discount.")

        assert result.coverage is Coverage.INSUFFICIENT_EVIDENCE
        assert result.gaps == []
        assert result.confidence == 0.0

    def test_vague_report_yields_insufficient_evidence_not_a_guess(self, engine):
        """A report with no conditions must not be answered with a verdict.

        The tests it retrieves share only words the whole suite uses, which is
        evidence neither way.
        """
        result = analyse(engine, "Something went wrong in production this morning.")

        assert result.coverage is Coverage.INSUFFICIENT_EVIDENCE
        assert result.gaps == []
        assert "does not describe" in result.explanation or "not enough" in result.explanation

    def test_symptom_without_a_trigger_is_insufficient_evidence(self, app_state):
        """Sharing the feature's own vocabulary is not evidence of coverage."""
        app_state.index.build(
            [
                make_test("TC-1", "order_list", "Orders", "The orders page lists recent orders"),
                make_test("TC-2", "order_detail", "Orders", "The orders page shows one order"),
            ]
        )
        result = analyse(
            GapAnalysisEngine(app_state.index),
            "Customers reported that the orders page looked wrong for about twenty minutes.",
            feature="Orders",
        )

        assert result.coverage is Coverage.INSUFFICIENT_EVIDENCE

    def test_insufficient_evidence_never_becomes_a_blind_spot(self, engine):
        """An unanswerable incident must not feed the recurring-pattern data."""
        result = analyse(engine, "Something went wrong in production this morning.")

        assert result.gaps == []
        assert result.recommendations, "the reader still needs to know what to add"
        assert all(
            "test" not in r.title.lower() or "re-analyse" in r.title.lower()
            for r in result.recommendations
        ), "recommending tests for an unknown scenario would be inventing it"

    def test_a_named_area_with_no_related_test_is_a_real_finding(self, app_state):
        """Not covered, not insufficient: the suite was searched and came up empty.

        The distinction matters. "Insufficient evidence" says we could not look;
        this incident names its area clearly and nothing in it is related.
        """
        app_state.index.build(
            [
                make_test("TC-1", "login_valid", "Authentication", "Login with valid credentials"),
                make_test("TC-2", "login_invalid", "Authentication", "Login with a bad password"),
            ]
        )
        result = analyse(
            GapAnalysisEngine(app_state.index),
            "A chargeback raised by the issuing bank was recorded against the wrong order, "
            "because the chargeback handler matched on customer rather than transaction.",
            feature="Payments",
        )

        assert result.coverage is Coverage.NOT_COVERED

    def test_cross_feature_test_does_not_grant_coverage(self, app_state):
        """A timeout test in Payments is not timeout coverage for Profile."""
        app_state.index.build(
            [
                make_test("TC-1", "payment_timeout", "Payments", "Payment gateway timeout is handled"),
                make_test("TC-2", "profile_view", "Profile", "Viewing the profile page"),
            ]
        )
        result = analyse(
            GapAnalysisEngine(app_state.index),
            "Profile sync hung when the identity service timed out.",
            feature="Profile",
        )
        assert result.coverage is not Coverage.COVERED


# --------------------------------------------------------------------------
# Explainability
# --------------------------------------------------------------------------


class TestExplainability:
    def test_evidence_cites_both_sides_of_the_comparison(self, engine):
        result = analyse(engine, "Checkout failed when a 100% discount coupon was applied.")
        statements = [item.statement for item in result.evidence]

        assert any("discount = 100%" in s for s in statements)
        assert any("10%" in s and "100%" in s for s in statements)

    def test_no_test_id_is_invented(self, engine):
        result = analyse(engine, "Checkout failed when a 100% discount coupon was applied.")
        known = {item.test.id for item in result.relevant_tests}
        cited = {item.test_id for item in result.evidence if item.test_id}
        assert cited <= known

    def test_debug_fields_are_populated(self, engine):
        result = analyse(engine, "Checkout failed when a 100% discount coupon was applied.")

        assert result.retrieval.candidate_count > 0
        assert result.retrieval.strategy.startswith("hybrid")
        assert result.comparison.feature_match is True
        assert result.comparison.condition_match is False
        assert result.reasoning_source == "deterministic"

    def test_confidence_is_derived_not_invented(self, engine):
        rich = analyse(engine, "Checkout failed when a 100% discount coupon was applied.")
        vague = analyse(engine, "Something went wrong somewhere in the system.", incident_id="INC-2")

        assert 0.0 <= vague.confidence <= rich.confidence <= 1.0
        assert rich.confidence_level.value in {"HIGH", "MEDIUM"}


# --------------------------------------------------------------------------
# Term specificity: what counts as topical evidence
# --------------------------------------------------------------------------


class TestTermSpecificity:
    """Shared wording is evidence only when the words are rare in the corpus."""

    def test_a_term_used_across_the_suite_is_not_distinctive(self, app_state):
        app_state.index.build(
            [make_test(f"TC-{i}", f"orders_{i}", "Orders", "The orders page works") for i in range(60)]
        )
        assert app_state.index.is_distinctive_term("orders") is False

    def test_a_rare_term_is_distinctive(self, app_state):
        corpus = [make_test(f"TC-{i}", f"orders_{i}", "Orders", "The orders page works") for i in range(60)]
        corpus.append(make_test("TC-99", "logout", "Authentication", "Logout clears the session"))
        app_state.index.build(corpus)

        assert app_state.index.is_distinctive_term("logout") is True

    def test_small_suites_still_have_distinctive_terms(self, app_state):
        """In a 20-test suite one test is already 5% of the corpus.

        Applying the 2% share flatly would make nothing distinctive and collapse
        every topical verdict to "insufficient evidence".
        """
        app_state.index.build(
            [make_test(f"TC-{i}", f"t{i}", "Orders", f"Scenario number {i} for widgets") for i in range(20)]
        )
        assert app_state.index.is_distinctive_term("widgets") is False
        assert app_state.index.is_distinctive_term("number") is False
        app_state.index.build(
            [make_test("TC-1", "logout", "Authentication", "Logout clears the session")]
            + [make_test(f"TC-{i}", f"t{i}", "Orders", f"Ordinary scenario {i}") for i in range(2, 21)]
        )
        assert app_state.index.is_distinctive_term("logout") is True

    def test_an_unindexed_term_is_not_treated_as_rare(self, app_state):
        """Never seen is not the same as rare, and must not read as evidence."""
        app_state.index.build([make_test("TC-1", "t", "Orders", "The orders page works")])

        assert app_state.index.is_distinctive_term("chargeback") is False


# --------------------------------------------------------------------------
# Retrieval of signal witnesses
# --------------------------------------------------------------------------


class TestSignalWitnesses:
    def test_a_dominant_candidate_does_not_hide_the_other_signal(self, app_state):
        """Combination gaps are invisible unless each signal keeps a witness.

        A strongly-scoring retry test raises the relative floor above the
        concurrency test, and the interaction between them is the finding.
        """
        app_state.index.build(
            [
                make_test("TC-1", "payment_retry", "Payments",
                          "Payment retry after a failed attempt is retried again"),
                make_test("TC-2", "payment_concurrent", "Payments",
                          "Two simultaneous refund requests for the same payment"),
                make_test("TC-3", "payment_card", "Payments", "Payment with a valid card"),
            ]
        )
        incident = IncidentNormalizer().normalize(
            "A payment was retried while the original attempt was still in flight, and both "
            "charges settled.",
            incident_id="INC-1",
            structured={"feature": "Payments"},
        )
        retrieved, _ = Retriever(app_state.index).retrieve(incident)

        assert {"TC-1", "TC-2"} <= {item.test.id for item in retrieved}

    def test_another_features_test_is_not_counted_as_coverage(self, app_state):
        """One feature's concurrency test cannot answer for another's.

        Retrieval may still surface it, since it is genuinely the closest thing
        by wording. What must not happen is it counting as coverage and closing
        the gap: the Checkout concurrency failure stays reported.
        """
        app_state.index.build(
            [
                make_test("TC-2", "payment_concurrent", "Payments",
                          "Two simultaneous refund requests for the same payment"),
                make_test("TC-3", "orders_list", "Orders", "The orders page lists orders"),
            ]
        )
        result = analyse(
            GapAnalysisEngine(app_state.index),
            "Two concurrent checkout requests for the same cart created duplicate orders.",
            feature="Checkout",
        )

        assert result.coverage is Coverage.NOT_COVERED
        assert result.gaps[0].gap_type is GapType.CONCURRENCY


# --------------------------------------------------------------------------
# Recommendations and risk
# --------------------------------------------------------------------------


class TestRecommendations:
    def test_boundary_gap_recommends_boundary_values(self, engine):
        result = analyse(engine, "Checkout failed when a 100% discount coupon was applied.")
        titles = " | ".join(r.title for r in result.recommendations)

        assert "100%" in titles
        assert "negative" in titles.lower()
        assert len(result.recommendations) <= 6, "recommendations must not flood the backlog"

    def test_combination_gap_recommends_an_end_to_end_test(self, engine):
        result = analyse(engine, "Payment failed when an immediate retry occurred after a timeout.")
        titles = " ".join(r.title for r in result.recommendations).lower()
        assert "end-to-end" in titles or "sequence" in titles

    def test_recommendations_inherit_risk_as_priority(self, engine):
        result = analyse(engine, "Checkout failed when a 100% discount coupon was applied.")
        assert all(r.priority is result.risk for r in result.recommendations)

    def test_risk_rises_with_recurrence(self, indexed_state):
        engine = GapAnalysisEngine(indexed_state.index)
        incident = IncidentNormalizer().normalize(
            "Search returned wrong ordering for a query.", incident_id="INC-1"
        )
        isolated = engine.analyze(incident, similar_incident_count=0)
        recurring = engine.analyze(incident, similar_incident_count=8)

        order = {Risk.LOW: 0, Risk.MEDIUM: 1, Risk.HIGH: 2}
        assert order[recurring.risk] >= order[isolated.risk]


# --------------------------------------------------------------------------
# Recurring blind spots
# --------------------------------------------------------------------------


class TestPatternDetection:
    def _observations(self, *specs: tuple[str, GapType, str, str]) -> list[GapObservation]:
        from app.domain.enums import family_for_gap_type

        result = []
        for incident_id, gap_type, feature, severity in specs:
            key, label = family_for_gap_type(gap_type)
            result.append(
                GapObservation(
                    incident_id=incident_id,
                    gap_type=gap_type,
                    family_key=key,
                    family_label=label,
                    feature=feature,
                    severity=severity,
                    coverage="NOT_COVERED",
                )
            )
        return result

    def test_groups_related_gap_types_into_one_family(self):
        """Spec §21: null, missing and empty roll up into one blind spot."""
        observations = self._observations(
            ("INC-101", GapType.NULL_HANDLING, "Profile", "HIGH"),
            ("INC-105", GapType.MISSING_FIELD, "Orders", "HIGH"),
            ("INC-109", GapType.EMPTY_INPUT, "Checkout", "MEDIUM"),
            ("INC-121", GapType.NULL_HANDLING, "Payments", "CRITICAL"),
        )
        patterns = PatternDetector(min_incidents=3).detect(observations)

        assert len(patterns) == 1
        pattern = patterns[0]
        assert pattern.key == "NULL_EMPTY_INPUTS"
        assert pattern.label == "Null / Empty Inputs"
        assert pattern.incident_count == 4
        assert pattern.risk is Risk.HIGH
        assert "4 incidents" in pattern.summary

    def test_a_pattern_concentrated_in_one_area_says_so(self):
        """Seven incidents in one service is that service's problem, not the
        organisation's, and the summary must not imply otherwise."""
        observations = self._observations(
            *[(f"INC-{i}", GapType.NULL_HANDLING, "Billing", "HIGH") for i in range(6)],
            ("INC-99", GapType.EMPTY_INPUT, "Search", "LOW"),
        )
        pattern = PatternDetector(min_incidents=3).detect(observations)[0]

        assert pattern.concentrated_in == "Billing"
        assert pattern.concentration >= 0.8
        assert "in Billing" in pattern.summary

    def test_a_genuinely_spread_pattern_is_not_reported_as_concentrated(self):
        observations = self._observations(
            ("INC-1", GapType.NULL_HANDLING, "Billing", "HIGH"),
            ("INC-2", GapType.EMPTY_INPUT, "Search", "HIGH"),
            ("INC-3", GapType.MISSING_FIELD, "Orders", "HIGH"),
        )
        pattern = PatternDetector(min_incidents=3).detect(observations)[0]

        assert pattern.concentration < 0.6
        assert "spread across" in pattern.summary

    def test_below_the_threshold_is_not_a_pattern(self):
        observations = self._observations(
            ("INC-1", GapType.NULL_HANDLING, "Profile", "LOW"),
            ("INC-2", GapType.EMPTY_INPUT, "Profile", "LOW"),
        )
        assert PatternDetector(min_incidents=3).detect(observations) == []

    def test_repeat_incidents_are_counted_once(self):
        observations = self._observations(
            ("INC-1", GapType.NULL_HANDLING, "Profile", "LOW"),
            ("INC-1", GapType.EMPTY_INPUT, "Profile", "LOW"),
            ("INC-2", GapType.NULL_HANDLING, "Profile", "LOW"),
            ("INC-3", GapType.NULL_HANDLING, "Profile", "LOW"),
        )
        pattern = PatternDetector(min_incidents=3).detect(observations)[0]
        assert pattern.incident_count == 3
        assert pattern.gap_count == 4

    def test_patterns_are_ordered_by_incident_count(self):
        observations = self._observations(
            ("INC-1", GapType.PERMISSION, "Orders", "HIGH"),
            ("INC-2", GapType.PERMISSION, "Orders", "HIGH"),
            ("INC-3", GapType.PERMISSION, "Orders", "HIGH"),
            *[
                (f"INC-{i}", GapType.BOUNDARY_CONDITION, "Checkout", "HIGH")
                for i in range(10, 20)
            ],
        )
        patterns = PatternDetector(min_incidents=3).detect(observations)
        assert [p.key for p in patterns] == ["BOUNDARY_CONDITIONS", "PERMISSION_COMBINATIONS"]
