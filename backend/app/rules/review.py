"""The weekly review: every rule's suggestion as typed recommendations (pure).

Each recommendation is checked by validate_recommendation before it is returned, and
the user approves each one before anything changes.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.models.enums import Mechanic
from app.rules.calories import InbodyChange, calorie_adjustment
from app.rules.limits import Kind, UserState, validate_recommendation
from app.rules.progression import Action, SessionResult, progression, stall_detection
from app.rules.recovery import deload


@dataclass(frozen=True)
class ExerciseHistory:
    exercise_id: str
    name: str
    rep_min: int
    rep_max: int
    mechanic: Mechanic
    sessions: tuple[SessionResult, ...]  # oldest first


@dataclass(frozen=True)
class ReviewInput:
    state: UserState
    exercises: tuple[ExerciseHistory, ...]
    weekly_changes: tuple[float, ...]  # kg per week, oldest first
    inbody: InbodyChange | None  # between the last two confirmed scans
    hard_weeks: int  # consecutive weeks of full training
    readiness_scores: tuple[int, ...]  # the last seven days


@dataclass(frozen=True)
class Recommendation:
    kind: Kind
    payload: Mapping[str, Any]
    reason: str  # a stable code, for translation and analytics
    message: str  # a plain-English explanation


def propose(inp: ReviewInput) -> list[Recommendation]:
    """Every rule's suggestion, in a stable order: loads, stalls, calories, deload."""
    lighter_week = deload(inp.hard_weeks, inp.readiness_scores)
    out: list[Recommendation] = []
    for ex in inp.exercises:
        step = progression(ex.sessions, ex.rep_min, ex.rep_max, ex.mechanic)
        if step.action is not Action.KEEP and step.weight_kg is not None:
            if step.action is Action.ADD and lighter_week is not None:
                continue  # no heavier weights in a lighter week
            out.append(
                Recommendation(
                    Kind.LOAD_CHANGE,
                    {
                        "exercise_id": ex.exercise_id,
                        "action": step.action.value,
                        "from_kg": round(step.weight_kg - step.change_kg, 2),
                        "to_kg": step.weight_kg,
                    },
                    step.reason,
                    f"{ex.name}: {step.message}",
                )
            )
            continue
        stall = stall_detection(ex.sessions, ex.rep_min, ex.rep_max)
        if stall is not None:
            out.append(
                Recommendation(
                    Kind.STALL_FIX,
                    {"exercise_id": ex.exercise_id, "fix": stall.fix},
                    "stalled",
                    f"{ex.name}: {stall.message}",
                )
            )
    advice = calorie_adjustment(inp.state, inp.weekly_changes, inp.inbody)
    if advice is not None:
        out.append(
            Recommendation(
                Kind.CALORIE_CHANGE,
                {"change_kcal": advice.change_kcal},
                advice.reason,
                advice.message,
            )
        )
    if lighter_week is not None:
        out.append(
            Recommendation(
                Kind.DELOAD,
                {"weeks": lighter_week.weeks, "set_factor": lighter_week.set_factor},
                lighter_week.reason,
                lighter_week.message,
            )
        )
    return out


def weekly_review(inp: ReviewInput) -> list[Recommendation]:
    """The week's recommendations, each re-checked against the hard limits."""
    return [
        r
        for r in propose(inp)
        if validate_recommendation(r.kind.value, r.payload, inp.state).allowed
    ]
