"""Cross-encoder re-ranking: bounded, shared between the question and the planner's
sub-queries, cached, batched, on the right device — and what the diagnostic shows."""

from __future__ import annotations

import asyncio
import json
import sys
import types
import uuid

import pytest

from src import cli
from src.config import get_settings, reset_settings_cache
from src.rag import retrieval
from src.retrieval import rerank
from src.retrieval.store import RetrievedChunk
from tests.conftest import grant, upload
from tests.fakes import FakeCrossEncoder, FakeTorch, is_condense_prompt, is_plan_prompt, make_pdf

# Eight articles about the same subject: many candidates pass the grounding threshold.
_TOPICS = [
    ("Champ d application", "Les contrats derives de gre a gre conclus par une contrepartie financiere sont soumis a l obligation de compensation."),
    ("Contreparties", "Une contrepartie non financiere au dessus du seuil de compensation est soumise a l obligation de compensation des contrats derives."),
    ("Categories", "L autorite determine les categories de contrats derives de gre a gre soumises a l obligation de compensation."),
    ("Delais", "L obligation de compensation des contrats derives de gre a gre s applique apres une periode de mise en oeuvre."),
    ("Exemptions", "Les transactions intragroupe sont exemptees de l obligation de compensation des contrats derives de gre a gre."),
    ("Registre", "Le registre public recense les contrats derives de gre a gre soumis a l obligation de compensation."),
    ("Acces", "Une contrepartie centrale accepte la compensation des contrats derives de gre a gre de facon non discriminatoire."),
    ("Notification", "La contrepartie centrale notifie a l autorite les contrats derives de gre a gre qu elle compense."),
]
ARTICLES = [(f"Article {n}", title, text) for n, (title, text) in enumerate(_TOPICS, start=1)]
CORPUS = make_pdf([list(article) for article in ARTICLES])
QUESTION = "Quels contrats derives de gre a gre sont soumis a l obligation de compensation ?"
SUB_QUERIES = [
    "Quelles contreparties sont soumises a l obligation de compensation des contrats derives ?",
    "Quelles transactions sont exemptees de l obligation de compensation des contrats derives ?",
]


@pytest.fixture
def cross_encoder(monkeypatch):
    torch = FakeTorch()
    FakeCrossEncoder.instances = []
    FakeCrossEncoder.torch = torch
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(CrossEncoder=FakeCrossEncoder))
    monkeypatch.setenv("RERANKER", "cross_encoder")
    reset_settings_cache()
    rerank._cross_encoder.cache_clear()
    rerank.clear_cache()
    yield torch
    monkeypatch.delenv("RERANKER")
    for name in ("RERANK_CANDIDATES", "RERANK_MAX_PAIRS", "RERANKER_DEVICE", "RERANKER_THREADS"):
        monkeypatch.delenv(name, raising=False)
    reset_settings_cache()
    rerank._cross_encoder.cache_clear()
    rerank.clear_cache()


def _settings(monkeypatch, **values) -> None:
    for name, value in values.items():
        monkeypatch.setenv(name, str(value))
    reset_settings_cache()


def _model() -> FakeCrossEncoder:
    assert len(FakeCrossEncoder.instances) == 1  # loaded once
    return FakeCrossEncoder.instances[0]


def _chunks(count: int) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            id=uuid.uuid4(), document_id=uuid.uuid4(), collection_id=uuid.uuid4(),
            content=f"passage {n}" + (" compensation" if n % 3 == 0 else ""), metadata={}, distance=0.1,
        )
        for n in range(count)
    ]


# --------------------------------------------------------------------------- unit


def test_only_the_first_candidates_are_rescored_the_others_keep_their_order(cross_encoder):
    chunks = _chunks(50)
    result = rerank.rerank("compensation", chunks, limit=30)
    assert result.pairs == 30 and result.scored == 30 and result.cached == 0
    assert len(_model().pairs) == 30
    assert {p for _, p in _model().pairs} == {c.content for c in chunks[:30]}
    head, tail = result.chunks[:30], result.chunks[30:]
    assert {c.id for c in head} == {c.id for c in chunks[:30]}
    assert [c.rerank_score for c in head] == sorted((c.rerank_score for c in head), reverse=True)
    assert "compensation" in head[0].content  # the cross-encoder's choice comes first
    assert tail == chunks[30:] and all(c.rerank_score is None for c in tail)
    assert all(c.score == 0.0 for c in chunks)  # the fused score is left as it was


