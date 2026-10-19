from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import httpx
import redis
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.deps import actor_of, require_admin
from src.api.routes.auth import serialize_user
from src.audit import service as audit
from src.auth.security import generate_password, hash_password, validate_password_strength
from src.auth.users import create_local_user, find_by_email
from src.config import get_settings
from src.db.models import (
    ACCESS_EDITOR,
    ACCESS_VIEWER,
    ROLE_ADMIN,
    ROLE_MEMBER,
    Collection,
    CollectionGrant,
    Conversation,
    Document,
    Message,
    User,
)
from src.db.session import get_db
from src.ingestion.loaders import ocr_available
from src.llm.factory import model_catalogue

router = APIRouter(prefix="/admin", tags=["admin"])


class UserCreate(BaseModel):
    email: str = Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    full_name: str | None = Field(default=None, max_length=255)
    role: str = Field(default=ROLE_MEMBER, pattern=f"^({ROLE_ADMIN}|{ROLE_MEMBER})$")
    password: str | None = Field(default=None, max_length=1024)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, pattern=f"^({ROLE_ADMIN}|{ROLE_MEMBER})$")
    is_active: bool | None = None


class GrantSet(BaseModel):
    user_id: uuid.UUID
    level: str = Field(pattern=f"^({ACCESS_VIEWER}|{ACCESS_EDITOR})$")


def _admins_left(db: Session, excluding: uuid.UUID) -> int:
    return db.execute(
        select(func.count()).select_from(User).where(User.role == ROLE_ADMIN, User.is_active.is_(True), User.id != excluding)
    ).scalar_one()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@router.get("/users")
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    return [serialize_user(u) for u in db.execute(select(User).order_by(User.email)).scalars()]


@router.post("/users", status_code=201)
def create_user(body: UserCreate, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if find_by_email(db, body.email):
        raise HTTPException(status_code=409, detail="Un compte existe déjà avec cette adresse.")
    password = body.password or generate_password()
    try:
        user = create_local_user(db, body.email, password, full_name=body.full_name, role=body.role)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc))
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Un compte existe déjà avec cette adresse.")
    audit.record(
        "user.create",
        actor_of(request, admin),
        resource_type="user",
        resource_id=user.id,
        details={"email": user.email, "role": user.role},
    )
    data = serialize_user(user)
    if not body.password:
        data["temporary_password"] = password  # shown once, never stored in clear
    return data


