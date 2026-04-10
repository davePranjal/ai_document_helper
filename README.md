# AI Document Vault

Upload documents, get AI-generated insights, and chat with them in natural language. Answers are grounded in the source material with page-level citations using a Retrieval-Augmented Generation (RAG) pipeline.

A small web UI is bundled at **`/ui/`** so you can drive the whole system from a browser without curl.

---

## Features

- **Async ingestion pipeline** — upload → text extraction → chunking → embedding → AI analysis, all in the background via Celery.
- **AI insights** — single Claude call produces summary, key topics, entities, category, tags, sentiment, and a self-reported confidence score. Customizable by length, tone, and focus area.
- **Document chat (RAG)** — top-K vector retrieval with neighbor expansion, multi-turn conversation history, and structured citations (page number + snippet).
- **Comparative analysis** — compare 2+ documents to surface similarities, differences, and unique insights.
- **SSE streaming** — chat responses stream token-by-token for a chat-app feel.
- **Multi-document chat** — single session can span multiple documents.
- **Document versioning** — re-uploading a filename auto-increments its version.
- **Redis caching** — query embeddings (1h), insights (24h), metrics (60s).
- **Cost tracking** — every Claude call records token usage and estimated USD cost.
- **Structured logging** — JSON logs with request-ID propagation, written to both stdout and a rotating file.
- **Built-in UI** — single-page browser client at `/ui/` for upload, chat, insights, compare, and metrics.
- **Cloud-ready** — single Dockerfile, pluggable storage backend (local fs or Google Cloud Storage).

---

## Architecture

```
                        ┌─────────────┐
                        │   Browser   │
                        │  (UI / API) │
                        └──────┬──────┘
                               │
                        ┌──────┴──────┐
                        │   FastAPI   │  ◀── rate limiting, request IDs,
                        │   Server    │      structured logging, CORS
                        └──────┬──────┘
                ┌──────────────┼──────────────┐
                ▼              ▼              ▼
         ┌───────────┐  ┌───────────┐  ┌───────────┐
         │ Documents │  │   Chat    │  │  Insights │
         │   API     │  │   API     │  │  /Metrics │
         └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
               │              │              │
               │ enqueue      │ sync RAG     │ read
               ▼              ▼              ▼
         ┌───────────┐  ┌───────────┐  ┌───────────┐
         │  Celery   │  │   RAG     │  │  Postgres │
         │  worker   │  │ pipeline  │  │ +pgvector │
         └─────┬─────┘  └─────┬─────┘  └─────▲─────┘
               │              │              │
   extract → chunk → embed    │              │
   → store chunks → analyze ──┼──────────────┘
               │              │
               └──────────────┴──── Anthropic Claude
                              │
                        local sentence-transformers
                        (all-MiniLM-L6-v2, 384-dim)
```

**Storage**: local filesystem in dev, Google Cloud Storage in production (controlled by `STORAGE_BACKEND`).
**Cache / broker**: Redis.
**Embeddings**: 100% local — no second API key required.

---

## Tech stack

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| Web | FastAPI, uvicorn, slowapi (rate limiting) |
| Database | PostgreSQL 16 + pgvector (HNSW index) |
| ORM / migrations | SQLAlchemy 2.0 (async), Alembic |
| Task queue | Celery + Redis |
| LLM | Claude (Anthropic SDK, direct calls — not LangChain) |
| Embeddings | sentence-transformers `all-MiniLM-L6-v2` (local, 384-dim) |
| Text splitting | LangChain `RecursiveCharacterTextSplitter` |
| Document parsing | pdfplumber (PDF), python-docx (DOCX) |
| Logging | structlog (JSON + console) with rotating file handler |
| Storage | Local filesystem or Google Cloud Storage (pluggable) |
| Container | Single Dockerfile, role-switched (api / worker / migrate) |

See [`AI_USAGE.md`](AI_USAGE.md) for the full prompt-engineering and AI-design rationale.

---

## Key design decisions

1. **pgvector over a separate vector DB.** One database for documents, chunks, embeddings, chat, and metrics. ACID across all data, fewer moving parts, HNSW index for fast cosine similarity.

2. **Direct Anthropic SDK over LangChain.** LangChain is used *only* for `RecursiveCharacterTextSplitter`. All LLM calls go through the Anthropic SDK directly, giving us full control over prompts, streaming, token counting, and error handling.

3. **Single LLM key.** Embeddings run locally via sentence-transformers, so the only secret you need is `ANTHROPIC_API_KEY`. Reduces setup friction and removes ongoing embedding costs.

