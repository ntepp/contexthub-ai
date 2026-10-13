"""Chunk storage and hybrid retrieval.

``PgChunkStore`` owns the ``hub_chunks`` table (created by Alembic): one row per
chunk with its pgvector embedding and a generated ``tsvector``. Owning the schema —
rather than going through langchain-postgres — gives cascading deletes per
document/collection, a full-text index for hybrid search, and multi-collection
queries.

Hybrid retrieval: the top-N candidates by cosine distance and the top-N by
full-text rank are fused with Reciprocal Rank Fusion. Every candidate carries its
cosine **distance** (0 = identical, 2 = opposite) so the caller can still apply the
grounding threshold: lexical matching widens the candidate pool (e.g. a question
quoting "article 93"), it never lets an off-topic chunk through.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import text

from src.config import get_settings
from src.db.session import get_engine
from src.utils.logging_config import get_logger

log = get_logger()


@dataclass
class ChunkRecord:
    document_id: uuid.UUID
    collection_id: uuid.UUID
    chunk_index: int
    content: str
    metadata: dict
    embedding: list[float]
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class RetrievedChunk:
    id: uuid.UUID
    document_id: uuid.UUID
    collection_id: uuid.UUID
    content: str
    metadata: dict
    distance: float
    score: float = 0.0


class ChunkStore(Protocol):
    def add_chunks(self, chunks: Sequence[ChunkRecord]) -> None: ...

    def delete_document(self, document_id: uuid.UUID) -> int: ...

    def delete_collection(self, collection_id: uuid.UUID) -> int: ...

    def count_document(self, document_id: uuid.UUID) -> int: ...

    def search(
        self,
        query_text: str,
        query_embedding: list[float],
        collection_ids: Sequence[uuid.UUID],
        candidates: int,
        hybrid: bool,
    ) -> list[RetrievedChunk]: ...


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def lexical_terms(query: str) -> list[str]:
    """Words worth matching: alphanumeric tokens, numbers kept (article numbers matter)."""
    terms: list[str] = []
    for token in _TOKEN_RE.findall(query.lower()):
        if (len(token) >= 3 or token.isdigit()) and token not in terms:
            terms.append(token)
    return terms[:32]


def _vector_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(x):.7g}" for x in vec) + "]"


class PgChunkStore:
    def __init__(self) -> None:
        self._iterative_scan: bool | None = None

    def add_chunks(self, chunks: Sequence[ChunkRecord]) -> None:
        if not chunks:
            return
        rows = [
            {
                "id": str(c.id),
                "document_id": str(c.document_id),
                "collection_id": str(c.collection_id),
                "chunk_index": c.chunk_index,
                "content": c.content.replace("\x00", ""),
                "metadata": json.dumps(c.metadata, ensure_ascii=False),
                "embedding": _vector_literal(c.embedding),
            }
            for c in chunks
        ]
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO hub_chunks (id, document_id, collection_id, chunk_index, content, metadata, embedding)
                    VALUES (CAST(:id AS uuid), CAST(:document_id AS uuid), CAST(:collection_id AS uuid),
                            :chunk_index, :content, CAST(:metadata AS jsonb), CAST(:embedding AS vector))
                    """
                ),
                rows,
            )

    def delete_document(self, document_id: uuid.UUID) -> int:
        with get_engine().begin() as conn:
            result = conn.execute(
                text("DELETE FROM hub_chunks WHERE document_id = CAST(:id AS uuid)"), {"id": str(document_id)}
            )
            return result.rowcount or 0

    def delete_collection(self, collection_id: uuid.UUID) -> int:
        with get_engine().begin() as conn:
            result = conn.execute(
                text("DELETE FROM hub_chunks WHERE collection_id = CAST(:id AS uuid)"), {"id": str(collection_id)}
            )
            return result.rowcount or 0

    def count_document(self, document_id: uuid.UUID) -> int:
        with get_engine().connect() as conn:
            return conn.execute(
                text("SELECT count(*) FROM hub_chunks WHERE document_id = CAST(:id AS uuid)"),
                {"id": str(document_id)},
            ).scalar_one()

    def _supports_iterative_scan(self, conn) -> bool:
        if self._iterative_scan is None:
            version = conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one_or_none() or "0"
            major, minor = (version.split(".") + ["0"])[:2]
            self._iterative_scan = (int(major), int(minor)) >= (0, 8)
        return self._iterative_scan

    def search(
        self,
        query_text: str,
        query_embedding: list[float],
        collection_ids: Sequence[uuid.UUID],
        candidates: int,
        hybrid: bool,
    ) -> list[RetrievedChunk]:
        if not collection_ids:
            return []
        settings = get_settings()
        terms = lexical_terms(query_text) if hybrid else []
        params = {
            "qvec": _vector_literal(query_embedding),
            "cids": [str(c) for c in collection_ids],
            "n": candidates,
            "rrf_k": settings.rrf_k,
            "lang": settings.fts_language,
            # Prefix match on longer words: "définit" (stem "defin") must reach
            # "Définitions" (stem "definit").
            "tsq": " | ".join(f"{t}:*" if len(t) >= 5 and not t.isdigit() else t for t in terms),
        }
        lexical_cte = (
            """
            lex AS (
                SELECT id, row_number() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS r
                FROM hub_chunks, to_tsquery(CAST(:lang AS regconfig), :tsq) q
                WHERE collection_id = ANY(CAST(:cids AS uuid[])) AND tsv @@ q
                ORDER BY ts_rank_cd(tsv, q) DESC
                LIMIT :n
            )
            """
            if terms
            else "lex AS (SELECT NULL::uuid AS id, NULL::bigint AS r WHERE false)"
        )
        sql = f"""
            WITH vec AS (
                SELECT id, row_number() OVER (ORDER BY embedding <=> CAST(:qvec AS vector)) AS r
                FROM (
                    SELECT id, embedding FROM hub_chunks
                    WHERE collection_id = ANY(CAST(:cids AS uuid[]))
                    ORDER BY embedding <=> CAST(:qvec AS vector)
                    LIMIT :n
                ) v
            ),
            {lexical_cte}
            SELECT c.id, c.document_id, c.collection_id, c.content, c.metadata,
                   (c.embedding <=> CAST(:qvec AS vector)) AS distance,
                   COALESCE(1.0 / (:rrf_k + vec.r), 0) + COALESCE(1.0 / (:rrf_k + lex.r), 0) AS score
            FROM hub_chunks c
            LEFT JOIN vec ON vec.id = c.id
            LEFT JOIN lex ON lex.id = c.id
            WHERE vec.id IS NOT NULL OR lex.id IS NOT NULL
            ORDER BY score DESC, distance ASC
        """
        with get_engine().begin() as conn:
            if self._supports_iterative_scan(conn):
                conn.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
            conn.execute(text(f"SET LOCAL hnsw.ef_search = {max(40, candidates * 2)}"))
            rows = conn.execute(text(sql), params).mappings().all()
        return [
            RetrievedChunk(
                id=row["id"] if isinstance(row["id"], uuid.UUID) else uuid.UUID(str(row["id"])),
                document_id=uuid.UUID(str(row["document_id"])),
                collection_id=uuid.UUID(str(row["collection_id"])),
                content=row["content"],
                metadata=row["metadata"] if isinstance(row["metadata"], dict) else json.loads(row["metadata"]),
                distance=float(row["distance"]),
                score=float(row["score"]),
            )
            for row in rows
        ]