def test_a_pair_already_scored_is_not_scored_again(cross_encoder):
    first = rerank.rerank("compensation", _chunks(10), limit=10)
    again = rerank.rerank("compensation", _chunks(10), limit=10)  # same texts, new ids
    assert (first.scored, again.scored, again.cached) == (10, 0, 10)
    assert [c.rerank_score for c in again.chunks] == [c.rerank_score for c in first.chunks]
    other = rerank.rerank("autre question", _chunks(10), limit=10)
    assert other.scored == 10
    assert len(_model().pairs) == 20


def test_nothing_is_scored_when_off_or_below_two_pairs(cross_encoder, monkeypatch):
    chunks = _chunks(5)
    assert rerank.rerank("q", chunks, limit=1).chunks == chunks
    assert rerank.rerank("q", chunks, limit=0).pairs == 0
    monkeypatch.setenv("RERANKER", "none")
    reset_settings_cache()
    assert rerank.rerank("q", chunks, limit=30).chunks == chunks
    assert FakeCrossEncoder.instances == []  # the model is not even loaded


def test_batched_in_inference_mode(cross_encoder, monkeypatch):
    _settings(monkeypatch, RERANKER_BATCH_SIZE=8)
    rerank.rerank("compensation", _chunks(20), limit=20)
    assert _model().calls == [{"pairs": 20, "batch_size": 8, "inference": True}]
    assert _model().kwargs["max_length"] == 512
    monkeypatch.delenv("RERANKER_BATCH_SIZE")


def test_device_is_cuda_when_available_else_cpu(cross_encoder, monkeypatch):
    assert rerank.resolve_device("auto") == "cpu"
    cross_encoder._cuda = True
    assert rerank.resolve_device("auto") == "cuda"
    assert rerank.resolve_device("") == "cuda"
    assert rerank.resolve_device("cpu") == "cpu"  # forced
    assert rerank.resolve_device("CUDA:1") == "cuda:1"

    rerank._cross_encoder()
    assert _model().kwargs["device"] == "cuda"
    assert cross_encoder.threads is None  # threads are a CPU setting


def test_cpu_threads(cross_encoder, monkeypatch):
    _settings(monkeypatch, RERANKER_DEVICE="cpu", RERANKER_THREADS=3)
    cross_encoder._cuda = True
    rerank._cross_encoder()
    assert _model().kwargs["device"] == "cpu"
    assert cross_encoder.threads == 3


def test_budget_shared_with_the_sub_queries(monkeypatch):
    _settings(monkeypatch, RERANK_CANDIDATES=30, RERANK_MAX_PAIRS=60)
    assert retrieval.question_rerank_limit() == 30
    assert retrieval.sub_query_rerank_limit(30, 3) == 10
    assert retrieval.sub_query_rerank_limit(30, 2) == 15
    assert retrieval.sub_query_rerank_limit(12, 2) == 24  # what the question did not use
    assert retrieval.sub_query_rerank_limit(0, 1) == 30  # never more than RERANK_CANDIDATES
    assert retrieval.sub_query_rerank_limit(60, 3) == 0
    _settings(monkeypatch, RERANK_MAX_PAIRS=20)
    assert retrieval.question_rerank_limit() == 20
    for name in ("RERANK_CANDIDATES", "RERANK_MAX_PAIRS"):
        monkeypatch.delenv(name)
    reset_settings_cache()


# --------------------------------------------------------------------------- whole answer


@pytest.fixture
def corpus(admin, member_user, collection):
    assert upload(admin, collection["id"], "compensation.pdf", CORPUS).status_code == 202
    grant(admin, collection["id"], member_user.id, "viewer")
    return collection


def _planner(messages) -> str:
    if is_plan_prompt(messages):
        return json.dumps({"queries": SUB_QUERIES})
    if is_condense_prompt(messages):
        return QUESTION
    return "Les contrats derives de gre a gre [1]."


