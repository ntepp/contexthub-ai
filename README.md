# local_contexthub_rag

`local_contexthub_rag` is a Retrieval Augmented Generation (RAG) application that lets you upload PDFs or web pages and ask questions about their content. It uses local or cloud LLMs (via a factory pattern), `bge-m3` for multilingual embeddings, **PostgreSQL + pgvector** for vector storage, Redis for caching, Celery for async document ingestion, and a **React frontend** for a modern web interface.

## Features

* **PDF & Web Page Loading:** Upload PDFs via the API or load any URL; content is chunked, embedded, and stored automatically.
* **Async Ingestion:** PDF uploads are processed asynchronously via Celery workers — the API returns immediately with a `task_id` to poll.
* **Collections:** Organize documents into named collections (e.g. "Dossier RH"). Each upload and query is scoped to a collection.
* **Local & Cloud LLMs:** Switch between Ollama (local) and OpenAI via a single env variable — no code change needed.
* **Model Discovery:** `GET /models` returns all available Ollama models + OpenAI models dynamically.
* **pgvector Storage:** Vectors stored in PostgreSQL via `langchain-postgres` — persistent, queryable, production-ready.
* **Redis Caching:** Tracks processed sources so re-uploading the same file is a no-op.
* **Sourced Answers:** Every answer from `/ask` includes the source documents and page numbers used to generate it.
* **Chunk Quality Filter:** Noise chunks (e.g. 2D-DOC QR barcodes) are automatically discarded — only meaningful text with ≥ 40% alphabetic characters is stored.
* **React Frontend:** Dark-themed UI with retractable sidebar, Knowledge Base upload page, and Chat page with model selector.
* **Interactive CLI:** Ask multiple questions on the same loaded context without restarting.

## Architecture

### Data Flow

```
PDF Upload (POST /upload?collection=<name>)
  → Save to ./uploads/
  → Celery task dispatched → task_id returned immediately
  → Worker: PDFDataLoader → split_text() → bge-m3 → pgvector (PostgreSQL)
  → Redis key set (collection:source cached)

URL Load (POST /load)
  → Synchronous: WebDataLoader → split_text() → bge-m3 → pgvector

Question (POST /ask)
  → LangGraph: retrieve (pgvector similarity search, threshold ≥ 0.50, scoped to collection)
             → generate (LLM via factory: Ollama or OpenAI)
  → Returns: { answer, sources: [{source, page}] }
```

### LangChain & LangGraph Integration

* **LangChain:** Document loading, text splitting, embedding generation, pgvector interaction.
* **LangGraph:** Orchestrates the two-step `retrieve → generate` pipeline with typed state (includes `collection` field).
* **LangSmith:** Optional observability — tracks pipeline execution, helps identify bottlenecks.

## Prerequisites

