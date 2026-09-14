"""Optional LLM assistance.

Two narrowly-scoped jobs, both of which degrade cleanly to a no-op:

  1. `extract_incident`, read the report and contribute structured facts the
     deterministic rules missed. A regex vocabulary only understands the phrasing
     it was written for; a model understands the phrasing a customer actually
     used. That is the single biggest accuracy constraint in the product, so the
     model is given this job.

     It is made safe rather than trusted. Every contributed fact must carry a
     verbatim quote from the report, which is verified against the text, so an
     invented condition is discarded instead of silently changing a verdict.
     Deterministic values always win, signals come from a fixed vocabulary, and
     features from the vocabulary learned from the customer's own suite.

  2. `refine_explanation`, rewrite the templated explanation more fluently. It
     receives the already-computed facts and is told not to add new ones.

The verdict itself is never the model's. Comparison, classification, confidence
and risk stay deterministic, and the extracted facts are persisted so that
re-analysing an incident reuses them rather than asking again.

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
from ..domain.text import normalize
from ..providers.llm import LLMProvider
from .comparator import ScenarioComparison
from .extraction import SIGNAL_PATTERNS

log = get_logger(__name__)

_INCIDENT_SYSTEM = """You extract structured facts from a production incident report for a test-coverage tool.

Return ONLY a JSON object:
{
  "feature": "<one of the FEATURES listed in the prompt, or Unknown>",
  "conditions": [
    {"name": "<input name>", "value": "<value production used>", "quote": "<verbatim span from the report>"}
  ],
  "signals": [
    {"name": "<one of the SIGNALS listed in the prompt>", "quote": "<verbatim span from the report>"}
  ]
}

Rules:
  - "quote" MUST be copied character for character from the report. It is checked.
    If you cannot quote it, omit the item.
  - A condition is an input the failing request actually used, such as a discount
    percentage, a field that was null, or a role. Not a symptom, not an outcome.
  - "feature" must be chosen from the provided list. Use "Unknown" if none fits.
  - Report only what the text states. Never infer a value that is not present.
  - Do not explain. Output JSON only."""

_EXPLANATION_SYSTEM = """You rewrite a test-coverage finding for senior engineers.
You are given the verdict and the supporting facts. Rewrite the explanation so it reads naturally.
Rules:
  - Use ONLY the facts provided. Never add a test ID, value, or cause that is not in them.
  - Never change the verdict.
  - Two to three sentences, plain and specific. No preamble, no bullet points.
Return ONLY {"explanation": "..."}."""

#: Signals the model may contribute. Anything outside this set is discarded, so
#: an invented signal cannot reach the classifier.
_ALLOWED_SIGNALS = frozenset(SIGNAL_PATTERNS)

#: Caps on what one response may add, so a verbose or looping model cannot
#: flood the comparison with conditions.
MAX_LLM_CONDITIONS = 6
MAX_LLM_SIGNALS = 6

#: A quote shorter than this proves nothing: "a" appears in every report.
MIN_QUOTE_CHARS = 6


def _quote_supported(quote: object, haystack: str) -> bool:
    """Whether a claimed quote really appears in the incident text.

    This is the guard that makes model-supplied facts safe to use. A condition
    the model invented cannot be evidenced, so requiring a verifiable quote
    turns hallucination from an invisible failure into a discarded item.
    Comparison is on normalised text so punctuation and casing do not matter.
    """
    if not isinstance(quote, str) or len(quote.strip()) < MIN_QUOTE_CHARS:
        return False
    return normalize(quote).strip() in haystack


class LLMEnricher:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    @property
    def active(self) -> bool:
        return self.provider.available

    # -- incident understanding --------------------------------------------

    def extract_incident(
        self, incident: NormalizedIncident, features: list[str] | None = None
    ) -> bool:
        """Add facts the deterministic rules missed. Returns True if it changed anything.

        The model reads language far better than a regex vocabulary, which is
        why it is allowed to contribute conditions and signals at all. Three
        rules keep that safe:

          1. Every item must carry a verbatim quote from the report, which is
             checked against the text. Unquotable items are dropped.
          2. Deterministic values always win. The model may only add a key the
             rules did not produce, never overwrite one.
          3. Signals and features are restricted to known vocabularies.

        Reproducibility is preserved by the caller, which persists the merged
        result and reuses it on re-analysis rather than extracting again.
        """
        if not self.active:
            return False

        known = features or []
        payload = self.provider.complete_json(
            system=_INCIDENT_SYSTEM,
            prompt=(
                f"FEATURES: {', '.join(known) if known else 'Unknown'}\n"
                f"SIGNALS: {', '.join(sorted(_ALLOWED_SIGNALS))}\n\n"
                f"Incident report:\n\n{incident.description[:4000]}"
            ),
            max_tokens=800,
        )
        if not payload:
            return False

        haystack = normalize(incident.description)
        provenance: dict[str, str] = {}
        rejected = 0
        changed = False

        feature = payload.get("feature")
        if (
            incident.feature in {"Unknown", "", None}
            and isinstance(feature, str)
            and feature in set(known)
        ):
            incident.feature = feature
            provenance["feature"] = "model"
            changed = True

        added = 0
        for item in payload.get("conditions") or []:
            if added >= MAX_LLM_CONDITIONS:
                break
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip().lower().replace(" ", "_")[:40]
            value = item.get("value")
            if not name or value is None or name in incident.conditions:
                continue
            if not _quote_supported(item.get("quote"), haystack):
                rejected += 1
                continue
            incident.conditions[name] = str(value)[:80]
            provenance[f"condition:{name}"] = str(item.get("quote"))[:200]
            added += 1
            changed = True

        added = 0
        for item in payload.get("signals") or []:
            if added >= MAX_LLM_SIGNALS:
                break
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip().lower()
            if name not in _ALLOWED_SIGNALS or name in incident.signals:
                continue
            if not _quote_supported(item.get("quote"), haystack):
                rejected += 1
                continue
            incident.signals.append(name)
            provenance[f"signal:{name}"] = str(item.get("quote"))[:200]
            added += 1
            changed = True

        if provenance:
            # Kept on the incident so the UI can show which facts came from the
            # model and what text supports each one.
            incident.extra["extracted_by_model"] = provenance

        if changed or rejected:
            log.info(
                "incident extraction assisted by llm",
                extra={
                    "event": "llm.incident_extracted",
                    "incident": incident.id,
                    "accepted": len(provenance),
                    "rejected_unquoted": rejected,
                },
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
