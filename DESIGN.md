# Design Notes

This document explains the design principles used in this codebase and where each one shows up. It's intended as a "talk track" — every claim points to a specific file so you can open it during a discussion.

---

## 1. Layered architecture (separation of concerns)

The code is organized in concentric layers; each layer only depends on layers below it.

```
┌──────────────────────────────────────────────────┐
│ api/        ← HTTP layer (routing, validation)   │
├──────────────────────────────────────────────────┤
│ services/   ← business logic (pure functions    │
│              that take a session + inputs)       │
├──────────────────────────────────────────────────┤
│ models/     ← SQLAlchemy ORM (persistence)       │
│ schemas/    ← Pydantic DTOs (wire format)        │
├──────────────────────────────────────────────────┤
│ database / config / logging / middleware         │
│              ← cross-cutting infrastructure      │
└──────────────────────────────────────────────────┘
```

**Why this matters**

- API handlers (`app/api/*.py`) are *thin*: they parse the request, call one or two service functions, and shape the response. They contain no business logic and no SQL. Look at `app/api/chat.py:91-119` — the `ask_question` route is ~25 lines of orchestration.
- Services (`app/services/*.py`) do all the real work and never touch FastAPI types. This means business logic is testable without spinning up an HTTP client and reusable from anywhere (Celery tasks, scripts, future GraphQL endpoints).
- ORM models (`app/models/*.py`) and Pydantic schemas (`app/schemas/*.py`) are deliberately separate. ORM models describe the database; Pydantic schemas describe the API contract. Mixing them would couple the wire format to the storage format and make migrations painful.

**Rule of thumb followed**: a request flows downward through the layers, and a response flows back up. No layer ever calls *up* into a layer above it.

---

## 2. Single Responsibility Principle (SRP)

Each service file owns one concern:

| File | Responsibility |
|---|---|
| `services/document_service.py` | Document CRUD + version tracking |
| `services/storage_service.py` | Where bytes live (local fs vs GCS) — **only** that |
| `services/processing_service.py` | Text extraction + chunking |
| `services/embedding_service.py` | Local sentence-transformers wrapper |
| `services/ai_service.py` | Anthropic SDK wrapper (non-streaming + streaming) |
| `services/chat_service.py` | RAG pipeline + multi-turn orchestration |
| `services/cache_service.py` | Redis caching primitives |
| `services/metrics_service.py` | Token + cost tracking |

If a future change touches "where files are stored", **only** `storage_service.py` should change. If it touches "how we talk to Claude", only `ai_service.py`. This is the test for whether SRP is real or just talk.

The service split was a deliberate refactor — earlier the upload code wrote files inline, then the worker re-opened them by path. Splitting storage out of `document_service.py` was what made the GCS migration a one-file change rather than a search-and-replace across the codebase.

---

## 3. Dependency inversion via abstraction

Two clear examples in the code:

### a) Storage backend (`app/services/storage_service.py`)

Callers don't know whether files live on disk or in GCS. They get an opaque "URI" from `save_file()` and pass it to `delete_file()` or the `local_path()` context manager. The backend is selected by the `STORAGE_BACKEND` setting.

```python
with storage_service.local_path(document.file_path) as local_file:
    text, page_count = extract_text(local_file, document.mime_type)
```

`extract_text` (which uses pdfplumber and python-docx — both require real filesystem paths) doesn't know or care that the file may have been transparently downloaded from a GCS bucket to a temp file. The abstraction protects everyone above it from the difference.

**Why**: this is exactly the "depend on abstractions, not implementations" principle. The Cloud Run deployment dropped in cleanly precisely because the abstraction was already in place.

### b) Cache (`app/services/cache_service.py`)

The `cache_service` exposes `get_cached_*` / `set_cached_*` functions. Callers don't know it's Redis under the hood, and they also don't know that it's silently disabled when `TESTING=true`. If we ever moved to in-memory caching for dev or Memorystore for prod, no caller changes.

