"""Deterministic extraction — the foundation every verdict rests on."""
from __future__ import annotations

import pytest

from app.domain.text import as_number, normalize, values_equivalent
from app.intelligence.extraction import (
    derive_test_signals,
    detect_signals,
    extract_conditions,
    infer_feature,
)


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


class TestFeatureInference:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Checkout failed with a discount coupon", "Checkout"),
            ("Payment gateway declined the card", "Payments"),
            ("test_login_with_an_unknown_username", "Authentication"),
            ("test_payments", "Payments"),
            ("Searching for a product by name", "Search"),
            ("Order confirmation email is sent", "Orders"),
        ],
    )
    def test_infers_feature(self, text, expected):
        assert infer_feature(text, default="Unknown") == expected

    def test_unknown_when_nothing_matches(self):
        assert infer_feature("The quick brown fox", default="Unknown") == "Unknown"

    def test_plural_and_gerund_forms_match(self):
        """Regression: `test_payments.py` matched no keyword, so its tests were "Unknown"."""
        assert infer_feature("payments", default="Unknown") == "Payments"
        assert infer_feature("Searching", default="Unknown") == "Search"


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
