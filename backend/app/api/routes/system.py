"""Dashboard, settings and health endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...services.dashboard_service import DashboardService
from ...services.state import AppState
from ..deps import app_state, dashboard_service
from ..schemas import DashboardResponse, SettingsResponse

router = APIRouter(prefix="/api", tags=["system"])

VERSION = "0.1.0"


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(service: DashboardService = Depends(dashboard_service)) -> DashboardResponse:
    return DashboardResponse(**service.summary())


@router.get("/settings", response_model=SettingsResponse)
def settings(state: AppState = Depends(app_state)) -> SettingsResponse:
    config = state.settings
    return SettingsResponse(
        ai=state.describe_ai(),
        index=state.index.describe(),
        retrieval={
            "top_k": config.retrieval_top_k,
            "min_score": config.retrieval_min_score,
            "vector_weight": config.retrieval_vector_weight,
            "lexical_weight": config.retrieval_lexical_weight,
            "vector_store": config.vector_store,
        },
        limits={
            "max_file_size_bytes": config.max_file_size_bytes,
            "max_scanned_files": config.max_scanned_files,
            "blind_spot_min_incidents": config.blind_spot_min_incidents,
            "allowed_repository_roots": [str(p) for p in config.allowed_repository_roots],
        },
        # The URL may contain a path but never credentials for the SQLite default.
        database=config.database_url.split("://")[0],
        version=VERSION,
    )


@router.get("/health")
def health(state: AppState = Depends(app_state)) -> dict[str, object]:
    return {
        "status": "ok",
        "version": VERSION,
        "tests_indexed": len(state.index),
        "external_ai": state.settings.external_ai_enabled,
    }