---

## 4. Async ingestion vs synchronous chat (right tool for the job)

This is a deliberate, defendable architectural choice — not laziness.

- **Document ingestion** runs in **Celery** (`app/tasks/document_tasks.py`). Reason: it takes 10–30s per document (extract → chunk → embed → analyze with Claude). Blocking the HTTP request would tie up a uvicorn worker and time out browsers. Returning `202 Accepted` + a polled status is the standard pattern.
- **Chat completions** run **synchronously** inside the request handler. Reason: chat is conversational. 2–5s of latency is acceptable, even expected. Adding a queue would add complexity (poll-for-result, websocket fanout, lost messages on broker outage) for zero UX benefit.

**Rule of thumb**: don't add a queue unless the work is too slow to block on, *or* you need retries / fanout. Chat needs neither.

---

## 5. Configuration over code (12-factor)

`app/config.py` defines a single `Settings` class fed by environment variables (with `.env` for local dev). Every behavior that varies between environments — DB URL, broker URL, model names, storage backend, GCS bucket, log level, file size limits — is a setting, not a hardcoded constant.

**Why**: the same Docker image runs locally, in staging, and in Cloud Run with no rebuild. The `Dockerfile` is environment-agnostic; the `entrypoint.sh` reads `$ROLE` to decide whether to launch the API, the worker, or migrations.

---

## 6. Fail-soft with graceful degradation

Several places choose to return *something useful* instead of crashing on unexpected input. Each is a deliberate trade-off, not laziness:

- **`chat_service._parse_response`** — Claude is *asked* to return citations as a JSON block, but LLMs occasionally drop or malform structured output. If parsing fails, we synthesize citations from the retrieved chunks themselves and provide generic follow-ups. The user always gets *some* answer with *some* sources.
- **`ai_service.analyze_document`** — if the analysis JSON fails to parse, we return a low-confidence (`0.3`) fallback structure with the raw text as the summary. The pipeline never crashes on a single bad LLM response.
- **`cache_service`** — every Redis call is wrapped so a Redis outage degrades the system to "uncached" rather than taking the API down.

The pattern: **identify the boundaries where we don't trust the world** (LLMs, network services, user input) and contain failure there. Inside those boundaries, code can assume things work.

---

## 7. Hallucination-aware prompt engineering

The hardest part of any RAG system is getting the model to *not* make things up while also not refusing every question. The chat prompt (`app/prompts/chat_qa.py`) uses three calibrated rules:

1. "Ground every claim in the provided passages."
2. "The passages may be non-contiguous excerpts. Synthesize across them."
3. "Refuse only when there is genuinely no relevant evidence; otherwise give a partial answer with caveats."

This was tuned after observing the original prompt over-refuse on narrative questions. The current wording is the result of an explicit experiment: "what's the minimum constraint that prevents fabrication without making the model paranoid?"

**Defendable claim**: prompt engineering decisions in this codebase are documented (in `AI_USAGE.md`) so they can be reviewed and changed deliberately, not by accident.

---

## 8. RAG pipeline design

`chat_service._retrieve_context_chunks` does **top-K vector search + neighbor expansion**:

1. Embed the question (cache check first to avoid re-embedding identical questions).
2. Cosine similarity search via pgvector → top 12 chunks.
3. For each hit, also pull the chunk before and after (within the same document).
4. Pass all of these to Claude in original document order.

**Why neighbors matter**: a 1000-character chunk is a few sentences. A vector search hit on chunk 42 might contain "the killer was…" but the actual name is in chunk 41 or 43. Without neighbor expansion, the LLM has the right paragraph but missing context. With neighbors, narrative reasoning works.

**Why pgvector specifically**: one database for documents, chunks, embeddings, chat, and metrics. ACID across all of it. No data sync between Postgres and a vector DB. The HNSW index gives sub-millisecond cosine similarity at our scale.

---

