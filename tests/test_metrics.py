import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_document_stats(client: AsyncClient):
    response = await client.get("/api/v1/metrics/documents")
    assert response.status_code == 200
    data = response.json()
    assert "total_documents" in data
    assert "by_status" in data
    assert "by_type" in data
    assert "total_chunks" in data
    assert "total_storage_bytes" in data


@pytest.mark.asyncio
async def test_processing_stats(client: AsyncClient):
    response = await client.get("/api/v1/metrics/processing")
    assert response.status_code == 200
    data = response.json()
    assert "total_operations" in data
    assert "by_operation" in data
    assert "total_estimated_cost_usd" in data


@pytest.mark.asyncio
async def test_chat_stats(client: AsyncClient):
    response = await client.get("/api/v1/metrics/chat")
    assert response.status_code == 200
    data = response.json()
    assert "total_sessions" in data
    assert "total_messages" in data
    assert "total_chat_tokens" in data
