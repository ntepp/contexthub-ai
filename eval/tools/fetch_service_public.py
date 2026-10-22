"""Snapshot Service-Public.fr fact sheets as Markdown, for a reproducible evaluation corpus.

Service-Public.fr content is published by the DILA under the Etalab 2.0 licence
(Licence Ouverte): it may be copied and redistributed with attribution, which each
snapshot carries in its header. Only the main content is kept, with its structure:
tabs (CDI / CDD / Intérim) and accordion questions become Markdown headings, so the
chunker's section paths read "Période d'essai › CDI › Quelle est la durée… › Cadre".

    python -m eval.tools.fetch_service_public eval/corpora/rh F2278 F1643 ...

Re-running it refreshes the corpus; check the golden answers against the new text
before committing (the sheets are updated when the law changes).
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import time
import unicodedata
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup

BASE_URL = "https://www.service-public.gouv.fr/particuliers/vosdroits/"
_BLOCKS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr"]
_JUNK = (
    "button:not(.fr-accordion__btn), nav, script, style, .rs_skip, [aria-hidden=true], "
    ".fr-sr-only, [role=tooltip], [role=tablist]"
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def fetch(sheet_id: str) -> str:
    request = urllib.request.Request(BASE_URL + sheet_id, headers={"User-Agent": "ContextHub-eval/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed https host
        return response.read().decode("utf-8")


def to_markdown(html: str, sheet_id: str, extracted_on: str) -> tuple[str, str]:
    """(title, markdown) of one fact sheet."""
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("article", class_="article")
    if article is None:
        raise ValueError(f"{sheet_id}: pas de fiche dans la page (identifiant inconnu ?)")

    # Tabs become a heading at the top of their panel, one level below the heading the
    # group belongs to ("## Question ?" → "### Cas général"). The level is computed once
    # per group: inside it, the closest previous heading of the 2nd tab is in the 1st.
    for tabs in article.select(".fr-tabs"):
        previous = tabs.find_previous(["h1", "h2", "h3", "h4", "h5"])
        level = min(int(previous.name[1]) + 1, 6) if previous is not None else 2
        for panel in tabs.find_all(attrs={"role": "tabpanel"}):
            if panel.find_parent(class_="fr-tabs") is not tabs:
                continue  # nested group, handled on its own
            tab = article.find(id=panel.get("aria-labelledby"))
            if tab is not None:
                heading = soup.new_tag(f"h{level}")
                heading.string = _clean(tab.get_text(" "))
                panel.insert(0, heading)
    # Accordion titles are h3 > button and survive; tooltips, toolbars and tab lists go.
    for junk in article.select(_JUNK):
        junk.decompose()

    title = _clean(article.find("h1").get_text(" "))
    updated = soup.find(attrs={"data-test": "date-maj"})
    updated = _clean(updated.get_text(" ")) if updated else "date de mise à jour non indiquée"
    lines = [
        f"# {title}",
        "",
        f"Source : Service-Public.fr (DILA), {BASE_URL}{sheet_id} — {updated}. Licence Etalab 2.0 "
        f"(Licence Ouverte). Instantané extrait le {extracted_on} pour le banc d'évaluation ContextHub.",
        "",
    ]
    captured: list = []
    for element in article.find_all(_BLOCKS):
        if element.name == "h1" or any(parent in captured for parent in element.parents):
            continue
        text = _clean(element.get_text(" "))
        if not text or text.lower().startswith(("vérifié le", "publié par", "partager", "imprimer")):
            continue
        captured.append(element)
        if element.name[0] == "h":
            lines += ["", "#" * int(element.name[1]) + " " + text, ""]
        elif element.name == "li":
            lines.append(f"- {text}")
        elif element.name == "tr":
            # Unclosed <th> tags nest the next cell inside: read each cell's own paragraphs.
            cells = [
                _clean(" ".join(p.get_text(" ") for p in cell.find_all("p", recursive=False)) or cell.get_text(" "))
                for cell in element.find_all(["th", "td"])
            ]
            lines.append("| " + " | ".join(cells) + " |")
        else:
            lines += [text, ""]
    return title, re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def slug(title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_title).strip("-")[:60]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path)
    parser.add_argument("sheets", nargs="+", help="identifiants de fiche, ex. F2278")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    for index, sheet_id in enumerate(args.sheets):
        if index:
            time.sleep(1)  # be polite with the public service
        title, markdown = to_markdown(fetch(sheet_id), sheet_id, today)
        path = args.output / f"{sheet_id}-{slug(title)}.md"
        path.write_text(markdown, encoding="utf-8")
        print(f"{path} ({len(markdown)} caractères) — {title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
