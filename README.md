# ContextHub AI — Enterprise Retrieval Copilot

> Production-oriented Retrieval Augmented Generation (RAG) platform designed to transform fragmented enterprise documentation into a grounded, auditable conversational knowledge system.

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.11x-009688?logo=fastapi)
![LangGraph](https://img.shields.io/badge/LangGraph-Orchestration-orange)
![pgvector](https://img.shields.io/badge/pgvector-PostgreSQL-336791?logo=postgresql)
![Celery](https://img.shields.io/badge/Celery-Async%20Ingestion-37814A?logo=celery)
![LangSmith](https://img.shields.io/badge/LangSmith-Observability-purple)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

---

## Why This Matters

Enterprise organizations accumulate knowledge across PDFs, internal documents, and web sources — fragmented, unsearchable, and siloed. Staff waste hours finding answers that already exist in documentation.

Generic chatbots hallucinate. Keyword search misses context. ContextHub AI solves this with:

- **Grounded responses** — answers are strictly derived from indexed documents, never invented
- **Source attribution** — every answer cites the exact document and page number
- **Confidence scoring** — retrieval quality is quantified per query, not opaque
- **Async ingestion at scale** — PDFs are processed in the background via a job queue; the API never blocks
- **Provider independence** — swap between local (Ollama) and cloud (OpenAI) LLMs with a single environment variable

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        Client Layer                          │
│   React Frontend (5173)  │  Streamlit Demo (8501)  │  CLI   │
└────────────────────────────────┬─────────────────────────────┘
                                 │
                   ┌─────────────▼─────────────┐
                   │     FastAPI Gateway        │
                   │       Port 8000            │
                   │  /ask  /upload  /metrics   │
                   └──────────┬────────────────┘
                              │
         ┌────────────────────┼──────────────────┐
         │                    │                  │
┌────────▼───────┐  ┌────────▼────────┐  ┌──────▼──────────┐
│ Celery Worker  │  │    LangGraph    │  │  Redis Cache    │
│ Async PDF      │  │  Orchestrator   │  │  Source dedup   │
│ Ingestion      │  │  retrieve →     │  │  Job broker     │
│ (auto-retry)   │  │  generate       │  └─────────────────┘
└────────┬───────┘  └────────┬────────┘
         │                   │
         │          ┌────────▼────────┐
         │          │   LLM Factory   │
         │          │  Ollama/OpenAI  │
         │          │  (env-switched) │
         │          └─────────────────┘
         │
┌────────▼────────────────────────────────────────────┐
│              PostgreSQL + pgvector                   │
│  • bge-m3 vector embeddings (1024 dims)             │
│  • Collection-scoped similarity search              │
│  • Document index + ingestion status                │
└─────────────────────────────────────────────────────┘
         │
┌────────▼─────────────┐
│  LangSmith           │
│  Trace every run:    │
│  retrieve + generate │
│  latency, tokens     │
└──────────────────────┘
```

---

## Product Preview

### Streamlit Demo UI

| Feature | Description |
|---|---|
| Document Upload | Upload PDF → async Celery job → live polling until indexed |
| Grounded Q&A | Question → retrieved context → LLM answer with sources |
| Confidence Score | Average pgvector similarity score of top-3 retrieved chunks |
| Latency Metric | End-to-end pipeline time (retrieve + generate) in seconds |
| System Sidebar | Live operational metrics from `/metrics` endpoint |

```bash
streamlit run demo_ui.py
# → http://localhost:8501
```

### React Frontend

Full-featured web interface at `http://localhost:5173`:
- **Knowledge Base** page — drag-and-drop PDF upload, real-time Celery task polling, indexed document list
- **Chat** page — streaming token-by-token responses via SSE, model selector, collection picker

### Swagger UI

Auto-generated interactive API documentation:
```
http://localhost:8000/docs
```

---

## AI Engineering Decisions

These are the deliberate architectural choices made to move beyond a proof-of-concept toward a production-oriented system.

### 1. Asynchronous ingestion via Celery + Redis

PDF processing involves disk I/O, embedding calls (up to 8192 tokens per chunk with bge-m3), and bulk vector writes — easily 20–60 seconds per document. A synchronous endpoint would block the API server thread for every upload.

Celery offloads this to a worker pool. The API returns a `task_id` immediately; clients poll `/tasks/{id}`. This is the same pattern used by cloud ML pipelines (e.g. SageMaker batch transforms, Azure ML endpoints). Workers auto-retry up to 3 times on failure.

### 2. pgvector over a standalone vector database

ChromaDB is excellent for local prototyping but introduces a separate process and persistence layer. pgvector runs inside PostgreSQL — the same database already storing collection metadata and document status. One fewer infra dependency, ACID-compliant writes, and SQL-queryable embeddings for future analytics.

### 3. Provider-agnostic LLM factory

```
LLM_PROVIDER=ollama   → ChatOllama (llama3.1:8b, fully local, zero API cost)
LLM_PROVIDER=openai   → ChatOpenAI (gpt-4o, cloud-grade reasoning)
```

No code change, no redeploy. This matters in production where you might route cost-sensitive queries to a local model and high-stakes queries to a frontier model. The factory pattern also makes adding new providers (Anthropic, Mistral, Vertex) a one-file change.

### 4. LangGraph over a simple LLMChain

LangGraph models the pipeline as a typed state machine with explicit node transitions (`retrieve → generate`). This gives three production advantages over a chain:

- **Debuggability** — LangSmith traces each node individually: inputs, outputs, latency, tokens
- **Testability** — each node is a plain Python function, unit-testable in isolation
- **Extensibility** — adding reranking, query expansion, or a guardrails node means adding a new graph node, not refactoring a chain

### 5. Similarity threshold over pure top-k retrieval

`similarity_search_with_score` returns a relevance score per chunk. We filter at ≥ 0.50 rather than always returning the top-k results. When no chunk exceeds the threshold, the system returns *"The information is unavailable in the current knowledge base."* rather than constructing an answer from loosely related content.

This is the core mechanism behind grounded responses — the system knows what it doesn't know.

---

## Key Capabilities

| Capability | Implementation |
|---|---|
| PDF & URL ingestion | `PDFDataLoader`, `WebDataLoader` + `RecursiveCharacterTextSplitter` |
| Multilingual embeddings | `bge-m3` via Ollama (1024 dims, 8192 token context) |
| Semantic search | pgvector cosine similarity, collection-scoped |
| Async job queue | Celery + Redis, auto-retry (max 3×) |
| Streaming answers | Server-Sent Events via `llm.astream()` |
| Observability | LangSmith traces (retrieve + generate nodes, latency, tokens) |
| Confidence scoring | Mean pgvector score of top-3 retrieved chunks |
| Chunk quality filtering | Reject chunks < 40% alphabetic or < 20 characters |
| Source attribution | Document path + page number returned with every answer |
| LLM switching | `LLM_PROVIDER` env var — no code change |

---

## Getting Started

### Prerequisites

- Python 3.11+
- Docker (Redis + PostgreSQL via `docker-compose`)
- [Ollama](https://ollama.com/) — if using local LLMs
- Node.js 18+ — for the React frontend

### Installation

```bash
# 1. Clone
git clone <your_repo_url>
cd contexthub-ai

# 2. Python environment
python -m venv venv
source venv/bin/activate      # Linux/macOS
venv\Scripts\activate         # Windows

# 3. Dependencies
pip install -r requirements.txt
cd frontend && npm install && cd ..

# 4. Ollama models (if using local LLMs)
ollama pull llama3.1:8b
ollama pull bge-m3

# 5. Environment
cp .env.example .env
# Edit .env with your values
```

### Start (recommended)

**Windows:**
```bat
start.bat
```

**Linux / macOS:**
```bash
chmod +x start.sh && ./start.sh
```

The script starts Docker services, runs Alembic migrations, launches the Celery worker, FastAPI backend, and React dev server.

### Manual start

```bash
docker-compose up -d                              # Redis + PostgreSQL
alembic upgrade head                              # DB migrations (first run)
celery -A src.workers.celery_app worker --loglevel=info  # Async worker
uvicorn src.app:app --reload                      # API (port 8000)
cd frontend && npm run dev                        # UI (port 5173)
streamlit run demo_ui.py                          # Demo UI (port 8501)
```

---

## API Reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/collections` | List all collections |
| `POST` | `/collections` | Create a collection `{ name, description? }` |
| `POST` | `/upload?collection=<name>` | Upload PDF (async) → `{ task_id }` |
| `GET` | `/tasks/{id}` | Poll Celery task status |
| `POST` | `/load` | Synchronous load `{ source, collection }` |
| `GET` | `/documents?collection=<name>` | List indexed documents with status |
| `POST` | `/ask` | Q&A → `{ answer, sources, confidence_metric, latency_seconds }` |
| `POST` | `/ask/stream` | Streaming Q&A via Server-Sent Events |
| `GET` | `/models` | Available LLM models (embedding models excluded) |
| `GET` | `/metrics` | Operational system snapshot |

**Example — grounded Q&A:**
```http
POST /ask
{ "question": "What are the eligibility criteria?", "collection": "HR-Policy-2024" }

→ {
    "answer": "Employees are eligible after 6 months of continuous service...",
    "sources": [
      { "source": "uploads/hr-policy.pdf", "page": 12 }
    ],
    "confidence_metric": 0.847,
    "latency_seconds": 2.31
  }
```

---

## Configuration

Copy `.env.example` to `.env` and fill in your values:

| Variable | Description | Default |
|---|---|---|
| `LLM_PROVIDER` | `ollama` or `openai` | `ollama` |
| `LLM_MODEL_NAME` | Model name for the chosen provider | *(required)* |
| `OPENAI_API_KEY` | Required only when `LLM_PROVIDER=openai` | — |
| `OLLAMA_BASE_URL` | Ollama server URL | `http://localhost:11434` |
| `POSTGRES_HOST` | PostgreSQL hostname | `localhost` |
| `POSTGRES_PORT` | PostgreSQL port | `5432` |
| `POSTGRES_USER` | PostgreSQL user | `postgres` |
| `POSTGRES_PASSWORD` | PostgreSQL password | `postgres` |
| `POSTGRES_DB` | Database name | `contexthub` |
| `REDIS_HOST` | Redis hostname | `localhost` |
| `REDIS_PORT` | Redis port | `6379` |
| `LANGSMITH_API_KEY` | LangSmith API key for tracing | *(optional)* |
| `LANGCHAIN_TRACING_V2` | Enable LangSmith tracing | `false` |
| `LANGCHAIN_PROJECT` | LangSmith project name | `contexthub-ai` |

---

## Project Structure

```
contexthub-ai/
├── src/
│   ├── rag/               # LangGraph pipeline: retrieve → generate
│   ├── llm/               # LLMFactory: Ollama / OpenAI abstraction
│   ├── vector_store/      # pgvector (default), ChromaDB (legacy), in-memory
│   ├── data_loading/      # PDF + web loaders, text splitter + quality filter
│   ├── cache/             # Redis-backed source deduplication
│   ├── db/                # SQLAlchemy models, session, migrations
│   ├── workers/           # Celery app + ingest_document task (auto-retry)
│   ├── app.py             # FastAPI application — all endpoints
│   └── main.py            # Interactive CLI
│
├── frontend/              # React + Vite + TypeScript + TailwindCSS
│   └── src/
│       ├── components/    # Chat, KnowledgeBase, Sidebar
│       ├── pages/         # Chat.tsx, KnowledgeBase.tsx
│       └── lib/           # api.ts (all backend calls proxied via /api)
│
├── alembic/               # Database migrations
├── demo_ui.py             # Streamlit demo (upload + Q&A + metrics sidebar)
├── docker-compose.yaml    # Redis + PostgreSQL (pgvector)
├── start.bat / start.sh   # One-command startup
├── stop.bat / stop.sh     # One-command shutdown
├── .env.example           # Environment template
└── requirements.txt
```

---

## Production Roadmap

This project is intentionally designed to be deployable beyond localhost. The path to production involves:

- **Containerization** — Dockerfile per service (API, worker, frontend); Docker Compose v2 for orchestration
- **Horizontal scaling** — Celery workers scale independently behind the job queue; pgvector handles concurrent reads
- **Reranking** — Cross-encoder reranking layer between `retrieve` and `generate` to improve precision on long documents
- **Query expansion** — HyDE (Hypothetical Document Embeddings) or multi-query retrieval for sparse or ambiguous questions
- **Authentication** — OAuth2 / API key middleware on FastAPI routes
- **Monitoring** — Prometheus metrics endpoint + Grafana dashboard (latency, cache hit rate, embedding throughput)
- **Fine-tuned embeddings** — Domain-specific embedding model fine-tuned on enterprise document corpus
- **Multi-tenancy** — Collection-level RBAC; tenant isolation at the pgvector collection layer

---

## License

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

MIT License — contributions welcome.