class InMemoryChunkStore:
    """Reference implementation used by the test-suite (same contract as PgChunkStore)."""

    def __init__(self) -> None:
        self.chunks: dict[uuid.UUID, ChunkRecord] = {}

    def add_chunks(self, chunks: Sequence[ChunkRecord]) -> None:
        for chunk in chunks:
            self.chunks[chunk.id] = chunk

    def delete_document(self, document_id: uuid.UUID) -> int:
        doomed = [k for k, c in self.chunks.items() if c.document_id == document_id]
        for key in doomed:
            del self.chunks[key]
        return len(doomed)

    def delete_collection(self, collection_id: uuid.UUID) -> int:
        doomed = [k for k, c in self.chunks.items() if c.collection_id == collection_id]
        for key in doomed:
            del self.chunks[key]
        return len(doomed)

    def count_document(self, document_id: uuid.UUID) -> int:
        return sum(1 for c in self.chunks.values() if c.document_id == document_id)

    @staticmethod
    def _cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return 1.0 - dot / (na * nb)

    def search(self, query_text, query_embedding, collection_ids, candidates, hybrid):
        settings = get_settings()
        pool = [c for c in self.chunks.values() if c.collection_id in set(collection_ids)]
        by_distance = sorted(pool, key=lambda c: self._cosine_distance(query_embedding, c.embedding))[:candidates]
        ranks_vec = {c.id: i + 1 for i, c in enumerate(by_distance)}
        ranks_lex: dict[uuid.UUID, int] = {}
        terms = lexical_terms(query_text) if hybrid else []
        if terms:
            scored = []
            for c in pool:
                words = set(_TOKEN_RE.findall(c.content.lower()))
                hits = sum(1 for t in terms if t in words)
                if hits:
                    scored.append((hits, c))
            scored.sort(key=lambda x: -x[0])
            ranks_lex = {c.id: i + 1 for i, (_, c) in enumerate(scored[:candidates])}
        ids = set(ranks_vec) | set(ranks_lex)
        results = []
        for cid in ids:
            c = self.chunks[cid]
            score = 0.0
            if cid in ranks_vec:
                score += 1.0 / (settings.rrf_k + ranks_vec[cid])
            if cid in ranks_lex:
                score += 1.0 / (settings.rrf_k + ranks_lex[cid])
            results.append(
                RetrievedChunk(
                    id=c.id,
                    document_id=c.document_id,
                    collection_id=c.collection_id,
                    content=c.content,
                    metadata=dict(c.metadata),
                    distance=self._cosine_distance(query_embedding, c.embedding),
                    score=score,
                )
            )
        results.sort(key=lambda r: (-r.score, r.distance))
        return results


_store_override: ChunkStore | None = None
_pg_store: PgChunkStore | None = None


def get_store() -> ChunkStore:
    global _pg_store
    if _store_override is not None:
        return _store_override
    if _pg_store is None:
        _pg_store = PgChunkStore()
    return _pg_store


def set_store_override(store: ChunkStore | None) -> None:
    global _store_override
    _store_override = store
