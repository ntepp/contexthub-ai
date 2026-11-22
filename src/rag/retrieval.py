"""Retrieval with its reasons.

One code path for the chat and for the operator diagnostic: the ``retrieve`` node of
the pipeline and ``python -m src.cli explain-retrieval`` both call :func:`search`, so
what the diagnostic shows is what the chat did. Every candidate of the hybrid search
comes back with its fate: kept (and its rank in the context) or excluded, with the
reason (distance above the grounding threshold, beyond ``RETRIEVAL_TOP_K``).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from src.config import get_settings
from src.retrieval.embeddings import get_embeddings
from src.retrieval.rerank import rerank
from src.retrieval.store import RetrievedChunk, get_store

KEPT = "kept"
EXCLUDED = "excluded"


@dataclass
class Candidate:
    chunk: RetrievedChunk
    status: str = EXCLUDED
    reason: str | None = None
    final_rank: int | None = None  # 1-based position in the context, kept chunks only


@dataclass
class Retrieval:
    query: str
    candidates: list[Candidate] = field(default_factory=list)  # search order (fused score)
    kept: list[RetrievedChunk] = field(default_factory=list)

    def ordered(self) -> list[Candidate]:
        """Kept candidates first, in context order, then the excluded ones in search order."""
        return sorted(self.candidates, key=lambda c: (c.final_rank is None, c.final_rank or 0))


async def search(query: str, collection_ids: Sequence[uuid.UUID]) -> Retrieval:
    """Hybrid search, grounding threshold, optional re-ranking, top-k — with reasons."""
    settings = get_settings()
    vector = await asyncio.to_thread(get_embeddings().embed_query, query)
    results = await asyncio.to_thread(
        get_store().search,
        query,
        vector,
        list(collection_ids),
        settings.retrieval_candidates,
        settings.retrieval_hybrid,
    )
    candidates = [Candidate(chunk) for chunk in results]
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
    return Retrieval(query=query, candidates=candidates, kept=kept)


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
            "reason": candidate.reason,
        }
        if excerpt:
            row["excerpt"] = _excerpt(chunk.content, excerpt)
        rows.append(row)
    return rows
