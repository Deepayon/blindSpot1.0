"""Confidence scoring.

Confidence answers "how much should you trust this classification?", not "how
bad is this gap?". It is computed from observable properties of *this* analysis,
never invented:

    component            weight  meaning
    -------------------  ------  --------------------------------------------
    retrieval_strength     0.30  how strongly the best candidate matched
    evidence_richness      0.30  how many structured facts were comparable
    decisiveness           0.25  how clear-cut the verdict was
    corpus_support         0.15  whether the index was large enough to trust

When there is too little to go on the score stays low and the API exposes the
qualitative level (High/Medium/Low) instead, per spec §23.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import ConfidenceLevel, Coverage
from .comparator import ScenarioComparison

WEIGHTS = {
    "retrieval_strength": 0.30,
    "evidence_richness": 0.30,
    "decisiveness": 0.25,
    "corpus_support": 0.15,
}

#: Below this many indexed tests, absence of a matching test is weak evidence.
CORPUS_CONFIDENT_SIZE = 200

HIGH_THRESHOLD = 0.75
MEDIUM_THRESHOLD = 0.50


@dataclass
class ConfidenceBreakdown:
    score: float
    level: ConfidenceLevel
    components: dict[str, float]

    def as_dict(self) -> dict[str, float | str]:
        return {"score": self.score, "level": self.level.value, **self.components}


class ConfidenceScorer:
    def score(
        self,
        comparison: ScenarioComparison,
        coverage: Coverage,
        *,
        corpus_size: int,
        top_score: float,
    ) -> ConfidenceBreakdown:
        components = {
            "retrieval_strength": self._retrieval_strength(top_score),
            "evidence_richness": self._evidence_richness(comparison),
            "decisiveness": self._decisiveness(comparison, coverage),
            "corpus_support": self._corpus_support(corpus_size),
        }
        score = sum(WEIGHTS[name] * value for name, value in components.items())
        score = round(max(0.0, min(1.0, score)), 3)

        if coverage is Coverage.INSUFFICIENT_EVIDENCE:
            level = ConfidenceLevel.LOW
        elif score >= HIGH_THRESHOLD:
            level = ConfidenceLevel.HIGH
        elif score >= MEDIUM_THRESHOLD:
            level = ConfidenceLevel.MEDIUM
        else:
            level = ConfidenceLevel.LOW

        return ConfidenceBreakdown(
            score=score,
            level=level,
            components={k: round(v, 3) for k, v in components.items()},
        )

    def _retrieval_strength(self, top_score: float) -> float:
        """A strong top candidate means we are comparing against the right tests."""
        return max(0.0, min(1.0, top_score / 0.8))

    def _evidence_richness(self, comparison: ScenarioComparison) -> float:
        """Structured facts beat prose. Conditions count double signals."""
        conditions = len(comparison.incident.conditions)
        signals = len(comparison.decisive_signals)
        if not comparison.comparisons:
            return 0.0
        facts = min(3.0, conditions * 1.0) / 3.0 * 0.6 + min(3.0, signals * 1.0) / 3.0 * 0.4
        return min(1.0, facts)

    def _decisiveness(self, comparison: ScenarioComparison, coverage: Coverage) -> float:
        """How unambiguous the verdict was.

        An exact condition match, or an input tested at other values but never
        the production one, are both crisp findings. "Nothing looked related" is
        the least decisive outcome.
        """
        best = comparison.best
        if best is None:
            return 0.15

        if coverage is Coverage.COVERED and best.covers_all_conditions:
            return 1.0
        if comparison.has_combination_gap():
            return 0.9
        if coverage is Coverage.PARTIAL and best.differing_conditions:
            return 0.9
        if coverage is Coverage.PARTIAL and best.matched_signals:
            return 0.7
        if coverage is Coverage.NOT_COVERED and comparison.unmatched_signals():
            return 0.75
        if coverage is Coverage.NOT_COVERED and not comparison.any_feature_match:
            return 0.6
        return 0.45

    def _corpus_support(self, corpus_size: int) -> float:
        """A "not covered" verdict from a 5-test index deserves little trust."""
        if corpus_size <= 0:
            return 0.0
        return min(1.0, corpus_size / CORPUS_CONFIDENT_SIZE)
