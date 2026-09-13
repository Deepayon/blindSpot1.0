"""Local, deterministic TF-IDF embeddings over a hashed feature space.

Chosen as the default because BlindSpot must work offline, must never ship a
private repository to a third party, and must produce byte-identical vectors on
every run so that analyses are reproducible and testable.

Feature space:
  * unigrams              weight 1.0
  * adjacent bigrams      weight 0.7   (captures "immediate retry", "100 discount")
  * curated synonyms      weight 0.4   (lets "coupon" reach a "discount" test)

Hashing uses blake2b with a signed bucket, which cancels part of the collision
bias you would otherwise get from a plain modulo hash.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter
from collections.abc import Sequence
from typing import Any

import numpy as np

from ...domain.text import SYNONYMS, bigrams, tokenize
from .base import EmbeddingProvider, l2_normalize

_UNIGRAM_WEIGHT = 1.0
_BIGRAM_WEIGHT = 0.7
_SYNONYM_WEIGHT = 0.4


class HashedTfidfEmbedder(EmbeddingProvider):
    name = "hashed_tfidf"

    def __init__(self, dimensions: int = 512, model_name: str = "local-hashed-tfidf-v1") -> None:
        self._dimensions = max(64, int(dimensions))
        self.model_name = model_name
        # Document frequency per bucket, learned in `fit`. Until then IDF is 1.
        self._idf: np.ndarray = np.ones(self._dimensions, dtype=np.float32)
        self._fitted = False
        self._bucket_cache: dict[str, tuple[int, float]] = {}

    @property
    def dimensions(self) -> int:
        return self._dimensions

    # -- feature extraction -------------------------------------------------

    def _bucket(self, feature: str) -> tuple[int, float]:
        """Map a feature to (index, sign). Cached — tokens repeat heavily."""
        cached = self._bucket_cache.get(feature)
        if cached is not None:
            return cached
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        index = value % self._dimensions
        sign = 1.0 if (value >> 63) & 1 else -1.0
        result = (index, sign)
        if len(self._bucket_cache) < 200_000:
            self._bucket_cache[feature] = result
        return result

    def _weighted_features(self, text: str) -> Counter[str]:
        """Weighted feature counts for one document."""
        tokens = tokenize(text)
        features: Counter[str] = Counter()
        for token in tokens:
            features[token] += _UNIGRAM_WEIGHT
        for gram in bigrams(tokens):
            features[gram] += _BIGRAM_WEIGHT
        for token in set(tokens):
            for synonym in SYNONYMS.get(token, ()):
                if synonym not in features:
                    features[synonym] += _SYNONYM_WEIGHT
        return features

    def _raw_vector(self, text: str) -> tuple[np.ndarray, set[int]]:
        """Sublinear-TF vector plus the set of buckets it touched."""
        vector = np.zeros(self._dimensions, dtype=np.float32)
        touched: set[int] = set()
        for feature, count in self._weighted_features(text).items():
            index, sign = self._bucket(feature)
            vector[index] += sign * (1.0 + math.log(count)) if count > 1 else sign * count
            touched.add(index)
        return vector, touched

    # -- EmbeddingProvider --------------------------------------------------

    def fit(self, texts: Sequence[str]) -> None:
        """Learn inverse document frequency per bucket from the test corpus."""
        total = len(texts)
        if total == 0:
            return
        document_frequency = np.zeros(self._dimensions, dtype=np.float32)
        for text in texts:
            _, touched = self._raw_vector(text)
            for index in touched:
                document_frequency[index] += 1.0
        # Smoothed IDF; never zero, so no bucket is entirely discarded.
        self._idf = (np.log((total + 1.0) / (document_frequency + 1.0)) + 1.0).astype(np.float32)
        self._fitted = True

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self._dimensions), dtype=np.float32)
        matrix = np.zeros((len(texts), self._dimensions), dtype=np.float32)
        for row, text in enumerate(texts):
            vector, _ = self._raw_vector(text)
            matrix[row] = vector
        matrix *= self._idf
        return l2_normalize(matrix)

    def embed_query(self, text: str) -> np.ndarray:
        vector, _ = self._raw_vector(text)
        vector *= self._idf
        return l2_normalize(vector.reshape(1, -1))[0]

    def state(self) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "dimensions": self._dimensions,
            "fitted": self._fitted,
            "idf": self._idf.tolist(),
        }

    def load_state(self, state: dict[str, Any]) -> None:
        idf = state.get("idf")
        if isinstance(idf, list) and len(idf) == self._dimensions:
            self._idf = np.asarray(idf, dtype=np.float32)
            self._fitted = bool(state.get("fitted", True))
