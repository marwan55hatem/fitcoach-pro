# Capacity: what the API can take today and what 1 million users would need

Measured 2026-10-30 on a development PC (20 logical cores, 16 GB RAM) with PostgreSQL and MinIO in Docker on the same machine, using `backend/scripts/loadtest.py`. The load generator is one Python process on the same machine, so it competes with the server for CPU and tops out at roughly 300 to 800 requests/s. **Treat every number as a conservative lower bound with about 2x run-to-run noise.** Re-run it on your own hardware (see README).

## What the first load test found (and what was fixed)

| | Before | After |
|---|---|---|
| 200 concurrent users on `GET /auth/me` | 6 req/s, 90% timeouts (30 s each) | 54 req/s, 0 errors, p50 about 3 s |
| Recovery after the spike | **77 seconds** of a dead server | none: stays responsive |
| Overload behaviour | 152 requests died with `QueuePool limit ... timed out` after 30 s | excess requests get an immediate `503` + `Retry-After` |

Root cause: sync endpoints keep one database session across several thread hops. With more requests in flight than pooled connections, threads blocked waiting for a connection while the requests holding connections waited for a thread, so the server wedged until timeouts fired. Fixes:

- `app/core/concurrency.py`: at most `MAX_INFLIGHT_REQUESTS` (default 40, validated to be at most the DB pool capacity) requests run per worker. Others wait up to `INFLIGHT_WAIT_S` for a slot, then get a clean 503. `/health` is exempt.
- Explicit DB pool (`DB_POOL_SIZE` 16 + `DB_MAX_OVERFLOW` 24, `DB_POOL_TIMEOUT_S` 10) and thread pool (`REQUEST_THREADS` 32) sizing, and pool exhaustion maps to a 503 instead of a stack trace.

## Measured numbers, one worker process (after the fixes)

| Endpoint | Concurrent users | Throughput | p50 latency |
|---|---|---|---|
| `GET /health` (no DB) | 10 | about 800 req/s | 5 to 9 ms |
| `GET /auth/me` (JWT + 1 DB read) | 10 / 50 | 200 to 350 req/s | 25 to 50 ms / 130 to 250 ms |
| `GET /auth/me` | 200 | about 55 req/s | about 3 s (degraded but healthy, no errors) |
| `GET /exercises` (list + 50 signed URLs) | 10 / 50 | 30 to 80 req/s | 100 to 300 ms / 0.5 to 1.6 s |
| `POST /auth/login` (Argon2) | 4 / 16 | 15 to 20 logins/s | 190 ms / about 1 s |

Known soft spots: the exercise list signs 50 image URLs per request (CPU heavy), and Argon2 makes login deliberately expensive (about 50 ms of CPU each).

## What "1 million users at the same time" means

| Reading | Rough load | Verdict |
|---|---|---|
| 1M **registered** users | about 100 to 200k active per day, 10 to 20k active at the peak hour, maybe 500 to 1,000 req/s | Reachable by scaling out (below). Not built yet. |
| 1M requests in the **same second** | 1,000,000 requests at once | Not a real design target for any system. We shed the excess with 503 + Retry-After instead of collapsing. Draining it takes about `1,000,000 / (processes x 250 req/s)`: 1 process about 67 min, 10 processes about 7 min, 100 processes about 40 s. |
| 1M users all asking the **AI coach** at once | about 1M replies | Limited by the AI provider and cost, not our servers (below). |

## The real limit at that scale is the AI, not the API

Using OpenAI's published numbers (checked 2026-10-28; re-check before relying on them): a coach reply of about 5k input and 500 output tokens on `gpt-6.1-sol` ($2 / $10 per 1M tokens) costs about **$0.015**, so one million replies is about **$15,000**. At the highest published tier (about 40M tokens/minute) that is roughly 110 to 220 replies per second, so a one-time burst of 1M replies would take around 1.5 to 2.5 hours. The per-user daily token budget (`AI_DAILY_TOKEN_BUDGET`, default 50,000) limits what one user can spend.

**Groq (the default provider today)** is far cheaper, about $0.001 per coach reply on `openai/gpt-oss-120b`, but its published Developer plan limits are shared across the whole account: 30 requests/minute, 1,000 requests/day, 8K tokens/minute and 200K tokens/day (checked 2026-10-31). At about 5,500 tokens per reply that is roughly 1.5 replies a minute and 36 a day for **all** users combined. That suits development only: real users need a higher Groq tier or another provider for those tasks.

## What to build to actually serve ~1M registered users

1. **Several API workers behind a load balancer** with autoscaling. Total DB connections = workers x pool capacity, so add **PgBouncer** (or move to async DB access) before going past Postgres's `max_connections` (100 by default).
2. **Redis** for shared state: the rate limiter is in-memory per process today, so limits are per worker.
3. **Cache or CDN the exercise catalog** (public-domain data): serve catalog images from a CDN and stop signing 50 URLs per list request.
4. **Dedicated auth capacity**: Argon2 login is CPU-bound (about 15 to 20/s per process). Cache sessions, keep access tokens short but refresh rarely.
5. **A job queue for AI work** with back-pressure, and never hold a DB session open during an AI call (release it first).
6. **Postgres**: read replica, tune connections, and plan partitioning for the high-volume tables (`set_logs`, `meal_logs`, `messages`, `notification_log`).
7. **Reminders**: shard and batch the scheduler tick.
8. **Run `scripts/loadtest.py` from a separate machine** (or use k6 / Locust) in a staging environment, and make it part of release checks.
