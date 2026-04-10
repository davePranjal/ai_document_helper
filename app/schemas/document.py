import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.document import DocumentStatus


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    original_filename: str
    file_size: int
    mime_type: str
    status: DocumentStatus
    version: int = 1
    error_message: str | None = None
    page_count: int | None = None
    chunk_count: int | None = None
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int
    page: int
    page_size: int


class DocumentUploadResponse(BaseModel):
    id: uuid.UUID
    filename: str
    status: DocumentStatus
    version: int = 1
    message: str


class ChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    content: str
    chunk_index: int
    page_number: int | None = None
    token_count: int | None = None
    created_at: datetime


class ChunkListResponse(BaseModel):
    chunks: list[ChunkResponse]
    total: int
    page: int
    page_size: int
