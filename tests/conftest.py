from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest
from fakeredis import FakeAsyncRedis
from mongomock_motor import AsyncMongoMockClient

from app.core.config import Settings
from app.main import create_app
from app.repositories.document_repository import DocumentRepository
from app.services.cache_service import CacheService
from app.services.rate_limit_service import RateLimitService
from app.workers.document_worker import DocumentWorker


class StubRandom:
    def __init__(self, roll: float = 0.99, delay: float = 15.0) -> None:
        self.roll = roll
        self.delay = delay
        self.uniform_calls: list[tuple[float, float]] = []

    def uniform(self, low: float, high: float) -> float:
        self.uniform_calls.append((low, high))
        return self.delay

    def random(self) -> float:
        return self.roll


@pytest.fixture
def settings() -> Settings:
    return Settings(
        mongodb_database="document_insights_test",
        max_active_documents_per_user=3,
        summary_cache_ttl_seconds=600,
        worker_failure_rate=0.1,
    )


@pytest.fixture
async def redis() -> AsyncIterator[FakeAsyncRedis]:
    client = FakeAsyncRedis(decode_responses=True)
    yield client
    await client.flushall()
    await client.aclose()


@pytest.fixture
def database(settings: Settings):
    client = AsyncMongoMockClient(tz_aware=True)
    return client[settings.mongodb_database]


@pytest.fixture
def repository(database) -> DocumentRepository:
    return DocumentRepository(database)


@pytest.fixture
def cache(redis, settings: Settings) -> CacheService:
    return CacheService(redis, settings.summary_cache_ttl_seconds)


@pytest.fixture
def rate_limiter(redis, settings: Settings) -> RateLimitService:
    return RateLimitService(
        redis, settings.max_active_documents_per_user, settings.active_job_ttl_seconds
    )


@pytest.fixture
def sleep_mock() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def stub_random() -> StubRandom:
    return StubRandom()


@pytest.fixture
def make_worker(repository, cache, rate_limiter, settings, sleep_mock, stub_random):
    def factory(rng=None, worker_id: str | None = None) -> DocumentWorker:
        return DocumentWorker(
            repository=repository,
            cache=cache,
            rate_limiter=rate_limiter,
            settings=settings,
            sleep=sleep_mock,
            rng=rng or stub_random,
            worker_id=worker_id,
        )

    return factory


@pytest.fixture
def worker(make_worker) -> DocumentWorker:
    return make_worker()


@pytest.fixture
async def client(settings, database, redis) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    app.state.database = database
    app.state.redis = redis
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.fixture
def make_payload():
    counter = {"value": 0}

    def factory(user_id: str = "user-1", title: str | None = None, content: str | None = None) -> dict:
        counter["value"] += 1
        number = counter["value"]
        return {
            "user_id": user_id,
            "title": title or f"Report {number}",
            "content": content or f"Quarterly revenue grew steadily in region {number}. Costs stayed flat.",
        }

    return factory

