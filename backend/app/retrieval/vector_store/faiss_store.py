"""FAISS-backed vector store.

Uses `IndexFlatIP`, exact inner-product search. Because BlindSpot normalises
every vector, inner product is cosine similarity, and an exact index keeps
results reproducible (an approximate index would make analyses non-deterministic
between runs, which undermines the evidence-first guarantee).
"""
from __future__ import annotations

from pathlib import Path

import faiss  # raises ImportError when unavailable; handled by build_vector_store
import numpy as np

from .base import VectorStore

_INDEX_FILE = "faiss.index"


class FaissVectorStore(VectorStore):
    name = "faiss"

    def __init__(self) -> None:
        self._index: faiss.Index | None = None

    def build(self, vectors: np.ndarray) -> None:
        matrix = np.ascontiguousarray(vectors, dtype=np.float32)
        if matrix.shape[0] == 0:
            self._index = None
            return
        index = faiss.IndexFlatIP(matrix.shape[1])
        index.add(matrix)
        self._index = index

    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        if self._index is None or self._index.ntotal == 0 or k <= 0:
            return []
        vector = np.ascontiguousarray(np.asarray(query, dtype=np.float32).reshape(1, -1))
        scores, indices = self._index.search(vector, min(k, self._index.ntotal))
        return [
            (int(i), float(s))
            for i, s in zip(indices[0], scores[0], strict=True)
            if i >= 0
        ]

    def __len__(self) -> int:
        return 0 if self._index is None else int(self._index.ntotal)

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        if self._index is not None:
            faiss.write_index(self._index, str(directory / _INDEX_FILE))

    def load(self, directory: Path) -> bool:
        path = directory / _INDEX_FILE
        if not path.is_file():
            return False
        try:
            self._index = faiss.read_index(str(path))
            return True
        except Exception:
            return False
