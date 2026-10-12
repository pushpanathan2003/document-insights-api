import logging
import time

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.exceptions import ServiceUnavailableError

logger = logging.getLogger(__name__)

ACTIVE_JOBS_KEY_PREFIX = "active_jobs"

ACQUIRE_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local job_id = ARGV[3]
local ttl = tonumber(ARGV[4])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - ttl)
if redis.call('ZSCORE', key, job_id) then
    return 1
end
if redis.call('ZCARD', key) >= limit then
    return 0
end
redis.call('ZADD', key, now, job_id)
redis.call('EXPIRE', key, ttl)
return 1
"""


class RateLimitService:
    def __init__(self, redis: Redis, limit: int, job_ttl_seconds: int) -> None:
        self._redis = redis
        self._limit = limit
        self._job_ttl_seconds = job_ttl_seconds
        self._acquire = redis.register_script(ACQUIRE_SCRIPT)

    @property
    def limit(self) -> int:
        return self._limit

    @staticmethod
    def key_for(user_id: str) -> str:
        return f"{ACTIVE_JOBS_KEY_PREFIX}:{user_id}"

    async def try_acquire(self, user_id: str, document_id: str) -> bool:
        try:
            result = await self._acquire(
                keys=[self.key_for(user_id)],
                args=[time.time(), self._limit, document_id, self._job_ttl_seconds],
            )
        except RedisError as exc:
            logger.error("Active job acquisition failed", exc_info=True, extra={"user_id": user_id})
            raise ServiceUnavailableError("Redis") from exc
        return int(result) == 1

    async def release(self, user_id: str, document_id: str) -> None:
        try:
            await self._redis.zrem(self.key_for(user_id), document_id)
        except RedisError:
            logger.error(
                "Active job release failed; the entry will expire on its own",
                exc_info=True,
                extra={"user_id": user_id, "document_id": document_id},
            )

    async def active_count(self, user_id: str) -> int:
        try:
            key = self.key_for(user_id)
            await self._redis.zremrangebyscore(key, "-inf", time.time() - self._job_ttl_seconds)
            return int(await self._redis.zcard(key))
        except RedisError as exc:
            raise ServiceUnavailableError("Redis") from exc
