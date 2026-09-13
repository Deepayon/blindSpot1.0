"""Application state.

Holds the objects that must be shared across requests, the test index and the
analysis engine, and owns the "index once, query many times" lifecycle from
spec Principle 1.

On start-up the index is restored from disk; if that fails or is stale it is
rebuilt from SQLite, which is always the source of truth.
"""
from __future__ import annotations

import threading

from ..config.logging_conf import get_logger
from ..config.settings import Settings, get_settings
from ..db.base import session_scope
from ..intelligence.engine import GapAnalysisEngine
from ..providers.llm import LLMProvider, build_llm_provider
from ..repositories.test_repository import TestRepository
from ..retrieval.index import TestIndex

log = get_logger(__name__)


class AppState:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.index = TestIndex(settings=self.settings)
        self.llm: LLMProvider = build_llm_provider(self.settings)
        self.engine = GapAnalysisEngine(self.index, self.llm, self.settings)
        self._lock = threading.RLock()

    # -- lifecycle ----------------------------------------------------------

    def bootstrap(self) -> None:
        """Restore the index, preferring the persisted copy."""
        with self._lock:
            if self.index.load():
                stored = self._stored_test_count()
                if stored == len(self.index):
                    return
                log.info(
                    "persisted index does not match the database; rebuilding",
                    extra={
                        "event": "index.mismatch",
                        "index": len(self.index),
                        "database": stored,
                    },
                )
            self.rebuild_index()

    def rebuild_index(self, *, persist: bool = True) -> int:
        """Rebuild the in-memory index from everything stored in SQLite."""
        with self._lock:
            with session_scope() as session:
                tests = TestRepository(session).all_normalized()
            self.index.build(tests)
            if persist:
                self.index.save()
            return len(tests)

    def _stored_test_count(self) -> int:
        try:
            with session_scope() as session:
                return TestRepository(session).count()
        except Exception as exc:  # database problems must not block start-up
            log.warning(
                "could not read stored test count",
                extra={"event": "db.count_failed", "error": str(exc)},
            )
            return -1

    # -- info ---------------------------------------------------------------

    def describe_ai(self) -> dict[str, object]:
        """AI configuration plus whether the provider is *actually* usable.

        `external_ai_enabled` reflects configuration; `llm_active` reflects
        reality. A provider named without an API key falls back to local
        analysis, and the UI must not claim data is leaving the machine when it
        is not.
        """
        described = {**self.settings.describe_ai(), "llm_active": self.llm.available}
        if self.llm.available and self.llm.model:
            # Report the resolved model, not the configured one.
            described["llm_model"] = self.llm.model
        return described

    def describe(self) -> dict[str, object]:
        return {"index": self.index.describe(), "ai": self.describe_ai()}


_state: AppState | None = None
_state_lock = threading.Lock()


def get_state() -> AppState:
    global _state
    if _state is None:
        with _state_lock:
            if _state is None:
                _state = AppState()
    return _state


def reset_state_for_tests() -> None:
    global _state
    _state = None
