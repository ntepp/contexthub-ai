# src/app.py
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from celery.result import AsyncResult
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.db.models import Collection
from src.db.session import get_db, init_db
from src.rag.rag_pipeline import create_and_run_graph, load_source
from src.workers.celery_app import celery_app
from src.workers.tasks import ingest_document

UPLOADS_DIR = Path("./uploads")
UPLOADS_DIR.mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="ContextHub RAG API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class SourceLoadRequest(BaseModel):
    source: str
    collection: str = "default"


class QuestionRequest(BaseModel):
    question: str
    collection: str = "default"


class CollectionCreate(BaseModel):
    name: str
    description: Optional[str] = None


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

@app.get("/collections")
async def list_collections(db: Session = Depends(get_db)):
    """List all document collections."""
    cols = db.query(Collection).order_by(Collection.created_at.desc()).all()
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "description": c.description,
            "document_count": c.document_count,
            "created_at": c.created_at.isoformat(),
        }
        for c in cols
    ]


@app.post("/collections", status_code=201)
async def create_collection(body: CollectionCreate, db: Session = Depends(get_db)):
    """Create a named collection."""
    existing = db.query(Collection).filter(Collection.name == body.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Collection '{body.name}' already exists.")
    col = Collection(name=body.name, description=body.description)
    db.add(col)
    db.commit()
    db.refresh(col)
    return {"id": str(col.id), "name": col.name, "description": col.description}


# ---------------------------------------------------------------------------
# Document ingestion
# ---------------------------------------------------------------------------

@app.post("/upload", status_code=202)
async def upload_pdf(
    file: UploadFile = File(...),
    collection: str = "default",
    db: Session = Depends(get_db),
):
    """Upload a PDF for async ingestion into a collection."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    dest = UPLOADS_DIR / f"{uuid.uuid4()}_{file.filename}"
    dest.write_bytes(await file.read())

    # Ensure collection exists
    col = db.query(Collection).filter(Collection.name == collection).first()
    if not col:
        col = Collection(name=collection)
        db.add(col)
        db.commit()

    task = ingest_document.delay(str(dest), collection)
    return {"task_id": task.id, "status": "pending", "collection": collection}


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


@app.post("/load")
async def load_source_endpoint(request: SourceLoadRequest, db: Session = Depends(get_db)):
    """Synchronously load a URL or local PDF path into a collection."""
    try:
        col = db.query(Collection).filter(Collection.name == request.collection).first()
        if not col:
            col = Collection(name=request.collection)
            db.add(col)
            db.commit()
        load_source(request.source, collection=request.collection)
        return {"message": "Source loaded successfully!", "collection": request.collection}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Q&A
# ---------------------------------------------------------------------------

@app.post("/ask")
async def ask_question_endpoint(request: QuestionRequest):
    """Ask a question against a collection; returns answer and source references."""
    try:
        result = create_and_run_graph(request.question, collection=request.collection)
        return {"answer": result["answer"], "sources": result["sources"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Models discovery
# ---------------------------------------------------------------------------

@app.get("/models")
async def list_models():
    """Return available LLM models (Ollama local + OpenAI static list)."""
    models = []

    # Ollama local models
    ollama_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{ollama_base}/api/tags")
            if resp.status_code == 200:
                for m in resp.json().get("models", []):
                    models.append({"id": m["name"], "provider": "ollama", "name": m["name"]})
    except Exception:
        pass  # Ollama not running — skip

    # OpenAI models (static, only shown when API key is set)
    if os.getenv("OPENAI_API_KEY"):
        for name in ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"]:
            models.append({"id": name, "provider": "openai", "name": name})

    return {"models": models}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
