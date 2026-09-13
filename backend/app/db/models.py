"""Relational schema.

    test_source 1---* test
    incident    1---* incident_analysis 1---* gap 1---* recommendation
    gap         *---1 blind_spot
    analysis_run  — one row per indexing/analysis batch, for observability

JSON-typed columns hold the extensible parts of the domain model so that adding
a field to `NormalizedTest` does not require a migration during the POC.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..domain.models import utcnow
from .base import Base


class TestSource(Base):
    __tablename__ = "test_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    location: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="READY")
    test_count: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    tests: Mapped[list[TestCase]] = relationship(
        back_populates="source", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (UniqueConstraint("kind", "location", name="uq_source_kind_location"),)


class TestCase(Base):
    __tablename__ = "tests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("test_sources.id", ondelete="CASCADE"), index=True
    )
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(512))
    feature: Mapped[str] = mapped_column(String(128), default="Unknown", index=True)
    scenario: Mapped[str] = mapped_column(Text, default="")
    expected_behavior: Mapped[str] = mapped_column(Text, default="")
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    assertions: Mapped[list[str]] = mapped_column(JSON, default=list)
    literals: Mapped[list[str]] = mapped_column(JSON, default=list)
    file_path: Mapped[str] = mapped_column(Text, default="")
    framework: Mapped[str] = mapped_column(String(64), default="unknown")
    line_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    code: Mapped[str | None] = mapped_column(Text, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    source: Mapped[TestSource] = relationship(back_populates="tests")

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_test_source_external"),
        Index("ix_tests_feature_framework", "feature", "framework"),
    )


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text)
    feature: Mapped[str] = mapped_column(String(128), default="Unknown", index=True)
    scenario: Mapped[str] = mapped_column(Text, default="")
    conditions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    failure: Mapped[str] = mapped_column(Text, default="")
    root_cause: Mapped[str] = mapped_column(Text, default="")
    signals: Mapped[list[str]] = mapped_column(JSON, default=list)
    severity: Mapped[str] = mapped_column(String(32), default="MEDIUM")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    analyses: Mapped[list[IncidentAnalysis]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", passive_deletes=True
    )


class IncidentAnalysis(Base):
    __tablename__ = "incident_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    coverage: Mapped[str] = mapped_column(String(32), index=True)
    effectiveness: Mapped[str] = mapped_column(String(32), default="NO_COVERAGE")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_level: Mapped[str] = mapped_column(String(16), default="LOW")
    risk: Mapped[str] = mapped_column(String(16), default="MEDIUM", index=True)
    explanation: Mapped[str] = mapped_column(Text, default="")
    reasoning_source: Mapped[str] = mapped_column(String(64), default="deterministic")
    relevant_tests: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    retrieval_debug: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    comparison_debug: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    incident: Mapped[Incident] = relationship(back_populates="analyses")
    gaps: Mapped[list[GapRecord]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan", passive_deletes=True
    )


class GapRecord(Base):
    __tablename__ = "gaps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("incident_analyses.id", ondelete="CASCADE"), index=True
    )
    blind_spot_id: Mapped[int | None] = mapped_column(
        ForeignKey("blind_spots.id", ondelete="SET NULL"), nullable=True, index=True
    )
    gap_type: Mapped[str] = mapped_column(String(64), index=True)
    family_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    family_label: Mapped[str] = mapped_column(String(128), default="")
    summary: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text, default="")
    missing_conditions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    analysis: Mapped[IncidentAnalysis] = relationship(back_populates="gaps")
    blind_spot: Mapped[BlindSpot | None] = relationship(back_populates="gaps")
    recommendations: Mapped[list[RecommendationRecord]] = relationship(
        back_populates="gap", cascade="all, delete-orphan", passive_deletes=True
    )


class RecommendationRecord(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gap_id: Mapped[int] = mapped_column(ForeignKey("gaps.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    status: Mapped[str] = mapped_column(String(32), default="PROPOSED", index=True)
    suggested_inputs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    gap: Mapped[GapRecord] = relationship(back_populates="recommendations")


class BlindSpot(Base):
    __tablename__ = "blind_spots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(128))
    incident_count: Mapped[int] = mapped_column(Integer, default=0)
    gap_count: Mapped[int] = mapped_column(Integer, default=0)
    risk: Mapped[str] = mapped_column(String(16), default="LOW")
    summary: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    gaps: Mapped[list[GapRecord]] = relationship(back_populates="blind_spot")


class AnalysisRun(Base):
    """Audit trail for indexing and analysis batches."""

    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
