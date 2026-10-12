import pytest
from bson import ObjectId

from app.models.schemas import DocumentStatus
from app.services.cache_service import CacheService, compute_content_hash


async def test_submit_valid_document_returns_201_and_queues_it(client, make_payload, database):
    payload = make_payload()

    response = await client.post("/documents", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["cached"] is False
    assert body["summary"] is None
    stored = await database["documents"].find_one({})
    assert str(stored["_id"]) == body["document_id"]
    assert stored["status"] == "queued"
    assert stored["user_id"] == payload["user_id"]
    assert stored["content_hash"] == compute_content_hash(payload["content"])


@pytest.mark.parametrize(
    "mutation",
    [
        {"user_id": ""},
        {"user_id": "bad user id!"},
        {"title": ""},
        {"title": "x" * 201},
        {"content": ""},
        {"content": "   "},
        {"content": "x" * 100_001},
        {"title": 123},
    ],
)
async def test_submit_rejects_invalid_payloads(client, make_payload, mutation, database):
    payload = {**make_payload(), **mutation}

    response = await client.post("/documents", json=payload)

    assert response.status_code == 422
    assert "detail" in response.json()
    assert await database["documents"].count_documents({}) == 0


@pytest.mark.parametrize("missing", ["user_id", "title", "content"])
async def test_submit_rejects_missing_fields(client, make_payload, missing):
    payload = make_payload()
    del payload[missing]

    response = await client.post("/documents", json=payload)

    assert response.status_code == 422
    assert missing in response.json()["detail"]


async def test_submit_rejects_malformed_json(client):
    response = await client.post(
        "/documents", content="{not json", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422


async def test_get_document_returns_current_status(client, make_payload):
    created = (await client.post("/documents", json=make_payload(title="Budget"))).json()

    response = await client.get(f"/documents/{created['document_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == created["document_id"]
    assert body["title"] == "Budget"
    assert body["status"] == "queued"
    assert body["summary"] is None
    assert body["user_id"] == "user-1"
    assert "content" not in body


async def test_get_document_returns_summary_once_completed(client, make_payload, worker):
    created = (await client.post("/documents", json=make_payload())).json()
    await worker.process_next()

    body = (await client.get(f"/documents/{created['document_id']}")).json()

    assert body["status"] == "completed"
    assert body["summary"]
    assert body["completed_at"] is not None


async def test_get_unknown_document_returns_404(client):
    response = await client.get("/documents/64b7f0c2a1b2c3d4e5f60718")

    assert response.status_code == 404
    assert "was not found" in response.json()["detail"]


@pytest.mark.parametrize("bad_id", ["abc", "123", "zzzzzzzzzzzzzzzzzzzzzzzz"])
async def test_get_document_with_malformed_id_returns_400(client, bad_id):
    response = await client.get(f"/documents/{bad_id}")

    assert response.status_code == 400
    assert "not a valid document id" in response.json()["detail"]


async def test_list_returns_only_the_requested_users_documents(client, make_payload):
    await client.post("/documents", json=make_payload(user_id="alice"))
    await client.post("/documents", json=make_payload(user_id="alice"))
    await client.post("/documents", json=make_payload(user_id="bob"))

    response = await client.get("/users/alice/documents")

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "alice"
    assert len(body["documents"]) == 2
    assert body["pagination"] == {"page": 1, "page_size": 10, "total_items": 2, "total_pages": 1}


async def test_list_for_unknown_user_is_empty(client):
    body = (await client.get("/users/nobody/documents")).json()

    assert body["documents"] == []
    assert body["pagination"]["total_items"] == 0
    assert body["pagination"]["total_pages"] == 0


async def test_list_is_paginated_newest_first(client, repository):
    for number in range(1, 6):
        await repository.insert(
            document_id=ObjectId(),
            user_id="carol",
            title=f"Doc {number}",
            content=f"Unique content {number}",
            content_hash=compute_content_hash(f"Unique content {number}"),
            status=DocumentStatus.COMPLETED,
            summary="done",
        )

    first = (await client.get("/users/carol/documents", params={"page": 1, "page_size": 2})).json()
    last = (await client.get("/users/carol/documents", params={"page": 3, "page_size": 2})).json()
    beyond = (await client.get("/users/carol/documents", params={"page": 4, "page_size": 2})).json()

    assert [item["title"] for item in first["documents"]] == ["Doc 5", "Doc 4"]
    assert first["pagination"] == {"page": 1, "page_size": 2, "total_items": 5, "total_pages": 3}
    assert [item["title"] for item in last["documents"]] == ["Doc 1"]
    assert beyond["documents"] == []


async def test_list_filters_by_status(client, make_payload, worker):
    for number in range(3):
        await client.post("/documents", json=make_payload(user_id="dave", content=f"Distinct text {number}"))
    await worker.process_next()

    completed = (await client.get("/users/dave/documents", params={"status": "completed"})).json()
    queued = (await client.get("/users/dave/documents", params={"status": "queued"})).json()
    failed = (await client.get("/users/dave/documents", params={"status": "failed"})).json()

    assert completed["pagination"]["total_items"] == 1
    assert {item["status"] for item in completed["documents"]} == {"completed"}
    assert queued["pagination"]["total_items"] == 2
    assert failed["documents"] == []


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page": -1},
        {"page": "abc"},
        {"page_size": 0},
        {"page_size": 101},
        {"status": "unknown"},
    ],
)
async def test_list_rejects_invalid_query_parameters(client, params):
    response = await client.get("/users/alice/documents", params=params)

    assert response.status_code == 422


async def test_rate_limit_allows_three_and_rejects_fourth_with_429(client, make_payload):
    for _ in range(3):
        assert (await client.post("/documents", json=make_payload(user_id="erin"))).status_code == 201

    response = await client.post("/documents", json=make_payload(user_id="erin"))

    assert response.status_code == 429
    assert "already has 3 documents" in response.json()["detail"]
    assert response.headers["retry-after"]
    listing = (await client.get("/users/erin/documents")).json()
    assert listing["pagination"]["total_items"] == 3


async def test_rate_limit_is_tracked_per_user(client, make_payload):
    for _ in range(3):
        await client.post("/documents", json=make_payload(user_id="frank"))

    response = await client.post("/documents", json=make_payload(user_id="grace"))

    assert response.status_code == 201


async def test_concurrent_submissions_never_exceed_the_limit(client, make_payload):
    import asyncio

    responses = await asyncio.gather(
        *(client.post("/documents", json=make_payload(user_id="heidi")) for _ in range(10))
    )

    codes = sorted(response.status_code for response in responses)
    assert codes.count(201) == 3
    assert codes.count(429) == 7


async def test_slot_is_released_after_processing_so_user_can_submit_again(client, make_payload, worker):
    for _ in range(3):
        await client.post("/documents", json=make_payload(user_id="ivan"))
    assert (await client.post("/documents", json=make_payload(user_id="ivan"))).status_code == 429

    await worker.process_next()

    assert (await client.post("/documents", json=make_payload(user_id="ivan"))).status_code == 201


async def test_identical_content_is_served_from_cache(client, make_payload, worker, sleep_mock):
    content = "Identical content that should only be summarized once."
    first = (await client.post("/documents", json=make_payload(user_id="judy", content=content))).json()
    await worker.process_next()
    stored_summary = (await client.get(f"/documents/{first['document_id']}")).json()["summary"]

    response = await client.post("/documents", json=make_payload(user_id="judy", content=content))

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "completed"
    assert body["cached"] is True
    assert body["summary"] == stored_summary
    assert body["document_id"] != first["document_id"]
    assert sleep_mock.await_count == 1
    assert await worker.process_next() is False
    detail = (await client.get(f"/documents/{body['document_id']}")).json()
    assert detail["status"] == "completed"
    assert detail["cached"] is True


async def test_cache_is_shared_across_users(client, make_payload, worker):
    content = "Shared content across different users."
    await client.post("/documents", json=make_payload(user_id="kim", content=content))
    await worker.process_next()

    body = (await client.post("/documents", json=make_payload(user_id="lee", content=content))).json()

    assert body["cached"] is True
    assert body["status"] == "completed"


async def test_cache_hit_does_not_consume_an_active_slot(client, make_payload, worker, rate_limiter):
    content = "Content cached before the burst."
    await client.post("/documents", json=make_payload(user_id="mia", content=content))
    await worker.process_next()

    for _ in range(5):
        assert (await client.post("/documents", json=make_payload(user_id="mia", content=content))).status_code == 201

    assert await rate_limiter.active_count("mia") == 0


async def test_cache_is_not_populated_for_failed_processing(
    client, make_payload, make_worker, cache, redis
):
    from tests.conftest import StubRandom

    content = "Content whose first attempt fails."
    await client.post("/documents", json=make_payload(user_id="nina", content=content))
    await make_worker(rng=StubRandom(roll=0.0)).process_next()

    assert await redis.keys("summary_cache:*") == []
    again = (await client.post("/documents", json=make_payload(user_id="nina", content=content))).json()
    assert again["status"] == "queued"
    assert again["cached"] is False


async def test_cache_entry_expires_with_configured_ttl(client, make_payload, worker, redis, settings):
    content = "Content with a ttl."
    await client.post("/documents", json=make_payload(content=content))
    await worker.process_next()

    ttl = await redis.ttl(CacheService.key_for(compute_content_hash(content)))

    assert 0 < ttl <= settings.summary_cache_ttl_seconds


async def test_cache_outage_falls_back_to_normal_processing(client, make_payload, monkeypatch):
    from redis.exceptions import ConnectionError as RedisConnectionError

    async def broken_get(*args, **kwargs):
        raise RedisConnectionError("down")

    monkeypatch.setattr("fakeredis.FakeAsyncRedis.get", broken_get)

    response = await client.post("/documents", json=make_payload())

    assert response.status_code == 201
    assert response.json()["status"] == DocumentStatus.QUEUED.value


async def test_redis_outage_during_submission_returns_503_without_leaking_details(
    client, make_payload, monkeypatch, database
):
    from redis.exceptions import ConnectionError as RedisConnectionError

    async def broken_zset(*args, **kwargs):
        raise RedisConnectionError("secret-host:6379 refused")

    monkeypatch.setattr("redis.commands.core.AsyncScript.__call__", broken_zset)

    response = await client.post("/documents", json=make_payload())

    assert response.status_code == 503
    assert "secret-host" not in response.text
    assert "Redis is currently unavailable" in response.json()["detail"]
    assert await database["documents"].count_documents({}) == 0


async def test_mongo_failure_on_insert_releases_slot_and_returns_503(
    client, make_payload, rate_limiter, monkeypatch
):
    from pymongo.errors import ServerSelectionTimeoutError

    async def broken_insert(self, *args, **kwargs):
        raise ServerSelectionTimeoutError("mongo:27017 timed out")

    monkeypatch.setattr("app.repositories.document_repository.DocumentRepository.insert", broken_insert)

    response = await client.post("/documents", json=make_payload(user_id="oscar"))

    assert response.status_code == 503
    assert "mongo:27017" not in response.text
    assert await rate_limiter.active_count("oscar") == 0


async def test_unexpected_errors_return_generic_500(client, make_payload, monkeypatch):
    async def explode(self, payload):
        raise RuntimeError("password=hunter2")

    monkeypatch.setattr("app.services.document_service.DocumentService.submit_document", explode)

    response = await client.post("/documents", json=make_payload())

    assert response.status_code == 500
    assert response.json() == {"detail": "An unexpected error occurred"}


async def test_health_reports_both_dependencies_up(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mongodb": "up", "redis": "up"}


async def test_health_returns_503_when_redis_is_down(client, monkeypatch):
    from redis.exceptions import ConnectionError as RedisConnectionError

    async def broken_ping(self, *args, **kwargs):
        raise RedisConnectionError("down")

    monkeypatch.setattr("fakeredis.FakeAsyncRedis.ping", broken_ping)

    response = await client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "mongodb": "up", "redis": "down"}
