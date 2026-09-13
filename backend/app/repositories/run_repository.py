"""Audit trail for indexing and analysis batches (spec §44, §53)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..db.models import AnalysisRun


class RunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record(
        self,
        *,
        kind: str,
        status: str,
        detail: dict[str, Any],
        duration_ms: int,
    ) -> AnalysisRun:
        run = AnalysisRun(
            kind=kind,
            status=status,
            detail=detail,
            duration_ms=duration_ms,
            finished_at=datetime.now(UTC).replace(tzinfo=None),
        )
        self.session.add(run)
        self.session.flush()
        return run

    def recent(self, limit: int = 20) -> list[AnalysisRun]:
        return list(
            self.session.scalars(
                select(AnalysisRun).order_by(desc(AnalysisRun.started_at)).limit(limit)
            ).all()
        )