4. **Async ingestion, synchronous chat.** Document processing runs in Celery (10–30s per doc); chat completions run synchronously inside the request handler (~2–5s). Adding a queue between user and Claude would add latency without benefit.

5. **Single-call structured analysis.** One Claude call returns summary + topics + entities + category + tags + sentiment + confidence as JSON. Cheaper, faster, and more coherent than separate calls.

6. **RAG with neighbor expansion.** Top-12 vector hits are expanded with adjacent chunks (±1 by default) so Claude sees surrounding narrative context, not isolated paragraphs. Critical for plot/causal questions.

7. **Hallucination guard, but not too strict.** The chat prompt requires Claude to ground every claim in the retrieved passages, but explicitly tells it to *synthesize partial evidence* rather than refusing on the slightest gap.

8. **Pluggable storage.** A small `storage_service` abstraction lets the same code run against the local filesystem in dev and GCS in production. Worker code uses a `local_path()` context manager that downloads from GCS to a tempfile when needed.

---

## Setup (local)

### Prerequisites
- Python 3.11+
- Docker & Docker Compose (for Postgres + Redis)
- An Anthropic API key

### Steps

```bash
# 1. Install
git clone <repo-url>
cd ai_document_helper
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=...

# 3. Start Postgres + Redis
docker compose up -d

# 4. Run migrations
alembic upgrade head

# 5. Start the API
uvicorn app.main:app --reload

# 6. Start the Celery worker (separate terminal)
celery -A app.tasks worker --loglevel=info --pool=threads --concurrency=4
```

> **Note:** the worker uses `--pool=threads` because PyTorch + macOS Metal + Celery's prefork pool crashes on `fork()`. The threads pool is safe everywhere.

### Open the app

| Page | URL |
|---|---|
| **Web UI** | http://localhost:8000/ui/ |
| Swagger | http://localhost:8000/docs |
| Health | http://localhost:8000/health/ready |

---

## API reference

### Health
| Method | Endpoint | Description |
|---|---|---|
| GET | `/health` | Basic liveness |
| GET | `/health/ready` | Deep readiness check (DB, Redis, pgvector, Celery, disk) |

### Documents
| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/v1/documents/upload` | Upload PDF/DOCX/TXT. Re-uploading same filename auto-increments version. |
| GET | `/api/v1/documents` | List (paginated, filterable by status) |
| GET | `/api/v1/documents/{id}` | Get document detail |
| GET | `/api/v1/documents/{id}/chunks` | Extracted text chunks (paginated) |
| GET | `/api/v1/documents/{id}/insights` | AI analysis (Redis-cached, 24h TTL) |
| POST | `/api/v1/documents/{id}/insights/regenerate` | Re-run analysis with `summary_length`, `tone`, `focus_area` |
| POST | `/api/v1/documents/compare` | Comparative analysis across 2+ documents |
| DELETE | `/api/v1/documents/{id}` | Delete document and its file |

### Chat
| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/v1/chat/sessions` | Create a session for one or many documents |
| GET | `/api/v1/chat/sessions` | List sessions |
| GET | `/api/v1/chat/sessions/{id}` | Session detail |
| POST | `/api/v1/chat/sessions/{id}/messages` | Ask a question (RAG, JSON response) |
| POST | `/api/v1/chat/sessions/{id}/messages/stream` | Same, but Server-Sent Events |
| GET | `/api/v1/chat/sessions/{id}/messages` | Full history |
| DELETE | `/api/v1/chat/sessions/{id}` | Delete session |

### Metrics
| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/metrics/documents` | Counts by status, total storage |
| GET | `/api/v1/metrics/processing` | Avg times, token usage, estimated USD cost |
| GET | `/api/v1/metrics/chat` | Session/message counts, response times |

Rate limits (slowapi): **10 uploads/min**, **20 chat messages/min** per IP.

---

## Demo flow (curl)

```bash
# Upload a document
curl -X POST http://localhost:8000/api/v1/documents/upload -F "file=@my_document.pdf"
# → {"id": "<doc-id>", "status": "pending", ...}

# Poll until completed
curl http://localhost:8000/api/v1/documents/<doc-id>
# → {"status": "completed", "chunk_count": 42, ...}

# View AI insights
curl http://localhost:8000/api/v1/documents/<doc-id>/insights

# Start a chat session
curl -X POST http://localhost:8000/api/v1/chat/sessions \
  -H "Content-Type: application/json" \
  -d '{"document_id": "<doc-id>"}'
# → {"id": "<session-id>", ...}

