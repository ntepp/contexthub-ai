"""Grounded question answering: condense → retrieve → plan → generate (LangGraph).

* ``condense`` rewrites a follow-up question into a standalone one using the
  conversation history (skipped on the first turn). History is used **only** for this
  rewrite: the answer itself is generated from the retrieved sources alone.
* ``retrieve`` runs hybrid search, keeps chunks whose cosine **distance** is
  ``<= RETRIEVAL_MAX_DISTANCE`` (0.0 = identical, higher = worse) and short-circuits
  to the refusal sentence — without calling the LLM — when nothing qualifies.
* ``plan`` (agentic retrieval) lets the LLM decide whether the question needs several
  searches — one per topic, document or step — runs the extra ones and merges the
  results so that every sub-question gets passages in the context. In the same call
  (QUERY_REWRITE) it rewords the question in the vocabulary the documents likely use; the
  rewordings are searched too and fused (RRF) with the question's own ranking, which keeps
  deciding grounding and confidence. It only runs once ``retrieve`` found something, so an
  unanswerable question still costs no LLM call and a rewording never turns a refusal into
  an answer.
* ``generate`` answers from numbered sources and must cite them as ``[n]``; a "how to /
  what to do" question is answered as numbered steps rebuilt from every relevant source,
  interface elements named after their description in the sources, conditions and limits
  stated and missing steps said (rule 7 of the system prompt). A
  refusal from the LLM is normalised to the exact ``NO_ANSWER_TEXT`` sentence and
  drops the sources, so the UI never shows citations for a non-answer.

Each node records its duration in ``timings`` (re-ranking: seconds and pairs, within
``retrieve`` and ``plan``); one ``[timing]`` log line per answer sums them up.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict, dataclass, field
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import START, StateGraph
from typing_extensions import TypedDict

from src.config import get_settings
from src.ingestion.references import question_references
from src.llm.factory import ModelSpec, parse_model_id, resolve_model
from src.llm.llm_chain import initialize_model_llm
from src.observability.metrics import RAG_LATENCY, RAG_QUERIES, RAG_RETAINED_CHUNKS
from src.rag import retrieval
from src.retrieval.embeddings import get_embeddings
from src.retrieval.store import RetrievedChunk
from src.utils.logging_config import get_logger, request_id_var

log = get_logger()

SYSTEM_PROMPT = """You are a knowledge assistant for the employees of an organisation.
You answer questions using ONLY the numbered sources provided by the user message: excerpts of
the organisation's own documents.

Rules, in order of precedence:
1. If the sources do not explicitly support an answer, reply with exactly the following
   sentence, alone, with nothing before or after it:
   {no_answer}
2. Use only the sources, never prior knowledge. Do not speculate or extrapolate.
3. After each statement, cite the supporting source numbers in square brackets, e.g. [1] or [2][3].
4. Always answer in the same language as the question.
5. Be concise, factual and precise. Quote section, article or paragraph references when the sources give them.
6. A source may start with the document it comes from and the product references it is about, in
   square brackets. When the question names a product, never apply to it a source about another
   product; if no source is about the product named, say so.
