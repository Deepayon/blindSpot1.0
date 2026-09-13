"""The test index, built once, searched per incident.

Principle 1 of the spec: tests are indexed once, not re-uploaded per incident.
This class owns that index (vectors + lexical postings + normalised metadata),
persists it under `BLINDSPOT_INDEX_PATH`, and reloads it on start-up.

Only normalised metadata is embedded, never raw source bodies, so enabling an
external embedding provider can never leak private repository code.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from ..config.logging_conf import get_logger
from ..config.settings import Settings, get_settings
from ..domain.models import NormalizedTest
from ..intelligence.extraction import derive_test_signals
from ..providers.embeddings import EmbeddingProvider, build_embedding_provider
from .lexical import BM25Index
from .vector_store.base import VectorStore, build_vector_store

log = get_logger(__name__)

_META_FILE = "index_meta.json"
_SCHEMA_VERSION = 1


class TestIndex:
    """Thread-safe in-memory index with disk persistence."""

    def __init__(
        self,
        embedder: EmbeddingProvider | None = None,
        vector_store: VectorStore | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.embedder = embedder or build_embedding_provider(self.settings)
        self.vector_store = vector_store or build_vector_store(self.settings.vector_store)
        self.lexical = BM25Index()
        self._tests: list[NormalizedTest] = []
        self._lock = threading.RLock()

    # -- lifecycle ----------------------------------------------------------

    def build(self, tests: Sequence[NormalizedTest]) -> None:
        """Rebuild the whole index from the given tests."""
        with self._lock:
            self._tests = list(tests)
            for test in self._tests:
                # Guarantee the signal invariant regardless of how the test was
                # produced (parser, API, or a hand-built fixture). Retrieval and
                # comparison both rely on it, so it is computed exactly once here.
                test.extra["signals"] = derive_test_signals(
                    test.name, test.scenario, test.inputs
                )
            documents = [test.searchable_text() for test in self._tests]

            if documents:
                # IDF must reflect this corpus, so fit before embedding.
                self.embedder.fit(documents)
                vectors = self.embedder.embed_documents(documents)
            else:
                vectors = np.zeros((0, self.embedder.dimensions), dtype=np.float32)

            self.vector_store.build(vectors)
            self.lexical.build(documents)

        log.info(
            "index built",
            extra={
                "event": "index.built",
                "tests": len(self._tests),
                "vector_store": self.vector_store.name,
                "embedding_model": getattr(self.embedder, "model_name", self.embedder.name),
            },
        )

    def clear(self) -> None:
        with self._lock:
            self.build([])

    # -- search primitives --------------------------------------------------

    def vector_search(self, query_text: str, k: int) -> list[tuple[int, float]]:
        if not self._tests:
            return []
        vector = self.embedder.embed_query(query_text)
        return self.vector_store.search(vector, k)

    def lexical_search(self, query_text: str) -> dict[int, float]:
        return self.lexical.search(query_text) if self._tests else {}

    def matched_terms(self, query_text: str, ordinal: int) -> list[str]:
        return self.lexical.matched_terms(query_text, ordinal)

    def test_at(self, ordinal: int) -> NormalizedTest | None:
        return self._tests[ordinal] if 0 <= ordinal < len(self._tests) else None

    @property
    def tests(self) -> list[NormalizedTest]:
        return self._tests

    def __len__(self) -> int:
        return len(self._tests)

    @property
    def is_empty(self) -> bool:
        return not self._tests

    def describe(self) -> dict[str, object]:
        return {
            "tests": len(self._tests),
            "vector_store": self.vector_store.name,
            "embedding_provider": self.embedder.name,
            "embedding_model": getattr(self.embedder, "model_name", self.embedder.name),
            "dimensions": self.embedder.dimensions,
        }

    # -- persistence --------------------------------------------------------

    def save(self, directory: Path | None = None) -> None:
        directory = Path(directory or self.settings.index_path)
        with self._lock:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                self.vector_store.save(directory)
                payload = {
                    "schema_version": _SCHEMA_VERSION,
                    "embedder": self.embedder.state(),
                    "lexical": self.lexical.state(),
                    "tests": [test.model_dump(mode="json") for test in self._tests],
                }
                (directory / _META_FILE).write_text(
                    json.dumps(payload), encoding="utf-8"
                )
            except OSError as exc:
                # A non-persistable index is a degraded state, not a failure:
                # it simply gets rebuilt from SQLite on the next start.
                log.warning(
                    "index could not be persisted",
                    extra={"event": "index.save_failed", "error": str(exc)},
                )
                return
        log.info("index persisted", extra={"event": "index.saved", "tests": len(self._tests)})

    def load(self, directory: Path | None = None) -> bool:
        directory = Path(directory or self.settings.index_path)
        meta_path = directory / _META_FILE
        if not meta_path.is_file():
            return False

        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != _SCHEMA_VERSION:
                log.info("index schema changed; rebuild required", extra={"event": "index.stale"})
                return False
            tests = [NormalizedTest.model_validate(t) for t in payload["tests"]]
            with self._lock:
                self.embedder.load_state(payload.get("embedder", {}))
                self.lexical.load_state(payload["lexical"])
                if not self.vector_store.load(directory):
                    return False
                self._tests = tests
                if len(self.vector_store) != len(tests):
                    log.warning(
                        "persisted index is inconsistent; rebuild required",
                        extra={"event": "index.inconsistent"},
                    )
                    return False
        except (OSError, ValueError, KeyError) as exc:
            log.warning(
                "index could not be loaded; rebuild required",
                extra={"event": "index.load_failed", "error": str(exc)},
            )
            return False

        log.info("index loaded", extra={"event": "index.loaded", "tests": len(self._tests)})
        return True
