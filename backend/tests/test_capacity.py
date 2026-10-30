"""Capacity guard rails: the settings and admission control that keep the API from wedging."""

import asyncio
import time

import anyio.to_thread
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.core.concurrency import ConcurrencyLimitMiddleware
from app.core.config import Settings, get_settings
from app.db.session import engine
from app.main import create_app


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


# --- settings ---------------------------------------------------------------------------


def test_defaults_never_allow_more_requests_than_connections() -> None:
    s = settings()
    assert s.max_inflight_requests <= s.db_pool_size + s.db_max_overflow


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_inflight_requests": 100, "db_pool_size": 5, "db_max_overflow": 10},
        {"max_inflight_requests": 41, "db_pool_size": 20, "db_max_overflow": 20},
    ],
)
def test_more_inflight_requests_than_connections_is_rejected(
    overrides: dict[str, int],
) -> None:
    with pytest.raises(ValidationError, match="MAX_INFLIGHT_REQUESTS"):
        settings(**overrides)


def test_boundary_is_allowed() -> None:
    s = settings(max_inflight_requests=40, db_pool_size=20, db_max_overflow=20)
    assert s.max_inflight_requests == s.db_pool_size + s.db_max_overflow


def test_engine_uses_the_configured_pool() -> None:
    s = get_settings()
    assert engine.pool.size() == s.db_pool_size
    assert engine.pool._max_overflow == s.db_max_overflow
    assert engine.pool.timeout() == s.db_pool_timeout_s
    assert engine.pool.timeout() <= 10  # fail fast: never the old 30 s hang


def test_thread_pool_is_sized_from_settings_at_startup() -> None:
    app = create_app()

    @app.get("/_test/threads")
    async def threads() -> dict[str, float]:
        return {"tokens": anyio.to_thread.current_default_thread_limiter().total_tokens}

    with TestClient(app) as client:  # entering the client runs the lifespan startup
        assert (
            client.get("/_test/threads").json()["tokens"]
            == get_settings().request_threads
        )


def test_the_real_app_has_the_limiter_inside_cors() -> None:
    app = create_app()
    names = [m.cls.__name__ for m in app.user_middleware]
    # Starlette lists the outermost middleware first: CORS must wrap the limiter so even
    # a shed 503 carries CORS headers and the browser shows the real error.
    assert names.index("CORSMiddleware") < names.index("ConcurrencyLimitMiddleware")


def test_pool_exhaustion_becomes_a_fast_503_with_retry_after() -> None:
    app = create_app()

    @app.get("/_test/busy")
    def busy() -> None:
        raise PoolTimeoutError(
            "QueuePool limit of size 16 overflow 24 reached", None, None
        )

    res = TestClient(app).get("/_test/busy")
    assert res.status_code == 503
    assert res.headers["retry-after"] == "2"
    detail = res.json()["detail"]
    assert detail["code"] == "server_busy" and detail["message"]
    assert (
        "QueuePool" not in res.text and "overflow" not in res.text
    )  # no internals leaked


# --- admission control ------------------------------------------------------------------


def limited_app(limit: int, wait_s: float) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ConcurrencyLimitMiddleware, limit=limit, wait_s=wait_s)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/slow")
    async def slow() -> dict[str, str]:
        await asyncio.sleep(0.3)
        return {"status": "done"}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("handler crashed")

    return app


def client_for(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_excess_requests_get_a_fast_503_with_retry_after() -> None:
    async with client_for(limited_app(limit=2, wait_s=0.05)) as client:
        started = time.perf_counter()

        async def timed() -> tuple[httpx.Response, float]:
            response = await client.get("/slow")
            return response, time.perf_counter() - started

        results = await asyncio.gather(*[timed() for _ in range(5)])

    ok = [r for r, _ in results if r.status_code == 200]
    shed = [(r, t) for r, t in results if r.status_code == 503]
    assert len(ok) == 2 and len(shed) == 3
    for response, elapsed in shed:
        assert response.headers["retry-after"] == "1"
        assert response.json()["detail"]["code"] == "server_busy"
        assert elapsed < 0.25  # shed quickly, not after the slow requests finish


async def test_a_request_waits_briefly_for_a_slot_instead_of_failing() -> None:
    async with client_for(limited_app(limit=1, wait_s=2.0)) as client:
        first, second = await asyncio.gather(client.get("/slow"), client.get("/slow"))
    assert first.status_code == second.status_code == 200  # the second queued, then ran


async def test_health_checks_bypass_the_limit() -> None:
    async with client_for(limited_app(limit=1, wait_s=0.05)) as client:
        busy = asyncio.create_task(client.get("/slow"))
        await asyncio.sleep(0.05)  # the only slot is now taken
        assert (await client.get("/health")).status_code == 200
        assert (
            await client.get("/slow")
        ).status_code == 503  # a normal request is shed
        assert (await busy).status_code == 200


async def test_slots_are_released_after_success_and_after_a_crash() -> None:
    async with client_for(limited_app(limit=1, wait_s=0.05)) as client:
        for _ in range(3):  # would hang or 503 if a slot leaked
            assert (await client.get("/boom")).status_code == 500
            assert (await client.get("/slow")).status_code == 200


async def test_non_http_traffic_is_not_limited() -> None:
    reached: list[str] = []

    async def inner(scope, receive, send) -> None:
        reached.append(scope["type"])

    middleware = ConcurrencyLimitMiddleware(inner, limit=1, wait_s=0.01)
    await middleware({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert reached == ["lifespan"]
