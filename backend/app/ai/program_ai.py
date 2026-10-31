"""AI program proposals. The AI only proposes: app/rules decides what is acceptable.

Flow: ask the model, validate; if the rules find errors, ask once more with those errors;
if it still fails, or the AI is unavailable, fall back to the pure-rules template.
Only training facts are sent: no name, email, birth date or injury free text.
"""

import json
import logging
import uuid
from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.ai.client import AiClient
from app.ai.errors import AiError
from app.ai.types import AssistantMessage, Message, Task, UserMessage
from app.ai.untrusted import with_security_preamble
from app.models.enums import Mechanic
from app.rules.exercise_catalog import CatalogExercise
from app.rules.program_validator import (
    MAX_REST_S,
    MAX_SETS,
    MIN_REST_S,
    VOLUME_RANGES,
    DayDraft,
    ExerciseDraft,
    ProgramContext,
    ProgramDraft,
    Violation,
    errors_in,
    validate_program,
    warnings_in,
)
from app.rules.templates import (
    SplitTemplate,
    build_template_program,
    scheme_for,
    shortlist_for_ai,
)

logger = logging.getLogger("app.ai")

MAX_OUTPUT_TOKENS = 5000
SYSTEM_PROMPT = with_security_preamble(
    "You are an experienced strength coach designing a weekly gym program. Use only "
    "exercises from the numbered list, referring to them by their number. Follow the "
    "template's days and the limits you are given. Put big compound lifts first each day. "
    "Keep each rationale to one short, plain sentence. Do not give medical advice."
)


class AiExercise(BaseModel):
    exercise_ref: int = Field(description="the number of the exercise in the list")
    sets: int
    rep_min: int
    rep_max: int
    rest_s: int
    notes: str | None = Field(description="a short coaching cue, or null")


class AiDay(BaseModel):
    name: str
    focus: str
    rationale: str
    exercises: list[AiExercise]


class AiProgram(BaseModel):
    rationale: str
    days: list[AiDay]


@dataclass(frozen=True)
class TrainingProfile:
    """What the model may know about the user. Deliberately no identity."""

    age: int
    sex: str
    height_cm: float
    weight_kg: float
    goal: str
    experience: str
    days_per_week: int
    session_minutes: int


@dataclass(frozen=True)
class ProgramProposal:
    draft: ProgramDraft
    source: str  # "ai" or "template"
    warnings: tuple[Violation, ...]
    fallback_reason: str | None = None  # "ai_unavailable" or "ai_invalid"


def _exercise_line(number: int, exercise: CatalogExercise) -> str:
    mechanic = "compound" if exercise.mechanic is Mechanic.COMPOUND else "isolation"
    secondary = ",".join(exercise.muscles_secondary) or "-"
    return f"{number}|{exercise.name}|{exercise.muscle_primary}|{secondary}|{mechanic}"


def _request_text(
    profile: TrainingProfile,
    template: SplitTemplate,
    ctx: ProgramContext,
    shortlist: list[CatalogExercise],
) -> str:
    minimum, maximum = VOLUME_RANGES[ctx.experience]
    compound = scheme_for(ctx.experience, True)
    isolation = scheme_for(ctx.experience, False)
    days = template.days[: ctx.days_per_week]
    template_days = "; ".join(f"{d.name} ({d.focus})" for d in days)
    limits = (
        f"Limits: exactly {len(days)} days; each session at most "
        f"{ctx.session_minutes} minutes including 5 warm-up minutes, 45 seconds per "
        "set plus rest, and 1 minute between exercises; "
        f"{minimum} to {maximum} weekly sets per muscle, where each set also counts "
        "half for every secondary muscle, so presses and rows add up on the "
        f"shoulders; at most {MAX_SETS} sets per exercise; rest "
        f"{MIN_REST_S} to {MAX_REST_S} seconds."
    )
    typical = (
        f"Typical compound: {compound.sets} sets of {compound.rep_min}-"
        f"{compound.rep_max}, rest {compound.rest_s}s. Typical isolation: "
        f"{isolation.sets} sets of {isolation.rep_min}-{isolation.rep_max}, rest "
        f"{isolation.rest_s}s."
    )
    return "\n".join(
        [
            f"User: {json.dumps(profile.__dict__)}",
            f"Avoid body areas: {[lim.body_area.value for lim in ctx.limitations]}",
            f"Template '{template.name}' days: {template_days}",
            limits,
            typical,
            "Exercises (number|name|primary muscle|secondary muscles|type):",
            *(_exercise_line(i, e) for i, e in enumerate(shortlist, start=1)),
        ]
    )


def _to_draft(answer: AiProgram, shortlist: list[CatalogExercise]) -> ProgramDraft:
    def exercise_id(ref: int) -> str:
        # An out-of-list number stays unknown, so the validator reports it.
        return shortlist[ref - 1].id if 1 <= ref <= len(shortlist) else f"unknown:{ref}"

    return ProgramDraft(
        answer.rationale,
        tuple(
            DayDraft(
                day.name,
                day.focus,
                day.rationale,
                tuple(
                    ExerciseDraft(
                        exercise_id(item.exercise_ref),
                        item.sets,
                        item.rep_min,
                        item.rep_max,
                        item.rest_s,
                        item.notes,
                    )
                    for item in day.exercises
                ),
            )
            for day in answer.days
        ),
    )


def template_proposal(
    template: SplitTemplate, ctx: ProgramContext, reason: str | None
) -> ProgramProposal:
    draft = build_template_program(template, ctx)
    return ProgramProposal(
        draft, "template", tuple(warnings_in(validate_program(draft, ctx))), reason
    )


def propose_program(
    ai: AiClient,
    *,
    user_id: uuid.UUID,
    profile: TrainingProfile,
    template: SplitTemplate,
    ctx: ProgramContext,
) -> ProgramProposal:
    shortlist = shortlist_for_ai(template, ctx)
    if not shortlist:
        return template_proposal(template, ctx, "ai_unavailable")

    messages: list[Message] = [
        UserMessage(_request_text(profile, template, ctx, shortlist))
    ]
    try:
        for attempt in (1, 2):
            answer = ai.structured(
                user_id=user_id,
                task=Task.MAIN,
                schema=AiProgram,
                system=SYSTEM_PROMPT,
                messages=messages,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                reasoning_effort="low",  # leaves room for the JSON within the limit
            )
            draft = _to_draft(answer, shortlist)
            violations = validate_program(draft, ctx)
            errors = errors_in(violations)
            if not errors:
                return ProgramProposal(draft, "ai", tuple(warnings_in(violations)))
            logger.warning(
                "program_ai_rejected attempt=%d errors=%d codes=%s",
                attempt,
                len(errors),
                sorted({e.code for e in errors}),
            )
            problems = "\n".join(f"- {e.message}" for e in errors[:15])
            messages = [
                messages[0],
                AssistantMessage(answer.model_dump_json()),
                UserMessage(
                    "That program broke these rules. Return a corrected full program.\n"
                    + problems
                ),
            ]
    except AiError as exc:
        logger.warning("program_ai_unavailable error=%s", type(exc).__name__)
        return template_proposal(template, ctx, "ai_unavailable")
    return template_proposal(template, ctx, "ai_invalid")
