"""Deterministic extraction, the foundation every verdict rests on."""
from __future__ import annotations

import pytest

from app.domain.text import as_number, normalize, values_equivalent
from app.intelligence.extraction import (
    derive_feature,
    derive_test_signals,
    detect_signals,
    extract_conditions,
)
from app.intelligence.vocabulary import build_vocabulary


class TestConditionExtraction:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Checkout failed with a 100% discount coupon", {"discount": "100%"}),
            ("Checkout with a 10 percent discount", {"discount": "10%"}),
            ("Order rejected when the cart quantity was 0", {"cart_quantity": "0"}),
            ("A page size of 500 results", {"page_size": "500"}),
            ("Listing orders with page 10000", {"page": "10000"}),
            ("the email field was null", {"email": "null"}),
            ("A declined card produced a confusing message", {"card": "declined"}),
            ("Login with an unknown username was rejected", {"username": "unknown"}),
        ],
    )
    def test_extracts_expected_condition(self, text, expected):
        conditions = extract_conditions(text)
        for key, value in expected.items():
            assert conditions.get(key) == value, f"{key} from {text!r} -> {conditions}"

    def test_negative_values_survive_normalisation(self):
        """Regression: normalize() used to turn "-5" into " 5"."""
        assert extract_conditions("a tax rate of -5 was configured")["tax_rate"] == "-5"
        assert extract_conditions("A negative discount of -10%")["discount"] == "-10%"

    def test_unrecognised_nouns_are_not_invented(self):
        """A wrong condition is worse than a missing one."""
        assert extract_conditions("The widget frobnicated 7 times") == {}

    def test_explicit_value_outranks_adjective(self):
        """In "page 10000 returned an empty page", the input is 10000."""
        conditions = extract_conditions("Listing orders with page 10000 returned an empty page")
        assert conditions["page"] == "10000"


class TestFeatureDerivation:
    """A feature name comes from where a test lives, never from a keyword list.

    The previous implementation hardcoded seven e-commerce domains, which meant
    every test in an insurance or logistics suite filed as "Unknown".
    """

    @pytest.mark.parametrize(
        "source,expected",
        [
            # The directory is the feature; the file is the scenario within it.
            ("tests/checkout/test_discounts.py", "Checkout"),
            ("tests/payments/test_refunds.py", "Payments"),
            # Flat layout: the file name is the only signal available.
            ("test_payments.py", "Payments"),
            ("tests/test_authentication.py", "Authentication"),
            # A worksheet states its own subject.
            ("regression_suite.xlsx[Notifications]", "Notifications"),
            # Domains the old hardcoded list could never have matched.
            ("src/claims_adjudication/test_eligibility.py", "Claims Adjudication"),
            ("e2e/specs/underwriting/policy.spec.ts", "Underwriting"),
            ("tests/freight_dispatch/test_routing.py", "Freight Dispatch"),
        ],
    )
    def test_derives_feature_from_provenance(self, source, expected):
        assert derive_feature(source, default="Unknown") == expected

    @pytest.mark.parametrize("source", ["", "tests/__init__.py", "tests/conftest.py", "src/utils/"])
    def test_structural_paths_yield_no_feature(self, source):
        assert derive_feature(source, default="Unknown") == "Unknown"


