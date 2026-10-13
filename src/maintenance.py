"""Retention policies, run daily by Celery beat and on demand from the CLI."""

from __future__ import annotations

import time
from datetime import timedelta

from sqlalchemy import delete

from src.audit import service as audit
from src.config import get_settings
from src.db.models import Conversation, utcnow
from src.db.session import session_scope
from src.ingestion.storage import upload_root
from src.utils.logging_config import get_logger

log = get_logger()

_STAGING_MAX_AGE_SECONDS = 24 * 3600


def purge_expired_data() -> dict:
    settings = get_settings()
    result = {"audit_events": 0, "conversations": 0, "staging_files": 0}

    with session_scope() as db:
        result["audit_events"] = audit.purge_older_than(db, settings.audit_retention_days)
        if settings.conversation_retention_days > 0:
            cutoff = utcnow() - timedelta(days=settings.conversation_retention_days)
            result["conversations"] = (
                db.execute(
                    delete(Conversation)
                    .where(Conversation.updated_at < cutoff)
                    .execution_options(synchronize_session=False)
                ).rowcount
                or 0
            )

    # Interrupted uploads leave ".staging-*" files behind.
    now = time.time()
    for path in upload_root().glob(".staging-*"):
        if now - path.stat().st_mtime > _STAGING_MAX_AGE_SECONDS:
            path.unlink(missing_ok=True)
            result["staging_files"] += 1

    if any(result.values()):
        audit.record("retention.purge", details=result)
    log.info("[retention] %s", result)
    return result
