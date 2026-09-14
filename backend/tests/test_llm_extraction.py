"""Model-assisted extraction.

The model reads language better than a regex vocabulary, which is why it is
allowed to contribute conditions and signals. These tests pin the guards that
make that safe: a fact without a verifiable quote is discarded, deterministic
values win, vocabularies are closed, and the verdict stays reproducible.
"""
from __future__ import annotations

from app.domain.models import NormalizedIncident
from app.intelligence.llm_enrichment import (
    MAX_LLM_CONDITIONS,
    LLMEnricher,
    _quote_supported,
)
from app.providers.llm.base import LLMProvider

REPORT = (
    "Checkout failed when a customer applied a 100% discount coupon during a promotion. "
    "Root cause: division by zero once the payable total reached zero."
)


class StubProvider(LLMProvider):
    """Returns a fixed payload, so the guards are what is under test."""

    name = "stub"

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.extraction_calls = 0
        self.prompt = ""

    @property
    def available(self) -> bool:
        return True

    def complete_json(self, *, system: str, prompt: str, max_tokens: int = 1024):
        self.calls += 1
        self.prompt = prompt
        # The enricher also rewrites explanations through this provider, so
        # extraction calls are counted separately.
        if prompt.startswith("FEATURES:"):
            self.extraction_calls += 1
        return self.payload


def _incident(**overrides) -> NormalizedIncident:
    base = {
        "id": "INC-1",
        "title": "Checkout failed",
        "description": REPORT,
        "feature": "Unknown",
        "conditions": {},
        "signals": [],
    }
    base.update(overrides)
    return NormalizedIncident(**base)


class TestQuoteVerification:
    def test_a_quoted_condition_is_accepted(self):
        provider = StubProvider(
            {
                "feature": "Checkout",
                "conditions": [
                    {"name": "discount", "value": "100%", "quote": "applied a 100% discount coupon"}
                ],
                "signals": [],
            }
        )
        incident = _incident()
        assert LLMEnricher(provider).extract_incident(incident, ["Checkout"]) is True
        assert incident.conditions["discount"] == "100%"
        assert incident.feature == "Checkout"

    def test_an_unquotable_condition_is_discarded(self):
        """A fact the model invented cannot be evidenced, so it never lands."""
        provider = StubProvider(
            {
                "conditions": [
                    {"name": "currency", "value": "JPY", "quote": "the currency was JPY"}
                ],
                "signals": [],
            }
        )
        incident = _incident()
        LLMEnricher(provider).extract_incident(incident, ["Checkout"])
        assert "currency" not in incident.conditions

    def test_a_missing_quote_is_discarded(self):
        provider = StubProvider(
            {"conditions": [{"name": "discount", "value": "100%"}], "signals": []}
        )
        incident = _incident()
        LLMEnricher(provider).extract_incident(incident, ["Checkout"])
        assert incident.conditions == {}

    def test_a_trivially_short_quote_proves_nothing(self):
        provider = StubProvider(
            {"conditions": [{"name": "discount", "value": "100%", "quote": "a"}], "signals": []}
        )
        incident = _incident()
        LLMEnricher(provider).extract_incident(incident, ["Checkout"])
        assert incident.conditions == {}

    def test_quote_matching_ignores_casing_and_punctuation(self):
        assert _quote_supported("APPLIED A 100% DISCOUNT", "applied a 100% discount coupon")
        assert not _quote_supported("applied a 50% discount", "applied a 100% discount coupon")


class TestDeterministicPrecedence:
    def test_the_model_cannot_overwrite_a_rule_derived_value(self):
        provider = StubProvider(
            {
                "conditions": [
                    {"name": "discount", "value": "5%", "quote": "applied a 100% discount coupon"}
                ],
                "signals": [],
            }
        )
        incident = _incident(conditions={"discount": "100%"})
        LLMEnricher(provider).extract_incident(incident, ["Checkout"])
        assert incident.conditions["discount"] == "100%"

    def test_a_supplied_feature_is_not_replaced(self):
        provider = StubProvider({"feature": "Payments", "conditions": [], "signals": []})
        incident = _incident(feature="Checkout")
        LLMEnricher(provider).extract_incident(incident, ["Checkout", "Payments"])
        assert incident.feature == "Checkout"


class TestClosedVocabularies:
    def test_an_unknown_signal_is_rejected(self):
        provider = StubProvider(
            {
                "conditions": [],
                "signals": [{"name": "gremlins", "quote": "division by zero once the payable"}],
            }
        )
        incident = _incident()
        LLMEnricher(provider).extract_incident(incident, ["Checkout"])
        assert incident.signals == []

    def test_a_known_signal_with_a_quote_is_accepted(self):
        provider = StubProvider(
            {
                "conditions": [],
                "signals": [
                    {"name": "error_handling", "quote": "division by zero once the payable"}
                ],
            }
        )
        incident = _incident()
        LLMEnricher(provider).extract_incident(incident, ["Checkout"])
        assert "error_handling" in incident.signals

    def test_a_feature_outside_the_learned_set_is_rejected(self):
        """The model may only choose a feature this organisation actually uses."""
        provider = StubProvider({"feature": "Warp Drive", "conditions": [], "signals": []})
        incident = _incident()
        LLMEnricher(provider).extract_incident(incident, ["Checkout", "Payments"])
        assert incident.feature == "Unknown"


class TestLimitsAndProvenance:
    def test_the_number_of_added_conditions_is_capped(self):
        provider = StubProvider(
            {
                "conditions": [
                    {"name": f"field_{i}", "value": str(i), "quote": "division by zero once"}
                    for i in range(MAX_LLM_CONDITIONS + 5)
                ],
                "signals": [],
            }
        )
        incident = _incident()
        LLMEnricher(provider).extract_incident(incident, ["Checkout"])
        assert len(incident.conditions) == MAX_LLM_CONDITIONS

    def test_accepted_facts_record_the_supporting_quote(self):
        provider = StubProvider(
            {
                "conditions": [
                    {"name": "discount", "value": "100%", "quote": "applied a 100% discount coupon"}
                ],
                "signals": [],
            }
        )
        incident = _incident()
        LLMEnricher(provider).extract_incident(incident, ["Checkout"])
        provenance = incident.extra["extracted_by_model"]
        assert "100% discount coupon" in provenance["condition:discount"]

    def test_the_prompt_offers_only_this_organisations_features(self):
        provider = StubProvider({"conditions": [], "signals": []})
        LLMEnricher(provider).extract_incident(_incident(), ["Underwriting", "Claims"])
        features_line = provider.prompt.splitlines()[0]
        assert features_line == "FEATURES: Underwriting, Claims"

    def test_a_failed_call_changes_nothing(self):
        provider = StubProvider(None)
        incident = _incident()
        assert LLMEnricher(provider).extract_incident(incident, ["Checkout"]) is False
        assert incident.conditions == {}
        assert incident.signals == []


class TestReproducibility:
    def test_a_finalised_incident_is_never_re_extracted(self, indexed_state):
        """Re-analysis must not be able to change a verdict because the model
        answered differently today."""
        from app.intelligence.engine import GapAnalysisEngine

        provider = StubProvider(
            {
                "conditions": [
                    {"name": "discount", "value": "100%", "quote": "applied a 100% discount coupon"}
                ],
                "signals": [],
            }
        )
        engine = GapAnalysisEngine(indexed_state.index, provider)

        engine.analyze(_incident(extra={"extraction_final": True}))
        assert provider.extraction_calls == 0, "a stored incident must reuse its persisted facts"

        engine.analyze(_incident())
        assert provider.extraction_calls == 1, "a new incident is extracted once"
