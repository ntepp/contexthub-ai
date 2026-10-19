# ContextHub AI

**The sovereign document assistant for your company.** ContextHub answers your employees'
questions from your own documents — procedures, internal policies, contracts, technical
documentation, regulations — and cites every statement down to the page and section. When your
documents do not contain the answer, it says so instead of making one up.

It runs on your infrastructure. In local mode, no content ever leaves it.

![MIT licence](https://img.shields.io/badge/licence-MIT-1E5B48)
![Python 3.12](https://img.shields.io/badge/python-3.12-16213A)
![PostgreSQL + pgvector](https://img.shields.io/badge/PostgreSQL-pgvector-16213A)

---

## Who it is for

Any team that spends too long looking for the right answer in a large body of documentation.

| Team | Documents | Example question |
|---|---|---|
| **Human resources** | Collective agreements, company agreements, internal rules, HR guides | "How many days off do I get when I move house?" |
| **Support and IT** | Procedures, knowledge base, product documentation, security policy | "How do I reset a contractor's VPN access?" |
| **Legal and procurement** | Contracts, terms and conditions, templates, policies | "What is the termination notice for the maintenance contract?" |
| **Quality, manufacturing, healthcare** | Standards, operating procedures, data sheets, product files | "How often must the scales be calibrated?" |
| **Sales and pre-sales** | Offers, product sheets, tender responses | "Is our solution hosted in France?" |
| **Compliance, risk, finance** | Regulations, guidance, control procedures | "When does MiFID II apply?" |

One instance serves several teams: each **collection** of documents has its own access rights,
and an employee only queries what they are allowed to read.

## What ContextHub does

| | |
|---|---|
| **Cited answers** | Every sentence points to a numbered source `[n]`: document, page, section, excerpt. One click opens the document at the right page. |
| **Explicit refusal** | When no relevant passage exists, ContextHub says it does not know — without even calling the model. |
| **Conversations** | Follow-up questions are understood ("and for managers?"); per-user history. |
| **All your formats** | PDF (including scans, through OCR), Word, Markdown, plain text, HTML; chunking follows the document's headings and sections. |
| **Hybrid search** | Meaning (multilingual bge-m3 embeddings) and exact words (accent-insensitive French full-text) combined: finds "parental leave" as well as a part number. |
| **Access control** | OpenID Connect SSO (Entra ID, Google, Okta, Keycloak) or local accounts, per-collection rights, API keys for integrations. |
| **Audit trail** | Questions, answers, sources consulted, imports, permission changes: everything is logged, SHA-256 hash-chained, verifiable and exportable to CSV. |
| **GDPR** | Deletion of documents, collections and users; retention periods; erasure of personal data from the audit trail without breaking its integrity. |
| **Any LLM provider** | Local Ollama by default; Anthropic, OpenAI, Mistral or any OpenAI-compatible endpoint if you allow it. The UI always shows when a cloud model is in use. |
| **Fits your business** | Configurable business context (`ASSISTANT_CONTEXT`); EU legal acts (GDPR, AI Act, NIS 2, CSRD, MiFID II…) recognised by their common names. |

The user interface is in French; answers come in the language of the question. Answer quality
can be measured on your own corpus with the bundled evaluation harness
([docs/evaluation.md](docs/evaluation.md)).

## Architecture

```
 Browser ──▶ nginx ──▶ FastAPI ──▶ PostgreSQL + pgvector  (accounts, rights, audit, indexed passages)
              │          │     └──▶ Redis                   (task queue, rate limits)
              │          │     └──▶ LangGraph: condense → hybrid retrieval → cited generation
              │          └──▶ Celery worker: PDF/DOCX/HTML/TXT → OCR → section-aware chunking → embeddings
              └ only exposed service         └──▶ Ollama (local)  or  an allowed cloud provider
```

| Module | Responsibility |
|---|---|
| `src/api/` | Routes, authentication, per-collection access control |
| `src/auth/` | Passwords, sessions, OIDC, API keys, rate limiting |
| `src/audit/` | Hash-chained audit trail, verification, export, GDPR erasure |
| `src/ingestion/` | File types, OCR, structure-aware chunking, EU act recognition, SSRF-safe URL import |
| `src/retrieval/` | pgvector storage, RRF hybrid search, optional re-ranking |
| `src/rag/` | LangGraph `condense → retrieve → generate`, citations, refusal |
| `src/llm/` | Providers and model allow-list |
| `eval/` | Evaluation harness |
| `frontend/` | React UI (French) |

## Getting started

### Production

```bash
cp .env.production.example .env        # replace every "change-me"
docker compose -f docker-compose.yaml -f docker-compose.gpu.yaml --profile ollama up -d --build
docker compose exec ollama ollama pull bge-m3 && docker compose exec ollama ollama pull llama3.1:8b
```

Full guide: [docs/deployment.md](docs/deployment.md) · security and data flows:
[docs/security.md](docs/security.md) (both in French).

### Development

```bash
python -m venv venv && venv\Scripts\activate          # source venv/bin/activate on Linux/macOS
pip install -r requirements-dev.txt
cd frontend && npm ci && cd ..
cp .env.example .env
ollama pull bge-m3 && ollama pull llama3.1:8b
start.bat                                              # or ./start.sh: PostgreSQL, Redis, migrations, API, worker, UI
```

UI: http://localhost:5173 (account from `ADMIN_EMAIL` / `ADMIN_PASSWORD`) ·
API: http://localhost:8000/docs.

### Tests

```bash
ruff check src eval tests
pytest                                                 # SQLite, no external service
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/contexthub_test pytest   # + real PostgreSQL
cd frontend && npm run build
```

## API

Every business route requires a session (cookie) or an API key
(`Authorization: Bearer chk_…`).

| Method | Route | |
|---|---|---|
| `POST` | `/auth/login`, `/auth/logout` | Password login |
| `GET` | `/auth/oidc/login` | SSO login |
| `GET` | `/collections` | Accessible collections |
| `POST` | `/collections/{id}/documents` | Import a document (asynchronous) |
| `DELETE` | `/documents/{id}` | Delete a document |
| `GET` | `/documents/{id}/file` | Original file |
| `POST` | `/chat/ask` | Question → answer, sources, conversation |
| `POST` | `/chat/stream` | Same, streamed (Server-Sent Events) |
| `GET` | `/conversations` | History |
| `GET` | `/admin/audit`, `/admin/audit/export.csv`, `/admin/audit/verify` | Audit trail |
| `GET` | `/health`, `/ready`, `/metrics` | Monitoring |

Example:

```bash
curl -X POST https://contexthub.example.com/api/chat/ask \
  -H "Authorization: Bearer chk_..." -H "Content-Type: application/json" \
  -d '{"question": "How much notice do I need to give for a remote-work day?"}'
```

```json
{
  "answer": "A remote-work day must be booked at least 48 hours in advance in the HR tool [1].",
  "sources": [{"index": 1, "filename": "remote-work-agreement-2025.pdf", "page": 4,
               "section_path": ["Title II — Organisation", "Article 6 — Planning"],
               "cited": true}],
  "refused": false,
  "conversation_id": "…"
}
```

## Documentation

- [PRD — product, target, scope, status](docs/PRD.md)
- [Installation and operations](docs/deployment.md)
- [Security and data flows](docs/security.md)
- [Quality evaluation](docs/evaluation.md)
- [Incidents and lessons learned](docs/incidents.md)

## Licence and services

ContextHub is released under the [MIT licence](LICENSE). Installation, onboarding, support with
guaranteed response times and custom development are offered separately:
ntepp.marcus@gmail.com.
