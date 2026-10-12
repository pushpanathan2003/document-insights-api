import asyncio
import logging
import random
import signal
import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.mongodb import create_mongo_client, ensure_indexes
from app.db.redis import create_redis_client
from app.repositories.document_repository import DocumentRecord, DocumentRepository
from app.services.cache_service import CacheService
from app.services.rate_limit_service import RateLimitService
from app.workers.summarizer import generate_summary

logger = logging.getLogger(__name__)

SIMULATED_FAILURE_MESSAGE = "Summarization failed: the AI provider returned an error"
UNEXPECTED_FAILURE_MESSAGE = "Summarization failed due to an unexpected processing error"


class RandomSource(Protocol):
    def uniform(self, low: float, high: float) -> float: ...

    def random(self) -> float: ...


class SimulatedSummarizationError(Exception):
    pass


class DocumentWorker:
    def __init__(
        self,
        repository: DocumentRepository,
        cache: CacheService,
        rate_limiter: RateLimitService,
        settings: Settings,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: RandomSource | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._repository = repository
        self._cache = cache
        self._rate_limiter = rate_limiter
        self._settings = settings
        self._sleep = sleep
        self._rng = rng or random.Random()
        self.worker_id = worker_id or uuid.uuid4().hex

    async def process_next(self) -> bool:
        document = await self._repository.claim_next(
            self.worker_id, self._settings.worker_lock_timeout_seconds
        )
        if document is None:
            return False
        await self._process(document)
        return True

    async def run(self, stop_event: asyncio.Event) -> None:
        logger.info("Worker started", extra={"worker_id": self.worker_id})
        while not stop_event.is_set():
            try:
                processed = await self.process_next()
            except Exception:
                logger.exception("Worker loop iteration failed", extra={"worker_id": self.worker_id})
                processed = False
            if not processed:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=self._settings.worker_poll_interval_seconds
                    )
                except asyncio.TimeoutError:
                    pass
        logger.info("Worker stopped", extra={"worker_id": self.worker_id})

    async def _process(self, document: DocumentRecord) -> None:
        document_id = document["document_id"]
        user_id = document["user_id"]
        context = {"document_id": document_id, "user_id": user_id, "worker_id": self.worker_id}
        logger.info("Processing started", extra=context)
        try:
            summary = await self._summarize(document)
            completed = await self._repository.mark_completed(document_id, self.worker_id, summary)
            if completed:
                await self._cache.store_summary(document["content_hash"], summary)
                logger.info("Processing completed", extra=context)
            else:
                logger.warning("Document was no longer owned by this worker on completion", extra=context)
        except SimulatedSummarizationError:
            logger.error("Processing failed", extra=context)
            await self._record_failure(document_id, SIMULATED_FAILURE_MESSAGE, context)
        except Exception:
            logger.exception("Unexpected error while processing document", extra=context)
            await self._record_failure(document_id, UNEXPECTED_FAILURE_MESSAGE, context)
        finally:
            await self._rate_limiter.release(user_id, document_id)

    async def _summarize(self, document: DocumentRecord) -> str:
        delay = self._rng.uniform(
            self._settings.worker_min_processing_seconds,
            self._settings.worker_max_processing_seconds,
        )
        await self._sleep(delay)
        if self._rng.random() < self._settings.worker_failure_rate:
            raise SimulatedSummarizationError()
        return generate_summary(document["title"], document["content"])

    async def _record_failure(self, document_id: str, message: str, context: dict[str, str]) -> None:
        try:
            await self._repository.mark_failed(document_id, self.worker_id, message)
        except Exception:
            logger.exception("Could not persist failed status", extra=context)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    mongo_client = create_mongo_client(settings)
    redis = create_redis_client(settings)
    database = mongo_client[settings.mongodb_database]
    await ensure_indexes(database)

    worker = DocumentWorker(
        repository=DocumentRepository(database),
        cache=CacheService(redis, settings.summary_cache_ttl_seconds),
        rate_limiter=RateLimitService(
            redis, settings.max_active_documents_per_user, settings.active_job_ttl_seconds
        ),
        settings=settings,
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        await worker.run(stop_event)
    finally:
        await redis.aclose()
        mongo_client.close()


if __name__ == "__main__":
    asyncio.run(main())