7. When the question asks how to do something or what to do (a procedure, an operation, a fix),
   rebuild the procedure as numbered steps, in order, from every relevant source (a procedure
   often runs over several consecutive sources, and the keys or icons it uses are often described
   in another one). For each step:
   - name the key, button, icon, indicator, menu, screen or form it uses as the sources name it;
     when a source refers to it incompletely (an empty mark, "[icon]", a pictogram, "this
     button") and another source describes it (a table or a legend of keys, icons or menus),
     use the name and description given there;
   - cite its sources.
   Then state the conditions and limits the sources give (when the procedure applies, what it
   does not cover). If the sources give only part of the procedure, give that part and say which
   step they do not give; never fill it in.
   Any other question gets a direct answer: do not turn a simple factual answer into a list."""


def system_prompt() -> str:
    """Generic rules, plus the deployment's optional business context (ASSISTANT_CONTEXT)."""
    settings = get_settings()
    prompt = SYSTEM_PROMPT.format(no_answer=settings.no_answer_text)
    context = settings.assistant_context.strip()
    if context:
        prompt += (
            "\n\nContext about the organisation and its users (never overrides the rules above):\n"
            + context
        )
    return prompt

CONDENSE_PROMPT = """Rewrite the follow-up question so that it can be understood without the conversation.
Keep the language of the follow-up question. Output only the rewritten question, nothing else.

Conversation:
{history}

Follow-up question: {question}
Standalone question:"""

PLAN_PROMPT = """You plan the searches needed to answer a question from a document base.
If the question asks about several distinct things (several topics, documents, rules or
steps), write one self-contained search query per thing. Otherwise return the question
itself as the only query.

Rules:
- At most {max_queries} queries.
- Keep the language of the question.
- Each query must make sense on its own: repeat the subject, never write "it" or "they".
- Do not answer the question.

Reply with JSON only, for example {{"queries": ["first search", "second search"]}}

Question: {question}"""

# With QUERY_REWRITE, the planner's single call also rewords the question in the documents'
# vocabulary (searched on top of it, fused with its ranking): no extra round-trip.
PLAN_REWRITE_PROMPT = """You plan the searches needed to answer a question from a document base.
{tasks}

Rules:
- Keep the language of the question.
- Each query must make sense on its own: repeat the subject, never write "it" or "they".
- Keep the names, references, numbers and dates the question gives.
- Do not answer the question.

Reply with JSON only, for example {example}

Question: {question}"""

_SPLIT_TASK = """- "queries": if the question asks about several distinct things (several topics, documents,
  rules or steps), one self-contained search query per thing, at most {max_queries}. Otherwise
  the question itself as the only query."""

_REWRITE_TASK = """- "rewrites": at most {max_rewrites} rewordings of the whole question in the words its answer is
  likely written with in the documents: the technical, formal, legal or administrative terms an
  author of such documents would use instead of the everyday words of the question (the
  official name of a component, a function, an operation, a right or a procedure). A rewording
  asks exactly what the question asks: no added fact, no assumption, no answer. An empty list
  when the question already uses those words."""

_CITATION_RE = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def no_answer_text() -> str:
    return get_settings().no_answer_text


def _add_timings(left: dict[str, float] | None, right: dict[str, float] | None) -> dict[str, float]:
    """Durations and counts written by the nodes, summed per key (re-ranking runs in
    ``retrieve`` and again in ``plan``)."""
    total = dict(left or {})
    for key, value in (right or {}).items():
        total[key] = total.get(key, 0) + value
    return total


class State(TypedDict, total=False):
    question: str
    history: list[tuple[str, str]]
    collection_ids: list[str]
    model_id: str
    # ContextHub UUID of the asking user: attributes LLM calls at the gateway (never an e-mail).
    user_id: str
    standalone_question: str
    search_queries: list[str]
    # Rewordings of the question in the documents' vocabulary searched by ``plan`` (QUERY_REWRITE).
    search_rewrites: list[str]
    # Kept passages, best first (the planner merges its sub-queries' with these), and the
    # context the LLM reads: the same passages with their neighbours.
    anchors: list[RetrievedChunk]
    context: list[RetrievedChunk]
    answer: str | None
    sources: list[dict]
    confidence_metric: float
    refused: bool
    # Seconds per step (condense, retrieve, plan, generate, rerank) and re-ranking pairs.
    timings: Annotated[dict[str, float], _add_timings]


@dataclass
class RAGResult:
    answer: str
    sources: list[dict]
    confidence_metric: float
    refused: bool
    latency_seconds: float
    model: str
    standalone_question: str
    # Searches actually run: the question itself, then the sub-queries planned by the LLM.
    search_queries: list[str] = field(default_factory=list)
    # Rewordings of the question searched on top of it (QUERY_REWRITE); never shown to the LLM
    # that answers, which reads the question as asked.
    search_rewrites: list[str] = field(default_factory=list)
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


def _product_mismatch_pattern() -> re.Pattern[str]:
    """The refusal written by the product guard: ``NO_ANSWER_TEXT`` then
    ``PRODUCT_MISMATCH_TEXT``, whatever the length of the references it names."""
    template = _normalise(get_settings().product_mismatch_text)
    parts = re.split(r"\\{(?:found|asked)\\}", re.escape(template))
    return re.compile(re.escape(_normalise(no_answer_text())) + " " + ".+?".join(parts) + "$")


def is_refusal(answer: str) -> bool:
    target = _normalise(no_answer_text())
    got = _normalise(answer)
    if not got:
        return True
    if got == target or (target in got and len(got) <= len(target) + 80):
        return True
    # Only the guard's own form, not any long text that merely starts with a refusal.
    return bool(_product_mismatch_pattern().fullmatch(got))


def cited_indices(answer: str) -> set[int]:
    found: set[int] = set()
    for group in _CITATION_RE.findall(answer):
        for number in re.split(r"[,;]", group):
            if number.strip().isdigit():
                found.add(int(number))
    return found


_LEADING_HEADINGS = re.compile(r"\A(?:#{1,6} [^\n]*\n+)+(?=\S)")


def build_sources(chunks: Sequence[RetrievedChunk]) -> list[dict]:
    sources = []
    for index, chunk in enumerate(chunks, start=1):
        meta = chunk.metadata or {}
        body = chunk.content.split("\n", 1)[1] if chunk.content.startswith("[") and "\n" in chunk.content else chunk.content
        # Markdown / HTML sections start with their own heading line, already shown as the
        # section path: keep it out of the excerpt when text follows.
        body = _LEADING_HEADINGS.sub("", body, count=1) or body
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
                # Joined for being next to a kept passage (same document), not found by the search.
                "neighbour": chunk.neighbour_of is not None,
                "url": meta.get("source_url"),
                "source_kind": meta.get("source_kind"),
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


def parse_plan(reply: str, question: str, max_queries: int) -> list[str]:
    """Search queries from the planner's JSON reply: the question itself, then up to
    ``max_queries`` planned sub-queries.

    Anything unusable (no JSON, wrong shape) falls back to the question alone: planning
    can only widen retrieval, never break it. So does a plan with a single sub-query:
    that is a paraphrase, not a split, and searching it again only crowds the context.
    """
    queries = [question]
    match = _JSON_OBJECT_RE.search(reply or "")
    planned: list = []
    if match:
        try:
            data = json.loads(match.group(0))
            planned = data.get("queries", []) if isinstance(data, dict) else []
        except json.JSONDecodeError:
            planned = []
    seen = {_normalise(question)}
    for item in planned if isinstance(planned, list) else []:
        if not isinstance(item, str) or not item.strip():
            continue
        key = _normalise(item)
        if key in seen:
            continue
        seen.add(key)
        queries.append(" ".join(item.split()))
        if len(queries) - 1 >= max_queries:
            break
    return queries if len(queries) >= 3 else [question]


def plan_prompt(question: str, split: bool, rewrite: bool) -> str:
    """The planner's prompt: sub-queries (``split``, AGENTIC_RETRIEVAL) and / or rewordings in
    the documents' vocabulary (``rewrite``, QUERY_REWRITE). Without rewording, the prompt is
    exactly the sub-query planner's."""
    settings = get_settings()
    if not rewrite:
        return PLAN_PROMPT.format(max_queries=settings.agentic_max_queries, question=question)
    tasks = [_REWRITE_TASK.format(max_rewrites=settings.query_rewrite_max)]
    example = '{"rewrites": ["the question in the words of the documents"]}'
    if split:
        tasks.insert(0, _SPLIT_TASK.format(max_queries=settings.agentic_max_queries))
        example = '{"queries": ["first search", "second search"], "rewrites": ["the question in the words of the documents"]}'
    return PLAN_REWRITE_PROMPT.format(tasks="\n".join(tasks), example=example, question=question)


def parse_rewrites(reply: str, taken: Sequence[str], max_rewrites: int) -> list[str]:
    """Rewordings from the planner's JSON reply (``"rewrites"``): up to ``max_rewrites``, none
    identical to the question, to a sub-query (``taken``) or to one another. Anything unusable
    gives none: rewording can only widen retrieval, never break it."""
    match = _JSON_OBJECT_RE.search(reply or "")
    if not match or max_rewrites < 1:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    items = data.get("rewrites") if isinstance(data, dict) else None
    seen = {_normalise(text) for text in taken}
    rewrites: list[str] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, str) or not item.strip() or len(item) > 500:
            continue
        key = _normalise(item)
        if key in seen:
            continue
        seen.add(key)
        rewrites.append(" ".join(item.split()))
        if len(rewrites) >= max_rewrites:
            break
    return rewrites


