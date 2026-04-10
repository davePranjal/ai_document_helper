import io
from unittest.mock import patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_documents_empty(client: AsyncClient):
    response = await client.get("/api/v1/documents")
    assert response.status_code == 200
    data = response.json()
    assert "documents" in data
    assert "total" in data
    assert data["page"] == 1


@pytest.mark.asyncio
async def test_upload_document_txt(client: AsyncClient):
    content = b"This is a test document with some content for testing purposes."
    files = {"file": ("test.txt", io.BytesIO(content), "text/plain")}

    with patch("app.api.documents.process_document") as mock_task:
        mock_task.delay.return_value = None
        response = await client.post("/api/v1/documents/upload", files=files)

    assert response.status_code == 202
    data = response.json()
    assert data["filename"] == "test.txt"
    assert data["status"] == "pending"
    assert "id" in data


@pytest.mark.asyncio
async def test_upload_invalid_extension(client: AsyncClient):
    content = b"invalid content"
    files = {"file": ("test.exe", io.BytesIO(content), "application/octet-stream")}

    response = await client.post("/api/v1/documents/upload", files=files)
    assert response.status_code == 422
    assert "not allowed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_document_not_found(client: AsyncClient):
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"/api/v1/documents/{fake_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_upload_and_get_document(client: AsyncClient):
    content = b"Test document content."
    files = {"file": ("gettest.txt", io.BytesIO(content), "text/plain")}

    with patch("app.api.documents.process_document") as mock_task:
        mock_task.delay.return_value = None
        upload_resp = await client.post("/api/v1/documents/upload", files=files)

    doc_id = upload_resp.json()["id"]
    response = await client.get(f"/api/v1/documents/{doc_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["original_filename"] == "gettest.txt"
    assert data["status"] == "pending"
    assert data["file_size"] == len(content)


@pytest.mark.asyncio
async def test_delete_document(client: AsyncClient):
    content = b"To be deleted."
    files = {"file": ("delete_me.txt", io.BytesIO(content), "text/plain")}

    with patch("app.api.documents.process_document") as mock_task:
        mock_task.delay.return_value = None
        upload_resp = await client.post("/api/v1/documents/upload", files=files)

    doc_id = upload_resp.json()["id"]
    delete_resp = await client.delete(f"/api/v1/documents/{doc_id}")
    assert delete_resp.status_code == 204

    # Verify it's gone
    get_resp = await client.get(f"/api/v1/documents/{doc_id}")
    assert get_resp.status_code == 404
