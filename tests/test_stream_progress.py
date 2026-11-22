"""/chat/stream: progress events and keep-alive comments while the answer is prepared,
without changing what forms the answer."""

from __future__ import annotations

import asyncio
import json
import sys
import time
import types

import pytest

from src.api.routes.chat import SSE_HEARTBEAT, with_heartbeat
from src.config import get_settings, reset_settings_cache
from src.retrieval import embeddings as embeddings_module
from src.retrieval import rerank
from tests.conftest import grant, upload
from tests.fakes import FakeCrossEncoder, FakeTorch, HashEmbeddings, is_condense_prompt, is_plan_prompt, make_pdf

PDF = make_pdf(
    [
        ["Article 93", "Transposition",
         "Les Etats membres appliquent ces dispositions a partir du 3 janvier 2017."],
        ["Article 4", "Definitions",
         "Un systeme organise de negociation est un systeme multilateral autre qu un marche reglemente."],
    ]
)
QUESTION = "A partir de quelle date les Etats membres appliquent ces dispositions ?"
OFF_TOPIC = "Recette du gateau au chocolat facile"


@pytest.fixture
def indexed(admin, member_user, collection):
    assert upload(admin, collection["id"], "directive.pdf", PDF).status_code == 202
    grant(admin, collection["id"], member_user.id, "viewer")
    return collection


def _frames(resp) -> list[str]:
    return [frame for frame in resp.text.split("\n\n") if frame]


def _events(resp) -> list[dict]:
    return [json.loads(frame[6:]) for frame in _frames(resp) if frame.startswith("data: ")]


def _steps(events: list[dict]) -> list[str]:
    return [e["step"] for e in events if e["type"] == "status"]


class SlowEmbeddings(HashEmbeddings):
    """Embedding of the question takes ``delay`` seconds (in a worker thread, like a real one)."""

    def __init__(self, delay: float) -> None:
        self.delay = delay

    def embed_query(self, text: str) -> list[float]:
        time.sleep(self.delay)
        return super().embed_query(text)


@pytest.fixture
def fast_heartbeat(monkeypatch):
    monkeypatch.setenv("SSE_HEARTBEAT_SECONDS", "0.05")
    reset_settings_cache()
    yield
    monkeypatch.delenv("SSE_HEARTBEAT_SECONDS")
    reset_settings_cache()


# --------------------------------------------------------------------------- status events


def test_steps_are_announced_before_the_answer_and_are_not_part_of_it(member, indexed):
    events = _events(member.post("/chat/stream", json={"question": QUESTION}))
    assert events[0]["type"] == "meta"
    # First turn: nothing to condense. The planner runs (agentic retrieval is on by default).
    assert _steps(events) == ["retrieve", "plan", "generate"]
    kinds = [e["type"] for e in events]
    assert kinds.index("token") > max(i for i, k in enumerate(kinds) if k == "status")
    tokens = "".join(e["content"] for e in events if e["type"] == "token")
    final = next(e for e in events if e["type"] == "final")
    assert tokens.strip() == final["answer"]
    assert events[-1]["type"] == "done"


def test_follow_up_announces_condense_and_without_planner_no_plan(member, indexed, llm, monkeypatch):
    first = member.post("/chat/ask", json={"question": QUESTION}).json()
    monkeypatch.setattr(get_settings(), "agentic_retrieval", False)
    llm.responder = lambda m: QUESTION if is_condense_prompt(m) else "Le 3 janvier 2017 [1]."
    events = _events(
        member.post("/chat/stream", json={"question": "Et ensuite ?", "conversation_id": first["conversation_id"]})
    )
    assert _steps(events) == ["condense", "retrieve", "generate"]


def test_no_step_follows_a_short_circuited_refusal(member, indexed, llm):
    events = _events(member.post("/chat/stream", json={"question": OFF_TOPIC}))
    assert _steps(events) == ["retrieve"]
    tokens = "".join(e["content"] for e in events if e["type"] == "token")
    assert tokens == get_settings().no_answer_text
    assert next(e for e in events if e["type"] == "final")["refused"] is True
    assert llm.calls == []


