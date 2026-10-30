from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.ai.errors import AiError


async def ai_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """AI failures become clean JSON errors: a stable code and a safe, friendly message.

    Never includes provider details, prompts or user data.
    """
    assert isinstance(exc, AiError)
    return JSONResponse(
        status_code=exc.http_status,
        content={"detail": {"code": exc.code, "message": exc.user_message}},
    )


async def db_busy_handler(_request: Request, _exc: Exception) -> JSONResponse:
    """The database connection pool is exhausted: shed load with a quick, clean 503.

    Clients are told when to retry; no pool or SQL detail is exposed.
    """
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "2"},
        content={
            "detail": {
                "code": "server_busy",
                "message": "The server is busy right now. Please try again in a moment.",
            }
        },
    )


__all__ = ["PoolTimeoutError", "ai_error_handler", "db_busy_handler"]
