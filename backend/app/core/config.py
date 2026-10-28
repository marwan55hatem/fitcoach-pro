from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_WEAK_SECRETS = {"", "change-me", "change-me-to-a-long-random-string"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3001"])

    database_url: str = "postgresql+psycopg://fitcoach:fitcoach@localhost:5433/fitcoach"

    jwt_secret: str = "change-me"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    rate_limit_auth: str = "10/minute"

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "fitcoach"
    s3_region: str = "us-east-1"

    # AI: every model call goes through app/ai/client.py (see CLAUDE.md).
    ai_provider: Literal["openai"] = "openai"
    openai_api_key: str = ""
    anthropic_api_key: str = ""  # reserved for a future Claude provider adapter
    # Empty model = the provider's default for that task (app/ai/models.py).
    ai_model_main: str = ""  # coach chat, program generation
    ai_model_vision: str = ""  # InBody and food photo extraction
    ai_model_fast: str = ""  # cheap classification
    ai_daily_token_budget: int = 300_000  # input + output tokens per user per day
    ai_timeout_s: float = 60.0
    ai_max_retries: int = 2
    # Optional price overrides: {"model-id": [input, cached_input, output]} USD per 1M tokens.
    ai_price_overrides: dict[str, list[float]] = Field(default_factory=dict)

    telegram_bot_token: str = ""
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:admin@example.com"

    @model_validator(mode="after")
    def _require_real_secret_outside_dev(self) -> "Settings":
        if self.app_env != "development" and (
            self.jwt_secret in _WEAK_SECRETS or len(self.jwt_secret) < 32
        ):
            raise ValueError("JWT_SECRET must be a random string of 32+ characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
