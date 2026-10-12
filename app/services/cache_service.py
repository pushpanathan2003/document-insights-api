import hashlib
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "summary_cache"


def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class CacheService:
    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    @staticmethod
    def key_for(content_hash: str) -> str:
        return f"{CACHE_KEY_PREFIX}:{content_hash}"

    async def get_summary(self, content_hash: str) -> str | None:
        try:
            return await self._redis.get(self.key_for(content_hash))
        except RedisError:
            logger.warning("Summary cache lookup failed", exc_info=True, extra={"content_hash": content_hash})
            return None

    async def store_summary(self, content_hash: str, summary: str) -> None:
        if not summary:
            return
        try:
            await self._redis.set(self.key_for(content_hash), summary, ex=self._ttl_seconds)
        except RedisError:
            logger.warning("Summary cache write failed", exc_info=True, extra={"content_hash": content_hash})
