from src.ingestion.chunking import chunk_pages, detect_heading
from src.ingestion.loaders import PageText


def test_detects_eu_legal_headings():
    assert detect_heading("Article 93") == (6, "Article 93", True)
    assert detect_heading("Article premier") == (6, "Article premier", True)
    assert detect_heading("TITRE PREMIER") == (2, "Titre premier", True)
    assert detect_heading("CHAPITRE II") == (3, "Chapitre II", True)
    assert detect_heading("SECTION A") == (4, "Section A", True)
    assert detect_heading("Article 4 - Définitions") == (6, "Article 4 — Définitions", False)
    assert detect_heading("## Procédure") == (2, "Procédure", False)


def test_ignores_references_inside_sentences():
    assert detect_heading("conformément à l'article 4, paragraphe 1") is None
    assert detect_heading("Article 4, paragraphe 1, point 23), de la directive 2014/65/UE précise que") is None


def test_chunks_never_straddle_two_articles():
    pages = [
        PageText(1, "Article 1\nObjet\n" + "Le present reglement fixe des regles. " * 10),
        PageText(2, "Article 2\nDefinitions\n" + "Aux fins du present reglement on entend par. " * 10),
    ]
    chunks = chunk_pages(pages, chunk_size=300, chunk_overlap=30)
    for chunk in chunks:
        assert not ("fixe des regles" in chunk.body and "on entend par" in chunk.body)
    assert {c.section for c in chunks} == {"Article 1 — Objet", "Article 2 — Definitions"}
    assert {c.page for c in chunks if c.section.startswith("Article 2")} == {2}


def test_long_article_is_split_and_pages_follow_the_text():
    pages = [PageText(10, "Article 7\nProcedure\n" + "Phrase de la page dix. " * 40),
             PageText(11, "Phrase de la page onze. " * 40)]
    chunks = chunk_pages(pages, chunk_size=400, chunk_overlap=40)
    assert len(chunks) > 2
    assert all(c.section == "Article 7 — Procedure" for c in chunks)
    assert chunks[0].page == 10 and chunks[-1].page == 11


def test_noise_chunks_are_dropped():
    pages = [PageText(1, "12 34 56 78 90 12 34 56 78 90 12 34 56 78\n\n" + "Texte utile et lisible pour le lecteur. " * 3)]
    chunks = chunk_pages(pages, chunk_size=60, chunk_overlap=0)
    assert chunks and all("12 34 56" not in c.body for c in chunks)
