"""The normalised internal representation.

Every ingestion path (repository, CSV, Excel) produces `NormalizedTest`, and
every incident path produces `NormalizedIncident`. The analysis engine knows
about nothing else, which is what keeps future integrations (Jira, TestRail,
Sentry, ...) out of the core.

Both models are deliberately extensible: unknown structured fields survive in
`extra`, so a richer source never loses information just because BlindSpot does
not understand it yet.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    ConfidenceLevel,
    Coverage,
    GapType,
    RecommendationStatus,
    Risk,
    TestEffectiveness,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class DomainModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, use_enum_values=False)


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


class NormalizedTest(DomainModel):
    """A single test case, independent of where it came from."""

    id: str = Field(description="Stable identifier, unique within a test source.")
    name: str
    feature: str = "Unknown"
    scenario: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected_behavior: str = ""
    tags: list[str] = Field(default_factory=list)

    # Provenance
    source: str = Field(default="", description="File path or sheet the test came from.")
    framework: str = Field(default="unknown", description="pytest, jest, csv, excel, ...")
    line_number: int | None = None
    code: str | None = Field(default=None, description="Raw test body, repository sources only.")

    # Signals extracted deterministically from the test body / row.
    assertions: list[str] = Field(default_factory=list)
    literals: list[str] = Field(default_factory=list)

    extra: dict[str, Any] = Field(default_factory=dict)

    def searchable_text(self) -> str:
        """The text used for embedding and lexical retrieval.

        Only normalised metadata is included — never the raw source body — so
        that enabling an external provider cannot leak private code.
        """
        parts = [
            self.name.replace("_", " "),
            self.feature,
            self.scenario,
            self.expected_behavior,
            " ".join(self.tags),
            " ".join(f"{k} {v}" for k, v in self.inputs.items()),
            " ".join(self.assertions),
        ]
        return "  ".join(p for p in parts if p).strip()

    def describe(self) -> str:
        """Compact one-line description used in explanations and LLM prompts."""
        detail = self.scenario or self.expected_behavior or self.name
        if self.inputs:
            rendered = ", ".join(f"{k}={v}" for k, v in sorted(self.inputs.items()))
            detail = f"{detail} ({rendered})" if detail else rendered
        return f"{self.id} — {detail}".strip()


class TestSourceInfo(DomainModel):
    id: int
    kind: str
    location: str
    label: str
    test_count: int
    indexed_at: datetime | None = None
    status: str = "READY"
    detail: dict[str, Any] = Field(default_factory=dict)


class IngestionIssue(DomainModel):
    """A file or row that could not be ingested. Never fatal."""

    location: str
    reason: str
    severity: str = "WARNING"


class IngestionResult(DomainModel):
    source: TestSourceInfo
    tests_discovered: int = 0
    tests_indexed: int = 0
    duplicates_skipped: int = 0
    files_scanned: int = 0
    files_skipped: int = 0
    issues: list[IngestionIssue] = Field(default_factory=list)
    duration_ms: int = 0


# --------------------------------------------------------------------------
# Incidents
# --------------------------------------------------------------------------


class NormalizedIncident(DomainModel):
    """A production incident reduced to the scenario it exercised."""

    id: str
    title: str = ""
    description: str
    feature: str = "Unknown"
    scenario: str = ""
    conditions: dict[str, Any] = Field(default_factory=dict)
    failure: str = ""
    root_cause: str = ""
    signals: list[str] = Field(
        default_factory=list,
        description="Behavioural markers detected in the text (retry, timeout, unicode, ...).",
    )
    severity: str = "MEDIUM"
    occurred_at: datetime | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    def searchable_text(self) -> str:
        parts = [
            self.title,
            self.feature,
            self.scenario,
            self.description,
            self.failure,
            self.root_cause,
            " ".join(f"{k} {v}" for k, v in self.conditions.items()),
            " ".join(self.signals),
        ]
        return "  ".join(p for p in parts if p).strip()


# --------------------------------------------------------------------------
# Retrieval
# --------------------------------------------------------------------------


class RetrievedTest(DomainModel):
    test: NormalizedTest
    score: float
    vector_score: float = 0.0
    lexical_score: float = 0.0
    feature_match: bool = False
    matched_terms: list[str] = Field(default_factory=list)


class RetrievalDebug(DomainModel):
    """Observability payload surfaced under the UI's "Analysis Details"."""

    candidate_count: int = 0
    considered_count: int = 0
    top_score: float = 0.0
    strategy: str = "hybrid"
    vector_store: str = ""
    embedding_model: str = ""


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------


class Evidence(DomainModel):
    """One traceable fact supporting a conclusion. Never generated by an LLM."""

    kind: str
    statement: str
    production_value: str | None = None
    test_value: str | None = None
    test_id: str | None = None


class ComparisonDebug(DomainModel):
    feature_match: bool = False
    scenario_match: bool = False
    condition_match: bool = False
    matched_conditions: list[str] = Field(default_factory=list)
    unmatched_conditions: list[str] = Field(default_factory=list)
    signal_match: bool = False
    unmatched_signals: list[str] = Field(default_factory=list)
    best_test_id: str | None = None


class Recommendation(DomainModel):
    id: int | None = None
    title: str
    rationale: str = ""
    priority: Risk = Risk.MEDIUM
    status: RecommendationStatus = RecommendationStatus.PROPOSED
    suggested_inputs: dict[str, Any] = Field(default_factory=dict)


class Gap(DomainModel):
    id: int | None = None
    gap_type: GapType
    summary: str
    detail: str = ""
    missing_conditions: dict[str, Any] = Field(default_factory=dict)
    family_key: str = ""
    family_label: str = ""


class AnalysisResult(DomainModel):
    """The complete, explainable answer to "was production protected?"."""

    incident: NormalizedIncident
    coverage: Coverage
    effectiveness: TestEffectiveness
    confidence: float
    confidence_level: ConfidenceLevel
    risk: Risk
    explanation: str
    gaps: list[Gap] = Field(default_factory=list)
    relevant_tests: list[RetrievedTest] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    retrieval: RetrievalDebug = Field(default_factory=RetrievalDebug)
    comparison: ComparisonDebug = Field(default_factory=ComparisonDebug)
    reasoning_source: str = Field(
        default="deterministic",
        description="'deterministic' or 'deterministic+llm' — makes external AI use visible.",
    )
    analyzed_at: datetime = Field(default_factory=utcnow)
    analysis_id: int | None = None


class BlindSpotPattern(DomainModel):
    id: int | None = None
    key: str
    label: str
    incident_count: int
    gap_count: int
    risk: Risk
    gap_types: list[GapType] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    example_incident_ids: list[str] = Field(default_factory=list)
    summary: str = ""
