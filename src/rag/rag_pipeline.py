"""Grounded question answering: condense → retrieve → generate (LangGraph).

* ``condense`` rewrites a follow-up question into a standalone one using the
  conversation history (skipped on the first turn). History is used **only** for this
  rewrite: the answer itself is generated from the retrieved sources alone.
* ``retrieve`` runs hybrid search, keeps chunks whose cosine **distance** is
  ``<= RETRIEVAL_MAX_DISTANCE`` (0.0 = identical, higher = worse) and short-circuits
  to the refusal sentence — without calling the LLM — when nothing qualifies.
* ``generate`` answers from numbered sources and must cite them as ``[n]``. A
  refusal from the LLM is normalised to the exact ``NO_ANSWER_TEXT`` sentence and
  drops the sources, so the UI never shows citations for a non-answer.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import START, StateGraph
from typing_extensions import TypedDict

from src.config import get_settings
from src.llm.factory import ModelSpec, resolve_model
from src.llm.llm_chain import initialize_model_llm
from src.observability.metrics import RAG_LATENCY, RAG_QUERIES, RAG_RETAINED_CHUNKS
from src.retrieval.embeddings import get_embeddings
from src.retrieval.rerank import rerank
from src.retrieval.store import RetrievedChunk, get_store
from src.utils.logging_config import get_logger

log = get_logger()

SYSTEM_PROMPT = """You are a compliance knowledge assistant for a regulated financial institution.
You answer questions using ONLY the numbered sources provided by the user message.

Rules, in order of precedence:
1. If the sources do not explicitly support an answer, reply with exactly the following
   sentence, alone, with nothing before or after it:
   {no_answer}
2. Use only the sources, never prior knowledge. Do not speculate or extrapolate.
3. After each statement, cite the supporting source numbers in square brackets, e.g. [1] or [2][3].
4. Always answer in the same language as the question.
5. Be concise, factual and precise. Quote article and paragraph numbers when the sources give them."""

CONDENSE_PROMPT = """Rewrite the follow-up question so that it can be understood without the conversation.
Keep the language of the follow-up question. Output only the rewritten question, nothing else.

Conversation:
{history}

