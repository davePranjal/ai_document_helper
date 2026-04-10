import json
import structlog
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatSession, MessageRole
from app.models.document import Document, DocumentChunk
from app.prompts.chat_qa import CHAT_SYSTEM_PROMPT, build_chat_context
from app.services.ai_service import chat_completion, chat_completion_stream
from app.services.cache_service import get_cached_embedding, set_cached_embedding
from app.services.embedding_service import generate_single_embedding
from app.services.metrics_service import record_metric

logger = structlog.get_logger(__name__)

TOP_K_CHUNKS = 12
NEIGHBOR_WINDOW = 1  # include this many chunks before/after each hit
MAX_HISTORY_MESSAGES = 10


async def create_session(
    db: AsyncSession,
    document_id: uuid.UUID,
    document_ids: list[uuid.UUID] | None = None,
) -> ChatSession:
    """Create a new chat session for one or more documents."""
    session = ChatSession(
        document_id=document_id,
        document_ids=[str(d) for d in document_ids] if document_ids else None,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def get_session(db: AsyncSession, session_id: uuid.UUID) -> ChatSession | None:
    result = await db.execute(select(ChatSession).where(ChatSession.id == session_id))
    return result.scalar_one_or_none()


async def list_sessions(
    db: AsyncSession,
    document_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[ChatSession], int]:
    query = select(ChatSession)
    count_query = select(func.count()).select_from(ChatSession)

    if document_id:
        query = query.where(ChatSession.document_id == document_id)
        count_query = count_query.where(ChatSession.document_id == document_id)

    query = query.order_by(ChatSession.updated_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    sessions = list(result.scalars().all())

    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    return sessions, total


async def get_messages(
    db: AsyncSession, session_id: uuid.UUID
) -> list[ChatMessage]:
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    return list(result.scalars().all())


async def delete_session(db: AsyncSession, session_id: uuid.UUID) -> None:
    session = await get_session(db, session_id)
    if session:
        await db.delete(session)
        await db.commit()


async def ask_question(
    db: AsyncSession, session_id: uuid.UUID, question: str
) -> tuple[ChatMessage, ChatMessage]:
    """Process a user question through the RAG pipeline.

    Supports both single-document and multi-document chat.
    Returns (user_message, assistant_message).
    """
    session = await get_session(db, session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    # Determine which documents to search
    doc_ids = _get_session_document_ids(session)

    # Get document names for context
    doc_result = await db.execute(
        select(Document).where(Document.id.in_(doc_ids))
    )
    documents = {str(d.id): d for d in doc_result.scalars().all()}
    doc_names = [d.original_filename for d in documents.values()]
    context_label = ", ".join(doc_names) if len(doc_names) > 1 else doc_names[0]

    # Step 1: Store user message
    user_msg = ChatMessage(
        session_id=session_id,
        role=MessageRole.USER,
        content=question,
    )
    db.add(user_msg)
    await db.flush()

    # Step 2: Embed the question (check cache first)
    question_embedding = await get_cached_embedding(question)
    if not question_embedding:
        question_embedding = generate_single_embedding(question)
        await set_cached_embedding(question, question_embedding)

    # Step 3: Retrieve context (full doc if small, top-K otherwise)
    relevant_chunks = await _retrieve_context_chunks(db, doc_ids, question_embedding)

    # Annotate chunks with document names
    for chunk in relevant_chunks:
        doc = documents.get(chunk.get("document_id"))
        chunk["document_name"] = doc.original_filename if doc else None

    # Step 4: Build conversation context
    context = build_chat_context(
        document_name=context_label,
        chunks=relevant_chunks,
    )

    # Step 5: Get conversation history for multi-turn context
    history = await _get_recent_history(db, session_id, MAX_HISTORY_MESSAGES)

    # Step 6: Build messages for Claude
    messages = []
    for msg in history:
        messages.append({"role": msg.role.value, "content": msg.content})

    # Add context + question as the current user message
    full_question = f"{context}\n\nQuestion: {question}"
    messages.append({"role": "user", "content": full_question})

    # Step 7: Call Claude
    result = chat_completion(
        system_prompt=CHAT_SYSTEM_PROMPT,
        messages=messages,
    )

    # Step 8: Parse response
    answer_text, citations, follow_ups = _parse_response(result["content"], relevant_chunks)

    # Step 9: Store assistant message
    assistant_msg = ChatMessage(
        session_id=session_id,
        role=MessageRole.ASSISTANT,
        content=answer_text,
        citations=citations,
        follow_up_suggestions=follow_ups,
        token_usage=result["token_usage"],
        response_time_seconds=result["processing_time_seconds"],
    )
    db.add(assistant_msg)

    # Record chat metric
    await record_metric(
        db, session.document_id, "chat",
        result["processing_time_seconds"], result["token_usage"],
    )

    # Update session
    session.message_count = session.message_count + 2
    if not session.title and session.message_count <= 2:
        session.title = question[:100] + ("..." if len(question) > 100 else "")

    await db.commit()
    await db.refresh(user_msg)
    await db.refresh(assistant_msg)

    return user_msg, assistant_msg


async def ask_question_stream(
    db: AsyncSession, session_id: uuid.UUID, question: str
):
    """Stream a RAG response via SSE. Yields event dicts."""
    session = await get_session(db, session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found")

    doc_ids = _get_session_document_ids(session)

    doc_result = await db.execute(select(Document).where(Document.id.in_(doc_ids)))
    documents = {str(d.id): d for d in doc_result.scalars().all()}
    doc_names = [d.original_filename for d in documents.values()]
    context_label = ", ".join(doc_names) if len(doc_names) > 1 else doc_names[0]

    # Store user message
    user_msg = ChatMessage(
        session_id=session_id, role=MessageRole.USER, content=question
    )
    db.add(user_msg)
    await db.flush()

    # Embed & search (check cache first)
    question_embedding = await get_cached_embedding(question)
    if not question_embedding:
        question_embedding = generate_single_embedding(question)
        await set_cached_embedding(question, question_embedding)
    relevant_chunks = await _retrieve_context_chunks(db, doc_ids, question_embedding)
    for chunk in relevant_chunks:
        doc = documents.get(chunk.get("document_id"))
        chunk["document_name"] = doc.original_filename if doc else None

    context = build_chat_context(document_name=context_label, chunks=relevant_chunks)
    history = await _get_recent_history(db, session_id, MAX_HISTORY_MESSAGES)

    messages = [{"role": msg.role.value, "content": msg.content} for msg in history]
    messages.append({"role": "user", "content": f"{context}\n\nQuestion: {question}"})

    # Stream from Claude
    full_text = ""
    token_usage = {}
    processing_time = 0.0

    for event in chat_completion_stream(system_prompt=CHAT_SYSTEM_PROMPT, messages=messages):
        if event["type"] == "chunk":
            full_text += event["text"]
            yield {"type": "chunk", "text": event["text"]}
        elif event["type"] == "done":
            token_usage = event["token_usage"]
            processing_time = event["processing_time_seconds"]

    # Parse and emit citations
    answer_text, citations, follow_ups = _parse_response(full_text, relevant_chunks)

    yield {"type": "citations", "citations": citations}

    # Store assistant message
    assistant_msg = ChatMessage(
        session_id=session_id,
        role=MessageRole.ASSISTANT,
        content=answer_text,
        citations=citations,
        follow_up_suggestions=follow_ups,
        token_usage=token_usage,
        response_time_seconds=processing_time,
    )
    db.add(assistant_msg)

    await record_metric(db, session.document_id, "chat", processing_time, token_usage)

    session.message_count = session.message_count + 2
    if not session.title and session.message_count <= 2:
        session.title = question[:100] + ("..." if len(question) > 100 else "")

    await db.commit()
    await db.refresh(assistant_msg)

    yield {
        "type": "done",
        "message_id": str(assistant_msg.id),
        "follow_up_suggestions": follow_ups,
    }


def _get_session_document_ids(session: ChatSession) -> list[uuid.UUID]:
    """Get all document IDs for a session (supports multi-doc)."""
    if session.document_ids:
        return [uuid.UUID(d) for d in session.document_ids]
    return [session.document_id]


async def _retrieve_context_chunks(
    db: AsyncSession,
    document_ids: list[uuid.UUID],
    question_embedding: list[float],
) -> list[dict]:
    """RAG retrieval: top-K vector search, expanded with neighboring chunks.

    Vector search alone returns isolated paragraphs. By also pulling the
    chunks immediately before and after each hit (within the same document),
    Claude sees the surrounding narrative context — which is what makes
    plot/causal questions answerable.
    """
    hits = await _similarity_search_multi(
        db, document_ids, question_embedding, TOP_K_CHUNKS
    )
    if not hits or NEIGHBOR_WINDOW <= 0:
        return hits

    # Collect (document_id, chunk_index) pairs for hits + their neighbors
    wanted: set[tuple[str, int]] = set()
    similarity_by_key: dict[tuple[str, int], float] = {}
    for h in hits:
        doc_id = h["document_id"]
        idx = h["chunk_index"]
        similarity_by_key[(doc_id, idx)] = h["similarity"]
        for offset in range(-NEIGHBOR_WINDOW, NEIGHBOR_WINDOW + 1):
            wanted.add((doc_id, idx + offset))

    # Single query to fetch hits + neighbors (filtered to valid indices)
    doc_id_strs = list({d for d, _ in wanted})
    rows = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.document_id.in_([uuid.UUID(d) for d in doc_id_strs]))
        .order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
    )
    expanded: list[dict] = []
    for c in rows.scalars().all():
        key = (str(c.document_id), c.chunk_index)
        if key not in wanted:
            continue
        expanded.append({
            "chunk_id": str(c.id),
            "document_id": str(c.document_id),
            "content": c.content,
            "chunk_index": c.chunk_index,
            "page_number": c.page_number,
            # Neighbor chunks inherit the similarity of their nearest hit so
            # citation ordering still favors the strongest matches.
            "similarity": similarity_by_key.get(key, 0.0),
        })

    logger.info(
        "RAG retrieval: %d hits → %d chunks after ±%d neighbor expansion",
        len(hits), len(expanded), NEIGHBOR_WINDOW,
    )
    return expanded


async def _similarity_search_multi(
    db: AsyncSession,
    document_ids: list[uuid.UUID],
    query_embedding: list[float],
    top_k: int,
) -> list[dict]:
    """Find similar chunks across multiple documents using pgvector cosine distance."""
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
    doc_id_strs = [str(d) for d in document_ids]

    query = text("""
        SELECT id, document_id, content, chunk_index, page_number,
               1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
        FROM document_chunks
        WHERE document_id = ANY(CAST(:doc_ids AS uuid[]))
        ORDER BY embedding <=> CAST(:embedding AS vector)
        LIMIT :top_k
    """)

    result = await db.execute(
        query,
        {
            "embedding": embedding_str,
            "doc_ids": doc_id_strs,
            "top_k": top_k,
        },
    )

    chunks = []
    for row in result.fetchall():
        chunks.append({
            "chunk_id": str(row[0]),
            "document_id": str(row[1]),
            "content": row[2],
            "chunk_index": row[3],
            "page_number": row[4],
            "similarity": round(float(row[5]), 4),
        })

    logger.info(
        "Retrieved %d chunks across %d documents (similarities: %s)",
        len(chunks), len(document_ids),
        [c["similarity"] for c in chunks],
    )
    return chunks


async def _get_recent_history(
    db: AsyncSession, session_id: uuid.UUID, limit: int
) -> list[ChatMessage]:
    """Get the most recent messages for multi-turn context."""
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .where(ChatMessage.role.in_([MessageRole.USER, MessageRole.ASSISTANT]))
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )
    messages = list(result.scalars().all())
    messages.reverse()  # Oldest first
    return messages


def _parse_response(
    response_text: str, relevant_chunks: list[dict]
) -> tuple[str, list[dict], list[str]]:
    """Parse Claude's response to extract answer text, citations, and follow-up suggestions."""
    citations = []
    follow_ups = []

    # Try to find JSON block at the end of the response
    json_start = response_text.rfind("```json")
    if json_start != -1:
        answer_text = response_text[:json_start].strip()
        json_block = response_text[json_start:]
        json_str = json_block.replace("```json", "").replace("```", "").strip()
        try:
            data = json.loads(json_str)
            raw_citations = data.get("citations", [])
            for c in raw_citations:
                chunk_id = None
                chunk_idx = c.get("chunk_index")
                if chunk_idx is not None:
                    for rc in relevant_chunks:
                        if rc["chunk_index"] == chunk_idx:
                            chunk_id = rc["chunk_id"]
                            break

                citations.append({
                    "chunk_id": chunk_id,
                    "snippet": c.get("snippet", ""),
                    "page_number": c.get("page_number"),
                    "relevance_score": next(
                        (rc["similarity"] for rc in relevant_chunks
                         if rc.get("chunk_id") == chunk_id),
                        None,
                    ),
                    "document_name": next(
                        (rc.get("document_name") for rc in relevant_chunks
                         if rc.get("chunk_id") == chunk_id),
                        None,
                    ),
                })
            follow_ups = data.get("follow_up_suggestions", [])[:3]
        except json.JSONDecodeError:
            logger.warning("Failed to parse citation JSON from response")
    else:
        answer_text = response_text

    # If no citations were parsed, generate basic ones from retrieved chunks
    if not citations:
        for chunk in relevant_chunks[:3]:
            citations.append({
                "chunk_id": chunk["chunk_id"],
                "snippet": chunk["content"][:150] + "...",
                "page_number": chunk.get("page_number"),
                "relevance_score": chunk["similarity"],
                "document_name": chunk.get("document_name"),
            })

    if not follow_ups:
        follow_ups = ["Can you tell me more about this topic?",
                      "What are the key takeaways?",
                      "Are there any related points in the document?"]

    return answer_text, citations, follow_ups
