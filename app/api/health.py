import shutil

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db

router = APIRouter()


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Basic health check — fast, suitable for load balancer probes."""
    checks = {"status": "healthy", "database": "unknown", "redis": "unknown"}

    # Check database
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "connected"
    except Exception as e:
        checks["database"] = f"error: {e}"
        checks["status"] = "unhealthy"

    # Check Redis
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
        checks["redis"] = "connected"
    except Exception as e:
        checks["redis"] = f"error: {e}"
        checks["status"] = "unhealthy"

    return checks


@router.get("/health/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """Deep readiness check — verifies all dependencies are operational."""
    checks = {
        "status": "ready",
        "database": "unknown",
        "redis": "unknown",
        "celery": "unknown",
        "disk": "unknown",
    }

    # Check database with pgvector
    try:
        result = await db.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
        row = result.fetchone()
        checks["database"] = f"connected (pgvector v{row[0]})" if row else "connected (pgvector missing)"
    except Exception as e:
        checks["database"] = f"error: {e}"
        checks["status"] = "not_ready"

    # Check Redis
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url)
        info = await r.info("server")
        checks["redis"] = f"connected (v{info.get('redis_version', 'unknown')})"
        await r.aclose()
    except Exception as e:
        checks["redis"] = f"error: {e}"
        checks["status"] = "not_ready"

    # Check Celery worker availability
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.celery_broker_url)
        await r.ping()
        await r.aclose()
        checks["celery"] = "broker_reachable"
    except Exception as e:
        checks["celery"] = f"broker_error: {e}"
        checks["status"] = "not_ready"

    # Check disk space
    try:
        usage = shutil.disk_usage(settings.upload_dir)
        free_gb = usage.free / (1024 ** 3)
        checks["disk"] = f"{free_gb:.1f}GB free"
        if free_gb < 1.0:
            checks["status"] = "not_ready"
            checks["disk"] += " (LOW)"
    except Exception as e:
        checks["disk"] = f"error: {e}"

    return checks
