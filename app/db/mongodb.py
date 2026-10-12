from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from app.core.config import Settings

DOCUMENTS_COLLECTION = "documents"


def create_mongo_client(settings: Settings) -> AsyncIOMotorClient:
    return AsyncIOMotorClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=settings.mongodb_timeout_ms,
        tz_aware=True,
    )


async def ensure_indexes(database: AsyncIOMotorDatabase) -> None:
    collection = database[DOCUMENTS_COLLECTION]
    await collection.create_index(
        [("user_id", ASCENDING), ("created_at", DESCENDING)], name="user_created_at"
    )
    await collection.create_index(
        [("user_id", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)],
        name="user_status_created_at",
    )
    await collection.create_index(
        [("status", ASCENDING), ("created_at", ASCENDING)], name="status_created_at"
    )
    await collection.create_index([("content_hash", ASCENDING)], name="content_hash")