class TestLearnedVocabulary:
    """Incidents are classified against the features the corpus actually uses."""

    def _corpus(self):
        from tests.conftest import make_test

        return [
            make_test(f"U-{i}", f"underwriting_case_{i}", "Underwriting",
                      "Underwriting risk assessment for a new policy application")
            for i in range(4)
        ] + [
            make_test(f"C-{i}", f"claims_case_{i}", "Claims",
                      "Claims adjudication after a submitted accident report")
            for i in range(4)
        ]

    def test_learns_features_from_the_corpus(self):
        vocabulary = build_vocabulary(self._corpus())
        assert set(vocabulary.features) == {"Underwriting", "Claims"}

    def test_classifies_an_incident_into_a_learned_feature(self):
        vocabulary = build_vocabulary(self._corpus())
        assert vocabulary.classify("Risk assessment failed for a new policy") == "Underwriting"
        assert vocabulary.classify("Adjudication of an accident report crashed") == "Claims"

    def test_unrelated_text_is_not_forced_into_a_feature(self):
        vocabulary = build_vocabulary(self._corpus())
        assert vocabulary.classify("The quick brown fox") == "Unknown"

    def test_empty_corpus_classifies_nothing(self):
        assert build_vocabulary([]).classify("anything at all") == "Unknown"

    def test_a_feature_with_too_few_tests_is_not_learned(self):
        """One oddly-named test must not define a whole feature."""
        from tests.conftest import make_test

        corpus = [*self._corpus(), make_test("X-1", "odd", "Typo Feature", "A one-off test")]
        assert "Typo Feature" not in build_vocabulary(corpus).features

    def test_supplied_feature_is_matched_against_the_known_set(self):
        vocabulary = build_vocabulary(self._corpus())
        assert vocabulary.matches("underwriting") == "Underwriting"
        assert vocabulary.matches("Nonexistent") is None


class TestSignalDetection:
    @pytest.mark.parametrize(
        "text,signal",
        [
            ("Checkout failed with a 100% discount", "boundary_max"),
            ("Payment failed after a gateway timeout", "timeout"),
            ("an immediate retry occurred", "retry"),
            ("Two concurrent checkout requests", "concurrency"),
            ("A read-only role could cancel the order", "permission"),
            ("display name contains unicode characters", "unicode"),
            ("returned an unhandled exception", "error_handling"),
            ("the email field was null", "null"),
        ],
    )
    def test_detects_signal(self, text, signal):
        assert signal in detect_signals(text)

    def test_expired_coupon_is_not_a_timeout(self):
        """Regression: a business-rule expiry was being read as timeout coverage."""
        assert "timeout" not in detect_signals("Checkout with an expired coupon")

    def test_division_by_zero_is_not_a_minimum_boundary(self):
        signals = detect_signals("Root cause: division by zero in the discount calculation")
        assert "error_handling" in signals
        assert "boundary_min" not in signals

    def test_state_transition_requires_an_ordering(self):
        """Regression: bare "status" matched nearly every test."""
        assert "state_transition" not in detect_signals("Checkout with a single item")
        assert "state_transition" in detect_signals("an order moved from shipped back to pending")


class TestDerivedTestSignals:
    def test_input_values_are_signals(self):
        signals = derive_test_signals("profile_update", "Updating the display name", {"name": "null"})
        assert "null" in signals

    def test_percentage_boundary_from_input(self):
        signals = derive_test_signals("checkout", "Checkout with a discount", {"discount": "100%"})
        assert "boundary_max" in signals

    def test_assertions_are_not_treated_as_exercised_behaviour(self):
        """A test asserting `result.status == ok` is not a state-transition test.

        Signals must come from what a test drives, not what it checks.
        """
        signals = derive_test_signals("checkout_single_item", "Checkout with a single item", {})
        assert signals == []


class TestValueComparison:
    @pytest.mark.parametrize(
        "left,right,expected",
        [
            ("10%", 10, True),
            ("10%", "10 percent", True),
            ("100%", "10%", False),
            ("null", "NULL", True),
            (0, "0", True),
        ],
    )
    def test_values_equivalent(self, left, right, expected):
        assert values_equivalent(left, right) is expected

    def test_as_number_handles_percentages(self):
        assert as_number("100%") == 100.0
        assert as_number("-5") == -5.0
        assert as_number("not a number at all here") is None

    def test_normalize_splits_word_hyphens_but_keeps_negatives(self):
        assert normalize("sign-up") == "sign up"
        assert "-5" in normalize("a rate of -5")
