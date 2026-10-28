from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.ai.errors import AiError
from app.api import auth, consents, exercises, health, me, onboarding
from app.api.errors import ai_error_handler
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.rate_limit import limiter


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    app = FastAPI(title="FitCoach PRO API")
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_exception_handler(AiError, ai_error_handler)
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
    return app


app = create_app()
