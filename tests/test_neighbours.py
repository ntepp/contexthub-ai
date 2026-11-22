"""Neighbouring passages joined around the kept ones (RETRIEVAL_NEIGHBOURS, RETRIEVAL_CONTEXT_CHARS)."""

from __future__ import annotations

import asyncio
import uuid

from src.config import get_settings
from src.rag import retrieval
from src.retrieval.store import ChunkRecord, RetrievedChunk
from tests.conftest import grant, upload
from tests.fakes import HashEmbeddings, make_pdf

COLLECTION = uuid.uuid4()
WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
DOC = uuid.uuid4()


def _fill(store, pages=(1, 2, 3, 4, 5, 6), document=DOC, collection=COLLECTION, size=100) -> list[ChunkRecord]:
    records = [
        ChunkRecord(
            document_id=document,
            collection_id=collection,
            chunk_index=index,
            content=f"passage {WORDS[index]} " + "x" * (size - 10 - len(WORDS[index])),
            metadata={"filename": "manuel.pdf", "page": page},
            embedding=HashEmbeddings().embed_query(f"passage {WORDS[index]}"),
        )
        for index, page in enumerate(pages)
    ]
    store.add_chunks(records)
    return records


def _anchor(record: ChunkRecord) -> RetrievedChunk:
    return RetrievedChunk(
        id=record.id,
        document_id=record.document_id,
        collection_id=record.collection_id,
        content=record.content,
        metadata=dict(record.metadata),
        distance=0.2,
        chunk_index=record.chunk_index,
    )


def _expand(anchors, collections=(COLLECTION,)) -> list[RetrievedChunk]:
    return asyncio.run(retrieval.expand(anchors, HashEmbeddings().embed_query("passage"), list(collections)))


def _indexes(chunks) -> list[int]:
    return [c.chunk_index for c in chunks]


def test_each_kept_passage_comes_with_the_passages_around_it(store):
    records = _fill(store)
    context = _expand([_anchor(records[2])])
    assert _indexes(context) == [1, 2, 3]
    assert [c.neighbour_of for c in context] == [records[2].id, None, records[2].id]
    assert all(0.0 <= c.distance <= 2.0 for c in context)


def test_depth_budget_and_contiguity(store, monkeypatch):
    records = _fill(store)
    monkeypatch.setattr(get_settings(), "retrieval_neighbours", 2)
    assert _indexes(_expand([_anchor(records[2])])) == [0, 1, 2, 3, 4]
    # Budget: the anchors (never dropped) plus two neighbours, served best anchor first.
    monkeypatch.setattr(get_settings(), "retrieval_context_chars", 400)
    assert _indexes(_expand([_anchor(records[4]), _anchor(records[1])])) == [3, 4, 5, 1]
    monkeypatch.setattr(get_settings(), "retrieval_context_chars", 50)
    assert _indexes(_expand([_anchor(records[4]), _anchor(records[1])])) == [4, 1]
    monkeypatch.setattr(get_settings(), "retrieval_neighbours", 0)
    monkeypatch.setattr(get_settings(), "retrieval_context_chars", 12000)
    assert _indexes(_expand([_anchor(records[2])])) == [2]


def test_adjacent_kept_passages_share_their_neighbours_in_document_order(store):
    records = _fill(store)
    context = _expand([_anchor(records[2]), _anchor(records[4])])
    assert _indexes(context) == [1, 2, 3, 4, 5]


def test_neighbours_stay_in_the_document_on_next_pages_and_in_the_searched_collections(store):
    records = _fill(store, pages=(1, 2, 9, 10))
    assert _indexes(_expand([_anchor(records[3])])) == [2, 3]
    _fill(store, document=uuid.uuid4())  # same positions, another document
    assert _indexes(_expand([_anchor(records[1])])) == [0, 1]  # page 9 is not next to page 2
    assert _indexes(_expand([_anchor(records[1])], collections=[uuid.uuid4()])) == [1]


def test_explain_lists_the_neighbours_joined_to_the_context(store, monkeypatch):
    records = _fill(store)
    monkeypatch.setattr(get_settings(), "retrieval_top_k", 1)
    found = asyncio.run(retrieval.retrieve_context("passage delta", [COLLECTION]))
    assert _indexes(found.kept) == [3] and _indexes(found.context) == [2, 3, 4]
    rows = retrieval.describe(found)
    assert [(r["status"], r["context_position"]) for r in rows[:3]] == [("kept", 2), ("neighbour", 1), ("neighbour", 3)]
    assert rows[1]["reason"] == "voisin du passage retenu n° 1"
    assert {r["chunk_index"] for r in rows if r["status"] == "excluded"} == {0, 1, 5}
    assert records


def test_the_chat_cites_neighbours_and_keeps_its_confidence_on_the_kept_passages(
    admin, member, member_user, collection, llm, monkeypatch
):
    filler = "Controlez le cablage du tableau electrique puis le serrage des bornes. "
    steps = [
        ["Article 7", "Mise en service", *[f"Etape 1.{n} : {filler}" for n in range(12)]],
        [*[f"Etape 2.{n} : fermez le sectionneur de sortie avant la mise sous tension." for n in range(3)],
         *[f"Etape 2.{n} : {filler}" for n in range(3, 12)]],
        [*[f"Etape 3.{n} : {filler}" for n in range(12)]],
    ]
    upload(admin, collection["id"], "procedure.pdf", make_pdf(steps))
    grant(admin, collection["id"], member_user.id)
    monkeypatch.setattr(get_settings(), "retrieval_top_k", 1)
    question = "Quand fermer le sectionneur de sortie avant la mise sous tension ?"
    body = member.post("/chat/ask", json={"question": question, "collection_ids": [collection["id"]]}).json()
    assert body["refused"] is False, body
    sources = body["sources"]
    kept = [s for s in sources if not s["neighbour"]]
    assert len(kept) == 1
    neighbours = [s for s in sources if s["neighbour"]]
    assert neighbours and all(abs(s["page"] - kept[0]["page"]) <= 1 for s in neighbours)
    assert body["confidence_metric"] == round(max(0.0, 1.0 - kept[0]["distance"]), 3)
    prompt = str(llm.calls[-1][-1].content)
    assert "fermez le sectionneur" in prompt
    assert all(s["snippet"][:40] in prompt for s in sources)

    off_topic = member.post(
        "/chat/ask", json={"question": "Quel est le menu de la cantine ?", "collection_ids": [collection["id"]]}
    ).json()
    assert off_topic["refused"] is True and off_topic["sources"] == []
