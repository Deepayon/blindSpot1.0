"""Incident intake and analysis orchestration."""
from __future__ import annotations

import time
from typing import Any

from ..config.logging_conf import get_logger
from ..db.base import session_scope
from ..domain.enums import (
    ConfidenceLevel,
    Coverage,
    GapType,
    RecommendationStatus,
    Risk,
    TestEffectiveness,
)
from ..domain.models import (
    AnalysisResult,
    ComparisonDebug,
    Evidence,
    Gap,
    NormalizedIncident,
    NormalizedTest,
    Recommendation,
    RetrievalDebug,
    RetrievedTest,
)
from ..intelligence.incident_normalizer import IncidentNormalizer
from ..repositories.incident_repository import IncidentRepository
from ..repositories.run_repository import RunRepository
from .blind_spot_service import BlindSpotService
from .state import AppState

log = get_logger(__name__)


class IncidentService:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.normalizer = IncidentNormalizer()

    # -- analysis -----------------------------------------------------------

    def analyze(
        self,
        text: str,
        *,
        structured: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> AnalysisResult:
        """Normalise, analyse and (by default) persist a production incident."""
        started = time.perf_counter()

        with session_scope() as session:
            incident_id = str((structured or {}).get("id") or "") or IncidentRepository(
                session
            ).next_incident_id()

        incident = self.normalizer.normalize(text, incident_id=incident_id, structured=structured)

        # Recurrence feeds risk scoring, so it must be known before analysis.
        similar = self._similar_incident_count(incident)
        result = self.state.engine.analyze(incident, similar_incident_count=similar)

        if persist:
            self._persist(result, started)
            # New evidence can change which patterns qualify as recurring.
            BlindSpotService().recompute()

        return result

    def _similar_incident_count(self, incident: NormalizedIncident) -> int:
        """How many previous incidents already share this incident's likely family.

        Uses the signals BlindSpot has already extracted, so recurrence is
        counted from evidence rather than guessed.
        """
        from ..domain.enums import family_for_gap_type
        from ..intelligence.classifier import SIGNAL_GAP_TYPES

        families = {
            family_for_gap_type(gap_type)[0]
            for signal, gap_type in SIGNAL_GAP_TYPES
            if signal in incident.signals
        }
        if not families:
            return 0

        with session_scope() as session:
            repository = IncidentRepository(session)
            return max(
                (repository.count_similar_open_gaps(family) for family in families), default=0
            )

    def _persist(self, result: AnalysisResult, started: float) -> None:
        with session_scope() as session:
            repository = IncidentRepository(session)
            existing = repository.get_by_external_id(result.incident.id)
            row = existing or repository.create_incident(result.incident)
            repository.save_analysis(row, result)

            RunRepository(session).record(
                kind="analysis.incident",
                status="COMPLETED",
                detail={
                    "incident": result.incident.id,
                    "coverage": result.coverage.value,
                    "confidence": result.confidence,
                    "candidates": result.retrieval.candidate_count,
                },
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

    def reanalyze(self, external_id: str) -> AnalysisResult | None:
        """Re-run analysis for a stored incident against the current index."""
        with session_scope() as session:
            row = IncidentRepository(session).get_by_external_id(external_id)
            if row is None:
                return None
            structured = {
                "id": row.external_id,
                "title": row.title,
                "feature": row.feature,
                "severity": row.severity,
                "occurred_at": row.occurred_at,
            }
            description = row.description

        return self.analyze(description, structured=structured, persist=True)

    # -- reads --------------------------------------------------------------

    def list_incidents(
        self,
        *,
        coverage: str | None = None,
        feature: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        with session_scope() as session:
            pairs, total = IncidentRepository(session).list_incidents(
                coverage=coverage, feature=feature, query=query, limit=limit, offset=offset
            )
            # One query for every gap type on the page, rather than one per row.
            gap_types = self._gap_types_for(session, [a.id for _, a in pairs if a is not None])
            items = [
                {
                    "id": incident.external_id,
                    "title": incident.title,
                    "description": incident.description[:400],
                    "feature": incident.feature,
                    "severity": incident.severity,
                    "conditions": incident.conditions,
                    "signals": incident.signals,
                    "created_at": incident.created_at,
                    "coverage": analysis.coverage if analysis else None,
                    "confidence": analysis.confidence if analysis else None,
                    "confidence_level": analysis.confidence_level if analysis else None,
                    "risk": analysis.risk if analysis else None,
                    "gap_type": gap_types.get(analysis.id) if analysis else None,
                }
                for incident, analysis in pairs
            ]
        return items, total

    def _gap_types_for(self, session, analysis_ids: list[int]) -> dict[int, str]:
        """Map analysis id to its first gap type, in a single query."""
        if not analysis_ids:
            return {}
        from sqlalchemy import select

        from ..db.models import GapRecord

        rows = session.execute(
            select(GapRecord.analysis_id, GapRecord.gap_type)
            .where(GapRecord.analysis_id.in_(analysis_ids))
            .order_by(GapRecord.id)
        ).all()
        mapping: dict[int, str] = {}
        for analysis_id, gap_type in rows:
            mapping.setdefault(analysis_id, gap_type)
        return mapping

    def get_analysis(self, external_id: str) -> AnalysisResult | None:
        """Rebuild a stored analysis into the same shape a fresh one has."""
        with session_scope() as session:
            repository = IncidentRepository(session)
            row = repository.get_by_external_id(external_id)
            if row is None:
                return None
            analysis = repository.latest_analysis(row)
            if analysis is None:
                return None

            from sqlalchemy import select

            from ..db.models import GapRecord, RecommendationRecord

            gap_rows = session.scalars(
                select(GapRecord).where(GapRecord.analysis_id == analysis.id)
            ).all()
            recommendation_rows = (
                session.scalars(
                    select(RecommendationRecord).where(
                        RecommendationRecord.gap_id.in_([g.id for g in gap_rows])
                    )
                ).all()
                if gap_rows
                else []
            )

            incident = NormalizedIncident(
                id=row.external_id,
                title=row.title,
                description=row.description,
                feature=row.feature,
                scenario=row.scenario or "",
                conditions=row.conditions or {},
                failure=row.failure or "",
                root_cause=row.root_cause or "",
                signals=row.signals or [],
                severity=row.severity,
                occurred_at=row.occurred_at,
                extra=row.extra or {},
            )

            return AnalysisResult(
                incident=incident,
                coverage=_enum(Coverage, analysis.coverage, Coverage.NOT_COVERED),
                effectiveness=_enum(
                    TestEffectiveness, analysis.effectiveness, TestEffectiveness.NO_COVERAGE
                ),
                confidence=analysis.confidence,
                confidence_level=_enum(
                    ConfidenceLevel, analysis.confidence_level, ConfidenceLevel.LOW
                ),
                risk=_enum(Risk, analysis.risk, Risk.MEDIUM),
                explanation=analysis.explanation,
                gaps=[
                    Gap(
                        id=gap.id,
                        gap_type=_enum(GapType, gap.gap_type, GapType.MISSING_TEST),
                        summary=gap.summary,
                        detail=gap.detail,
                        missing_conditions=gap.missing_conditions or {},
                        family_key=gap.family_key,
                        family_label=gap.family_label,
                    )
                    for gap in gap_rows
                ],
                relevant_tests=[
                    _retrieved_from_json(item) for item in (analysis.relevant_tests or [])
                ],
                recommendations=[
                    Recommendation(
                        id=rec.id,
                        title=rec.title,
                        rationale=rec.rationale,
                        priority=_enum(Risk, rec.priority, Risk.MEDIUM),
                        status=_enum(
                            RecommendationStatus, rec.status, RecommendationStatus.PROPOSED
                        ),
                        suggested_inputs=rec.suggested_inputs or {},
                    )
                    for rec in recommendation_rows
                ],
                evidence=[Evidence.model_validate(e) for e in (analysis.evidence or [])],
                retrieval=RetrievalDebug.model_validate(analysis.retrieval_debug or {}),
                comparison=ComparisonDebug.model_validate(analysis.comparison_debug or {}),
                reasoning_source=analysis.reasoning_source,
                analyzed_at=analysis.created_at,
                analysis_id=analysis.id,
            )

    def update_recommendation(self, recommendation_id: int, status: str) -> bool:
        try:
            parsed = RecommendationStatus(status.upper())
        except ValueError:
            return False
        with session_scope() as session:
            return (
                IncidentRepository(session).update_recommendation_status(recommendation_id, parsed)
                is not None
            )

    def delete_incident(self, external_id: str) -> bool:
        with session_scope() as session:
            deleted = IncidentRepository(session).delete_incident(external_id)
        if deleted:
            BlindSpotService().recompute()
        return deleted


def _enum(enum_class, value, default):
    try:
        return enum_class(value)
    except (ValueError, TypeError):
        return default


def _retrieved_from_json(item: dict[str, Any]) -> RetrievedTest:
    return RetrievedTest(
        test=NormalizedTest(
            id=item.get("id", ""),
            name=item.get("name", ""),
            feature=item.get("feature", "Unknown"),
            scenario=item.get("scenario", ""),
            inputs=item.get("inputs") or {},
            expected_behavior=item.get("expected_behavior", ""),
            source=item.get("source", ""),
            framework=item.get("framework", "unknown"),
            line_number=item.get("line_number"),
        ),
        score=item.get("score", 0.0),
        vector_score=item.get("vector_score", 0.0),
        lexical_score=item.get("lexical_score", 0.0),
        feature_match=item.get("feature_match", False),
        matched_terms=item.get("matched_terms") or [],
    )
