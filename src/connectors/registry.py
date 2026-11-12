"""Builds the connector of a stored connection."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse

from src.connectors.base import Connector, ConnectorError
from src.connectors.confluence import ConfluenceConnector
from src.connectors.crypto import SecretUnreadable, decrypt_secret
from src.connectors.notion import NotionConnector
from src.db.models import CONNECTOR_CONFLUENCE, CONNECTOR_NOTION, Connection

KIND_LABELS = {CONNECTOR_CONFLUENCE: "Confluence", CONNECTOR_NOTION: "Notion"}

_factory_override: Callable[[Connection], Connector] | None = None


def set_connector_factory_override(factory: Callable[[Connection], Connector] | None) -> None:
    """For tests: replace the remote systems by fakes."""
    global _factory_override
    _factory_override = factory


def validate_base_url(kind: str, base_url: str | None) -> str | None:
    """Normalise the address of a connection.

    Internal addresses are allowed on purpose (Confluence Data Center lives inside the
    company network); only an administrator can set it.
    """
    if kind == CONNECTOR_NOTION:
        return None
    parsed = urlparse((base_url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConnectorError("Adresse de Confluence invalide (attendu : https://…).")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConnectorError("L'adresse de Confluence ne doit contenir ni identifiants ni paramètres.")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")


def build_connector(connection: Connection) -> Connector:
    if _factory_override is not None:
        return _factory_override(connection)
    try:
        secret = decrypt_secret(connection.secret_encrypted)
    except SecretUnreadable as exc:
        raise ConnectorError(
            "Le jeton de cette connexion est illisible : l'administrateur doit le saisir à nouveau."
        ) from exc
    if connection.kind == CONNECTOR_CONFLUENCE:
        return ConfluenceConnector(connection.base_url or "", secret, connection.account or None)
    if connection.kind == CONNECTOR_NOTION:
        return NotionConnector(secret)
    raise ConnectorError("Type de connexion inconnu.")
