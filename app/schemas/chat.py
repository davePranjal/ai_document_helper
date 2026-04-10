import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CreateSessionRequest(BaseModel):
    document_id: uuid.UUID | None = None
    document_ids: list[uuid.UUID] | None = None


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    document_ids: list[uuid.UUID] | None = None
    title: str | None = None
    message_count: int
    created_at: datetime
    updated_at: datetime


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]
    total: int
    page: int
    page_size: int


class AskQuestionRequest(BaseModel):
    question: str


class CitationDetail(BaseModel):
    chunk_id: str | None = None
    snippet: str
    page_number: int | None = None
    relevance_score: float | None = None
    document_name: str | None = None


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    content: str
    citations: list[CitationDetail] | None = None
    follow_up_suggestions: list[str] | None = None
    token_usage: dict | None = None
    response_time_seconds: float | None = None
    created_at: datetime


class ChatAnswerResponse(BaseModel):
    answer: MessageResponse
    user_message: MessageResponse


class MessageListResponse(BaseModel):
    messages: list[MessageResponse]
    total: int
