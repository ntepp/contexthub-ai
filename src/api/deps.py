"""Authentication and authorisation dependencies.

A request is authenticated either by the ``ch_session`` cookie (browser) or by an
``Authorization: Bearer chk_...`` API key (integrations). Cookie-authenticated
state-changing requests must carry ``X-Requested-With`` — a header a cross-site form
cannot set, and that a cross-origin script cannot send without a CORS preflight the
API refuses. Together with ``SameSite=Strict`` this closes CSRF.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.audit.service import Actor
from src.auth.security import API_KEY_PREFIX, SESSION_COOKIE, decode_session_token, hash_api_key
from src.db.models import (
    ACCESS_EDITOR,
    ACCESS_VIEWER,
    ApiKey,
    Collection,
    CollectionGrant,
    CollectionGroupGrant,
    User,
    utcnow,
)
from src.db.session import get_db

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "x-requested-with"


@dataclass
class Principal:
    user: User
    via_api_key: bool = False

    @property
    def id(self) -> uuid.UUID:
        return self.user.id


def client_ip(request: Request) -> str | None:
    # X-Forwarded-For is deliberately not read here: uvicorn runs with
    # --proxy-headers --forwarded-allow-ips=<proxy>, so request.client is already the
    # real client when (and only when) the request came through the trusted proxy.
    return request.client.host if request.client else None


def actor_of(request: Request, user: User | None) -> Actor:
    return Actor(
        id=user.id if user else None,
        email=user.email if user else None,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


def _unauthorized(detail: str = "Authentification requise.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _from_api_key(db: Session, raw: str) -> User | None:
    key = db.execute(select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw))).scalar_one_or_none()
    if key is None or key.revoked_at is not None:
        return None
    key.last_used_at = utcnow()
    db.commit()
    return db.get(User, key.user_id)


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        raw = auth_header[7:].strip()
        if not raw.startswith(API_KEY_PREFIX):
            raise _unauthorized("Clé d'API invalide.")
        user = _from_api_key(db, raw)
        if user is None or not user.is_active:
            raise _unauthorized("Clé d'API invalide ou révoquée.")
        return Principal(user=user, via_api_key=True)

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise _unauthorized()
    payload = decode_session_token(token)
    if payload is None:
        raise _unauthorized("Session expirée.")
    try:
        user = db.get(User, uuid.UUID(payload["sub"]))
    except (KeyError, ValueError):
        raise _unauthorized("Session invalide.")
    if user is None or not user.is_active or user.session_version != payload.get("sv"):
        raise _unauthorized("Session révoquée.")
    if request.method not in _SAFE_METHODS and not request.headers.get(CSRF_HEADER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="En-tête X-Requested-With manquant.")
    request.state.user = user
    return Principal(user=user)


def current_user(principal: Principal = Depends(get_principal)) -> User:
    return principal.user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Réservé aux administrateurs.")
    return user


# ---------------------------------------------------------------------------
# Collection access
# ---------------------------------------------------------------------------

_LEVEL_RANK = {None: 0, ACCESS_VIEWER: 1, ACCESS_EDITOR: 2, "admin": 3}


def _best(*levels: str | None) -> str | None:
    return max(levels, key=lambda level: _LEVEL_RANK[level])


def group_levels(db: Session, user: User, collection_id: uuid.UUID | None = None) -> dict[uuid.UUID, str]:
    """Access the user's SSO groups give, per collection (best level when several match)."""
    groups = [g for g in (user.oidc_groups or []) if isinstance(g, str)]
    if not groups:
        return {}
    stmt = select(CollectionGroupGrant).where(CollectionGroupGrant.group_name.in_(groups))
    if collection_id is not None:
        stmt = stmt.where(CollectionGroupGrant.collection_id == collection_id)
    levels: dict[uuid.UUID, str] = {}
    for grant in db.execute(stmt).scalars():
        levels[grant.collection_id] = _best(levels.get(grant.collection_id), grant.level)
    return levels


def access_level(db: Session, user: User, collection: Collection) -> str | None:
    # Session collections (pages attached to a conversation) are private to that
    # conversation, administrators included: they are reached through it only.
    if collection.conversation_id is not None:
        return None
    if user.is_admin:
        return "admin"
    grant = db.execute(
        select(CollectionGrant.level).where(
            CollectionGrant.collection_id == collection.id, CollectionGrant.user_id == user.id
        )
    ).scalar_one_or_none()
    from_groups = group_levels(db, user, collection.id).get(collection.id)
    return _best(grant, from_groups, ACCESS_VIEWER if collection.is_open else None)


def accessible_collections(db: Session, user: User) -> list[tuple[Collection, str]]:
    if user.is_admin:
        rows = db.execute(
            select(Collection).where(Collection.conversation_id.is_(None)).order_by(Collection.name)
        ).scalars()
        return [(c, "admin") for c in rows]
    grants = group_levels(db, user)
    for g in db.execute(select(CollectionGrant).where(CollectionGrant.user_id == user.id)).scalars():
        grants[g.collection_id] = _best(grants.get(g.collection_id), g.level)
    rows = db.execute(
        select(Collection)
        .where(
            Collection.conversation_id.is_(None),
            or_(Collection.is_open.is_(True), Collection.id.in_(list(grants) or [uuid.uuid4()])),
        )
        .order_by(Collection.name)
    ).scalars()
    return [(c, grants.get(c.id, ACCESS_VIEWER)) for c in rows]


def load_collection(db: Session, user: User, collection_id: uuid.UUID, minimum: str = ACCESS_VIEWER) -> Collection:
    """Fetch a collection the user may access at ``minimum`` level, else 404/403.

    Collections the user cannot see at all answer 404, so their existence is not leaked.
    """
    collection = db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection introuvable.")
    level = access_level(db, user, collection)
    if level is None:
        raise HTTPException(status_code=404, detail="Collection introuvable.")
    if _LEVEL_RANK[level] < _LEVEL_RANK[minimum]:
        raise HTTPException(status_code=403, detail="Droits insuffisants sur cette collection.")
    return collection
