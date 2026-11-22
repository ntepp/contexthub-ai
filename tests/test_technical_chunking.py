"""Technical documents: numbered sections, repeated chapter headers, paragraphs, captions."""

from __future__ import annotations

import pytest

from src.ingestion.chunking import chunk_pages, is_technical, numbered_heading
from src.ingestion.loaders import PageText


@pytest.mark.parametrize(
    "line, expected",
    [
        ("3.2.2 Touches de contrôle et d'utilisation", (9, "3.2.2 Touches de contrôle et d'utilisation")),
        ("3.1 Introduction", (8, "3.1 Introduction")),
        ("4.2.1. Basculement du mode Normal au mode Batterie", (9, "4.2.1 Basculement du mode Normal au mode Batterie")),
        ("50.01 Hz 50.01 Hz 50.01 Hz", None),
        ("45.0 A", None),
        ("1.5 Le système redémarre ensuite.", None),
        ("2.1 Emplacement ........................ 19", None),
        ("2.1 Emplacement 19", None),
        ("3.2.2 touches de contrôle", None),
        ("3 phases en entrée", None),
        ("Article 3.1 du règlement", None),
    ],
)
def test_numbered_headings(line, expected):
    assert numbered_heading(line) == expected


PARAGRAPH = "Le panneau comporte des voyants qui indiquent l etat de fonctionnement et les pannes du systeme. "


def _manual() -> list[PageText]:
    return [
        PageText(1, "Chapitre 3 Ecran LCD\n3.1 Introduction\n" + PARAGRAPH * 3 + "\n\n3.2 Panneau LCD\n" + PARAGRAPH),
        PageText(
            2,
            "Chapitre 3 Ecran LCD\n3.2.1 Voyants LED\n" + PARAGRAPH * 4
            + "\nTableau 3.1 Description des voyants\nVoyant Etat Description\nVert fixe Redresseur normal\n"
            + "Rouge fixe Defaut du redresseur\nEteint Redresseur inoperant",
        ),
        PageText(3, "Chapitre 3 Ecran LCD\nLa page trois prolonge la section des voyants.\n\n3.3 Fenetre de menu\n" + PARAGRAPH * 2),
    ]


def test_a_manual_is_detected_as_technical_and_a_legal_text_is_not():
    assert is_technical(_manual())
    legal = [PageText(1, "Article 1\nObjet\nTexte.\nArticle 2\nDefinitions\n1.1 Champ d application")]
    assert not is_technical(legal)
    assert not is_technical([PageText(None, "# Politique\n## 1.1 Objet\nTexte")])


def test_manual_sections_follow_the_numbering_across_pages():
    chunks = chunk_pages(_manual(), chunk_size=1200, chunk_overlap=150, technical_chunk_size=400)
    paths = [" › ".join(c.section_path) for c in chunks]
    assert "Chapitre 3 — Ecran LCD › 3.1 Introduction" in paths
    assert "Chapitre 3 — Ecran LCD › 3.2 Panneau LCD › 3.2.1 Voyants LED" in paths
    assert "Chapitre 3 — Ecran LCD › 3.3 Fenetre de menu" in paths
    # Page 3 starts with the chapter header again: 3.2.1 goes on, no empty "Chapitre 3" section.
    follow_up = next(c for c in chunks if "La page trois prolonge" in c.body)
    assert follow_up.section == "3.2.1 Voyants LED"
    assert all(len(c.body) <= 400 for c in chunks)
    assert not any("Chapitre 3 Ecran LCD" in c.body for c in chunks[1:])
    # The table starts a paragraph: caption and rows stay together, apart from the text before.
    table = next(c for c in chunks if "Tableau 3.1" in c.body)
    assert table.body.startswith("Tableau 3.1") and "Eteint Redresseur inoperant" in table.body


def test_without_the_technical_size_a_manual_is_cut_as_before():
    chunks = chunk_pages(_manual(), chunk_size=1200, chunk_overlap=150)
    assert {c.section for c in chunks} == {None}


def test_legal_texts_are_cut_exactly_as_before():
    pages = [
        PageText(1, "TITRE VII\nDispositions finales\nArticle 93\nTransposition\n" + "Les Etats membres appliquent. " * 30),
        PageText(2, "Article 94\nAbrogation\n1.1 Exigences generales\n" + "La directive est abrogee. " * 30),
    ]
    before = chunk_pages(pages, chunk_size=300, chunk_overlap=30)
    after = chunk_pages(pages, chunk_size=300, chunk_overlap=30, technical_chunk_size=200)
    assert [(c.content, c.page, c.section_path) for c in after] == [(c.content, c.page, c.section_path) for c in before]
