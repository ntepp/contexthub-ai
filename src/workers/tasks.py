import os

from src.workers.celery_app import celery_app


@celery_app.task(bind=True)
def ingest_document(self, file_path: str) -> dict:
    """Async Celery task: load, embed and index a document into ChromaDB."""
    try:
        # Import here to avoid circular imports at module level
        from src.rag.rag_pipeline import load_source
        load_source(file_path)
        return {"status": "success", "file": os.path.basename(file_path)}
    except Exception as exc:
        raise self.retry(exc=exc, countdown=5, max_retries=3)
