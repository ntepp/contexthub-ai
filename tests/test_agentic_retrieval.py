import json
import uuid

from src.config import get_settings
from src.rag.rag_pipeline import interleave, parse_plan
from src.retrieval.store import RetrievedChunk
from tests.conftest import grant, upload
from tests.fakes import is_condense_prompt, is_plan_prompt, make_pdf

# Clearing rules: three articles, so a question that mostly talks about clearing fills a
# small context with them and crowds out the separate transparency document.
CLEARING = make_pdf(
    [
        ["Article 4", "Obligation de compensation",
         "Les contrats derives de gre a gre sont soumis a l obligation de compensation par une contrepartie centrale."],
        ["Article 5", "Procedure de compensation",
         "La compensation des contrats derives de gre a gre est notifiee a l autorite pour chaque categorie de contrats derives."],
        ["Article 6", "Registre de compensation",
         "Le registre public recense les categories de contrats derives soumises a l obligation de compensation."],
    ]
)
TRANSPARENCY = make_pdf(
    [
        ["Article 8", "Transparence pre negociation",
         "Les plates-formes de negociation rendent publiques les regles de transparence pre negociation et post negociation."],
    ]
)
QUESTION = (
    "Quels contrats derives de gre a gre sont soumis a l obligation de compensation des contrats derives, "
    "et ou sont definies les regles de transparence ?"
)
SUB_QUERIES = [
    "Quels contrats derives de gre a gre sont soumis a l obligation de compensation ?",
    "Ou sont definies les regles de transparence pre negociation des plates-formes de negociation ?",
]


def _chunk(name: str) -> RetrievedChunk:
    return RetrievedChunk(
        id=uuid.uuid5(uuid.NAMESPACE_DNS, name), document_id=uuid.uuid4(), collection_id=uuid.uuid4(),
        content=name, metadata={}, distance=0.1,
    )


def test_parse_plan_keeps_the_question_first_and_dedupes():
    reply = 'Voici le plan : {"queries": ["Q", "  sous   question A ", "sous question a", "B", "C", "D"]}'
    assert parse_plan(reply, "Q", max_queries=3) == ["Q", "sous question A", "B", "C"]


def test_parse_plan_falls_back_to_the_question():
    for reply in ("Le 3 janvier 2017 [1].", '{"queries": "pas une liste"}', "{cassé", '{"queries": [1, null, ""]}', ""):
        assert parse_plan(reply, "Q", max_queries=3) == ["Q"]


def test_a_single_planned_query_is_a_paraphrase_not_a_split():
    # Seen with gpt-4o on "Combien de jours de congé pour son mariage ?": one reworded query.
    assert parse_plan('{"queries": ["Nombre de jours de congé pour un mariage"]}', "Q", max_queries=3) == ["Q"]
    assert parse_plan('{"queries": ["Q", "q"]}', "Q", max_queries=3) == ["Q"]


def test_interleave_gives_every_search_a_place():
    a1, a2, a3, b1, c1 = (_chunk(n) for n in ("a1", "a2", "a3", "b1", "c1"))
    assert interleave([[a1, a2, a3], [a1, b1], [c1]], limit=4) == [a1, c1, a2, b1]
    assert interleave([[], []], limit=4) == []


def _collection_with_two_documents(admin, member_user, collection):
    assert upload(admin, collection["id"], "compensation.pdf", CLEARING).status_code == 202
    assert upload(admin, collection["id"], "transparence.pdf", TRANSPARENCY).status_code == 202
    grant(admin, collection["id"], member_user.id, "viewer")


def _planner(messages) -> str:
    if is_plan_prompt(messages):
        return json.dumps({"queries": SUB_QUERIES})
    if is_condense_prompt(messages):
        return QUESTION
    return "Les contrats derives de gre a gre [1] ; les regles de transparence [2]."


def test_multi_part_question_reaches_both_documents(admin, member, member_user, collection, llm, monkeypatch):
    _collection_with_two_documents(admin, member_user, collection)
    monkeypatch.setattr(get_settings(), "retrieval_top_k", 2)

    monkeypatch.setattr(get_settings(), "agentic_retrieval", False)
    single = member.post("/chat/ask", json={"question": QUESTION}).json()
    assert {s["filename"] for s in single["sources"]} == {"compensation.pdf"}
    assert single["search_queries"] == [QUESTION]

    monkeypatch.setattr(get_settings(), "agentic_retrieval", True)
    llm.responder = _planner
    body = member.post("/chat/ask", json={"question": QUESTION}).json()
    assert body["refused"] is False
    assert {s["filename"] for s in body["sources"]} == {"compensation.pdf", "transparence.pdf"}
    assert body["search_queries"] == [QUESTION, *SUB_QUERIES]
    # The planner is called before the answer, and the answer prompt holds both documents.
    assert is_plan_prompt(llm.calls[-2])
    assert "transparence" in str(llm.calls[-1][-1].content)

    audit = admin.get("/admin/audit", params={"action": "query.ask"}).json()["items"][0]
    assert audit["details"]["search_queries"] == [QUESTION, *SUB_QUERIES]


def test_planner_tokens_are_not_streamed(admin, member, member_user, collection, llm):
    _collection_with_two_documents(admin, member_user, collection)
    llm.responder = _planner
    resp = member.post("/chat/stream", json={"question": QUESTION})
    tokens = "".join(
        json.loads(line[6:])["content"]
        for line in resp.text.splitlines()
        if line.startswith("data: ") and json.loads(line[6:])["type"] == "token"
    )
    assert "queries" not in tokens
    assert tokens.strip().startswith("Les contrats derives")


def test_unanswerable_question_skips_the_planner(admin, member, member_user, collection, llm):
    _collection_with_two_documents(admin, member_user, collection)
    llm.responder = _planner
    body = member.post("/chat/ask", json={"question": "Recette du gateau au chocolat facile"}).json()
    assert body["refused"] is True
    assert llm.calls == []


def test_planning_can_be_disabled(admin, member, member_user, collection, llm, monkeypatch):
    _collection_with_two_documents(admin, member_user, collection)
    monkeypatch.setattr(get_settings(), "agentic_retrieval", False)
    llm.responder = _planner
    member.post("/chat/ask", json={"question": QUESTION})
    assert len(llm.calls) == 1 and not is_plan_prompt(llm.calls[0])
