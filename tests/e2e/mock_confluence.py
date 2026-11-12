"""A stand-in Confluence for demos and end-to-end tests.

    python -m tests.e2e.mock_confluence            # http://127.0.0.1:8765/wiki, token "e2e-token"

It serves the subset of the Confluence REST API v1 that the connector uses (space
listing, CQL search, page by id with ``export_view``), over a tiny HR wiki. Add it as
a connection: address ``http://127.0.0.1:8765/wiki``, no account, token ``e2e-token``.
"""

from __future__ import annotations

import argparse
import re

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query

TOKEN = "e2e-token"
PREFIX = "/wiki"

PAGES = {
    "1001": {
        "title": "Politique de télétravail",
        "space": ("RH", "Ressources humaines"),
        "labels": ["politique", "teletravail"],
        "body": (
            "<h2>Règle générale</h2>"
            "<p>Chez ACME, le télétravail est ouvert à tous les salariés en CDI après leur période d'essai, "
            "à raison de 2 jours par semaine au maximum.</p>"
            "<h2>Jours de présence</h2>"
            "<p>Le mardi est un jour de présence obligatoire sur site pour toutes les équipes.</p>"
            "<h2>Indemnité</h2>"
            "<p>Une indemnité forfaitaire de 2,70 euros par jour télétravaillé est versée avec le salaire.</p>"
        ),
    },
    "1002": {
        "title": "Notes de frais",
        "space": ("RH", "Ressources humaines"),
        "labels": ["politique", "frais"],
        "body": (
            "<h2>Délais</h2>"
            "<p>Les notes de frais sont saisies dans l'outil Expensya avant le 5 du mois suivant et "
            "remboursées sous 30 jours après validation par le responsable.</p>"
            "<h2>Plafonds</h2>"
            "<p>Les repas sont remboursés jusqu'à 22 euros, les nuits d'hôtel jusqu'à 140 euros à Paris "
            "et 110 euros en province.</p>"
        ),
    },
    "2001": {
        "title": "Procédure d'astreinte",
        "space": ("IT", "Informatique"),
        "labels": ["procedure"],
        "body": (
            "<h2>Organisation</h2>"
            "<p>L'astreinte informatique couvre les nuits et les week-ends, par roulement d'une semaine.</p>"
            "<h2>Délai d'intervention</h2>"
            "<p>L'ingénieur d'astreinte prend en charge un incident critique en moins de 30 minutes.</p>"
        ),
    },
}


def _item(page_id: str, with_body: bool = False) -> dict:
    page = PAGES[page_id]
    item = {
        "id": page_id,
        "type": "page",
        "title": page["title"],
        "space": {"key": page["space"][0], "name": page["space"][1]},
        "version": {"when": "2026-10-01T08:00:00.000Z"},
        "_links": {"webui": f"/spaces/{page['space'][0]}/pages/{page_id}/{page['title'].replace(' ', '+')}"},
    }
    if with_body:
        item["body"] = {"export_view": {"value": page["body"]}}
    return item


def _clause(cql: str, field: str) -> str | None:
    match = re.search(rf'{field} = "([^"]*)"', cql)
    return match.group(1) if match else None


def create_app() -> FastAPI:
    app = FastAPI(title="Mock Confluence")

    def check(authorization: str | None) -> None:
        if authorization != f"Bearer {TOKEN}":
            raise HTTPException(status_code=401, detail="Unauthorized")

    @app.get(PREFIX + "/rest/api/space")
    def spaces(authorization: str | None = Header(default=None)) -> dict:
        check(authorization)
        return {"results": [{"key": "RH", "name": "Ressources humaines"}], "size": 1}

    @app.get(PREFIX + "/rest/api/content/search")
    def search(cql: str = Query(default=""), limit: int = 25, authorization: str | None = Header(default=None)) -> dict:
        check(authorization)
        space, label = _clause(cql, "space"), _clause(cql, "label")
        text = re.search(r'title ~ "([^"]*)"', cql)
        words = text.group(1).lower().split() if text else []
        results = []
        for page_id, page in PAGES.items():
            haystack = f"{page['title']} {page['body']}".lower()
            if space and page["space"][0] != space:
                continue
            if label and label not in page["labels"]:
                continue
            if words and not all(word in haystack for word in words):
                continue
            results.append(_item(page_id))
        return {"results": results[:limit], "size": len(results[:limit])}

    @app.get(PREFIX + "/rest/api/content/{page_id}")
    def content(page_id: str, expand: str = "", authorization: str | None = Header(default=None)) -> dict:
        check(authorization)
        if page_id not in PAGES:
            raise HTTPException(status_code=404, detail="Not found")
        return _item(page_id, with_body="body.export_view" in expand)

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")
