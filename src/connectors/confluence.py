"""Confluence connector (Cloud and Data Center), REST API v1.

* Cloud: ``base_url`` = ``https://<site>.atlassian.net/wiki``, account e-mail + API token.
* Data Center: ``base_url`` = the Confluence root, personal access token, no account.

Search uses CQL; the page body is read in ``export_view`` (rendered HTML, macros
expanded), which the existing HTML loader turns into headed text.
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, unquote_plus, urlparse

import httpx

from src.config import get_settings
from src.connectors.base import (
    ConnectorError,
    RemoteContent,
    RemotePage,
    SearchFilters,
    http_client,
    read_json,
    request,
)

SYSTEM = "Confluence"
_SPACE_KEY = re.compile(r"^[A-Za-z0-9_~.\-]{1,255}$")
_PAGE_ID = re.compile(r"^\d{1,20}$")


def cql_quote(value: str) -> str:
    """Quote a value for CQL. Backslash and double quote are the only characters that
    can end the string literal; control characters are dropped."""
    cleaned = "".join(ch for ch in value if ch.isprintable())
    return '"' + cleaned.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_cql(filters: SearchFilters) -> str:
    clauses = ["type = page"]
    if filters.space:
        if not _SPACE_KEY.match(filters.space):
            raise ConnectorError("Clé d'espace Confluence invalide.")
        clauses.append(f"space = {cql_quote(filters.space)}")
    if filters.label:
        clauses.append(f"label = {cql_quote(filters.label.strip().lower())}")
    if filters.query.strip():
        text = cql_quote(filters.query.strip())
        clauses.append(f"(title ~ {text} OR text ~ {text})")
    return " AND ".join(clauses) + " ORDER BY lastmodified DESC"


class ConfluenceConnector:
    kind = "confluence"

    def __init__(self, base_url: str, token: str, account: str | None = None, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        auth_headers = {}
        self._auth = None
        if account:
            self._auth = httpx.BasicAuth(account, token)
        else:
            auth_headers["Authorization"] = f"Bearer {token}"
        self._client = http_client(self.base_url, auth_headers, transport)
        if self._auth:
            self._client.auth = self._auth

    # ------------------------------------------------------------------ helpers
    def _get(self, path: str, **params) -> dict:
        return read_json(request(self._client, "GET", path, SYSTEM, params=params), SYSTEM)

    def _page_from(self, item: dict) -> RemotePage:
        links = item.get("_links") or {}
        webui = links.get("webui") or links.get("tinyui") or ""
        space = item.get("space") or {}
        return RemotePage(
            external_id=str(item.get("id", "")),
            title=str(item.get("title") or "Page sans titre"),
            url=f"{self.base_url}{webui}" if webui.startswith("/") else self.base_url,
            location=space.get("name") or space.get("key"),
            updated_at=(item.get("version") or {}).get("when"),
        )

    # ------------------------------------------------------------------ contract
    def test(self) -> str:
        data = self._get("/rest/api/space", limit=1)
        if "results" not in data:
            raise ConnectorError("Réponse inattendue de Confluence : vérifiez l'adresse de la connexion.")
        return "Confluence a accepté les identifiants."

    def search(self, filters: SearchFilters) -> list[RemotePage]:
        data = self._get(
            "/rest/api/content/search",
            cql=build_cql(filters),
            limit=max(1, min(filters.limit, 50)),
            expand="space,version",
        )
        return [self._page_from(item) for item in data.get("results", []) if item.get("id")]

    def resolve(self, reference: str) -> RemotePage:
        reference = reference.strip()
        if _PAGE_ID.match(reference):
            return self._page_from(self._get(f"/rest/api/content/{reference}", expand="space,version"))

        parsed = urlparse(reference)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ConnectorError("Collez l'adresse complète de la page Confluence.")
        if parsed.hostname.lower() != (urlparse(self.base_url).hostname or "").lower():
            raise ConnectorError("Ce lien n'appartient pas au Confluence de cette connexion.")

        match = re.search(r"/pages/(\d{1,20})(?:/|$)", parsed.path)
        page_id = match.group(1) if match else (parse_qs(parsed.query).get("pageId") or [None])[0]
        if page_id and _PAGE_ID.match(page_id):
            return self._page_from(self._get(f"/rest/api/content/{page_id}", expand="space,version"))

        display = re.search(r"/display/([^/]+)/([^/?#]+)", parsed.path)
        if display:
            space, title = unquote_plus(display.group(1)), unquote_plus(display.group(2))
            if not _SPACE_KEY.match(space):
                raise ConnectorError("Clé d'espace Confluence invalide dans ce lien.")
            data = self._get("/rest/api/content", spaceKey=space, title=title, expand="space,version", limit=1)
            results = data.get("results") or []
            if results:
                return self._page_from(results[0])
            raise ConnectorError("Page introuvable dans Confluence (ou non partagée avec la connexion).")

        raise ConnectorError("Lien non reconnu : ouvrez la page dans Confluence et copiez son adresse complète.")

    def fetch(self, external_id: str) -> RemoteContent:
        if not _PAGE_ID.match(external_id):
            raise ConnectorError("Identifiant de page Confluence invalide.")
        item = self._get(f"/rest/api/content/{external_id}", expand="body.export_view,space,version")
        page = self._page_from(item)
        body = ((item.get("body") or {}).get("export_view") or {}).get("value") or ""
        if not body.strip():
            raise ConnectorError(f"La page « {page.title} » est vide.")
        document = (
            # No <title>: the HTML loader would read it as a stray first paragraph.
            "<!doctype html><html><head><meta charset=\"utf-8\"></head><body>"
            f"<h1>{html.escape(page.title)}</h1>{body}</body></html>"
        ).encode()
        limit = get_settings().connector_max_page_mb * 1024 * 1024
        if len(document) > limit:
            raise ConnectorError(
                f"La page « {page.title} » dépasse {get_settings().connector_max_page_mb} Mo et ne peut pas être jointe."
            )
        return RemoteContent(page=page, content=document, mime_type="text/html")
