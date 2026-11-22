"""Product guard: sources about another product are left out, or the answer says so."""

from __future__ import annotations

import json

import pytest

from src import cli
from src.config import get_settings
from src.rag.rag_pipeline import is_refusal
from tests.conftest import grant, upload
from tests.fakes import is_plan_prompt, make_pdf

FOOTER_TS = "TS1001/2TSX Manuel d utilisation"
TS_PAGES = [
    ["Utilisation", "Appuyez sur n importe quel bouton pour mettre l alarme de l onduleur en silence.", FOOTER_TS],
    ["Batteries", "Chargez les batteries de l onduleur pendant cinq heures avant usage.", FOOTER_TS],
    ["Codes", "Le code 07 signale l entretien des batteries de l onduleur.", FOOTER_TS],
]
FOOTER_CN = "CN3010/15/20CNX Manuel d utilisation"
CN_PAGES = [
    ["Ecran", "Selectionnez l icone du bouton silence pour desactiver le buzzer de l onduleur.", FOOTER_CN],
    ["EPO", "Le bouton EPO arrete l onduleur en cas d urgence.", FOOTER_CN],
    ["Batteries", "Installez quarante batteries dans l armoire de l onduleur.", FOOTER_CN],
]
GENERIC = [["Consignes generales", "Coupez l alarme de l onduleur avant toute intervention electrique."]]


@pytest.fixture
def manuals(admin, member_user, collection):
    upload(admin, collection["id"], "ts.pdf", make_pdf(TS_PAGES))
    grant(admin, collection["id"], member_user.id)
    return collection


def _ask(client, collection, question: str) -> dict:
    resp = client.post("/chat/ask", json={"question": question, "collection_ids": [collection["id"]]})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_sources_about_another_product_only_give_an_explicit_refusal(member, manuals, llm):
    calls = len(llm.calls)
    body = _ask(member, manuals, "Mon onduleur CN3010CNX bipe : comment couper l alarme ?")
    no_answer = get_settings().no_answer_text
    assert body["refused"] is True and body["sources"] == []
    assert body["answer"].startswith(no_answer + " ")
    assert body["answer"].endswith("Les sources trouvées concernent TS1001TSX, TS1002TSX, pas CN3010CNX.")
    assert len(llm.calls) == calls  # decided at retrieval, like a refusal: no LLM call


def test_only_the_sources_about_the_asked_product_are_kept(admin, member, manuals, llm):
    upload(admin, manuals["id"], "cn.pdf", make_pdf(CN_PAGES))
    body = _ask(member, manuals, "Mon onduleur CN3015CNX bipe : comment couper l alarme ?")
    assert body["refused"] is False, body
    assert {s["filename"] for s in body["sources"]} == {"cn.pdf"}
    prompt = str(llm.calls[-1][-1].content)
    assert "ts.pdf" not in prompt and "CN3010CNX" in prompt


def test_documents_without_references_are_never_left_out_for_it(admin, member, manuals):
    upload(admin, manuals["id"], "consignes.pdf", make_pdf(GENERIC))
    body = _ask(member, manuals, "Mon onduleur CN3010CNX bipe : comment couper l alarme ?")
    assert body["refused"] is False, body
    assert {s["filename"] for s in body["sources"]} == {"consignes.pdf"}


def test_without_a_reference_nothing_is_filtered(admin, member, manuals):
    upload(admin, manuals["id"], "cn.pdf", make_pdf(CN_PAGES))
    body = _ask(member, manuals, "Mon onduleur bipe : comment couper l alarme ?")
    assert body["refused"] is False
    assert {s["filename"] for s in body["sources"]} == {"ts.pdf", "cn.pdf"}


def test_the_notice_is_streamed_and_off_topic_questions_keep_the_exact_refusal(member, manuals):
    resp = member.post(
        "/chat/stream",
        json={"question": "Mon onduleur CN3010CNX bipe : comment couper l alarme ?", "collection_ids": [manuals["id"]]},
    )
    events = [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]
    tokens = "".join(e["content"] for e in events if e["type"] == "token")
    assert "pas CN3010CNX" in tokens
    assert next(e for e in events if e["type"] == "final")["refused"] is True

    body = _ask(member, manuals, "Quel est le menu de la cantine ?")
    assert body["answer"] == get_settings().no_answer_text and body["refused"] is True


def test_planner_sub_queries_keep_the_question_references(admin, member, manuals, llm):
    upload(admin, manuals["id"], "cn.pdf", make_pdf(CN_PAGES))
    previous = llm.responder

    def responder(messages):
        if is_plan_prompt(messages):
            return '{"queries": ["comment couper l alarme de l onduleur", "batteries de l onduleur"]}'
        return previous(messages)

    llm.responder = responder
    try:
        body = _ask(member, manuals, "Mon onduleur CN3020CNX bipe et ses batteries ?")
    finally:
        llm.responder = previous
    assert len(body["search_queries"]) == 3, body
    assert {s["filename"] for s in body["sources"]} == {"cn.pdf"}


def test_custom_mismatch_text_and_explain_output(member, manuals, capsys, monkeypatch):
    monkeypatch.setattr(get_settings(), "product_mismatch_text", "Sources : {found} ; demandé : {asked}.")
    body = _ask(member, manuals, "Mon onduleur CN3010CNX bipe : comment couper l alarme ?")
    assert body["answer"].endswith("Sources : TS1001TSX, TS1002TSX ; demandé : CN3010CNX.")
    monkeypatch.setattr(get_settings(), "product_mismatch_text", "{inconnu}")
    assert _ask(member, manuals, "Mon onduleur CN3010CNX bipe : comment couper l alarme ?")["answer"].endswith(", pas CN3010CNX.")

    assert cli.main(["explain-retrieval", "--collection", manuals["name"], "--json", "Mon onduleur CN3010CNX bipe : comment couper l alarme ?"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["asked_refs"] == ["CN3010CNX"] and "pas CN3010CNX" in report["notice"]
    assert all(r["reason"].startswith("autre produit (TS1001TSX, TS1002TSX)") for r in report["candidates"]
               if r["product"] == "other" and r["reason"] and "seuil" not in r["reason"])


def test_the_notice_is_not_an_llm_refusal():
    # Normalisation of LLM replies is unchanged: the notice is only ever emitted by retrieval.
    assert is_refusal(get_settings().no_answer_text)


def test_the_benchmark_counts_the_product_notice_as_a_refusal():
    from eval.harness.metrics import refusal_exact_match
    from src.rag.retrieval import product_notice

    no_answer = get_settings().no_answer_text
    notice = product_notice(["TS1001TSX"], ["CN3010CNX"])
    assert refusal_exact_match(notice, no_answer, refused=True)
    assert not refusal_exact_match(notice, no_answer)  # an answer that merely starts like it
    assert refusal_exact_match(no_answer, no_answer)
