from fastapi import Request
from fastapi.responses import JSONResponse

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
