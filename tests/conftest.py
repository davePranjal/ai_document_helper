import os
import tempfile

os.environ["TESTING"] = "true"
# Isolate uploads to a temp dir so tests don't pollute the dev uploads/ folder
_TEST_UPLOAD_DIR = tempfile.mkdtemp(prefix="vault_test_uploads_")
os.environ["UPLOAD_DIR"] = _TEST_UPLOAD_DIR

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.dependencies import get_db
from app.main import app

settings.upload_dir = _TEST_UPLOAD_DIR

# Use NullPool to avoid connection state leaking between tests
test_engine = create_async_engine(settings.database_url, poolclass=pool.NullPool)
test_session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with test_session_factory() as session:
        yield session


app.dependency_overrides[get_db] = override_get_db


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(scope="session", autouse=True)
def _cleanup_test_data():
    """Wipe rows and temp upload files created during the test session."""
    yield
    import asyncio
    import shutil

    async def _truncate():
        async with test_engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE chat_messages, chat_sessions, document_chunks, "
                    "document_insights, processing_metrics, documents "
                    "RESTART IDENTITY CASCADE"
                )
            )
        await test_engine.dispose()

    try:
        asyncio.run(_truncate())
    except Exception:
        pass
    shutil.rmtree(_TEST_UPLOAD_DIR, ignore_errors=True)
