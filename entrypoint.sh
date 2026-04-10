#!/usr/bin/env bash
set -euo pipefail

ROLE="${ROLE:-api}"

case "$ROLE" in
  api)
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
    ;;
  worker)
    # threads pool: avoids fork-after-Metal issues and works fine on Cloud Run
    exec celery -A app.tasks worker --loglevel=info --pool=threads --concurrency=4
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  *)
    echo "Unknown ROLE: $ROLE (expected api|worker|migrate)" >&2
    exit 1
    ;;
esac
