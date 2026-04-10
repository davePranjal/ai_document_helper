import uuid

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import DocumentNotFoundError, FileValidationError
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services import storage_service

MIME_TYPE_MAP = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
}


def _get_extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _validate_file(file: UploadFile) -> str:
    if not file.filename:
        raise FileValidationError("Filename is required")

    ext = _get_extension(file.filename)
    if ext not in settings.allowed_extensions_list:
        raise FileValidationError(
            f"File type '.{ext}' not allowed. Allowed: {settings.allowed_extensions_list}"
        )

    return ext


async def upload_document(db: AsyncSession, file: UploadFile) -> Document:
    ext = _validate_file(file)

    # Read file content and validate size
    content = await file.read()
    if len(content) > settings.max_file_size_bytes:
        raise FileValidationError(
            f"File size {len(content)} bytes exceeds maximum {settings.max_file_size_mb}MB"
        )

    # Check for existing document with the same filename (version tracking)
    existing_result = await db.execute(
        select(Document)
        .where(Document.original_filename == file.filename)
        .order_by(Document.version.desc())
        .limit(1)
    )
    existing = existing_result.scalar_one_or_none()
    new_version = (existing.version + 1) if existing else 1

    # Persist via storage backend (local fs or GCS)
    file_uri = storage_service.save_file(content, ext)

    # Create database record
    mime_type = MIME_TYPE_MAP.get(ext, "application/octet-stream")
    document = Document(
        filename=file_uri.rsplit("/", 1)[-1],
        original_filename=file.filename,
        file_path=file_uri,
        file_size=len(content),
        mime_type=mime_type,
        status=DocumentStatus.PENDING,
        version=new_version,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    return document


async def get_document(db: AsyncSession, document_id: uuid.UUID) -> Document:
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()
    if not document:
        raise DocumentNotFoundError(str(document_id))
    return document


async def list_documents(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    status: DocumentStatus | None = None,
) -> tuple[list[Document], int]:
    query = select(Document)
    count_query = select(func.count()).select_from(Document)

    if status:
        query = query.where(Document.status == status)
        count_query = count_query.where(Document.status == status)

    query = query.order_by(Document.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    documents = list(result.scalars().all())

    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    return documents, total


async def get_document_chunks(
    db: AsyncSession,
    document_id: uuid.UUID,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[DocumentChunk], int]:
    # Verify document exists
    await get_document(db, document_id)

    query = (
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    count_query = (
        select(func.count())
        .select_from(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
    )

    result = await db.execute(query)
    chunks = list(result.scalars().all())

    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    return chunks, total


async def delete_document(db: AsyncSession, document_id: uuid.UUID) -> None:
    document = await get_document(db, document_id)
    storage_service.delete_file(document.file_path)
    await db.delete(document)
    await db.commit()
