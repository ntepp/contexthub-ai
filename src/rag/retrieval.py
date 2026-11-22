"""Retrieval with its reasons.

One code path for the chat and for the operator diagnostic: the ``retrieve`` node of
the pipeline and ``python -m src.cli explain-retrieval`` both call :func:`search`, so
what the diagnostic shows is what the chat did. Every candidate of the hybrid search
comes back with its fate: kept (and its rank in the context) or excluded, with the
reason (distance above the grounding threshold, beyond ``RETRIEVAL_TOP_K``).

Product references: when the question cites one (``US30010CNG``), the documents that
carry it (``hub_documents.product_refs``) are also searched on their own, so that their
passages compete even when another product's manual is closer in wording, and their
passages get ``RETRIEVAL_REFERENCE_BOOST`` on the fused score. A question without a
reference is searched exactly as before.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select

from src.config import get_settings
from src.ingestion.references import matching, question_references
from src.retrieval.embeddings import get_embeddings
from src.retrieval.rerank import rerank
from src.retrieval.store import RetrievedChunk, get_store

KEPT = "kept"
EXCLUDED = "excluded"
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


async def search(query: str, collection_ids: Sequence[uuid.UUID]) -> Retrieval:
    """Hybrid search, product references, grounding threshold, optional re-ranking, top-k —
    with reasons."""
    settings = get_settings()
    store = get_store()
    ids = list(collection_ids)
    vector = await asyncio.to_thread(get_embeddings().embed_query, query)
    results = await asyncio.to_thread(
        store.search, query, vector, ids, settings.retrieval_candidates, settings.retrieval_hybrid
    )
    candidates = [Candidate(chunk) for chunk in results]

    asked = question_references(query)
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

    order = {chunk.id: position for position, chunk in enumerate(
        await asyncio.to_thread(rerank, query, [c.chunk for c in eligible])
    )}
    eligible.sort(key=lambda c: order[c.chunk.id])
    for position, candidate in enumerate(eligible, start=1):
        if position <= settings.retrieval_top_k:
            candidate.status, candidate.final_rank = KEPT, position
        else:
            candidate.reason = f"au-delà du top_k (rang {position} > {settings.retrieval_top_k})"
    kept = [c.chunk for c in eligible[: settings.retrieval_top_k]]
    return Retrieval(query=query, candidates=candidates, kept=kept, asked_refs=asked, document_refs=refs)


def _excerpt(content: str, size: int) -> str:
    body = content.split("\n", 1)[1] if content.startswith("[") and "\n" in content else content
    return " ".join(body.split())[:size]


def describe(found: Retrieval, excerpt: int = 0) -> list[dict]:
    """One JSON-ready row per candidate, kept ones first (diagnostic output)."""
    rows = []
    for candidate in found.ordered():
        chunk, meta = candidate.chunk, candidate.chunk.metadata or {}
        row = {
            "final_rank": candidate.final_rank,
            "status": candidate.status,
            "distance": round(chunk.distance, 4),
            "vector_rank": chunk.vector_rank,
            "lexical_rank": chunk.lexical_rank,
            "lexical_score": None if chunk.lexical_score is None else round(chunk.lexical_score, 4),
            "fused_score": round(chunk.score, 5),
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
