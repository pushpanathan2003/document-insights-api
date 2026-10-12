import logging
from math import ceil

from bson import ObjectId
from pymongo.errors import PyMongoError

from app.core.exceptions import (
    DocumentNotFoundError,
    RateLimitExceededError,
    ServiceUnavailableError,
)
from app.models.schemas import (
    DocumentCreate,
    DocumentCreatedResponse,
    DocumentListItem,
    DocumentListResponse,
    DocumentResponse,
    DocumentStatus,
    PaginationMeta,
)
from app.repositories.document_repository import DocumentRepository
from app.services.cache_service import CacheService, compute_content_hash
from app.services.rate_limit_service import RateLimitService

logger = logging.getLogger(__name__)


class DocumentService:
    def __init__(
        self,
        repository: DocumentRepository,
        cache: CacheService,
        rate_limiter: RateLimitService,
    ) -> None:
        self._repository = repository
        self._cache = cache
        self._rate_limiter = rate_limiter

    async def submit_document(self, payload: DocumentCreate) -> DocumentCreatedResponse:
        content_hash = compute_content_hash(payload.content)
        document_id = ObjectId()

        cached_summary = await self._cache.get_summary(content_hash)
        if cached_summary is not None:
            await self._insert(
                document_id,
                payload,
                content_hash,
                DocumentStatus.COMPLETED,
                summary=cached_summary,
                cached=True,
            )
            logger.info(
                "Served summary from cache",
                extra={"document_id": str(document_id), "user_id": payload.user_id},
            )
            return DocumentCreatedResponse(
                document_id=str(document_id),
                status=DocumentStatus.COMPLETED,
                summary=cached_summary,
                cached=True,
            )

        acquired = await self._rate_limiter.try_acquire(payload.user_id, str(document_id))
        if not acquired:
            logger.info("Rate limit rejected submission", extra={"user_id": payload.user_id})
            raise RateLimitExceededError(payload.user_id, self._rate_limiter.limit)

        try:
            await self._insert(document_id, payload, content_hash, DocumentStatus.QUEUED)
        except Exception:
            await self._rate_limiter.release(payload.user_id, str(document_id))
            raise

        logger.info(
            "Document queued",
            extra={"document_id": str(document_id), "user_id": payload.user_id},
        )
        return DocumentCreatedResponse(document_id=str(document_id), status=DocumentStatus.QUEUED)

    async def get_document(self, document_id: str) -> DocumentResponse:
        try:
            record = await self._repository.get(document_id)
        except PyMongoError as exc:
            logger.error("Document lookup failed", exc_info=True)
            raise ServiceUnavailableError("MongoDB") from exc
        if record is None:
            raise DocumentNotFoundError(document_id)
        return DocumentResponse(**record)

    async def list_user_documents(
        self, user_id: str, page: int, page_size: int, status: DocumentStatus | None
    ) -> DocumentListResponse:
        try:
            records, total = await self._repository.list_for_user(user_id, page, page_size, status)
        except PyMongoError as exc:
            logger.error("Document listing failed", exc_info=True, extra={"user_id": user_id})
            raise ServiceUnavailableError("MongoDB") from exc
        return DocumentListResponse(
            user_id=user_id,
            documents=[DocumentListItem(**record) for record in records],
            pagination=PaginationMeta(
                page=page,
                page_size=page_size,
                total_items=total,
                total_pages=ceil(total / page_size) if total else 0,
            ),
        )

    async def _insert(
        self,
        document_id: ObjectId,
        payload: DocumentCreate,
        content_hash: str,
        status: DocumentStatus,
        summary: str | None = None,
        cached: bool = False,
    ) -> None:
        try:
            await self._repository.insert(
                document_id=document_id,
                user_id=payload.user_id,
                title=payload.title,
                content=payload.content,
                content_hash=content_hash,
                status=status,
                summary=summary,
                cached=cached,
            )
        except PyMongoError as exc:
            logger.error("Document insert failed", exc_info=True, extra={"user_id": payload.user_id})
            raise ServiceUnavailableError("MongoDB") from exc
