import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OperationType(str, enum.Enum):
    EMBEDDING = "embedding"
    ANALYSIS = "analysis"
    CHAT = "chat"
    REGENERATE = "regenerate"


class ProcessingMetric(Base):
    __tablename__ = "processing_metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    operation: Mapped[OperationType] = mapped_column(Enum(OperationType), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    token_usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="success")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
