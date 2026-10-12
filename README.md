# Document Insights API

A small backend service for summarizing documents in the background. You submit a document, get an ID back right away, and check its status later. A separate worker does the "summarizing". The AI part is mocked: it waits 10 to 30 seconds, builds a simple summary, and fails about 10% of the time.

## Features

- Submit a document, check its status, list a user's documents (with pagination and status filter)
- Separate worker process, safe to run more than one
- Max 3 active documents per user, tracked in Redis
- Same content twice returns the cached summary instantly
- Health check for MongoDB and Redis
- JSON logs and clear error messages

## Tech stack

Python 3.11, FastAPI, MongoDB (Motor), Redis, Docker Compose. Tests use pytest, HTTPX, fakeredis and mongomock-motor.

## Project structure

```
app/
  main.py                    app setup and error handlers
  api/                       routes (documents, health) and dependencies
  core/                      config, exceptions, logging
  models/schemas.py          request and response models
  db/                        MongoDB and Redis clients
  repositories/              all MongoDB queries
  services/                  document, cache and rate limit logic
  workers/                   background worker and mock summarizer
tests/
Dockerfile
docker-compose.yml
requirements.txt
requirements-dev.txt
.env.example
```

Routes call services, services call the repository, and only the repository talks to MongoDB.

## Prerequisites

Docker with Compose. Python 3.11+ is only needed if you want to run the tests outside Docker.

## Configuration

Everything comes from environment variables. See `.env.example` for the full list. Docker Compose already has working defaults, so you don't need a `.env` file to get started.

The ones you are most likely to change: `MAX_ACTIVE_DOCUMENTS_PER_USER` (3), `SUMMARY_CACHE_TTL_SECONDS` (3600), `WORKER_FAILURE_RATE` (0.1) and `API_PORT` (8000).

## Running it

```bash
docker compose up --build
```

The API is at http://localhost:8000 and the docs are at http://localhost:8000/docs. This starts the API, one worker, MongoDB and Redis. To run more workers:

```bash
docker compose up --build --scale worker=3
```

## API

**POST /documents**

```json
{"user_id": "alice", "title": "Q3 report", "content": "Revenue grew in Q3. Costs stayed flat."}
```

Returns `201`:

```json
{"document_id": "66f1c0a2b3d4e5f607182930", "status": "queued", "summary": null, "cached": false}
```

Other responses: `422` for invalid input, `429` if the user already has 3 active documents, `503` if MongoDB or Redis is down. If the content was already summarized, you get `201` with `"status": "completed"`, the summary, and `"cached": true`.

**GET /documents/{document_id}**

Returns the status, title, summary (once completed), error (if failed) and timestamps. `404` if it doesn't exist, `400` if the ID is malformed.

```json
{
  "document_id": "66f1c0a2b3d4e5f607182930",
  "user_id": "alice",
  "title": "Q3 report",
  "status": "completed",
  "summary": "Q3 report: Revenue grew in Q3. Costs stayed flat. (7 words; key terms: revenue, grew, costs)",
  "error": null,
  "cached": false
}
```

**GET /users/{user_id}/documents**

Query params: `page` (default 1), `page_size` (default 10, max 100) and `status` (`queued`, `processing`, `completed`, `failed`). Newest first.

```json
{
  "user_id": "alice",
  "documents": [{"document_id": "66f1...", "title": "Q3 report", "status": "completed", "summary": "...", "created_at": "...", "updated_at": "..."}],
  "pagination": {"page": 1, "page_size": 10, "total_items": 1, "total_pages": 1}
}
```

**GET /health**

Returns `200` when MongoDB and Redis are reachable, otherwise `503` showing which one is down.

## How the worker works

MongoDB is used as the queue, and the worker polls it.

1. `POST /documents` saves the document as `queued`.
2. A worker picks the oldest queued document using one atomic `find_one_and_update` and sets it to `processing`. Because it is a single atomic update, two workers can never take the same document.
3. It waits 10 to 30 seconds, then randomly fails about 10% of the time.
4. On success it saves the summary, sets `completed` and caches the summary. On failure it sets `failed` and saves an error message.
5. Either way, the user's active slot in Redis is released.

Errors are logged with the stack trace, the document is marked `failed`, and the worker keeps running. If a worker crashes mid-job, the document is picked up again after 2 minutes (`WORKER_LOCK_TIMEOUT_SECONDS`). On shutdown the worker finishes its current job first.

## MongoDB indexes

- `user_id + created_at` for listing a user's documents
- `user_id + status + created_at` for listing with a status filter
- `status + created_at` for the worker to find the oldest queued job
- `content_hash`

## Rate limiting (Redis)

Each user has a sorted set `active_jobs:{user_id}` that holds the IDs of their queued or processing documents.

- On submit, a small Lua script checks the size and adds the new ID in one atomic step. That keeps it safe when many requests arrive at once.
- The worker removes the ID when the job completes or fails, even if an unexpected error happens.
- It stores IDs instead of a number, so releasing twice does no harm. Entries older than 15 minutes are cleaned up automatically, so a crash can't block a user forever.
- If saving to MongoDB fails after taking a slot, the slot is released.
- If Redis is down, submissions return `503` rather than skipping the limit.

## Caching (Redis)

- Key: `summary_cache:{sha256 of content}` (content is trimmed first, title is not part of the key)
- Value: the summary, with a TTL of one hour by default
- Only the worker writes to the cache, and only after a successful completion, so failed or unfinished jobs are never cached
- On a cache hit the API creates a completed document right away and skips the queue and the rate limit
- If Redis fails during a cache lookup, it is treated as a miss

## Assumptions and decisions

- A cache hit still creates a document record so it shows up in the user's list. It returns `201` with `cached: true`.
- The cache is shared across users, since the same content gives the same summary.
- Failed documents are not retried automatically. The user can submit again.
- There is no authentication. `user_id` comes from the request body, as in the assignment.
- Two identical documents submitted at the same time are both processed. Only later ones hit the cache.

## Running the tests

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest
```

No MongoDB or Redis is needed for the tests. They use fakeredis and mongomock-motor, and the 10 to 30 second delay is mocked, so the whole suite takes a few seconds.

## Known limitations

- Tests use in-memory fakes, so the full flow should also be checked once with `docker compose up --build`.
- The worker polls every second, so a job can wait up to a second before it starts.
- No automatic retries for failed documents.
- Pagination uses skip/limit, which gets slow on very deep pages.
- MongoDB in Compose has no authentication, so it is for local use only.