import json

import pytest

from src.config import get_settings
from src.rag.rag_pipeline import cited_indices, is_refusal
from tests.conftest import grant, upload
from tests.fakes import is_condense_prompt, make_pdf

PDF = make_pdf(
    [
        ["Article 93", "Transposition",
         "Les Etats membres appliquent ces dispositions a partir du 3 janvier 2017."],
        ["Article 4", "Definitions",
         "Un systeme organise de negociation est un systeme multilateral autre qu un marche reglemente."],
    ]
)
QUESTION = "A partir de quelle date les Etats membres appliquent ces dispositions ?"


@pytest.fixture
def indexed(admin, member_user, collection):
    assert upload(admin, collection["id"], "directive.pdf", PDF).status_code == 202
    grant(admin, collection["id"], member_user.id, "viewer")
    return collection


def _events(resp) -> list[dict]:
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def test_grounded_answer_with_citations(member, indexed, admin):
    resp = member.post("/chat/ask", json={"question": QUESTION, "collection_ids": [indexed["id"]]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["refused"] is False
    assert "[1]" in body["answer"]
    assert body["confidence_metric"] > 0
    first = body["sources"][0]
    assert first["index"] == 1 and first["cited"] is True
    assert first["filename"] == "directive.pdf"
    assert first["page"] == 1
    assert first["section"] == "Article 93 — Transposition"
    assert "3 janvier 2017" in first["snippet"]
    assert "uploads" not in json.dumps(body["sources"])  # no server paths leak

    convo = member.get(f"/conversations/{body['conversation_id']}").json()
    assert [m["role"] for m in convo["messages"]] == ["user", "assistant"]
    assert convo["messages"][1]["sources"][0]["document_id"] == first["document_id"]

    events = admin.get("/admin/audit", params={"action": "query.ask"}).json()["items"]
    assert events[0]["details"]["question"] == QUESTION
    assert events[0]["details"]["answer"] == body["answer"]
    assert events[0]["details"]["sources"][0]["page"] == 1


def test_off_topic_question_is_refused_without_calling_the_llm(member, indexed, llm):
    resp = member.post("/chat/ask", json={"question": "Recette du gateau au chocolat facile"})
    body = resp.json()
    assert body["refused"] is True
    assert body["answer"] == get_settings().no_answer_text
    assert body["sources"] == []
    assert body["confidence_metric"] == 0.0
    assert llm.calls == []


def test_llm_refusal_is_normalised_and_drops_sources(member, indexed, llm):
    llm.responder = lambda messages: "Malheureusement : " + get_settings().no_answer_text
    body = member.post("/chat/ask", json={"question": QUESTION}).json()
    assert body["refused"] is True
    assert body["answer"] == get_settings().no_answer_text
    assert body["sources"] == []


def test_follow_up_question_is_condensed_with_history(member, indexed, llm):
    first = member.post("/chat/ask", json={"question": QUESTION}).json()
    llm.calls.clear()
    llm.responder = lambda m: QUESTION if is_condense_prompt(m) else "Le 3 janvier 2017 [1]."
    second = member.post(
        "/chat/ask", json={"question": "Et pour la transposition ?", "conversation_id": first["conversation_id"]}
    ).json()
    assert second["standalone_question"] == QUESTION
    assert is_condense_prompt(llm.calls[0])
    assert "3 janvier 2017" in str(llm.calls[0][-1].content)  # previous answer is in the history
    # The answer prompt contains the sources and the user's question, never the transcript.
    assert "Conversation:" not in str(llm.calls[1])
    assert len(member.get(f"/conversations/{first['conversation_id']}").json()["messages"]) == 4


def test_streaming_emits_tokens_then_final_result(member, indexed):
    resp = member.post("/chat/stream", json={"question": QUESTION})
    assert resp.status_code == 200
    events = _events(resp)
    assert events[0]["type"] == "meta"
    tokens = "".join(e["content"] for e in events if e["type"] == "token")
    final = next(e for e in events if e["type"] == "final")
    assert events[-1]["type"] == "done"
    assert tokens.strip() == final["answer"]
    assert final["sources"][0]["cited"] is True
    assert final["message_id"] and final["conversation_id"] == events[0]["conversation_id"]


def test_streaming_refusal_is_not_an_empty_bubble(member, indexed):
    events = _events(member.post("/chat/stream", json={"question": "Recette du gateau au chocolat facile"}))
    tokens = "".join(e["content"] for e in events if e["type"] == "token")
    assert tokens == get_settings().no_answer_text
    assert next(e for e in events if e["type"] == "final")["refused"] is True


def test_condense_tokens_are_not_streamed_to_the_user(member, indexed, llm):
    first = member.post("/chat/ask", json={"question": QUESTION}).json()
    llm.responder = lambda m: "SECRET-REWRITE " + QUESTION if is_condense_prompt(m) else "Reponse [1]."
    events = _events(member.post("/chat/stream", json={"question": "Et ensuite ?", "conversation_id": first["conversation_id"]}))
    tokens = "".join(e["content"] for e in events if e["type"] == "token")
    assert "SECRET-REWRITE" not in tokens


def test_model_allow_list(member, indexed):
    assert member.post("/chat/ask", json={"question": QUESTION, "model": "openai:gpt-4o"}).status_code == 400
    ok = member.post("/chat/ask", json={"question": QUESTION, "model": "anthropic:claude-sonnet-5-5"})
    assert ok.status_code == 200 and ok.json()["model"] == "anthropic:claude-sonnet-5-5"
    models = member.get("/models").json()
    assert models["default"] == "ollama:fake-model"
    flags = {m["id"]: m["is_local"] for m in models["models"]}
    assert flags == {"ollama:fake-model": True, "anthropic:claude-sonnet-5-5": False}


def test_feedback_is_stored(member, indexed):
    body = member.post("/chat/ask", json={"question": QUESTION}).json()
    assert member.post(f"/messages/{body['message_id']}/feedback", json={"value": -1, "comment": "incomplet"}).status_code == 200
    message = member.get(f"/conversations/{body['conversation_id']}").json()["messages"][1]
    assert message["feedback"] == -1


def test_v0_ask_alias_by_collection_name(member, indexed):
    resp = member.post("/ask", json={"question": QUESTION, "collection": indexed["name"]})
    assert resp.status_code == 200 and resp.json()["refused"] is False


def test_ask_rate_limit(member, indexed, monkeypatch):
    monkeypatch.setattr(get_settings(), "ask_rate_limit_per_minute", 2)
    assert member.post("/chat/ask", json={"question": QUESTION}).status_code == 200
    assert member.post("/chat/ask", json={"question": QUESTION}).status_code == 200
    assert member.post("/chat/ask", json={"question": QUESTION}).status_code == 429


def test_refusal_and_citation_parsing():
    no = get_settings().no_answer_text
    assert is_refusal(no)
    assert is_refusal(f"« {no} »")
    assert is_refusal("")
    assert not is_refusal("La date est le 3 janvier 2017 [1].")
    assert cited_indices("Oui [1], et aussi [2, 3] puis [4][5].") == {1, 2, 3, 4, 5}
