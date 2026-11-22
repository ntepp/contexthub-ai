"""Product references: extraction (factored ranges), storage, search and favouring."""

from __future__ import annotations

import asyncio

import pytest

from src.config import reset_settings_cache
from src.ingestion.layout import running_lines
from src.ingestion.loaders import PageText
from src.ingestion.references import (
    document_references,
    extract_references,
    keys_match,
    matching,
    question_references,
    reference_key,
)
from src.rag import retrieval
from tests.conftest import upload
from tests.fakes import make_pdf


@pytest.mark.parametrize(
    "text, expected",
    [
        ("US30010/15/20/30/40CNG Manuel d'utilisation",
         ["US30010CNG", "US30015CNG", "US30020CNG", "US30030CNG", "US30040CNG"]),
        ("Modèles US10001/2/1.5/3TSARM", ["US10001TSARM", "US10002TSARM", "US10001.5TSARM", "US10003TSARM"]),
        ("Pack BP10001.5/2TSA-RM", ["BP10001.5TSA-RM", "BP10002TSA-RM"]),
        ("US10001TSARM US10001.5TSARM US10001TSARM.", ["US10001TSARM", "US10001.5TSARM"]),
        ("Interfaces RS232/RS485, norme IEC62040-1-1.", ["RS232", "RS485", "IEC62040-1-1"]),
        ("Modèle RT-2000 et RT2000", ["RT-2000"]),  # same key: one reference
        ("Directive 2014/65/UE, article 93, section 3.2.2", []),
        ("Bornes J5, J10, +24V, BCB_DRV, UPS1, L4, 20kVA, 50.01 Hz, A320", []),
        ("Voir www.exemple.fr ou le chapitre IV", []),
        ("Le modèle us30010cng", []),  # documents: capitals only
    ],
)
def test_references_found_in_documents(text, expected):
    assert extract_references(text) == expected


def test_questions_may_cite_references_in_lower_case():
    assert question_references("j'ai une alarme sur mon onduleur Nitram us30010cng qui bipe") == ["US30010CNG"]
    assert question_references("Comment couper le bip ?") == []
    assert question_references("article L1237-19 du code du travail, covid19") == []


def test_matching_allows_a_missing_letter_suffix_only():
    assert reference_key("rt-2000 ") == "RT2000" and reference_key("US10001.5TSARM") == "US10001.5TSARM"
    assert keys_match("US30010", "US30010CNG") and keys_match("US30010CNG", "US30010")
    assert not keys_match("US10001", "US10001.5TSARM")  # another model, not a suffix
    assert not keys_match("US3001", "US30010CNG")
    assert matching(["US30050CNG"], ["US30010CNG", "US30015CNG", "US30040CNG"]) == []
    assert matching(["us30015cng"], ["US30010CNG", "US30015CNG"]) == ["US30015CNG"]


def test_document_references_put_the_prominent_ones_first():
    every, heading = document_references(
        ["", "Manuel US10001/2TSARM"],
        ["Manuel US10001/2TSARM", "Carte RMCARD205 et RS232", "Port RS232", "Port RS232 encore"],
    )
    assert heading == ["US10001TSARM", "US10002TSARM"]
    assert every == ["US10001TSARM", "US10002TSARM", "RS232", "RMCARD205"]


def test_running_headers_and_footers_are_found_whatever_the_page_number():
    bodies = ["Deballage du carton.", "Pose de l armoire.", "Raccordement des cables.", "Mise en service.", "Essais."]
    pages = [
        PageText(n, f"Chapitre 1 Installation\n{body}\n{n}\nAB1234/40XY Manuel")
        for n, body in enumerate(bodies, start=1)
    ]
    found = running_lines(pages)
    assert "AB1234/40XY Manuel" in found and "Chapitre 1 Installation" in found and "1" in found
    assert not any("armoire" in line or "Essais" in line for line in found)
    assert running_lines([PageText(None, "AB1234XY")]) == []  # unpaginated text: no running lines


# ---------------------------------------------------------------------------
# Ingestion and retrieval
# ---------------------------------------------------------------------------

FOOTER_A = "ZX2000/10/20KT Manuel utilisateur"
PAGES_A = [
    ["Securite", "Lisez les consignes de securite avant toute installation de l armoire.", FOOTER_A],
    ["Ecran", "Pour desactiver le buzzer declenche par les alarmes generales, selectionnez l icone silence.", FOOTER_A],
    ["Batteries", "Les batteries doivent etre remplacees par un technicien agree.", FOOTER_A],
]
PAGES_B = [
    ["QW5000TS Manuel", "L onduleur bipe : appuyez sur n importe quel bouton pour mettre l alarme en silence."],
]
QUESTION = "Mon onduleur ZX2010KT bipe, comment eteindre l alarme ?"


