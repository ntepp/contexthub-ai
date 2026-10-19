"""Database engine and session factory.

The engine is built lazily from settings so tests can point it at SQLite with
``configure_engine()`` before anything touches the database.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def configure_engine(url: str | None = None) -> Engine:
    global _engine, _session_factory
    url = url or get_settings().sqlalchemy_url
    if url.startswith("sqlite"):
        _engine = create_engine(
            url, connect_args={"check_same_thread": False}, poolclass=StaticPool
        )

        @event.listens_for(_engine, "connect")
        def _sqlite_fk(dbapi_connection, _record):  # cascades must behave as on PostgreSQL
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

    else:
        _engine = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)
    _session_factory = sessionmaker(bind=_engine, autocommit=False, autoflush=False, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        configure_engine()
    assert _engine is not None
    return _engine


def SessionLocal() -> Session:  # noqa: N802 - kept for backwards compatibility
    if _session_factory is None:
        configure_engine()
    assert _session_factory is not None
    return _session_factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Short-lived session for workers and background jobs."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def is_postgres() -> bool:
    return get_engine().dialect.name == "postgresql"
