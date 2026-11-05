"""Load progression and stall detection for one exercise (pure).

Double progression: stay at a weight until every set reaches the top of the rep range,
then add one step (+2.5 kg compound, +1 kg isolation). Two failed sessions in a row at
the same weight bring it down 5 %, or 10 % when the reps fell well short.
"""

import enum
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.models.enums import Mechanic

STEP_KG: Final = {Mechanic.COMPOUND: 2.5, Mechanic.ISOLATION: 1.0}
REDUCE_SHARE: Final = 0.05
BIG_MISS_REDUCE_SHARE: Final = 0.10
BIG_MISS_REPS: Final = 2  # this many reps under the range is a bad miss
FAILED_SESSIONS_TO_REDUCE: Final = 2
STALL_SESSIONS: Final = 3


@dataclass(frozen=True)
class WorkSet:
    weight_kg: float
    reps: int


@dataclass(frozen=True)
class SessionResult:
    """The completed working sets of one exercise in one workout."""

    performed_on: object  # a date; only the order of sessions matters here
    sets: tuple[WorkSet, ...]


class Action(enum.StrEnum):
    ADD = "add"
    KEEP = "keep"
    REDUCE = "reduce"


@dataclass(frozen=True)
class Progression:
    action: Action
    weight_kg: float | None  # the next target; None without history
    change_kg: float
    reason: str
    message: str


@dataclass(frozen=True)
class Stall:
    sessions: int
    fix: str  # vary_or_add_set | reduce_and_rebuild
    message: str


def working_weight(session: SessionResult) -> float:
    return max(s.weight_kg for s in session.sets)


def hit_top(session: SessionResult, rep_max: int) -> bool:
    return all(s.reps >= rep_max for s in session.sets)


def failed(session: SessionResult, rep_min: int) -> bool:
    return any(s.reps < rep_min for s in session.sets)


def _reduced(weight: float, share: float, step: float) -> float:
    """Down by about `share`, onto the plate step, by at least one step, not below 0."""
    target = math.ceil(weight * (1 - share) / step - 1e-9) * step
    if target >= weight:
        target = weight - step
    return round(max(0.0, target), 2)


def progression(
    history: Sequence[SessionResult], rep_min: int, rep_max: int, mechanic: Mechanic
) -> Progression:
    """What to lift next time, from the sessions so far (oldest first)."""
    done = [s for s in history if s.sets]
    if not done:
        return Progression(
            Action.KEEP,
            None,
            0,
            "no_history",
            "Pick a weight you can lift with good form.",
        )
    last = done[-1]
    weight = working_weight(last)
    step = STEP_KG[mechanic]
    if weight <= 0:
        return Progression(
            Action.KEEP,
            weight,
            0,
            "bodyweight",
            "Body-weight exercise: add a rep or slow the lowering when this gets easy.",
        )
    if hit_top(last, rep_max):
        return Progression(
            Action.ADD,
            round(weight + step, 2),
            step,
            "top_of_range",
            f"Every set reached {rep_max} reps: add {step:g} kg next time.",
        )
    recent = done[-FAILED_SESSIONS_TO_REDUCE:]
    if len(recent) == FAILED_SESSIONS_TO_REDUCE and all(
        failed(s, rep_min) and working_weight(s) == weight for s in recent
    ):
        badly = min(s.reps for s in last.sets) < rep_min - BIG_MISS_REPS
        target = _reduced(
            weight, BIG_MISS_REDUCE_SHARE if badly else REDUCE_SHARE, step
        )
        return Progression(
            Action.REDUCE,
            target,
            round(target - weight, 2),
            "failed_twice_badly" if badly else "failed_twice",
            f"Two sessions under {rep_min} reps at {weight:g} kg: drop to {target:g} kg "
            "and build back up.",
        )
    if failed(last, rep_min):
        return Progression(
            Action.KEEP,
            weight,
            0,
            "missed_once",
            "Some sets fell short: stay at this weight and try again.",
        )
    return Progression(
        Action.KEEP,
        weight,
        0,
        "in_range",
        f"Keep the weight until every set reaches {rep_max} reps.",
    )


def _score(session: SessionResult) -> tuple[float, int]:
    """Heavier wins; at the same weight, more total reps win."""
    weight = working_weight(session)
    return weight, sum(s.reps for s in session.sets if s.weight_kg == weight)


def stall_detection(
    history: Sequence[SessionResult], rep_min: int, rep_max: int
) -> Stall | None:
    """No progress over the last three sessions (and not simply ready to add weight)."""
    done = [s for s in history if s.sets]
    if len(done) < STALL_SESSIONS or hit_top(done[-1], rep_max):
        return None
    window = done[-STALL_SESSIONS:]
    if max(_score(s) for s in window[1:]) > _score(window[0]):
        return None
    if any(failed(s, rep_min) for s in window):
        return Stall(
            STALL_SESSIONS,
            "reduce_and_rebuild",
            "No progress in 3 sessions and sets falling short: drop the weight about "
            "10 % and build back up.",
        )
    return Stall(
        STALL_SESSIONS,
        "vary_or_add_set",
        "No progress in 3 sessions: add a set, or switch to a close variation for a few weeks.",
    )
