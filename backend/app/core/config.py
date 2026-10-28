from functools import lru_cache

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

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"

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
