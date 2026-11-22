"""Optional cross-encoder re-ranking (RERANKER=cross_encoder).

Requires the optional dependency set in ``requirements-rerank.txt``
(sentence-transformers + torch). The model runs locally; nothing leaves the host.
"""

from __future__ import annotations

import os
from functools import lru_cache

from src.config import get_settings
from src.retrieval.store import RetrievedChunk
from src.utils.logging_config import get_logger

log = get_logger()


@lru_cache
def _cross_encoder():
    settings = get_settings()
    if settings.hf_hub_offline:
        # Read by huggingface_hub when it is first imported: no call to the Hub at all, the
        # model is read from the local cache only (sovereign deployments, offline sites).
        os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "RERANKER=cross_encoder requiert sentence-transformers : pip install -r requirements-rerank.txt"
        ) from exc
    model = settings.reranker_model
    log.info("[rerank] chargement du cross-encoder %s%s", model, " (hors ligne)" if settings.hf_hub_offline else "")
    try:
        return CrossEncoder(model, local_files_only=settings.hf_hub_offline)
    except OSError as exc:
        if not settings.hf_hub_offline:
            raise
        raise RuntimeError(
            f"Modèle de re-classement {model} absent du cache local (HF_HUB_OFFLINE=1) : "
            "téléchargez-le une fois avec HF_HUB_OFFLINE=0, ou copiez le cache HuggingFace sur le serveur."
        ) from exc


def rerank(question: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    if get_settings().reranker != "cross_encoder" or len(chunks) < 2:
        return chunks
    scores = _cross_encoder().predict([(question, c.content) for c in chunks])
    for chunk, score in zip(chunks, scores):
        chunk.score = float(score)
    return sorted(chunks, key=lambda c: -c.score)
