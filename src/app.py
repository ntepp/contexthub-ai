# src/app.py
import os
import uuid
from pathlib import Path

from celery.result import AsyncResult
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

from src.rag.rag_pipeline import create_and_run_graph, load_source
from src.workers.celery_app import celery_app
from src.workers.tasks import ingest_document

UPLOADS_DIR = Path("./uploads")
UPLOADS_DIR.mkdir(exist_ok=True)

app = FastAPI(title="ContextHub RAG API")


class SourceLoadRequest(BaseModel):
    source: str


class QuestionRequest(BaseModel):
    question: str


# ---------------------------------------------------------------------------
# US 1.1 – Async PDF upload
# ---------------------------------------------------------------------------

@app.post("/upload", status_code=202)
async def upload_pdf(file: UploadFile = File(...)):
    """Upload a PDF for async ingestion. Returns a task_id to poll status."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    dest = UPLOADS_DIR / f"{uuid.uuid4()}_{file.filename}"
    dest.write_bytes(await file.read())

    task = ingest_document.delay(str(dest))
    return {"task_id": task.id, "status": "pending"}


@app.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """Poll the status of an async ingestion task."""
    result = AsyncResult(task_id, app=celery_app)
    payload = {"task_id": task_id, "status": result.status}
    if result.successful():
        payload["result"] = result.result
    elif result.failed():
        payload["error"] = str(result.result)
    return payload


# ---------------------------------------------------------------------------
# Synchronous URL loading (kept for backwards compatibility)
# ---------------------------------------------------------------------------

@app.post("/load")
async def load_source_endpoint(request: SourceLoadRequest):
    """Synchronously load a URL or local PDF path into the vector store."""
    try:
        load_source(request.source)
        return {"message": "Source loaded successfully!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# US 1.3 – Q&A with sourced answers
# ---------------------------------------------------------------------------

@app.post("/ask")
async def ask_question_endpoint(request: QuestionRequest):
    """Ask a question; returns the answer and its source references."""
    try:
        result = create_and_run_graph(request.question)
        return {"answer": result["answer"], "sources": result["sources"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
