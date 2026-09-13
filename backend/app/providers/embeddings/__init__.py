"""Embedding provider registry."""
from __future__ import annotations

from ...config.logging_conf import get_logger
from ...config.settings import Settings, get_settings
from .base import EmbeddingProvider
from .hashed_tfidf import HashedTfidfEmbedder

log = get_logger(__name__)

__all__ = ["EmbeddingProvider", "HashedTfidfEmbedder", "build_embedding_provider"]


def build_embedding_provider(settings: Settings | None = None) -> EmbeddingProvider:
    settings = settings or get_settings()
    name = settings.embedding_provider

    if name in {"hashed_tfidf", "local", "none", ""}:
        return HashedTfidfEmbedder(
            dimensions=settings.embedding_dimensions,
            model_name=settings.embedding_model,
        )

    if name == "sentence_transformers":
        try:
            from .sentence_transformers import SentenceTransformerEmbedder

            return SentenceTransformerEmbedder(settings.embedding_model)
        except Exception as exc:  # pragma: no cover - optional dependency
            log.warning(
                "embedding provider unavailable, falling back to local",
                extra={"event": "embeddings.fallback", "provider": name, "error": str(exc)},
            )

    log.warning(
        "unknown embedding provider, using local default",
        extra={"event": "embeddings.unknown", "provider": name},
    )
    return HashedTfidfEmbedder(
        dimensions=settings.embedding_dimensions, model_name=settings.embedding_model
    )
