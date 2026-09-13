"""Database engine and session management (SQLite for the POC)."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..config.logging_conf import get_logger
from ..config.settings import get_settings

log = get_logger(__name__)


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _ensure_sqlite_directory(url: str) -> None:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return
    path = Path(url[len(prefix) :])
    if path.parent and str(path.parent) not in {"", "."}:
        path.parent.mkdir(parents=True, exist_ok=True)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _ensure_sqlite_directory(settings.database_url)
        connect_args = (
            {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
        )
        _engine = create_engine(
            settings.database_url,
            future=True,
            connect_args=connect_args,
        )
        if settings.database_url.startswith("sqlite"):

            @event.listens_for(_engine, "connect")
            def _set_sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

        log.info("database configured", extra={"event": "db.configured"})
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionFactory


def init_db() -> None:
    """Create all tables. Safe to call repeatedly."""
    from . import models  # noqa: F401  (registers mappers)

    Base.metadata.create_all(bind=get_engine())
    log.info("database ready", extra={"event": "db.ready"})


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope. Rolls back on any exception."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_state_for_tests() -> None:
    """Drop cached engine/session so a test can point at a different database."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
