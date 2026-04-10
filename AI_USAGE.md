# AI Usage & Prompt Engineering

This document describes how AI models are used in the AI Document Vault system and the prompt engineering decisions behind each integration point.

## TL;DR

- **Development**: Built end-to-end with **Claude Code** (Anthropic's CLI coding assistant). Used for architecture planning, scaffolding the FastAPI project, generating Alembic migrations, debugging async SQLAlchemy / asyncpg issues, writing the test suite, and iteratively refactoring (storage abstraction, RAG with neighbor expansion, structured logging).
- **Runtime LLM**: **Claude Sonnet 4** (Anthropic API) for document analysis, RAG chat, streaming chat, and comparative analysis. Single API key for all LLM tasks.
- **Runtime embeddings**: **sentence-transformers `all-MiniLM-L6-v2`**, run locally on CPU. 384-dim, ~80 MB, no second API key needed.
- **Key prompt engineering decisions**: single Claude call returns structured JSON for analysis (cheaper + more coherent than multi-call); XML tags around document context (`<document>…</document>`); hallucination guard tuned to synthesize partial evidence rather than over-refuse; structured citations (snippet + page + chunk index) parsed from a JSON block; graceful fallback when JSON parsing fails so the pipeline never crashes.
- **AI-driven UX**: customizable summary length / tone / focus, follow-up question suggestions after every chat answer, comparative analysis across multiple documents, self-reported confidence score for downstream consumers.

Detailed breakdown below.

## AI Models Used

| Model | Provider | Purpose |
|-------|----------|---------|
| Claude (Anthropic) | Anthropic | Document analysis, chat completions, comparative analysis |
| all-MiniLM-L6-v2 | sentence-transformers (local) | Document chunk and query embeddings (384 dimensions) |

## Integration Points

### 1. Document Analysis (`app/services/ai_service.py` + `app/prompts/analysis.py`)

**What it does**: After a document is uploaded and chunked, the full text is sent to Claude for structured analysis producing a summary, key topics, named entities, category, tags, sentiment, language, and a confidence score.

**Prompt design decisions**:

- **Single call, structured JSON output**: One Claude call returns all analysis fields as a JSON object. This is cheaper, faster, and produces more coherent results than making separate calls for summary, entities, topics, etc. The model sees the full document once and reasons about all fields together.

- **XML document tags**: The document text is wrapped in `<document>` tags following Anthropic's recommended practice for clearly delineating input data from instructions.

- **Parameterized summary customization**: The prompt accepts three customization axes:
  - **Length** (brief / standard / detailed) — controls summary verbosity
  - **Tone** (professional / academic / casual / technical) — adjusts language register and formality
  - **Focus area** (free-text, e.g. "financial risks") — directs the model to emphasize specific aspects
  
  These are injected as separate instruction blocks so they compose naturally without conflicting.

- **Confidence score**: The model self-reports a 0.0-1.0 confidence score, which drops when input text is garbled, incomplete, or ambiguous. This gives downstream consumers a quality signal.

- **Truncation at 100k characters**: Very long documents are truncated with a `[Document truncated due to length]` marker to stay within token limits while signaling to the model that content is missing.

- **Graceful JSON parse failure**: If Claude wraps the JSON in markdown code blocks (common behavior), the parser strips them. If parsing still fails, a fallback structure is returned with the raw text as the summary and a low confidence score (0.3), so the pipeline never crashes.

### 2. RAG Chat (`app/services/chat_service.py` + `app/prompts/chat_qa.py`)

**What it does**: Users ask natural language questions about their documents. The system embeds the question, retrieves the top-12 most similar chunks (with ±1 neighbor expansion) via pgvector cosine similarity, and sends them as context to Claude along with conversation history.

**Prompt design decisions**:

- **Hallucination guard**: The system prompt explicitly states: *"Answer ONLY based on the provided context. Do not use external knowledge."* and instructs Claude to say *"I don't have enough information in the document to answer this question"* when context is insufficient. This is the most critical prompt engineering decision — RAG systems that don't constrain the model to provided context will confidently fabricate answers.

- **Structured citation format**: Claude is instructed to append a JSON block with `citations` (exact snippet, page number, chunk index) and `follow_up_suggestions`. This structured output enables the frontend to render clickable source references and conversation continuity suggestions.

- **Context passage formatting**: Retrieved chunks are formatted as numbered passages with page numbers and chunk indices (`[Passage 1] [Page 3] (chunk_index: 5)`). This gives Claude clear provenance markers to reference in citations.

- **Conversation history**: The last 10 messages are included for multi-turn context, enabling follow-up questions like "tell me more about that" or "what about the second point?".

- **Robust response parsing**: The `_parse_response` function handles cases where Claude omits the JSON block or produces malformed JSON. Fallback citations are generated from the retrieved chunks themselves, and generic follow-up suggestions are provided. The system never returns a response without citations.

- **Multi-document support**: When a session spans multiple documents, similarity search runs across all of them, and each citation includes the source document name so users know which document an answer came from.

### 3. Embeddings (`app/services/embedding_service.py`)

**What it does**: Generates vector embeddings for document chunks (at ingestion) and user questions (at query time) using a local `sentence-transformers` model.

**Design decisions**:

- **Local model over API-based**: Using `all-MiniLM-L6-v2` (384 dimensions) running locally instead of an external API. This eliminates the need for a second API key (only Anthropic is needed), removes embedding costs entirely, and avoids network latency for embeddings. The model downloads once (~80MB) and runs on CPU.

- **Single API key simplicity**: The entire system runs with just one `ANTHROPIC_API_KEY`. Claude handles all LLM tasks (analysis, chat, comparison), and embeddings run locally. This is a deliberate product decision — reducing setup friction matters for developer experience.

- **Batch embedding**: Document chunks are embedded in batches of 64 for efficient GPU/CPU utilization. Single embeddings are used for query-time question embedding.

- **Normalized embeddings**: `normalize_embeddings=True` ensures all vectors are unit-length, which makes cosine similarity equivalent to dot product — more efficient for pgvector.

### 4. Streaming (`app/services/ai_service.py`)

**What it does**: For chat, an SSE streaming endpoint sends partial responses to the client as Claude generates them.

**Design decisions**:

- **Anthropic streaming SDK**: Uses `client.messages.stream()` context manager, which yields text chunks incrementally. Token usage is extracted from the final message after the stream completes.

- **Three event types**: `chunk` (partial text), `citations` (parsed after stream completes), `done` (message ID + follow-up suggestions). This lets frontends render text progressively, then update the UI with structured metadata.

### 5. Comparative Analysis (`app/api/insights.py` + `app/prompts/comparison.py`)

**What it does**: Compares two or more documents by sending their AI-generated summaries and metadata to Claude for structured comparison.

**Prompt design decisions**:

- **Insights-based, not raw-text**: Instead of sending full document texts (which would be expensive and exceed context limits for large documents), the comparison uses previously generated summaries, key topics, categories, and sentiment. This is both efficient and produces better comparisons since the model works with pre-distilled information.

- **Structured comparison output**: The prompt requests JSON with `overview`, `similarities`, `differences`, `unique_insights` (per-document), and `relationships` (narrative). This gives consumers multiple levels of granularity.

- **Document labeling**: Each document section includes its name, category, sentiment, and topics, giving Claude enough context to produce meaningful cross-references.

## Caching Strategy (`app/services/cache_service.py`)

Redis is used to cache three types of data:

| Cache Target | TTL | Invalidation |
|-------------|-----|--------------|
| Query embeddings | 1 hour | None (immutable for same text) |
| Document insights | 24 hours | Invalidated on regeneration |
| Metrics aggregations | 60 seconds | Time-based expiry |

Query embedding caching is particularly valuable because the same question asked across different sessions produces identical embeddings — a cache hit saves a model inference (~50ms on CPU).

## Cost Tracking (`app/services/metrics_service.py`)

Every AI API call is tracked with:
- Operation type (embedding, analysis, chat, regenerate)
- Duration in seconds
- Token usage (input/output for Claude, total for embeddings)
- Estimated USD cost using per-token rates

Current rate assumptions:
| Model | Rate |
|-------|------|
| Claude input | $3.00 / 1M tokens |
| Claude output | $15.00 / 1M tokens |
| Embeddings | Free (local model) |

## Text Splitting Strategy

**LangChain RecursiveCharacterTextSplitter** is used for chunking with:
- `chunk_size=1000` characters
- `chunk_overlap=200` characters
- Default separators: `["\n\n", "\n", " ", ""]`

This was chosen over simpler splitting strategies because it respects paragraph and sentence boundaries, producing more semantically coherent chunks. The 200-character overlap ensures that information spanning chunk boundaries isn't lost during retrieval.

LangChain is used *only* for text splitting. All LLM calls use the Anthropic SDK directly, and embeddings run locally via sentence-transformers — giving full control over prompts, streaming, token counting, and error handling.

## Development with AI Coding Tools

This project was built end-to-end using **Claude Code** (Anthropic's CLI coding assistant). Below are representative prompts from the development process, showing how AI was used at each stage — from architecture to debugging to iteration.

### Architecture & Planning

**Prompt:**
> Design an implementation plan for a "ChatGPT for your documents" backend. Users upload documents, get AI-powered analysis, and chat with documents via RAG. Use FastAPI, PostgreSQL with pgvector, Celery + Redis for async processing, Claude for LLM, and local sentence-transformers for embeddings. Structure the project in phases so each one is independently demo-able.

**What it produced:** A 6-phase plan with exact file paths, database models, endpoint tables, and a dependency graph showing which phases could be parallelized. This became the project's roadmap — every phase maps to a commit in the final repo.

**Why this prompt worked:** Specifying the tech stack upfront prevented the AI from proposing alternatives mid-implementation. Requiring "independently demo-able" phases forced a bottom-up build order where each layer works before the next starts.

### Code Generation — RAG Pipeline

**Prompt:**
> Implement the RAG chat pipeline in chat_service.py. Steps: embed the user question via embedding_service, run pgvector cosine similarity search filtered to the session's document IDs, format retrieved chunks as numbered passages with page numbers, build conversation history from the last 10 messages, call Claude with a system prompt that requires grounded answers with citations, parse the response to extract a JSON citation block, and store both user and assistant messages. Support multi-document sessions where document_ids is a list.

**What it produced:** The full `chat_service.py` (~350 lines) including `_similarity_search_multi` with raw SQL for pgvector, `_parse_response` with fallback citation generation, and both sync and streaming code paths.

**Why this prompt worked:** Enumerating the pipeline steps in order gave the AI a clear contract for each function. Mentioning "multi-document sessions" early ensured the data model handled `document_ids` from the start rather than retrofitting it later.

### Debugging — SQL Parameter Binding

**Prompt:**
> The chat similarity search is failing silently — pgvector returns no results even though chunks exist. The raw SQL uses `:embedding::vector` and `:doc_ids::uuid[]` casts. I suspect the `::` syntax conflicts with SQLAlchemy's `:param` named parameter binding under asyncpg.

**What it produced:** Identified the exact conflict (asyncpg treats `::` as part of the parameter name, so `:embedding::vector` becomes a parameter named `embedding::vector` that's never bound). Fix: replace `::type` casts with `CAST(:param AS type)`.

**Why this prompt worked:** Including the specific SQL syntax and the hypothesis ("I suspect") gave the AI enough context to confirm the diagnosis immediately rather than suggesting generic debugging steps.

### Iteration — RAG Quality Improvement

**Prompt:**
> The RAG chat is returning too many "I don't have enough information" refusals, even for questions that should be answerable from the document. Top-5 chunks at 1000 characters each gives only ~5000 characters of context. For narrative questions about plot or causality, the model needs surrounding context, not just the best-matching paragraph.

**What it produced:** Three changes: (1) increased `TOP_K_CHUNKS` from 5 to 12, (2) added neighbor expansion that pulls ±1 adjacent chunks for each vector hit, and (3) rewrote the hallucination guard from "answer ONLY from context, refuse if insufficient" to "ground every claim in passages, synthesize across non-contiguous excerpts, refuse only when genuinely no evidence exists."

**Why this prompt worked:** Describing the *symptom* (over-refusal), the *root cause* (too little context), and the *domain constraint* (narrative questions need surrounding text) let the AI propose a multi-part fix that addressed retrieval quantity, retrieval quality, and prompt calibration simultaneously.

### Debugging — Celery Worker Crash

**Prompt:**
> The Celery worker crashes with SIGABRT when processing documents. The log shows it loads the sentence-transformers model on MPS (Metal), then the forked worker process dies. This is on macOS with Apple Silicon.

**What it produced:** Diagnosed the root cause (PyTorch initializes the Metal GPU backend, then Celery's prefork pool calls `fork()`, which is unsafe after Metal init). Two fixes: (1) force `device="cpu"` in `embedding_service.py`, (2) switch Celery to `--pool=threads` which avoids forking entirely.

### Refactoring — Storage Abstraction

**Prompt:**
> I need to deploy to GCP Cloud Run with Google Cloud Storage instead of the local filesystem. The upload code in document_service.py writes directly to disk, and the Celery worker reads from disk via file_path. Create a storage abstraction so the same code works with local filesystem in dev and GCS in production, switched by an env var. The worker needs real file paths for pdfplumber/python-docx, so GCS files need to be downloaded to a temp file transparently.

**What it produced:** `storage_service.py` with `save_file()`, `delete_file()`, and a `local_path()` context manager that transparently downloads GCS objects to temp files. Updated `document_service.py` and `document_tasks.py` to use the abstraction. Added `STORAGE_BACKEND` and `GCS_BUCKET` to config.

**Why this prompt worked:** Identifying the constraint (pdfplumber needs real paths) upfront prevented the AI from proposing a byte-stream interface that wouldn't work. Specifying "switched by env var" ensured the abstraction was config-driven rather than code-branched.

### Test Isolation Fix

**Prompt:**
> There are ~165 stale test files in the uploads/ directory and matching rows in the dev database from pytest runs. Fix the test setup so tests use an isolated upload directory and clean up database rows after the session.

**What it produced:** Updated `tests/conftest.py` to set `UPLOAD_DIR` to a `tempfile.mkdtemp()` before app import, added a session-scoped autouse fixture that truncates all document tables and removes the temp directory at teardown.

### Building the UI

**Prompt:**
> Build a basic single-page UI so I can test all the features from a browser instead of curl. It needs: file upload with drag-and-drop, a document list with status badges, tabs for Insights (summary + metadata cards), Chat (with SSE streaming, citations, follow-up buttons), Compare (multi-select documents), and Metrics. Use vanilla JS/HTML/CSS, no framework.

**What it produced:** A complete 863-line `static/index.html` with all requested features, auto-polling for document status, toast notifications, and a responsive layout.

### Key Takeaways

1. **Specificity beats brevity.** Prompts that named exact files, functions, and constraints produced working code in one shot. Vague prompts required multiple rounds of clarification.
2. **Include the "why."** Explaining the symptom and hypothesis (not just "fix this bug") let the AI skip generic suggestions and go straight to the right fix.
3. **Describe constraints upfront.** Mentioning "pdfplumber needs file paths" or "asyncpg conflicts with `::` syntax" in the prompt prevented solutions that would have failed on the first run.
4. **Iterate on AI output.** The RAG pipeline went through three versions: initial top-5, then full-document dump, then top-12 with neighbor expansion. Each iteration was driven by observing actual failure modes in testing.
