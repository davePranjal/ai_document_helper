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

**What it does**: Users ask natural language questions about their documents. The system embeds the question, retrieves the top-5 most similar chunks via pgvector cosine similarity, and sends them as context to Claude along with conversation history.

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

Query embedding caching is particularly valuable because the same question asked across different sessions produces identical embeddings — a cache hit saves an OpenAI API call (~100ms + cost).

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

LangChain is used *only* for text splitting. All LLM calls and embeddings use the Anthropic and OpenAI SDKs directly, giving full control over prompts, streaming, token counting, and error handling.

## Development Tools

This project was built using **Claude Code** (Anthropic's CLI coding assistant) for:
- Architecture planning and iterative implementation
- Code generation across all layers (models, services, APIs, tasks, prompts, tests)
- Debugging async SQLAlchemy / asyncpg issues (BaseHTTPMiddleware conflicts, connection pool leaks)
- Migration generation and pgvector index configuration
- Test authoring and failure diagnosis
