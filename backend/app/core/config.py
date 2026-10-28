from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3001"])

    database_url: str = "postgresql+psycopg://fitcoach:fitcoach@localhost:5433/fitcoach"
    jwt_secret: str = "change-me"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
