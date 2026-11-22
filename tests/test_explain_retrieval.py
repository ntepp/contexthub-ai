"""Operator diagnostic: python -m src.cli explain-retrieval."""

import asyncio
import json

import pytest

from src import cli
from src.config import reset_settings_cache
from src.rag.rag_pipeline import retrieve
from tests.conftest import upload
from tests.fakes import make_pdf

PDF = make_pdf(
    [
        ["Article 93", "Transposition", "Les Etats membres appliquent ces dispositions a partir du 3 janvier 2017."],
        ["Article 94", "Abrogation", "Les Etats membres appliquent la directive abrogee jusqu au 2 janvier 2017."],
        ["Article 4", "Definitions", "Un systeme organise de negociation est un systeme multilateral."],
    ]
)
QUESTION = "A partir de quelle date les Etats membres appliquent ces dispositions ?"


@pytest.fixture
def indexed(admin, collection):
    assert upload(admin, collection["id"], "directive.pdf", PDF).status_code == 202
    return collection


@pytest.fixture
def top_k_one(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_TOP_K", "1")
    monkeypatch.setenv("RETRIEVAL_MAX_DISTANCE", "0.7")
    reset_settings_cache()
    yield
    monkeypatch.delenv("RETRIEVAL_TOP_K")
    monkeypatch.setenv("RETRIEVAL_MAX_DISTANCE", "0.75")
    reset_settings_cache()


def _explain(capsys, *args) -> tuple[int, str]:
    code = cli.main(["explain-retrieval", *args])
    return code, capsys.readouterr().out


def test_every_candidate_comes_with_its_fate(indexed, capsys, top_k_one):
    code, out = _explain(capsys, "--collection", indexed["name"], "--json", "--excerpt", "120", QUESTION)
    assert code == 0
    report = json.loads(out)
    assert report["settings"]["retrieval_top_k"] == 1
    rows = report["candidates"]
    kept = [r for r in rows if r["status"] == "kept"]
    assert len(kept) == 1 and kept[0]["final_rank"] == 1
    assert kept[0]["section"] == "Article 93 — Transposition" and kept[0]["page"] == 1
    assert kept[0]["document"] == "directive.pdf"
    assert kept[0]["vector_rank"] is not None and kept[0]["lexical_rank"] is not None
    assert kept[0]["lexical_score"] > 0
    assert "3 janvier 2017" in kept[0]["excerpt"]
    excluded = [r for r in rows if r["status"] == "excluded"]
    assert excluded and all(r["reason"] and r["final_rank"] is None for r in excluded)
    reasons = " | ".join(r["reason"] for r in excluded)
    assert "au-delà du top_k (rang 2 > 1)" in reasons
    assert "> seuil 0.70" in reasons


def test_table_output_and_parity_with_the_chat(indexed, capsys, top_k_one):
    code, out = _explain(capsys, "--collection", indexed["id"], QUESTION)
    assert code == 0
    assert "candidats, 1 retenus" in out
    assert "retenu" in out and "exclu" in out and "au-delà du top_k" in out
    assert "sans reformulation" in out  # the operator knows condense/plan are not replayed

    state = asyncio.run(retrieve({"question": QUESTION, "collection_ids": [indexed["id"]]}))
    report = json.loads(_explain(capsys, "--collection", indexed["name"], "--json", QUESTION)[1])
    kept = [r["document_id"] + str(r["chunk_index"]) for r in report["candidates"] if r["status"] == "kept"]
    assert kept == [str(c.document_id) + str(c.chunk_index) for c in state["context"]]


def test_unknown_or_session_collection_is_refused(indexed, capsys, db, admin_user):
    from src.db.models import Collection, Conversation

    assert _explain(capsys, "--collection", "inconnue", QUESTION)[0] == 1
    conversation = Conversation(user_id=admin_user.id)
    db.add(conversation)
    db.flush()
    db.add(Collection(name="pages jointes", conversation_id=conversation.id))
    db.commit()
    assert _explain(capsys, "--collection", "pages jointes", QUESTION)[0] == 1
