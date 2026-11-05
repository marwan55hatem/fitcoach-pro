"""Calorie adjustments from the weight trend and InBody changes (pure).

Every suggestion is cut down to what the hard limits allow (app/rules/limits.py), and
none is made while the health check withholds numeric diet targets.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.models.enums import Goal
from app.rules.limits import UserState, allowed_calorie_change

BULK_TARGET_KG_PER_WEEK: Final = (0.25, 0.5)
BULK_TOO_SLOW: Final = 0.15
BULK_TOO_FAST: Final = 0.7
CUT_TOO_SLOW: Final = -0.1
CUT_TOO_FAST: Final = -1.0
DRIFT_KG_PER_WEEK: Final = 0.35  # maintain / recomp
FAT_FASTER_KG: Final = 0.5  # fat gain this much above muscle gain between scans
TREND_WEEKS: Final = 2
ADD_SLOW_BULK: Final = 250
CUT_SURPLUS: Final = -150
CUT_DEEPER: Final = -150
EASE_CUT: Final = 150
DRIFT_FIX: Final = 100


@dataclass(frozen=True)
class InbodyChange:
    """The change between the last two confirmed scans."""

    smm_change_kg: float
    fat_change_kg: float


@dataclass(frozen=True)
class CalorieAdvice:
    change_kcal: int
    reason: str
    message: str


def _trend(changes: Sequence[float], test) -> bool:
    recent = list(changes)[-TREND_WEEKS:]
    return len(recent) == TREND_WEEKS and all(test(c) for c in recent)


def _wanted(
    goal: Goal, changes: Sequence[float], inbody: InbodyChange | None
) -> tuple[int, str, str] | None:
    last = changes[-1] if changes else None
    if goal is Goal.LEAN_BULK:
        if (
            inbody is not None
            and last is not None
            and last > 0
            and inbody.fat_change_kg > 0
            and inbody.fat_change_kg - inbody.smm_change_kg >= FAT_FASTER_KG
        ):
            return (
                CUT_SURPLUS,
                "fat_rising_faster",
                (
                    "Your last scans show fat rising much faster than muscle: a smaller "
                    "surplus keeps the gain lean."
                ),
            )
        if last is not None and last > BULK_TOO_FAST:
            return (
                CUT_SURPLUS,
                "gaining_too_fast",
                (
                    f"You gained {last:.2f} kg last week, above the 0.25-0.5 kg target: "
                    "a little less food keeps it lean."
                ),
            )
        if _trend(changes, lambda c: c < BULK_TOO_SLOW):
            return (
                ADD_SLOW_BULK,
                "gaining_too_slowly",
                (
                    "Under 0.15 kg a week for two weeks: eat a bit more to keep building."
                ),
            )
    elif goal is Goal.CUT:
        if last is not None and last < CUT_TOO_FAST:
            return (
                EASE_CUT,
                "losing_too_fast",
                ("Over 1 kg lost last week: eat a little more to protect your muscle."),
            )
        if _trend(changes, lambda c: c > CUT_TOO_SLOW):
            return (
                CUT_DEEPER,
                "losing_too_slowly",
                (
                    "Weight has been flat for two weeks: a slightly bigger deficit restarts the loss."
                ),
            )
    elif _trend(changes, lambda c: c > DRIFT_KG_PER_WEEK):
        return -DRIFT_FIX, "drifting_up", "Weight is creeping up: trim a little."
    elif _trend(changes, lambda c: c < -DRIFT_KG_PER_WEEK):
        return DRIFT_FIX, "drifting_down", "Weight is slipping down: add a little."
    return None


def calorie_adjustment(
    state: UserState, weekly_changes: Sequence[float], inbody: InbodyChange | None
) -> CalorieAdvice | None:
    """A change to the calorie adjustment, or None. `weekly_changes` are kg per week,
    oldest first."""
    if state.targets_withheld:
        return None
    wanted = _wanted(state.goal, weekly_changes, inbody)
    if wanted is None:
        return None
    change, reason, message = wanted
    allowed = allowed_calorie_change(state, change)
    if allowed == 0:
        return None
    return CalorieAdvice(allowed, reason, message)