def fuse(rankings: Sequence[Sequence[RetrievedChunk]], limit: int) -> list[RetrievedChunk]:
    """Reciprocal Rank Fusion of rankings of the same need (the question and its rewordings):
    a passage found by several formulations comes first; ties keep the order of the first
    ranking (the question's). The passage object kept is the first one seen."""
    k = get_settings().rrf_k
    scores: dict[uuid.UUID, float] = {}
    first: dict[uuid.UUID, RetrievedChunk] = {}
    for results in rankings:
        for rank, chunk in enumerate(results, start=1):
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (k + rank)
            first.setdefault(chunk.id, chunk)
    order = {chunk_id: position for position, chunk_id in enumerate(first)}
    ranked = sorted(first, key=lambda chunk_id: (-scores[chunk_id], order[chunk_id]))
    return [first[chunk_id] for chunk_id in ranked[:limit]]


def interleave(ranked: Sequence[Sequence[RetrievedChunk]], limit: int) -> list[RetrievedChunk]:
    """Round-robin over the per-query rankings so each search contributes passages."""
    merged: list[RetrievedChunk] = []
    seen: set[uuid.UUID] = set()
    depth = max((len(r) for r in ranked), default=0)
    for position in range(depth):
        for results in ranked:
            if position < len(results) and results[position].id not in seen:
                seen.add(results[position].id)
                merged.append(results[position])
                if len(merged) >= limit:
                    return merged
    return merged


