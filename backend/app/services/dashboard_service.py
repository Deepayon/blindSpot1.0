"""Dashboard aggregation (spec §25)."""
from __future__ import annotations

from typing import Any

from ..db.base import session_scope
from ..domain.enums import Coverage
from ..repositories.blind_spot_repository import BlindSpotRepository
from ..repositories.incident_repository import IncidentRepository
from ..repositories.run_repository import RunRepository
from ..repositories.test_repository import TestRepository
from .state import AppState


class DashboardService:
    def __init__(self, state: AppState) -> None:
        self.state = state

    def summary(self) -> dict[str, Any]:
        with session_scope() as session:
            tests = TestRepository(session)
            incidents = IncidentRepository(session)
            blind_spots = BlindSpotRepository(session)
            runs = RunRepository(session)

            coverage_counts = incidents.coverage_counts()
            test_stats = tests.stats()
            patterns = blind_spots.list_all()

            return {
                "metrics": {
                    "incidents": incidents.count_incidents(),
                    "tests_indexed": test_stats["total"],
                    "test_gaps": incidents.count_gaps(),
                    "blind_spots": len(patterns),
                    "covered": coverage_counts.get(Coverage.COVERED.value, 0),
                    "partial": coverage_counts.get(Coverage.PARTIAL.value, 0),
                    "not_covered": coverage_counts.get(Coverage.NOT_COVERED.value, 0),
                    "insufficient_evidence": coverage_counts.get(
                        Coverage.INSUFFICIENT_EVIDENCE.value, 0
                    ),
                },
                "tests": test_stats,
                "top_blind_spots": [pattern.model_dump(mode="json") for pattern in patterns[:5]],
                "recent_incidents": incidents.recent_incidents(limit=8),
                "recent_runs": [
                    {
                        "kind": run.kind,
                        "status": run.status,
                        "detail": run.detail,
                        "duration_ms": run.duration_ms,
                        "started_at": run.started_at,
                    }
                    for run in runs.recent(limit=6)
                ],
                "index": self.state.index.describe(),
                "ai": self.state.describe_ai(),
            }