@pytest.fixture
def two_manuals(admin, collection):
    a = upload(admin, collection["id"], "zx.pdf", make_pdf(PAGES_A)).json()["id"]
    b = upload(admin, collection["id"], "qw.pdf", make_pdf(PAGES_B)).json()["id"]
    return collection, a, b


def test_references_are_stored_on_the_document_and_head_its_passages(admin, two_manuals, store):
    _, a, b = two_manuals
    doc = admin.get(f"/documents/{a}").json()
    assert doc["product_refs"] == ["ZX2000KT", "ZX2010KT", "ZX2020KT"]
    assert admin.get(f"/documents/{b}").json()["product_refs"] == ["QW5000TS"]
    contents = [c.content for c in store.chunks.values() if str(c.document_id) == a]
    assert contents and all(c.startswith("[ZX2000KT, ZX2010KT, ZX2020KT") for c in contents)


def _search(collection_id: str, question: str) -> retrieval.Retrieval:
    import uuid

    return asyncio.run(retrieval.search(question, [uuid.UUID(collection_id)]))


def test_a_cited_reference_favours_the_documents_that_carry_it(two_manuals):
    collection, a, b = two_manuals
    plain = _search(collection["id"], "Mon onduleur bipe, comment eteindre l alarme ?")
    assert str(plain.kept[0].document_id) == b  # the other product's wording is closer
    assert plain.asked_refs == [] and all(c.product is None for c in plain.candidates)

    cited = _search(collection["id"], QUESTION)
    assert cited.asked_refs == ["ZX2010KT"]
    assert str(cited.kept[0].document_id) == a
    rows = retrieval.describe(cited)
    first = rows[0]
    assert first["product"] == "match" and first["matched_refs"] == ["ZX2010KT"]
    assert any(r["product"] == "other" and r["document_id"] == b for r in rows)


def test_reference_search_reaches_documents_outside_the_candidate_pool(admin, two_manuals, monkeypatch):
    collection, a, _ = two_manuals
    for n in range(3):  # other products' manuals, worded like the question
        page = [f"QW{6000 + n}TS Manuel", f"Si l onduleur bipe, l alarme {n} se coupe avec le bouton silence."]
        upload(admin, collection["id"], f"qw{n}.pdf", make_pdf([page]))
    monkeypatch.setenv("RETRIEVAL_CANDIDATES", "1")
    reset_settings_cache()
    try:
        plain = _search(collection["id"], "Mon onduleur bipe, comment eteindre l alarme ?")
        assert a not in {str(c.chunk.document_id) for c in plain.candidates}
        cited = _search(collection["id"], QUESTION)
        reached = [c for c in cited.candidates if str(c.chunk.document_id) == a]
        assert reached and all(c.via_reference and c.product == "match" for c in reached)
        assert str(cited.kept[0].document_id) == a
    finally:
        monkeypatch.delenv("RETRIEVAL_CANDIDATES")
        reset_settings_cache()


def test_an_unknown_reference_changes_nothing_but_the_classification(two_manuals):
    collection, _, _ = two_manuals
    plain = _search(collection["id"], "Mon onduleur bipe, comment eteindre l alarme ?")
    other = _search(collection["id"], "Mon onduleur AB9999 bipe, comment eteindre l alarme ?")
    assert other.asked_refs == ["AB9999"]
    assert [c.document_id for c in other.kept] == [c.document_id for c in plain.kept]
    assert all(c.product == "other" for c in other.candidates)


def test_migration_adds_and_removes_the_product_references_column():
    import importlib.util

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location("m4258", "alembic/versions/69ab4258_document_product_references.py")
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "69ab4257"

    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE hub_documents (id VARCHAR(36) PRIMARY KEY, filename VARCHAR(500))"))
        context = MigrationContext.configure(conn)
        with Operations.context(context):
            migration.upgrade()
            assert "product_refs" in {c["name"] for c in sa.inspect(conn).get_columns("hub_documents")}
            migration.downgrade()
            assert {c["name"] for c in sa.inspect(conn).get_columns("hub_documents")} == {"id", "filename"}
