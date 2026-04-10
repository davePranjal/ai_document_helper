# Single image used for both API and Celery worker on Cloud Run.
# Set ROLE=api or ROLE=worker at deploy time.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/opt/hf_cache

# System deps for pdfplumber (needs libpoppler) and python-docx
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for layer caching
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

# Bake the embedding model into the image so cold starts don't re-download ~80MB
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# App source
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY static ./static
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

ENV PORT=8080
EXPOSE 8080

ENTRYPOINT ["./entrypoint.sh"]
