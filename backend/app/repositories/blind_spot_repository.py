"""Persistence for recurring blind spot patterns."""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db.models import BlindSpot, GapRecord
from ..domain.enums import Risk
from ..domain.models import BlindSpotPattern, utcnow


class BlindSpotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def replace_all(self, patterns: list[BlindSpotPattern]) -> list[BlindSpotPattern]:
        """Recompute the blind-spot table from freshly detected patterns.

        Patterns are derived data: rather than trying to mutate them
        incrementally, the table is rewritten whenever analyses change. Existing
        rows are updated in place so their ids stay stable for the UI.
        """
        existing = {row.key: row for row in self.session.scalars(select(BlindSpot)).all()}
        detected_keys = {pattern.key for pattern in patterns}

        for pattern in patterns:
            row = existing.get(pattern.key)
            if row is None:
                row = BlindSpot(key=pattern.key)
                self.session.add(row)
            row.label = pattern.label
            row.incident_count = pattern.incident_count
            row.gap_count = pattern.gap_count
            row.risk = pattern.risk.value
            row.summary = pattern.summary
            row.detail = {
                "gap_types": [gap_type.value for gap_type in pattern.gap_types],
                "features": pattern.features,
                "example_incident_ids": pattern.example_incident_ids,
            }
            row.updated_at = utcnow()
            self.session.flush()
            pattern.id = row.id

        # A family that no longer recurs should disappear from the dashboard.
        for key, row in existing.items():
            if key not in detected_keys:
                self.session.delete(row)

        self.session.flush()
        self._link_gaps(patterns)
        return patterns

    def _link_gaps(self, patterns: list[BlindSpotPattern]) -> None:
        """Point each gap row at its blind spot so the UI can drill down."""
        by_key = {pattern.key: pattern.id for pattern in patterns if pattern.id}
        self.session.execute(update(GapRecord).values(blind_spot_id=None))
        for key, blind_spot_id in by_key.items():
            self.session.execute(
                update(GapRecord)
                .where(GapRecord.family_key == key)
                .values(blind_spot_id=blind_spot_id)
            )

    def list_all(self) -> list[BlindSpotPattern]:
        rows = self.session.scalars(
            select(BlindSpot).order_by(BlindSpot.incident_count.desc(), BlindSpot.label)
        ).all()
        return [self._to_pattern(row) for row in rows]

    def get(self, identifier: str | int) -> BlindSpotPattern | None:
        row = None
        if isinstance(identifier, int) or str(identifier).isdigit():
            row = self.session.get(BlindSpot, int(identifier))
        if row is None:
            row = self.session.scalar(select(BlindSpot).where(BlindSpot.key == str(identifier)))
        return self._to_pattern(row) if row else None

    def count(self) -> int:
        return len(self.session.scalars(select(BlindSpot.id)).all())

    def _to_pattern(self, row: BlindSpot) -> BlindSpotPattern:
        from ..domain.enums import GapType

        detail = row.detail or {}
        gap_types = []
        for value in detail.get("gap_types", []):
            try:
                gap_types.append(GapType(value))
            except ValueError:
                continue
        return BlindSpotPattern(
            id=row.id,
            key=row.key,
            label=row.label,
            incident_count=row.incident_count,
            gap_count=row.gap_count,
            risk=Risk(row.risk) if row.risk in Risk.__members__ else Risk.LOW,
            gap_types=gap_types,
            features=detail.get("features", []),
            example_incident_ids=detail.get("example_incident_ids", []),
            summary=row.summary or "",
        )
