import uuid

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.middleware import limiter
from app.models.document import DocumentStatus
from app.schemas.document import (
    ChunkListResponse,
    ChunkResponse,
    DocumentListResponse,
    DocumentResponse,
    DocumentUploadResponse,
)
from app.services import document_service
from app.tasks.document_tasks import process_document

router = APIRouter()


@router.post("/documents/upload", response_model=DocumentUploadResponse, status_code=202)
@limiter.limit("10/minute")
async def upload_document(request: Request, file: UploadFile, db: AsyncSession = Depends(get_db)):
    """Upload a document for AI processing.

    Accepts PDF, DOCX, and TXT files. The document will be queued for
    asynchronous processing (text extraction, chunking, and embedding).
    """
    document = await document_service.upload_document(db, file)

    # Dispatch async processing task
    process_document.delay(str(document.id))

    return DocumentUploadResponse(
        id=document.id,
        filename=document.original_filename,
        status=document.status,
        message="Document uploaded successfully. Processing will begin shortly.",
    )


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: DocumentStatus | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List all documents with optional filtering and pagination."""
    documents, total = await document_service.list_documents(db, page, page_size, status)
    return DocumentListResponse(
        documents=[DocumentResponse.model_validate(doc) for doc in documents],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(document_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get a single document by ID."""
    document = await document_service.get_document(db, document_id)
    return DocumentResponse.model_validate(document)


@router.get("/documents/{document_id}/chunks", response_model=ChunkListResponse)
async def get_document_chunks(
    document_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Get the text chunks extracted from a document."""
    chunks, total = await document_service.get_document_chunks(db, document_id, page, page_size)
    return ChunkListResponse(
        chunks=[ChunkResponse.model_validate(c) for c in chunks],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Delete a document and its associated file."""
    await document_service.delete_document(db, document_id)
