import hashlib
import json
import os

import structlog
from redis.asyncio import Redis

from app.config import settings

logger = structlog.get_logger(__name__)

# Disable caching in test mode
_enabled = os.environ.get("TESTING", "").lower() != "true"

_redis: Redis | None = None


async def _get_redis() -> Redis | None:
    if not _enabled:
        return None
    global _redis
    if _redis is None:
        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _embedding_cache_key(text: str) -> str:
    text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    return f"emb:{text_hash}"


async def get_cached_embedding(text: str) -> list[float] | None:
    redis = await _get_redis()
    if not redis:
        return None
    try:
        data = await redis.get(_embedding_cache_key(text))
        if data:
            logger.debug("Cache hit for query embedding")
            return json.loads(data)
    except Exception:
        logger.warning("Redis cache read failed for embedding")
    return None


async def set_cached_embedding(text: str, embedding: list[float], ttl: int = 3600) -> None:
    redis = await _get_redis()
    if not redis:
        return
    try:
        await redis.set(_embedding_cache_key(text), json.dumps(embedding), ex=ttl)
    except Exception:
        logger.warning("Redis cache write failed for embedding")


async def get_cached_insights(document_id: str) -> dict | None:
    redis = await _get_redis()
    if not redis:
        return None
    try:
        data = await redis.get(f"insights:{document_id}")
        if data:
            logger.debug("Cache hit for document insights", document_id=document_id)
            return json.loads(data)
    except Exception:
        logger.warning("Redis cache read failed for insights")
    return None


async def set_cached_insights(document_id: str, insights: dict, ttl: int = 86400) -> None:
    redis = await _get_redis()
    if not redis:
        return
    try:
        await redis.set(f"insights:{document_id}", json.dumps(insights, default=str), ex=ttl)
    except Exception:
        logger.warning("Redis cache write failed for insights")


async def invalidate_insights(document_id: str) -> None:
    redis = await _get_redis()
    if not redis:
        return
    try:
        await redis.delete(f"insights:{document_id}")
    except Exception:
        logger.warning("Redis cache invalidation failed for insights")


async def get_cached_metrics(key: str) -> dict | None:
    redis = await _get_redis()
    if not redis:
        return None
    try:
        data = await redis.get(f"metrics:{key}")
        if data:
            return json.loads(data)
    except Exception:
        pass
    return None


async def set_cached_metrics(key: str, data: dict, ttl: int = 60) -> None:
    redis = await _get_redis()
    if not redis:
        return
    try:
        await redis.set(f"metrics:{key}", json.dumps(data, default=str), ex=ttl)
    except Exception:
        pass
