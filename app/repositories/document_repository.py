from datetime import datetime, timedelta, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import DESCENDING, ReturnDocument

from app.core.exceptions import InvalidDocumentIdError
from app.db.mongodb import DOCUMENTS_COLLECTION
from app.models.schemas import DocumentStatus

DocumentRecord = dict[str, Any]

_DATETIME_FIELDS = ("created_at", "updated_at", "started_at", "completed_at", "locked_at")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_object_id(document_id: str) -> ObjectId:
    try:
        return ObjectId(document_id)
    except (InvalidId, TypeError) as exc:
        raise InvalidDocumentIdError(document_id) from exc


def serialize(record: DocumentRecord | None) -> DocumentRecord | None:
    if record is None:
        return None
    result = dict(record)
    result["document_id"] = str(result.pop("_id"))
    for field in _DATETIME_FIELDS:
        value = result.get(field)
        if isinstance(value, datetime) and value.tzinfo is None:
            result[field] = value.replace(tzinfo=timezone.utc)
    return result


class DocumentRepository:
    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        self._collection = database[DOCUMENTS_COLLECTION]

    async def insert(
        self,
        document_id: ObjectId,
        user_id: str,
        title: str,
        content: str,
        content_hash: str,
        status: DocumentStatus,
        summary: str | None = None,
        cached: bool = False,
    ) -> DocumentRecord:
        now = utcnow()
        record: DocumentRecord = {
            "_id": document_id,
            "user_id": user_id,
            "title": title,
            "content": content,
            "content_hash": content_hash,
            "status": status.value,
            "summary": summary,
            "error": None,
            "cached": cached,
            "attempts": 0,
            "worker_id": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": now if status is DocumentStatus.COMPLETED else None,
            "locked_at": None,
        }
        await self._collection.insert_one(record)
        return serialize(record)

    async def get(self, document_id: str) -> DocumentRecord | None:
        record = await self._collection.find_one({"_id": parse_object_id(document_id)})
        return serialize(record)

    async def list_for_user(
        self, user_id: str, page: int, page_size: int, status: DocumentStatus | None
    ) -> tuple[list[DocumentRecord], int]:
        query: dict[str, Any] = {"user_id": user_id}
        if status is not None:
            query["status"] = status.value
        total = await self._collection.count_documents(query)
        cursor = (
            self._collection.find(query, projection={"content": 0})
            .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        records = [serialize(record) async for record in cursor]
        return records, total

    async def claim_next(self, worker_id: str, lock_timeout_seconds: int) -> DocumentRecord | None:
        now = utcnow()
        stale_before = now - timedelta(seconds=lock_timeout_seconds)
        record = await self._collection.find_one_and_update(
            {
                "$or": [
                    {"status": DocumentStatus.QUEUED.value},
                    {"status": DocumentStatus.PROCESSING.value, "locked_at": {"$lt": stale_before}},
                ]
            },
            {
                "$set": {
                    "status": DocumentStatus.PROCESSING.value,
                    "worker_id": worker_id,
                    "started_at": now,
                    "locked_at": now,
                    "updated_at": now,
                },
                "$inc": {"attempts": 1},
            },
            sort=[("created_at", 1)],
            return_document=ReturnDocument.AFTER,
        )
        return serialize(record)

    async def mark_completed(self, document_id: str, worker_id: str, summary: str) -> bool:
        now = utcnow()
        result = await self._collection.update_one(
            {
                "_id": parse_object_id(document_id),
                "status": DocumentStatus.PROCESSING.value,
                "worker_id": worker_id,
            },
            {
                "$set": {
                    "status": DocumentStatus.COMPLETED.value,
                    "summary": summary,
                    "error": None,
                    "completed_at": now,
                    "updated_at": now,
                    "locked_at": None,
                }
            },
        )
        return result.modified_count == 1

    async def mark_failed(self, document_id: str, worker_id: str, error: str) -> bool:
        now = utcnow()
        result = await self._collection.update_one(
            {
                "_id": parse_object_id(document_id),
                "status": DocumentStatus.PROCESSING.value,
                "worker_id": worker_id,
            },
            {
                "$set": {
                    "status": DocumentStatus.FAILED.value,
                    "error": error,
                    "completed_at": now,
                    "updated_at": now,
                    "locked_at": None,
                }
            },
        )
        return result.modified_count == 1
