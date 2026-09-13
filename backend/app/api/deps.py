"""Shared API dependencies."""
from __future__ import annotations

from ..services.blind_spot_service import BlindSpotService
from ..services.dashboard_service import DashboardService
from ..services.incident_service import IncidentService
from ..services.ingestion_service import IngestionService
from ..services.state import AppState, get_state


def app_state() -> AppState:
    return get_state()


def ingestion_service() -> IngestionService:
    return IngestionService(get_state())


def incident_service() -> IncidentService:
    return IncidentService(get_state())


def blind_spot_service() -> BlindSpotService:
    return BlindSpotService()


def dashboard_service() -> DashboardService:
    return DashboardService(get_state())
