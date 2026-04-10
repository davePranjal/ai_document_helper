import structlog
import time
import uuid

from sqlalchemy import select

from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.insight import DocumentInsight
from app.prompts.analysis import build_analysis_prompt
from app.services.ai_service import analyze_document
from app.services.embedding_service import count_tokens, generate_embeddings
from app.services.metrics_service import record_metric_sync
from app.services import storage_service
from app.services.processing_service import chunk_text, extract_text
from app.tasks import celery_app

logger = structlog.get_logger(__name__)


def _get_sync_session():
    """Create a synchronous database session for Celery tasks."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.config import settings

    engine = create_engine(settings.database_url_sync)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def process_document(self, document_id: str):
    """Process an uploaded document: extract text, chunk, embed, analyze."""
    db = _get_sync_session()
    doc_uuid = uuid.UUID(document_id)

    try:
        # Fetch document
        document = db.execute(
            select(Document).where(Document.id == doc_uuid)
        ).scalar_one_or_none()

        if not document:
            logger.error("Document %s not found", document_id)
            return {"status": "error", "message": "Document not found"}

        # Update status to processing
        document.status = DocumentStatus.PROCESSING
        db.commit()

        logger.info("Processing document %s: %s", document_id, document.original_filename)

        # Step 1: Extract text (download from storage backend if remote)
        with storage_service.local_path(document.file_path) as local_file:
            text, page_count = extract_text(local_file, document.mime_type)
        if not text.strip():
            document.status = DocumentStatus.FAILED
            document.error_message = "No text content could be extracted from the document"
            db.commit()
            return {"status": "failed", "message": "No text extracted"}

        document.page_count = page_count
        logger.info("Extracted %d characters, %s pages", len(text), page_count)

        # Step 2: Chunk text
        chunks = chunk_text(text, page_count)
        logger.info("Created %d chunks", len(chunks))

        # Step 3: Generate embeddings
        chunk_texts = [c["content"] for c in chunks]
        embed_start = time.time()
        embeddings = generate_embeddings(chunk_texts)
        embed_duration = time.time() - embed_start

        # Record embedding metric
        total_tokens = sum(count_tokens(t) for t in chunk_texts)
        record_metric_sync(
            db, doc_uuid, "embedding", embed_duration,
            {"total_tokens": total_tokens}, "success",
        )

        # Step 4: Store chunks with embeddings
        for chunk_data, embedding in zip(chunks, embeddings):
            chunk = DocumentChunk(
                document_id=doc_uuid,
                content=chunk_data["content"],
                chunk_index=chunk_data["chunk_index"],
                page_number=chunk_data["page_number"],
                embedding=embedding,
                token_count=count_tokens(chunk_data["content"]),
            )
            db.add(chunk)

        document.chunk_count = len(chunks)
        db.commit()
        logger.info("Stored %d chunks with embeddings", len(chunks))

        # Step 5: AI Analysis via Claude
        _generate_insights(db, doc_uuid, text)

        # Mark as completed
        document.status = DocumentStatus.COMPLETED
        db.commit()

        logger.info("Document %s processed successfully: %d chunks", document_id, len(chunks))
        return {
            "status": "completed",
            "document_id": document_id,
            "chunk_count": len(chunks),
            "page_count": page_count,
        }

    except Exception as exc:
        db.rollback()
        # Update document status to failed
        try:
            document = db.execute(
                select(Document).where(Document.id == doc_uuid)
            ).scalar_one_or_none()
            if document:
                document.status = DocumentStatus.FAILED
                document.error_message = str(exc)[:500]
                db.commit()
        except Exception:
            logger.exception("Failed to update document status")

        logger.exception("Error processing document %s", document_id)
        raise self.retry(exc=exc)
    finally:
        db.close()


def _generate_insights(
    db, document_id: uuid.UUID, text: str, summary_length: str = "standard",
    tone: str | None = None, focus_area: str | None = None,
):
    """Generate AI insights for a document and store them."""
    prompt = build_analysis_prompt(text, summary_length, tone=tone, focus_area=focus_area)
    result = analyze_document(prompt)
    analysis = result["analysis"]

    # Record analysis metric
    record_metric_sync(
        db, document_id, "analysis", result["processing_time_seconds"],
        result["token_usage"], "success",
    )

    # Delete existing insight if regenerating
    existing = db.execute(
        select(DocumentInsight).where(DocumentInsight.document_id == document_id)
    ).scalar_one_or_none()
    if existing:
        db.delete(existing)
        db.flush()

    insight = DocumentInsight(
        document_id=document_id,
        summary=analysis.get("summary", ""),
        key_topics=analysis.get("key_topics", []),
        entities=analysis.get("entities", {}),
        category=analysis.get("category"),
        tags=analysis.get("tags", []),
        sentiment=analysis.get("sentiment"),
        language=analysis.get("language"),
        confidence_score=analysis.get("confidence_score"),
        token_usage=result["token_usage"],
        processing_time_seconds=result["processing_time_seconds"],
    )
    db.add(insight)
    db.flush()
    logger.info("Generated insights for document %s", document_id)


@celery_app.task(bind=True, max_retries=2, default_retry_delay=30)
def regenerate_insights(
    self, document_id: str, summary_length: str = "standard",
    tone: str | None = None, focus_area: str | None = None,
):
    """Regenerate AI insights for an already-processed document."""
    db = _get_sync_session()
    doc_uuid = uuid.UUID(document_id)

    try:
        document = db.execute(
            select(Document).where(Document.id == doc_uuid)
        ).scalar_one_or_none()

        if not document:
            return {"status": "error", "message": "Document not found"}

        # Re-extract text for analysis
        with storage_service.local_path(document.file_path) as local_file:
            text, _ = extract_text(local_file, document.mime_type)
        _generate_insights(db, doc_uuid, text, summary_length, tone, focus_area)

        # Record regeneration metric
        record_metric_sync(db, doc_uuid, "regenerate", 0.0, None, "success")
        db.commit()

        return {"status": "completed", "document_id": document_id}

    except Exception as exc:
        db.rollback()
        logger.exception("Error regenerating insights for %s", document_id)
        raise self.retry(exc=exc)
    finally:
        db.close()
