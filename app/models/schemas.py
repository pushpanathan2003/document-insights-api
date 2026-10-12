from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class DocumentStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class DocumentCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.@-]+$")
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100_000)


class DocumentCreatedResponse(BaseModel):
    document_id: str
    status: DocumentStatus
    summary: str | None = None
    cached: bool = False


class DocumentResponse(BaseModel):
    document_id: str
    user_id: str
    title: str
    status: DocumentStatus
    summary: str | None = None
    error: str | None = None
    cached: bool = False
    content_hash: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class DocumentListItem(BaseModel):
    document_id: str
    title: str
    status: DocumentStatus
    summary: str | None = None
    created_at: datetime
    updated_at: datetime


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int


class DocumentListResponse(BaseModel):
    user_id: str
    documents: list[DocumentListItem]
    pagination: PaginationMeta


class HealthResponse(BaseModel):
    status: str
    mongodb: str
    redis: str


class ErrorResponse(BaseModel):
    detail: str
