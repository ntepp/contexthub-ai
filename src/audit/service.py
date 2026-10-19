"""Append-only, hash-chained audit trail.

Each event stores ``hash = sha256(prev_hash + canonical(event))``. Tampering with,
inserting or deleting any row in the middle of the chain is detected by
``verify_chain()``. Personal-data columns (actor email, IP, user agent) are excluded
from the hash so they can be redacted on GDPR erasure without breaking the chain.

Events are written in their own short transaction so that a failing business
transaction still leaves a trace of the attempt.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from src.db.models import AuditEvent, utcnow
from src.db.session import SessionLocal
from src.utils.logging_config import get_logger

log = get_logger()

GENESIS_HASH = "0" * 64
# Arbitrary constant for pg_advisory_xact_lock: serialises writers of the chain.
_CHAIN_LOCK_ID = 7_340_201


@dataclass
class Actor:
    id: uuid.UUID | None = None
    email: str | None = None
    ip: str | None = None
    user_agent: str | None = None


SYSTEM_ACTOR = Actor(email="system")


def _canon_ts(ts: datetime) -> str:
    if ts.tzinfo is not None:
        ts = ts.astimezone(UTC).replace(tzinfo=None)
    return ts.isoformat(timespec="microseconds")


def _canonical(event: AuditEvent) -> str:
    record = {
        "ts": _canon_ts(event.ts),
        "actor_id": str(event.actor_id) if event.actor_id else None,
        "action": event.action,
        "outcome": event.outcome,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "collection_id": str(event.collection_id) if event.collection_id else None,
        "details": event.details,
    }
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def compute_hash(prev_hash: str, event: AuditEvent) -> str:
    return hashlib.sha256((prev_hash + _canonical(event)).encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    """Round-trip through JSON so the hashed form equals what the database returns."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str)) if value is not None else None


def record(
    action: str,
    actor: Actor | None = None,
    *,
    outcome: str = "success",
    resource_type: str | None = None,
    resource_id: str | uuid.UUID | None = None,
    collection_id: uuid.UUID | None = None,
    details: dict | None = None,
) -> None:
    """Append one event. Never raises: an audit failure is logged loudly instead."""
    actor = actor or SYSTEM_ACTOR
    db = SessionLocal()
    try:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _CHAIN_LOCK_ID})
        prev_hash = db.execute(
            select(AuditEvent.hash).order_by(AuditEvent.id.desc()).limit(1)
        ).scalar_one_or_none() or GENESIS_HASH

        event = AuditEvent(
            ts=utcnow(),
            actor_id=actor.id,
            actor_email=actor.email,
            action=action,
            outcome=outcome,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            collection_id=collection_id,
            details=_json_safe(details),
            ip=actor.ip,
            user_agent=(actor.user_agent or "")[:500] or None,
            prev_hash=prev_hash,
        )
        event.hash = compute_hash(prev_hash, event)
        db.add(event)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("[audit] FAILED to record event action=%s", action)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


@dataclass
class ChainReport:
    valid: bool
    checked: int
    first_id: int | None
    last_id: int | None
    broken_at: int | None = None
    reason: str | None = None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def verify_chain(db: Session, batch_size: int = 1000) -> ChainReport:
    """Walk the chain in id order and recompute every hash.

    The first remaining row is trusted as the anchor (its ``prev_hash`` may point at
    a row removed by the retention policy).
    """
    checked = 0
    first_id = last_id = None
    expected_prev: str | None = None
    last_seen = 0
    while True:
        rows = db.execute(
            select(AuditEvent).where(AuditEvent.id > last_seen).order_by(AuditEvent.id).limit(batch_size)
        ).scalars().all()
        if not rows:
            break
        for event in rows:
            if first_id is None:
                first_id = event.id
            if expected_prev is not None and event.prev_hash != expected_prev:
                return ChainReport(False, checked, first_id, last_id, event.id, "prev_hash ne correspond pas")
            if compute_hash(event.prev_hash, event) != event.hash:
                return ChainReport(False, checked, first_id, last_id, event.id, "contenu modifié")
            expected_prev = event.hash
            last_id = event.id
            checked += 1
        last_seen = rows[-1].id
        db.expunge_all()
    return ChainReport(True, checked, first_id, last_id)


# ---------------------------------------------------------------------------
# Queries, export, retention, GDPR
# ---------------------------------------------------------------------------


def query_events(
    db: Session,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    actor_id: uuid.UUID | None = None,
    action: str | None = None,
    collection_id: uuid.UUID | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[AuditEvent], int]:
    stmt = select(AuditEvent)
    if start:
        stmt = stmt.where(AuditEvent.ts >= start)
    if end:
        stmt = stmt.where(AuditEvent.ts < end)
    if actor_id:
        stmt = stmt.where(AuditEvent.actor_id == actor_id)
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action))
    if collection_id:
        stmt = stmt.where(AuditEvent.collection_id == collection_id)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(AuditEvent.id.desc()).limit(limit).offset(offset)).scalars().all()
    return list(rows), total


def serialize(event: AuditEvent) -> dict:
    return {
        "id": event.id,
        "ts": event.ts.isoformat() if event.ts else None,
        "actor_id": str(event.actor_id) if event.actor_id else None,
        "actor_email": event.actor_email,
        "action": event.action,
        "outcome": event.outcome,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "collection_id": str(event.collection_id) if event.collection_id else None,
        "details": event.details,
        "ip": event.ip,
        "hash": event.hash,
    }


CSV_COLUMNS = [
    "id",
    "ts",
    "actor_id",
    "actor_email",
    "action",
    "outcome",
    "resource_type",
    "resource_id",
    "collection_id",
    "ip",
    "details",
    "prev_hash",
    "hash",
]


def export_csv(events: Iterable[AuditEvent]) -> Iterable[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(CSV_COLUMNS)
    yield "﻿" + buffer.getvalue()  # BOM so Excel opens UTF-8 correctly
    for event in events:
        buffer.seek(0)
        buffer.truncate()
        writer.writerow(
            [
                event.id,
                event.ts.isoformat() if event.ts else "",
                event.actor_id or "",
                event.actor_email or "",
                event.action,
                event.outcome,
                event.resource_type or "",
                event.resource_id or "",
                event.collection_id or "",
                event.ip or "",
                json.dumps(event.details, ensure_ascii=False) if event.details else "",
                event.prev_hash,
                event.hash,
            ]
        )
        yield buffer.getvalue()


def redact_actor(db: Session, actor_id: uuid.UUID) -> int:
    """GDPR erasure: strip personal data of an actor without touching hashed fields."""
    result = db.execute(
        update(AuditEvent)
        .where(AuditEvent.actor_id == actor_id)
        .values(actor_email="[utilisateur supprimé]", ip=None, user_agent=None)
    )
    return result.rowcount or 0


def purge_older_than(db: Session, days: int) -> int:
    if days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=days)
    stmt = delete(AuditEvent).where(AuditEvent.ts < cutoff).execution_options(synchronize_session=False)
    return db.execute(stmt).rowcount or 0
