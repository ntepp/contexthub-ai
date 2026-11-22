"""Procedure answers: the drafting rule of ``generate`` (numbered steps, interface elements named
from their description, conditions and limits, missing steps said, citations, refusals kept)."""

from __future__ import annotations

import re

from src.config import get_settings
from src.rag.rag_pipeline import SYSTEM_PROMPT, system_prompt
from tests.conftest import grant, upload
from tests.fakes import is_condense_prompt, is_plan_prompt, make_pdf

MANUAL = make_pdf(
    [
        ["2.1 Touches du panneau",
         "Tableau 2 Touches : ACCUEIL retour a la page d accueil ; SUIVANT page suivante ; VALIDER confirmation."],
        ["2.2 Ecran de controle",
         "Tableau 3 Icones : cloche barree = bouton Silence du signal sonore.",
         "Selectionnez l icone pour couper le signal sonore declenche par les alertes mineures."],
    ]
)
PROCEDURE = "Comment couper le signal sonore declenche par les alertes mineures ?"


def _rule() -> str:
    return re.search(r"^7\. .*?(?=\n\d+\. |\Z)", SYSTEM_PROMPT, re.S | re.M).group(0)


def test_the_procedure_rule_is_part_of_the_generic_rules():
    rule = _rule()
    for expected in ("numbered steps", "every relevant source", "key, button, icon", "incompletely",
                     "a table or a legend", "conditions and limits", "only part of the procedure",
                     "never fill it in", "cite its sources", "do not turn a simple factual answer"):
        assert expected in rule, expected
    prompt = system_prompt()
    # The refusal rule keeps precedence, with the exact sentence; nothing corpus-specific.
    assert prompt.index("1. If the sources do not explicitly support") < prompt.index("7. When the question")
    assert get_settings().no_answer_text in prompt
    for specific in ("onduleur", "ups", "buzzer", "mute", "nitram", "alarm"):
        assert specific not in prompt.lower(), specific


def test_a_procedure_question_is_answered_under_the_rule_with_its_citations(
    admin, member, member_user, collection, llm, monkeypatch
):
    monkeypatch.setattr(get_settings(), "agentic_retrieval", False)
    monkeypatch.setattr(get_settings(), "query_rewrite", False)
    upload(admin, collection["id"], "panneau.pdf", MANUAL)
    grant(admin, collection["id"], member_user.id)

    def responder(messages):
        assert not is_condense_prompt(messages) and not is_plan_prompt(messages)
        return (
            "1. Appuyez sur ACCUEIL pour revenir a la page d accueil [1].\n"
            "2. Avec SUIVANT, allez jusqu a l icone Silence (cloche barree) [1].\n"
            "3. Confirmez avec VALIDER [1].\n"
            "Le bouton Silence ne coupe que les alertes mineures [1]."
        )

    llm.responder = responder
    body = member.post("/chat/ask", json={"question": PROCEDURE, "collection_ids": [collection["id"]]}).json()
    assert body["refused"] is False, body
    system, human = llm.calls[-1][0].content, llm.calls[-1][-1].content
    assert _rule() in system
    assert "Tableau 2 Touches" in human and "Tableau 3 Icones" in human  # both passages are read
    assert body["answer"].startswith("1. Appuyez sur ACCUEIL")
    assert body["sources"] and all(s["cited"] for s in body["sources"])


def test_a_procedure_the_sources_do_not_support_is_still_refused(
    admin, member, member_user, collection, llm, monkeypatch
):
    monkeypatch.setattr(get_settings(), "agentic_retrieval", False)
    monkeypatch.setattr(get_settings(), "query_rewrite", False)
    upload(admin, collection["id"], "panneau.pdf", MANUAL)
    grant(admin, collection["id"], member_user.id)
    llm.responder = lambda messages: get_settings().no_answer_text
    body = member.post("/chat/ask", json={"question": PROCEDURE, "collection_ids": [collection["id"]]}).json()
    assert body["refused"] is True and body["sources"] == []
    assert body["answer"] == get_settings().no_answer_text
