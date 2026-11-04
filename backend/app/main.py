from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio.to_thread
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.ai.errors import AiError
from app.api import (
    auth,
    body,
    consents,
    exercises,
    files,
    health,
    inbody,
    me,
    nutrition,
    onboarding,
    programs,
    training,
)
from app.api.errors import PoolTimeoutError, ai_error_handler, db_busy_handler
from app.core.concurrency import ConcurrencyLimitMiddleware
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.rate_limit import limiter


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Sync endpoints run on a thread pool; size it to what the DB pool can serve.
    limiter_ = anyio.to_thread.current_default_thread_limiter()
    limiter_.total_tokens = get_settings().request_threads
    yield


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    app = FastAPI(title="FitCoach PRO API", lifespan=lifespan)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_exception_handler(AiError, ai_error_handler)
    app.add_exception_handler(PoolTimeoutError, db_busy_handler)
    app.add_middleware(
        ConcurrencyLimitMiddleware,
        limit=settings.max_inflight_requests,
        wait_s=settings.inflight_wait_s,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(consents.router)
    app.include_router(me.router)
    app.include_router(onboarding.router)
    app.include_router(exercises.router)
    app.include_router(programs.router)
    app.include_router(training.router)
    app.include_router(training.exercise_router)
    app.include_router(training.stats_router)
    app.include_router(nutrition.router)
    app.include_router(body.router)
    app.include_router(files.router)
    app.include_router(inbody.router)
    return app


app = create_app()
