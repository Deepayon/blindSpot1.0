"""Hybrid candidate retrieval.

Narrows thousands of indexed tests down to the handful worth reasoning about, so
that analysis never costs one LLM call per test (spec §45).

Signals blended per candidate:
  * embedding cosine similarity , generalises across wording
  * BM25 lexical score          , pins exact tokens (field names, codes, values)
  * feature agreement           , a Checkout incident prefers Checkout tests
  * condition-key overlap       , a test that varies `discount` beats one that doesn't

The blend is deterministic and every component is reported in the result, so the
UI can show exactly why a test was considered relevant.
"""
from __future__ import annotations

from ..config.logging_conf import get_logger
from ..config.settings import Settings, get_settings
from ..domain.models import NormalizedIncident, RetrievalDebug, RetrievedTest
from ..domain.text import normalize
from .index import TestIndex

log = get_logger(__name__)

#: Multiplicative boosts. Kept small, retrieval should surface candidates, not
#: pre-judge them; judging is the gap analyser's job.
_FEATURE_BOOST = 1.30
_CONDITION_KEY_BOOST = 1.15
_SIGNAL_BOOST = 1.10

#: How many vector neighbours to pull before blending. Wider than `top_k` so a
#: strong lexical match isn't lost just because its embedding ranked 30th.
_VECTOR_OVERSAMPLE = 6

#: Candidates scoring below this fraction of the best candidate are dropped.
#: An absolute floor alone cannot tell "weakly related" from "unrelated but the
#: corpus is small", and padding the evidence list with near-miss tests makes
#: the explanation harder to trust, not easier.
_RELATIVE_SCORE_FLOOR = 0.35


class TestRetriever:
    def __init__(self, index: TestIndex, settings: Settings | None = None) -> None:
        self.index = index
        self.settings = settings or get_settings()

    def retrieve(
        self, incident: NormalizedIncident, top_k: int | None = None
    ) -> tuple[list[RetrievedTest], RetrievalDebug]:
        top_k = top_k or self.settings.retrieval_top_k
        query = incident.searchable_text()

        debug = RetrievalDebug(
            strategy="hybrid(vector+bm25)",
            vector_store=self.index.vector_store.name,
            embedding_model=str(self.index.describe()["embedding_model"]),
        )

        if self.index.is_empty or not query.strip():
            return [], debug

        vector_hits = dict(
            self.index.vector_search(query, k=max(top_k * _VECTOR_OVERSAMPLE, top_k))
        )
        lexical_hits = self.index.lexical_search(query)

        candidates = set(vector_hits) | set(lexical_hits)
        debug.candidate_count = len(candidates)
        if not candidates:
            return [], debug

        # BM25 is unbounded; scale to 0..1 so the weights mean what they say.
        max_lexical = max(lexical_hits.values(), default=0.0) or 1.0
        incident_feature = normalize(incident.feature)
        condition_keys = {normalize(k) for k in incident.conditions}
        incident_signals = set(incident.signals)

        vector_weight = self.settings.retrieval_vector_weight
        lexical_weight = self.settings.retrieval_lexical_weight

        scored: list[RetrievedTest] = []
        for ordinal in candidates:
            test = self.index.test_at(ordinal)
            if test is None:
                continue

            vector_score = max(0.0, vector_hits.get(ordinal, 0.0))
            lexical_score = lexical_hits.get(ordinal, 0.0) / max_lexical
            score = vector_weight * vector_score + lexical_weight * lexical_score

            feature_match = bool(incident_feature) and normalize(test.feature) == incident_feature
            if feature_match:
                score *= _FEATURE_BOOST
            if condition_keys & {normalize(k) for k in test.inputs}:
                score *= _CONDITION_KEY_BOOST
            if incident_signals & set(test.extra.get("signals") or []):
                score *= _SIGNAL_BOOST

            scored.append(
                RetrievedTest(
                    test=test,
                    score=round(min(score, 1.0), 4),
                    vector_score=round(vector_score, 4),
                    lexical_score=round(lexical_score, 4),
                    feature_match=feature_match,
                    matched_terms=self.index.matched_terms(query, ordinal),
                )
            )

        scored.sort(key=lambda item: (-item.score, item.test.id))
        debug.top_score = scored[0].score if scored else 0.0

        floor = max(self.settings.retrieval_min_score, debug.top_score * _RELATIVE_SCORE_FLOOR)
        kept = [item for item in scored if item.score >= floor][:top_k]
        # A weak-but-best candidate is still evidence worth showing; never return
        # nothing just because everything scored below the floor.
        if not kept and scored:
            kept = scored[:3]
        debug.considered_count = len(kept)

        log.info(
            "retrieval completed",
            extra={
                "event": "analysis.retrieved",
                "incident": incident.id,
                "candidates": debug.candidate_count,
                "kept": len(kept),
                "top_score": debug.top_score,
            },
        )
        return kept, debug
