import uuid

import structlog
from sqlalchemy import case, cast, func, select, Integer
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatSession, MessageRole
from app.models.document import Document, DocumentChunk
from app.models.metrics import OperationType, ProcessingMetric

logger = structlog.get_logger(__name__)

# Cost per token estimates (USD) — update as pricing changes
COST_PER_TOKEN = {
    "claude_input": 3.0 / 1_000_000,    # $3 per 1M input tokens
    "claude_output": 15.0 / 1_000_000,   # $15 per 1M output tokens
    "embedding": 0.0,                    # Free — local model (sentence-transformers)
}


def estimate_cost(operation: str, token_usage: dict | None) -> float:
    """Estimate USD cost for an AI operation based on token usage."""
    if not token_usage:
        return 0.0

    if operation in ("analysis", "chat", "regenerate"):
        input_cost = token_usage.get("input_tokens", 0) * COST_PER_TOKEN["claude_input"]
        output_cost = token_usage.get("output_tokens", 0) * COST_PER_TOKEN["claude_output"]
        return round(input_cost + output_cost, 6)
    elif operation == "embedding":
        total_tokens = token_usage.get("total_tokens", 0)
        return round(total_tokens * COST_PER_TOKEN["embedding"], 6)

    return 0.0


def record_metric_sync(db, document_id: uuid.UUID | None, operation: str,
                       duration: float, token_usage: dict | None, status: str = "success"):
    """Record a processing metric (synchronous version for Celery tasks)."""
    cost = estimate_cost(operation, token_usage)
    metric = ProcessingMetric(
        document_id=document_id,
        operation=OperationType(operation),
        duration_seconds=round(duration, 3),
        token_usage=token_usage,
        estimated_cost_usd=cost,
        status=status,
    )
    db.add(metric)


async def record_metric(db: AsyncSession, document_id: uuid.UUID | None, operation: str,
                        duration: float, token_usage: dict | None, status: str = "success"):
    """Record a processing metric (async version for FastAPI)."""
    cost = estimate_cost(operation, token_usage)
    metric = ProcessingMetric(
        document_id=document_id,
        operation=OperationType(operation),
        duration_seconds=round(duration, 3),
        token_usage=token_usage,
        estimated_cost_usd=cost,
        status=status,
    )
    db.add(metric)
    await db.flush()


async def get_document_stats(db: AsyncSession) -> dict:
    """Get document statistics."""
    # Total and by status
    status_result = await db.execute(
        select(Document.status, func.count()).group_by(Document.status)
    )
    by_status = {row[0].value: row[1] for row in status_result.fetchall()}
    total = sum(by_status.values())

    # By mime type
    type_result = await db.execute(
        select(Document.mime_type, func.count()).group_by(Document.mime_type)
    )
    by_type = {row[0]: row[1] for row in type_result.fetchall()}

    # Total chunks
    chunk_count = await db.execute(select(func.count()).select_from(DocumentChunk))
    total_chunks = chunk_count.scalar_one()

    # Total storage
    storage_result = await db.execute(select(func.coalesce(func.sum(Document.file_size), 0)))
    total_storage = storage_result.scalar_one()

    return {
        "total_documents": total,
        "by_status": by_status,
        "by_type": by_type,
        "total_chunks": total_chunks,
        "total_storage_bytes": total_storage,
    }


async def get_processing_stats(db: AsyncSession) -> dict:
    """Get processing metrics statistics."""
    # All stats in a single query to avoid connection issues
    result = await db.execute(
        select(
            func.count(),
            func.avg(ProcessingMetric.duration_seconds),
            func.coalesce(func.sum(ProcessingMetric.estimated_cost_usd), 0.0),
            func.count().filter(ProcessingMetric.status == "success"),
        )
    )
    row = result.fetchone()
    total = row[0]
    avg_duration = row[1]
    total_cost = row[2]
    success_count = row[3]

    # By operation type
    op_result = await db.execute(
        select(ProcessingMetric.operation, func.count())
        .group_by(ProcessingMetric.operation)
    )
    by_operation = {r[0].value: r[1] for r in op_result.fetchall()}

    success_rate = round(success_count / total, 4) if total > 0 else None

    return {
        "total_operations": total,
        "by_operation": by_operation,
        "avg_duration_seconds": round(float(avg_duration), 3) if avg_duration else None,
        "total_input_tokens": 0,  # Simplified — avoid JSONB extraction in ORM
        "total_output_tokens": 0,
        "total_estimated_cost_usd": round(float(total_cost), 6),
        "success_rate": success_rate,
    }


async def get_chat_stats(db: AsyncSession) -> dict:
    """Get chat usage statistics."""
    result = await db.execute(
        select(func.count()).select_from(ChatSession)
    )
    total_sessions = result.scalar_one()

    msg_result = await db.execute(
        select(
            func.count(),
            func.avg(
                case(
                    (ChatMessage.response_time_seconds.is_not(None), ChatMessage.response_time_seconds),
                )
            ),
        ).select_from(ChatMessage)
    )
    msg_row = msg_result.fetchone()
    total_messages = msg_row[0]
    avg_response_time = msg_row[1]

    return {
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "avg_response_time_seconds": round(float(avg_response_time), 3) if avg_response_time else None,
        "total_chat_tokens": 0,  # Simplified — avoid JSONB extraction in ORM
    }
