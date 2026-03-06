# local_contexthub_rag

`local_contexthub_rag` is a Retrieval Augmented Generation (RAG) application that lets you upload PDFs or web pages and ask questions about their content. It uses local or cloud LLMs (via a factory pattern), `nomic-embed-text` for embeddings, ChromaDB for vector storage, Redis for caching, and Celery for async document ingestion.

## Features

* **PDF & Web Page Loading:** Upload PDFs via the API or load any URL; content is chunked, embedded, and stored automatically.
* **Async Ingestion:** PDF uploads are processed asynchronously via Celery workers — the API returns immediately with a `task_id` to poll.
* **Local & Cloud LLMs:** Switch between Ollama (local) and OpenAI via a single env variable — no code change needed.
* **ChromaDB Vector Storage:** Embedded, file-based vector store — no separate service required. Data persists in `./chroma_db/`.
* **Redis Caching:** Tracks processed sources so re-uploading the same file is a no-op.
* **Sourced Answers:** Every answer from `/ask` includes the source documents and page numbers used to generate it.
* **Chunk Quality Filter:** Noise chunks (e.g. 2D-DOC QR barcodes extracted as base32 strings) are automatically discarded before indexing — only meaningful text with ≥ 40% alphabetic characters is stored.
* **Interactive CLI:** Ask multiple questions on the same loaded context without restarting.

## Architecture

### Data Flow

```
PDF Upload (POST /upload)
  → Save to ./uploads/
  → Celery task dispatched → task_id returned immediately
  → Worker: PDFDataLoader → split_text() → nomic-embed-text → ChromaDB
  → Redis key set (source cached)

URL Load (POST /load)
  → Synchronous: WebDataLoader → split_text() → nomic-embed-text → ChromaDB

Question (POST /ask)
  → LangGraph: retrieve (ChromaDB similarity search, threshold ≥ 0.50)
             → generate (LLM via factory: Ollama or OpenAI)
  → Returns: { answer, sources: [{source, page}] }
```

### LangChain & LangGraph Integration

* **LangChain:** Document loading, text splitting, embedding generation, vector store interaction.
* **LangGraph:** Orchestrates the two-step `retrieve → generate` pipeline with typed state.
* **LangSmith:** Optional observability — tracks pipeline execution, helps identify bottlenecks.

## Prerequisites

* Python 3.9+
* [Ollama](https://ollama.com/) installed and running locally (if using local LLMs)
* Docker (for Redis only)

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

3. **Install dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

4. **Pull Ollama models:**

    ```bash
    ollama pull deepseek-r1       # or your chosen model (e.g. llama3.1:8b)
    ollama pull nomic-embed-text
    ```

5. **Configure environment:**

    Copy `.env` and set your values:

    ```env
    LLM_PROVIDER=ollama          # or: openai
    LLM_MODEL_NAME=deepseek-r1   # e.g. llama3.1:8b, gpt-4o
    OPENAI_API_KEY=              # required only when LLM_PROVIDER=openai
    ```

6. **Start Redis (only service needed via Docker):**

    ```bash
    docker-compose up
    ```

## Usage

### API mode (recommended)

Start the FastAPI server:

```bash
uvicorn src.app:app
```

Open Swagger UI at [http://localhost:8000/docs](http://localhost:8000/docs).

**Upload a PDF (async):**
```http
POST /upload
Content-Type: multipart/form-data
file: <your_file.pdf>

→ { "task_id": "abc123", "status": "pending" }
```

**Poll ingestion status:**
```http
GET /tasks/abc123

→ { "task_id": "abc123", "status": "SUCCESS", "result": { "status": "success", "file": "doc.pdf" } }
```

**Load a URL (synchronous):**
```http
POST /load
{ "source": "https://example.com/article" }
```

**Ask a question:**
```http
POST /ask
{ "question": "What is the main topic?" }

→ {
    "answer": "The main topic is... thanks for asking!",
    "sources": [
      { "source": "uploads/doc.pdf", "page": 3 },
      { "source": "uploads/doc.pdf", "page": 4 }
    ]
  }
```

### Start the Celery worker

The worker must be running for `POST /upload` to process files:

```bash
celery -A src.workers.celery_app worker --loglevel=info
```

### CLI mode

```bash
python -m src.main
```

Enter a URL or local PDF path, then ask questions interactively. Answers include source references.

## Configuration

All configuration is done via `.env`:

| Variable | Description | Default |
|---|---|---|
| `LLM_PROVIDER` | `ollama` or `openai` | `ollama` |
| `LLM_MODEL_NAME` | Model name passed to the provider | *(required)* |
| `OPENAI_API_KEY` | OpenAI API key | *(only for openai)* |
| `REDIS_HOST` | Redis hostname | `localhost` |
| `REDIS_PORT` | Redis port | `6379` |

## Project Structure

```
local_contexthub_rag/
├── src/
│   ├── cache/                  # Caching module
│   │   ├── base.py             # Abstract cache base classes
│   │   ├── factory.py          # Cache factory (Redis)
│   │   └── redis_cache.py      # Redis implementation
│   │
│   ├── data_loading/           # Data loading strategies
│   │   ├── abstract_loader.py  # Abstract base loader
│   │   ├── pdf_loader.py       # PDF loader (PyPDFLoader)
│   │   ├── text_splitter.py    # RecursiveCharacterTextSplitter
│   │   ├── webpage_loader.py   # Web page loader
│   │   └── website_loader.py   # Website crawler loader
│   │
│   ├── llm/                    # LLM abstraction layer
│   │   ├── factory.py          # LLMFactory: Ollama / OpenAI via LLM_PROVIDER
│   │   └── llm_chain.py        # initialize_model_llm() convenience wrapper
│   │
│   ├── rag/                    # RAG pipeline
│   │   └── rag_pipeline.py     # load_source() + LangGraph retrieve→generate
│   │
│   ├── utils/
│   │   └── source_type.py      # Classifies input as url / pdf / unknown
│   │
│   ├── vector_store/           # Vector storage module
│   │   ├── base.py             # Abstract base + VectorStoreConfig
│   │   ├── chroma.py           # ChromaDB implementation (embedded)
│   │   ├── embeddings.py       # nomic-embed-text via Ollama
│   │   ├── factory.py          # VectorStoreFactory (chroma / in_memory)
│   │   └── in_memory.py        # In-memory store (no persistence)
│   │
│   ├── workers/                # Async task processing
│   │   ├── celery_app.py       # Celery app wired to Redis broker + backend
│   │   └── tasks.py            # ingest_document task
│   │
│   ├── app.py                  # FastAPI: /upload, /tasks/{id}, /load, /ask
│   └── main.py                 # Interactive CLI
│
├── chroma_db/                  # ChromaDB persistent storage (auto-created)
├── uploads/                    # Uploaded PDFs staging area (auto-created)
├── .env                        # Environment variables
├── docker-compose.yaml         # Redis only
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
