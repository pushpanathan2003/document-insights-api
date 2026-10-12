from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Document Insights API"
    log_level: str = "INFO"

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "document_insights"
    mongodb_timeout_ms: int = Field(default=3000, ge=100)

    redis_url: str = "redis://localhost:6379/0"

    max_active_documents_per_user: int = Field(default=3, ge=1)
    active_job_ttl_seconds: int = Field(default=900, ge=60)

    summary_cache_ttl_seconds: int = Field(default=3600, ge=1)

    worker_poll_interval_seconds: float = Field(default=1.0, gt=0)
    worker_min_processing_seconds: float = Field(default=10.0, ge=0)
    worker_max_processing_seconds: float = Field(default=30.0, ge=0)
    worker_failure_rate: float = Field(default=0.1, ge=0, le=1)
    worker_lock_timeout_seconds: int = Field(default=120, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
