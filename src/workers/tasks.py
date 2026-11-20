from __future__ import annotations

import uuid

import httpx
from celery.exceptions import SoftTimeLimitExceeded

from src.audit import service as audit
from src.db.models import DOC_QUEUED, Document
from src.db.session import session_scope
from src.ingestion.filetypes import IngestionError
from src.ingestion.loaders import OcrOptions, OcrRequired
from src.ingestion.pipeline import ingest_document as run_ingestion
from src.ingestion.pipeline import mark_failed
from src.utils.logging_config import get_logger
from src.workers.celery_app import celery_app

log = get_logger()

# Transient failures worth retrying (embedding server restarting, network blips).
# Everything else — corrupt file, unsupported format — fails immediately: replaying a
# deterministic error only delays the inevitable (see docs/incidents.md). OCR engine
# failures are OcrError, never OSError: they are not retried (src/ocr/tesseract.py).
_TRANSIENT: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError, httpx.TransportError, OSError)
try:  # Ollama surfaces runner crashes as ResponseError (HTTP 500)
    import ollama

    _TRANSIENT += (ollama.ResponseError,)
except ImportError:  # pragma: no cover
    pass
_MAX_RETRIES = 3
# Whole-document limit of an OCR job; each page is bounded by OCR_PAGE_TIMEOUT_SECONDS.
_OCR_TIME_LIMIT = 6 * 3600


def _fail(document_id: str, message: str, error: str | None = None) -> dict:
    mark_failed(document_id, message)
    audit.record("document.index", outcome="failure", resource_type="document", resource_id=document_id,
                 details={"error": error or message})
    return {"status": "failed", "document_id": document_id, "error": error or message}


def dispatch_ocr(document_id: str, all_pages: bool = False) -> str:
    """Queue the document on the "ocr" queue (status back to queued); returns the task id."""
    task_id = str(uuid.uuid4())
    with session_scope() as db:
        doc = db.get(Document, uuid.UUID(str(document_id)))
        if doc is not None:
            doc.status = DOC_QUEUED
            doc.task_id = task_id
            doc.progress_current = doc.progress_total = 0
    # Eager mode (tests) runs the task inside this call: the row is updated before it.
    ocr_document.apply_async(args=[str(document_id)], kwargs={"all_pages": all_pages}, task_id=task_id)
    return task_id


def _index(task, document_id: str, ocr: OcrOptions) -> dict:
    try:
        result = run_ingestion(document_id, ocr)
        audit.record("document.index", resource_type="document", resource_id=document_id,
                     details={"chunks": result.chunk_count, "pages": result.page_count, "seconds": result.seconds,
                              "read_mode": result.read_mode, "ocr_pages": result.ocr_pages})
        return {"status": "success", "document_id": document_id, "chunks": result.chunk_count}
    except OcrRequired:
        log.info("[ingestion] %s : pages numérisées, envoi vers la file OCR", document_id)
        try:
            dispatch_ocr(document_id)
        except Exception as exc:
            log.exception("[ingestion] file OCR indisponible pour %s", document_id)
            return _fail(document_id, "File d'attente OCR indisponible : relancez l'OCR plus tard.", type(exc).__name__)
        return {"status": "deferred", "document_id": document_id, "queue": "ocr"}
    except IngestionError as exc:
        log.warning("[ingestion] échec définitif %s : %s", document_id, exc)
        return _fail(document_id, str(exc))
    except SoftTimeLimitExceeded:
        return _fail(document_id, "Délai maximal d'indexation dépassé.", "time_limit")
    except _TRANSIENT as exc:
        if task.request.retries >= _MAX_RETRIES:
            return _fail(document_id, f"Service indisponible après {_MAX_RETRIES} tentatives : {exc}")
        countdown = 10 * (2 ** task.request.retries)
        log.warning("[ingestion] erreur transitoire (%s), nouvel essai dans %ds", exc, countdown)
        raise task.retry(exc=exc, countdown=countdown)
    except Exception as exc:
        log.exception("[ingestion] erreur inattendue pour %s", document_id)
        return _fail(document_id, "Erreur interne pendant l'indexation.", type(exc).__name__)


@celery_app.task(bind=True, time_limit=1800, soft_time_limit=1740, max_retries=_MAX_RETRIES)
def ingest_document(self, document_id: str) -> dict:
    """Default queue. A document with pages to OCR is handed over to ``ocr_document``."""
    return _index(self, document_id, OcrOptions(mode="defer"))


@celery_app.task(bind=True, time_limit=_OCR_TIME_LIMIT, soft_time_limit=_OCR_TIME_LIMIT - 60,
                 max_retries=_MAX_RETRIES)
def ocr_document(self, document_id: str, all_pages: bool = False) -> dict:
    """Queue "ocr" (see ``task_routes``): reads the scanned pages, then indexes as usual."""
    return _index(self, document_id, OcrOptions(mode="run", all_pages=all_pages))


@celery_app.task
def purge_expired() -> dict:
    from src.maintenance import purge_expired_data

    return purge_expired_data()