def _eligible(query: str, collection_id: str) -> int:
    found = asyncio.run(retrieval.search(query, [uuid.UUID(collection_id)], rerank_limit=0))
    return sum(1 for c in found.candidates if c.chunk.distance <= get_settings().retrieval_max_distance)


def test_one_answer_never_scores_more_than_rerank_max_pairs(cross_encoder, corpus, member, llm, monkeypatch):
    llm.responder = _planner
    eligible = [_eligible(q, corpus["id"]) for q in [QUESTION, *SUB_QUERIES]]
    assert min(eligible) >= 6, eligible  # enough candidates for the bounds to bite

    # Unbounded (what the re-ranker did before): every eligible candidate of every search.
    _settings(monkeypatch, RERANK_CANDIDATES=1000, RERANK_MAX_PAIRS=100000)
    body = member.post("/chat/ask", json={"question": QUESTION}).json()
    assert body["search_queries"] == [QUESTION, *SUB_QUERIES]
    assert len(_model().pairs) == sum(eligible)

    # Bounded: the question takes RERANK_CANDIDATES, the two sub-queries share the rest.
    rerank.clear_cache()
    _model().pairs.clear()
    _settings(monkeypatch, RERANK_CANDIDATES=4, RERANK_MAX_PAIRS=8)
    body = member.post("/chat/ask", json={"question": QUESTION}).json()
    assert body["refused"] is False and body["search_queries"] == [QUESTION, *SUB_QUERIES]
    queries = [q for q, _ in _model().pairs]
    assert queries.count(QUESTION) == 4
    assert queries.count(SUB_QUERIES[0]) == 2 and queries.count(SUB_QUERIES[1]) == 2

    # The same question again: every pair comes from the cache.
    _model().pairs.clear()
    member.post("/chat/ask", json={"question": QUESTION})
    assert _model().pairs == []


def test_sub_queries_keep_their_fused_order_when_the_budget_is_spent(cross_encoder, corpus, member, llm, monkeypatch):
    llm.responder = _planner
    _settings(monkeypatch, RERANK_CANDIDATES=5, RERANK_MAX_PAIRS=5)
    body = member.post("/chat/ask", json={"question": QUESTION}).json()
    assert body["search_queries"] == [QUESTION, *SUB_QUERIES]
    assert [q for q, _ in _model().pairs] == [QUESTION] * 5


def test_the_diagnostic_reranks_exactly_like_the_chat(cross_encoder, corpus, member, capsys, monkeypatch):
    _settings(monkeypatch, RERANK_CANDIDATES=3, AGENTIC_RETRIEVAL="false")
    assert cli.main(["explain-retrieval", "--collection", corpus["name"], "--json", QUESTION]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["settings"]["rerank_candidates"] == 3
    assert report["rerank"]["limit"] == 3 and report["rerank"]["pairs"] == 3 and report["rerank"]["scored"] == 3
    scored = [row for row in report["candidates"] if row["rerank_score"] is not None]
    assert len(scored) == 3
    diagnostic = [row["chunk_index"] for row in report["candidates"] if row["status"] == "kept"]

    # The chat's retrieve node, same question: same pairs (all cached now), same kept order.
    _model().pairs.clear()
    body = member.post("/chat/ask", json={"question": QUESTION}).json()
    assert _model().pairs == []
    convo = member.get(f"/conversations/{body['conversation_id']}").json()
    assert len(convo["messages"][1]["sources"]) >= len(diagnostic)
    found = asyncio.run(retrieval.retrieve_context(QUESTION, [uuid.UUID(corpus["id"])]))
    assert [c.chunk_index for c in found.kept] == diagnostic
    assert found.rerank_pairs == 3 and found.rerank_scored == 0
    monkeypatch.delenv("AGENTIC_RETRIEVAL")
    reset_settings_cache()

    capsys.readouterr()
    assert cli.main(["explain-retrieval", "--collection", corpus["name"], QUESTION]) == 0
    out = capsys.readouterr().out
    assert "Re-classement : 3 paire(s)" in out and " CE " in out
