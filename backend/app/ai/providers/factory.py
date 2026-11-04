"""Builds the configured provider. A missing key gives None: AiClient reports it per call."""

from app.ai.providers.base import Provider
from app.ai.providers.demo import DemoProvider
from app.ai.providers.groq_provider import GROQ_BASE_URL, GroqProvider
from app.ai.providers.openai_provider import OpenAiProvider, build_client
from app.core.config import Settings

KEY_VARIABLES = {"groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY"}
BILLING_PAGES = {
    "groq": "https://console.groq.com/settings/billing",
    "openai": "https://platform.openai.com/settings/organization/billing/",
}


def api_key_for(settings: Settings) -> str:
    keys = {"groq": settings.groq_api_key, "openai": settings.openai_api_key}
    return keys.get(settings.ai_provider, "")


def build_provider(settings: Settings) -> Provider | None:
    if settings.ai_provider == "demo":
        return DemoProvider()
    key = api_key_for(settings)
    if not key:
        return None
    if settings.ai_provider == "groq":
        return GroqProvider(
            build_client(key, settings.ai_timeout_s, base_url=GROQ_BASE_URL)
        )
    return OpenAiProvider(build_client(key, settings.ai_timeout_s))