* Python 3.9+
* [Ollama](https://ollama.com/) installed and running locally (if using local LLMs)
* Docker (for Redis + PostgreSQL)
* Node.js 18+ (for the frontend)

## Installation

1. **Clone the repository:**

    ```bash
    git clone <your_repo_url>
    cd local_contexthub_rag
    ```

2. **Create a virtual environment:**

    ```bash
    python -m venv venv
    source venv/bin/activate   # Linux/macOS
    venv\Scripts\activate      # Windows
    ```

3. **Install Python dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

4. **Install frontend dependencies:**

    ```bash
    cd frontend && npm install && cd ..
    ```

5. **Pull Ollama models:**

    ```bash
    ollama pull llama3.1:8b    # or your chosen LLM
    ollama pull bge-m3         # embedding model
    ```

6. **Configure environment:**

    Edit `.env` with your values:

    ```env
    LLM_PROVIDER=ollama          # or: openai
    LLM_MODEL_NAME=llama3.1:8b   # e.g. deepseek-r1, gpt-4o
    OPENAI_API_KEY=              # required only when LLM_PROVIDER=openai
    POSTGRES_HOST=localhost
    POSTGRES_PORT=5432
    POSTGRES_USER=postgres
    POSTGRES_PASSWORD=postgres
    POSTGRES_DB=contexthub
    ```

7. **Start infrastructure (Redis + PostgreSQL):**

    ```bash
    docker-compose up
    ```

8. **Run database migrations:**

    ```bash
    alembic upgrade head
    ```

## Usage

### Frontend (recommended)

Start all services, then launch the dev server:

```bash
cd frontend && npm run dev
```

Open [http://localhost:5173](http://localhost:5173) — the UI connects to the backend via `/api` proxy.

### API mode

Start the FastAPI server:

```bash
uvicorn src.app:app --reload
```

Open Swagger UI at [http://localhost:8000/docs](http://localhost:8000/docs).

### Start the Celery worker

Required for `POST /upload` to process files:

```bash
celery -A src.workers.celery_app worker --loglevel=info
```

> **Windows note:** The worker automatically uses the `solo` pool to avoid `PermissionError (WinError 5)` caused by Windows' lack of `fork()` support. For production concurrency on Windows, install `gevent` and pass `--pool=gevent`.

### CLI mode

```bash
python -m src.main
```

Enter a URL or local PDF path, then ask questions interactively. Answers include source references.

## API Reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/collections` | List all collections |
| `POST` | `/collections` | Create a collection `{ name, description? }` |
| `POST` | `/upload?collection=<name>` | Upload PDF (async) → `{ task_id, status }` |
| `GET` | `/tasks/{id}` | Poll Celery task status |
| `POST` | `/load` | Synchronous load `{ source, collection }` |
| `POST` | `/ask` | Q&A `{ question, collection }` → `{ answer, sources }` |
| `GET` | `/models` | List available Ollama + OpenAI models |

**Ask a question:**
```http
POST /ask
{ "question": "What is the main topic?", "collection": "Dossier RH" }

→ {
    "answer": "The main topic is... thanks for asking!",
    "sources": [
      { "source": "uploads/doc.pdf", "page": 3 },
      { "source": "uploads/doc.pdf", "page": 4 }
    ]
  }
```

## Configuration

All configuration is done via `.env`:

| Variable | Description | Default |
|---|---|---|
| `LLM_PROVIDER` | `ollama` or `openai` | `ollama` |
| `LLM_MODEL_NAME` | Model name passed to the provider | *(required)* |
| `OPENAI_API_KEY` | OpenAI API key | *(only for openai)* |
| `REDIS_HOST` | Redis hostname | `localhost` |
| `REDIS_PORT` | Redis port | `6379` |
| `POSTGRES_HOST` | PostgreSQL hostname | `localhost` |
| `POSTGRES_PORT` | PostgreSQL port | `5432` |
| `POSTGRES_USER` | PostgreSQL user | `postgres` |
| `POSTGRES_PASSWORD` | PostgreSQL password | `postgres` |
| `POSTGRES_DB` | Database name | `contexthub` |
| `OLLAMA_BASE_URL` | Ollama server URL | `http://localhost:11434` |

## Project Structure

```
local_contexthub_rag/
├── frontend/                   # React + Vite + TypeScript + TailwindCSS
│   └── src/
│       ├── components/
│       │   ├── chat/           # ModelSelector, MessageBubble (markdown)
│       │   ├── knowledge-base/ # UploadZone, TaskStatus (Celery polling)
│       │   └── layout/         # Sidebar (retractable), AppLayout
│       ├── pages/              # Chat.tsx, KnowledgeBase.tsx
│       └── lib/                # api.ts (all backend calls), utils.ts
│
├── src/
│   ├── cache/                  # Redis-backed source caching
│   ├── data_loading/           # PDF + web loaders, text splitter
│   ├── db/                     # SQLAlchemy models (Collection), session
│   ├── llm/                    # LLMFactory: Ollama / OpenAI
│   ├── rag/                    # LangGraph pipeline (retrieve → generate)
│   ├── utils/                  # source_type classifier
│   ├── vector_store/           # pgvector (default), ChromaDB (legacy), in-memory
│   ├── workers/                # Celery app + ingest_document task
│   ├── app.py                  # FastAPI application
│   └── main.py                 # Interactive CLI
│
├── alembic/                    # Database migrations
├── uploads/                    # Uploaded PDFs staging area (auto-created)
├── .env                        # Environment variables
├── docker-compose.yaml         # Redis + PostgreSQL (pgvector)
├── requirements.txt
└── README.md
```

## PDF Ingestion Quality

### Current approach — Chunk filtering (`src/data_loading/text_splitter.py`)
After splitting, every chunk is evaluated before indexing:
- Chunks with **< 40% alphabetic characters** are rejected (catches 2D-DOC barcodes, random encoded strings)
- Chunks **shorter than 20 characters** are rejected (catches lone headers/page numbers)
- `chunk_overlap=50` is applied to avoid context loss at chunk boundaries

### Planned improvements
- **Better loader:** swap `PyPDFLoader` → `PyMuPDFLoader` (fixes `â€` encoding issues on French PDFs)
- **Pre-processing:** `ftfy` text normalization pass before splitting
- **Semantic chunking:** `SemanticChunker` to split by meaning instead of character count

## License

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

This project is licensed under the MIT License — see the [MIT License](https://opensource.org/licenses/MIT) page for details.

## Contributing

Contributions are welcome! Feel free to fork the repository, submit issues, and create pull requests.

By contributing, you agree that your contributions will be licensed under the same MIT License.
