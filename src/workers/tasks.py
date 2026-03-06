import os

from src.workers.celery_app import celery_app


@celery_app.task(bind=True)
def ingest_document(self, file_path: str, collection: str = "default") -> dict:
    """Async Celery task: load, embed and index a document into pgvector."""
    try:
        from src.rag.rag_pipeline import load_source
        load_source(file_path, collection=collection)
        return {"status": "success", "file": os.path.basename(file_path), "collection": collection}
    except Exception as exc:
        raise self.retry(exc=exc, countdown=5, max_retries=3)
