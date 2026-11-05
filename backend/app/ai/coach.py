"""The AI coach: a streamed tool-use loop through AiClient.

The model reads the user's data through tools scoped to that user and can only propose
changes (checked by the rules engine, then approved by the user). Red flags are handled
before this runs (app/rules/redflags.py). Database work happens inside each tool call
with its own short session, never across a model call.
"""

import datetime as dt
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.ai.client import AiClient
from app.ai.types import (
    AssistantMessage,
    Message,
    StreamDone,
    Task,
    TextDelta,
    ToolResultMessage,
    Usage,
    UserMessage,
)
from app.ai.untrusted import with_security_preamble, wrap_untrusted
from app.services.coach_tools import TOOL_SPECS, run_tool

MAX_ITERATIONS = 8  # model turns per user message; the last one may not use tools
MAX_OUTPUT_TOKENS = 1500
SUMMARY_MAX_TOKENS = 400
COACH_PROMPT = (Path(__file__).parent / "prompts" / "coach.md").read_text(
    encoding="utf-8"
)
SUMMARY_PROMPT = with_security_preamble(
    "Summarize this chat between a user and their fitness coach in at most 120 words, "
    "in English: the user's questions, the advice given and anything still open. "
    "Keep facts and numbers exactly; add nothing."
)
CAP_NOTE = "\n\n(I looked at a lot of your data for this one. Ask me again if anything is missing.)"

SessionFactory = Callable[[], AbstractContextManager[Session]]


@dataclass(frozen=True)
class CoachContext:
    """What the coach knows about the situation. No name or email."""

    language: str  # "en" or "ar"
    today: dt.date
    risk_level: str | None
    training_allowed: bool
    diet_numbers_allowed: bool
    safety_mode: str | None  # a red flag earlier in this conversation
    summary: str | None  # of older messages in this conversation


def system_prompt(ctx: CoachContext) -> str:
    language = "Egyptian Arabic" if ctx.language == "ar" else "English"
    lines = [
        COACH_PROMPT,
        "Context:",
        f"- Reply in: {language}.",
        f"- Today: {ctx.today.isoformat()}.",
        f"- Health check: {ctx.risk_level or 'not answered'}.",
    ]
    if not ctx.training_allowed:
        lines.append(
            "- Training plans are on hold until the health check is cleared: do not "
            "give or change workouts; encourage seeing a doctor."
        )
    if not ctx.diet_numbers_allowed:
        lines.append(
            "- Diet numbers are withheld: never give calories, macros or food weights."
        )
    if ctx.safety_mode:
        lines.append(
            f"- Earlier in this chat a safety concern came up ({ctx.safety_mode}): stay "
            "careful, no numeric advice on that topic, and recommend a professional."
        )
    prompt = "\n".join(lines)
    if ctx.summary:
        prompt += "\n\nEarlier in this conversation:\n" + wrap_untrusted(
            ctx.summary, "chat_summary"
        )
    return with_security_preamble(prompt)


# --- events -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Delta:
    text: str


@dataclass(frozen=True)
class ToolUse:
    name: str


@dataclass(frozen=True)
class Proposal:
    card: dict[str, Any]


@dataclass(frozen=True)
class Finished:
    text: str
    usage: Usage
    tool_calls: list[str] = field(default_factory=list)  # names, for the record
    capped: bool = False


CoachEvent = Delta | ToolUse | Proposal | Finished


def run_coach(
    ai: AiClient,
    sessions: SessionFactory,
    user_id: uuid.UUID,
    *,
    ctx: CoachContext,
    history: list[Message],
    message: UserMessage,
) -> Iterator[CoachEvent]:
    """Stream one coach reply, running tools as the model asks for them."""
    system = system_prompt(ctx)
    messages: list[Message] = [*history, message]
    text: list[str] = []
    used: list[str] = []
    usage = Usage()
    for iteration in range(MAX_ITERATIONS):
        last = iteration == MAX_ITERATIONS - 1
        result = None
        for event in ai.stream_chat(
            user_id=user_id,
            task=Task.MAIN,
            system=system,
            messages=messages,
            tools=() if last else TOOL_SPECS,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        ):
            if isinstance(event, TextDelta):
                text.append(event.text)
                yield Delta(event.text)
            elif isinstance(event, StreamDone):
                result = event.result
        assert result is not None
        usage = usage + result.usage
        if not result.tool_calls:
            yield Finished("".join(text), usage, used)
            return
        if last:  # cannot happen: no tools were offered, but never loop forever
            break
        messages.append(
            AssistantMessage(result.text, result.tool_calls, result.provider_items)
        )
        for call in result.tool_calls:
            used.append(call.name)
            yield ToolUse(call.name)
            with sessions() as db:
                outcome = run_tool(db, user_id, call.name, call.arguments)
            if outcome.proposal is not None:
                yield Proposal(outcome.proposal)
            messages.append(ToolResultMessage(call.id, outcome.content))
    text.append(CAP_NOTE)
    yield Delta(CAP_NOTE)
    yield Finished("".join(text), usage, used, capped=True)


def summarize(
    ai: AiClient, user_id: uuid.UUID, previous: str | None, lines: list[str]
) -> str:
    """Fold older messages into the rolling summary (a cheap model, no tools)."""
    transcript = "\n".join(lines)
    if previous:
        transcript = f"Summary so far: {previous}\n\n{transcript}"
    result = ai.chat(
        user_id=user_id,
        task=Task.FAST,
        system=SUMMARY_PROMPT,
        messages=[UserMessage(wrap_untrusted(transcript, "chat_history"))],
        max_output_tokens=SUMMARY_MAX_TOKENS,
    )
    return result.text.strip()[:2000]
