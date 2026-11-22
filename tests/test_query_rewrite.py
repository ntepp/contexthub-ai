"""Query rewording (QUERY_REWRITE): the planner's call also rewords the question in the
documents' vocabulary; the rewordings are searched on top of the question and fused with its
ranking, without changing the question the answering LLM reads nor the refusal invariants."""

from __future__ import annotations

import json
import uuid

import pytest

from src.config import get_settings
from src.rag.rag_pipeline import PLAN_PROMPT, fuse, parse_plan, parse_rewrites, plan_prompt
from src.retrieval.store import RetrievedChunk
from tests.conftest import grant, upload
from tests.fakes import is_condense_prompt, is_plan_prompt, make_pdf

ALARM = make_pdf([["Le signal bip de l alarme retentit quand une alerte mineure survient sur l appareil."]])
BUZZER = make_pdf([["Desactivation du buzzer : selectionnez la touche Silence puis confirmez avec Valider."]])
QUESTION = "Comment arreter le bip de l alarme ?"
REWRITE = "Desactivation du buzzer avec la touche Silence"
ANSWER = "1. Selectionnez la touche Silence [2].\n2. Confirmez avec Valider [2]."


def _chunk(name: str) -> RetrievedChunk:
    return RetrievedChunk(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, name), document_id=uuid.uuid4(), collection_id=uuid.uuid4(),
        content=name, metadata={}, distance=0.1,
    )


def test_parse_rewrites_dedupes_against_the_searches_and_caps():
    reply = json.dumps({"queries": ["Q", "A"], "rewrites": ["q", " a ", "R1", "r1", "R2", "R3", 7, ""]})
    assert parse_rewrites(reply, ["Q", "A"], max_rewrites=2) == ["R1", "R2"]
    assert parse_rewrites(reply, ["Q", "A"], max_rewrites=0) == []
    for broken in ("pas de JSON", "{cassé", '{"rewrites": "pas une liste"}', '{"queries": ["Q"]}', "", None):
        assert parse_rewrites(broken, ["Q"], max_rewrites=2) == []
    assert parse_rewrites(json.dumps({"rewrites": ["x" * 501]}), ["Q"], max_rewrites=2) == []


def test_the_prompt_without_rewording_is_the_planner_prompt(monkeypatch):
    plain = plan_prompt("Q ?", split=True, rewrite=False)
    assert plain == PLAN_PROMPT.format(max_queries=get_settings().agentic_max_queries, question="Q ?")
    both = plan_prompt("Q ?", split=True, rewrite=True)
    assert both.startswith("You plan the searches") and '"queries"' in both and '"rewrites"' in both
    assert "no added fact" in both and "Do not answer the question" in both and both.endswith("Question: Q ?")
    only = plan_prompt("Q ?", split=False, rewrite=True)
    assert '"rewrites"' in only and '"queries"' not in only
    # Generic: no corpus, product or customer words.
    for specific in ("onduleur", "buzzer", "mute", "nitram", "alarm"):
        assert specific not in both.lower()


def test_fuse_puts_what_several_formulations_found_first_and_keeps_the_question_order_on_ties():
    a, b, c, d = (_chunk(n) for n in "abcd")
    assert fuse([[a, b, c], [c, d]], limit=8) == [c, a, b, d]
    assert fuse([[a, b], [d]], limit=8) == [a, d, b]  # d (rank 1) ties with a (rank 1): a first
    assert fuse([[a, b, c], [c, d]], limit=2) == [c, a]
    assert fuse([[a], []], limit=8) == [a]


def test_rewording_and_sub_queries_do_not_repeat_each_other():
    reply = json.dumps({"queries": ["sous A", "sous B"], "rewrites": ["sous a", "Q", "autre"]})
    queries = parse_plan(reply, "Q", max_queries=3)
    assert queries == ["Q", "sous A", "sous B"]
    assert parse_rewrites(reply, queries, max_rewrites=2) == ["autre"]


@pytest.fixture
def manuals(admin, member_user, collection):
    upload(admin, collection["id"], "alarme.pdf", ALARM)
    upload(admin, collection["id"], "buzzer.pdf", BUZZER)
    grant(admin, collection["id"], member_user.id)
    return collection


def _responder(rewrites, queries=None):
    def respond(messages):
        if is_plan_prompt(messages):
            return json.dumps({"queries": queries or [QUESTION], "rewrites": rewrites})
        if is_condense_prompt(messages):
            return QUESTION
        return ANSWER

    return respond


