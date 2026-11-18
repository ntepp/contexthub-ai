"""Encryption of connector secrets at rest.

The key is derived from ``SECRET_KEY`` (HKDF-SHA256), so a database dump alone does
not reveal the tokens. Rotating ``SECRET_KEY`` makes stored secrets unreadable: the
administrator must then re-enter them (the API reports the connection as unusable).
"""

from __future__ import annotations

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.config import get_settings


class SecretUnreadable(Exception):
    pass


def _fernet() -> Fernet:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"contexthub-connector-secrets").derive(
        get_settings().secret_key.encode("utf-8")
    )
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise SecretUnreadable("Secret illisible : SECRET_KEY a changé depuis l'enregistrement.") from exc
