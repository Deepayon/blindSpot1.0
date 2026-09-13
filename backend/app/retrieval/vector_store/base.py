"""VectorStore abstraction.

Keeps the analysis engine independent of the index implementation. The POC ships
an exact numpy store and an optional FAISS store; a managed vector database
would be another implementation of this same interface.

All vectors are assumed L2-normalised, so inner product == cosine similarity.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np


class VectorStore(ABC):
    name: str = "base"

    @abstractmethod
    def build(self, vectors: np.ndarray) -> None:
        """Replace the index contents with `vectors` (shape: n x dim).

        Row `i` corresponds to ordinal `i`; mapping ordinals back to tests is
        the caller's responsibility.
        """

    @abstractmethod
    def search(self, query: np.ndarray, k: int) -> list[tuple[int, float]]:
        """Return up to `k` (ordinal, similarity) pairs, best first."""

    @abstractmethod
    def __len__(self) -> int: ...

    @abstractmethod
    def save(self, directory: Path) -> None: ...

    @abstractmethod
    def load(self, directory: Path) -> bool:
        """Restore from `directory`. Returns False when nothing was persisted."""


def build_vector_store(preference: str = "auto") -> VectorStore:
    """Select a vector store.

    "auto" prefers FAISS when installed and silently falls back to the exact
    numpy store, which is more than fast enough at POC scale (a few thousand
    tests searched in well under a millisecond).
    """
    from .numpy_store import NumpyVectorStore

    if preference in {"numpy", "memory", "exact"}:
        return NumpyVectorStore()

    if preference in {"auto", "faiss"}:
        try:
            from .faiss_store import FaissVectorStore

            return FaissVectorStore()
        except ImportError:
            if preference == "faiss":
                raise
    return NumpyVectorStore()


__all__ = ["VectorStore", "build_vector_store"]
