"""Incident normalisation.

Turns free-form production text into the same structured shape tests are
reduced to. No rigid format is required, a pasted Slack message, a Sentry
title, or a filled-in JSON object all work.

Crucially this uses the *same* `extraction` rules as test parsing. Production
conditions and test conditions are therefore comparable because they were read
by identical logic, not by two independently-tuned heuristics.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..config.logging_conf import get_logger
from ..domain.models import NormalizedIncident
from .extraction import (
    detect_signals,
    extract_conditions,
    infer_feature,
    merge_conditions,
)

log = get_logger(__name__)

_ROOT_CAUSE_RE = re.compile(
    r"(?:root\s*cause|rca|caused\s+by|reason)\s*[:\--]\s*(.+?)(?:\n\n|\n(?=[A-Z][a-z]+\s*:)|$)",
    re.IGNORECASE | re.DOTALL,
)
_FAILURE_RE = re.compile(
    r"([^.\n]*\b(?:fail(?:s|ed|ure)?|error|crash(?:ed|es)?|exception|timeout|timed out|rejected|"
    r"returned\s+\d{3}|unable to|could not|cannot|broke|incorrect|wrong|denied|hangs?|stuck)\b[^.\n]*)",
    re.IGNORECASE,
)
_TITLE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

_SEVERITY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("CRITICAL", ("outage", "all customers", "data loss", "sev1", "sev-1", "p0", "critical", "revenue impact")),
    ("HIGH", ("sev2", "sev-2", "p1", "high", "many customers", "checkout failed", "payment failed", "cannot complete")),
    ("LOW", ("cosmetic", "minor", "sev4", "sev-4", "p3", "low", "single user", "typo")),
)

#: Structured keys callers may supply directly; anything else lands in `extra`.
_KNOWN_KEYS = {
    "id", "title", "description", "feature", "scenario", "conditions",
    "failure", "root_cause", "signals", "severity", "occurred_at",
}


class IncidentNormalizer:
    """Free-form incident text -> `NormalizedIncident`."""

    def normalize(
        self,
        text: str,
        *,
        incident_id: str,
        structured: dict[str, Any] | None = None,
    ) -> NormalizedIncident:
        structured = dict(structured or {})
        description = (text or "").strip()
        if not description and structured.get("description"):
            description = str(structured["description"]).strip()
        if not description:
            raise ValueError("Incident text is empty.")

        title = str(structured.get("title") or self._title(description)).strip()
        root_cause = str(structured.get("root_cause") or self._root_cause(description)).strip()
        failure = str(structured.get("failure") or self._failure(description)).strip()

        # The title and failure sentence carry the signal; the root cause often
        # names the mechanism ("division by zero") that decides the gap type.
        corpus = " ".join(filter(None, [title, description, failure, root_cause]))

        feature = str(structured.get("feature") or "").strip().title() or infer_feature(
            title, description, failure, root_cause
        )

        conditions = merge_conditions(
            self._coerce_conditions(structured.get("conditions")),
            extract_conditions(title),
            extract_conditions(failure),
            extract_conditions(description),
            extract_conditions(root_cause),
        )

        signals = structured.get("signals") or detect_signals(corpus)
        scenario = str(structured.get("scenario") or "").strip() or self._scenario(
            feature, conditions, title
        )

        incident = NormalizedIncident(
            id=str(structured.get("id") or incident_id),
            title=title[:240],
            description=description,
            feature=feature,
            scenario=scenario,
            conditions=conditions,
            failure=failure[:500],
            root_cause=root_cause[:500],
            signals=list(signals),
            severity=str(structured.get("severity") or self._severity(corpus)).upper(),
            occurred_at=self._occurred_at(structured.get("occurred_at")),
            extra={k: v for k, v in structured.items() if k not in _KNOWN_KEYS},
        )

        log.info(
            "incident normalised",
            extra={
                "event": "incident.normalized",
                "incident": incident.id,
                "feature": incident.feature,
                "conditions": len(incident.conditions),
                "signals": len(incident.signals),
            },
        )
        return incident

    # -- field extraction ---------------------------------------------------

    def _title(self, description: str) -> str:
        first_line = description.strip().splitlines()[0].strip()
        # A short first line is almost always a title; a long one is prose.
        if len(first_line) <= 120:
            return first_line
        return _TITLE_SPLIT_RE.split(first_line)[0][:120]

    def _root_cause(self, description: str) -> str:
        match = _ROOT_CAUSE_RE.search(description)
        if not match:
            return ""
        return re.sub(r"\s+", " ", match.group(1)).strip()[:400]

    def _failure(self, description: str) -> str:
        # Skip a root-cause line when looking for the failure symptom.
        body = _ROOT_CAUSE_RE.sub(" ", description)
        match = _FAILURE_RE.search(body)
        return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""

    def _scenario(self, feature: str, conditions: dict[str, str], title: str) -> str:
        if conditions:
            rendered = ", ".join(f"{k} = {v}" for k, v in sorted(conditions.items()))
            return f"{feature}: {rendered}"
        return title[:160] or feature

    def _severity(self, corpus: str) -> str:
        lowered = corpus.lower()
        for severity, keywords in _SEVERITY_KEYWORDS:
            if any(keyword in lowered for keyword in keywords):
                return severity
        return "MEDIUM"

    def _occurred_at(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None

    def _coerce_conditions(self, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {str(k): str(v) for k, v in value.items() if k is not None and v is not None}
