"""End-to-end scenario for connectors through the real UI (Playwright + Chrome).

Needs the running stack plus the stand-in Confluence:

    python -m tests.e2e.mock_confluence
    E2E_BASE_URL=http://localhost:5173 E2E_EMAIL=... E2E_PASSWORD=... pytest -m e2e tests/e2e/test_ui_connectors.py

An administrator adds and tests a Confluence connection; a page is attached to a new
conversation by search, a second one by link; a question restricted to the attached
pages is answered with a citation that opens the page in Confluence; removing the
conversation removes its pages. The connection is deleted at the end.
"""

from __future__ import annotations

import os
import time

import pytest

BASE = os.getenv("E2E_BASE_URL")
WIKI = os.getenv("E2E_CONFLUENCE_URL", "http://127.0.0.1:8765/wiki")
WIKI_TOKEN = os.getenv("E2E_CONFLUENCE_TOKEN", "e2e-token")
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not BASE, reason="E2E_BASE_URL non défini")]
HEADERS = {"X-Requested-With": "contexthub"}


def test_attach_confluence_pages_and_ask_through_the_ui():
    sync_api = pytest.importorskip("playwright.sync_api")
    expect = sync_api.expect
    name = f"Wiki E2E {time.strftime('%H%M%S')}"

    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv("E2E_BROWSER_CHANNEL", "chrome"), headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        conversation_id = None

        def api_get(path: str, **params):
            return page.request.get(f"{BASE}/api{path}", params=params or None).json()

        page.goto(f"{BASE}/login")
        page.fill("#email", os.environ["E2E_EMAIL"])
        page.fill("#password", os.environ["E2E_PASSWORD"])
        page.locator("button[type=submit]").click()
        page.wait_for_url(f"{BASE}/")

        try:
            # --- administrator: add the connection, then test it ---------------------
            page.goto(f"{BASE}/admin/connecteurs")
            page.get_by_role("button", name="Ajouter une connexion").first.click()
            dialog = page.get_by_role("dialog")
            dialog.locator("#c-name").fill(name)
            dialog.locator("#c-url").fill(WIKI)
            dialog.locator("#c-secret").fill(WIKI_TOKEN)
            dialog.get_by_role("button", name="Ajouter la connexion").click()
            row = page.get_by_role("row").filter(has_text=name)
            expect(row).to_be_visible()
            row.get_by_role("button", name="Tester").click()
            expect(row.get_by_text("Confluence a accepté les identifiants.")).to_be_visible(timeout=30_000)
            # The token never comes back to the browser.
            assert WIKI_TOKEN not in page.request.get(f"{BASE}/api/admin/connections").text()

            # --- user: attach a page found by search ---------------------------------
            page.goto(f"{BASE}/")
            page.get_by_role("button", name="Joindre des pages Confluence ou Notion").click()
            dialog = page.get_by_role("dialog")
            dialog.locator("#attach-connection").select_option(label=f"{name} (Confluence)")
            dialog.get_by_role("tab", name="Rechercher").click()
            dialog.locator("#attach-query").fill("télétravail")
            dialog.locator("#attach-space").fill("RH")
            dialog.get_by_role("button", name="Rechercher").click()
            found = dialog.get_by_role("list", name="Pages trouvées")
            expect(found.get_by_text("Politique de télétravail")).to_be_visible(timeout=30_000)
            expect(found.get_by_role("listitem")).to_have_count(1)  # the IT page is filtered out by the space
            found.get_by_role("checkbox").check()
            dialog.get_by_role("button", name="Joindre la page").click()

            page.wait_for_url(f"{BASE}/c/*", timeout=60_000)
            conversation_id = page.url.rsplit("/", 1)[-1]
            chips = page.get_by_role("list", name="Pages jointes à la conversation")
            expect(chips.get_by_text("Politique de télétravail")).to_be_visible()

            # --- attach a second page by pasting its address -------------------------
            page.get_by_role("button", name="Joindre des pages Confluence ou Notion").click()
            dialog = page.get_by_role("dialog")
            dialog.locator("#attach-connection").select_option(label=f"{name} (Confluence)")
            dialog.get_by_role("tab", name="Par lien").click()  # the dialog reopens on the last mode used
            dialog.locator("#attach-links").fill(f"{WIKI}/spaces/RH/pages/1002/Notes+de+frais")
            dialog.get_by_role("button", name="Joindre la page").click()
            expect(chips.get_by_text("Notes de frais")).to_be_visible(timeout=60_000)

            deadline = time.time() + 180
            while time.time() < deadline:
                sources = api_get(f"/conversations/{conversation_id}/sources")
                if len(sources) == 2 and all(s["status"] in ("ready", "failed") for s in sources):
                    break
                time.sleep(2)
            assert {s["status"] for s in sources} == {"ready"}, sources
            expect(chips.get_by_text("lecture…")).to_have_count(0, timeout=15_000)

            # --- ask, restricted to the attached pages -------------------------------
            page.locator("select").first.select_option(label="Pages jointes uniquement")
            feedback = page.get_by_role("button", name="Réponse utile")
            page.get_by_placeholder("Posez une question sur vos documents", exact=False).fill(
                "Combien de jours de télétravail par semaine sont autorisés chez ACME, et quel jour est obligatoire sur site ?"
            )
            page.get_by_role("button", name="Envoyer la question").click()
            expect(feedback).to_have_count(1, timeout=240_000)
            answer = page.locator(".answer").last.inner_text()
            assert "2" in answer and "mardi" in answer.lower(), answer

            details = api_get("/admin/audit", action="query.ask")["items"][0]["details"]
            cited = [s for s in details["sources"] if s["cited"]]
            assert cited and all(s["filename"] == "Politique de télétravail" for s in cited), details["sources"]
            assert cited[0]["url"].startswith(f"{WIKI}/spaces/RH/pages/1001")

            # The citation opens the page in Confluence, not a local file.
            page.locator(".answer button[aria-label^='Voir la source']").first.click()
            panel = page.get_by_role("complementary", name="Sources de la réponse")
            link = panel.get_by_role("link", name="Ouvrir dans Confluence").first
            expect(link).to_have_attribute("href", f"{WIKI}/spaces/RH/pages/1001/Politique+de+télétravail")
            page.get_by_role("button", name="Fermer les sources").click()

            # --- detach one page ------------------------------------------------------
            page.get_by_role("button", name="Retirer « Notes de frais » de la conversation").click()
            expect(chips.get_by_text("Notes de frais")).to_have_count(0)
            assert [s["title"] for s in api_get(f"/conversations/{conversation_id}/sources")] == ["Politique de télétravail"]

            # --- the pages are not a collection, and go away with the conversation ----
            assert all(not c["name"].startswith("session:") for c in api_get("/collections"))
            attach_events = api_get("/admin/audit", action="source.attach")["items"]
            assert attach_events[0]["details"]["connection"] == name
            resp = page.request.delete(f"{BASE}/api/conversations/{conversation_id}", headers=HEADERS)
            assert resp.ok
            assert page.request.get(f"{BASE}/api/conversations/{conversation_id}/sources").status == 404
            conversation_id = None
            assert api_get("/admin/audit/verify")["valid"] is True
        finally:
            if conversation_id:
                page.request.delete(f"{BASE}/api/conversations/{conversation_id}", headers=HEADERS)
            for connection in page.request.get(f"{BASE}/api/admin/connections").json():
                if connection["name"] == name:
                    page.request.delete(f"{BASE}/api/admin/connections/{connection['id']}", headers=HEADERS)
            browser.close()
