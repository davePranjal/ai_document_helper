from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://vault:vault_secret@localhost:5432/vault_documents"
    database_url_sync: str = (
        "postgresql+psycopg2://vault:vault_secret@localhost:5432/vault_documents"
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"

    # AI API Keys
    anthropic_api_key: str = ""

    # Model Configuration
    claude_model: str = "claude-sonnet-4-20250514"
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_dimensions: int = 384

    # Storage
    storage_backend: str = "local"  # "local" or "gcs"
    gcs_bucket: str = ""  # required when storage_backend=gcs

    # App Configuration
    upload_dir: str = "uploads"
    max_file_size_mb: int = 50
    allowed_extensions: str = "pdf,docx,txt"
    chunk_size: int = 1000
    chunk_overlap: int = 200

    # Logging
    log_level: str = "INFO"

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def allowed_extensions_list(self) -> list[str]:
        return [ext.strip() for ext in self.allowed_extensions.split(",")]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
