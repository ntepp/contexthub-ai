"""LLM consumption, read from the gateway (ADR-001). ContextHub stores no cost.

The browser never talks to the gateway: these routes query it with a service key and
filter by user here. ``/usage/me`` takes the user from the session, never from a
parameter.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.deps import current_user, require_admin
from src.db.models import User
from src.db.session import get_db
from src.usage.base import UsageError, UsageReport
from src.usage.registry import NO_ADMIN_KEY, build_usage_provider, usage_not_configured
from src.utils.logging_config import get_logger

log = get_logger()
router = APIRouter(tags=["usage"])

MAX_PERIOD_DAYS = 366
_EARLIEST = date(2020, 1, 1)
NOT_CONFIGURED = "Aucune passerelle LLM n'est configurée : la consommation n'est pas suivie."
NO_ADMIN_KEY_ME = "Le suivi de la consommation n'est pas encore configuré. Contactez votre administrateur."
NO_ADMIN_KEY_ADMIN = (
    "La lecture de la consommation n'est pas configurée : définissez LLM_GATEWAY_ADMIN_KEY "
    "(clé d'administrateur de la passerelle) puis redémarrez l'API."
)
UNKNOWN_USER = "Utilisateur inconnu ou supprimé"
EXTERNAL_ID = "Identifiant hors ContextHub"


def _period(start: date | None, end: date | None) -> tuple[date, date]:
    """[start, end], inclusive; default = current month (UTC) up to today."""
    today = datetime.now(UTC).date()
    end = end or today
    start = start or end.replace(day=1)
    if start > end:
        raise HTTPException(status_code=422, detail="La date de début doit précéder la date de fin.")
    if start < _EARLIEST or end > today + timedelta(days=1):
        raise HTTPException(status_code=422, detail="Période hors limites.")
    if (end - start).days + 1 > MAX_PERIOD_DAYS:
        raise HTTPException(status_code=422, detail=f"La période ne peut pas dépasser {MAX_PERIOD_DAYS} jours.")
    return start, end


def _report(report: UsageReport, kind: str) -> dict:
    return {
        "available": True,
        "gateway": kind,
        "period": {"from": report.start.isoformat(), "to": report.end.isoformat()},
        "currency": report.currency,
        "totals": report.totals.as_dict(),
        "by_model": [m.as_dict() for m in report.by_model],
        "daily": [d.as_dict() for d in report.daily],
        "budget": report.budget.as_dict() if report.budget else None,
        "missing": report.missing,
    }


def _unavailable(exc: UsageError) -> HTTPException:
    log.warning("[usage] lecture de la consommation impossible : %s", exc)
    return HTTPException(status_code=503, detail=str(exc))


@router.get("/usage/me")
async def my_usage(
    start: date | None = Query(default=None, alias="from"),
    end: date | None = Query(default=None, alias="to"),
    user: User = Depends(current_user),
) -> dict:
    start, end = _period(start, end)
    provider = build_usage_provider()
    if provider is None:
        missing_key = usage_not_configured() == NO_ADMIN_KEY
        return {"available": False, "reason": NO_ADMIN_KEY_ME if missing_key else NOT_CONFIGURED}
    try:
        report = await provider.user_usage(str(user.id), start, end)
    except UsageError as exc:
        raise _unavailable(exc)
    return _report(report, provider.kind)


def _people(db: Session, ids: list[str]) -> dict[str, User]:
    parsed: dict[uuid.UUID, str] = {}
    for raw in ids:
        try:
            parsed[uuid.UUID(raw)] = raw
        except ValueError:
            continue
    if not parsed:
        return {}
    users = db.execute(select(User).where(User.id.in_(list(parsed)))).scalars()
    return {parsed[u.id]: u for u in users}


@router.get("/admin/usage")
async def admin_usage(
    start: date | None = Query(default=None, alias="from"),
    end: date | None = Query(default=None, alias="to"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    start, end = _period(start, end)
    provider = build_usage_provider()
    if provider is None:
        missing_key = usage_not_configured() == NO_ADMIN_KEY
        return {"available": False, "reason": NO_ADMIN_KEY_ADMIN if missing_key else NOT_CONFIGURED}
    try:
        report = await provider.global_usage(start, end)
    except UsageError as exc:
        raise _unavailable(exc)
    people = _people(db, [u.user_id for u in report.by_user])
    by_user = []
    for usage in report.by_user:
        person = people.get(usage.user_id)
        if person is not None:
            entry = {
                "user_id": str(person.id),
                "known": True,
                "email": person.email,
                "full_name": person.full_name,
                "label": person.full_name or person.email,
            }
        else:
            # Kept, under a neutral label: deleted accounts, other applications on the gateway.
            is_uuid = True
            try:
                uuid.UUID(usage.user_id)
            except ValueError:
                is_uuid = False
            entry = {
                "user_id": usage.user_id if is_uuid else None,
                "known": False,
                "email": None,
                "full_name": None,
                "label": UNKNOWN_USER if is_uuid else EXTERNAL_ID,
            }
        by_user.append({**entry, **usage.totals.as_dict()})
    return {**_report(report, provider.kind), "by_user": by_user}
