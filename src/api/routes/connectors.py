"""Connectors: connections (admin) and pages attached to a conversation (users).

Nothing is synchronised in bulk. A user picks pages by link or by search; each page is
fetched once with the connection's account, indexed for that conversation only and
deleted with it.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.deps import actor_of, current_user, require_admin
from src.api.routes.collections import dispatch_ingestion
from src.audit import service as audit
from src.auth.rate_limit import limiter
from src.config import get_settings
from src.connectors import session_sources
from src.connectors.base import ConnectorError, SearchFilters
from src.connectors.crypto import encrypt_secret
from src.connectors.registry import KIND_LABELS, build_connector, validate_base_url
from src.db.models import CONNECTOR_CONFLUENCE, CONNECTOR_KINDS, Connection, Conversation, Document, User
from src.db.session import get_db
from src.utils.logging_config import get_logger

log = get_logger()
router = APIRouter(tags=["connectors"])

_KIND_PATTERN = f"^({'|'.join(CONNECTOR_KINDS)})$"
_MAX_REFERENCES = 10


class ConnectionCreate(BaseModel):
    kind: str = Field(pattern=_KIND_PATTERN)
    name: str = Field(min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=500)
    account: str | None = Field(default=None, max_length=320)
    secret: str = Field(min_length=1, max_length=4000)


class ConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    base_url: str | None = Field(default=None, max_length=500)
    account: str | None = Field(default=None, max_length=320)
    secret: str | None = Field(default=None, min_length=1, max_length=4000)
    is_enabled: bool | None = None


class AttachRequest(BaseModel):
    connection_id: uuid.UUID
    # Page links pasted by the user, or page ids returned by the search.
    references: list[str] = Field(min_length=1, max_length=_MAX_REFERENCES)
    conversation_id: uuid.UUID | None = None


def _enabled() -> None:
    if not get_settings().connectors_enabled:
        raise HTTPException(status_code=403, detail="Les connecteurs sont désactivés par l'administrateur.")


def serialize_connection(c: Connection, admin: bool = False) -> dict:
    data = {"id": str(c.id), "kind": c.kind, "kind_label": KIND_LABELS.get(c.kind, c.kind), "name": c.name}
    if admin:
        # The secret is write-only: it is never sent back.
        data.update(
            base_url=c.base_url,
            account=c.account,
            is_enabled=c.is_enabled,
            created_at=c.created_at.isoformat() if c.created_at else None,
            updated_at=c.updated_at.isoformat() if c.updated_at else None,
        )
    return data


def serialize_source(doc: Document, kinds: dict[uuid.UUID, str]) -> dict:
    kind = kinds.get(doc.connection_id) if doc.connection_id else None
    return {
        "id": str(doc.id),
        "title": doc.filename,
        "url": doc.source_url,
        "kind": kind,
        "kind_label": KIND_LABELS.get(kind or "", "Source externe"),
        "connection_id": str(doc.connection_id) if doc.connection_id else None,
        "status": doc.status,
        "error_message": doc.error_message,
        "warnings": doc.warnings or [],
    }


def _kinds(db: Session) -> dict[uuid.UUID, str]:
    return {cid: kind for cid, kind in db.execute(select(Connection.id, Connection.kind)).all()}


def serialize_sources(db: Session, conversation_id: uuid.UUID) -> list[dict]:
    kinds = _kinds(db)
    return [serialize_source(d, kinds) for d in session_sources.list_sources(db, conversation_id)]


# ---------------------------------------------------------------------------
# Administration
# ---------------------------------------------------------------------------


def _connection(db: Session, connection_id: uuid.UUID) -> Connection:
    connection = db.get(Connection, connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Connexion introuvable.")
    return connection


def _name_taken(db: Session, name: str, excluding: uuid.UUID | None = None) -> bool:
    found = db.execute(select(Connection).where(Connection.name == name)).scalar_one_or_none()
    return found is not None and found.id != excluding


@router.get("/admin/connections")
def list_connections(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(select(Connection).order_by(Connection.name)).scalars()
    return [serialize_connection(c, admin=True) for c in rows]


@router.post("/admin/connections", status_code=201)
def create_connection(
    body: ConnectionCreate, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    name = body.name.strip()
    if _name_taken(db, name):
        raise HTTPException(status_code=409, detail=f"La connexion « {name} » existe déjà.")
    try:
        base_url = validate_base_url(body.kind, body.base_url)
    except ConnectorError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    connection = Connection(
        kind=body.kind,
        name=name,
        base_url=base_url,
        account=(body.account or "").strip() or None if body.kind == CONNECTOR_CONFLUENCE else None,
        secret_encrypted=encrypt_secret(body.secret.strip()),
        created_by=admin.id,
    )
    db.add(connection)
    db.commit()
    audit.record(
        "connection.create",
        actor_of(request, admin),
        resource_type="connection",
        resource_id=connection.id,
        details={"kind": connection.kind, "name": name, "base_url": base_url},
    )
    return serialize_connection(connection, admin=True)


@router.patch("/admin/connections/{connection_id}")
def update_connection(
    connection_id: uuid.UUID,
    body: ConnectionUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    connection = _connection(db, connection_id)
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes:
        name = changes["name"].strip()
        if _name_taken(db, name, excluding=connection.id):
            raise HTTPException(status_code=409, detail=f"La connexion « {name} » existe déjà.")
        connection.name = name
    if "base_url" in changes:
        try:
            connection.base_url = validate_base_url(connection.kind, changes["base_url"])
        except ConnectorError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if "account" in changes and connection.kind == CONNECTOR_CONFLUENCE:
        connection.account = (changes["account"] or "").strip() or None
    if changes.get("secret"):
        connection.secret_encrypted = encrypt_secret(changes["secret"].strip())
    if changes.get("is_enabled") is not None:
        connection.is_enabled = changes["is_enabled"]
    db.commit()
    audit.record(
        "connection.update",
        actor_of(request, admin),
        resource_type="connection",
        resource_id=connection.id,
        # Never the secret itself: only the fact that it changed.
        details={"changed": sorted(changes), "is_enabled": connection.is_enabled},
    )
    return serialize_connection(connection, admin=True)


@router.delete("/admin/connections/{connection_id}")
def delete_connection(
    connection_id: uuid.UUID, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    connection = _connection(db, connection_id)
    details = {"kind": connection.kind, "name": connection.name}
    db.delete(connection)
    db.commit()
    audit.record(
        "connection.delete", actor_of(request, admin), resource_type="connection", resource_id=connection_id, details=details
    )
    return {"ok": True}


@router.post("/admin/connections/{connection_id}/test")
def test_connection(
    connection_id: uuid.UUID, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    connection = _connection(db, connection_id)
    try:
        message = build_connector(connection).test()
        ok = True
    except ConnectorError as exc:
        message, ok = str(exc), False
    audit.record(
        "connection.test",
        actor_of(request, admin),
        outcome="success" if ok else "failure",
        resource_type="connection",
        resource_id=connection.id,
        details={"name": connection.name},
    )
    return {"ok": ok, "message": message}


# ---------------------------------------------------------------------------
# Users: browse a connection, attach pages to a conversation
# ---------------------------------------------------------------------------


def _usable_connection(db: Session, connection_id: uuid.UUID) -> Connection:
    connection = db.get(Connection, connection_id)
    if connection is None or not connection.is_enabled:
        raise HTTPException(status_code=404, detail="Connexion introuvable.")
    return connection


def _own_conversation(db: Session, user: User, conversation_id: uuid.UUID) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation introuvable.")
    return conversation


def _throttle(user: User) -> None:
    if not limiter.hit(f"rl:connector:{user.id}", get_settings().ask_rate_limit_per_minute, 60):
        raise HTTPException(status_code=429, detail="Trop de requêtes vers les connecteurs, patientez un instant.")


@router.get("/connections")
def available_connections(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    if not get_settings().connectors_enabled:
        return []
    rows = db.execute(select(Connection).where(Connection.is_enabled.is_(True)).order_by(Connection.name)).scalars()
    return [serialize_connection(c) for c in rows]


@router.get("/connections/{connection_id}/search")
def search_connection(
    connection_id: uuid.UUID,
    request: Request,
    q: str = Query(default="", max_length=200),
    space: str = Query(default="", max_length=255),
    label: str = Query(default="", max_length=255),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    _enabled()
    _throttle(user)
    connection = _usable_connection(db, connection_id)
    try:
        pages = build_connector(connection).search(SearchFilters(query=q, space=space.strip(), label=label.strip()))
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    audit.record(
        "connection.search",
        actor_of(request, user),
        resource_type="connection",
        resource_id=connection.id,
        details={"name": connection.name, "query": q, "space": space, "label": label, "results": len(pages)},
    )
    return {"pages": [p.as_dict() for p in pages]}


@router.get("/conversations/{conversation_id}/sources")
def conversation_sources(
    conversation_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[dict]:
    _own_conversation(db, user, conversation_id)
    return serialize_sources(db, conversation_id)


@router.post("/chat/sources")
def attach_sources(
    body: AttachRequest, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    """Fetch the given pages and attach them to a conversation (created if needed)."""
    _enabled()
    _throttle(user)
    connection = _usable_connection(db, body.connection_id)
    conversation = _own_conversation(db, user, body.conversation_id) if body.conversation_id else None

    try:
        connector = build_connector(connection)
    except ConnectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    references = list(dict.fromkeys(r.strip() for r in body.references if r.strip()))
    attached: list[Document] = []
    errors: list[dict] = []
    created = False
    for reference in references:
        try:
            page = connector.resolve(reference)
            content = connector.fetch(page.external_id)
            if conversation is None:
                # Committed at once: a later failure rolls back the page, not the conversation.
                conversation = Conversation(user_id=user.id, collection_ids=[])
                db.add(conversation)
                db.commit()
                created = True
            attached.append(session_sources.attach_page(db, user, conversation, connection, content))
        except ConnectorError as exc:
            db.rollback()
            errors.append({"reference": reference[:300], "message": str(exc)})
        except Exception:
            db.rollback()
            log.exception("[connectors] échec inattendu en joignant une page")
            errors.append({"reference": reference[:300], "message": "Erreur interne en joignant cette page."})

    if conversation is None or not attached:
        # Nothing could be attached: report why, without leaving an empty conversation.
        if created:
            session_sources.drop_for_conversations(db, [conversation.id])
            db.delete(conversation)
            db.commit()
        detail = errors[0]["message"] if errors else "Aucune page à joindre."
        raise HTTPException(status_code=422, detail=detail)

    conversation_id = conversation.id
    for doc in attached:
        audit.record(
            "source.attach",
            actor_of(request, user),
            resource_type="document",
            resource_id=doc.id,
            details={
                "conversation_id": str(conversation_id),
                "connection": connection.name,
                "kind": connection.kind,
                "title": doc.filename,
                "url": doc.source_url,
                "external_id": doc.external_id,
                "sha256": doc.sha256,
            },
        )
        try:
            dispatch_ingestion(db, doc)
        except HTTPException as exc:
            errors.append({"reference": doc.filename, "message": str(exc.detail)})
    # Indexing ran in other sessions (worker, or eagerly in tests): reload the statuses.
    db.expire_all()
    return {
        "conversation_id": str(conversation_id),
        "sources": serialize_sources(db, conversation_id),
        "errors": errors,
    }


@router.delete("/conversations/{conversation_id}/sources/{document_id}")
def detach_source(
    conversation_id: uuid.UUID,
    document_id: uuid.UUID,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    _own_conversation(db, user, conversation_id)
    removed = session_sources.detach(db, conversation_id, document_id)
    if removed is None:
        raise HTTPException(status_code=404, detail="Page jointe introuvable.")
    audit.record(
        "source.detach",
        actor_of(request, user),
        resource_type="document",
        resource_id=document_id,
        details={"conversation_id": str(conversation_id), **removed},
    )
    return {"ok": True}
