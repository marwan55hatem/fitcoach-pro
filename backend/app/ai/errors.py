"""AI failures, each with a safe message for the user and an HTTP status.

`user_message` never contains provider details, prompts or user data. `transient` marks
the failures worth retrying.
"""

from typing import ClassVar


class AiError(Exception):
    code: ClassVar[str] = "ai_error"
    http_status: ClassVar[int] = 502
    transient: ClassVar[bool] = False
    user_message: ClassVar[str] = "The AI service had a problem. Please try again."


class AiNotConfiguredError(AiError):
    code = "ai_not_configured"
    http_status = 503
    user_message = "The AI features are not available right now."


class AiBudgetExceededError(AiError):
    code = "ai_budget_exceeded"
    http_status = 429
    user_message = "You have reached today's AI usage limit. It resets tomorrow."


class AiRefusalError(AiError):
    code = "ai_refused"
    http_status = 422
    user_message = "The AI could not process this request."


class AiOutputError(AiError):
    code = "ai_bad_output"
    http_status = 502
    user_message = "The AI returned an answer we could not use. Please try again."


class AiProviderError(AiError):
    code = "ai_provider_error"
    http_status = 502


class AiTimeoutError(AiError):
    code = "ai_timeout"
    http_status = 504
    transient = True
    user_message = "The AI took too long to answer. Please try again."


class AiConnectionError(AiError):
    code = "ai_unreachable"
    http_status = 503
    transient = True
    user_message = "The AI service is unreachable right now. Please try again shortly."


class AiRateLimitError(AiError):
    code = "ai_busy"
    http_status = 503
    transient = True
    user_message = "The AI service is busy. Please try again in a moment."


class AiServerError(AiError):
    code = "ai_server_error"
    http_status = 502
    transient = True
