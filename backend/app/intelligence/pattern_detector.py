"""Recurring blind spot detection.

One gap is a bug. The same *kind* of gap four times is a testing-strategy
problem, and that is the finding engineering leadership can act on.

Gaps are grouped into the families declared in `domain/enums.py`
(`BLIND_SPOT_FAMILIES`) rather than by raw gap type, so that NULL_HANDLING,
EMPTY_INPUT and MISSING_FIELD roll up into one "Null / Empty Inputs" pattern
exactly as the spec's example requires.

Kept as a pure function over `GapObservation` records so it is trivially
testable and independent of the database.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..config.settings import get_settings
from ..domain.enums import BLIND_SPOT_FAMILIES, GapType, Risk
from ..domain.models import BlindSpotPattern
from .risk import SEVERITY_POINTS, recurrence_points

#: A family needs at least this many *distinct incidents* to count as recurring.
#: Configurable via BLINDSPOT_PATTERN_MIN_INCIDENTS.
DEFAULT_MIN_INCIDENTS = 3

#: Pattern risk thresholds (see `_score_pattern` for the point table).
HIGH_THRESHOLD = 7
MEDIUM_THRESHOLD = 4


@dataclass
class GapObservation:
    """One gap, flattened for pattern analysis."""

    incident_id: str
    gap_type: GapType
    family_key: str
    family_label: str
    feature: str = "Unknown"
    severity: str = "MEDIUM"
    coverage: str = "NOT_COVERED"


@dataclass
class _Bucket:
    key: str
    label: str
    incidents: set[str] = field(default_factory=set)
    gap_count: int = 0
    gap_types: Counter[str] = field(default_factory=Counter)
    features: Counter[str] = field(default_factory=Counter)
    severities: Counter[str] = field(default_factory=Counter)
    coverages: Counter[str] = field(default_factory=Counter)
    examples: list[str] = field(default_factory=list)


class PatternDetector:
    def __init__(self, min_incidents: int | None = None) -> None:
        if min_incidents is None:
            min_incidents = getattr(
                get_settings(), "blind_spot_min_incidents", DEFAULT_MIN_INCIDENTS
            )
        self.min_incidents = max(1, int(min_incidents))

    def detect(self, observations: list[GapObservation]) -> list[BlindSpotPattern]:
        """Group gaps into recurring patterns, strongest first."""
        buckets: dict[str, _Bucket] = {}

        for observation in observations:
            key = observation.family_key or "UNCATEGORISED"
            label = observation.family_label or BLIND_SPOT_FAMILIES.get(key, (key,))[0]
            bucket = buckets.get(key)
            if bucket is None:
                bucket = buckets[key] = _Bucket(key=key, label=label)

            bucket.incidents.add(observation.incident_id)
            bucket.gap_count += 1
            bucket.gap_types[observation.gap_type.value] += 1
            bucket.features[observation.feature] += 1
            bucket.severities[observation.severity.upper()] += 1
            bucket.coverages[observation.coverage] += 1
            if observation.incident_id not in bucket.examples and len(bucket.examples) < 12:
                bucket.examples.append(observation.incident_id)

        patterns = [
            self._to_pattern(bucket)
            for bucket in buckets.values()
            if len(bucket.incidents) >= self.min_incidents
        ]
        patterns.sort(key=lambda p: (-p.incident_count, p.label))
        return patterns

    def _to_pattern(self, bucket: _Bucket) -> BlindSpotPattern:
        incident_count = len(bucket.incidents)
        risk = self._score_pattern(bucket, incident_count)
        top_features = [feature for feature, _ in bucket.features.most_common(4)]
        concentration_feature, concentration_share = self._concentration(bucket)

        return BlindSpotPattern(
            key=bucket.key,
            label=bucket.label,
            incident_count=incident_count,
            gap_count=bucket.gap_count,
            risk=risk,
            gap_types=[GapType(value) for value, _ in bucket.gap_types.most_common()],
            features=top_features,
            example_incident_ids=bucket.examples,
            summary=self._summary(bucket, incident_count, top_features),
            concentrated_in=concentration_feature,
            concentration=round(concentration_share, 2),
        )

    def _score_pattern(self, bucket: _Bucket, incident_count: int) -> Risk:
        """Pattern risk.

            recurrence            0-3   how many incidents share the family
            worst severity        0-3   CRITICAL 3 / HIGH 2 / MEDIUM 1 / LOW 0
            uncovered proportion  0-2   share of incidents with no coverage at all
            feature spread        0-1   a pattern crossing 3+ features is systemic

            >= 7 HIGH, >= 4 MEDIUM, else LOW
        """
        recurrence = recurrence_points(incident_count)
        worst_severity = max(
            (SEVERITY_POINTS.get(severity, 1) for severity in bucket.severities),
            default=1,
        )
        uncovered = bucket.coverages.get("NOT_COVERED", 0)
        total = sum(bucket.coverages.values()) or 1
        uncovered_points = 2 if uncovered / total >= 0.6 else 1 if uncovered else 0
        spread_points = 1 if len(bucket.features) >= 3 else 0

        score = recurrence + worst_severity + uncovered_points + spread_points
        if score >= HIGH_THRESHOLD:
            return Risk.HIGH
        if score >= MEDIUM_THRESHOLD:
            return Risk.MEDIUM
        return Risk.LOW

    def _concentration(self, bucket: _Bucket) -> tuple[str, float]:
        """The feature carrying most of this pattern, and its share.

        Eight null-handling incidents reads as an organisation-wide weakness.
        If seven are in one service it is that service's weakness, and saying so
        is the difference between an actionable finding and a misleading one.
        """
        if not bucket.features:
            return "", 0.0
        feature, count = bucket.features.most_common(1)[0]
        total = sum(bucket.features.values()) or 1
        return feature, count / total

    def _summary(self, bucket: _Bucket, incident_count: int, features: list[str]) -> str:
        leading = bucket.gap_types.most_common(1)[0][0].replace("_", " ").lower()
        feature, share = self._concentration(bucket)

        # Lead with concentration where it exists. "Mostly in Payments" tells a
        # reader where to act; "across Payments, Orders, Search" does not.
        if share >= 0.6 and feature:
            where = f", {int(share * 100)}% of them in {feature}"
        elif len(features) > 1:
            where = f", spread across {', '.join(features[:3])}"
        elif features:
            where = f" in {features[0]}"
        else:
            where = ""

        return (
            f"{incident_count} incidents involve {bucket.label.lower()}{where}. "
            f"The most common gap is {leading}."
        )


def observations_from_pairs(pairs: list[tuple[str, GapType, str, str, str, str]]) -> list[GapObservation]:
    """Convenience adapter for `(incident_id, gap_type, family_key, family_label, feature, severity)`."""
    return [
        GapObservation(
            incident_id=incident_id,
            gap_type=gap_type,
            family_key=family_key,
            family_label=family_label,
            feature=feature,
            severity=severity,
        )
        for incident_id, gap_type, family_key, family_label, feature, severity in pairs
    ]
