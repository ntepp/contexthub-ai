"""Connectors: remote clients (mocked HTTP), secrets, and pages attached to a conversation."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import select

from src.connectors import registry
from src.connectors.base import ConnectorError, RemoteContent, RemotePage, SearchFilters
from src.connectors.confluence import ConfluenceConnector, build_cql
from src.connectors.crypto import decrypt_secret, encrypt_secret
from src.connectors.notion import NotionConnector, block_to_markdown, parse_page_id
from src.db.models import Collection, Connection, Document

BASE = "https://acme.atlassian.net/wiki"
QUESTION = "Combien de jours de teletravail par semaine sont autorises ?"
PAGE_HTML = (
    "<h2>Regle generale</h2><p>Le teletravail est autorise deux jours par semaine pour les salaries "
    "en contrat a duree indeterminee.</p><h2>Materiel</h2><p>Un ordinateur portable est fourni.</p>"
)


# ---------------------------------------------------------------------------
# Confluence client
# ---------------------------------------------------------------------------


def _confluence(handler, account: str | None = "bot@acme.fr") -> ConfluenceConnector:
    return ConfluenceConnector(BASE, "s3cret-token", account, transport=httpx.MockTransport(handler))


def _page_json(page_id="12345", title="Politique de teletravail", body=PAGE_HTML) -> dict:
    return {
        "id": page_id,
        "title": title,
        "space": {"key": "RH", "name": "Ressources humaines"},
        "version": {"when": "2026-10-01T08:00:00.000Z"},
        "_links": {"webui": f"/spaces/RH/pages/{page_id}/Politique"},
        "body": {"export_view": {"value": body}},
    }


def test_cql_quotes_user_input():
    cql = build_cql(SearchFilters(query='conges" OR type = attachment', space="RH", label="Politique"))
    assert cql.startswith('type = page AND space = "RH" AND label = "politique"')
    # The quote typed by the user stays inside the string literal.
    assert 'title ~ "conges\\" OR type = attachment"' in cql
    with pytest.raises(ConnectorError):
        build_cql(SearchFilters(space='RH" OR space != "X'))


def test_confluence_search_and_auth_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = request.url
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"results": [_page_json()]})

    pages = _confluence(handler).search(SearchFilters(query="teletravail", space="RH"))
    assert seen["url"].path == "/wiki/rest/api/content/search"
    assert 'space = "RH"' in seen["url"].params["cql"]
    assert seen["auth"].startswith("Basic ")
    assert pages[0].external_id == "12345"
    assert pages[0].url == f"{BASE}/spaces/RH/pages/12345/Politique"
    assert pages[0].location == "Ressources humaines"

    # Data Center: no account, personal access token as a bearer.
    _confluence(handler, account=None).search(SearchFilters())
    assert seen["auth"] == "Bearer s3cret-token"


@pytest.mark.parametrize(
    "link",
    [
        f"{BASE}/spaces/RH/pages/12345/Politique+de+teletravail",
        f"{BASE}/pages/viewpage.action?pageId=12345",
        "12345",
    ],
)
def test_confluence_resolves_page_links(link):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/wiki/rest/api/content/12345"
        return httpx.Response(200, json=_page_json())

    assert _confluence(handler).resolve(link).title == "Politique de teletravail"


def test_confluence_rejects_links_to_another_host():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be called
        raise AssertionError("no request expected")

    with pytest.raises(ConnectorError, match="n'appartient pas"):
        _confluence(handler).resolve("https://evil.example/wiki/spaces/RH/pages/12345/x")
    with pytest.raises(ConnectorError):
        _confluence(handler).resolve("file:///etc/passwd")


def test_confluence_fetch_builds_an_html_document():
    content = _confluence(lambda r: httpx.Response(200, json=_page_json(title="Conges <b>payes</b>"))).fetch("12345")
    text = content.content.decode()
    assert content.mime_type == "text/html"
    assert "<h1>Conges &lt;b&gt;payes&lt;/b&gt;</h1>" in text  # the title is escaped
    assert "deux jours par semaine" in text


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, "refuse les identifiants"), (403, "pas le droit"), (404, "introuvable"), (302, "redirection"), (500, "500")],
)
def test_confluence_errors_are_explained_without_leaking_the_response(status, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="internal stack trace with s3cret-token", headers={"location": "http://x"})

    with pytest.raises(ConnectorError) as err:
        _confluence(handler).fetch("12345")
    assert expected in str(err.value)
    assert "s3cret" not in str(err.value) and "stack" not in str(err.value)


def test_confluence_unreachable_is_explained():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(ConnectorError, match="injoignable"):
        _confluence(handler).test()


# ---------------------------------------------------------------------------
# Notion client
# ---------------------------------------------------------------------------

NOTION_ID = "0123456789abcdef0123456789abcdef"
NOTION_UUID = str(uuid.UUID(NOTION_ID))


def _rich(text: str) -> list[dict]:
    return [{"plain_text": text}]


def _notion_page() -> dict:
    return {
        "object": "page",
        "id": NOTION_UUID,
        "url": f"https://www.notion.so/Onboarding-{NOTION_ID}",
        "last_edited_time": "2026-10-01T08:00:00.000Z",
        "parent": {"type": "workspace"},
        "properties": {"Nom": {"type": "title", "title": _rich("Guide d'accueil")}},
    }


def test_notion_page_id_parsing():
    assert parse_page_id(f"https://www.notion.so/acme/Guide-d-accueil-{NOTION_ID}?pvs=4") == NOTION_UUID
    assert parse_page_id(NOTION_UUID) == NOTION_UUID
    with pytest.raises(ConnectorError):
        parse_page_id("https://www.notion.so/acme/sans-identifiant")


def test_notion_blocks_to_markdown():
    assert block_to_markdown({"type": "heading_2", "heading_2": {"rich_text": _rich("Badge")}}).strip() == "## Badge"
    assert block_to_markdown({"type": "to_do", "to_do": {"rich_text": _rich("Signer"), "checked": True}}) == "- [x] Signer"
    assert block_to_markdown({"type": "table_row", "table_row": {"cells": [_rich("a"), _rich("b")]}}) == "| a | b |"
    assert block_to_markdown({"type": "paragraph", "paragraph": {"rich_text": []}}) is None


def test_notion_fetch_follows_pagination_and_nested_blocks_but_not_subpages():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer ntn_token"
        assert request.headers["notion-version"]
        path = request.url.path
        calls.append(path + ("?" + request.url.query.decode() if request.url.query else ""))
        if path == f"/v1/pages/{NOTION_UUID}":
            return httpx.Response(200, json=_notion_page())
        if path == f"/v1/blocks/{NOTION_UUID}/children":
            if "start_cursor" not in request.url.params:
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {"id": "b1", "type": "heading_1", "heading_1": {"rich_text": _rich("Premier jour")}},
                            {"id": "b2", "type": "bulleted_list_item", "has_children": True,
                             "bulleted_list_item": {"rich_text": _rich("Retirer son badge")}},
                        ],
                        "has_more": True,
                        "next_cursor": "c2",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": "sub", "type": "child_page", "has_children": True, "child_page": {"title": "Annexe"}},
                        {"id": "b3", "type": "paragraph", "paragraph": {"rich_text": _rich("Bienvenue chez ACME.")}},
                    ],
                    "has_more": False,
                },
            )
        if path == "/v1/blocks/b2/children":
            return httpx.Response(
                200, json={"results": [{"id": "b4", "type": "paragraph", "paragraph": {"rich_text": _rich("A l'accueil")}}]}
            )
        raise AssertionError(f"unexpected call {path}")

    content = NotionConnector("ntn_token", transport=httpx.MockTransport(handler)).fetch(NOTION_UUID)
    text = content.content.decode()
    assert content.mime_type == "text/markdown"
    assert text.startswith("# Guide d'accueil")
    assert "# Premier jour" in text and "- Retirer son badge" in text
    assert "  A l'accueil" in text  # nested block, indented
    assert "Sous-page : Annexe" in text
    assert not any("/blocks/sub/" in c for c in calls)  # sub-pages are listed, never followed
    assert content.page.url.startswith("https://www.notion.so/")


def test_notion_search_returns_pages_only():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["query"] == "accueil" and body["filter"]["value"] == "page"
        return httpx.Response(200, json={"results": [_notion_page(), {"object": "database", "id": "x"}]})

    pages = NotionConnector("ntn_token", transport=httpx.MockTransport(handler)).search(SearchFilters(query="accueil"))
    assert [p.title for p in pages] == ["Guide d'accueil"]


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------


def test_secret_is_encrypted_at_rest_and_never_returned(admin, db):
    resp = admin.post(
        "/admin/connections",
        json={"kind": "confluence", "name": "Wiki RH", "base_url": BASE + "/", "account": "bot@acme.fr", "secret": "s3cret-token"},
    )
    assert resp.status_code == 201, resp.text
    assert "secret" not in resp.text and "s3cret" not in resp.text
    assert resp.json()["base_url"] == BASE

    stored = db.execute(select(Connection)).scalar_one()
    assert "s3cret-token" not in stored.secret_encrypted
    assert decrypt_secret(stored.secret_encrypted) == "s3cret-token"
    assert encrypt_secret("a") != encrypt_secret("a")  # random IV

    listing = admin.get("/admin/connections").text
    assert "s3cret" not in listing and "secret_encrypted" not in listing
    audit_log = admin.get("/admin/audit").text
    assert "s3cret" not in audit_log


def test_connection_validation(admin):
    bad = {"kind": "confluence", "name": "x", "secret": "t", "base_url": "ftp://wiki"}
    assert admin.post("/admin/connections", json=bad).status_code == 400
    bad["base_url"] = "https://user:pw@wiki.acme.fr"
    assert admin.post("/admin/connections", json=bad).status_code == 400
    assert admin.post("/admin/connections", json={"kind": "sharepoint", "name": "x", "secret": "t"}).status_code == 422
    ok = {"kind": "notion", "name": "Notion", "secret": "ntn_x"}
    assert admin.post("/admin/connections", json=ok).status_code == 201
    assert admin.post("/admin/connections", json=ok).status_code == 409


def test_connections_are_admin_managed(member, admin):
    body = {"kind": "notion", "name": "Notion", "secret": "ntn_x"}
    assert member.post("/admin/connections", json=body).status_code == 403
    assert member.get("/admin/connections").status_code == 403
    created = admin.post("/admin/connections", json=body).json()
    assert member.patch(f"/admin/connections/{created['id']}", json={"is_enabled": False}).status_code == 403
    assert member.delete(f"/admin/connections/{created['id']}").status_code == 403
    # Members see the name and type only.
    assert member.get("/connections").json() == [
        {"id": created["id"], "kind": "notion", "kind_label": "Notion", "name": "Notion"}
    ]


# ---------------------------------------------------------------------------
# Pages attached to a conversation
# ---------------------------------------------------------------------------


class FakeConnector:
    """A tiny in-memory wiki."""

    kind = "confluence"

    def __init__(self):
        self.pages = {
            "100": ("Politique de teletravail", PAGE_HTML),
            "200": ("Notes de frais", "<p>Les notes de frais sont remboursees sous trente jours apres validation.</p>"),
        }
        self.fetched: list[str] = []

    def _page(self, page_id: str) -> RemotePage:
        if page_id not in self.pages:
            raise ConnectorError("Page introuvable dans Confluence (ou non partagée avec la connexion).")
        return RemotePage(page_id, self.pages[page_id][0], f"{BASE}/spaces/RH/pages/{page_id}", "Ressources humaines")

    def test(self) -> str:
        return "Confluence a accepté les identifiants."

    def search(self, filters: SearchFilters) -> list[RemotePage]:
        return [self._page(i) for i, (title, _) in self.pages.items() if filters.query.lower() in title.lower()]

    def resolve(self, reference: str) -> RemotePage:
        return self._page(reference.rstrip("/").rsplit("/", 1)[-1])

    def fetch(self, external_id: str) -> RemoteContent:
        page = self._page(external_id)
        self.fetched.append(external_id)
        body = f"<html><body><h1>{page.title}</h1>{self.pages[external_id][1]}</body></html>"
        return RemoteContent(page=page, content=body.encode(), mime_type="text/html")


@pytest.fixture
def wiki(admin):
    fake = FakeConnector()
    registry.set_connector_factory_override(lambda connection: fake)
    resp = admin.post(
        "/admin/connections",
        json={"kind": "confluence", "name": "Wiki RH", "base_url": BASE, "account": "bot@acme.fr", "secret": "tok"},
    )
    assert resp.status_code == 201, resp.text
    fake.connection_id = resp.json()["id"]
    yield fake
    registry.set_connector_factory_override(None)


def _attach(client, wiki, references, conversation_id=None):
    body = {"connection_id": wiki.connection_id, "references": references}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return client.post("/chat/sources", json=body)


def test_search_a_connection(member, wiki, admin):
    resp = member.get(f"/connections/{wiki.connection_id}/search", params={"q": "frais"})
    assert resp.status_code == 200, resp.text
    assert [p["title"] for p in resp.json()["pages"]] == ["Notes de frais"]
    event = admin.get("/admin/audit", params={"action": "connection.search"}).json()["items"][0]
    assert event["details"]["query"] == "frais" and event["details"]["results"] == 1


def test_attached_page_answers_with_a_link_to_the_source(member, wiki, admin, store):
    resp = _attach(member, wiki, [f"{BASE}/spaces/RH/pages/100"])
    assert resp.status_code == 200, resp.text
    data = resp.json()
    conversation_id = data["conversation_id"]
    assert data["errors"] == []
    source = data["sources"][0]
    assert source["title"] == "Politique de teletravail"
    assert source["kind"] == "confluence" and source["status"] == "ready"  # Celery runs eagerly in tests
    assert source["url"] == f"{BASE}/spaces/RH/pages/100"

    # The member has no collection at all: the attached page alone grounds the answer.
    answer = member.post("/chat/ask", json={"question": QUESTION, "conversation_id": conversation_id})
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["refused"] is False
    cited = body["sources"][0]
    assert cited["filename"] == "Politique de teletravail"
    assert cited["url"] == f"{BASE}/spaces/RH/pages/100"
    assert cited["source_kind"] == "confluence"
    assert "deux jours par semaine" in cited["snippet"]

    convo = member.get(f"/conversations/{conversation_id}").json()
    assert convo["title"].startswith("Combien de jours")  # named after the first question
    assert [s["title"] for s in convo["sources"]] == ["Politique de teletravail"]

    attach_event = admin.get("/admin/audit", params={"action": "source.attach"}).json()["items"][0]
    assert attach_event["details"]["title"] == "Politique de teletravail"
    assert attach_event["details"]["connection"] == "Wiki RH"
    ask_event = admin.get("/admin/audit", params={"action": "query.ask"}).json()["items"][0]
    assert ask_event["details"]["sources"][0]["url"] == f"{BASE}/spaces/RH/pages/100"


def test_attached_pages_stay_private_to_their_conversation(member, wiki, admin, admin_user, db):
    conversation_id = _attach(member, wiki, ["100"]).json()["conversation_id"]
    session = db.execute(select(Collection).where(Collection.conversation_id.is_not(None))).scalar_one()
    document = db.execute(select(Document).where(Document.collection_id == session.id)).scalar_one()

    # Not a regular collection, even for an administrator.
    assert admin.get("/collections").json() == []
    assert admin.get(f"/collections/{session.id}/documents").status_code == 404
    assert admin.get(f"/documents/{document.id}").status_code == 404
    assert admin.get(f"/documents/{document.id}/file").status_code == 404
    assert admin.delete(f"/collections/{session.id}").status_code == 404
    grant = admin.put(f"/admin/collections/{session.id}/grants", json={"user_id": str(admin_user.id), "level": "viewer"})
    assert grant.status_code == 404
    assert admin.get("/admin/system").json()["counts"]["collections"] == 0

    # Another user cannot read, extend or query the conversation's pages.
    assert admin.get(f"/conversations/{conversation_id}/sources").status_code == 404
    assert _attach(admin, wiki, ["200"], conversation_id).status_code == 404
    assert admin.delete(f"/conversations/{conversation_id}/sources/{document.id}").status_code == 404
    asked = admin.post("/chat/ask", json={"question": QUESTION, "collection_ids": [str(session.id)]})
    assert asked.status_code == 404
    # ...and a question asked elsewhere never reaches them.
    elsewhere = member.post("/chat/ask", json={"question": QUESTION})
    assert elsewhere.status_code == 400  # no collection, no attached page in this new conversation


def test_attached_only_scope_and_regular_collections(member, wiki, admin, collection, member_user):
    from tests.conftest import grant, upload
    from tests.fakes import make_pdf

    pdf = make_pdf([["Article 1", "Teletravail", "Le teletravail est autorise cinq jours par semaine a l etranger."]])
    assert upload(admin, collection["id"], "ancien.pdf", pdf).status_code == 202
    grant(admin, collection["id"], member_user.id, "viewer")
    # One conversation per question: a follow-up would be rewritten by the condense step.
    conversation_id = _attach(member, wiki, ["100"]).json()["conversation_id"]
    both = member.post("/chat/ask", json={"question": QUESTION, "conversation_id": conversation_id}).json()
    assert {s["filename"] for s in both["sources"]} == {"Politique de teletravail", "ancien.pdf"}

    other_id = _attach(member, wiki, ["100"]).json()["conversation_id"]
    only = member.post(
        "/chat/ask", json={"question": QUESTION, "conversation_id": other_id, "attached_only": True}
    ).json()
    assert {s["filename"] for s in only["sources"]} == {"Politique de teletravail"}

    fresh = member.post("/chat/ask", json={"question": QUESTION, "attached_only": True})
    assert fresh.status_code == 400


def test_reattaching_refreshes_and_detaching_removes(member, wiki, store, db):
    conversation_id = _attach(member, wiki, ["100", "200"]).json()["conversation_id"]
    wiki.pages["100"] = ("Politique de teletravail", "<p>Le teletravail est autorise trois jours par semaine.</p>")
    again = _attach(member, wiki, ["100"], conversation_id).json()
    assert [s["title"] for s in again["sources"]].count("Politique de teletravail") == 1
    assert len(again["sources"]) == 2

    answer = member.post("/chat/ask", json={"question": QUESTION, "conversation_id": conversation_id}).json()
    snippets = " ".join(s["snippet"] for s in answer["sources"])
    assert "trois jours" in snippets and "deux jours" not in snippets

    target = next(s for s in again["sources"] if s["title"] == "Notes de frais")
    assert member.delete(f"/conversations/{conversation_id}/sources/{target['id']}").status_code == 200
    assert member.delete(f"/conversations/{conversation_id}/sources/{target['id']}").status_code == 404
    assert [s["title"] for s in member.get(f"/conversations/{conversation_id}/sources").json()] == [
        "Politique de teletravail"
    ]
    assert store.count_document(uuid.UUID(target["id"])) == 0


def test_deleting_the_conversation_deletes_its_pages(member, wiki, store, db):
    data = _attach(member, wiki, ["100"]).json()
    document_id = uuid.UUID(data["sources"][0]["id"])
    assert store.count_document(document_id) > 0
    assert member.delete(f"/conversations/{data['conversation_id']}").status_code == 200
    assert store.count_document(document_id) == 0
    assert db.execute(select(Collection)).scalars().all() == []
    assert db.execute(select(Document)).scalars().all() == []


def test_deleting_the_user_deletes_their_attached_pages(member, member_user, wiki, admin, store, db):
    data = _attach(member, wiki, ["100"]).json()
    document_id = uuid.UUID(data["sources"][0]["id"])
    assert admin.delete(f"/admin/users/{member_user.id}").status_code == 200
    assert store.count_document(document_id) == 0
    assert db.execute(select(Collection)).scalars().all() == []


def test_partial_failure_reports_each_page(member, wiki):
    resp = _attach(member, wiki, ["100", "999"])
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sources"]) == 1
    assert data["errors"][0]["reference"] == "999" and "introuvable" in data["errors"][0]["message"]

    nothing = _attach(member, wiki, ["999"])
    assert nothing.status_code == 422 and "introuvable" in nothing.json()["detail"]
    # A failed first attachment leaves no empty conversation behind.
    assert len(member.get("/conversations").json()) == 1


def test_page_limit_per_conversation(member, wiki, monkeypatch):
    from src.config import reset_settings_cache

    monkeypatch.setenv("CONNECTOR_MAX_PAGES_PER_CONVERSATION", "1")
    reset_settings_cache()
    try:
        data = _attach(member, wiki, ["100", "200"]).json()
        assert len(data["sources"]) == 1
        assert "ne peut pas dépasser 1 pages" in data["errors"][0]["message"]
    finally:
        monkeypatch.delenv("CONNECTOR_MAX_PAGES_PER_CONVERSATION")
        reset_settings_cache()


def test_disabled_connection_and_disabled_feature(member, wiki, admin, monkeypatch):
    from src.config import reset_settings_cache

    assert admin.patch(f"/admin/connections/{wiki.connection_id}", json={"is_enabled": False}).status_code == 200
    assert member.get("/connections").json() == []
    assert _attach(member, wiki, ["100"]).status_code == 404
    assert member.get(f"/connections/{wiki.connection_id}/search").status_code == 404
    admin.patch(f"/admin/connections/{wiki.connection_id}", json={"is_enabled": True})

    monkeypatch.setenv("CONNECTORS_ENABLED", "false")
    reset_settings_cache()
    try:
        assert member.get("/connections").json() == []
        assert _attach(member, wiki, ["100"]).status_code == 403
    finally:
        monkeypatch.delenv("CONNECTORS_ENABLED")
        reset_settings_cache()


def test_connection_test_endpoint_and_anonymous_access(admin, wiki, anon):
    result = admin.post(f"/admin/connections/{wiki.connection_id}/test").json()
    assert result == {"ok": True, "message": "Confluence a accepté les identifiants."}
    assert anon.get("/connections").status_code == 401
    assert anon.post("/chat/sources", json={"connection_id": wiki.connection_id, "references": ["100"]}).status_code == 401
    # Cookie session without the CSRF header is refused on state-changing calls.
    from fastapi.testclient import TestClient

    from src.app import app

    bare = TestClient(app)
    bare.cookies = admin.cookies
    assert bare.post("/chat/sources", json={"connection_id": wiki.connection_id, "references": ["100"]}).status_code == 403
