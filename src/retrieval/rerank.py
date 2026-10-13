"""Optional cross-encoder re-ranking (RERANKER=cross_encoder).

Requires the optional dependency set in ``requirements-rerank.txt``
(sentence-transformers + torch). The model runs locally; nothing leaves the host.
"""

from __future__ import annotations

from functools import lru_cache

from src.config import get_settings
from src.retrieval.store import RetrievedChunk
from src.utils.logging_config import get_logger

log = get_logger()


@lru_cache
def _cross_encoder():
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "RERANKER=cross_encoder requiert sentence-transformers : pip install -r requirements-rerank.txt"
        ) from exc
    model = get_settings().reranker_model
    log.info("[rerank] chargement du cross-encoder %s", model)
    return CrossEncoder(model)


def rerank(question: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    if get_settings().reranker != "cross_encoder" or len(chunks) < 2:
        return chunks
    scores = _cross_encoder().predict([(question, c.content) for c in chunks])
    for chunk, score in zip(chunks, scores):
        chunk.score = float(score)
    return sorted(chunks, key=lambda c: -c.score)
