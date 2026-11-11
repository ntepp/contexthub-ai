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


# --------------------------------------------------------------- definitions

EU_DEFINITIONS = [
    PageText(1, "\n".join([
        "Article 2",
        "Définitions",
        "Aux fins du présent règlement, on entend par:",
        "1) «contrepartie centrale», une personne morale qui s'interpose entre les contreparties",
        "à des contrats négociés sur un ou plusieurs marchés financiers;",
        "2) «référentiel central», une personne morale qui collecte et conserve de manière",
        "centralisée les enregistrements relatifs aux produits dérivés au sens de l'article 4, point",
        "7), de ladite directive;",
    ])),
    PageText(2, "\n".join([
        "3) «dispositif de régime de retraite»:",
        "a) les institutions de retraite professionnelle au sens de la directive;",
        "b) les activités de fourniture de retraite professionnelle des institutions;",
        "Article 3",
        "Transactions intragroupe",
        "Une transaction intragroupe est un contrat conclu avec une autre contrepartie du même groupe.",
    ])),
]


def test_definitions_are_split_one_per_chunk_with_the_introduction():
    chunks = chunk_pages(EU_DEFINITIONS, doc_label="EMIR — Règlement (UE) n° 648/2012")
    definitions = [c for c in chunks if c.section_path[0] == "Article 2 — Définitions"]
    assert [c.section for c in definitions] == [
        "1) contrepartie centrale",
        "2) référentiel central",
        "3) dispositif de régime de retraite",
    ]
    for chunk in definitions:
        assert chunk.body.startswith("Aux fins du présent règlement, on entend par:")
    # A wrapped "7), de ladite directive" stays inside definition 2; sub-items stay with 3.
    assert "7), de ladite directive" in definitions[1].body
    assert "b) les activités" in definitions[2].body
    assert definitions[2].page == 2
    assert definitions[1].content.startswith(
        "[EMIR — Règlement (UE) n° 648/2012 › Article 2 — Définitions › 2) référentiel central]"
    )
    # The next article is chunked normally.
    assert chunks[-1].section_path == ["Article 3 — Transactions intragroupe"]


def test_paragraph_number_is_not_a_definition():
    pages = [PageText(1, "\n".join([
        "Article 4",
        "Définitions",
        "1. Aux fins de la présente directive, on entend par:",
        "1) «entreprise d'investissement», toute personne morale qui fournit des services d'investissement;",
        "2) «client», toute personne physique ou morale à laquelle un service est fourni;",
    ]))]
    sections = [c.section for c in chunk_pages(pages)]
    assert sections == ["1) entreprise d'investissement", "2) client"]


def test_markdown_glossary_is_split_by_term():
    pages = [PageText(1, "\n".join([
        "## Glossaire",
        "- **Astreinte** : période pendant laquelle le salarié doit pouvoir intervenir pour l'entreprise.",
        "- **Forfait jours** : décompte du temps de travail en jours sur l'année, sans référence horaire.",
        "- Télétravail : travail effectué hors des locaux de l'employeur de façon volontaire.",
    ]))]
    assert [c.section for c in chunk_pages(pages)] == ["Astreinte", "Forfait jours", "Télétravail"]


def test_numbered_steps_outside_definitions_are_not_split():
    pages = [PageText(1, "\n".join([
        "## Réinitialiser l'accès VPN",
        "1) Ouvrir le portail d'administration avec un compte habilité par le support.",
        "2) Sélectionner le prestataire puis révoquer le certificat existant du poste.",
    ]))]
    chunks = chunk_pages(pages)
    assert len(chunks) == 1 and chunks[0].section == "Réinitialiser l'accès VPN"


def test_definition_splitting_can_be_disabled():
    chunks = chunk_pages(EU_DEFINITIONS, split_definitions=False)
    assert [c.section for c in chunks] == ["Article 2 — Définitions", "Article 3 — Transactions intragroupe"]
