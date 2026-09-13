"""Persistence for incidents, analyses, gaps and recommendations."""
from __future__ import annotations

from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..db.models import GapRecord, Incident, IncidentAnalysis, RecommendationRecord
from ..domain.enums import Coverage, GapType, RecommendationStatus
from ..domain.models import AnalysisResult, NormalizedIncident
from ..intelligence.pattern_detector import GapObservation


class IncidentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- identifiers --------------------------------------------------------

    def next_incident_id(self) -> str:
        """Sequential, human-readable ids: INC-1001, INC-1002, ..."""
        highest = int(self.session.scalar(select(func.count(Incident.id))) or 0)
        return f"INC-{1001 + highest}"

    def get_by_external_id(self, external_id: str) -> Incident | None:
        return self.session.scalar(select(Incident).where(Incident.external_id == external_id))

    # -- writes -------------------------------------------------------------

    def create_incident(self, incident: NormalizedIncident) -> Incident:
        row = Incident(
            external_id=incident.id,
            title=incident.title,
            description=incident.description,
            feature=incident.feature,
            scenario=incident.scenario,
            conditions=incident.conditions,
            failure=incident.failure,
            root_cause=incident.root_cause,
            signals=incident.signals,
            severity=incident.severity,
            occurred_at=incident.occurred_at.replace(tzinfo=None)
            if incident.occurred_at
            else None,
            extra=incident.extra,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def save_analysis(self, incident_row: Incident, result: AnalysisResult) -> IncidentAnalysis:
        analysis = IncidentAnalysis(
            incident_id=incident_row.id,
            coverage=result.coverage.value,
            effectiveness=result.effectiveness.value,
            confidence=result.confidence,
            confidence_level=result.confidence_level.value,
            risk=result.risk.value,
            explanation=result.explanation,
            reasoning_source=result.reasoning_source,
            relevant_tests=[
                {
                    "id": item.test.id,
                    "name": item.test.name,
                    "feature": item.test.feature,
                    "scenario": item.test.scenario,
                    "inputs": item.test.inputs,
                    "expected_behavior": item.test.expected_behavior,
                    "source": item.test.source,
                    "framework": item.test.framework,
                    "line_number": item.test.line_number,
                    "score": item.score,
                    "vector_score": item.vector_score,
                    "lexical_score": item.lexical_score,
                    "feature_match": item.feature_match,
                    "matched_terms": item.matched_terms,
                }
                for item in result.relevant_tests
            ],
            evidence=[e.model_dump(mode="json") for e in result.evidence],
            retrieval_debug=result.retrieval.model_dump(mode="json"),
            comparison_debug=result.comparison.model_dump(mode="json"),
        )
        self.session.add(analysis)
        self.session.flush()

        for gap in result.gaps:
            gap_row = GapRecord(
                analysis_id=analysis.id,
                gap_type=gap.gap_type.value,
                family_key=gap.family_key,
                family_label=gap.family_label,
                summary=gap.summary,
                detail=gap.detail,
                missing_conditions=gap.missing_conditions,
            )
            self.session.add(gap_row)
            self.session.flush()
            gap.id = gap_row.id

            for recommendation in result.recommendations:
                row = RecommendationRecord(
                    gap_id=gap_row.id,
                    title=recommendation.title,
                    rationale=recommendation.rationale,
                    priority=recommendation.priority.value,
                    status=recommendation.status.value,
                    suggested_inputs=recommendation.suggested_inputs,
                )
                self.session.add(row)
                self.session.flush()
                recommendation.id = row.id

        result.analysis_id = analysis.id
        return analysis

    def update_recommendation_status(
        self, recommendation_id: int, status: RecommendationStatus
    ) -> RecommendationRecord | None:
        row = self.session.get(RecommendationRecord, recommendation_id)
        if row is None:
            return None
        row.status = status.value
        return row

    def delete_incident(self, external_id: str) -> bool:
        row = self.get_by_external_id(external_id)
        if row is None:
            return False
        self.session.delete(row)
        return True

    # -- reads --------------------------------------------------------------

    def latest_analysis(self, incident_row: Incident) -> IncidentAnalysis | None:
        return self.session.scalar(
            select(IncidentAnalysis)
            .where(IncidentAnalysis.incident_id == incident_row.id)
            .order_by(desc(IncidentAnalysis.created_at), desc(IncidentAnalysis.id))
            .limit(1)
        )

    def list_incidents(
        self,
        *,
        coverage: str | None = None,
        feature: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[tuple[Incident, IncidentAnalysis | None]], int]:
        statement = select(Incident)
        if feature:
            statement = statement.where(Incident.feature == feature)
        if query:
            like = f"%{query.lower()}%"
            statement = statement.where(
                func.lower(Incident.title).like(like)
                | func.lower(Incident.description).like(like)
                | func.lower(Incident.external_id).like(like)
            )

        rows = self.session.scalars(
            statement.order_by(desc(Incident.created_at), desc(Incident.id))
        ).all()

        paired: list[tuple[Incident, IncidentAnalysis | None]] = []
        for row in rows:
            analysis = self.latest_analysis(row)
            if coverage and (analysis is None or analysis.coverage != coverage):
                continue
            paired.append((row, analysis))

        total = len(paired)
        return paired[offset : offset + limit], total

    def coverage_counts(self) -> dict[str, int]:
        """Counts by coverage using each incident's most recent analysis."""
        counts = {coverage.value: 0 for coverage in Coverage}
        for incident in self.session.scalars(select(Incident)).all():
            analysis = self.latest_analysis(incident)
            if analysis is not None:
                counts[analysis.coverage] = counts.get(analysis.coverage, 0) + 1
        return counts

    def count_incidents(self) -> int:
        return int(self.session.scalar(select(func.count(Incident.id))) or 0)

    def count_gaps(self) -> int:
        return int(self.session.scalar(select(func.count(GapRecord.id))) or 0)

    def gap_observations(self) -> list[GapObservation]:
        """Flatten every gap on every latest analysis, for pattern detection."""
        observations: list[GapObservation] = []
        incidents = self.session.scalars(select(Incident)).all()

        for incident in incidents:
            analysis = self.latest_analysis(incident)
            if analysis is None:
                continue
            gaps = self.session.scalars(
                select(GapRecord).where(GapRecord.analysis_id == analysis.id)
            ).all()
            for gap in gaps:
                try:
                    gap_type = GapType(gap.gap_type)
                except ValueError:
                    gap_type = GapType.MISSING_TEST
                observations.append(
                    GapObservation(
                        incident_id=incident.external_id,
                        gap_type=gap_type,
                        family_key=gap.family_key or "UNCATEGORISED",
                        family_label=gap.family_label or "Uncategorised Missing Coverage",
                        feature=incident.feature,
                        severity=incident.severity,
                        coverage=analysis.coverage,
                    )
                )
        return observations

    def count_similar_open_gaps(self, family_key: str) -> int:
        """How many distinct incidents already share a blind-spot family.

        Feeds the recurrence factor of risk scoring, so a repeat offender scores
        higher than a first occurrence.
        """
        rows = self.session.execute(
            select(Incident.external_id)
            .join(IncidentAnalysis, IncidentAnalysis.incident_id == Incident.id)
            .join(GapRecord, GapRecord.analysis_id == IncidentAnalysis.id)
            .where(GapRecord.family_key == family_key)
            .distinct()
        ).all()
        return len(rows)

    def incidents_for_family(self, family_key: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(Incident, IncidentAnalysis, GapRecord)
            .join(IncidentAnalysis, IncidentAnalysis.incident_id == Incident.id)
            .join(GapRecord, GapRecord.analysis_id == IncidentAnalysis.id)
            .where(GapRecord.family_key == family_key)
            .order_by(desc(Incident.created_at))
            .limit(limit)
        ).all()

        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        for incident, analysis, gap in rows:
            if incident.external_id in seen:
                continue
            seen.add(incident.external_id)
            results.append(
                {
                    "id": incident.external_id,
                    "title": incident.title,
                    "feature": incident.feature,
                    "severity": incident.severity,
                    "coverage": analysis.coverage,
                    "risk": analysis.risk,
                    "gap_type": gap.gap_type,
                    "gap_summary": gap.summary,
                    "created_at": incident.created_at,
                }
            )
        return results

    def recent_incidents(self, limit: int = 8) -> list[dict[str, Any]]:
        rows = self.session.scalars(
            select(Incident).order_by(desc(Incident.created_at), desc(Incident.id)).limit(limit)
        ).all()
        results: list[dict[str, Any]] = []
        for incident in rows:
            analysis = self.latest_analysis(incident)
            results.append(
                {
                    "id": incident.external_id,
                    "title": incident.title,
                    "feature": incident.feature,
                    "severity": incident.severity,
                    "coverage": analysis.coverage if analysis else None,
                    "risk": analysis.risk if analysis else None,
                    "confidence": analysis.confidence if analysis else None,
                    "created_at": incident.created_at,
                }
            )
        return results
