"""Notion connector (public API, internal integration token).

Only pages explicitly shared with the integration in Notion are visible. A page is
read block by block and rendered as Markdown; sub-pages are listed by title but not
followed (the user attaches them separately if needed).
"""

from __future__ import annotations

import re
import uuid

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

SYSTEM = "Notion"
API_ROOT = "https://api.notion.com"
API_VERSION = "2022-06-28"
_MAX_DEPTH = 4
_MAX_BLOCKS = 3000
_HEX32 = re.compile(r"[0-9a-fA-F]{32}")


def parse_page_id(reference: str) -> str:
    """Extract the page id from a Notion link, or accept a raw id (with or without dashes)."""
    reference = reference.strip().split("?")[0].split("#")[0]
    compact = reference.rsplit("/", 1)[-1].replace("-", "")
    found = _HEX32.findall(compact)
    if not found:
        raise ConnectorError("Lien Notion non reconnu : copiez l'adresse de la page depuis « Partager ».")
    return str(uuid.UUID(found[-1]))


def _plain(rich_text: list | None) -> str:
    return "".join(part.get("plain_text", "") for part in rich_text or [])


def page_title(page: dict) -> str:
    for prop in (page.get("properties") or {}).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            title = _plain(prop.get("title"))
            if title.strip():
                return title.strip()
    return "Page sans titre"


def block_to_markdown(block: dict, depth: int = 0) -> str | None:
    """One Notion block as a Markdown line (``None`` when the block carries no text)."""
    kind = block.get("type", "")
    data = block.get(kind) or {}
    text = _plain(data.get("rich_text"))
    indent = "  " * depth
    if kind == "paragraph":
        return f"{indent}{text}" if text.strip() else None
    if kind in ("heading_1", "heading_2", "heading_3"):
        return f"\n{'#' * int(kind[-1])} {text}\n" if text.strip() else None
    if kind == "bulleted_list_item" or kind == "toggle":
        return f"{indent}- {text}"
    if kind == "numbered_list_item":
        return f"{indent}1. {text}"
    if kind == "to_do":
        return f"{indent}- [{'x' if data.get('checked') else ' '}] {text}"
    if kind in ("quote", "callout"):
        return f"{indent}> {text}" if text.strip() else None
    if kind == "code":
        return f"```\n{text}\n```"
    if kind == "divider":
        return "\n---\n"
    if kind == "table_row":
        return "| " + " | ".join(_plain(cell) for cell in data.get("cells") or []) + " |"
    if kind == "child_page":
        return f"{indent}- Sous-page : {data.get('title', '')}"
    if kind in ("bookmark", "embed", "link_preview") and data.get("url"):
        return f"{indent}{data['url']}"
    return f"{indent}{text}" if text.strip() else None


class NotionConnector:
    kind = "notion"

    def __init__(self, token: str, transport: httpx.BaseTransport | None = None):
        self._client = http_client(
            API_ROOT, {"Authorization": f"Bearer {token}", "Notion-Version": API_VERSION}, transport
        )

    def _call(self, method: str, path: str, **kwargs) -> dict:
        return read_json(request(self._client, method, path, SYSTEM, **kwargs), SYSTEM)

    def _page_from(self, page: dict) -> RemotePage:
        parent = page.get("parent") or {}
        location = {"workspace": "Espace de travail", "page_id": "Sous-page", "database_id": "Base de données"}.get(
            parent.get("type", ""), None
        )
        return RemotePage(
            external_id=str(page.get("id", "")),
            title=page_title(page),
            url=page.get("url") or "https://www.notion.so/",
            location=location,
            updated_at=page.get("last_edited_time"),
        )

    # ------------------------------------------------------------------ contract
    def test(self) -> str:
        me = self._call("GET", "/v1/users/me")
        name = me.get("name") or "l'intégration"
        return f"Notion a accepté le jeton ({name})."

    def search(self, filters: SearchFilters) -> list[RemotePage]:
        body = {
            "filter": {"property": "object", "value": "page"},
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
            "page_size": max(1, min(filters.limit, 50)),
        }
        if filters.query.strip():
            body["query"] = filters.query.strip()
        data = self._call("POST", "/v1/search", json=body)
        return [self._page_from(item) for item in data.get("results", []) if item.get("object") == "page"]

    def resolve(self, reference: str) -> RemotePage:
        return self._page_from(self._call("GET", f"/v1/pages/{parse_page_id(reference)}"))

    def _children(self, block_id: str, depth: int, lines: list[str], budget: list[int]) -> bool:
        """Append the Markdown of a block's children; return False once truncated."""
        cursor = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = self._call("GET", f"/v1/blocks/{block_id}/children", params=params)
            for block in data.get("results", []):
                budget[0] -= 1
                if budget[0] < 0:
                    return False
                line = block_to_markdown(block, depth)
                if line is not None:
                    lines.append(line)
                # A sub-page is its own document: listed, never followed.
                if block.get("has_children") and block.get("type") not in ("child_page", "child_database"):
                    if depth + 1 >= _MAX_DEPTH:
                        continue
                    if not self._children(block["id"], depth + 1, lines, budget):
                        return False
            if not data.get("has_more"):
                return True
            cursor = data.get("next_cursor")
            if not cursor:
                return True

    def fetch(self, external_id: str) -> RemoteContent:
        page_id = parse_page_id(external_id)
        page = self._page_from(self._call("GET", f"/v1/pages/{page_id}"))
        lines: list[str] = [f"# {page.title}", ""]
        complete = self._children(page_id, 0, lines, [_MAX_BLOCKS])
        if len(lines) <= 2:
            raise ConnectorError(f"La page « {page.title} » est vide.")
        content = "\n".join(lines).encode("utf-8")
        limit = get_settings().connector_max_page_mb * 1024 * 1024
        if len(content) > limit:
            raise ConnectorError(
                f"La page « {page.title} » dépasse {get_settings().connector_max_page_mb} Mo et ne peut pas être jointe."
            )
        warnings = [] if complete else ["Page très longue : seul le début a été joint."]
        return RemoteContent(page=page, content=content, mime_type="text/markdown", warnings=warnings)
