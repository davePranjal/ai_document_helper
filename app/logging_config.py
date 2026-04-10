import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog

from app.config import settings


def setup_logging():
    """Configure structlog for structured JSON logging with request ID support."""
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer()
            if settings.log_level == "DEBUG"
            else structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    # Detailed JSON file log (always JSON, regardless of console format)
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    file_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )
    file_handler = RotatingFileHandler(
        log_dir / "app.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(stream_handler)
    root.addHandler(file_handler)
    root.setLevel(settings.log_level)

    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("celery").setLevel(logging.INFO)
