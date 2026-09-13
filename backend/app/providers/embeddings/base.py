"""EmbeddingProvider abstraction.

The default implementation is fully local and deterministic. Swapping in
OpenAI / Anthropic / sentence-transformers means implementing this interface and
registering it in `providers/embeddings/__init__.py` — nothing else changes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any

import numpy as np


class EmbeddingProvider(ABC):
    """Turns text into L2-normalised vectors."""

    name: str = "base"

    @property
    @abstractmethod
    def dimensions(self) -> int: ...

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        """Embed a corpus. Returns shape (len(texts), dimensions), float32."""

    @abstractmethod
    def embed_query(self, text: str) -> np.ndarray:
        """Embed a single query. Returns shape (dimensions,), float32."""

    def fit(self, texts: Sequence[str]) -> None:
        """Optional corpus-statistics pass. No-op for stateless providers."""
        return None

    def state(self) -> dict[str, Any]:
        """Serialisable state to persist alongside the index."""
        return {}

    def load_state(self, state: dict[str, Any]) -> None:
        """Restore state produced by `state()`."""
        return None

    @property
    def is_external(self) -> bool:
        """True when embedding sends text to a third party."""
        return False


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    np.maximum(norms, 1e-12, out=norms)
    return (matrix / norms).astype(np.float32)
