import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import documents, health
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    DocumentNotFoundError,
    InvalidDocumentIdError,
    RateLimitExceededError,
    ServiceUnavailableError,
)
from app.core.logging import configure_logging
from app.db.mongodb import create_mongo_client, ensure_indexes
from app.db.redis import create_redis_client

logger = logging.getLogger(__name__)


def _error(status_code: int, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail}, headers=headers)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    mongo_client = create_mongo_client(settings)
    redis = create_redis_client(settings)
    app.state.database = mongo_client[settings.mongodb_database]
    app.state.redis = redis
    try:
        await ensure_indexes(app.state.database)
    except Exception:
        logger.error("Could not create MongoDB indexes at startup", exc_info=True)
    logger.info("API started")
    try:
        yield
    finally:
        await redis.aclose()
        mongo_client.close()
        logger.info("API stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    app = FastAPI(title=resolved_settings.app_name, version="1.0.0", lifespan=lifespan)
    app.state.settings = resolved_settings

    app.include_router(health.router)
    app.include_router(documents.router)

    @app.exception_handler(DocumentNotFoundError)
    async def handle_not_found(_: Request, exc: DocumentNotFoundError) -> JSONResponse:
        return _error(404, str(exc))

    @app.exception_handler(InvalidDocumentIdError)
    async def handle_invalid_id(_: Request, exc: InvalidDocumentIdError) -> JSONResponse:
        return _error(400, str(exc))

    @app.exception_handler(RateLimitExceededError)
    async def handle_rate_limit(_: Request, exc: RateLimitExceededError) -> JSONResponse:
        return _error(429, str(exc), headers={"Retry-After": "30"})

    @app.exception_handler(ServiceUnavailableError)
    async def handle_unavailable(_: Request, exc: ServiceUnavailableError) -> JSONResponse:
        return _error(503, str(exc))

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        messages = [
            f"{'.'.join(str(part) for part in error['loc'][1:]) or error['loc'][0]}: {error['msg']}"
            for error in exc.errors()
        ]
        return _error(422, "; ".join(messages))

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled exception", exc_info=exc)
        return _error(500, "An unexpected error occurred")

    return app


app = create_app()
