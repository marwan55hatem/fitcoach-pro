"""Default model per task and provider, plus price estimates.

Model IDs and prices come from the provider's published docs (checked 2026-10-28) and
change often: override per task with AI_MODEL_* and per model with AI_PRICE_OVERRIDES.
`python -m app.ai.check` confirms the configured models exist for your API key.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.ai.types import Task, Usage

DEFAULT_MODELS: dict[str, dict[Task, str]] = {
    "openai": {
        Task.MAIN: "gpt-6.1-sol",  # near-flagship quality at a lower price
        Task.VISION: "gpt-6.1-sol",
        Task.FAST: "gpt-6-luna",  # cheapest tier, high volume
    },
}


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1M tokens (prompts up to 272K tokens; longer prompts cost more)."""

    input: float
    cached_input: float
    output: float


DEFAULT_PRICES: dict[str, ModelPrice] = {
    "gpt-6-astra": ModelPrice(10.00, 1.00, 50.00),
    "gpt-6.1-sol": ModelPrice(2.00, 0.10, 10.00),
    "gpt-6-sol": ModelPrice(2.00, 0.20, 10.00),
    "gpt-6-luna": ModelPrice(0.10, 0.01, 0.50),
}

_MILLION = Decimal(1_000_000)


def resolve_price(
    model: str, overrides: dict[str, list[float]] | None = None
) -> ModelPrice | None:
    if overrides and model in overrides:
        input_, cached, output = overrides[model]
        return ModelPrice(input_, cached, output)
    return DEFAULT_PRICES.get(model)


def estimate_cost(
    model: str, usage: Usage, overrides: dict[str, list[float]] | None = None
) -> Decimal | None:
    """Estimated USD for one call, or None when the model has no known price."""
    price = resolve_price(model, overrides)
    if price is None:
        return None
    cached = min(usage.cached_tokens, usage.input_tokens)
    uncached = usage.input_tokens - cached
    cost = (
        Decimal(str(price.input)) * uncached
        + Decimal(str(price.cached_input)) * cached
        + Decimal(str(price.output)) * usage.output_tokens
    ) / _MILLION
    return cost.quantize(Decimal("0.000001"))