## 9. Caching strategy (read-mostly, time-bounded)

| What | TTL | Why |
|---|---|---|
| Query embeddings | 1h | Same question text → same embedding deterministically |
| Document insights | 24h | Insights are stable until the user explicitly regenerates |
| Metrics aggregations | 60s | Tolerates slight staleness, saves expensive aggregate queries |

**Rule followed**: cache things that are *expensive to compute* and *cheap to be slightly stale*. Never cache things that the user expects to see immediately after writing them.

The insights cache is also explicitly **invalidated on regenerate** — time-based TTL is the floor, not the ceiling.

---

## 10. Defensive testing posture

`tests/conftest.py` does three important things:

1. Sets `TESTING=true` *before* importing the app, so `cache_service` knows to no-op against Redis.
2. Routes test uploads to a temp directory (`tempfile.mkdtemp(...)`) so they don't pollute the dev `uploads/` folder.
3. Truncates document/chunk/insight tables at session end to keep the dev DB clean.

This was a fix for an actual problem (~165 stray test files in `uploads/` and matching DB rows). The principle: **tests should leave no trace.** A test suite that requires manual cleanup is broken.

---

## 11. Observable by default

Three pillars, all in place from day one:

- **Structured logging** (`app/logging_config.py`) — JSON to stdout *and* a rotating file (`logs/app.log`). Every log line carries a request ID propagated via `structlog.contextvars`, so you can trace a single request across middleware, service calls, and Celery tasks.
- **Metrics** (`/api/v1/metrics/*`) — every Claude call records duration, token usage, and estimated USD cost in `processing_metrics`. The endpoints aggregate these for monitoring and cost control.
- **Health checks** — `/health` is a liveness check; `/health/ready` is a deep readiness check that probes Postgres + pgvector, Redis, the Celery broker, and disk space. Cloud Run can use `/health/ready` as its startup probe.

**Why this matters**: you cannot debug or right-size what you can't measure. Putting observability in *before* you need it is cheaper than retrofitting it under pressure.

---

## 12. Things deliberately left out (YAGNI)

It's just as important to be able to defend the things you *didn't* build. A few:

- **No user accounts / multi-tenancy.** This is a single-user demo. Adding auth would be ~3 days of work and obscures the AI architecture, which is what the assessment is about. The gap is documented and the design (per-document IDs, pluggable auth via FastAPI dependencies) leaves room to add it later without rewrites.
- **No streaming for document processing.** Chat streams; ingestion doesn't. Ingestion is a fire-and-forget background job — there's nothing to stream to.
- **No retry/dead-letter logic for chat failures.** A failed chat message returns 500 and the user retries. Adding retries to a synchronous LLM call risks doubling token costs on transient errors. Celery does retry document processing where retries are safe.
- **No abstraction over Anthropic.** We use the Anthropic SDK directly instead of LangChain or a custom LLM provider interface. Reason: there's only one LLM provider, and the abstraction would be speculative. If we add a second provider, the right shape of the abstraction will become obvious *then* — not now.

The principle: **don't build for hypothetical futures.** Build for the current requirements clearly and leave seams (small services, abstractions only where they currently pay rent) so future change is cheap.

---

## How to talk about this in an interview

1. **Start with the layered architecture** — this is the load-bearing decision. Every other principle is enabled by it.
2. **Pick one or two abstractions to walk through end-to-end.** The storage abstraction is the best one because it has a "before" (local-only) and an "after" (works with GCS unchanged) and the migration was a single-file change.
3. **Defend an omission.** Interviewers love when candidates can articulate what they *didn't* build and why. The "no streaming for ingestion" or "no LLM provider abstraction" examples work well.
4. **Have a number ready for one tradeoff.** "Top-K=12 + neighbor expansion gives ~30 chunks of context = ~7,500 tokens per chat call = ~$0.022 per question at Sonnet input rates" — concrete numbers signal that the design wasn't picked at random.
