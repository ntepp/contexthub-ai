"""Passage context: the document's title and section path head every indexed passage."""

from __future__ import annotations

from src.ingestion.layout import document_title, running_lines, strip_running_lines
from src.ingestion.loaders import PageText
from tests.conftest import grant, upload
from tests.fakes import make_pdf

FOOTER = "AB1234/40XY Manuel d installation"
BODIES = [
    "Deballez l armoire avec precaution et verifiez le contenu du carton.",
    "Fixez l armoire au sol avec les quatre chevilles fournies.",
    "Raccordez les cables d alimentation avant toute mise sous tension.",
    "Pour la mise en service, fermez le sectionneur d entree puis le sectionneur de sortie.",
]


def _pages(bodies=BODIES, footer=FOOTER) -> list[PageText]:
    return [PageText(n, f"{body}\n{n}\n{footer}") for n, body in enumerate(bodies, 1)]


def test_title_comes_from_metadata_then_running_lines_then_first_page():
    pages = _pages()
    running = running_lines(pages)
    assert document_title(pages, running, "Guide d installation AB1234") == "Guide d installation AB1234"
    assert document_title(pages, running, "NI_AB1234_40_XY_FR") == FOOTER  # a file name, not a title
    cover = [PageText(1, "Manuel d'utilisation\nAB1234XY\nNITRAM\nSommaire"), *_pages(footer="Page")[1:]]
    assert document_title(cover, running_lines(cover)) == "Manuel d'utilisation — AB1234XY"
    assert document_title([PageText(1, "Une note d une seule page.\nSuite.")], []) is None
    assert document_title([PageText(1, "Article 1\nObjet"), PageText(2, "Article 2")], []) is None
    assert document_title([PageText(None, "# Politique\nTexte")], []) is None


def test_headings_at_the_top_of_every_page_are_not_running_lines():
    pages = [PageText(n, f"Article {90 + n}\n{body}\n{n}") for n, body in enumerate(BODIES, start=1)]
    assert running_lines(pages) == ["1"]


def test_running_lines_are_removed_at_page_edges_only():
    pages = [PageText(1, "Intro\n" + FOOTER + " est cite dans le texte\n1\n" + FOOTER), *_pages()[1:]]
    stripped = strip_running_lines(pages, running_lines(pages))
    assert stripped[0].text.splitlines() == ["Intro", FOOTER + " est cite dans le texte"]
    assert all(FOOTER not in p.text and not p.text.rstrip().endswith(str(p.number)) for p in stripped[1:])


def _manual_pdf() -> bytes:
    pages = [["# 2.1 Emplacement", body, FOOTER] for body in BODIES]
    pages[3] = ["3.1 Mise en service", BODIES[3], FOOTER]
    return make_pdf(pages)


def test_title_and_sections_head_the_indexed_passages_not_the_citations(admin, member, member_user, collection, store, llm):
    doc_id = upload(admin, collection["id"], "ab.pdf", _manual_pdf()).json()["id"]
    doc = admin.get(f"/documents/{doc_id}").json()
    assert doc["status"] == "ready" and doc["title"] is None  # Document.title: recognised acts only
    contents = [c.content for c in store.chunks.values()]
    assert all(c.startswith(f"[{FOOTER} — AB1234XY") for c in contents)
    assert not any(FOOTER in c.split("\n", 1)[1] for c in contents)

    grant(admin, collection["id"], member_user.id)
    body = member.post(
        "/chat/ask", json={"question": "Comment faire la mise en service du sectionneur ?", "collection_ids": [collection["id"]]}
    ).json()
    assert body["refused"] is False, body
    source = body["sources"][0]
    assert source["filename"] == "ab.pdf" and source["doc_title"] is None
    assert FOOTER not in source["snippet"]
    prompt = str(llm.calls[-1][-1].content)
    assert f"[{FOOTER} — AB1234XY" in prompt  # the LLM reads which document and product it is
