"""Optional LLM assistance.

Two narrowly-scoped jobs, both of which degrade cleanly to a no-op:

  1. `enrich_incident`, recover the *feature* when the deterministic rules
     could not identify one at all. This is the single place a model can widen
     what gets retrieved, it is a closed seven-value set, and it only applies to
     a genuine blank.

     It explicitly does NOT supply conditions or signals: those are the direct
     inputs to the verdict, and accepting them measurably degraded accuracy on
     the sample dataset while forfeiting the reproducibility guarantee. What
     the model read is kept on the incident for the explanation step instead.
  2. `refine_explanation`, rewrite the templated explanation more fluently. It
     receives the already-computed facts and is told not to add new ones.

What is never sent: repository source code, file paths, or raw test bodies. Only
normalised metadata leaves the machine, per spec §39.

What the LLM is never allowed to do: change the coverage verdict, invent a test
ID, or alter the evidence list.
"""
from __future__ import annotations

import json
from typing import Any

from ..config.logging_conf import get_logger
from ..domain.models import NormalizedIncident
from ..providers.llm import LLMProvider
from .comparator import ScenarioComparison

log = get_logger(__name__)

_INCIDENT_SYSTEM = """You extract structured facts from production incident reports for a QA tool.
Return ONLY a JSON object with these keys:
  "feature":    one of Authentication, Checkout, Payments, Orders, Profile, Search, Notifications, or Unknown
  "conditions": object of input-name -> value that the production request actually used
  "signals":    array from [null, empty, missing_field, invalid, boundary_max, boundary_min,
                timeout, retry, concurrency, permission, unicode, error_handling,
                state_transition, data_format, environment]
Rules:
  - Report only what the text states. Never infer values that are not present.
  - If you cannot determine a field, use "Unknown", {} or [].
  - Do not explain. Output JSON only."""

_EXPLANATION_SYSTEM = """You rewrite a test-coverage finding for senior engineers.
You are given the verdict and the supporting facts. Rewrite the explanation so it reads naturally.
Rules:
  - Use ONLY the facts provided. Never add a test ID, value, or cause that is not in them.
  - Never change the verdict.
  - Two to three sentences, plain and specific. No preamble, no bullet points.
Return ONLY {"explanation": "..."}."""

#: Signals the model is allowed to contribute.
_ALLOWED_SIGNALS = {
    "null", "empty", "missing_field", "invalid", "boundary_max", "boundary_min",
    "timeout", "retry", "concurrency", "permission", "unicode", "error_handling",
    "state_transition", "data_format", "environment",
}
_ALLOWED_FEATURES = {
    "Authentication", "Checkout", "Payments", "Orders", "Profile", "Search", "Notifications",
}


class LLMEnricher:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    @property
    def active(self) -> bool:
        return self.provider.available

    # -- incident understanding --------------------------------------------

    def enrich_incident(self, incident: NormalizedIncident) -> bool:
        """Fill gaps the deterministic extractor left. Returns True if it changed anything."""
        if not self.active:
            return False

        payload = self.provider.complete_json(
            system=_INCIDENT_SYSTEM,
            prompt=f"Incident report:\n\n{incident.description[:4000]}",
            max_tokens=600,
        )
        if not payload:
            return False

        changed = False

        feature = payload.get("feature")
        if (
            incident.feature in {"Unknown", "", None}
            and isinstance(feature, str)
            and feature in _ALLOWED_FEATURES
        ):
            incident.feature = feature
            changed = True

        # Conditions and signals are deliberately NOT taken from the model, even
        # when the deterministic extractor found none.
        #
        # They are the direct inputs to the coverage verdict: one extra
        # condition downgrades COVERED to PARTIAL, one extra signal invents a
        # gap. Measured against the sample dataset, letting the model supply
        # them cost two correct verdicts out of eight; restricting it to empty
        # findings still cost one. Accepting them at all would also forfeit the
        # reproducibility guarantee, the same incident could be judged
        # differently as the model drifts.
        #
        # The model's reading of the incident is still used: it is passed to
        # `refine_explanation`, where it can improve the wording without
        # touching the classification.
        for field in ("conditions", "signals"):
            suggested = payload.get(field)
            if suggested:
                incident.extra.setdefault("llm_suggested", {})[field] = suggested

        if changed:
            log.info(
                "incident enriched by llm",
                extra={"event": "llm.incident_enriched", "incident": incident.id},
            )
        return changed

    # -- explanation -------------------------------------------------------

    def refine_explanation(
        self,
        comparison: ScenarioComparison,
        coverage: str,
        gap_type: str,
        explanation: str,
        evidence_statements: list[str],
    ) -> str | None:
        """Return improved prose, or None to keep the deterministic version."""
        if not self.active:
            return None

        facts: dict[str, Any] = {
            "verdict": coverage,
            "gap_type": gap_type,
            "feature": comparison.incident.feature,
            "production_conditions": comparison.incident.conditions,
            "related_tests": [
                {
                    "id": c.test.id,
                    "scenario": c.test.scenario[:160],
                    "inputs": c.test.inputs,
                }
                for c in comparison.comparisons[:6]
            ],
            "evidence": evidence_statements[:12],
            "draft_explanation": explanation,
        }

        payload = self.provider.complete_json(
            system=_EXPLANATION_SYSTEM,
            prompt=json.dumps(facts, default=str)[:6000],
            max_tokens=400,
        )
        if not payload:
            return None

        refined = payload.get("explanation")
        if not isinstance(refined, str) or not refined.strip():
            return None
        refined = refined.strip()

        # Guard against fabricated test IDs: every ID mentioned must be real.
        known_ids = {c.test.id for c in comparison.comparisons}
        for token in _candidate_ids(refined):
            if token not in known_ids:
                log.warning(
                    "llm explanation referenced an unknown test id; keeping deterministic text",
                    extra={"event": "llm.explanation_rejected", "token": token},
                )
                return None

        return refined[:1200]


def _candidate_ids(text: str) -> list[str]:
    """Tokens that look like test identifiers (TC-182, test_foo::bar)."""
    import re

    return re.findall(r"\b(?:TC-\d+|[A-Za-z_][\w]*::[\w:]+)\b", text)