def _spec(state: State) -> ModelSpec:
    # Validated against the allow-list when the state was built: no catalogue lookup
    # (a possible blocking gateway call) inside the graph's nodes.
    return parse_model_id(state["model_id"])


def _rerank_timings(found: Sequence[retrieval.Retrieval]) -> dict[str, float]:
    return {
        "rerank": sum(f.rerank_seconds for f in found),
        "rerank_pairs": sum(f.rerank_pairs for f in found),
        "rerank_scored": sum(f.rerank_scored for f in found),
    }


def _references(state: State) -> list[str]:
    """Product references cited by the question, as asked and as reformulated."""
    found: list[str] = []
    for text in (state.get("standalone_question") or "", state["question"]):
        for ref in question_references(text):
            if ref not in found:
                found.append(ref)
    return found


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------


async def condense(state: State) -> dict:
    history = state.get("history") or []
    question = state["question"]
    if not history:
        return {"standalone_question": question, "timings": {"condense": 0.0}}
    started = time.perf_counter()
    turns = history[-2 * get_settings().history_turns :]
    transcript = "\n".join(
        f"{'User' if role == 'user' else 'Assistant'}: {content[:1500]}" for role, content in turns
    )
    llm = initialize_model_llm(_spec(state), user_id=state.get("user_id"), step="condense")
    response = await llm.ainvoke([HumanMessage(CONDENSE_PROMPT.format(history=transcript, question=question))])
    rewritten = (response.content if isinstance(response.content, str) else str(response.content)).strip()
    rewritten = rewritten.splitlines()[0].strip() if rewritten else ""
    log.info("[condense] %r -> %r", question, rewritten)
    return {"standalone_question": rewritten or question, "timings": {"condense": time.perf_counter() - started}}


