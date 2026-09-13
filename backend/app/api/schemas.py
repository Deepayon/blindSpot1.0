"""API request and response schemas.

Kept separate from the domain models so the wire format can evolve without
dragging the analysis engine with it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..domain.models import (
    AnalysisResult,
    BlindSpotPattern,
    IngestionResult,
    TestSourceInfo,
)

# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------


class AnalyzeIncidentRequest(BaseModel):
    incident: str = Field(
        min_length=3,
        max_length=20_000,
        description="Free-form production incident text.",
    )
    title: str | None = None
    feature: str | None = None
    severity: str | None = None
    occurred_at: datetime | None = None
    conditions: dict[str, Any] | None = None
    persist: bool = True


class IndexRepositoryRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096, description="Local directory to index.")


class UpdateRecommendationRequest(BaseModel):
    status: str = Field(description="ACCEPTED | IGNORED | ALREADY_COVERED | PROPOSED")


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None


class TestListItem(BaseModel):
    id: str
    name: str
    feature: str
    scenario: str
    inputs: dict[str, Any]
    expected_behavior: str
    tags: list[str]
    source: str
    framework: str
    line_number: int | None = None


class TestListResponse(BaseModel):
    items: list[TestListItem]
    total: int
    limit: int
    offset: int


class TestStatsResponse(BaseModel):
    total: int
    sources: int
    by_feature: dict[str, int]
    by_framework: dict[str, int]
    last_indexed: str | None = None
    index: dict[str, Any]


class SourceListResponse(BaseModel):
    items: list[TestSourceInfo]


class IngestionResponse(IngestionResult):
    pass


class IncidentListItem(BaseModel):
    id: str
    title: str
    description: str
    feature: str
    severity: str
    conditions: dict[str, Any]
    signals: list[str]
    created_at: datetime
    coverage: str | None = None
    confidence: float | None = None
    confidence_level: str | None = None
    risk: str | None = None
    gap_type: str | None = None


class IncidentListResponse(BaseModel):
    items: list[IncidentListItem]
    total: int
    limit: int
    offset: int


class AnalysisResponse(AnalysisResult):
    pass


class BlindSpotListResponse(BaseModel):
    items: list[BlindSpotPattern]


class BlindSpotDetailResponse(BaseModel):
    pattern: BlindSpotPattern
    incidents: list[dict[str, Any]]


class DashboardResponse(BaseModel):
    metrics: dict[str, int]
    tests: dict[str, Any]
    top_blind_spots: list[dict[str, Any]]
    recent_incidents: list[dict[str, Any]]
    recent_runs: list[dict[str, Any]]
    index: dict[str, Any]
    ai: dict[str, Any]


class SettingsResponse(BaseModel):
    ai: dict[str, Any]
    index: dict[str, Any]
    retrieval: dict[str, Any]
    limits: dict[str, Any]
    security: dict[str, Any]
    database: str
    version: str
