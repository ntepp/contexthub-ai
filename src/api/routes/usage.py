"""LLM consumption, read from the gateway (ADR-001), and per-user budgets driven on it
(ADR-002). ContextHub stores no cost and no budget.

The browser never talks to the gateway: these routes query it with a service key and
filter by user here. ``/usage/me`` takes the user from the session, never from a
parameter.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.deps import actor_of, current_user, require_admin
from src.audit import service as audit
from src.db.models import User
from src.db.session import get_db
from src.usage.base import Budget, BudgetConflict, BudgetPeriod, UsageError, UsageReport
from src.usage.registry import (
    NO_ADMIN_KEY,
    UNSUPPORTED_KIND,
    budgets_not_configured,
    build_budget_manager,
    build_usage_provider,
    usage_not_configured,
)
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
NO_ADMIN_KEY_BUDGETS = (
    "La gestion des budgets n'est pas configurée : définissez LLM_GATEWAY_ADMIN_KEY "
    "(clé d'administrateur de la passerelle) puis redémarrez l'API."
)
NO_GATEWAY_BUDGETS = "Aucune passerelle LLM n'est configurée : les budgets ne peuvent pas être gérés."
UNSUPPORTED_KIND_ADMIN = (
    "Type de passerelle LLM non pris en charge (LLM_GATEWAY_KIND) : la consommation et les budgets "
    "ne peuvent pas être lus. Valeur acceptée : litellm."
)
UNKNOWN_USER = "Utilisateur inconnu ou supprimé"
EXTERNAL_ID = "Identifiant hors ContextHub"
# /admin/usage reads the budgets of at most this many users (the biggest consumers first).
MAX_BUDGET_LOOKUPS = 200
MAX_BUDGET_LIMIT = 1_000_000.0


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
        why = usage_not_configured()
        # Members get no configuration detail: an unsupported kind reads as "not configured yet".
        reason = NO_ADMIN_KEY_ME if why in (NO_ADMIN_KEY, UNSUPPORTED_KIND) else NOT_CONFIGURED
        return {"available": False, "reason": reason}
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
        why = usage_not_configured()
        reasons = {NO_ADMIN_KEY: NO_ADMIN_KEY_ADMIN, UNSUPPORTED_KIND: UNSUPPORTED_KIND_ADMIN}
        return {"available": False, "reason": reasons.get(why, NOT_CONFIGURED)}
    try:
        report = await provider.global_usage(start, end)
    except UsageError as exc:
        raise _unavailable(exc)
    people = _people(db, [u.user_id for u in report.by_user])
    budgets: dict[str, Budget | None] = {}
    missing = list(report.missing)
    manager = build_budget_manager()
    if manager is not None and people:
        wanted = [u.user_id for u in report.by_user if u.user_id in people]
        batch = await manager.get_budgets(wanted[:MAX_BUDGET_LOOKUPS])
        budgets = batch.budgets
        if batch.failed or len(wanted) > MAX_BUDGET_LOOKUPS:
            missing.append("budgets")
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
        budget = budgets.get(usage.user_id) if person is not None else None
        by_user.append({**entry, **usage.totals.as_dict(), "budget": budget.as_dict() if budget else None})
    return {
        **_report(report, provider.kind),
        "missing": missing,
        "by_user": by_user,
        "budgets_available": manager is not None,
    }


# ---------------------------------------------------------------------------
# Per-user budgets (ADR-002): held and enforced by the gateway
# ---------------------------------------------------------------------------


class BudgetSet(BaseModel):
    limit: float
    period: str


def _target(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    return user


def _budget_reason() -> str:
    reasons = {NO_ADMIN_KEY: NO_ADMIN_KEY_BUDGETS, UNSUPPORTED_KIND: UNSUPPORTED_KIND_ADMIN}
    return reasons.get(budgets_not_configured(), NO_GATEWAY_BUDGETS)


def _audited(budget: Budget | None) -> dict | None:
    """Old and new values of budget events, in one shape; the limit and its window only,
    never the spend."""
    if budget is None:
        return None
    return {
        "limit": budget.limit,
        "period_kind": budget.period_kind.value if budget.period_kind else None,
        "period": budget.period,
        "managed": budget.managed,
    }


def _budget_response(user: User, budget: Budget | None, currency: str) -> dict:
    return {
        "available": True,
        "user_id": str(user.id),
        "currency": currency,
        "budget": budget.as_dict() if budget else None,
    }


@router.get("/admin/users/{user_id}/budget")
async def get_user_budget(user_id: uuid.UUID, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    user = _target(db, user_id)
    manager = build_budget_manager()
    if manager is None:
        return {"available": False, "reason": _budget_reason()}
    try:
        return _budget_response(user, await manager.get_budget(str(user.id)), manager.currency)
    except UsageError as exc:
        raise _unavailable(exc)


@router.put("/admin/users/{user_id}/budget")
async def set_user_budget(
    user_id: uuid.UUID,
    body: BudgetSet,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    user = _target(db, user_id)
    if not math.isfinite(body.limit) or body.limit <= 0:
        raise HTTPException(status_code=422, detail="Le montant du budget doit être un nombre strictement positif.")
    if body.limit > MAX_BUDGET_LIMIT:
        raise HTTPException(status_code=422, detail="Le montant du budget ne peut pas dépasser 1 000 000.")
    try:
        period = BudgetPeriod(body.period)
    except ValueError:
        raise HTTPException(status_code=422, detail="Période inconnue : choisissez day (jour), week (semaine) ou month (30 jours).")
    manager = build_budget_manager()
    if manager is None:
        raise HTTPException(status_code=503, detail=_budget_reason())
    limit = round(body.limit, 6)
    actor = actor_of(request, admin)
    try:
        old = await manager.get_budget(str(user.id))
        budget = await manager.set_budget(str(user.id), limit, period)
    except UsageError as exc:
        # What was asked, in the same shape (the gateway window is unknown: nothing was written).
        asked = {"limit": limit, "period_kind": period.value, "period": None, "managed": True}
        audit.record("budget.set", actor, outcome="failure", resource_type="user", resource_id=user.id, details={"new": asked})
        raise _unavailable(exc)
    audit.record(
        "budget.set", actor, resource_type="user", resource_id=user.id, details={"old": _audited(old), "new": _audited(budget)}
    )
    return _budget_response(user, budget, manager.currency)


@router.delete("/admin/users/{user_id}/budget")
async def remove_user_budget(
    user_id: uuid.UUID, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    user = _target(db, user_id)
    manager = build_budget_manager()
    if manager is None:
        raise HTTPException(status_code=503, detail=_budget_reason())
    actor = actor_of(request, admin)
    try:
        old = await manager.get_budget(str(user.id))
        await manager.remove_budget(str(user.id))
        # Read back rather than assumed: shows what the gateway really kept.
        budget = await manager.get_budget(str(user.id))
    except BudgetConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except UsageError as exc:
        audit.record("budget.remove", actor, outcome="failure", resource_type="user", resource_id=user.id)
        raise _unavailable(exc)
    audit.record(
        "budget.remove", actor, resource_type="user", resource_id=user.id, details={"old": _audited(old), "new": _audited(budget)}
    )
    return _budget_response(user, budget, manager.currency)
