import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from redis.asyncio import Redis

from app.api.dependencies import get_database, get_redis
from app.models.schemas import HealthResponse

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health", response_model=HealthResponse)
async def health_check(
    database: AsyncIOMotorDatabase = Depends(get_database),
    redis: Redis = Depends(get_redis),
) -> JSONResponse:
    mongodb_state = "up"
    redis_state = "up"
    try:
        await database.command("ping")
    except Exception:
        logger.error("MongoDB health check failed", exc_info=True)
        mongodb_state = "down"
    try:
        await redis.ping()
    except Exception:
        logger.error("Redis health check failed", exc_info=True)
        redis_state = "down"

    healthy = mongodb_state == "up" and redis_state == "up"
    body = HealthResponse(
        status="ok" if healthy else "degraded", mongodb=mongodb_state, redis=redis_state
    )
    return JSONResponse(status_code=200 if healthy else 503, content=body.model_dump())
