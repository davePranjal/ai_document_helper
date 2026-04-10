from pydantic import BaseModel


class DocumentStats(BaseModel):
    total_documents: int
    by_status: dict[str, int]
    by_type: dict[str, int]
    total_chunks: int
    total_storage_bytes: int


class ProcessingStats(BaseModel):
    total_operations: int
    by_operation: dict[str, int]
    avg_duration_seconds: float | None
    total_input_tokens: int
    total_output_tokens: int
    total_estimated_cost_usd: float
    success_rate: float | None


class ChatStats(BaseModel):
    total_sessions: int
    total_messages: int
    avg_response_time_seconds: float | None
    total_chat_tokens: int