def test_planner_output_never_leaks_through_status_events(member, indexed, llm):
    llm.responder = lambda m: (
        json.dumps({"queries": ["SECRET-PLAN date des Etats membres", "SECRET-PLAN transposition directive"]})
        if is_plan_prompt(m)
        else "Le 3 janvier 2017 [1]."
    )
    resp = member.post("/chat/stream", json={"question": QUESTION})
    tokens = "".join(e["content"] for e in _events(resp) if e["type"] == "token")
    assert "SECRET-PLAN" not in tokens
    assert all(set(e) == {"type", "step"} for e in _events(resp) if e["type"] == "status")


def test_rerank_is_announced_when_the_cross_encoder_runs(admin, member, member_user, collection, monkeypatch):
    from tests.test_rerank import CORPUS
    from tests.test_rerank import QUESTION as CLEARING_QUESTION

    assert upload(admin, collection["id"], "compensation.pdf", CORPUS).status_code == 202
    grant(admin, collection["id"], member_user.id, "viewer")
    monkeypatch.setitem(sys.modules, "torch", FakeTorch())
    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(CrossEncoder=FakeCrossEncoder))
    monkeypatch.setattr(get_settings(), "reranker", "cross_encoder")
    monkeypatch.setattr(get_settings(), "agentic_retrieval", False)
    rerank._cross_encoder.cache_clear()
    rerank.clear_cache()
    try:
        events = _events(member.post("/chat/stream", json={"question": CLEARING_QUESTION}))
    finally:
        rerank._cross_encoder.cache_clear()
        rerank.clear_cache()
    assert _steps(events) == ["retrieve", "rerank", "generate"]


# --------------------------------------------------------------------------- heartbeats


def test_heartbeats_keep_a_slow_answer_alive(member, indexed, fast_heartbeat):
    embeddings_module.set_embeddings_override(SlowEmbeddings(0.4))
    resp = member.post("/chat/stream", json={"question": QUESTION})
    frames = _frames(resp)
    pings = [i for i, frame in enumerate(frames) if frame + "\n\n" == SSE_HEARTBEAT]
    assert len(pings) >= 3  # ~0.4 s silent search, 0.05 s interval (and again in the planner)
    # Sent while retrieval runs: after the retrieve status, before the first token.
    first_token = next(i for i, f in enumerate(frames) if f.startswith("data: ") and '"token"' in f)
    retrieve = next(i for i, f in enumerate(frames) if '"step": "retrieve"' in f)
    assert any(retrieve < i < first_token for i in pings)
    # The answer is unchanged and nothing else than events and comments is on the wire.
    assert all(f.startswith(("data: ", ": ")) for f in frames)
    events = _events(resp)
    final = next(e for e in events if e["type"] == "final")
    assert "".join(e["content"] for e in events if e["type"] == "token").strip() == final["answer"]
    assert final["refused"] is False and events[-1]["type"] == "done"


def test_no_heartbeat_when_events_flow(member, indexed):
    resp = member.post("/chat/stream", json={"question": QUESTION})  # default interval: 10 s
    assert SSE_HEARTBEAT.strip() not in resp.text


def test_with_heartbeat_waits_without_cancelling_the_work():
    async def source():
        await asyncio.sleep(0.12)
        yield {"n": 1}
        yield {"n": 2}

    async def collect():
        return [item async for item in with_heartbeat(source(), 0.03)]

    items = asyncio.run(collect())
    assert items[-2:] == [{"n": 1}, {"n": 2}]
    assert items.count(None) >= 2 and all(i is None for i in items[:-2])


def test_with_heartbeat_reraises_the_source_error():
    async def source():
        yield {"n": 1}
        raise ValueError("panne")

    async def collect():
        return [item async for item in with_heartbeat(source(), 1)]

    with pytest.raises(ValueError, match="panne"):
        asyncio.run(collect())


def test_closing_the_stream_cancels_the_work():
    state = {"cancelled": False}

    async def source():
        try:
            yield {"n": 1}
            await asyncio.sleep(10)
            yield {"n": 2}
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    async def client_goes_away():
        stream = with_heartbeat(source(), 0.01)
        assert await anext(stream) == {"n": 1}
        assert await anext(stream) is None
        await stream.aclose()

    asyncio.run(client_goes_away())
    assert state["cancelled"] is True
