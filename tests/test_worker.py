import asyncio
import logging
from datetime import timedelta

from app.repositories.document_repository import utcnow
from app.services.cache_service import CacheService, compute_content_hash
from app.workers.summarizer import generate_summary
from tests.conftest import StubRandom


async def submit(client, payload) -> str:
    response = await client.post("/documents", json=payload)
    assert response.status_code == 201
    return response.json()["document_id"]


async def test_worker_returns_false_when_queue_is_empty(worker):
    assert await worker.process_next() is False


async def test_successful_processing_completes_document_with_summary(
    client, make_payload, worker, database, sleep_mock, settings
):
    payload = make_payload(content="Revenue grew in the third quarter. Margins improved as costs fell.")
    document_id = await submit(client, payload)

    assert await worker.process_next() is True

    stored = (await client.get(f"/documents/{document_id}")).json()
    assert stored["status"] == "completed"
    assert "Revenue grew in the third quarter." in stored["summary"]
    assert stored["error"] is None
    assert stored["started_at"] is not None
    assert stored["completed_at"] is not None
    sleep_mock.assert_awaited_once_with(15.0)


async def test_processing_delay_is_drawn_from_ten_to_thirty_seconds(
    client, make_payload, worker, stub_random
):
    await submit(client, make_payload())

    await worker.process_next()

    assert stub_random.uniform_calls == [(10.0, 30.0)]


async def test_document_is_marked_processing_while_summarization_runs(
    client, make_payload, make_worker, sleep_mock
):
    document_id = await submit(client, make_payload())
    observed = {}

    async def observing_sleep(delay: float) -> None:
        observed["status"] = (await client.get(f"/documents/{document_id}")).json()["status"]

    sleep_mock.side_effect = observing_sleep

    await make_worker().process_next()

    assert observed["status"] == "processing"


async def test_successful_processing_populates_cache(client, make_payload, worker, redis):
    payload = make_payload()
    await submit(client, payload)

    await worker.process_next()

    cached = await redis.get(CacheService.key_for(compute_content_hash(payload["content"])))
    assert cached
    assert cached.startswith(payload["title"])


async def test_simulated_failure_marks_document_failed_without_caching(
    client, make_payload, make_worker, redis, caplog
):
    document_id = await submit(client, make_payload())
    failing_worker = make_worker(rng=StubRandom(roll=0.05))

    with caplog.at_level(logging.ERROR):
        await failing_worker.process_next()

    stored = (await client.get(f"/documents/{document_id}")).json()
    assert stored["status"] == "failed"
    assert stored["summary"] is None
    assert "Summarization failed" in stored["error"]
    assert await redis.keys("summary_cache:*") == []
    assert any("Processing failed" in record.getMessage() for record in caplog.records)


async def test_failure_threshold_matches_configured_rate(client, make_payload, make_worker):
    first = await submit(client, make_payload())
    second = await submit(client, make_payload())

    await make_worker(rng=StubRandom(roll=0.099)).process_next()
    await make_worker(rng=StubRandom(roll=0.1)).process_next()

    assert (await client.get(f"/documents/{first}")).json()["status"] == "failed"
    assert (await client.get(f"/documents/{second}")).json()["status"] == "completed"


async def test_unexpected_exception_is_logged_and_marks_document_failed(
    client, make_payload, worker, sleep_mock, caplog
):
    document_id = await submit(client, make_payload())
    sleep_mock.side_effect = RuntimeError("provider exploded")

    with caplog.at_level(logging.ERROR):
        await worker.process_next()

    stored = (await client.get(f"/documents/{document_id}")).json()
    assert stored["status"] == "failed"
    assert stored["error"] == "Summarization failed due to an unexpected processing error"
    assert "provider exploded" not in stored["error"]
    logged = [record for record in caplog.records if record.exc_info]
    assert logged and "provider exploded" in str(logged[0].exc_info[1])


async def test_active_slot_is_released_after_completion(client, make_payload, worker, rate_limiter):
    await submit(client, make_payload(user_id="pat"))
    assert await rate_limiter.active_count("pat") == 1

    await worker.process_next()

    assert await rate_limiter.active_count("pat") == 0


async def test_active_slot_is_released_after_failure(client, make_payload, make_worker, rate_limiter):
    await submit(client, make_payload(user_id="quinn"))

    await make_worker(rng=StubRandom(roll=0.0)).process_next()

    assert await rate_limiter.active_count("quinn") == 0


async def test_active_slot_is_released_after_unexpected_error(
    client, make_payload, worker, rate_limiter, sleep_mock
):
    await submit(client, make_payload(user_id="rita"))
    sleep_mock.side_effect = RuntimeError("boom")

    await worker.process_next()

    assert await rate_limiter.active_count("rita") == 0


