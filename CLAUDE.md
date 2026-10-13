# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Product

ContextHub AI v1: on-prem, single-tenant RAG assistant sold to compliance / finance teams in France.
Open-core (AGPL-3.0 + commercial licence). Product requirements and implementation status live in
`docs/PRD.md` — update its "Suivi d'implémentation" section when a feature lands.

## Commands

```bash
start.bat | ./start.sh                     # dev: PostgreSQL+Redis (docker-compose.dev.yaml), migrations, API, worker, Vite
uvicorn src.app:app --reload               # API (http://localhost:8000/docs)
celery -A src.workers.celery_app worker --loglevel=info
cd frontend && npm run dev                 # UI (http://localhost:5173, /api proxied to :8000)
alembic upgrade head
python -m src.cli create-admin|verify-audit|purge|reindex
python -m eval.run_eval --collection mifid [--ingest DIR]   # quality benchmark (real LLM + corpus)

ruff check src eval tests
pytest                                     # SQLite + fakes, no external service
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/contexthub_test pytest   # + real pgvector tests
cd frontend && npm run build               # tsc strict + vite

docker compose up -d --build               # production stack (docker-compose.yaml, .env from .env.production.example)
```

## Architecture

| Module | Responsibility |
|---|---|
| `src/config.py` | `get_settings()` — every env var, defaults, production safety guards. Never call `os.getenv` elsewhere. |
| `src/app.py` | `create_app()`: middleware (request id, metrics, security headers), generic error handlers, routers |
| `src/api/deps.py` | Auth (session cookie or `Bearer chk_` API key), CSRF header check, `load_collection(db, user, id, minimum)` access control |
| `src/api/routes/` | `auth`, `collections` (+ documents), `chat` (+ conversations, models), `admin`, `health` |
| `src/auth/` | bcrypt passwords, JWT session, OIDC (PKCE + JWKS), rate limiter (Redis, in-memory fallback) |
| `src/audit/service.py` | Hash-chained audit trail: `record()`, `verify_chain()`, CSV export, GDPR `redact_actor()`, retention |
| `src/db/models.py` | ORM (portable types so tests run on SQLite). `hub_chunks` is NOT an ORM model. |
| `alembic/versions/` | `69ab4253` = v1 schema (users, grants, audit, conversations, `hub_chunks` with pgvector HNSW + generated `tsvector` on `fr_unaccent`) |
| `src/ingestion/` | `filetypes` (sniffing), `loaders` (PDF+OCR, DOCX, TXT/MD, HTML), `eu_acts` (MiFID II/EMIR… recognition), `chunking` (Titre/Chapitre/Article), `storage`, `url_fetch` (SSRF guard), `pipeline` (idempotent ingestion) |
| `src/retrieval/` | `store.py` (`PgChunkStore` raw SQL hybrid search + `InMemoryChunkStore` for tests), `embeddings.py`, `rerank.py` (optional) |
| `src/rag/rag_pipeline.py` | LangGraph `condense → retrieve → generate`; `answer_question()`, `stream_answer()`, `create_and_run_graph()` (by collection name) |
| `src/llm/factory.py` | `provider:model` ids, `LLM_MODELS` allow-list, Ollama / Anthropic / OpenAI / OpenAI-compatible |
| `src/workers/` | Celery app + `ingest_document(document_id)` (backoff on transient errors only) + nightly `purge_expired` |
| `eval/` | Golden dataset, metrics, reporter, runner |
| `frontend/src/` | React + Tailwind, French UI. `lib/api.ts` (all calls, CSRF header), `lib/auth.tsx`, pages: Chat, KnowledgeBase, Account, admin/* |

## Invariants — do not break

- **Scores are cosine distances** (0 = identical, 2 = opposite), not similarities. `retrieve` keeps
  `distance <= RETRIEVAL_MAX_DISTANCE` and short-circuits to `NO_ANSWER_TEXT` without calling the LLM
  when nothing qualifies (the bug fixed in 13a1760 inverted this and refused everything).
- Hybrid search widens the candidate pool; the distance threshold still gates grounding.
- `NO_ANSWER_TEXT` is emitted verbatim by `retrieve` and normalised from the LLM (`is_refusal`); a
  refusal drops its sources. The eval harness matches on the exact string.
- Streaming forwards only tokens from the `generate` node (`condense` also calls the LLM). When retrieve
  short-circuits, the refusal is emitted from the retrieve end event, else the UI shows an empty bubble.
- Ingestion is idempotent: chunks of a document are deleted before (re)indexing.
- Every collection access goes through `load_collection()` / `accessible_collections()`; inaccessible
  collections answer 404. Retrieval filters by collection ids in SQL.
- Audit hash covers every column except `actor_email`, `ip`, `user_agent` (redactable for GDPR).
- LangSmith tracing is forced off unless `LANGCHAIN_TRACING_V2=true` (it ships document excerpts out).
- Cookie-authenticated non-GET requests need the `X-Requested-With` header (frontend sets it).

## Tests

`tests/conftest.py` sets env vars before importing `src`, uses SQLite in-memory, `InMemoryChunkStore`,
`HashEmbeddings` and `ScriptedChatModel` (`tests/fakes.py`), Celery eager mode. `tests/test_pg.py`
(marker `pg`) migrates a real database from scratch.
