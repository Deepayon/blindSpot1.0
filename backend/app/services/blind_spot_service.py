"""Recurring blind spot recomputation and queries."""
from __future__ import annotations

from typing import Any

from ..config.logging_conf import get_logger
from ..db.base import session_scope
from ..domain.models import BlindSpotPattern
from ..intelligence.pattern_detector import PatternDetector
from ..repositories.blind_spot_repository import BlindSpotRepository
from ..repositories.incident_repository import IncidentRepository

log = get_logger(__name__)


class BlindSpotService:
    def __init__(self, detector: PatternDetector | None = None) -> None:
        self.detector = detector or PatternDetector()

    def recompute(self) -> list[BlindSpotPattern]:
        """Re-derive every pattern from the current set of analyses."""
        with session_scope() as session:
            observations = IncidentRepository(session).gap_observations()
            patterns = self.detector.detect(observations)
            stored = BlindSpotRepository(session).replace_all(patterns)

        log.info(
            "blind spots recomputed",
            extra={
                "event": "patterns.recomputed",
                "observations": len(observations),
                "patterns": len(stored),
            },
        )
        return stored

    def list_patterns(self) -> list[BlindSpotPattern]:
        with session_scope() as session:
            return BlindSpotRepository(session).list_all()

    def get_pattern(self, identifier: str) -> dict[str, Any] | None:
        with session_scope() as session:
            pattern = BlindSpotRepository(session).get(identifier)
            if pattern is None:
                return None
            incidents = IncidentRepository(session).incidents_for_family(pattern.key)
        return {"pattern": pattern, "incidents": incidents}

    def count(self) -> int:
        with session_scope() as session:
            return BlindSpotRepository(session).count()
