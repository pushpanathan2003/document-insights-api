from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.dependencies import get_document_service
from app.models.schemas import (
    DocumentCreate,
    DocumentCreatedResponse,
    DocumentListResponse,
    DocumentResponse,
    DocumentStatus,
    ErrorResponse,
)
from app.services.document_service import DocumentService

router = APIRouter(tags=["documents"])

ServiceDependency = Annotated[DocumentService, Depends(get_document_service)]


@router.post(
    "/documents",
    response_model=DocumentCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        429: {"model": ErrorResponse, "description": "Too many active documents for this user"},
        503: {"model": ErrorResponse, "description": "A backing service is unavailable"},
    },
)
async def create_document(payload: DocumentCreate, service: ServiceDependency) -> DocumentCreatedResponse:
    return await service.submit_document(payload)


@router.get(
    "/documents/{document_id}",
    response_model=DocumentResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Malformed document id"},
        404: {"model": ErrorResponse, "description": "Document not found"},
    },
)
async def get_document(
    document_id: Annotated[str, Path(description="MongoDB ObjectId of the document")],
    service: ServiceDependency,
) -> DocumentResponse:
    return await service.get_document(document_id)


@router.get("/users/{user_id}/documents", response_model=DocumentListResponse)
async def list_user_documents(
    user_id: Annotated[str, Path(min_length=1, max_length=128)],
    service: ServiceDependency,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
    status: Annotated[DocumentStatus | None, Query()] = None,
) -> DocumentListResponse:
    return await service.list_user_documents(user_id, page, page_size, status)