@router.patch("/users/{user_id}")
def update_user(
    user_id: uuid.UUID, body: UserUpdate, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    changes = body.model_dump(exclude_unset=True)
    demoting = changes.get("role") == ROLE_MEMBER or changes.get("is_active") is False
    if demoting and user.role == ROLE_ADMIN and _admins_left(db, user.id) == 0:
        raise HTTPException(status_code=409, detail="Impossible : ce compte est le dernier administrateur actif.")
    for key, value in changes.items():
        setattr(user, key, value)
    if changes.get("is_active") is False or "role" in changes:
        user.session_version += 1
    db.commit()
    audit.record("user.update", actor_of(request, admin), resource_type="user", resource_id=user.id, details=changes)
    return serialize_user(user)


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: uuid.UUID, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    password = generate_password()
    assert validate_password_strength(password) is None
    user.password_hash = hash_password(password)
    user.session_version += 1
    db.commit()
    audit.record("user.reset_password", actor_of(request, admin), resource_type="user", resource_id=user.id)
    return {"temporary_password": password}


@router.delete("/users/{user_id}")
def delete_user(user_id: uuid.UUID, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """GDPR erasure: removes the account, its conversations, grants and API keys, and
    redacts its personal data from the audit trail (the chain stays verifiable)."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    if user.id == admin.id:
        raise HTTPException(status_code=409, detail="Vous ne pouvez pas supprimer votre propre compte.")
    if user.role == ROLE_ADMIN and _admins_left(db, user.id) == 0:
        raise HTTPException(status_code=409, detail="Impossible : ce compte est le dernier administrateur actif.")
    conversations = db.execute(select(func.count()).where(Conversation.user_id == user.id)).scalar_one()
    db.delete(user)
    redacted = audit.redact_actor(db, user_id)
    db.commit()
    audit.record(
        "user.delete",
        actor_of(request, admin),
        resource_type="user",
        resource_id=user_id,
        details={"conversations_deleted": conversations, "audit_events_redacted": redacted},
    )
    return {"ok": True, "conversations_deleted": conversations, "audit_events_redacted": redacted}


# ---------------------------------------------------------------------------
# Collection grants
# ---------------------------------------------------------------------------


def _collection(db: Session, collection_id: uuid.UUID) -> Collection:
    collection = db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection introuvable.")
    return collection


@router.get("/collections/{collection_id}/grants")
def list_grants(collection_id: uuid.UUID, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    collection = _collection(db, collection_id)
    rows = db.execute(
        select(CollectionGrant, User).join(User, User.id == CollectionGrant.user_id).where(
            CollectionGrant.collection_id == collection.id
        ).order_by(User.email)
    ).all()
    return [
        {"user_id": str(u.id), "email": u.email, "full_name": u.full_name, "level": g.level} for g, u in rows
    ]


@router.put("/collections/{collection_id}/grants")
def set_grant(
    collection_id: uuid.UUID, body: GrantSet, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    collection = _collection(db, collection_id)
    if db.get(User, body.user_id) is None:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable.")
    grant = db.execute(
        select(CollectionGrant).where(CollectionGrant.collection_id == collection.id, CollectionGrant.user_id == body.user_id)
    ).scalar_one_or_none()
    if grant is None:
        grant = CollectionGrant(collection_id=collection.id, user_id=body.user_id, level=body.level)
        db.add(grant)
    else:
        grant.level = body.level
    db.commit()
    audit.record(
        "grant.set",
        actor_of(request, admin),
        resource_type="user",
        resource_id=body.user_id,
        collection_id=collection.id,
        details={"level": body.level},
    )
    return {"ok": True}


@router.delete("/collections/{collection_id}/grants/{user_id}")
def revoke_grant(
    collection_id: uuid.UUID, user_id: uuid.UUID, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    grant = db.execute(
        select(CollectionGrant).where(CollectionGrant.collection_id == collection_id, CollectionGrant.user_id == user_id)
    ).scalar_one_or_none()
    if grant is None:
        raise HTTPException(status_code=404, detail="Droit introuvable.")
    db.delete(grant)
    db.commit()
    audit.record("grant.revoke", actor_of(request, admin), resource_type="user", resource_id=user_id, collection_id=collection_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@router.get("/audit")
def audit_events(
    start: datetime | None = None,
    end: datetime | None = None,
    actor_id: uuid.UUID | None = None,
    action: str | None = Query(default=None, max_length=64),
    collection_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    rows, total = audit.query_events(
        db, start=start, end=end, actor_id=actor_id, action=action, collection_id=collection_id, limit=limit, offset=offset
    )
    return {"total": total, "items": [audit.serialize(e) for e in rows]}


@router.get("/audit/export.csv")
def audit_export(
    request: Request,
    start: datetime | None = None,
    end: datetime | None = None,
    actor_id: uuid.UUID | None = None,
    action: str | None = Query(default=None, max_length=64),
    collection_id: uuid.UUID | None = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows, total = audit.query_events(
        db, start=start, end=end, actor_id=actor_id, action=action, collection_id=collection_id, limit=1_000_000
    )
    rows.reverse()  # chronological order in the file
    audit.record(
        "audit.export",
        actor_of(request, admin),
        details={"rows": total, "start": str(start) if start else None, "end": str(end) if end else None, "action": action},
    )
    return StreamingResponse(
        audit.export_csv(rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="contexthub-audit.csv"'},
    )


@router.get("/audit/verify")
def audit_verify(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    report = audit.verify_chain(db)
    audit.record("audit.verify", actor_of(request, admin), outcome="success" if report.valid else "failure", details=report.as_dict())
    return report.as_dict()


# ---------------------------------------------------------------------------
# System status
# ---------------------------------------------------------------------------


def _check_db(db: Session) -> dict:
    try:
        db.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def _check_redis() -> dict:
    try:
        redis.Redis.from_url(get_settings().redis_db_url(0), socket_connect_timeout=1, socket_timeout=1).ping()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def _check_workers() -> dict:
    try:
        from src.workers.celery_app import celery_app

        replies = celery_app.control.inspect(timeout=1.0).ping() or {}
        return {"ok": bool(replies), "workers": sorted(replies)}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def _check_ollama() -> dict:
    try:
        resp = httpx.get(f"{get_settings().ollama_base_url}/api/tags", timeout=2)
        resp.raise_for_status()
        return {"ok": True, "models": sorted(m["name"] for m in resp.json().get("models", []))}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


@router.get("/system")
def system_status(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    counts = {
        "users": db.execute(select(func.count()).select_from(User)).scalar_one(),
        "collections": db.execute(select(func.count()).select_from(Collection)).scalar_one(),
        "documents": db.execute(select(func.count()).select_from(Document)).scalar_one(),
        "messages": db.execute(select(func.count()).select_from(Message)).scalar_one(),
    }
    uses_ollama = settings.embedding_provider == "ollama" or any(m.provider.value == "ollama" for m in model_catalogue())
    probes = {"redis": _check_redis, "workers": _check_workers, "ocr": ocr_available}
    if uses_ollama:
        probes["ollama"] = _check_ollama
    # Each probe may wait on a timeout: run them side by side, not one after another.
    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        futures = {name: pool.submit(fn) for name, fn in probes.items()}
        results = {name: future.result() for name, future in futures.items()}
    ocr_ok = results.pop("ocr")
    return {
        "version": settings.app_version,
        "environment": settings.environment,
        "checks": {"database": _check_db(db), **results},
        "counts": counts,
        "configuration": {
            "models": [m.as_dict() for m in model_catalogue()],
            "embedding": {"provider": settings.embedding_provider, "model": settings.embedding_model},
            "retrieval": {
                "max_distance": settings.retrieval_max_distance,
                "top_k": settings.retrieval_top_k,
                "candidates": settings.retrieval_candidates,
                "hybrid": settings.retrieval_hybrid,
                "reranker": settings.reranker,
            },
            "ocr": {"enabled": settings.ocr_enabled, "available": ocr_ok},
            "url_ingestion": settings.allow_url_ingestion,
            "sso": settings.oidc_enabled,
            "retention_days": {
                "audit": settings.audit_retention_days,
                "conversations": settings.conversation_retention_days,
            },
            "external_tracing": settings.langchain_tracing_v2,
            "upload_max_mb": settings.upload_max_mb,
        },
    }
