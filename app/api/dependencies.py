from fastapi import Depends, Request
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis

from app.core.config import Settings
from app.repositories.document_repository import DocumentRepository
from app.services.cache_service import CacheService
from app.services.document_service import DocumentService
from app.services.rate_limit_service import RateLimitService


def get_settings_dependency(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> AsyncIOMotorDatabase:
    return request.app.state.database


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_document_service(
    database: AsyncIOMotorDatabase = Depends(get_database),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings_dependency),
) -> DocumentService:
    return DocumentService(
        repository=DocumentRepository(database),
        cache=CacheService(redis, settings.summary_cache_ttl_seconds),
        rate_limiter=RateLimitService(
            redis, settings.max_active_documents_per_user, settings.active_job_ttl_seconds
        ),
    )
