import io
import uuid
from unittest.mock import patch, MagicMock

import pytest
from httpx import AsyncClient


async def _create_completed_document(client: AsyncClient) -> str:
    """Upload a doc and manually set it to completed for chat testing."""
    content = b"This is test content about artificial intelligence and machine learning."
    files = {"file": ("chat_test.txt", io.BytesIO(content), "text/plain")}

    with patch("app.api.documents.process_document") as mock_task:
        mock_task.delay.return_value = None
        resp = await client.post("/api/v1/documents/upload", files=files)

    doc_id = resp.json()["id"]

    # Mark document as completed directly via DB
    from tests.conftest import test_session_factory
    from app.models.document import Document, DocumentStatus

    async with test_session_factory() as db:
        from sqlalchemy import select
        result = await db.execute(select(Document).where(Document.id == uuid.UUID(doc_id)))
        doc = result.scalar_one()
        doc.status = DocumentStatus.COMPLETED
        doc.chunk_count = 3
        await db.commit()

    return doc_id


@pytest.mark.asyncio
async def test_create_chat_session(client: AsyncClient):
    doc_id = await _create_completed_document(client)
    response = await client.post(
        "/api/v1/chat/sessions",
        json={"document_id": doc_id},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["document_id"] == doc_id
    assert data["message_count"] == 0


@pytest.mark.asyncio
async def test_create_session_requires_document(client: AsyncClient):
    response = await client.post("/api/v1/chat/sessions", json={})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_session_rejects_pending_document(client: AsyncClient):
    content = b"Pending doc."
    files = {"file": ("pending.txt", io.BytesIO(content), "text/plain")}

    with patch("app.api.documents.process_document") as mock_task:
        mock_task.delay.return_value = None
        resp = await client.post("/api/v1/documents/upload", files=files)

    doc_id = resp.json()["id"]
    response = await client.post(
        "/api/v1/chat/sessions",
        json={"document_id": doc_id},
    )
    assert response.status_code == 400
    assert "processed" in response.json()["detail"].lower() or "status" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_chat_sessions(client: AsyncClient):
    doc_id = await _create_completed_document(client)
    await client.post("/api/v1/chat/sessions", json={"document_id": doc_id})
    await client.post("/api/v1/chat/sessions", json={"document_id": doc_id})

    response = await client.get("/api/v1/chat/sessions")
    assert response.status_code == 200
    data = response.json()
    assert "sessions" in data
    assert data["total"] >= 2


@pytest.mark.asyncio
async def test_list_sessions_filter_by_document(client: AsyncClient):
    doc_id = await _create_completed_document(client)
    await client.post("/api/v1/chat/sessions", json={"document_id": doc_id})

    response = await client.get(f"/api/v1/chat/sessions?document_id={doc_id}")
    assert response.status_code == 200
    data = response.json()
    for session in data["sessions"]:
        assert session["document_id"] == doc_id


@pytest.mark.asyncio
async def test_get_chat_session(client: AsyncClient):
    doc_id = await _create_completed_document(client)
    create_resp = await client.post(
        "/api/v1/chat/sessions",
        json={"document_id": doc_id},
    )
    session_id = create_resp.json()["id"]

    response = await client.get(f"/api/v1/chat/sessions/{session_id}")
    assert response.status_code == 200
    assert response.json()["id"] == session_id


@pytest.mark.asyncio
async def test_get_session_not_found(client: AsyncClient):
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"/api/v1/chat/sessions/{fake_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_ask_question(client: AsyncClient):
    doc_id = await _create_completed_document(client)

    # Insert a chunk so similarity search can work
    from tests.conftest import test_session_factory
    from app.models.document import DocumentChunk

    async with test_session_factory() as db:
        chunk = DocumentChunk(
            document_id=uuid.UUID(doc_id),
            content="Artificial intelligence is the simulation of human intelligence by machines.",
            chunk_index=0,
            page_number=1,
            embedding=[0.1] * 384,
            token_count=12,
        )
        db.add(chunk)
        await db.commit()

    create_resp = await client.post(
        "/api/v1/chat/sessions",
        json={"document_id": doc_id},
    )
    session_id = create_resp.json()["id"]

    # Mock both the embedding and Claude calls
    mock_chat_result = {
        "content": 'Based on the document, AI is the simulation of human intelligence. [Page 1]\n\n```json\n{"citations": [{"snippet": "simulation of human intelligence", "page_number": 1, "chunk_index": 0}], "follow_up_suggestions": ["What are examples of AI?", "How does machine learning relate?"]}\n```',
        "token_usage": {"input_tokens": 100, "output_tokens": 50},
        "processing_time_seconds": 1.2,
    }

    with (
        patch("app.services.chat_service.generate_single_embedding", return_value=[0.1] * 384),
        patch("app.services.chat_service.chat_completion", return_value=mock_chat_result),
    ):
        response = await client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            json={"question": "What is artificial intelligence?"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "answer" in data
    assert "user_message" in data
    assert data["user_message"]["role"] == "user"
    assert data["answer"]["role"] == "assistant"
    assert data["answer"]["citations"] is not None
    assert len(data["answer"]["citations"]) > 0
    assert data["answer"]["follow_up_suggestions"] is not None


@pytest.mark.asyncio
async def test_ask_question_empty_rejected(client: AsyncClient):
    doc_id = await _create_completed_document(client)
    create_resp = await client.post(
        "/api/v1/chat/sessions",
        json={"document_id": doc_id},
    )
    session_id = create_resp.json()["id"]

    response = await client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        json={"question": "   "},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_chat_messages(client: AsyncClient):
    doc_id = await _create_completed_document(client)

    from tests.conftest import test_session_factory
    from app.models.document import DocumentChunk

    async with test_session_factory() as db:
        chunk = DocumentChunk(
            document_id=uuid.UUID(doc_id),
            content="Test content for message history.",
            chunk_index=0,
            page_number=1,
            embedding=[0.1] * 384,
            token_count=6,
        )
        db.add(chunk)
        await db.commit()

    create_resp = await client.post(
        "/api/v1/chat/sessions",
        json={"document_id": doc_id},
    )
    session_id = create_resp.json()["id"]

    mock_chat_result = {
        "content": "Here is the answer.\n\n```json\n{\"citations\": [], \"follow_up_suggestions\": []}\n```",
        "token_usage": {"input_tokens": 50, "output_tokens": 20},
        "processing_time_seconds": 0.5,
    }

    with (
        patch("app.services.chat_service.generate_single_embedding", return_value=[0.1] * 384),
        patch("app.services.chat_service.chat_completion", return_value=mock_chat_result),
    ):
        await client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            json={"question": "Test question?"},
        )

    response = await client.get(f"/api/v1/chat/sessions/{session_id}/messages")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2  # user + assistant
    roles = [m["role"] for m in data["messages"]]
    assert "user" in roles
    assert "assistant" in roles


@pytest.mark.asyncio
async def test_delete_chat_session(client: AsyncClient):
    doc_id = await _create_completed_document(client)
    create_resp = await client.post(
        "/api/v1/chat/sessions",
        json={"document_id": doc_id},
    )
    session_id = create_resp.json()["id"]

    delete_resp = await client.delete(f"/api/v1/chat/sessions/{session_id}")
    assert delete_resp.status_code == 204

    get_resp = await client.get(f"/api/v1/chat/sessions/{session_id}")
    assert get_resp.status_code == 404
