"""Administrator choices stored by ContextHub (ADR-002), table ``hub_settings``.

Reads go through a short in-process cache because some of them sit on the chat path
(the offered models are checked on every question). The cache expires by itself after
``_TTL_SECONDS``: with several API workers, a change made through one of them reaches the
others within that delay. A write invalidates the cache of the writing process at once.

A database error on read is treated as "no setting": the environment defaults apply,
chat keeps working. A missing table (migration 69ab4256 not applied) is logged once per
process, then at debug level, and reported by ``probe()`` in /admin/system; any other
error (database briefly down) keeps being logged, at most once a minute.
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError

from src.db.models import InstanceSetting, utcnow
from src.db.session import SessionLocal, get_engine
from src.utils.logging_config import get_logger

log = get_logger()

_TTL_SECONDS = 5.0
_TRANSIENT_LOG_SECONDS = 60.0
_MISSING = object()
TABLE = InstanceSetting.__tablename__
MISSING_TABLE_NOTE = (
    "Migration manquante : réglages des modèles indisponibles (table hub_settings absente). "
    "Lancez « alembic upgrade head » puis redémarrez l'API."
)

_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}
_missing_table_logged = False
_transient_logged_at: float | None = None


class SettingsUnavailable(Exception):
    """The settings table cannot be written (database down, migration missing)."""


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def _table_exists() -> bool | None:
    """Whether the settings table exists; None when the database cannot tell (down)."""
    try:
        return inspect(get_engine()).has_table(TABLE)
    except SQLAlchemyError:
        return None


def _log_read_error(key: str, exc: SQLAlchemyError) -> None:
    global _missing_table_logged, _transient_logged_at
    if _table_exists() is False:
        if not _missing_table_logged:
            _missing_table_logged = True
            log.warning("[settings] %s : valeurs d'environnement appliquées", MISSING_TABLE_NOTE)
        else:
            log.debug("[settings] table %s absente (réglage %s)", TABLE, key)
        return
    now = time.monotonic()
    if _transient_logged_at is None or now - _transient_logged_at >= _TRANSIENT_LOG_SECONDS:
        _transient_logged_at = now
        log.warning("[settings] lecture du réglage %s impossible (%s) : valeurs d'environnement", key, type(exc).__name__)


def probe() -> dict:
    """For /admin/system: ``{"ok": True}`` or ``{"ok": False, "error": "missing_table"|<type>}``."""
    global _missing_table_logged
    db = SessionLocal()
    try:
        db.execute(select(InstanceSetting.key).limit(1)).all()
        _missing_table_logged = False  # migrated meanwhile: warn again if it ever disappears
        return {"ok": True}
    except SQLAlchemyError as exc:
        db.rollback()
        return {"ok": False, "error": "missing_table" if _table_exists() is False else type(exc).__name__}
    finally:
        db.close()


def get(key: str) -> Any | None:
    """The stored JSON value of ``key`` (a copy), or None. Blocking: not from the event loop."""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] > now:
            return None if hit[1] is _MISSING else copy.deepcopy(hit[1])
    db = SessionLocal()
    try:
        row = db.get(InstanceSetting, key)
        value = _MISSING if row is None else row.value
    except SQLAlchemyError as exc:
        db.rollback()
        db.close()
        _log_read_error(key, exc)
        value = _MISSING
    finally:
        db.close()
    with _lock:
        _cache[key] = (now + _TTL_SECONDS, value)
    return None if value is _MISSING else copy.deepcopy(value)


def put(key: str, value: Any, actor_id: uuid.UUID | None) -> None:
    db = SessionLocal()
    try:
        row = db.get(InstanceSetting, key)
        if row is None:
            db.add(InstanceSetting(key=key, value=value, updated_by=actor_id))
        else:
            row.value = value
            row.updated_by = actor_id
            row.updated_at = utcnow()
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        log.error("[settings] écriture du réglage %s impossible (%s)", key, type(exc).__name__)
        raise SettingsUnavailable from exc
    finally:
        db.close()
        with _lock:
            _cache.pop(key, None)


def delete(key: str) -> None:
    db = SessionLocal()
    try:
        row = db.get(InstanceSetting, key)
        if row is not None:
            db.delete(row)
            db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        log.error("[settings] suppression du réglage %s impossible (%s)", key, type(exc).__name__)
        raise SettingsUnavailable from exc
    finally:
        db.close()
        with _lock:
            _cache.pop(key, None)
