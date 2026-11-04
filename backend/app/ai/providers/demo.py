"""An offline stand-in for an AI provider, for local demos and browser tests.

It never touches the network and always gives the same plausible answers, so the
screens can be exercised without an API key or real photos. Settings refuse it outside
APP_ENV=development, so it can never answer a real user.
"""

import datetime as dt
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from pydantic import BaseModel

from app.ai.types import (
    ChatResult,
    Messages,
    RawStructured,
    StreamDone,
    StreamEvent,
    TextDelta,
    ToolSpec,
    Usage,
)

DEMO_MODEL = "demo"
_USAGE = Usage(input_tokens=0, output_tokens=0)
_CHAT_TEXT = "This is the offline demo coach. Connect a real AI provider for answers."


def _reading(value: float | None, confidence: float = 0.95) -> dict[str, Any]:
    return {"value": value, "confidence": confidence if value is not None else 0.0}


def _segments(*values: float) -> dict[str, Any]:
    parts = ("left_arm", "right_arm", "trunk", "left_leg", "right_leg")
    return {part: _reading(v) for part, v in zip(parts, values, strict=True)}


def _inbody() -> dict[str, Any]:
    """A plausible scan for a 193 cm, 72 kg man; it passes every InBody rule."""
    return {
        "test_date": (dt.date.today() - dt.timedelta(days=1)).isoformat(),
        "height_cm": _reading(193),
        "weight_kg": _reading(72.0),
        "skeletal_muscle_mass_kg": _reading(34.2),
        "body_fat_mass_kg": _reading(11.5),
        "percent_body_fat": _reading(16.0),
        "bmi": _reading(19.3),
        "total_body_water_l": _reading(44.5, 0.55),  # a low-confidence field to review
        "bmr_kcal": _reading(1760),
        "visceral_fat_level": _reading(3),
        "inbody_score": _reading(79),
        "segmental_lean_kg": _segments(3.3, 3.4, 26.5, 9.3, 9.4),
        "segmental_fat_kg": None,
    }


def _food() -> dict[str, Any]:
    """A koshari lunch whose calories add up from its macros."""
    return {
        "food_found": True,
        "meal_name": "Koshari with salad",
        "items": [
            {
                "name": "Koshari",
                "grams": 450,
                "units": None,
                "kcal_min": 700,
                "kcal_max": 850,
                "protein_g": 22,
                "carbs_g": 140,
                "fat_g": 15,
            },
            {
                "name": "Green salad",
                "grams": 150,
                "units": None,
                "kcal_min": 40,
                "kcal_max": 70,
                "protein_g": 2,
                "carbs_g": 8,
                "fat_g": 2,
            },
        ],
        "confidence": 0.7,
        "questions": ["Did you add extra oil or dakka on top?"],
    }


_ANSWERS: dict[str, Callable[[], dict[str, Any]]] = {
    "InbodyReading": _inbody,
    "AiFoodEstimate": _food,
}


class DemoProvider:
    name = "demo"

    def structured(
        self,
        *,
        model: str,
        system: str,
        messages: Messages,
        schema: type[BaseModel],
        max_output_tokens: int,
        reasoning_effort: str | None,
        user_ref: str | None,
    ) -> RawStructured:
        answer = _ANSWERS.get(schema.__name__)
        if answer is None:
            return RawStructured(
                error=f"the demo provider has no answer for {schema.__name__}",
                usage=_USAGE,
            )
        return RawStructured(
            parsed=schema.model_validate(answer()), usage=_USAGE, request_id="demo"
        )

    def chat(
        self,
        *,
        model: str,
        system: str,
        messages: Messages,
        tools: Sequence[ToolSpec],
        max_output_tokens: int,
        reasoning_effort: str | None,
        user_ref: str | None,
    ) -> ChatResult:
        return ChatResult(text=_CHAT_TEXT, usage=_USAGE, request_id="demo")

    def stream_chat(
        self,
        *,
        model: str,
        system: str,
        messages: Messages,
        tools: Sequence[ToolSpec],
        max_output_tokens: int,
        reasoning_effort: str | None,
        user_ref: str | None,
    ) -> Iterator[StreamEvent]:
        yield TextDelta(_CHAT_TEXT)
        yield StreamDone(ChatResult(text=_CHAT_TEXT, usage=_USAGE, request_id="demo"))
