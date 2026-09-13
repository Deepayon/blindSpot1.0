"""Incident intake, analysis and results."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status

from ...config.logging_conf import get_logger
from ...security import require_admin
from ...services.incident_service import IncidentService
from ..deps import incident_service
from ..schemas import (
    AnalysisResponse,
    AnalyzeIncidentRequest,
    IncidentListItem,
    IncidentListResponse,
    UpdateRecommendationRequest,
)

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["incidents"])

MAX_INCIDENT_UPLOAD_BYTES = 2 * 1024 * 1024


def _structured(payload: AnalyzeIncidentRequest) -> dict[str, object]:
    structured: dict[str, object] = {}
    if payload.title:
        structured["title"] = payload.title
    if payload.feature:
        structured["feature"] = payload.feature
    if payload.severity:
        structured["severity"] = payload.severity
    if payload.occurred_at:
        structured["occurred_at"] = payload.occurred_at
    if payload.conditions:
        structured["conditions"] = payload.conditions
    return structured


@router.post("/incidents/analyze", response_model=AnalysisResponse)
def analyze_incident(
    payload: AnalyzeIncidentRequest,
    service: IncidentService = Depends(incident_service),
) -> AnalysisResponse:
    """Analyse a production incident against the indexed tests."""
    try:
        result = service.analyze(
            payload.incident, structured=_structured(payload), persist=payload.persist
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AnalysisResponse.model_validate(result.model_dump())


@router.post("/incidents", response_model=AnalysisResponse, status_code=status.HTTP_201_CREATED)
def create_incident(
    payload: AnalyzeIncidentRequest,
    service: IncidentService = Depends(incident_service),
) -> AnalysisResponse:
    """Create and analyse an incident (always persisted)."""
    try:
        result = service.analyze(payload.incident, structured=_structured(payload), persist=True)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AnalysisResponse.model_validate(result.model_dump())


@router.post("/incidents/upload", response_model=AnalysisResponse)
async def upload_incident(
    file: UploadFile = File(...),
    service: IncidentService = Depends(incident_service),
) -> AnalysisResponse:
    """Analyse an incident supplied as a .txt, .md, .log or .json file."""
    raw = await file.read(MAX_INCIDENT_UPLOAD_BYTES + 1)
    await file.close()

    if len(raw) > MAX_INCIDENT_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Incident file is too large.",
        )
    if not raw.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Incident file is empty."
        )

    text = raw.decode("utf-8", errors="replace")
    structured: dict[str, object] = {}

    # A JSON incident may already carry structured fields; use them.
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                structured = {k: v for k, v in parsed.items() if v is not None}
                text = str(
                    structured.pop("description", None)
                    or structured.pop("text", None)
                    or json.dumps(parsed)
                )
        except json.JSONDecodeError:
            pass  # treat it as plain text

    try:
        result = service.analyze(text, structured=structured, persist=True)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AnalysisResponse.model_validate(result.model_dump())


@router.get("/incidents", response_model=IncidentListResponse)
def list_incidents(
    coverage: str | None = Query(default=None, max_length=32),
    feature: str | None = Query(default=None, max_length=128),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: IncidentService = Depends(incident_service),
) -> IncidentListResponse:
    items, total = service.list_incidents(
        coverage=coverage, feature=feature, query=q, limit=limit, offset=offset
    )
    return IncidentListResponse(
        items=[IncidentListItem.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/incidents/{incident_id}", response_model=AnalysisResponse)
def get_incident(
    incident_id: str, service: IncidentService = Depends(incident_service)
) -> AnalysisResponse:
    result = service.get_analysis(incident_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Incident '{incident_id}' not found."
        )
    return AnalysisResponse.model_validate(result.model_dump())


@router.post("/incidents/{incident_id}/reanalyze", response_model=AnalysisResponse)
def reanalyze_incident(
    incident_id: str, service: IncidentService = Depends(incident_service)
) -> AnalysisResponse:
    """Re-run analysis against the current index, useful after adding tests."""
    result = service.reanalyze(incident_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Incident '{incident_id}' not found."
        )
    return AnalysisResponse.model_validate(result.model_dump())


@router.delete("/incidents/{incident_id}")
def delete_incident(
    incident_id: str,
    request: Request,
    service: IncidentService = Depends(incident_service),
) -> dict[str, bool]:
    require_admin(request)
    if not service.delete_incident(incident_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Incident '{incident_id}' not found."
        )
    return {"deleted": True}


@router.patch("/recommendations/{recommendation_id}")
def update_recommendation(
    recommendation_id: int,
    payload: UpdateRecommendationRequest,
    service: IncidentService = Depends(incident_service),
) -> dict[str, str]:
    """Mark a recommendation Accepted / Ignored / Already Covered."""
    if not service.update_recommendation(recommendation_id, payload.status):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unknown recommendation or invalid status.",
        )
    return {"status": payload.status.upper()}
