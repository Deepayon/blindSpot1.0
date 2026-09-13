"""Test ingestion and catalogue endpoints."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from ...config.logging_conf import get_logger
from ...db.base import session_scope
from ...repositories.test_repository import TestRepository
from ...services.ingestion_service import (
    IngestionService,
    RepositoryPathError,
    UnsupportedFileError,
)
from ...services.state import AppState
from ..deps import app_state, ingestion_service
from ..schemas import (
    IndexRepositoryRequest,
    IngestionResponse,
    SourceListResponse,
    TestListItem,
    TestListResponse,
    TestStatsResponse,
)

log = get_logger(__name__)
router = APIRouter(prefix="/api/tests", tags=["tests"])

#: Upload guard. Larger suites belong in a repository or a split export.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
ALLOWED_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm"}


@router.post("/index/file", response_model=IngestionResponse)
async def index_file(
    file: UploadFile = File(...),
    service: IngestionService = Depends(ingestion_service),
) -> IngestionResponse:
    """Index a CSV or Excel test export."""
    original_name = Path(file.filename or "upload").name
    suffix = Path(original_name).suffix.lower()

    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported file type '{suffix or original_name}'. "
                f"Supported: {', '.join(sorted(ALLOWED_SUFFIXES))}."
            ),
        )

    # Stream to a temporary file so a large upload never sits fully in memory.
    with tempfile.TemporaryDirectory(prefix="blindspot-upload-") as directory:
        target = Path(directory) / original_name
        written = 0
        try:
            with target.open("wb") as handle:
                while chunk := await file.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
                        )
                    handle.write(chunk)
        finally:
            await file.close()

        if written == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="The uploaded file is empty."
            )

        try:
            result = service.index_file(target, original_name=original_name)
        except UnsupportedFileError as exc:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
            ) from exc

    return IngestionResponse.model_validate(result.model_dump())


@router.post("/index/repository", response_model=IngestionResponse)
def index_repository(
    payload: IndexRepositoryRequest,
    service: IngestionService = Depends(ingestion_service),
) -> IngestionResponse:
    """Index a local project directory. The repository is never uploaded or executed."""
    try:
        result = service.index_repository(payload.path)
    except RepositoryPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return IngestionResponse.model_validate(result.model_dump())


@router.post("/reindex")
def reindex(service: IngestionService = Depends(ingestion_service)) -> dict[str, int]:
    """Rebuild the search index from stored tests without re-parsing sources."""
    return {"tests_indexed": service.reindex()}


@router.get("", response_model=TestListResponse)
def list_tests(
    q: str | None = Query(default=None, max_length=200),
    feature: str | None = Query(default=None, max_length=128),
    framework: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> TestListResponse:
    with session_scope() as session:
        items, total = TestRepository(session).search(
            query=q, feature=feature, framework=framework, limit=limit, offset=offset
        )
    return TestListResponse(
        items=[
            TestListItem(
                id=test.id,
                name=test.name,
                feature=test.feature,
                scenario=test.scenario,
                inputs=test.inputs,
                expected_behavior=test.expected_behavior,
                tags=test.tags,
                source=test.source,
                framework=test.framework,
                line_number=test.line_number,
            )
            for test in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/stats", response_model=TestStatsResponse)
def test_stats(state: AppState = Depends(app_state)) -> TestStatsResponse:
    with session_scope() as session:
        stats = TestRepository(session).stats()
    return TestStatsResponse(**stats, index=state.index.describe())


@router.get("/sources", response_model=SourceListResponse)
def list_sources(service: IngestionService = Depends(ingestion_service)) -> SourceListResponse:
    return SourceListResponse(items=service.list_sources())


@router.delete("/sources/{source_id}")
def delete_source(
    source_id: int, service: IngestionService = Depends(ingestion_service)
) -> dict[str, bool]:
    if not service.delete_source(source_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found.")
    return {"deleted": True}