async def retrieve(state: State) -> dict:
    settings = get_settings()
    query = state.get("standalone_question") or state["question"]
    collection_ids = [uuid.UUID(str(c)) for c in state.get("collection_ids") or []]

    started = time.perf_counter()
    found = await retrieval.retrieve_context(query, collection_ids, _references(state))
    results, kept = [c.chunk for c in found.candidates], found.kept
    timings = {"retrieve": time.perf_counter() - started, **_rerank_timings([found])}

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
        RAG_RETAINED_CHUNKS.observe(0)
        if found.notice:
            log.warning("[retrieve] sources sur d'autres produits que %s — court-circuit", ", ".join(found.asked_refs))
        else:
            log.warning("[retrieve] aucun chunk sous le seuil — court-circuit, le LLM ne sera pas appele")
        return {
            "search_queries": [query],
            "anchors": [],
            "context": [],
            "answer": found.notice or no_answer_text(),
            "sources": [],
            "confidence_metric": 0.0,
            "refused": True,
            "timings": timings,
        }
    if len(found.context) > len(kept):
        log.info("[retrieve] %d passage(s) voisin(s) joint(s) au contexte", len(found.context) - len(kept))
    return {
        "search_queries": [query],
        "anchors": kept,
        "context": found.context,
        "answer": None,
        "sources": build_sources(found.context),
        "confidence_metric": confidence_from(kept),
        "refused": False,
        "timings": timings,
    }


async def plan(state: State) -> dict:
    """Agentic retrieval: the LLM decides which extra searches the question needs (sub-queries,
    AGENTIC_RETRIEVAL) and rewords it in the documents' vocabulary (QUERY_REWRITE), in one call.

    Only after ``retrieve`` kept passages for the question itself: a rewording never turns a
    refusal into an answer, and each extra search applies the grounding threshold."""
    settings = get_settings()
    split = settings.agentic_retrieval and settings.agentic_max_queries >= 2
    rewrite = settings.query_rewrite and settings.query_rewrite_max >= 1
    if state.get("refused") or not (split or rewrite):
        RAG_RETAINED_CHUNKS.observe(len(state.get("context") or []))
        return {}
    question = state.get("standalone_question") or state["question"]
    started = time.perf_counter()
    llm = initialize_model_llm(_spec(state), user_id=state.get("user_id"), step="plan")
    response = await llm.ainvoke([HumanMessage(plan_prompt(question, split, rewrite))])
    reply = response.content if isinstance(response.content, str) else str(response.content)
    queries = parse_plan(reply, question, settings.agentic_max_queries) if split else [question]
    rewrites = parse_rewrites(reply, queries, settings.query_rewrite_max) if rewrite else []
    if len(queries) == 1 and not rewrites:
        log.info("[plan] une seule recherche suffit")
        RAG_RETAINED_CHUNKS.observe(len(state["context"]))
        return {"timings": {"plan": time.perf_counter() - started}}

    collection_ids = [uuid.UUID(str(c)) for c in state.get("collection_ids") or []]
    # Sub-queries and rewordings keep the question's product references, even when the planner drops them.
    references = _references(state)
    # Sub-queries and rewordings share what the question left of RERANK_MAX_PAIRS: planning
    # never multiplies the cost of re-ranking.
    extra_queries = [*queries[1:], *rewrites]
    limit = retrieval.sub_query_rerank_limit(
        int((state.get("timings") or {}).get("rerank_pairs", 0)), len(extra_queries)
    )
    extra = await asyncio.gather(
        *(retrieval.search(q, collection_ids, references, rerank_limit=limit) for q in extra_queries)
    )
    sub_kept = [found.kept for found in extra[: len(queries) - 1]]
    rewrite_kept = [found.kept for found in extra[len(queries) - 1 :]]
    own = state.get("anchors") or state["context"]
    # The question's ranking, fused with its rewordings' (same need), comes first in the merge.
    if rewrites:
        own = fuse([own, *rewrite_kept], settings.retrieval_top_k)
    rankings = [own, *sub_kept]
    merged = interleave(rankings, settings.retrieval_top_k)
    vector = await asyncio.to_thread(get_embeddings().embed_query, question)
    context = await retrieval.expand(merged, vector, collection_ids)
    RAG_RETAINED_CHUNKS.observe(len(merged))
    log.info(
        "[plan] %d recherches : %s — reformulations : %s — retenus par recherche : %s",
        len(queries),
        " | ".join(queries),
        " | ".join(rewrites) or "aucune",
        ", ".join(str(len(r)) for r in [state.get("anchors") or [], *sub_kept, *rewrite_kept]),
    )
    # Confidence on what the question and its sub-queries found: a rewording written in the
    # documents' words is closer to them by construction and would inflate it.
    by_question = {c.id for results in [state.get("anchors") or [], *sub_kept] for c in results}
    scored = [c for c in merged if c.id in by_question]
    return {
        "search_queries": queries,
        "search_rewrites": rewrites,
        "anchors": merged,
        "context": context,
        "sources": build_sources(context),
        "confidence_metric": confidence_from(scored) if scored else state.get("confidence_metric", 0.0),
        "timings": {"plan": time.perf_counter() - started, **_rerank_timings(extra)},
    }


