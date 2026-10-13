"""Password hashing, session tokens and API keys."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from src.config import get_settings

SESSION_COOKIE = "ch_session"
OIDC_STATE_COOKIE = "ch_oidc"
API_KEY_PREFIX = "chk_"
_ALGORITHM = "HS256"

# bcrypt only looks at the first 72 bytes; longer inputs are pre-hashed so that a long
# passphrase is not silently truncated.
_BCRYPT_MAX = 72


def _prepare(password: str) -> bytes:
    raw = password.encode("utf-8")
    if len(raw) > _BCRYPT_MAX:
        raw = hashlib.sha256(raw).hexdigest().encode("ascii")
    return raw


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        # Constant-ish work even for SSO-only accounts, to avoid user enumeration by timing.
        bcrypt.checkpw(b"x", bcrypt.hashpw(b"y", bcrypt.gensalt(rounds=4)))
        return False
    try:
        return bcrypt.checkpw(_prepare(password), password_hash.encode("ascii"))
    except ValueError:
        return False


def validate_password_strength(password: str) -> str | None:
    """Return an error message, or None if the password is acceptable."""
    settings = get_settings()
    if len(password) < settings.password_min_length:
        return f"Le mot de passe doit contenir au moins {settings.password_min_length} caractères."
    classes = sum(
        [
            any(c.islower() for c in password),
            any(c.isupper() for c in password),
            any(c.isdigit() for c in password),
            any(not c.isalnum() for c in password),
        ]
    )
    if classes < 3:
        return "Le mot de passe doit mélanger au moins trois types : minuscules, majuscules, chiffres, symboles."
    return None


def generate_password(length: int = 16) -> str:
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_!@#%"
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if validate_password_strength(candidate) is None:
            return candidate


# ---------------------------------------------------------------------------
# Session tokens
# ---------------------------------------------------------------------------


def create_session_token(user_id: uuid.UUID, session_version: int) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "sv": session_version,
        "typ": "session",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.session_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=_ALGORITHM)


def decode_session_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[_ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "session":
        return None
    return payload


def sign_payload(payload: dict, ttl_seconds: int) -> str:
    """Short-lived signed blob (OIDC state, nonce, PKCE verifier)."""
    data = dict(payload)
    data["exp"] = int((datetime.now(UTC) + timedelta(seconds=ttl_seconds)).timestamp())
    data["typ"] = "state"
    return jwt.encode(data, get_settings().secret_key, algorithm=_ALGORITHM)


def unsign_payload(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[_ALGORITHM])
    except jwt.PyJWTError:
        return None
    return payload if payload.get("typ") == "state" else None


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


def generate_api_key() -> tuple[str, str, str]:
    """Return (plaintext key, display prefix, sha256 hash). Only the hash is stored."""
    key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return key, key[:12], hash_api_key(key)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