def _ask(member, collection, question=QUESTION) -> dict:
    return member.post("/chat/ask", json={"question": question, "collection_ids": [collection["id"]]}).json()


def test_a_rewording_reaches_the_passage_written_in_the_documents_words(admin, member, manuals, llm, monkeypatch):
    monkeypatch.setattr(get_settings(), "agentic_retrieval", False)
    llm.responder = _responder([])
    before = _ask(member, manuals)
    assert before["refused"] is False and {s["filename"] for s in before["sources"]} == {"alarme.pdf"}

    llm.responder = _responder([REWRITE])
    llm.calls.clear()
    body = _ask(member, manuals)
    assert body["refused"] is False, body
    assert {s["filename"] for s in body["sources"]} == {"alarme.pdf", "buzzer.pdf"}
    assert body["search_queries"] == [QUESTION] and body["search_rewrites"] == [REWRITE]
    # One planner call (rewording only: sub-queries are off), then the answer.
    assert len(llm.calls) == 2 and is_plan_prompt(llm.calls[0]) and '"queries"' not in str(llm.calls[0][-1].content)
    # The answering LLM reads the question as asked, with the passage found by the rewording.
    human = str(llm.calls[-1][-1].content)
    assert human.endswith(f"Question: {QUESTION}") and REWRITE not in human and "Desactivation du buzzer" in human
    # The confidence stays the one of what the question itself found.
    assert body["confidence_metric"] == before["confidence_metric"]
    audit = admin.get("/admin/audit", params={"action": "query.ask"}).json()["items"][0]
    assert audit["details"]["search_rewrites"] == [REWRITE]


def test_a_rewording_never_turns_a_refusal_into_an_answer(member, manuals, llm):
    llm.responder = _responder(["Desactivation du buzzer touche Silence Valider"])
    body = _ask(member, manuals, "Quel est le menu de la cantine ce midi ?")
    assert body["refused"] is True and body["sources"] == [] and body["search_rewrites"] == []
    assert body["answer"] == get_settings().no_answer_text
    assert llm.calls == []  # no planner call, no rewording searched


def test_a_rewording_that_finds_nothing_under_the_threshold_adds_nothing(member, manuals, llm, monkeypatch):
    monkeypatch.setattr(get_settings(), "agentic_retrieval", False)
    llm.responder = _responder(["Recette du gateau au chocolat"])
    body = _ask(member, manuals)
    assert body["search_rewrites"] == ["Recette du gateau au chocolat"]
    assert {s["filename"] for s in body["sources"]} == {"alarme.pdf"}


def test_rewording_can_be_disabled(member, manuals, llm, monkeypatch):
    monkeypatch.setattr(get_settings(), "query_rewrite", False)
    llm.responder = _responder([REWRITE])
    body = _ask(member, manuals)
    assert body["search_rewrites"] == [] and {s["filename"] for s in body["sources"]} == {"alarme.pdf"}
    plans = [call for call in llm.calls if is_plan_prompt(call)]
    assert len(plans) == 1 and '"rewrites"' not in str(plans[0][-1].content)
    monkeypatch.setattr(get_settings(), "agentic_retrieval", False)
    llm.calls.clear()
    _ask(member, manuals)
    assert len(llm.calls) == 1 and not is_plan_prompt(llm.calls[0])  # no planner call at all


def test_rewordings_are_searched_with_the_sub_queries_and_streamed_tokens_stay_the_answer(
    member, manuals, llm
):
    llm.responder = _responder([REWRITE, QUESTION], queries=["bip de l alarme", "signal sonore de l appareil"])
    body = _ask(member, manuals)
    assert body["search_queries"] == [QUESTION, "bip de l alarme", "signal sonore de l appareil"]
    assert body["search_rewrites"] == [REWRITE]
    assert "buzzer.pdf" in {s["filename"] for s in body["sources"]}
    resp = member.post("/chat/stream", json={"question": QUESTION, "collection_ids": [manuals["id"]]})
    tokens = "".join(
        json.loads(line[6:])["content"]
        for line in resp.text.splitlines()
        if line.startswith("data: ") and json.loads(line[6:])["type"] == "token"
    )
    assert tokens.strip() == ANSWER.strip() and "rewrites" not in tokens
    events = [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]
    result = next(e for e in events if e["type"] == "final")
    assert result["search_rewrites"] == [REWRITE]
