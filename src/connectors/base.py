"""Common contract of a document connector."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx

from src.config import get_settings


class ConnectorError(Exception):
    """A failure the user can act on (bad link, missing rights, remote system down).

    The message is shown as is in the interface, so it must be in French and must not
    contain secrets or raw remote responses.
    """


@dataclass
class RemotePage:
    external_id: str
    title: str
    url: str
    # Where the page lives (Confluence space, Notion parent), for display only.
    location: str | None = None
    updated_at: str | None = None

    def as_dict(self) -> dict:
        return {
            "external_id": self.external_id,
            "title": self.title,
            "url": self.url,
            "location": self.location,
            "updated_at": self.updated_at,
        }


@dataclass
class RemoteContent:
    page: RemotePage
    content: bytes
    mime_type: str  # "text/html" or "text/markdown"
    warnings: list[str] = field(default_factory=list)


@dataclass
class SearchFilters:
    query: str = ""
    space: str = ""
    label: str = ""
    limit: int = 20


class Connector(Protocol):
    kind: str

    def test(self) -> str:
        """Check the credentials; return a short French description of what answered."""

    def search(self, filters: SearchFilters) -> list[RemotePage]: ...

    def resolve(self, reference: str) -> RemotePage:
        """Turn a page link (or raw id) into a page, checking that it is readable."""

    def fetch(self, external_id: str) -> RemoteContent: ...


def http_client(base_url: str, headers: dict[str, str], transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """Client for a remote document system.

    Redirects are not followed: a compromised or misconfigured remote must not be able
    to make the server send the connection's credentials to another host.
    """
    settings = get_settings()
    return httpx.Client(
        base_url=base_url,
        headers={"User-Agent": f"ContextHub/{settings.app_version}", "Accept": "application/json", **headers},
        timeout=settings.connector_timeout_seconds,
        follow_redirects=False,
        transport=transport,
    )


def raise_for_remote(resp: httpx.Response, system: str) -> None:
    """Translate a remote HTTP error into a message the user understands."""
    code = resp.status_code
    if code < 300:
        return
    if 300 <= code < 400:
        raise ConnectorError(f"{system} a répondu par une redirection : vérifiez l'adresse de la connexion.")
    if code == 401:
        raise ConnectorError(f"{system} refuse les identifiants de la connexion.")
    if code == 403:
        raise ConnectorError(f"Le compte de la connexion n'a pas le droit de lire ce contenu dans {system}.")
    if code == 404:
        raise ConnectorError(f"Page introuvable dans {system} (ou non partagée avec la connexion).")
    if code == 429:
        raise ConnectorError(f"{system} limite le débit des requêtes : réessayez dans un instant.")
    raise ConnectorError(f"{system} a répondu {code}.")


def request(client: httpx.Client, method: str, url: str, system: str, **kwargs) -> httpx.Response:
    try:
        resp = client.request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise ConnectorError(f"{system} ne répond pas (délai dépassé).") from exc
    except httpx.HTTPError as exc:
        raise ConnectorError(f"{system} est injoignable.") from exc
    raise_for_remote(resp, system)
    return resp


def read_json(resp: httpx.Response, system: str) -> dict:
    try:
        data = resp.json()
    except ValueError as exc:
        raise ConnectorError(f"Réponse inattendue de {system} : vérifiez l'adresse de la connexion.") from exc
    if not isinstance(data, dict):
        raise ConnectorError(f"Réponse inattendue de {system}.")
    return data
