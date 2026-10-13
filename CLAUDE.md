# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Run the application

**CLI mode:**
```bash
python -m src.main
```

**API mode (FastAPI + Swagger UI at `/docs`):**
```bash
uvicorn src.app:app --reload
```

**Celery worker (required for `POST /upload` async ingestion):**
```bash
celery -A src.workers.celery_app worker --loglevel=info
```

**Frontend dev server:**
```bash
cd frontend && npm run dev
```

### Start infrastructure
```bash
docker-compose up
```
Starts **Redis** (port 6379) and **PostgreSQL + pgvector** (port 5432). Ollama must be running separately.

### Database migrations (Alembic)
```bash
alembic upgrade head        # apply all migrations
alembic revision --autogenerate -m "description"  # generate new migration
```

### Install dependencies
```bash
pip install -r requirements.txt
cd frontend && npm install
```

### Pull Ollama models
```bash
ollama pull llama3.1:8b
ollama pull bge-m3
```

## Architecture

### Overview

A RAG (Retrieval Augmented Generation) pipeline using **LangGraph** to orchestrate two-step graph execution: `retrieve → generate`. Sources are loaded once, embedded, and cached to avoid re-processing. PDF uploads are handled asynchronously via **Celery + Redis**. Vectors are stored in **PostgreSQL + pgvector** (via `langchain-postgres`).

### Data Flow

1. **PDF Upload** → `POST /upload?collection=<name>` saves file to `./uploads/`, dispatches `ingest_document` Celery task, returns `task_id`
2. **URL Load** → `POST /load` runs synchronously with `collection` in body
3. **Cache check** → Redis stores `collection:source` keys; if cached, skip loading
4. **Loading** → `WebDataLoader` (URLs) or `PDFDataLoader` (local PDFs) load raw content
5. **Splitting** → `split_text()` chunks documents via `RecursiveCharacterTextSplitter`
6. **Embedding + storage** → `bge-m3` (via Ollama) generates vectors, stored in PostgreSQL pgvector per collection
7. **Query** → LangGraph runs `retrieve` (vector search, cosine **distance** ≤ `RETRIEVAL_MAX_DISTANCE`) then `generate` (LLM via factory)
8. **Answer** → Returns `{"answer": "...", "sources": [{source, page}, ...]}`

### Key modules

| Module | Responsibility |
|--------|---------------|
| `src/rag/rag_pipeline.py` | Core orchestration: `load_source(source, collection)` and `create_and_run_graph(question, collection)` |
| `src/llm/factory.py` | `LLMFactory`: creates Ollama or OpenAI LLM based on `LLM_PROVIDER` env |
| `src/llm/llm_chain.py` | Thin wrapper: `initialize_model_llm()` delegates to `LLMFactory` |
| `src/vector_store/pgvector.py` | `PGVectorManager`: pgvector via `langchain-postgres`, one collection = one LangChain collection |
| `src/vector_store/chroma.py` | Legacy ChromaDB (kept, not default) |
| `src/vector_store/factory.py` | Factory pattern: `PGVECTOR`, `CHROMA`, or `IN_MEMORY` via `VectorStoreType` enum |
| `src/db/models.py` | SQLAlchemy model: `Collection` (hub_collections table) |
| `src/db/session.py` | DB engine, `SessionLocal`, `get_db()` dependency, `init_db()` |
| `alembic/` | Database migrations — run `alembic upgrade head` on first start |
| `src/cache/` | Factory pattern: Redis-backed source caching |
| `src/data_loading/` | Abstract loader + concrete implementations (web, PDF) |
| `src/utils/source_type.py` | Classifies input as URL or local PDF |
| `src/workers/celery_app.py` | Celery app: Redis as broker (`/0`) and result backend (`/1`) |
| `src/workers/tasks.py` | `ingest_document(file_path, collection)` Celery task with auto-retry (max 3×) |
| `src/app.py` | FastAPI: `/upload`, `/tasks/{id}`, `/load`, `/ask`, `/collections`, `/models` |
| `src/main.py` | Interactive CLI loop |
| `frontend/` | Vite + React + TypeScript + TailwindCSS frontend |

### Configuration via `.env`

