"""The gap analysis engine.

Wires retrieval → comparison → classification → explanation → recommendation
into one call. Deterministic throughout; the LLM, when configured, only enriches
the incident and polishes the wording.
"""
from __future__ import annotations

import time

from ..config.logging_conf import get_logger
from ..config.settings import Settings, get_settings
from ..domain.enums import ConfidenceLevel, Coverage, Risk, TestEffectiveness
from ..domain.models import AnalysisResult, ComparisonDebug, NormalizedIncident
from ..providers.llm import LLMProvider, NullLLMProvider
from ..retrieval.index import TestIndex
from ..retrieval.retriever import TestRetriever
from .classifier import CoverageClassifier, GapClassifier
from .comparator import ScenarioComparator, ScenarioComparison
from .confidence import ConfidenceScorer
from .explainer import Explainer
from .llm_enrichment import LLMEnricher
from .recommender import Recommender
from .risk import RiskScorer

log = get_logger(__name__)


class GapAnalysisEngine:
    def __init__(
        self,
        index: TestIndex,
        llm: LLMProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.index = index
        self.retriever = TestRetriever(index, self.settings)
        self.comparator = ScenarioComparator()
        self.coverage_classifier = CoverageClassifier()
        self.gap_classifier = GapClassifier()
        self.explainer = Explainer()
        self.recommender = Recommender()
        self.confidence_scorer = ConfidenceScorer()
        self.risk_scorer = RiskScorer()
        self.enricher = LLMEnricher(llm or NullLLMProvider())

    def analyze(
        self, incident: NormalizedIncident, *, similar_incident_count: int = 0
    ) -> AnalysisResult:
        started = time.perf_counter()
        log.info(
            "incident analysis started",
            extra={"event": "analysis.started", "incident": incident.id},
        )

        reasoning_source = "deterministic"
        if self.enricher.active and self.enricher.enrich_incident(incident):
            reasoning_source = "deterministic+llm"

        # An empty index cannot support any verdict — saying "not covered" would
        # be a fabrication, so the honest answer is that evidence is missing.
        if self.index.is_empty:
            return self._insufficient_evidence(incident, reasoning_source, started)

        candidates, retrieval_debug = self.retriever.retrieve(incident)
        comparison = self.comparator.compare(incident, candidates)

        coverage, effectiveness = self.coverage_classifier.classify(comparison)
        gap_type = self.gap_classifier.classify(comparison, coverage)

        evidence = self.explainer.build_evidence(comparison, coverage)
        gap = self.explainer.build_gap(comparison, coverage, gap_type)
        explanation = self.explainer.build_explanation(comparison, coverage, gap_type)

        confidence = self.confidence_scorer.score(
            comparison,
            coverage,
            corpus_size=len(self.index),
            top_score=retrieval_debug.top_score,
        )
        risk, risk_breakdown = self.risk_scorer.score(
            severity=incident.severity,
            coverage=coverage,
            feature=incident.feature,
            similar_incident_count=similar_incident_count,
        )
        recommendations = self.recommender.recommend(comparison, gap, coverage, risk)

        if self.enricher.active:
            refined = self.enricher.refine_explanation(
                comparison,
                coverage.value,
                gap_type.value,
                explanation,
                [e.statement for e in evidence],
            )
            if refined:
                explanation = refined
                reasoning_source = "deterministic+llm"

        result = AnalysisResult(
            incident=incident,
            coverage=coverage,
            effectiveness=effectiveness,
            confidence=confidence.score,
            confidence_level=confidence.level,
            risk=risk,
            explanation=explanation,
            gaps=[gap] if gap else [],
            relevant_tests=candidates,
            recommendations=recommendations,
            evidence=evidence,
            retrieval=retrieval_debug,
            comparison=self._comparison_debug(comparison, risk_breakdown, confidence.components),
            reasoning_source=reasoning_source,
        )

        log.info(
            "analysis completed",
            extra={
                "event": "analysis.completed",
                "incident": incident.id,
                "coverage": coverage.value,
                "gap_type": gap_type.value,
                "confidence": confidence.score,
                "risk": risk.value,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return result

    # -- helpers ------------------------------------------------------------

    def _comparison_debug(
        self,
        comparison: ScenarioComparison,
        risk_breakdown: dict[str, int],
        confidence_components: dict[str, float],
    ) -> ComparisonDebug:
        best = comparison.best
        return ComparisonDebug(
            feature_match=bool(best and best.feature_match),
            scenario_match=bool(best and best.scenario_overlap >= 0.45),
            condition_match=bool(best and best.covers_all_conditions),
            matched_conditions=[c.key for c in best.matched_conditions] if best else [],
            unmatched_conditions=(
                [c.key for c in best.differing_conditions + best.absent_conditions] if best else []
            ),
            signal_match=bool(best and not best.missing_signals),
            unmatched_signals=comparison.unmatched_signals(),
            best_test_id=best.test.id if best else None,
        )

    def _insufficient_evidence(
        self, incident: NormalizedIncident, reasoning_source: str, started: float
    ) -> AnalysisResult:
        log.info(
            "analysis completed",
            extra={
                "event": "analysis.completed",
                "incident": incident.id,
                "coverage": Coverage.INSUFFICIENT_EVIDENCE.value,
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return AnalysisResult(
            incident=incident,
            coverage=Coverage.INSUFFICIENT_EVIDENCE,
            effectiveness=TestEffectiveness.NO_COVERAGE,
            confidence=0.0,
            confidence_level=ConfidenceLevel.LOW,
            risk=Risk.MEDIUM,
            explanation=(
                "No tests are indexed yet, so BlindSpot cannot say whether this scenario "
                "was covered. Add a test source first — reporting 'not covered' against an "
                "empty index would not be evidence."
            ),
            gaps=[],
            relevant_tests=[],
            recommendations=[],
            evidence=[],
            reasoning_source=reasoning_source,
        )
