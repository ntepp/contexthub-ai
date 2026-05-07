import os

from src.workers.celery_app import celery_app


def _update_document_status(task_id: str, status: str, chunk_count: int = 0) -> None:
    """Update hub_documents status from within the Celery worker."""
    try:
        from src.db.session import SessionLocal
        from src.db.models import Document
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.task_id == task_id).first()
            if doc:
                doc.status = status
                if chunk_count:
                    doc.chunk_count = chunk_count
                db.commit()
        finally:
            db.close()
    except Exception:
        pass  # DB update failure must not crash the task


@celery_app.task(bind=True, time_limit=600, soft_time_limit=540)
def ingest_document(self, file_path: str, collection: str = "default") -> dict:
    """Async Celery task: load, embed and index a document into pgvector."""
    try:
        from src.rag.rag_pipeline import load_source
        load_source(file_path, collection=collection)
        _update_document_status(self.request.id, "ready")
        return {"status": "success", "file": os.path.basename(file_path), "collection": collection}
    except Exception as exc:
        _update_document_status(self.request.id, "failed")
        raise self.retry(exc=exc, countdown=5, max_retries=3)
