"""Risk scoring.

Risk answers "how much should engineering care?". Unlike confidence it is about
the gap's consequences, and per spec §65 the rules are documented rather than
arbitrary.

    factor                points  rationale
    --------------------  ------  ------------------------------------------
    incident severity      0–3    CRITICAL 3 / HIGH 2 / MEDIUM 1 / LOW 0
    coverage level         0–2    NOT_COVERED 2 / PARTIAL 1 / COVERED 1
    feature criticality    0–2    money & identity paths score highest
    recurrence             0–3    how many prior incidents share this gap family

    total >= 6  -> HIGH
    total >= 3  -> MEDIUM
    otherwise   -> LOW

COVERED scores 1 rather than 0 because a scenario that was covered and still
failed indicates an ineffective test, which is its own kind of problem.
"""
from __future__ import annotations

from ..domain.enums import Coverage, Risk

SEVERITY_POINTS: dict[str, int] = {"CRITICAL": 3, "HIGH": 2, "MEDIUM": 1, "LOW": 0}

COVERAGE_POINTS: dict[Coverage, int] = {
    Coverage.NOT_COVERED: 2,
    Coverage.PARTIAL: 1,
    Coverage.COVERED: 1,
    Coverage.INSUFFICIENT_EVIDENCE: 0,
}

#: Features where a defect has direct financial or security consequences.
FEATURE_POINTS: dict[str, int] = {
    "Payments": 2,
    "Checkout": 2,
    "Authentication": 2,
    "Orders": 1,
    "Profile": 1,
    "Notifications": 1,
    "Search": 0,
}

HIGH_THRESHOLD = 6
MEDIUM_THRESHOLD = 3


def recurrence_points(similar_incident_count: int) -> int:
    """0 for a one-off, up to 3 for a well-established pattern."""
    if similar_incident_count >= 6:
        return 3
    if similar_incident_count >= 3:
        return 2
    if similar_incident_count >= 2:
        return 1
    return 0


class RiskScorer:
    def score(
        self,
        *,
        severity: str,
        coverage: Coverage,
        feature: str,
        similar_incident_count: int = 0,
    ) -> tuple[Risk, dict[str, int]]:
        breakdown = {
            "severity": SEVERITY_POINTS.get(severity.upper(), 1),
            "coverage": COVERAGE_POINTS.get(coverage, 1),
            "feature": FEATURE_POINTS.get(feature, 1),
            "recurrence": recurrence_points(similar_incident_count),
        }
        total = sum(breakdown.values())
        breakdown["total"] = total

        if total >= HIGH_THRESHOLD:
            return Risk.HIGH, breakdown
        if total >= MEDIUM_THRESHOLD:
            return Risk.MEDIUM, breakdown
        return Risk.LOW, breakdown
