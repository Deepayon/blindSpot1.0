"""BM25 lexical index.

The semantic half of retrieval generalises ("coupon" finds "discount"); the
lexical half is what reliably pins down the *exact* tokens that matter, an
error code, a field name, `100`. The spec calls for neither keywords alone nor
embeddings alone, so BlindSpot runs both and blends the scores.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

from ..domain.text import expand, tokenize

_K1 = 1.5
_B = 0.75


class BM25Index:
    """Classic Okapi BM25 over an in-memory inverted index."""

    def __init__(self, k1: float = _K1, b: float = _B) -> None:
        self.k1 = k1
        self.b = b
        self._postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self._idf: dict[str, float] = {}
        self._lengths: list[int] = []
        self._average_length: float = 0.0
        self._document_count: int = 0

    def build(self, documents: Sequence[str]) -> None:
        self._postings = defaultdict(list)
        self._lengths = []
        self._document_count = len(documents)

        for ordinal, document in enumerate(documents):
            tokens = tokenize(document)
            self._lengths.append(len(tokens))
            for term, count in Counter(tokens).items():
                self._postings[term].append((ordinal, count))

        total = sum(self._lengths)
        self._average_length = total / self._document_count if self._document_count else 0.0
        self._idf = {
            term: math.log(1.0 + (self._document_count - len(postings) + 0.5) / (len(postings) + 0.5))
            for term, postings in self._postings.items()
        }

    def search(self, query: str, *, use_synonyms: bool = True) -> dict[int, float]:
        """Return {ordinal: bm25 score} for every document sharing a term."""
        terms = self._query_terms(query, use_synonyms)
        if not terms or not self._document_count:
            return {}

        scores: dict[int, float] = defaultdict(float)
        for term, weight in terms.items():
            postings = self._postings.get(term)
            if not postings:
                continue
            idf = self._idf.get(term, 0.0)
            for ordinal, frequency in postings:
                length = self._lengths[ordinal] or 1
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * length / (self._average_length or 1)
                )
                scores[ordinal] += weight * idf * (frequency * (self.k1 + 1)) / denominator
        return dict(scores)

    def matched_terms(self, query: str, ordinal: int, limit: int = 8) -> list[str]:
        """Which query terms actually occur in a given document, used as evidence."""
        matched: list[str] = []
        for term in self._query_terms(query, use_synonyms=False):
            postings = self._postings.get(term)
            if postings and any(o == ordinal for o, _ in postings):
                matched.append(term)
                if len(matched) >= limit:
                    break
        return matched

    def _query_terms(self, query: str, use_synonyms: bool) -> dict[str, float]:
        """Query terms with weights; synonyms count for less than literal terms."""
        tokens = tokenize(query)
        if not tokens:
            return {}
        terms: dict[str, float] = {token: 1.0 for token in tokens}
        if use_synonyms:
            for token in expand(tokens):
                terms.setdefault(token, 0.5)
        return terms

    def state(self) -> dict[str, Any]:
        return {
            "postings": {term: postings for term, postings in self._postings.items()},
            "lengths": self._lengths,
            "document_count": self._document_count,
        }

    def load_state(self, state: dict[str, Any]) -> None:
        self._postings = defaultdict(
            list, {term: [tuple(p) for p in postings] for term, postings in state["postings"].items()}
        )
        self._lengths = list(state["lengths"])
        self._document_count = int(state["document_count"])
        total = sum(self._lengths)
        self._average_length = total / self._document_count if self._document_count else 0.0
        self._idf = {
            term: math.log(1.0 + (self._document_count - len(postings) + 0.5) / (len(postings) + 0.5))
            for term, postings in self._postings.items()
        }

    def __len__(self) -> int:
        return self._document_count
