"""Optional cross-encoder re-ranking (RERANKER=cross_encoder).

Requires the optional dependency set in ``requirements-rerank.txt``
(sentence-transformers + torch). The model runs locally; nothing leaves the host.

A cross-encoder reads every (query, passage) pair through the whole model
(``bge-reranker-v2-m3``: 568 M parameters): its cost grows with each pair and, on a CPU, it can
outweigh the rest of the answer. So:

* only the ``limit`` first candidates (fused order) are scored, the others keep their order
  behind them (``RERANK_CANDIDATES``; the caller shares ``RERANK_MAX_PAIRS`` between the
  question and the planner's sub-queries);
* a pair already scored (same model, same query, same passage text) is read from a cache, not
  scored again — a question asked again after a network error costs nothing here;
* pairs are scored in batches, in inference mode, on CUDA when PyTorch sees a GPU
  (``RERANKER_DEVICE``), with ``RERANKER_THREADS`` CPU threads, one call at a time (concurrent
  calls on a CPU only slow each other down), each pair cut at ``RERANKER_MAX_LENGTH`` tokens.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

from src.config import get_settings
from src.retrieval.store import RetrievedChunk
from src.utils.logging_config import get_logger

log = get_logger()

_CACHE_SIZE = 4096
_cache: OrderedDict[tuple[str, str, str], float] = OrderedDict()
_cache_lock = threading.Lock()
# One scoring call at a time: the model is shared by the API's threads.
_model_lock = threading.Lock()


@dataclass
class Reranked:
    """Result of :func:`rerank`: the chunks in their new order and what it cost."""

    chunks: list[RetrievedChunk]
    pairs: int = 0  # (query, passage) pairs re-ranked: the unit of RERANK_MAX_PAIRS
    scored: int = 0  # of which scored by the model; the others came from the cache
    seconds: float = 0.0

    @property
    def cached(self) -> int:
        return self.pairs - self.scored


def enabled() -> bool:
    return get_settings().reranker == "cross_encoder"


def resolve_device(wanted: str) -> str:
    """``RERANKER_DEVICE``: "auto" is CUDA when PyTorch sees a GPU, else the CPU."""
    wanted = (wanted or "").strip().lower()
    if wanted and wanted != "auto":
        return wanted
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


@lru_cache
def _cross_encoder():
    """(model, device), loaded once."""
    settings = get_settings()
    if settings.hf_hub_offline:
        # Read by huggingface_hub when it is first imported: no call to the Hub at all, the
        # model is read from the local cache only (sovereign deployments, offline sites).
        os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        import torch
        from sentence_transformers import CrossEncoder
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "RERANKER=cross_encoder requiert sentence-transformers : pip install -r requirements-rerank.txt"
        ) from exc
    model = settings.reranker_model
    device = resolve_device(settings.reranker_device)
    if device.startswith("cpu") and settings.reranker_threads:
        torch.set_num_threads(settings.reranker_threads)
    log.info(
        "[rerank] chargement du cross-encoder %s sur %s (%s thread(s) CPU, %d jetons par paire)%s",
        model,
        device,
        torch.get_num_threads(),
        settings.reranker_max_length,
        " (hors ligne)" if settings.hf_hub_offline else "",
    )
    try:
        encoder = CrossEncoder(
            model,
            device=device,
            max_length=settings.reranker_max_length,
            local_files_only=settings.hf_hub_offline,
        )
    except OSError as exc:
        if not settings.hf_hub_offline:
            raise
        raise RuntimeError(
            f"Modèle de re-classement {model} absent du cache local (HF_HUB_OFFLINE=1) : "
            "téléchargez-le une fois avec HF_HUB_OFFLINE=0, ou copiez le cache HuggingFace sur le serveur."
        ) from exc
    return encoder, device


def _key(model: str, query: str, content: str) -> tuple[str, str, str]:
    return model, query, hashlib.sha1(content.encode("utf-8")).hexdigest()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _inference_mode():
    try:
        import torch
    except ImportError:  # pragma: no cover - torch comes with sentence-transformers
        return contextlib.nullcontext()
    return torch.inference_mode()


def _score(pairs: Sequence[tuple[str, str]]) -> list[float]:
    settings = get_settings()
    encoder, _ = _cross_encoder()
    with _model_lock, _inference_mode():
        scores = encoder.predict(list(pairs), batch_size=settings.reranker_batch_size, show_progress_bar=False)
    return [float(s) for s in scores]


def rerank(query: str, chunks: Sequence[RetrievedChunk], limit: int) -> Reranked:
    """The ``limit`` first ``chunks`` ordered by the cross-encoder, then the others unchanged.

    Sets ``rerank_score`` on the re-ranked chunks; ``score`` (fused) is left as it was.
    Nothing happens when the re-ranker is off or fewer than two pairs would be compared."""
    chunks = list(chunks)
    head = chunks[: max(limit, 0)]
    if not enabled() or len(head) < 2:
        return Reranked(chunks)
    started = time.perf_counter()
    model = get_settings().reranker_model
    keys = [_key(model, query, c.content) for c in head]
    scores: dict[tuple[str, str, str], float] = {}
    with _cache_lock:
        for key in keys:
            if key in _cache:
                _cache.move_to_end(key)
                scores[key] = _cache[key]
    missing: dict[tuple[str, str, str], str] = {}
    for key, chunk in zip(keys, head):
        if key not in scores:
            missing.setdefault(key, chunk.content)
    if missing:
        values = _score([(query, content) for content in missing.values()])
        with _cache_lock:
            for key, value in zip(missing, values):
                scores[key] = value
                _cache[key] = value
                _cache.move_to_end(key)
            while len(_cache) > _CACHE_SIZE:
                _cache.popitem(last=False)
    for key, chunk in zip(keys, head):
        chunk.rerank_score = scores[key]
    ordered = sorted(head, key=lambda c: -c.rerank_score) + chunks[len(head) :]
    return Reranked(ordered, pairs=len(head), scored=len(missing), seconds=time.perf_counter() - started)