# Ask a question
curl -X POST http://localhost:8000/api/v1/chat/sessions/<session-id>/messages \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the main themes?"}'
```

…or just open `http://localhost:8000/ui/` and click around.

---

## Testing

```bash
pytest tests/ -v
```

Tests use a separate temp upload directory and truncate document tables at session end so they don't pollute your dev database. The `TESTING=true` flag is set automatically by `tests/conftest.py`.

---

## Logs

- **Console** — pretty (`structlog.dev.ConsoleRenderer`) when `LOG_LEVEL=DEBUG`, JSON otherwise.
- **File** — `logs/app.log`, JSON, rotated at 10 MB with 5 backups.

Both handlers receive the same events, including request IDs propagated via `structlog.contextvars`.

---

## Deploying to GCP (cheaply)

The project ships with a single `Dockerfile` and an `entrypoint.sh` that role-switches on `$ROLE`:

| `ROLE` | Command |
|---|---|
| `api` (default) | `uvicorn app.main:app` |
| `worker` | `celery -A app.tasks worker --pool=threads` |
| `migrate` | `alembic upgrade head` |

Recommended free/cheap tier setup (~$8–12/mo):

| Component | GCP service |
|---|---|
| API + worker | Cloud Run (one image, two services) |
| Postgres + pgvector | Cloud SQL `db-f1-micro` (or pgvector on a free-tier `e2-micro` VM for $0) |
| Redis | Upstash free tier (Memorystore is too expensive) |
| Files | Google Cloud Storage bucket (`STORAGE_BACKEND=gcs`, `GCS_BUCKET=...`) |
| Secrets | Secret Manager (`ANTHROPIC_API_KEY`) |
| Build/registry | Cloud Build + Artifact Registry |

Sketch of the deploy commands:

```bash
gcloud builds submit --tag us-central1-docker.pkg.dev/PROJECT/vault/app

# Run migrations once
gcloud run jobs create vault-migrate --image .../vault/app \
  --set-env-vars ROLE=migrate,DATABASE_URL_SYNC=...
gcloud run jobs execute vault-migrate

# API service
gcloud run deploy vault-api --image .../vault/app --region us-central1 \
  --add-cloudsql-instances PROJECT:us-central1:vault-db \
  --set-env-vars ROLE=api,STORAGE_BACKEND=gcs,GCS_BUCKET=your-bucket,DATABASE_URL=...,REDIS_URL=... \
  --set-secrets ANTHROPIC_API_KEY=anthropic-key:latest

# Worker service
gcloud run deploy vault-worker --image .../vault/app --region us-central1 \
  --no-cpu-throttling --min-instances=0 \
  --add-cloudsql-instances PROJECT:us-central1:vault-db \
  --set-env-vars ROLE=worker,STORAGE_BACKEND=gcs,GCS_BUCKET=your-bucket,DATABASE_URL=...,CELERY_BROKER_URL=... \
  --set-secrets ANTHROPIC_API_KEY=anthropic-key:latest
```

The `all-MiniLM-L6-v2` model is baked into the image at build time, so cold starts don't re-download the ~80 MB weights.

---

## Project structure

```
app/
  main.py                # FastAPI app, middleware, static UI mount
  config.py              # Pydantic settings (env-driven)
  database.py            # Async SQLAlchemy engine
  logging_config.py      # structlog + rotating file handler
  middleware.py          # Request ID, rate limiting
  models/                # SQLAlchemy ORM (Document, Chunk, Insight, Chat, Metric)
  schemas/               # Pydantic request/response models
  api/                   # Route handlers (thin)
  services/              # Business logic
    storage_service.py   #   pluggable local/GCS file storage
    document_service.py  #   upload, list, delete, versioning
    processing_service.py#   text extraction + chunking
    embedding_service.py #   local sentence-transformers
    ai_service.py        #   Anthropic SDK wrapper (non-streaming + streaming)
    chat_service.py      #   RAG pipeline + multi-turn history
    cache_service.py     #   Redis caching
    metrics_service.py   #   token + cost tracking
  tasks/                 # Celery worker tasks (process_document, regenerate_insights)
  prompts/               # Prompt templates (analysis, chat_qa, comparison)
alembic/                 # Database migrations
static/                  # Bundled single-page UI
tests/                   # pytest suite (uses isolated upload dir + table truncation)
Dockerfile               # Single image for api / worker / migrate
entrypoint.sh            # Role switch
docker-compose.yml       # Local Postgres + Redis
```

---

## Further reading

- [`AI_USAGE.md`](AI_USAGE.md) — prompt design, model choices, cost model, caching strategy.