| Variable | Description | Default |
|---|---|---|
| `LLM_PROVIDER` | `ollama` or `openai` | `ollama` |
| `LLM_MODEL_NAME` | Model name for the chosen provider | *(required)* |
| `OPENAI_API_KEY` | Required only when `LLM_PROVIDER=openai` | — |
| `REDIS_HOST` | Redis hostname | `localhost` |
| `REDIS_PORT` | Redis port | `6379` |
| `POSTGRES_HOST` | PostgreSQL hostname | `localhost` |
| `POSTGRES_PORT` | PostgreSQL port | `5432` |
| `POSTGRES_USER` | PostgreSQL user | `postgres` |
| `POSTGRES_PASSWORD` | PostgreSQL password | `postgres` |
| `POSTGRES_DB` | Database name | `contexthub` |
| `OLLAMA_BASE_URL` | Ollama server URL | `http://localhost:11434` |
| `RETRIEVAL_MAX_DISTANCE` | Max cosine distance for a chunk to be retained (lower = stricter) | `0.75` |
| `RETRIEVAL_TOP_K` | Candidate chunks pulled from pgvector before the distance filter | `4` |
| `NO_ANSWER_TEXT` | Refusal sentence, emitted verbatim by both `retrieve` and the LLM; must match the corpus language | *(French default)* |
| `LOG_LEVEL` | Backend log verbosity | `INFO` |

### Vector store

pgvector runs in PostgreSQL (via Docker). Each collection is a separate LangChain PGVector collection in the `langchain_pg_embedding` table. The `hub_collections` table tracks user-facing metadata.

To revert to ChromaDB:
```python
# rag_pipeline.py — change VectorStoreType.PGVECTOR → VectorStoreType.CHROMA
```

### LangGraph state

The graph uses a `TypedDict` state with six fields: `question`, `context`, `answer`, `sources`, `collection`, `confidence_metric`.

**Scores are distances, not similarities.** `langchain-postgres` returns cosine *distance* from `similarity_search_with_score()` — `0.0` is a perfect match, higher is worse, and results arrive sorted ascending (`vectorstores.py`: `cosine_distance` labelled `"distance"`, `order_by(asc("distance"))`). `retrieve` pulls `RETRIEVAL_TOP_K` candidates, keeps those with `distance <= RETRIEVAL_MAX_DISTANCE`, and short-circuits to `NO_ANSWER` when nothing qualifies. `confidence_metric` is `max(0, 1 - mean(top-3 distances))` — clamped because cosine distance runs to 2.0.

`RETRIEVAL_TOP_K` is the binding constraint, not the threshold: the filter can only discard from those k candidates, never reach past them.

When `retrieve` short-circuits, `generate` returns that answer **without invoking the LLM**, so no `on_chat_model_stream` events are produced. `stream_answer()` therefore emits the short-circuit answer itself from the `retrieve` chain-end event — otherwise the UI renders an empty bubble.

### LLM switching

Change `LLM_PROVIDER` in `.env` — no code change needed:
- `LLM_PROVIDER=ollama` + `LLM_MODEL_NAME=llama3.1:8b` → uses `ChatOllama`
- `LLM_PROVIDER=openai` + `LLM_MODEL_NAME=gpt-4o` → uses `ChatOpenAI`

### API endpoints (Sprint 2)

| Method | Path | Description |
|---|---|---|
| `GET` | `/collections` | List all collections |
| `POST` | `/collections` | Create a collection |
| `POST` | `/upload?collection=<name>` | Upload PDF (async, returns task_id) |
| `GET` | `/tasks/{id}` | Poll Celery task status |
| `POST` | `/load` | Synchronous URL/PDF load with collection |
| `POST` | `/ask` | Q&A with collection scoping |
| `GET` | `/models` | List Ollama + OpenAI models |

### Frontend (Sprint 2)

- `frontend/src/pages/Chat.tsx` — Chat page with model selector + collection picker
- `frontend/src/pages/KnowledgeBase.tsx` — Upload page with Celery task tracking
- `frontend/src/components/layout/Sidebar.tsx` — Retractable sidebar
- `frontend/src/lib/api.ts` — All API calls (proxied via `/api` → `localhost:8000`)