async def generate(state: State) -> dict:
    if state.get("answer"):
        return {"answer": state["answer"]}
    spec = _spec(state)
    messages = [
        SystemMessage(system_prompt()),
        HumanMessage(f"Sources:\n\n{format_sources(state['context'])}\n\nQuestion: {state['question']}"),
    ]
    llm = initialize_model_llm(spec, user_id=state.get("user_id"), step="generate")
    started = time.perf_counter()
    full = ""
    async for chunk in llm.astream(messages):
        content = chunk.content if isinstance(chunk.content, str) else "".join(
            part.get("text", "") for part in chunk.content if isinstance(part, dict)
        )
        full += content
    elapsed = time.perf_counter() - started
    log.info("[generate] model=%s — %d caracteres en %.2fs", spec.id, len(full), elapsed)
    timings = {"generate": elapsed}

    if is_refusal(full):
        return {"answer": no_answer_text(), "sources": [], "refused": True, "timings": timings}

    cited = cited_indices(full)
    sources = [dict(s, cited=s["index"] in cited) for s in state["sources"]]
    return {"answer": full.strip(), "sources": sources, "refused": False, "timings": timings}


def _build_graph():
    graph = StateGraph(State).add_sequence([condense, retrieve, plan, generate])
    graph.add_edge(START, "condense")
    return graph.compile()


_GRAPH = None


def graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = _build_graph()
    return _GRAPH


async def _initial_state(question, collection_ids, history, model_id, user_id=None) -> State:
    # Raises ModelNotAllowed before any work; in a thread, the catalogue may call the gateway.
    spec = await asyncio.to_thread(resolve_model, model_id)
    state: State = {
        "question": question,
        "collection_ids": [str(c) for c in collection_ids],
        "history": list(history or []),
        "model_id": spec.id,
    }
    if user_id is not None:
        state["user_id"] = str(uuid.UUID(str(user_id)))
    return state


def log_timings(state: dict, latency: float, first_token: float | None = None) -> None:
    """One ``[timing]`` line per answer: duration of each step, re-ranking pairs."""
    timings = state.get("timings") or {}
    parts = [f"total={latency:.2f}s"]
    if first_token is not None:
        parts.append(f"first_token={first_token:.2f}s")
    parts += [f"{step}={timings[step]:.2f}s" for step in ("condense", "retrieve", "plan", "generate") if step in timings]
    pairs, scored = int(timings.get("rerank_pairs", 0)), int(timings.get("rerank_scored", 0))
    parts += [
        f"rerank={timings.get('rerank', 0.0):.2f}s",
        f"rerank_pairs={pairs}",
        f"rerank_cached={pairs - scored}",
        f"queries={len(state.get('search_queries') or [])}",
        f"refused={'yes' if state.get('refused') else 'no'}",
    ]
    log.info("[timing] request=%s %s", request_id_var.get() or "-", " ".join(parts))


