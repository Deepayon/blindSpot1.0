"""Exact brute-force vector store backed by a single numpy matrix.

At POC scale this is the right default: an exhaustive search over a few thousand
512-dimensional vectors is one matrix-vector product, it has no approximation
error, and it needs no extra dependency.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .base import VectorStore

_VECTORS_FILE = "vectors.npy"


class NumpyVectorStore(VectorStore):
    name = "numpy"

    def __init__(self) -> None:
        self._matrix: np.ndarray | None = None

    def build(self, vectors: np.ndarray) -> None:
        self._matrix = np.ascontiguousarray(vectors, dtype=np.float32)

    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        if self._matrix is None or self._matrix.shape[0] == 0 or k <= 0:
            return []
        scores = self._matrix @ np.asarray(query, dtype=np.float32)
        k = min(k, scores.shape[0])
        # argpartition gives the top-k cheaply; sort only that slice.
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [(int(i), float(scores[i])) for i in top]

    def __len__(self) -> int:
        return 0 if self._matrix is None else int(self._matrix.shape[0])

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        if self._matrix is not None:
            np.save(directory / _VECTORS_FILE, self._matrix)

    def load(self, directory: Path) -> bool:
        path = directory / _VECTORS_FILE
        if not path.is_file():
            return False
        try:
            self._matrix = np.load(path).astype(np.float32)
            return True
        except (OSError, ValueError):
            return False
