import io
import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient


async def _create_completed_doc_with_insight(client: AsyncClient) -> str:
    """Upload doc, mark completed, and insert an insight."""
    content = b"Document about renewable energy sources and climate change."
    files = {"file": ("insight_test.txt", io.BytesIO(content), "text/plain")}

    with patch("app.api.documents.process_document") as mock_task:
        mock_task.delay.return_value = None
        resp = await client.post("/api/v1/documents/upload", files=files)

    doc_id = resp.json()["id"]

    from tests.conftest import test_session_factory
    from app.models.document import Document, DocumentStatus
    from app.models.insight import DocumentInsight
    from sqlalchemy import select

    async with test_session_factory() as db:
        result = await db.execute(select(Document).where(Document.id == uuid.UUID(doc_id)))
        doc = result.scalar_one()
        doc.status = DocumentStatus.COMPLETED
        doc.chunk_count = 5

        insight = DocumentInsight(
            document_id=uuid.UUID(doc_id),
            summary="This document discusses renewable energy sources including solar and wind power.",
            key_topics=["renewable energy", "solar power", "wind power", "climate change"],
            entities={
                "people": [],
                "organizations": ["IPCC", "UN"],
                "locations": ["Europe"],
                "dates": ["2024"],
                "other": [],
            },
            category="report",
            tags=["energy", "climate", "sustainability", "renewable"],
            sentiment="neutral",
            language="English",
            confidence_score=0.92,
            token_usage={"input_tokens": 500, "output_tokens": 200},
            processing_time_seconds=3.5,
        )
        db.add(insight)
        await db.commit()

    return doc_id


@pytest.mark.asyncio
async def test_get_insights(client: AsyncClient):
    doc_id = await _create_completed_doc_with_insight(client)
    response = await client.get(f"/api/v1/documents/{doc_id}/insights")
    assert response.status_code == 200
    data = response.json()
    assert data["document_id"] == doc_id
    assert "renewable" in data["summary"].lower()
    assert len(data["key_topics"]) > 0
    assert data["category"] == "report"
    assert data["sentiment"] == "neutral"
    assert data["confidence_score"] == 0.92
    assert isinstance(data["entities"], dict)
    assert isinstance(data["tags"], list)


@pytest.mark.asyncio
async def test_get_insights_not_found(client: AsyncClient):
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"/api/v1/documents/{fake_id}/insights")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_insights_before_processing(client: AsyncClient):
    """Document exists but hasn't been analyzed yet."""
    content = b"No insights yet."
    files = {"file": ("no_insight.txt", io.BytesIO(content), "text/plain")}

    with patch("app.api.documents.process_document") as mock_task:
        mock_task.delay.return_value = None
        resp = await client.post("/api/v1/documents/upload", files=files)

    doc_id = resp.json()["id"]

    # Mark completed but don't add insight
    from tests.conftest import test_session_factory
    from app.models.document import Document, DocumentStatus
    from sqlalchemy import select

    async with test_session_factory() as db:
        result = await db.execute(select(Document).where(Document.id == uuid.UUID(doc_id)))
        doc = result.scalar_one()
        doc.status = DocumentStatus.COMPLETED
        await db.commit()

    response = await client.get(f"/api/v1/documents/{doc_id}/insights")
    assert response.status_code == 404
    assert "not yet generated" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_regenerate_insights(client: AsyncClient):
    doc_id = await _create_completed_doc_with_insight(client)

    with patch("app.api.insights.regenerate_insights") as mock_task:
        mock_task.delay.return_value = None
        response = await client.post(
            f"/api/v1/documents/{doc_id}/insights/regenerate",
            json={"summary_length": "brief"},
        )

    assert response.status_code == 202
    data = response.json()
    assert data["summary_length"] == "brief"
    assert data["document_id"] == doc_id


@pytest.mark.asyncio
async def test_regenerate_insights_invalid_length(client: AsyncClient):
    doc_id = await _create_completed_doc_with_insight(client)

    response = await client.post(
        f"/api/v1/documents/{doc_id}/insights/regenerate",
        json={"summary_length": "invalid"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_regenerate_insights_pending_document(client: AsyncClient):
    """Cannot regenerate insights for a document that's still processing."""
    content = b"Still pending."
    files = {"file": ("pending_regen.txt", io.BytesIO(content), "text/plain")}

    with patch("app.api.documents.process_document") as mock_task:
        mock_task.delay.return_value = None
        resp = await client.post("/api/v1/documents/upload", files=files)

    doc_id = resp.json()["id"]
    response = await client.post(
        f"/api/v1/documents/{doc_id}/insights/regenerate",
        json={"summary_length": "standard"},
    )
    assert response.status_code == 400
