"""Application configuration.

All configuration is read from environment variables (optionally seeded from a
`.env` file at the project root). Nothing here may contain a default secret.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

# Project root = <repo>/  (this file lives at backend/app/config/settings.py)
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader.

    Deliberately dependency-free and non-destructive: variables already present
    in the real environment always win.
    """
    if not path.is_file():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
    except OSError:
        # A broken .env must never prevent the app from starting.
        pass


_load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


class Settings:
    """Resolved, immutable-by-convention application settings."""

    def __init__(self) -> None:
        self.project_root: Path = PROJECT_ROOT

        # --- Server ---
        self.host: str = _env("BLINDSPOT_HOST", "127.0.0.1")
        self.port: int = _env_int("BLINDSPOT_PORT", 8000)
        self.log_level: str = _env("BLINDSPOT_LOG_LEVEL", "INFO").upper()
        self.log_json: bool = _env_bool("BLINDSPOT_LOG_JSON", False)

        # --- Persistence ---
        default_db = (PROJECT_ROOT / "data" / "blindspot.db").as_posix()
        self.database_url: str = _env("BLINDSPOT_DATABASE_URL", f"sqlite:///{default_db}")
        self.index_path: Path = Path(
            _env("BLINDSPOT_INDEX_PATH", (PROJECT_ROOT / "data" / "index").as_posix())
        )

        # --- AI providers ---
        # "none" keeps BlindSpot fully offline and deterministic.
        self.llm_provider: str = _env("BLINDSPOT_LLM_PROVIDER", "none").lower()
        self.llm_model: str = _env("BLINDSPOT_LLM_MODEL", "claude-sonnet-5")
        self.llm_api_key: str | None = os.environ.get("BLINDSPOT_LLM_API_KEY") or None
        # Overrides the provider's endpoint. Lets any OpenAI-compatible gateway
        # (OpenRouter, Together, vLLM, LM Studio, Azure) be used without new code.
        self.llm_base_url: str | None = os.environ.get("BLINDSPOT_LLM_BASE_URL") or None
        self.llm_timeout_seconds: float = _env_float("BLINDSPOT_LLM_TIMEOUT_SECONDS", 30.0)
        self.llm_max_candidates: int = _env_int("BLINDSPOT_LLM_MAX_CANDIDATES", 8)

        self.embedding_provider: str = _env("BLINDSPOT_EMBEDDING_PROVIDER", "hashed_tfidf").lower()
        self.embedding_model: str = _env("BLINDSPOT_EMBEDDING_MODEL", "local-hashed-tfidf-v1")
        self.embedding_dimensions: int = _env_int("BLINDSPOT_EMBEDDING_DIMENSIONS", 512)

        self.vector_store: str = _env("BLINDSPOT_VECTOR_STORE", "auto").lower()

        # --- Retrieval / analysis tuning ---
        self.retrieval_top_k: int = _env_int("BLINDSPOT_RETRIEVAL_TOP_K", 15)
        self.retrieval_min_score: float = _env_float("BLINDSPOT_RETRIEVAL_MIN_SCORE", 0.08)
        self.retrieval_vector_weight: float = _env_float("BLINDSPOT_RETRIEVAL_VECTOR_WEIGHT", 0.5)
        self.retrieval_lexical_weight: float = _env_float("BLINDSPOT_RETRIEVAL_LEXICAL_WEIGHT", 0.5)

        # --- Ingestion safety limits (repository files are untrusted input) ---
        self.max_file_size_bytes: int = _env_int("BLINDSPOT_MAX_FILE_SIZE_BYTES", 2_000_000)
        self.max_scanned_files: int = _env_int("BLINDSPOT_MAX_SCANNED_FILES", 20_000)
        self.allowed_repository_roots: list[Path] = [
            Path(p).expanduser().resolve()
            for p in _env("BLINDSPOT_ALLOWED_REPOSITORY_ROOTS", "").split(os.pathsep)
            if p.strip()
        ]

        # --- Blind spot detection ---
        self.blind_spot_min_incidents: int = _env_int("BLINDSPOT_PATTERN_MIN_INCIDENTS", 3)

        # --- Frontend ---
        self.frontend_dir: Path = Path(
            _env("BLINDSPOT_FRONTEND_DIR", (PROJECT_ROOT / "frontend").as_posix())
        )
        self.serve_frontend: bool = _env_bool("BLINDSPOT_SERVE_FRONTEND", True)

    @property
    def external_ai_enabled(self) -> bool:
        """True when analysis may send data to a third-party service."""
        return self.llm_provider not in {"none", "", "null"}

    def describe_ai(self) -> dict[str, object]:
        """Non-secret summary of the AI configuration, safe to expose in the UI."""
        return {
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model if self.external_ai_enabled else None,
            "llm_configured": bool(self.llm_api_key) if self.external_ai_enabled else False,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "external_ai_enabled": self.external_ai_enabled,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
