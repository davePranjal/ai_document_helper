import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware import limiter
from app.models.document import DocumentStatus
from app.schemas.chat import (
    AskQuestionRequest,
    ChatAnswerResponse,
    CreateSessionRequest,
    MessageListResponse,
    MessageResponse,
    SessionListResponse,
    SessionResponse,
)
from app.services import chat_service, document_service

router = APIRouter()


@router.post("/chat/sessions", response_model=SessionResponse, status_code=201)
async def create_chat_session(
    request: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new chat session for one or more documents.

    Provide either `document_id` for single-document chat or
    `document_ids` for multi-document chat. All documents must be
    fully processed (status=completed).
    """
    # Determine document IDs
    if request.document_ids:
        doc_ids = request.document_ids
    elif request.document_id:
        doc_ids = [request.document_id]
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either document_id or document_ids",
        )

    # Validate all documents exist and are completed
    for did in doc_ids:
        document = await document_service.get_document(db, did)
        if document.status != DocumentStatus.COMPLETED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Document {did} must be fully processed. Current status: {document.status.value}",
            )

    primary_id = doc_ids[0]
    multi_ids = doc_ids if len(doc_ids) > 1 else None
    session = await chat_service.create_session(db, primary_id, multi_ids)
    return SessionResponse.model_validate(session)


@router.get("/chat/sessions", response_model=SessionListResponse)
async def list_chat_sessions(
    document_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List chat sessions, optionally filtered by document."""
    sessions, total = await chat_service.list_sessions(db, document_id, page, page_size)
    return SessionListResponse(
        sessions=[SessionResponse.model_validate(s) for s in sessions],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/chat/sessions/{session_id}", response_model=SessionResponse)
async def get_chat_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get a chat session by ID."""
    session = await chat_service.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    return SessionResponse.model_validate(session)


@router.post("/chat/sessions/{session_id}/messages", response_model=ChatAnswerResponse)
@limiter.limit("20/minute")
async def ask_question(
    session_id: uuid.UUID,
    request: Request,
    payload: AskQuestionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Ask a question about the document in a chat session.

    Uses RAG (Retrieval-Augmented Generation) to find relevant passages
    from the document, then generates an answer with source citations.
    Supports multi-turn conversation with context from previous messages.
    """
    session = await chat_service.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")

    if not payload.question.strip():
        raise HTTPException(status_code=422, detail="Question cannot be empty")

    user_msg, assistant_msg = await chat_service.ask_question(
        db, session_id, payload.question.strip()
    )

    return ChatAnswerResponse(
        user_message=MessageResponse.model_validate(user_msg),
        answer=MessageResponse.model_validate(assistant_msg),
    )


@router.get("/chat/sessions/{session_id}/messages", response_model=MessageListResponse)
async def get_chat_messages(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get the full message history for a chat session."""
    session = await chat_service.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")

    messages = await chat_service.get_messages(db, session_id)
    return MessageListResponse(
        messages=[MessageResponse.model_validate(m) for m in messages],
        total=len(messages),
    )


@router.post("/chat/sessions/{session_id}/messages/stream")
@limiter.limit("20/minute")
async def ask_question_stream(
    session_id: uuid.UUID,
    request: Request,
    payload: AskQuestionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Ask a question with Server-Sent Events (SSE) streaming response.

    Returns a stream of events:
    - `data: {"type": "chunk", "text": "..."}` — partial response text
    - `data: {"type": "citations", "citations": [...]}` — source citations
    - `data: {"type": "done", "message_id": "...", "follow_up_suggestions": [...]}` — completion
    """
    session = await chat_service.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")

    if not payload.question.strip():
        raise HTTPException(status_code=422, detail="Question cannot be empty")

    async def event_stream():
        async for event in chat_service.ask_question_stream(
            db, session_id, payload.question.strip()
        ):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/chat/sessions/{session_id}", status_code=204)
async def delete_chat_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a chat session and all its messages."""
    session = await chat_service.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    await chat_service.delete_session(db, session_id)
