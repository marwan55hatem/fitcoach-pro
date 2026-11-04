"""Prove the AI wiring end to end against the real provider (AI_PROVIDER).

    python -m app.ai.check

Reads the key from backend/.env, never prints it, and writes nothing to the database. It:
  1. shows provider, models and budget,
  2. confirms the configured models exist for your key,
  3. makes one tiny structured call,
  4. runs a two-turn tool call to prove stateless replay works,
  5. streams a short reply,
  6. reads a number from a generated image into strict JSON (no real photo is used),
  7. prints token usage and the estimated cost of each step.
"""

import io
import sys
from typing import Any

import openai
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from app.ai.errors import AiError, AiQuotaError
from app.ai.models import DEFAULT_MODELS, estimate_cost
from app.ai.providers.factory import (
    BILLING_PAGES,
    KEY_VARIABLES,
    api_key_for,
    build_provider,
)
from app.ai.types import (
    AssistantMessage,
    StreamDone,
    Task,
    TextDelta,
    ToolResultMessage,
    ToolSpec,
    Usage,
    UserMessage,
)
from app.ai.untrusted import prepare_image, with_security_preamble
from app.core.config import Settings, get_settings

MAX_TOKENS = 2000


class Sum(BaseModel):
    total: int = Field(description="the sum of the two numbers")


class PrintedWeight(BaseModel):
    weight_kg: float = Field(description="the weight printed in the image, in kg")


def main() -> int:
    settings = get_settings()
    try:
        return _run(settings)
    except AiQuotaError:
        print("\nYour AI provider account has no credit left. Add credit at")
        print(f"{BILLING_PAGES[settings.ai_provider]} then run this again.")
        return 1
    except AiError as exc:
        print(f"\nThe provider call failed: {type(exc).__name__} ({exc.user_message})")
        return 1


def _report(model: str, usage: Usage, settings: Settings) -> None:
    cost = estimate_cost(model, usage, settings.ai_price_overrides)
    print(f"   usage: {usage}  est. cost: ${cost}")


def _sample_image() -> bytes:
    image = Image.new("RGB", (900, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.text(
        (40, 110), "Weight: 70.5 kg", fill="black", font=ImageFont.load_default(72)
    )
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _check_structured(provider: Any, model: str, settings: Settings) -> bool:
    print(f"\n2. structured output ({model})")
    raw = provider.structured(
        model=model,
        system="Answer with the sum only.",
        messages=[UserMessage("What is 19 + 23?")],
        schema=Sum,
        max_output_tokens=MAX_TOKENS,
        reasoning_effort=None,
        user_ref=None,
    )
    print(f"   parsed: {raw.parsed}  refusal: {raw.refusal}  error: {raw.error}")
    _report(model, raw.usage, settings)
    return isinstance(raw.parsed, Sum) and raw.parsed.total == 42


def _check_tools(provider: Any, model: str, settings: Settings) -> bool:
    print(f"\n3. tool call, then stateless replay ({model})")
    tool = ToolSpec(
        "add",
        "Add two integers.",
        {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    )
    common: dict[str, Any] = {
        "model": model,
        "system": "Use the add tool for arithmetic, then state the result briefly.",
        "tools": [tool],
        "max_output_tokens": MAX_TOKENS,
        "reasoning_effort": None,
        "user_ref": None,
    }
    history: list[Any] = [UserMessage("What is 19 + 23? Use the tool.")]
    first = provider.chat(messages=history, **common)
    print(f"   turn 1 tool calls: {[(c.name, c.arguments) for c in first.tool_calls]}")
    if not first.tool_calls:
        return False
    call = first.tool_calls[0]
    total = int(call.arguments.get("a", 0)) + int(call.arguments.get("b", 0))
    history += [
        AssistantMessage(first.text, first.tool_calls, first.provider_items),
        ToolResultMessage(call.id, str(total)),
    ]
    second = provider.chat(messages=history, **common)
    print(f"   turn 2 answer: {second.text!r}")
    _report(model, first.usage + second.usage, settings)
    return "42" in second.text


def _check_stream(provider: Any, model: str, settings: Settings) -> bool:
    print(f"\n4. streaming ({model})")
    deltas, usage = 0, Usage()
    text = ""
    for event in provider.stream_chat(
        model=model,
        system="Reply with one short sentence.",
        messages=[UserMessage("Say hello to a gym member.")],
        tools=[],
        max_output_tokens=MAX_TOKENS,
        reasoning_effort=None,
        user_ref=None,
    ):
        if isinstance(event, TextDelta):
            deltas += 1
        elif isinstance(event, StreamDone):
            usage, text = event.result.usage, event.result.text
    print(f"   {deltas} chunks, final text: {text!r}")
    _report(model, usage, settings)
    return deltas > 0 and bool(text)


def _check_vision(provider: Any, model: str, settings: Settings) -> bool:
    print(f"\n5. image to strict JSON ({model})")
    raw = provider.structured(
        model=model,
        system=with_security_preamble("Read only what is printed in the image."),
        messages=[
            UserMessage("What weight is printed?", (prepare_image(_sample_image()),))
        ],
        schema=PrintedWeight,
        max_output_tokens=MAX_TOKENS,
        reasoning_effort=None,
        user_ref=None,
    )
    print(f"   parsed: {raw.parsed}  refusal: {raw.refusal}  error: {raw.error}")
    _report(model, raw.usage, settings)
    return isinstance(raw.parsed, PrintedWeight) and raw.parsed.weight_kg == 70.5


def _run(settings: Settings) -> int:
    models = {
        task: {
            Task.MAIN: settings.ai_model_main,
            Task.VISION: settings.ai_model_vision,
            Task.FAST: settings.ai_model_fast,
        }[task]
        or DEFAULT_MODELS[settings.ai_provider][task]
        for task in Task
    }
    print(f"provider: {settings.ai_provider}")
    for task, model in models.items():
        print(f"  {task.value:<7} model: {model}")
    print(f"  daily token budget per user: {settings.ai_daily_token_budget}")

    if settings.ai_provider == "demo":
        print("\nThe demo provider is offline and fixed: there is nothing to check.")
        return 0
    key = api_key_for(settings)
    if not key:
        variable = KEY_VARIABLES[settings.ai_provider]
        print(f"\n{variable} is empty. Add it to backend/.env and run this again.")
        return 2
    print(f"  api key: set ({len(key)} characters, not shown)")

    provider: Any = build_provider(settings)

    print("\n1. models available to this key")
    try:
        available = {m.id for m in provider.client.models.list()}
    except openai.OpenAIError as exc:
        print(f"   could not list models: {type(exc).__name__}")
        return 1
    unique = list(dict.fromkeys(models.values()))
    for model in unique:
        print(f"   {'OK     ' if model in available else 'MISSING'} {model}")
    if any(model not in available for model in unique):
        print(
            "   Set AI_MODEL_MAIN / AI_MODEL_VISION / AI_MODEL_FAST in backend/.env to"
        )
        print("   models your account can use. Available models:")
        for name in sorted(available)[:40]:
            print(f"     {name}")
        return 1

    results = {
        "structured output": _check_structured(provider, models[Task.FAST], settings),
        "tool call + replay": _check_tools(provider, models[Task.MAIN], settings),
        "streaming": _check_stream(provider, models[Task.FAST], settings),
        "image to strict JSON": _check_vision(provider, models[Task.VISION], settings),
    }
    print("\nresult:")
    for name, passed in results.items():
        print(f"   {name}: {'PASS' if passed else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
