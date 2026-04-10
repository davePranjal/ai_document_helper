import json
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db
from app.models.insight import DocumentInsight
from app.prompts.comparison import build_comparison_prompt
from app.schemas.insight import (
    CompareDocumentsRequest,
    ComparisonResponse,
    InsightResponse,
    RegenerateInsightRequest,
)
from app.services.ai_service import chat_completion
from app.services.cache_service import (
    get_cached_insights,
    invalidate_insights,
    set_cached_insights,
)
from app.services.document_service import get_document
from app.tasks.document_tasks import regenerate_insights

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/documents/{document_id}/insights", response_model=InsightResponse)
async def get_document_insights(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get AI-generated insights for a document."""
    # Verify document exists
    await get_document(db, document_id)

    # Check cache first
    cached = await get_cached_insights(str(document_id))
    if cached:
        return InsightResponse(**cached)

    result = await db.execute(
        select(DocumentInsight).where(DocumentInsight.document_id == document_id)
    )
    insight = result.scalar_one_or_none()

    if not insight:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Insights not yet generated. Document may still be processing.",
        )

    response = InsightResponse.model_validate(insight)
    await set_cached_insights(str(document_id), response.model_dump(mode="json"))
    return response


@router.post("/documents/{document_id}/insights/regenerate", status_code=202)
async def regenerate_document_insights(
    document_id: uuid.UUID,
    request: RegenerateInsightRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Regenerate AI insights for a document with optional customization."""
    document = await get_document(db, document_id)

    if document.status.value != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Document must be in 'completed' status. Current: {document.status.value}",
        )

    summary_length = request.summary_length if request else "standard"
    tone = request.tone if request else None
    focus_area = request.focus_area if request else None

    if summary_length not in ("brief", "standard", "detailed"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="summary_length must be one of: brief, standard, detailed",
        )
    if tone and tone not in ("professional", "academic", "casual", "technical"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="tone must be one of: professional, academic, casual, technical",
        )

    # Invalidate cached insights before regeneration
    await invalidate_insights(str(document_id))

    regenerate_insights.delay(str(document_id), summary_length, tone, focus_area)

    return {
        "message": "Insight regeneration started",
        "document_id": str(document_id),
        "summary_length": summary_length,
        "tone": tone,
        "focus_area": focus_area,
    }


@router.post("/documents/compare", response_model=ComparisonResponse)
async def compare_documents(
    request: CompareDocumentsRequest,
    db: AsyncSession = Depends(get_db),
):
    """Compare two or more documents using their AI-generated insights.

    All documents must have completed analysis (insights generated).
    Returns similarities, differences, unique insights, and relationships.
    """
    if len(request.document_ids) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least 2 document IDs are required for comparison.",
        )

    # Gather insights for all documents
    doc_data = []
    for doc_id in request.document_ids:
        document = await get_document(db, doc_id)
        result = await db.execute(
            select(DocumentInsight).where(DocumentInsight.document_id == doc_id)
        )
        insight = result.scalar_one_or_none()
        if not insight:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Document {doc_id} does not have insights yet. Process it first.",
            )
        doc_data.append({
            "name": document.original_filename,
            "summary": insight.summary,
            "key_topics": insight.key_topics or [],
            "category": insight.category,
            "sentiment": insight.sentiment,
        })

    # Build comparison prompt and call Claude
    prompt = build_comparison_prompt(doc_data)
    result = chat_completion(
        system_prompt="You are a document analysis expert. Compare documents and return structured JSON.",
        messages=[{"role": "user", "content": prompt}],
    )

    # Parse JSON response
    response_text = result["content"].strip()
    if response_text.startswith("```"):
        response_text = response_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        comparison = json.loads(response_text)
    except json.JSONDecodeError:
        logger.error("Failed to parse comparison response", response=response_text[:200])
        comparison = {
            "overview": response_text,
            "similarities": [],
            "differences": [],
            "unique_insights": [],
            "relationships": "Could not parse structured comparison.",
        }

    return ComparisonResponse(
        overview=comparison.get("overview", ""),
        similarities=comparison.get("similarities", []),
        differences=comparison.get("differences", []),
        unique_insights=[
            {"document": u.get("document", ""), "insight": u.get("insight", "")}
            for u in comparison.get("unique_insights", [])
        ],
        relationships=comparison.get("relationships", ""),
        document_ids=request.document_ids,
        token_usage=result["token_usage"],
        processing_time_seconds=result["processing_time_seconds"],
    )
