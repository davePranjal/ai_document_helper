import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class InsightResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    summary: str
    key_topics: list[str]
    entities: dict
    category: str | None = None
    tags: list[str]
    sentiment: str | None = None
    language: str | None = None
    confidence_score: float | None = None
    token_usage: dict | None = None
    processing_time_seconds: float | None = None
    created_at: datetime
    updated_at: datetime


class RegenerateInsightRequest(BaseModel):
    summary_length: str = "standard"  # brief, standard, detailed
    tone: str | None = None  # professional, academic, casual, technical
    focus_area: str | None = None  # free-text focus area, e.g. "financial risks"


class CompareDocumentsRequest(BaseModel):
    document_ids: list[uuid.UUID]  # 2 or more document IDs


class UniqueInsight(BaseModel):
    document: str
    insight: str


class ComparisonResponse(BaseModel):
    overview: str
    similarities: list[str]
    differences: list[str]
    unique_insights: list[UniqueInsight]
    relationships: str
    document_ids: list[uuid.UUID]
    token_usage: dict | None = None
    processing_time_seconds: float | None = None
