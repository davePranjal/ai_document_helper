from celery import Celery

from app.config import settings

celery_app = Celery(
    "vault_documents",
    broker=settings.celery_broker_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Autodiscover task modules
celery_app.autodiscover_tasks(["app.tasks"])
import app.tasks.document_tasks  # noqa: E402,F401
