"""End-to-end scenario through the real UI (Playwright + an installed Chrome).

Covers agentic retrieval, definition chunking and refusal on the HR corpus, against a
running stack (API + worker + frontend + LLM). Skipped unless E2E_BASE_URL is set:

    E2E_BASE_URL=http://localhost:5173 E2E_EMAIL=admin@demo.fr E2E_PASSWORD=... pytest -m e2e

It creates a throw-away collection "E2E RH <time>", imports two HR sheets plus a small
glossary, asks three questions and deletes the collection. Answers come from the
configured LLM, so the checks look for facts and structure, not exact wording.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

BASE = os.getenv("E2E_BASE_URL")
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not BASE, reason="E2E_BASE_URL non défini")]

CORPUS = Path(__file__).resolve().parents[2] / "eval" / "corpora" / "rh"
TRIAL = CORPUS / "F1643-periode-d-essai-pour-un-salarie.md"
TERMINATION = CORPUS / "F19030-rupture-conventionnelle-d-un-salarie-du-secteur-prive.md"
GLOSSARY = """# Guide RH ACME

## Glossaire

- **Astreinte** : période pendant laquelle le salarié, sans être sur son lieu de travail, doit pouvoir intervenir pour l'entreprise. Chez ACME, chaque semaine d'astreinte donne droit à une prime de 150 euros.
- **Forfait jours** : décompte du temps de travail en jours sur l'année, sans référence horaire. Chez ACME, le forfait est de 214 jours travaillés par an.
- **Prime de cooptation** : prime de 800 euros versée au salarié qui recommande un candidat recruté en CDI, après la période d'essai de ce dernier.
- **Télétravail** : travail effectué hors des locaux de l'employeur de façon volontaire. Chez ACME, 2 jours par semaine au maximum.
"""
HEADERS = {"X-Requested-With": "contexthub"}


def test_agentic_retrieval_definitions_and_refusal_through_the_ui(tmp_path):
    sync_api = pytest.importorskip("playwright.sync_api")
    expect = sync_api.expect
    glossary = tmp_path / "glossaire-rh-acme.md"
    glossary.write_text(GLOSSARY, encoding="utf-8")
    files = [TRIAL, TERMINATION, glossary]
    no_answer = os.getenv("NO_ANSWER_TEXT", "L'information n'est pas disponible dans la base de connaissances actuelle.")

    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv("E2E_BROWSER_CHANNEL", "chrome"), headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        def ask(question: str) -> None:
            feedback = page.get_by_role("button", name="Réponse utile")
            before = feedback.count()
            page.get_by_placeholder("Posez une question sur vos documents", exact=False).fill(question)
            page.get_by_role("button", name="Envoyer la question").click()
            expect(feedback).to_have_count(before + 1, timeout=240_000)  # this answer is complete

        def last_audit() -> dict:
            return page.request.get(f"{BASE}/api/admin/audit", params={"action": "query.ask"}).json()["items"][0]["details"]

        page.goto(f"{BASE}/login")
        page.fill("#email", os.environ["E2E_EMAIL"])
        page.fill("#password", os.environ["E2E_PASSWORD"])
        page.locator("button[type=submit]").click()
        page.wait_for_url(f"{BASE}/")

        name = f"E2E RH {time.strftime('%H%M%S')}"
        page.goto(f"{BASE}/documents")
        page.get_by_role("button", name="Créer une collection").click()
        page.fill("#col-name", name)
        page.get_by_role("dialog").get_by_role("button", name="Créer la collection").click()
        expect(page.get_by_role("navigation", name="Collections").get_by_text(name)).to_be_visible()
        collection = next(c for c in page.request.get(f"{BASE}/api/collections").json() if c["name"] == name)
        try:
            page.locator("input[type=file]").set_input_files([str(f) for f in files])
            deadline = time.time() + 300
            while time.time() < deadline:
                docs = page.request.get(f"{BASE}/api/collections/{collection['id']}/documents").json()
                if len(docs) == len(files) and all(d["status"] in ("ready", "failed") for d in docs):
                    break
                time.sleep(2)
            assert {d["status"] for d in docs} == {"ready"}, docs

            page.goto(f"{BASE}/")
            page.locator("select").first.select_option(label=name)

            # Agentic retrieval: one search per part, both sheets cited.
            ask("Quelle est la durée maximale de la période d'essai initiale d'un cadre en CDI, "
                "et quel est le délai de rétractation après la signature d'une rupture conventionnelle ?")
            answer = page.locator(".answer").last.inner_text()
            assert "4 mois" in answer and "15 jours" in answer, answer
            details = last_audit()
            assert len(details["search_queries"]) >= 3, details["search_queries"]  # question + 2 parts
            assert {TRIAL.name, TERMINATION.name} <= {s["filename"] for s in details["sources"] if s["cited"]}
            page.locator(".answer button[aria-label^='Voir la source']").first.click()
            expect(page.get_by_role("complementary", name="Sources de la réponse")).to_be_visible()
            page.get_by_role("button", name="Fermer les sources").click()

            # Definition chunking: the term is its own passage; a one-topic question is searched once.
            ask("Qu'est-ce qu'une prime de cooptation ?")
            assert "800" in page.locator(".answer").last.inner_text()
            details = last_audit()
            assert len(details["search_queries"]) == 1, details["search_queries"]
            assert "Prime de cooptation" in [s["section"] for s in details["sources"] if s["cited"]]
            page.locator(".answer button[aria-label^='Voir la source']").last.click()
            panel = page.get_by_role("complementary", name="Sources de la réponse")
            expect(panel.get_by_text("Glossaire › Prime de cooptation", exact=False).first).to_be_visible()
            page.get_by_role("button", name="Fermer les sources").click()

            # Refusal: an HR question the documents do not answer.
            page.get_by_placeholder("Posez une question sur vos documents", exact=False).fill(
                "Combien de jours de congé un salarié obtient-il pour son mariage ?")
            page.get_by_role("button", name="Envoyer la question").click()
            expect(page.get_by_text(no_answer).last).to_be_visible(timeout=240_000)
            page.wait_for_timeout(1000)  # the audit event is written once the stream ends
            details = last_audit()
            assert details["refused"] is True and details["sources"] == []
        finally:
            page.request.delete(f"{BASE}/api/collections/{collection['id']}", headers=HEADERS)
            browser.close()