async def test_counter_stays_correct_over_repeated_cycles(client, make_payload, make_worker, rate_limiter):
    for cycle in range(4):
        for _ in range(3):
            await submit(client, make_payload(user_id="sam"))
        assert (await client.post("/documents", json=make_payload(user_id="sam"))).status_code == 429
        roll = 0.0 if cycle % 2 else 0.9
        worker = make_worker(rng=StubRandom(roll=roll))
        while await worker.process_next():
            pass
        assert await rate_limiter.active_count("sam") == 0


async def test_documents_are_processed_oldest_first(client, make_payload, worker):
    first = await submit(client, make_payload(user_id="tess"))
    second = await submit(client, make_payload(user_id="tess"))

    await worker.process_next()

    assert (await client.get(f"/documents/{first}")).json()["status"] == "completed"
    assert (await client.get(f"/documents/{second}")).json()["status"] == "queued"


async def test_concurrent_workers_never_claim_the_same_document(client, make_payload, make_worker):
    ids = [await submit(client, make_payload(user_id=f"user-{n}")) for n in range(3)]
    workers = [make_worker(worker_id=f"worker-{n}") for n in range(6)]

    results = await asyncio.gather(*(worker.process_next() for worker in workers))

    assert results.count(True) == 3
    assert results.count(False) == 3
    statuses = [(await client.get(f"/documents/{document_id}")).json()["status"] for document_id in ids]
    assert statuses == ["completed"] * 3


async def test_claimed_document_is_not_claimed_again(repository, client, make_payload):
    await submit(client, make_payload())

    first = await repository.claim_next("worker-a", 120)
    second = await repository.claim_next("worker-b", 120)

    assert first is not None
    assert first["status"] == "processing"
    assert first["worker_id"] == "worker-a"
    assert first["attempts"] == 1
    assert second is None


async def test_stale_processing_document_is_reclaimed(repository, client, make_payload, database):
    document_id = await submit(client, make_payload())
    await repository.claim_next("crashed-worker", 120)
    await database["documents"].update_many(
        {}, {"$set": {"locked_at": utcnow() - timedelta(seconds=500)}}
    )

    reclaimed = await repository.claim_next("healthy-worker", 120)

    assert reclaimed is not None
    assert reclaimed["document_id"] == document_id
    assert reclaimed["worker_id"] == "healthy-worker"
    assert reclaimed["attempts"] == 2


async def test_fresh_processing_document_is_not_reclaimed(repository, client, make_payload):
    await submit(client, make_payload())
    await repository.claim_next("worker-a", 120)

    assert await repository.claim_next("worker-b", 120) is None


async def test_worker_that_lost_its_claim_cannot_overwrite_the_result(repository, client, make_payload):
    document_id = await submit(client, make_payload())
    await repository.claim_next("worker-a", 120)

    assert await repository.mark_completed(document_id, "worker-b", "intruder") is False
    assert await repository.mark_completed(document_id, "worker-a", "legit") is True
    assert await repository.mark_failed(document_id, "worker-a", "late failure") is False

    stored = await repository.get(document_id)
    assert stored["status"] == "completed"
    assert stored["summary"] == "legit"


async def test_run_loop_processes_queue_then_stops_on_event(client, make_payload, worker):
    first = await submit(client, make_payload(user_id="uma"))
    second = await submit(client, make_payload(user_id="uma"))
    stop_event = asyncio.Event()

    task = asyncio.create_task(worker.run(stop_event))
    for _ in range(50):
        await asyncio.sleep(0.01)
        statuses = {
            (await client.get(f"/documents/{document_id}")).json()["status"]
            for document_id in (first, second)
        }
        if statuses == {"completed"}:
            break
    stop_event.set()
    await asyncio.wait_for(task, timeout=3)

    assert statuses == {"completed"}


async def test_run_loop_survives_a_failing_iteration(worker, monkeypatch, caplog):
    calls = {"count": 0}
    stop_event = asyncio.Event()

    async def flaky_process_next() -> bool:
        calls["count"] += 1
        if calls["count"] == 1:
            raise ConnectionError("mongo gone")
        stop_event.set()
        return False

    monkeypatch.setattr(worker, "process_next", flaky_process_next)

    with caplog.at_level(logging.ERROR):
        await asyncio.wait_for(worker.run(stop_event), timeout=3)

    assert calls["count"] == 2
    assert any("Worker loop iteration failed" in record.getMessage() for record in caplog.records)


def test_generate_summary_uses_title_excerpt_and_word_count():
    summary = generate_summary(
        "Launch plan", "Shipping begins in March. Marketing starts earlier. Budget is approved."
    )

    assert summary.startswith("Launch plan: Shipping begins in March. Marketing starts earlier.")
    assert "Budget is approved" not in summary
    assert "(10 words" in summary


def test_generate_summary_truncates_long_content():
    summary = generate_summary("Long", "word " * 500)

    assert len(summary) < 400
    assert "..." in summary
