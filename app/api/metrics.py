from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.schemas.metrics import ChatStats, DocumentStats, ProcessingStats
from app.services import metrics_service
from app.services.cache_service import get_cached_metrics, set_cached_metrics

router = APIRouter()


@router.get("/metrics/documents", response_model=DocumentStats)
async def get_document_stats(db: AsyncSession = Depends(get_db)):
    """Get document statistics: counts by status/type, total storage."""
    cached = await get_cached_metrics("documents")
    if cached:
        return DocumentStats(**cached)
    data = await metrics_service.get_document_stats(db)
    await set_cached_metrics("documents", data, ttl=60)
    return data


@router.get("/metrics/processing", response_model=ProcessingStats)
async def get_processing_stats(db: AsyncSession = Depends(get_db)):
    """Get processing metrics: average times, success rates, token usage, costs."""
    cached = await get_cached_metrics("processing")
    if cached:
        return ProcessingStats(**cached)
    data = await metrics_service.get_processing_stats(db)
    await set_cached_metrics("processing", data, ttl=60)
    return data


@router.get("/metrics/chat", response_model=ChatStats)
async def get_chat_stats(db: AsyncSession = Depends(get_db)):
    """Get chat usage statistics: session/message counts, response times."""
    cached = await get_cached_metrics("chat")
    if cached:
        return ChatStats(**cached)
    data = await metrics_service.get_chat_stats(db)
    await set_cached_metrics("chat", data, ttl=60)
    return data
