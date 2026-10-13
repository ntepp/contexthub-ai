"""Document ingestion: load → chunk → embed (batched) → store.

Idempotent by construction: the document's existing chunks are deleted before
indexing starts, so a Celery retry after a partial run never leaves duplicates.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from src.config import get_settings
from src.db.models import DOC_FAILED, DOC_PROCESSING, DOC_READY, Document, utcnow
from src.db.session import session_scope
from src.ingestion import storage
from src.ingestion.chunking import chunk_pages
from src.ingestion.filetypes import IngestionError, sniff
from src.ingestion.loaders import load_file
from src.ingestion.url_fetch import fetch_url
from src.retrieval.embeddings import get_embeddings
from src.retrieval.store import ChunkRecord, get_store
from src.utils.logging_config import get_logger

log = get_logger()


@dataclass
class IngestionResult:
    document_id: str
    chunk_count: int
    page_count: int
    seconds: float


def _update(document_id: uuid.UUID, **fields) -> None:
    with session_scope() as db:
        doc = db.get(Document, document_id)
        if doc is not None:
            for key, value in fields.items():
                setattr(doc, key, value)


def _materialise_url(doc: Document) -> tuple[Path, str]:
    fetched = fetch_url(doc.source_url)
    ext = {"application/pdf": ".pdf", "text/html": ".html", "text/markdown": ".md"}.get(fetched.mime_type, ".txt")
    staged = storage.stage_bytes(fetched.content)
    destination = storage.commit_staged(staged, storage.final_path(doc.collection_id, doc.id, ext))
    _update(
        doc.id,
        storage_path=storage.relative_to_root(destination),
        mime_type=fetched.mime_type,
        size_bytes=staged.size,
        sha256=staged.sha256,
    )
    return destination, fetched.mime_type


def ingest_document(document_id: uuid.UUID | str) -> IngestionResult:
    settings = get_settings()
    document_id = uuid.UUID(str(document_id))
    started = time.time()

    with session_scope() as db:
        doc = db.get(Document, document_id)
        if doc is None:
            raise IngestionError(f"Document {document_id} introuvable.")
        doc.status = DOC_PROCESSING
        doc.error_message = None
        doc.progress_current = 0
        doc.progress_total = 0
        db.flush()
        db.expunge(doc)

    store = get_store()
    removed = store.delete_document(document_id)
    if removed:
        log.info("[ingestion] %d chunks existants supprimés (reprise idempotente)", removed)

    if doc.source_url and not doc.storage_path:
        path, mime = _materialise_url(doc)
    else:
        path = storage.resolve_stored(doc.storage_path)
        mime = doc.mime_type
        if path is None or mime is None:
            raise IngestionError("Fichier source introuvable sur le disque.")
        sniff(path, mime)

    loaded = load_file(path, mime)
    chunks = chunk_pages(loaded.pages, settings.chunk_size, settings.chunk_overlap)
    if not chunks:
        raise IngestionError(
            "Aucun texte exploitable dans ce document"
            + (" (document numérisé : activez l'OCR)." if loaded.page_count else ".")
        )

    total = len(chunks)
    _update(document_id, progress_total=total, page_count=loaded.page_count, warnings=loaded.warnings or None)
    log.info("[ingestion] %s : %d chunks à indexer par lots de %d", doc.filename, total, settings.embed_batch_size)

    embeddings = get_embeddings()
    batch_size = max(1, settings.embed_batch_size)
    for start in range(0, total, batch_size):
        batch = chunks[start : start + batch_size]
        vectors = embeddings.embed_documents([c.content for c in batch])
        store.add_chunks(
            [
                ChunkRecord(
                    document_id=document_id,
                    collection_id=doc.collection_id,
                    chunk_index=start + offset,
                    content=chunk.content,
                    metadata={
                        "filename": doc.filename,
                        "page": chunk.page,
                        "section": chunk.section,
                        "section_path": chunk.section_path,
                    },
                    embedding=vector,
                )
                for offset, (chunk, vector) in enumerate(zip(batch, vectors))
            ]
        )
        done = min(start + batch_size, total)
        _update(document_id, progress_current=done)
        log.info("[ingestion] lot %d : %d/%d", start // batch_size + 1, done, total)

    elapsed = round(time.time() - started, 2)
    _update(
        document_id,
        status=DOC_READY,
        chunk_count=total,
        progress_current=total,
        indexed_at=utcnow(),
    )
    log.info("[ingestion] %s indexé : %d chunks, %.1fs", doc.filename, total, elapsed)
    return IngestionResult(str(document_id), total, loaded.page_count, elapsed)


def mark_failed(document_id: uuid.UUID | str, message: str) -> None:
    document_id = uuid.UUID(str(document_id))
    try:
        get_store().delete_document(document_id)
    except Exception:  # pragma: no cover - best effort cleanup
        log.exception("[ingestion] nettoyage des chunks impossible pour %s", document_id)
    _update(document_id, status=DOC_FAILED, error_message=message[:2000])

