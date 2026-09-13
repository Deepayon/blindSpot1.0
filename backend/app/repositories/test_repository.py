"""Persistence for test sources and normalised tests."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config.settings import get_settings
from ..db.models import TestCase, TestSource
from ..domain.models import NormalizedTest, TestSourceInfo, utcnow
from ..parsers.base import fingerprint


class TestRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- sources ------------------------------------------------------------

    def upsert_source(self, *, kind: str, location: str, label: str) -> TestSource:
        """Find or create the source row for (kind, location)."""
        source = self.session.scalar(
            select(TestSource).where(TestSource.kind == kind, TestSource.location == location)
        )
        if source is None:
            source = TestSource(kind=kind, location=location, label=label)
            self.session.add(source)
            self.session.flush()
        else:
            source.label = label or source.label
        return source

    def list_sources(self) -> list[TestSourceInfo]:
        sources = self.session.scalars(select(TestSource).order_by(TestSource.id)).all()
        return [self.to_source_info(source) for source in sources]

    def get_source(self, source_id: int) -> TestSource | None:
        return self.session.get(TestSource, source_id)

    def delete_source(self, source_id: int) -> bool:
        source = self.session.get(TestSource, source_id)
        if source is None:
            return False
        self.session.delete(source)
        return True

    def to_source_info(self, source: TestSource) -> TestSourceInfo:
        return TestSourceInfo(
            id=source.id,
            kind=source.kind,
            location=source.location,
            label=source.label,
            test_count=source.test_count,
            indexed_at=source.indexed_at,
            status=source.status,
            detail=source.detail or {},
        )

    # -- tests --------------------------------------------------------------

    def replace_tests(
        self, source: TestSource, tests: Sequence[NormalizedTest]
    ) -> tuple[int, int]:
        """Replace this source's tests. Returns (stored, duplicates_skipped).

        Re-indexing is a replace rather than an append so a removed test also
        disappears from the index, otherwise the suite would only ever grow.
        """
        self.session.execute(delete(TestCase).where(TestCase.source_id == source.id))
        self.session.flush()

        # Fingerprints owned by *other* sources: an identical test imported twice
        # (e.g. the same case in a CSV and in the repo) is stored once.
        existing = set(
            self.session.scalars(
                select(TestCase.fingerprint).where(TestCase.source_id != source.id)
            ).all()
        )

        seen_fingerprints: set[str] = set()
        seen_ids: set[str] = set()
        duplicates = 0
        rows: list[TestCase] = []

        # Privacy: the analysis engine works from normalised metadata, so the
        # raw test body is dropped before it reaches the database unless the
        # operator has explicitly opted in on a machine they own.
        retain_code = get_settings().store_source_code

        for test in tests:
            digest = fingerprint(test)
            if digest in seen_fingerprints or digest in existing:
                duplicates += 1
                continue
            seen_fingerprints.add(digest)

            external_id = self._unique_external_id(test.id, seen_ids)
            seen_ids.add(external_id)

            rows.append(
                TestCase(
                    source_id=source.id,
                    external_id=external_id,
                    name=test.name[:512],
                    feature=test.feature[:128],
                    scenario=test.scenario,
                    expected_behavior=test.expected_behavior,
                    inputs=test.inputs,
                    tags=test.tags,
                    assertions=test.assertions,
                    literals=test.literals,
                    file_path=test.source,
                    framework=test.framework,
                    line_number=test.line_number,
                    code=test.code if retain_code else None,
                    fingerprint=digest,
                    extra=test.extra,
                )
            )

        if rows:
            self.session.add_all(rows)

        source.test_count = len(rows)
        source.indexed_at = utcnow()
        source.status = "READY"
        self.session.flush()
        return len(rows), duplicates

    def _unique_external_id(self, candidate: str, seen: set[str]) -> str:
        """Keep (source_id, external_id) unique without dropping the test."""
        base = (candidate or "TEST")[:120]
        if base not in seen:
            return base
        for suffix in range(2, 10_000):
            variant = f"{base}#{suffix}"
            if variant not in seen:
                return variant
        return f"{base}#{len(seen)}"

    def all_normalized(self) -> list[NormalizedTest]:
        """Every stored test, as the domain model the index consumes."""
        rows = self.session.scalars(select(TestCase).order_by(TestCase.id)).all()
        return [self._to_normalized(row) for row in rows]

    def _to_normalized(self, row: TestCase) -> NormalizedTest:
        return NormalizedTest(
            id=row.external_id,
            name=row.name,
            feature=row.feature,
            scenario=row.scenario or "",
            inputs=row.inputs or {},
            expected_behavior=row.expected_behavior or "",
            tags=row.tags or [],
            source=row.file_path or "",
            framework=row.framework,
            line_number=row.line_number,
            code=row.code,
            assertions=row.assertions or [],
            literals=row.literals or [],
            extra=row.extra or {},
        )

    def count(self) -> int:
        return int(self.session.scalar(select(func.count(TestCase.id))) or 0)

    def stats(self) -> dict[str, object]:
        by_feature = dict(
            self.session.execute(
                select(TestCase.feature, func.count(TestCase.id)).group_by(TestCase.feature)
            ).all()
        )
        by_framework = dict(
            self.session.execute(
                select(TestCase.framework, func.count(TestCase.id)).group_by(TestCase.framework)
            ).all()
        )
        last_indexed: datetime | None = self.session.scalar(
            select(func.max(TestSource.indexed_at))
        )
        return {
            "total": self.count(),
            "sources": int(self.session.scalar(select(func.count(TestSource.id))) or 0),
            "by_feature": by_feature,
            "by_framework": by_framework,
            "last_indexed": last_indexed.replace(tzinfo=UTC).isoformat()
            if isinstance(last_indexed, datetime)
            else None,
        }

    def search(
        self,
        *,
        query: str | None = None,
        feature: str | None = None,
        framework: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[NormalizedTest], int]:
        statement = select(TestCase)
        if query:
            like = f"%{query.lower()}%"
            statement = statement.where(
                func.lower(TestCase.name).like(like)
                | func.lower(TestCase.scenario).like(like)
                | func.lower(TestCase.external_id).like(like)
            )
        if feature:
            statement = statement.where(TestCase.feature == feature)
        if framework:
            statement = statement.where(TestCase.framework == framework)

        total = int(
            self.session.scalar(
                select(func.count()).select_from(statement.subquery())
            )
            or 0
        )
        rows = self.session.scalars(
            statement.order_by(TestCase.id).limit(limit).offset(offset)
        ).all()
        return [self._to_normalized(row) for row in rows], total

