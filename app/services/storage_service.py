"""Storage abstraction supporting local filesystem and Google Cloud Storage.

Files are stored under an opaque "URI" returned by `save_file`. For the local
backend the URI is just the absolute path; for GCS it is `gs://bucket/key`.
Callers should treat it as opaque and use this module to read/delete.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from app.config import settings


def _is_gcs(uri: str) -> bool:
    return uri.startswith("gs://")


def _parse_gcs(uri: str) -> tuple[str, str]:
    # gs://bucket/key/path
    without_scheme = uri[len("gs://") :]
    bucket, _, key = without_scheme.partition("/")
    return bucket, key


def save_file(content: bytes, extension: str) -> str:
    """Persist bytes and return a storage URI."""
    unique_name = f"{uuid.uuid4().hex}.{extension}"

    if settings.storage_backend == "gcs":
        if not settings.gcs_bucket:
            raise RuntimeError("GCS_BUCKET must be set when STORAGE_BACKEND=gcs")
        from google.cloud import storage  # imported lazily

        client = storage.Client()
        bucket = client.bucket(settings.gcs_bucket)
        blob = bucket.blob(unique_name)
        blob.upload_from_string(content)
        return f"gs://{settings.gcs_bucket}/{unique_name}"

    # local
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    path = os.path.join(settings.upload_dir, unique_name)
    with open(path, "wb") as f:
        f.write(content)
    return path


def delete_file(uri: str) -> None:
    if _is_gcs(uri):
        from google.cloud import storage

        bucket_name, key = _parse_gcs(uri)
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(key)
        if blob.exists():
            blob.delete()
        return

    p = Path(uri)
    if p.exists():
        p.unlink()


@contextmanager
def local_path(uri: str):
    """Yield a local filesystem path for the file.

    For local storage this is the URI itself. For GCS we download to a temp
    file and clean up afterwards. This lets pdfplumber/python-docx — which
    require real file paths — work transparently against either backend.
    """
    if not _is_gcs(uri):
        yield uri
        return

    from google.cloud import storage

    bucket_name, key = _parse_gcs(uri)
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(key)

    suffix = Path(key).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        blob.download_to_filename(tmp.name)
        try:
            yield tmp.name
        finally:
            os.unlink(tmp.name)
