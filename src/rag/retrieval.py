"""Retrieval with its reasons.

One code path for the chat and for the operator diagnostic: the ``retrieve`` node of
the pipeline and ``python -m src.cli explain-retrieval`` both call :func:`search`, so
what the diagnostic shows is what the chat did. Every candidate of the hybrid search
comes back with its fate: kept (and its rank in the context) or excluded, with the
reason (distance above the grounding threshold, beyond ``RETRIEVAL_TOP_K``).

Product references: when the question cites one (``US30010CNG``), the documents that
carry it (``hub_documents.product_refs``) are also searched on their own, so that their
passages compete even when another product's manual is closer in wording, and their
passages get ``RETRIEVAL_REFERENCE_BOOST`` on the fused score. Passages of documents that
carry other references only (another product's manual) are then left out; when they were
all the question had, retrieval answers itself — the exact ``NO_ANSWER_TEXT`` followed by
``PRODUCT_MISMATCH_TEXT`` naming what the sources are about — and the LLM is not called.
Documents without any reference are never left out for this reason. A question without a
reference is searched exactly as before.

Re-ranking (``RERANKER=cross_encoder``): only the ``RERANK_CANDIDATES`` first candidates the
grounding threshold let through are re-ranked (``limit``), the others keep their fused order
behind them; :func:`rerank_limits` shares ``RERANK_MAX_PAIRS`` between the question and the
planner's sub-queries, so that one answer never scores more pairs than that.

Neighbours: a procedure often runs over several passages and pages. Around each kept
passage, the adjacent passages of the same document and section (``RETRIEVAL_NEIGHBOURS``
on each side, pages next to each other) join the context, best-ranked passages first, within
``RETRIEVAL_CONTEXT_CHARS``. They only ever come with a kept passage: the grounding
threshold still decides alone whether the question is answered.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select

from src.config import DEFAULT_PRODUCT_MISMATCH, get_settings
from src.ingestion.references import matching, question_references
from src.retrieval import rerank as reranker
from src.retrieval.embeddings import get_embeddings
from src.retrieval.store import RetrievedChunk, get_store
from src.utils.logging_config import get_logger

log = get_logger()

KEPT = "kept"
EXCLUDED = "excluded"
NEIGHBOUR = "neighbour"
# How a candidate's document relates to the references cited by the question.
PRODUCT_MATCH = "match"  # the document carries one of them
PRODUCT_OTHER = "other"  # the document carries references, none of them


@dataclass
class Candidate:
    chunk: RetrievedChunk
    status: str = EXCLUDED
    reason: str | None = None
    final_rank: int | None = None  # 1-based position in the context, kept chunks only
    product: str | None = None  # PRODUCT_MATCH, PRODUCT_OTHER, None (no reference on either side)
    matched_refs: list[str] = field(default_factory=list)
    # Found by the search restricted to the documents carrying the question's reference
    # (its vector and full-text ranks are then ranks within those documents).
    via_reference: bool = False


@dataclass
class Retrieval:
    query: str
    candidates: list[Candidate] = field(default_factory=list)  # search order (fused score)
    kept: list[RetrievedChunk] = field(default_factory=list)
    asked_refs: list[str] = field(default_factory=list)  # references cited by the question
    document_refs: dict[uuid.UUID, list[str]] = field(default_factory=dict)  # searched documents' references
    # What the LLM reads: the kept passages and their neighbours (see expand()).
    context: list[RetrievedChunk] = field(default_factory=list)
    vector: list[float] = field(default_factory=list, repr=False)
    collection_ids: list[uuid.UUID] = field(default_factory=list)
    # Set when every passage the threshold let through is about another product: the answer.
    notice: str | None = None
    # Re-ranking: candidates re-ranked (pairs), of which scored by the model (not cached).
    rerank_limit: int = 0
    rerank_pairs: int = 0
    rerank_scored: int = 0
    rerank_seconds: float = 0.0

    def ordered(self) -> list[Candidate]:
        """Kept candidates first, in context order, then the excluded ones in search order."""
        return sorted(self.candidates, key=lambda c: (c.final_rank is None, c.final_rank or 0))


def document_references(collection_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    """Product references of the documents of ``collection_ids`` that have some."""
    from src.db.models import Document
    from src.db.session import SessionLocal

    if not collection_ids:
        return {}
    db = SessionLocal()
    try:
        rows = db.execute(
            select(Document.id, Document.product_refs).where(
                Document.collection_id.in_(list(collection_ids)), Document.product_refs.is_not(None)
            )
        ).all()
    finally:
        db.close()
    return {doc_id: list(refs) for doc_id, refs in rows if refs}


def _classify(candidate: Candidate, asked: list[str], refs: dict[uuid.UUID, list[str]]) -> None:
    known = refs.get(candidate.chunk.document_id)
    if not asked or not known:
        return
    candidate.matched_refs = matching(asked, known)
    candidate.product = PRODUCT_MATCH if candidate.matched_refs else PRODUCT_OTHER


def _other_products(candidates: Sequence[Candidate], refs: dict[uuid.UUID, list[str]], limit: int = 2) -> list[str]:
    """The first references of each document these candidates come from, best-ranked first."""
    found: list[str] = []
    for candidate in candidates:
        for ref in refs.get(candidate.chunk.document_id, [])[:limit]:
            if ref not in found:
                found.append(ref)
    return found


def product_notice(found: Sequence[str], asked: Sequence[str]) -> str:
    """``NO_ANSWER_TEXT`` followed by what the sources are about (``PRODUCT_MISMATCH_TEXT``)."""
    settings = get_settings()
    shown = ", ".join(found[:6]) + ("…" if len(found) > 6 else "")
    try:
        detail = settings.product_mismatch_text.format(found=shown, asked=", ".join(asked))
    except (KeyError, IndexError, ValueError):
        detail = DEFAULT_PRODUCT_MISMATCH.format(found=shown, asked=", ".join(asked))
    return f"{settings.no_answer_text} {detail}"


def question_rerank_limit() -> int:
    """Candidates re-ranked for the question itself (chat ``retrieve`` node, diagnostic)."""
    settings = get_settings()
    return min(settings.rerank_candidates, settings.rerank_max_pairs)


def sub_query_rerank_limit(used: int, sub_queries: int) -> int:
    """Candidates re-ranked for each of ``sub_queries`` planned searches, once the question
    used ``used`` pairs: what is left of ``RERANK_MAX_PAIRS``, shared equally, at most
    ``RERANK_CANDIDATES`` each (below 2, the sub-queries keep their fused order)."""
    settings = get_settings()
    if sub_queries <= 0:
        return 0
    return max(0, min(settings.rerank_candidates, (settings.rerank_max_pairs - used) // sub_queries))


async def _announce(step: str) -> None:
    """Progress event for the chat stream (``{"type": "status", "step": …}``); a no-op
    outside a LangGraph run (diagnostic, scripts)."""
    try:
        from langchain_core.callbacks.manager import adispatch_custom_event

        await adispatch_custom_event("status", {"step": step})
    except RuntimeError:
        pass


async def search(
    query: str,
    collection_ids: Sequence[uuid.UUID],
    references: Sequence[str] | None = None,
    rerank_limit: int | None = None,
) -> Retrieval:
    """Hybrid search, product references, grounding threshold, optional re-ranking, product
    filter, top-k — with reasons. ``references``: the product references the question cites
    (default: those found in ``query``; the planner passes the question's to its sub-queries).
    ``rerank_limit``: candidates re-ranked (default :func:`question_rerank_limit`)."""
    settings = get_settings()
    limit = question_rerank_limit() if rerank_limit is None else rerank_limit
    store = get_store()
    ids = list(collection_ids)
    vector = await asyncio.to_thread(get_embeddings().embed_query, query)
    results = await asyncio.to_thread(
        store.search, query, vector, ids, settings.retrieval_candidates, settings.retrieval_hybrid
    )
    candidates = [Candidate(chunk) for chunk in results]

    asked = list(references) if references is not None else question_references(query)
    refs: dict[uuid.UUID, list[str]] = {}
    if asked:
        refs = await asyncio.to_thread(document_references, ids)
        carrying = [doc_id for doc_id, known in refs.items() if matching(asked, known)]
        if carrying:
            seen = {chunk.id for chunk in results}
            own = await asyncio.to_thread(
                store.search, query, vector, ids, settings.retrieval_candidates, settings.retrieval_hybrid, carrying
            )
            for chunk in own:
                if chunk.id in seen:
                    continue
                # Ranks within a few documents are not ranks within the collections: the
                # passage is scored as if it came right after each list of the main search.
                after = settings.rrf_k + settings.retrieval_candidates
                chunk.score = sum(1.0 / (after + rank) for rank in (chunk.vector_rank, chunk.lexical_rank) if rank)
                candidates.append(Candidate(chunk, via_reference=True))
        for candidate in candidates:
            _classify(candidate, asked, refs)
            if candidate.product == PRODUCT_MATCH:
                candidate.chunk.score += settings.retrieval_reference_boost
        candidates.sort(key=lambda c: (-c.chunk.score, c.chunk.distance))
    eligible: list[Candidate] = []
    for candidate in candidates:
        if candidate.chunk.distance > settings.retrieval_max_distance:
            candidate.reason = (
                f"distance {candidate.chunk.distance:.3f} > seuil {settings.retrieval_max_distance:.2f}"
            )
        else:
            eligible.append(candidate)

    reranked = reranker.Reranked([c.chunk for c in eligible])
    if reranker.enabled() and min(limit, len(eligible)) >= 2:
        await _announce("rerank")
        reranked = await asyncio.to_thread(reranker.rerank, query, [c.chunk for c in eligible], limit)
        log.info(
            "[rerank] %d paire(s) sur %d candidat(s) éligible(s) — %d notée(s), %d en cache, %.2fs",
            reranked.pairs,
            len(eligible),
            reranked.scored,
            reranked.cached,
            reranked.seconds,
        )
    order = {chunk.id: position for position, chunk in enumerate(reranked.chunks)}
    eligible.sort(key=lambda c: order[c.chunk.id])

    notice = None
    others = [c for c in eligible if c.product == PRODUCT_OTHER]
    if others:
        for candidate in others:
            shown = ", ".join(refs.get(candidate.chunk.document_id, [])[:2])
            candidate.reason = f"autre produit ({shown})"
        eligible = [c for c in eligible if c.product != PRODUCT_OTHER]
        if not eligible:
            notice = product_notice(_other_products(others, refs), asked)
    for position, candidate in enumerate(eligible, start=1):
        if position <= settings.retrieval_top_k:
            candidate.status, candidate.final_rank = KEPT, position
        else:
            candidate.reason = f"au-delà du top_k (rang {position} > {settings.retrieval_top_k})"
    kept = [c.chunk for c in eligible[: settings.retrieval_top_k]]
    return Retrieval(
        query=query,
        candidates=candidates,
        kept=kept,
        asked_refs=asked,
        document_refs=refs,
        context=list(kept),
        vector=vector,
        collection_ids=ids,
        notice=notice,
        rerank_limit=limit if reranker.enabled() else 0,
        rerank_pairs=reranked.pairs,
        rerank_scored=reranked.scored,
        rerank_seconds=reranked.seconds,
    )


def _next_pages(anchor: RetrievedChunk, other: RetrievedChunk) -> bool:
    a, b = (anchor.metadata or {}).get("page"), (other.metadata or {}).get("page")
    return a is None or b is None or abs(a - b) <= 1


def _same_section(anchor: RetrievedChunk, other: RetrievedChunk) -> bool:
    """A long section cut into passages, not the next article or the next procedure."""
    return (anchor.metadata or {}).get("section_path") == (other.metadata or {}).get("section_path")


async def expand(
    anchors: Sequence[RetrievedChunk], vector: list[float], collection_ids: Sequence[uuid.UUID]
) -> list[RetrievedChunk]:
    """``anchors`` with their adjacent passages: the passages right before and after each one
    in its document and section (each on the page of the passage next to it or the page
    after / before), best-ranked anchors served first, nearest
    neighbours first, within ``RETRIEVAL_CONTEXT_CHARS``; each anchor followed by its
    neighbours in document order. Anchors are never dropped."""
    settings = get_settings()
    depth = settings.retrieval_neighbours
    anchors = list(anchors)
    present = {(a.document_id, a.chunk_index) for a in anchors if a.chunk_index is not None}
    wanted = {
        (doc_id, index + step * side)
        for doc_id, index in present
        for step in range(1, depth + 1)
        for side in (-1, 1)
        if index + step * side >= 0
    } - present
    if depth <= 0 or not wanted:
        return anchors
    found = await asyncio.to_thread(get_store().fetch, sorted(wanted), vector, list(collection_ids))
    at = {(c.document_id, c.chunk_index): c for c in found}
    budget = settings.retrieval_context_chars - sum(len(a.content) for a in anchors)
    chosen: dict[tuple[uuid.UUID, int], RetrievedChunk] = {}
    for step in range(1, depth + 1):
        for anchor in anchors:
            if anchor.chunk_index is None:
                continue
            for side in (-1, 1):
                position = (anchor.document_id, anchor.chunk_index + step * side)
                between = (anchor.document_id, anchor.chunk_index + (step - 1) * side)
                chunk = at.get(position)
                if chunk is None or position in chosen or position in present:
                    continue
                if step > 1 and between not in chosen and between not in present:
                    continue  # only contiguous runs: never skip a passage
                # Pages next to each other along the run (a long section stays on one page).
                if (
                    not _same_section(anchor, chunk)
                    or not _next_pages(at.get(between) or anchor, chunk)
                    or len(chunk.content) > budget
                ):
                    continue
                budget -= len(chunk.content)
                chunk.neighbour_of = anchor.id
                chosen[position] = chunk
    context: list[RetrievedChunk] = []
    seen: set[uuid.UUID] = set()
    for anchor in anchors:
        group = [anchor] + [c for c in chosen.values() if c.neighbour_of == anchor.id]
        for chunk in sorted(group, key=lambda c: c.chunk_index if c.chunk_index is not None else -1):
            if chunk.id not in seen:
                seen.add(chunk.id)
                context.append(chunk)
    return context


async def retrieve_context(
    query: str, collection_ids: Sequence[uuid.UUID], references: Sequence[str] | None = None
) -> Retrieval:
    """What the ``retrieve`` node does: :func:`search` (question's re-ranking budget), then the
    neighbours of the kept passages."""
    found = await search(query, collection_ids, references)
    if found.kept:
        found.context = await expand(found.kept, found.vector, found.collection_ids)
    return found


def _excerpt(content: str, size: int) -> str:
    body = content.split("\n", 1)[1] if content.startswith("[") and "\n" in content else content
    return " ".join(body.split())[:size]


def _product_of(found: Retrieval, chunk: RetrievedChunk) -> dict:
    probe = Candidate(chunk)
    _classify(probe, found.asked_refs, found.document_refs)
    return {"product": probe.product, "matched_refs": probe.matched_refs}


def describe(found: Retrieval, excerpt: int = 0) -> list[dict]:
    """One JSON-ready row per candidate, kept ones first, then the neighbours joined to the
    context, then the excluded candidates (diagnostic output)."""
    position = {chunk.id: index for index, chunk in enumerate(found.context, start=1)}
    rank = {chunk.id: index for index, chunk in enumerate(found.kept, start=1)}
    neighbours = [
        Candidate(
            chunk,
            status=NEIGHBOUR,
            reason=f"voisin du passage retenu n° {rank.get(chunk.neighbour_of, '?')}",
            **_product_of(found, chunk),
        )
        for chunk in found.context
        if chunk.neighbour_of is not None
    ]
    ordered = found.ordered()
    kept = [c for c in ordered if c.status == KEPT]
    rows = []
    # An excluded candidate joined as a neighbour is listed once, as a neighbour.
    excluded = [c for c in ordered if c.status != KEPT and c.chunk.id not in position]
    for candidate in kept + neighbours + excluded:
        chunk, meta = candidate.chunk, candidate.chunk.metadata or {}
        row = {
            "final_rank": candidate.final_rank,
            "context_position": position.get(chunk.id),
            "status": candidate.status,
            "distance": round(chunk.distance, 4),
            "vector_rank": chunk.vector_rank,
            "lexical_rank": chunk.lexical_rank,
            "lexical_score": None if chunk.lexical_score is None else round(chunk.lexical_score, 4),
            "fused_score": round(chunk.score, 5),
            "rerank_score": None if chunk.rerank_score is None else round(chunk.rerank_score, 4),
            "document": meta.get("filename"),
            "document_id": str(chunk.document_id),
            "page": meta.get("page"),
            "section": meta.get("section"),
            "chunk_index": chunk.chunk_index,
            "product": candidate.product,
            "matched_refs": candidate.matched_refs,
            "via_reference": candidate.via_reference,
            "reason": candidate.reason,
        }
        if excerpt:
            row["excerpt"] = _excerpt(chunk.content, excerpt)
        rows.append(row)
    return rows