Follow-up question: {question}
Standalone question:"""

_CITATION_RE = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")


def no_answer_text() -> str:
    return get_settings().no_answer_text


class State(TypedDict, total=False):
    question: str
    history: list[tuple[str, str]]
    collection_ids: list[str]
    model_id: str
    standalone_question: str
    context: list[RetrievedChunk]
    answer: str | None
    sources: list[dict]
    confidence_metric: float
    refused: bool


@dataclass
class RAGResult:
    answer: str
    sources: list[dict]
    confidence_metric: float
    refused: bool
    latency_seconds: float
    model: str
    standalone_question: str
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        data = asdict(self)
        data.pop("extra")
        return data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    return " ".join(text.replace("’", "'").split()).strip().strip('"«» ').lower()


def is_refusal(answer: str) -> bool:
    target = _normalise(no_answer_text())
    got = _normalise(answer)
    if not got:
        return True
    return got == target or (target in got and len(got) <= len(target) + 80)


def cited_indices(answer: str) -> set[int]:
    found: set[int] = set()
    for group in _CITATION_RE.findall(answer):
        for number in re.split(r"[,;]", group):
            if number.strip().isdigit():
                found.add(int(number))
    return found


def build_sources(chunks: Sequence[RetrievedChunk]) -> list[dict]:
    sources = []
    for index, chunk in enumerate(chunks, start=1):
        meta = chunk.metadata or {}
        body = chunk.content.split("\n", 1)[1] if chunk.content.startswith("[") and "\n" in chunk.content else chunk.content
        sources.append(
            {
                "index": index,
                "document_id": str(chunk.document_id),
                "collection_id": str(chunk.collection_id),
                "filename": meta.get("filename", "document"),
                "doc_title": meta.get("doc_title"),
                "page": meta.get("page"),
                "section": meta.get("section"),
                "section_path": meta.get("section_path") or [],
                "snippet": body[:600],
                "distance": round(chunk.distance, 4),
                "cited": False,
            }
        )
    return sources


def format_sources(chunks: Sequence[RetrievedChunk]) -> str:
    blocks = []
    for index, chunk in enumerate(chunks, start=1):
        meta = chunk.metadata or {}
        header = [meta.get("filename", "document")]
        if meta.get("page"):
            header.append(f"p. {meta['page']}")
        blocks.append(f"[{index}] {' — '.join(header)}\n{chunk.content}")
    return "\n\n".join(blocks)


def confidence_from(chunks: Sequence[RetrievedChunk]) -> float:
    # Cosine distance runs to 2.0, so 1 - mean(distance) can go negative once
    # MAX_DISTANCE is raised above 1.0. Clamped: a negative score is meaningless.
    best = sorted(c.distance for c in chunks)[:3]
    if not best:
        return 0.0
    return round(max(0.0, 1.0 - sum(best) / len(best)), 3)


def _spec(state: State) -> ModelSpec:
    return resolve_model(state.get("model_id"))


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------


async def condense(state: State) -> dict:
    history = state.get("history") or []
    question = state["question"]
    if not history:
        return {"standalone_question": question}
    turns = history[-2 * get_settings().history_turns :]
    transcript = "\n".join(
        f"{'User' if role == 'user' else 'Assistant'}: {content[:1500]}" for role, content in turns
    )
    llm = initialize_model_llm(_spec(state))
    response = await llm.ainvoke([HumanMessage(CONDENSE_PROMPT.format(history=transcript, question=question))])
    rewritten = (response.content if isinstance(response.content, str) else str(response.content)).strip()
    rewritten = rewritten.splitlines()[0].strip() if rewritten else ""
    log.info("[condense] %r -> %r", question, rewritten)
    return {"standalone_question": rewritten or question}


async def retrieve(state: State) -> dict:
    settings = get_settings()
    query = state.get("standalone_question") or state["question"]
    collection_ids = [uuid.UUID(str(c)) for c in state.get("collection_ids") or []]

    vector = await asyncio.to_thread(get_embeddings().embed_query, query)
    results = await asyncio.to_thread(
        get_store().search,
        query,
        vector,
        collection_ids,
        settings.retrieval_candidates,
        settings.retrieval_hybrid,
    )
    kept = [r for r in results if r.distance <= settings.retrieval_max_distance]
    kept = (await asyncio.to_thread(rerank, query, kept))[: settings.retrieval_top_k]
    RAG_RETAINED_CHUNKS.observe(len(kept))

    log.info(
        "[retrieve] %d/%d candidats retenus (top_k=%d, max_distance=%.2f, hybride=%s) — distances: %s",
        len(kept),
        len(results),
        settings.retrieval_top_k,
        settings.retrieval_max_distance,
        settings.retrieval_hybrid,
        ", ".join(f"{r.distance:.3f}" for r in results[:10]) or "aucun resultat",
    )

    if not kept:
        log.warning("[retrieve] aucun chunk sous le seuil — court-circuit, le LLM ne sera pas appele")
        return {
            "context": [],
            "answer": no_answer_text(),
            "sources": [],
            "confidence_metric": 0.0,
            "refused": True,
        }
    return {
        "context": kept,
        "answer": None,
        "sources": build_sources(kept),
        "confidence_metric": confidence_from(kept),
        "refused": False,
    }


async def generate(state: State) -> dict:
    if state.get("answer"):
        return {"answer": state["answer"]}
    spec = _spec(state)
    messages = [
        SystemMessage(SYSTEM_PROMPT.format(no_answer=no_answer_text())),
        HumanMessage(f"Sources:\n\n{format_sources(state['context'])}\n\nQuestion: {state['question']}"),
    ]
    llm = initialize_model_llm(spec)
    started = time.time()
    full = ""
    async for chunk in llm.astream(messages):
        content = chunk.content if isinstance(chunk.content, str) else "".join(
            part.get("text", "") for part in chunk.content if isinstance(part, dict)
        )
        full += content
    log.info("[generate] model=%s — %d caracteres en %.2fs", spec.id, len(full), time.time() - started)

    if is_refusal(full):
        return {"answer": no_answer_text(), "sources": [], "refused": True}

    cited = cited_indices(full)
    sources = [dict(s, cited=s["index"] in cited) for s in state["sources"]]
    return {"answer": full.strip(), "sources": sources, "refused": False}


def _build_graph():
    graph = StateGraph(State).add_sequence([condense, retrieve, generate])
    graph.add_edge(START, "condense")
    return graph.compile()


_GRAPH = None


def graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


def _initial_state(question, collection_ids, history, model_id) -> State:
    spec = resolve_model(model_id)  # raises ModelNotAllowed before any work
    return {
        "question": question,
        "collection_ids": [str(c) for c in collection_ids],
        "history": list(history or []),
        "model_id": spec.id,
    }


def _result(state: dict, started: float) -> RAGResult:
    latency = round(time.time() - started, 2)
    refused = bool(state.get("refused"))
    RAG_QUERIES.labels("refused" if refused else "answered").inc()
    RAG_LATENCY.observe(latency)
    return RAGResult(
        answer=state.get("answer") or no_answer_text(),
        sources=state.get("sources") or [],
        confidence_metric=state.get("confidence_metric", 0.0) if not refused else 0.0,
        refused=refused,
        latency_seconds=latency,
        model=state["model_id"],
        standalone_question=state.get("standalone_question") or state["question"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def answer_question(
    question: str,
    collection_ids: Sequence[uuid.UUID | str],
    history: Sequence[tuple[str, str]] | None = None,
    model_id: str | None = None,
) -> RAGResult:
    started = time.time()
    state = await graph().ainvoke(_initial_state(question, collection_ids, history, model_id))
    return _result(state, started)


async def stream_answer(
    question: str,
    collection_ids: Sequence[uuid.UUID | str],
    history: Sequence[tuple[str, str]] | None = None,
    model_id: str | None = None,
) -> AsyncIterator[dict]:
    """Yield ``{"type": "token", "content"}`` events, then one ``{"type": "result", ...}``.

    Only tokens produced by the ``generate`` node are forwarded — the ``condense``
    node also calls the LLM and its output must not reach the user. When retrieve
    short-circuits, generate never calls the LLM, so the refusal is emitted from the
    retrieve end event, otherwise the UI would render an empty bubble.
    """
    started = time.time()
    initial = _initial_state(question, collection_ids, history, model_id)
    final_state: dict | None = None
    async for event in graph().astream_events(initial, version="v2"):
        kind = event["event"]
        node = (event.get("metadata") or {}).get("langgraph_node")
        if kind == "on_chat_model_stream" and node == "generate":
            chunk = event["data"]["chunk"]
            content = chunk.content if isinstance(chunk.content, str) else "".join(
                part.get("text", "") for part in chunk.content if isinstance(part, dict)
            )
            if content:
                yield {"type": "token", "content": content}
        elif kind == "on_chain_end" and event.get("name") == "retrieve":
            output = event["data"].get("output") or {}
            if output.get("answer"):
                yield {"type": "token", "content": output["answer"]}
        elif kind == "on_chain_end" and event.get("name") == "LangGraph":
            final_state = event["data"].get("output")
    if final_state is None:  # pragma: no cover - defensive
        raise RuntimeError("Le graphe RAG n'a pas produit d'état final.")
    yield {"type": "result", **_result(final_state, started).as_dict()}


async def create_and_run_graph(question: str, collection: str = "default", model_id: str | None = None) -> dict:
    """Compatibility entry point (evaluation harness, scripts): collection by name."""
    from src.db.models import Collection
    from src.db.session import SessionLocal

    db = SessionLocal()
    try:
        col = db.query(Collection).filter(Collection.name == collection).first()
    finally:
        db.close()
    if col is None:
        raise ValueError(f"Collection inconnue : {collection}")
    return (await answer_question(question, [col.id], model_id=model_id)).as_dict()
