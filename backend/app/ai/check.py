"""Prove the AI wiring end to end against the real provider.

    python -m app.ai.check

Reads the key from backend/.env, never prints it, and writes nothing to the database. It:
  1. shows provider, models and budget,
  2. confirms the configured models exist for your key,
  3. makes one tiny structured call,
  4. runs a two-turn tool call to prove stateless (store=False) replay works,
  5. prints token usage and the estimated cost of each step.
"""

import sys

import openai
from pydantic import BaseModel, Field

from app.ai.models import DEFAULT_MODELS, estimate_cost
from app.ai.providers.openai_provider import OpenAiProvider, build_client
from app.ai.types import (
    AssistantMessage,
    Task,
    ToolResultMessage,
    ToolSpec,
    UserMessage,
)
from app.core.config import get_settings


class Sum(BaseModel):
    total: int = Field(description="the sum of the two numbers")


def main() -> int:
    settings = get_settings()
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

    if not settings.openai_api_key:
        print("\nOPENAI_API_KEY is empty. Add it to backend/.env and run this again.")
        return 2
    print(f"  api key: set ({len(settings.openai_api_key)} characters, not shown)")

    provider = OpenAiProvider(
        build_client(settings.openai_api_key, settings.ai_timeout_s)
    )
    overrides = settings.ai_price_overrides

    print("\n1. models available to this key")
    try:
        available = {m.id for m in provider.client.models.list()}
    except openai.OpenAIError as exc:
        print(f"   could not list models: {type(exc).__name__}")
        return 1
    missing = [m for m in dict.fromkeys(models.values()) if m not in available]
    for model in dict.fromkeys(models.values()):
        print(f"   {'OK     ' if model in available else 'MISSING'} {model}")
    if missing:
        print(
            "   Set AI_MODEL_MAIN / AI_MODEL_VISION / AI_MODEL_FAST in backend/.env to models"
        )
        print("   your account can use. Available GPT models:")
        for name in sorted(a for a in available if a.startswith("gpt"))[:25]:
            print(f"     {name}")
        return 1

    model = models[Task.FAST]
    print(f"\n2. structured output ({model})")
    raw = provider.structured(
        model=model,
        system="Answer with the sum only.",
        messages=[UserMessage("What is 19 + 23?")],
        schema=Sum,
        max_output_tokens=2000,
        reasoning_effort=None,
        user_ref=None,
    )
    print(f"   parsed: {raw.parsed}  refusal: {raw.refusal}  error: {raw.error}")
    print(
        f"   usage: {raw.usage}  est. cost: ${estimate_cost(model, raw.usage, overrides)}"
    )
    ok_structured = isinstance(raw.parsed, Sum) and raw.parsed.total == 42

    print(f"\n3. tool call, then replay (store=False) ({model})")
    tool = ToolSpec(
        "add",
        "Add two integers.",
        {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    )
    system = (
        "Use the add tool for arithmetic, then state the result in one short sentence."
    )
    history = [UserMessage("What is 19 + 23? Use the tool.")]
    first = provider.chat(
        model=model,
        system=system,
        messages=history,
        tools=[tool],
        max_output_tokens=2000,
        reasoning_effort=None,
        user_ref=None,
    )
    print(f"   turn 1 tool calls: {[(c.name, c.arguments) for c in first.tool_calls]}")
    ok_tool = bool(first.tool_calls)
    final_text = ""
    usage = first.usage
    if first.tool_calls:
        call = first.tool_calls[0]
        total = int(call.arguments.get("a", 0)) + int(call.arguments.get("b", 0))
        history += [
            AssistantMessage(
                text=first.text,
                tool_calls=first.tool_calls,
                provider_items=first.provider_items,
            ),
            ToolResultMessage(call.id, str(total)),
        ]
        second = provider.chat(
            model=model,
            system=system,
            messages=history,
            tools=[tool],
            max_output_tokens=2000,
            reasoning_effort=None,
            user_ref=None,
        )
        final_text = second.text
        usage = usage + second.usage
        print(f"   turn 2 answer: {final_text!r}")
    ok_tool = ok_tool and "42" in final_text
    print(f"   usage: {usage}  est. cost: ${estimate_cost(model, usage, overrides)}")

    print("\nresult:")
    print(f"   structured output: {'PASS' if ok_structured else 'FAIL'}")
    print(f"   tool call + replay: {'PASS' if ok_tool else 'FAIL'}")
    return 0 if ok_structured and ok_tool else 1


if __name__ == "__main__":
    sys.exit(main())
