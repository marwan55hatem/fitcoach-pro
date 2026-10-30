"""Closed-loop load test against a RUNNING API (never run against production).

    python -m scripts.loadtest http://localhost:8011 "label" [seconds_per_scenario]

Registers a throwaway user, then runs scenarios where N concurrent virtual users each send
requests back to back. Prints one line per scenario as it finishes and deletes the user.
Start the server with a high auth rate limit first, for example:
    $env:RATE_LIMIT_AUTH="10000000/minute"; python -m uvicorn app.main:app --port 8011

Run it from a second machine for realistic numbers: on one machine the load generator and
the server compete for the same CPU, so results are conservative.
"""

import asyncio
import sys
import time
import uuid

import httpx

BASE, LABEL = sys.argv[1], sys.argv[2]
SECONDS = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0
EMAIL = f"bench.{uuid.uuid4().hex[:8]}@example.com"
PASSWORD = "benchmark-password-123"


async def scenario(name, build, concurrency):
    latencies, errors = [], 0
    stop = time.perf_counter() + SECONDS
    limits = httpx.Limits(
        max_connections=concurrency, max_keepalive_connections=concurrency
    )
    async with httpx.AsyncClient(base_url=BASE, limits=limits, timeout=30) as client:

        async def user():
            nonlocal errors
            while time.perf_counter() < stop:
                started = time.perf_counter()
                try:
                    ok = (await build(client)).status_code < 400
                except Exception:  # noqa: BLE001 (a load test counts every failure)
                    ok = False
                latencies.append(time.perf_counter() - started)
                if not ok:
                    errors += 1

        t0 = time.perf_counter()
        await asyncio.gather(*[user() for _ in range(concurrency)])
        duration = time.perf_counter() - t0
    latencies.sort()
    n = len(latencies)
    pick = lambda q: latencies[min(n - 1, int(n * q))] * 1000
    print(
        f"{name:<46}{concurrency:>5}{n / duration:>9.0f}{pick(0.5):>9.0f}{pick(0.95):>9.0f}"
        f"{pick(0.99):>9.0f}{latencies[-1] * 1000:>9.0f}{errors:>6}/{n}",
        flush=True,
    )


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        await c.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})
        r = await c.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    health = lambda c: c.get("/health")
    me = lambda c: c.get("/auth/me", headers=auth)
    exercises = lambda c: c.get("/exercises?limit=50&muscle=chest", headers=auth)
    login = lambda c: c.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})

    print(f"=== {LABEL} ({SECONDS:.0f}s per scenario) ===")
    print(
        f"{'endpoint':<46}{'users':>5}{'req/s':>9}{'p50 ms':>9}{'p95 ms':>9}"
        f"{'p99 ms':>9}{'max ms':>9}{'errors':>12}",
        flush=True,
    )
    await scenario("warmup /health", health, 10)
    for conc in (10, 100, 500):
        await scenario("GET /health (no DB)", health, conc)
    for conc in (10, 50, 200):
        await scenario("GET /auth/me (JWT + 1 DB read)", me, conc)
    for conc in (10, 50, 200):
        await scenario("GET /exercises (list+count+50 URLs)", exercises, conc)
    for conc in (4, 16):
        await scenario("POST /auth/login (Argon2)", login, conc)

    async with httpx.AsyncClient(base_url=BASE, timeout=60) as c:
        r = await c.request("DELETE", "/me", headers=auth, json={"password": PASSWORD})
        print(f"cleanup: benchmark user deleted -> HTTP {r.status_code}", flush=True)


asyncio.run(main())
