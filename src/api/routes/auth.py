from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.deps import Principal, accessible_collections, actor_of, client_ip, current_user, get_principal
from src.audit import service as audit
from src.auth.oidc import OIDCError, oidc_client
from src.auth.rate_limit import limiter
from src.auth.security import (
    OIDC_STATE_COOKIE,
    SESSION_COOKIE,
    create_session_token,
    decode_session_token,
    generate_api_key,
    hash_password,
    sign_payload,
    unsign_payload,
    validate_password_strength,
    verify_password,
)
from src.auth.users import find_by_email, normalise_email, upsert_oidc_user
from src.config import get_settings
from src.db.models import ApiKey, User, utcnow
from src.db.session import get_db
from src.utils.logging_config import get_logger

log = get_logger()
router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


def serialize_user(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "is_active": user.is_active,
        "auth_provider": user.auth_provider,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def _set_session(response: Response, user: User) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user.id, user.session_version),
        max_age=settings.session_ttl_minutes * 60,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
        path="/",
    )


@router.get("/config")
def auth_config() -> dict:
    settings = get_settings()
    return {
        "local_enabled": settings.local_login_enabled,
        "oidc_enabled": settings.oidc_enabled,
        "oidc_label": settings.oidc_label,
        "app_name": settings.app_name,
        "version": settings.app_version,
        "source_code_url": settings.source_code_url or None,
    }


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    if not settings.local_login_enabled:
        raise HTTPException(status_code=403, detail="La connexion par mot de passe est désactivée (SSO uniquement).")
    email = normalise_email(body.email)
    key = f"rl:login:{client_ip(request)}:{email}"
    if not limiter.hit(key, settings.login_max_attempts, settings.login_window_seconds):
        audit.record("auth.login", actor_of(request, None), outcome="denied", details={"email": email, "reason": "rate_limited"})
        raise HTTPException(status_code=429, detail="Trop de tentatives. Réessayez dans quelques minutes.")

    user = find_by_email(db, email)
    if user is None or not verify_password(body.password, user.password_hash) or not user.is_active:
        audit.record(
            "auth.login",
            actor_of(request, user),
            outcome="failure",
            details={"email": email, "reason": "inactive" if user and not user.is_active else "bad_credentials"},
        )
        raise HTTPException(status_code=401, detail="Identifiants invalides.")

    limiter.reset(key)
    user.last_login_at = utcnow()
    db.commit()
    _set_session(response, user)
    audit.record("auth.login", actor_of(request, user), details={"method": "password"})
    return serialize_user(user)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    payload = decode_session_token(request.cookies.get(SESSION_COOKIE, ""))
    user = db.get(User, uuid.UUID(payload["sub"])) if payload else None
    if user is not None:
        audit.record("auth.logout", actor_of(request, user))
    return {"ok": True}


@router.get("/me")
def me(principal: Principal = Depends(get_principal), db: Session = Depends(get_db)) -> dict:
    data = serialize_user(principal.user)
    data["collections"] = [
        {"id": str(c.id), "name": c.name, "access": level} for c, level in accessible_collections(db, principal.user)
    ]
    return data


@router.post("/password")
def change_password(
    body: PasswordChange,
    request: Request,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    if user.auth_provider != "local" and not user.password_hash:
        raise HTTPException(status_code=400, detail="Ce compte se connecte par SSO.")
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Mot de passe actuel incorrect.")
    problem = validate_password_strength(body.new_password)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    user.password_hash = hash_password(body.new_password)
    user.session_version += 1  # revoke every other session
    db.commit()
    _set_session(response, user)
    audit.record("auth.password_change", actor_of(request, user))
    return {"ok": True}


# ---------------------------------------------------------------------------
# OIDC
# ---------------------------------------------------------------------------


@router.get("/oidc/login")
def oidc_login() -> RedirectResponse:
    settings = get_settings()
    if not settings.oidc_enabled:
        raise HTTPException(status_code=404, detail="SSO non configuré.")
    flow = oidc_client.new_flow()
    try:
        target = oidc_client.authorization_url(flow)
    except Exception:
        log.exception("[oidc] découverte impossible")
        raise HTTPException(status_code=502, detail="Fournisseur d'identité injoignable.")
    response = RedirectResponse(target, status_code=302)
    response.set_cookie(
        OIDC_STATE_COOKIE,
        sign_payload({k: flow[k] for k in ("state", "nonce", "verifier")}, ttl_seconds=600),
        max_age=600,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",  # must survive the cross-site redirect back from the IdP
        path="/",
    )
    return response


@router.get("/oidc/callback")
def oidc_callback(request: Request, code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db)):
    settings = get_settings()
    home = settings.public_url.rstrip("/")
    flow = unsign_payload(request.cookies.get(OIDC_STATE_COOKIE, ""))
    if error or not code or flow is None or flow.get("state") != state:
        audit.record("auth.login", actor_of(request, None), outcome="failure", details={"method": "oidc", "reason": error or "invalid_state"})
        return RedirectResponse(f"{home}/login?error=sso", status_code=302)
    try:
        identity = oidc_client.exchange(code, flow)
    except (OIDCError, Exception) as exc:  # noqa: BLE001 - any IdP failure is a failed login
        log.warning("[oidc] échec : %s", exc)
        audit.record("auth.login", actor_of(request, None), outcome="failure", details={"method": "oidc", "reason": str(exc)[:200]})
        return RedirectResponse(f"{home}/login?error=sso", status_code=302)

    user = upsert_oidc_user(db, identity)
    if not user.is_active:
        audit.record("auth.login", actor_of(request, user), outcome="failure", details={"method": "oidc", "reason": "inactive"})
        return RedirectResponse(f"{home}/login?error=inactive", status_code=302)
    response = RedirectResponse(f"{home}/", status_code=302)
    response.delete_cookie(OIDC_STATE_COOKIE, path="/")
    _set_session(response, user)
    audit.record("auth.login", actor_of(request, user), details={"method": "oidc"})
    return response


# ---------------------------------------------------------------------------
# Personal API keys
# ---------------------------------------------------------------------------


def _serialize_key(key: ApiKey) -> dict:
    return {
        "id": str(key.id),
        "name": key.name,
        "prefix": key.prefix,
        "created_at": key.created_at.isoformat() if key.created_at else None,
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
        "revoked": key.revoked_at is not None,
    }


@router.get("/api-keys")
def list_api_keys(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    keys = db.execute(select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())).scalars()
    return [_serialize_key(k) for k in keys]


@router.post("/api-keys", status_code=201)
def create_api_key(
    body: ApiKeyCreate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    plaintext, prefix, digest = generate_api_key()
    key = ApiKey(user_id=user.id, name=body.name.strip(), prefix=prefix, key_hash=digest)
    db.add(key)
    db.commit()
    audit.record("apikey.create", actor_of(request, user), resource_type="api_key", resource_id=key.id, details={"name": key.name})
    return {**_serialize_key(key), "key": plaintext}


@router.delete("/api-keys/{key_id}")
def revoke_api_key(
    key_id: uuid.UUID, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    key = db.get(ApiKey, key_id)
    if key is None or key.user_id != user.id:
        raise HTTPException(status_code=404, detail="Clé introuvable.")
    key.revoked_at = utcnow()
    db.commit()
    audit.record("apikey.revoke", actor_of(request, user), resource_type="api_key", resource_id=key.id)
    return {"ok": True}
