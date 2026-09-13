"""Test ingestion orchestration.

Ties parsers -> persistence -> index rebuild into the two operations the API
exposes: index a file (CSV/Excel) and index a local repository.

Ingestion is resilient by contract: a malformed file or row produces an
`IngestionIssue` in the result, never an exception that aborts the run.
"""
from __future__ import annotations

import time
from pathlib import Path

from ..config.logging_conf import get_logger
from ..config.settings import Settings, get_settings
from ..db.base import session_scope
from ..domain.enums import TestSourceKind
from ..domain.models import IngestionResult, TestSourceInfo
from ..parsers.base import ParseOutcome
from ..parsers.csv_parser import CsvTestParser
from ..parsers.excel_parser import ExcelTestParser
from ..parsers.repository_scanner import (
    RepositoryPathError,
    RepositoryScanner,
    validate_repository_path,
)
from ..repositories.run_repository import RunRepository
from ..repositories.test_repository import TestRepository
from .state import AppState

log = get_logger(__name__)


class IngestionService:
    def __init__(self, state: AppState, settings: Settings | None = None) -> None:
        self.state = state
        self.settings = settings or get_settings()
        self.csv_parser = CsvTestParser()
        self.excel_parser = ExcelTestParser()

    # -- public operations --------------------------------------------------

    def index_file(self, path: Path, *, original_name: str | None = None) -> IngestionResult:
        """Index a CSV or Excel test export."""
        started = time.perf_counter()
        label = original_name or path.name

        if self.csv_parser.supports(path):
            parser, kind = self.csv_parser, TestSourceKind.CSV
        elif self.excel_parser.supports(path):
            parser, kind = self.excel_parser, TestSourceKind.EXCEL
        else:
            raise UnsupportedFileError(
                f"Unsupported file type '{path.suffix or path.name}'. "
                "Supported: .csv, .tsv, .xlsx, .xlsm."
            )

        outcome = parser.parse(path)
        # Report the user's filename, not the temporary upload path.
        for test in outcome.tests:
            test.source = label

        return self._persist(
            outcome,
            kind=kind.value,
            location=label,
            label=label,
            started=started,
        )

    def index_repository(self, raw_path: str) -> IngestionResult:
        """Index a local project directory (never uploaded, never executed)."""
        started = time.perf_counter()
        root = validate_repository_path(raw_path, self.settings)
        outcome = RepositoryScanner(settings=self.settings).scan(root)

        return self._persist(
            outcome,
            kind=TestSourceKind.REPOSITORY.value,
            location=str(root),
            label=root.name,
            started=started,
        )

    def reindex(self) -> int:
        """Rebuild the search index from stored tests without re-parsing."""
        return self.state.rebuild_index()

    def delete_source(self, source_id: int) -> bool:
        with session_scope() as session:
            deleted = TestRepository(session).delete_source(source_id)
        if deleted:
            self.state.rebuild_index()
        return deleted

    def list_sources(self) -> list[TestSourceInfo]:
        with session_scope() as session:
            return TestRepository(session).list_sources()

    # -- internals ----------------------------------------------------------

    def _persist(
        self,
        outcome: ParseOutcome,
        *,
        kind: str,
        location: str,
        label: str,
        started: float,
    ) -> IngestionResult:
        with session_scope() as session:
            repository = TestRepository(session)
            source = repository.upsert_source(kind=kind, location=location, label=label)
            stored, duplicates = repository.replace_tests(source, outcome.tests)

            source.detail = {
                "files_scanned": outcome.files_scanned,
                "files_skipped": outcome.files_skipped,
                "issues": len(outcome.issues),
                "duplicates_skipped": duplicates,
            }
            source_info = repository.to_source_info(source)

            duration_ms = int((time.perf_counter() - started) * 1000)
            RunRepository(session).record(
                kind=f"index.{kind.lower()}",
                status="COMPLETED",
                detail={
                    "location": location,
                    "discovered": len(outcome.tests),
                    "stored": stored,
                    "duplicates_skipped": duplicates,
                    "issues": len(outcome.issues),
                },
                duration_ms=duration_ms,
            )

        # The index is rebuilt from the database so it always reflects every
        # source, not just the one that was just added.
        self.state.rebuild_index()

        log.info(
            "indexing completed",
            extra={
                "event": "index.completed",
                "kind": kind,
                "location": location,
                "discovered": len(outcome.tests),
                "stored": stored,
                "duplicates": duplicates,
                "issues": len(outcome.issues),
                "duration_ms": duration_ms,
            },
        )

        return IngestionResult(
            source=source_info,
            tests_discovered=len(outcome.tests),
            tests_indexed=stored,
            duplicates_skipped=duplicates,
            files_scanned=outcome.files_scanned,
            files_skipped=outcome.files_skipped,
            issues=outcome.issues[:100],
            duration_ms=duration_ms,
        )


class UnsupportedFileError(ValueError):
    """The uploaded file type has no parser."""


__all__ = [
    "IngestionService",
    "UnsupportedFileError",
    "RepositoryPathError",
]