def _result(state: dict, started: float, first_token: float | None = None) -> RAGResult:
    latency = round(time.time() - started, 2)
    refused = bool(state.get("refused"))
    RAG_QUERIES.labels("refused" if refused else "answered").inc()
    RAG_LATENCY.observe(latency)
    log_timings(state, latency, first_token)
    return RAGResult(
        answer=state.get("answer") or no_answer_text(),
        sources=state.get("sources") or [],
        confidence_metric=state.get("confidence_metric", 0.0) if not refused else 0.0,
        refused=refused,
        latency_seconds=latency,
        model=state["model_id"],
        standalone_question=state.get("standalone_question") or state["question"],
        search_queries=state.get("search_queries") or [],
        search_rewrites=state.get("search_rewrites") or [],
        extra={"timings": dict(state.get("timings") or {})},
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def answer_question(
    question: str,
    collection_ids: Sequence[uuid.UUID | str],
    history: Sequence[tuple[str, str]] | None = None,
    model_id: str | None = None,
    user_id: uuid.UUID | str | None = None,
) -> RAGResult:
    started = time.time()
    state = await graph().ainvoke(await _initial_state(question, collection_ids, history, model_id, user_id))
    return _result(state, started)


# Steps announced to the chat stream ({"type": "status", "step": …}); "rerank" comes from
# inside retrieve / plan (src/rag/retrieval.py) as a LangGraph custom event.
STREAM_STEPS = ("condense", "retrieve", "rerank", "plan", "generate")


async def stream_answer(
    question: str,
    collection_ids: Sequence[uuid.UUID | str],
    history: Sequence[tuple[str, str]] | None = None,
    model_id: str | None = None,
    user_id: uuid.UUID | str | None = None,
) -> AsyncIterator[dict]:
    """Yield ``{"type": "status", "step"}`` and ``{"type": "token", "content"}`` events, then
    one ``{"type": "result", ...}``.

    Only tokens produced by the ``generate`` node are forwarded — the ``condense`` and
    ``plan`` nodes also call the LLM and their output must not reach the user. When retrieve
    short-circuits, generate never calls the LLM, so the refusal is emitted from the
    retrieve end event, otherwise the UI would render an empty bubble.

    ``status`` events say which step is running (``STREAM_STEPS``), so that the UI shows
    progress while nothing is written yet. They are never part of the answer, and none
    follows a short-circuited refusal. ``condense`` is announced only when there is a
    history to read, ``plan`` only when agentic retrieval is on.
    """
    settings = get_settings()
    started = time.time()
    first_token: float | None = None
    initial = await _initial_state(question, collection_ids, history, model_id, user_id)
    planning = settings.agentic_retrieval and settings.agentic_max_queries >= 2
    answered = False  # the short-circuited refusal was emitted: nothing more to announce
    final_state: dict | None = None
    async for event in graph().astream_events(initial, version="v2"):
        kind, name = event["event"], event.get("name")
        node = (event.get("metadata") or {}).get("langgraph_node")
        if kind == "on_chat_model_stream" and node == "generate":
            chunk = event["data"]["chunk"]
            content = chunk.content if isinstance(chunk.content, str) else "".join(
                part.get("text", "") for part in chunk.content if isinstance(part, dict)
            )
            if content:
                if first_token is None:
                    first_token = time.time() - started
                yield {"type": "token", "content": content}
        elif kind == "on_chain_start" and name in STREAM_STEPS and node == name:
            skipped = (name == "condense" and not initial["history"]) or (name == "plan" and not planning)
            if not answered and not skipped:
                yield {"type": "status", "step": name}
        elif kind == "on_custom_event" and name == "status":
            step = (event.get("data") or {}).get("step")
            if not answered and step in STREAM_STEPS:
                yield {"type": "status", "step": step}
        elif kind == "on_chain_end" and name == "retrieve":
            output = event["data"].get("output") or {}
            if output.get("answer"):
                answered = True
                first_token = time.time() - started
                yield {"type": "token", "content": output["answer"]}
        elif kind == "on_chain_end" and name == "LangGraph":
            final_state = event["data"].get("output")
    if final_state is None:  # pragma: no cover - defensive
        raise RuntimeError("Le graphe RAG n'a pas produit d'état final.")
    yield {"type": "result", **_result(final_state, started, first_token).as_dict()}


async def create_and_run_graph(
    question: str, collection: str = "default", model_id: str | None = None, user_id: uuid.UUID | str | None = None
) -> dict:
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
    return (await answer_question(question, [col.id], model_id=model_id, user_id=user_id)).as_dict()
