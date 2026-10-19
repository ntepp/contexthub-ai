from __future__ import annotations

import httpx

from src.audit import service as audit
from src.ingestion.filetypes import IngestionError
from src.ingestion.pipeline import ingest_document as run_ingestion
from src.ingestion.pipeline import mark_failed
from src.utils.logging_config import get_logger
from src.workers.celery_app import celery_app

log = get_logger()

# Transient failures worth retrying (embedding server restarting, network blips).
# Everything else — corrupt file, unsupported format — fails immediately: replaying a
# deterministic error only delays the inevitable (see docs/incidents.md).
_TRANSIENT: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError, httpx.TransportError, OSError)
try:  # Ollama surfaces runner crashes as ResponseError (HTTP 500)
    import ollama

    _TRANSIENT += (ollama.ResponseError,)
except ImportError:  # pragma: no cover
    pass
_MAX_RETRIES = 3


@celery_app.task(bind=True, time_limit=1800, soft_time_limit=1740, max_retries=_MAX_RETRIES)
def ingest_document(self, document_id: str) -> dict:
    try:
        result = run_ingestion(document_id)
        audit.record("document.index", resource_type="document", resource_id=document_id,
                     details={"chunks": result.chunk_count, "pages": result.page_count, "seconds": result.seconds})
        return {"status": "success", "document_id": document_id, "chunks": result.chunk_count}
    except IngestionError as exc:
        log.warning("[ingestion] échec définitif %s : %s", document_id, exc)
        mark_failed(document_id, str(exc))
        audit.record("document.index", outcome="failure", resource_type="document", resource_id=document_id,
                     details={"error": str(exc)})
        return {"status": "failed", "document_id": document_id, "error": str(exc)}
    except _TRANSIENT as exc:
        if self.request.retries >= _MAX_RETRIES:
            message = f"Service indisponible après {_MAX_RETRIES} tentatives : {exc}"
            mark_failed(document_id, message)
            audit.record("document.index", outcome="failure", resource_type="document", resource_id=document_id,
                         details={"error": message})
            return {"status": "failed", "document_id": document_id, "error": message}
        countdown = 10 * (2 ** self.request.retries)
        log.warning("[ingestion] erreur transitoire (%s), nouvel essai dans %ds", exc, countdown)
        raise self.retry(exc=exc, countdown=countdown)
    except Exception as exc:
        log.exception("[ingestion] erreur inattendue pour %s", document_id)
        mark_failed(document_id, "Erreur interne pendant l'indexation.")
        audit.record("document.index", outcome="failure", resource_type="document", resource_id=document_id,
                     details={"error": type(exc).__name__})
        return {"status": "failed", "document_id": document_id, "error": "internal"}


@celery_app.task
def purge_expired() -> dict:
    from src.maintenance import purge_expired_data

    return purge_expired_data()
