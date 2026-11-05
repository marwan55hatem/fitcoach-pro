"""Hard limits for coaching changes, and the check every proposal must pass.

validate_recommendation is the gate for ANY proposal, from these rules or written by an
AI: it re-checks the payload against the user's current state and refuses anything
outside the limits. Nothing is applied without passing it and the user's approval.
"""

import enum
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from app.models.enums import Goal
from app.rules.nutrition import (
    GOAL_ADJUSTMENT_KCAL,
    MAX_CALORIE_ADJUSTMENT,
    PROTEIN_RANGE_G_PER_KG,
)

MAX_INCREASE_KG: Final = 2.5  # one compound step per change
MAX_DECREASE_SHARE: Final = 0.10
MAX_DECREASE_KG_FLOOR: Final = 2.5  # light weights may still drop by one plate step
MAX_LOAD_KG: Final = 500.0
MAX_CALORIE_CHANGE_KCAL: Final = 300  # per change
MAX_TOTAL_ADJUSTMENT_KCAL: Final = MAX_CALORIE_ADJUSTMENT  # on top of the goal's own
BULK_GOAL_SURPLUS_KCAL: Final = GOAL_ADJUSTMENT_KCAL[Goal.LEAN_BULK]
DELOAD_WEEKS_RANGE: Final = (1, 2)
DELOAD_SET_FACTOR_RANGE: Final = (0.4, 0.7)
STALL_FIXES: Final = frozenset({"vary_or_add_set", "reduce_and_rebuild"})

__all__ = [
    "BULK_GOAL_SURPLUS_KCAL",
    "DELOAD_SET_FACTOR_RANGE",
    "DELOAD_WEEKS_RANGE",
    "MAX_CALORIE_CHANGE_KCAL",
    "MAX_DECREASE_KG_FLOOR",
    "MAX_DECREASE_SHARE",
    "MAX_INCREASE_KG",
    "MAX_TOTAL_ADJUSTMENT_KCAL",
    "PROTEIN_RANGE_G_PER_KG",
    "STALL_FIXES",
    "Kind",
    "UserState",
    "Verdict",
    "allowed_calorie_change",
    "validate_recommendation",
]


class Kind(enum.StrEnum):
    LOAD_CHANGE = "load_change"  # next target weight for one exercise
    CALORIE_CHANGE = "calorie_change"  # change to the approved calorie adjustment
    PROTEIN_CHANGE = "protein_change"  # protein per kg override
    DELOAD = "deload"  # a lighter week
    SET_CUT = "set_cut"  # one set less per exercise for one session
    STALL_FIX = "stall_fix"  # advice only, no numbers


@dataclass(frozen=True)
class UserState:
    """What a proposal is checked against, as it is NOW (it may have changed since)."""

    goal: Goal
    weight_kg: float
    calorie_adjustment_kcal: int  # the approved adjustment in nutrition_settings
    protein_g_per_kg: float
    targets_withheld: bool  # the health check withholds numeric diet targets


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str  # "ok" or why it was refused


_OK = Verdict(True, "ok")
_KEYS: Final[dict[str, frozenset[str]]] = {
    Kind.LOAD_CHANGE: frozenset({"exercise_id", "action", "from_kg", "to_kg"}),
    Kind.CALORIE_CHANGE: frozenset({"change_kcal"}),
    Kind.PROTEIN_CHANGE: frozenset({"g_per_kg"}),
    Kind.DELOAD: frozenset({"weeks", "set_factor"}),
    Kind.SET_CUT: frozenset({"sets_delta"}),
    Kind.STALL_FIX: frozenset({"exercise_id", "fix"}),
}


class _Invalid(Exception):
    pass


def _number(value: Any) -> float:
    """A finite int or float; bools, strings and NaN are refused."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _Invalid
    if not math.isfinite(value):
        raise _Invalid
    return float(value)


def _integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _Invalid
    return value


def allowed_calorie_change(state: UserState, change: int) -> int:
    """The part of a calorie change that fits every limit (0 if none does)."""
    wanted = change
    change = max(-MAX_CALORIE_CHANGE_KCAL, min(MAX_CALORIE_CHANGE_KCAL, change))
    total = state.calorie_adjustment_kcal + change
    total = max(-MAX_TOTAL_ADJUSTMENT_KCAL, min(MAX_TOTAL_ADJUSTMENT_KCAL, total))
    if state.goal is Goal.LEAN_BULK:
        total = max(total, -BULK_GOAL_SURPLUS_KCAL)  # never below maintenance
    change = total - state.calorie_adjustment_kcal
    # Nothing left, or only a move the other way (the state was past a limit already).
    if change == 0 or (change > 0) != (wanted > 0):
        return 0
    return change


def _load(payload: Mapping[str, Any]) -> Verdict:
    if not isinstance(payload["exercise_id"], str) or not payload["exercise_id"]:
        raise _Invalid
    before, after = _number(payload["from_kg"]), _number(payload["to_kg"])
    if not 0 <= before <= MAX_LOAD_KG or not 0 <= after <= MAX_LOAD_KG:
        raise _Invalid
    delta = after - before
    if payload["action"] == "add":
        if delta <= 0:
            return Verdict(False, "wrong_direction")
        if delta > MAX_INCREASE_KG + 1e-9:
            return Verdict(False, "increase_too_big")
        return _OK
    if payload["action"] == "reduce":
        if delta >= 0:
            return Verdict(False, "wrong_direction")
        if -delta > max(MAX_DECREASE_SHARE * before, MAX_DECREASE_KG_FLOOR) + 1e-9:
            return Verdict(False, "decrease_too_big")
        return _OK
    raise _Invalid


def _calories(payload: Mapping[str, Any], state: UserState) -> Verdict:
    change = _integer(payload["change_kcal"])
    if change == 0:
        raise _Invalid
    if abs(change) > MAX_CALORIE_CHANGE_KCAL:
        return Verdict(False, "change_too_big")
    total = state.calorie_adjustment_kcal + change
    if abs(total) > MAX_TOTAL_ADJUSTMENT_KCAL:
        return Verdict(False, "total_out_of_range")
    if state.goal is Goal.LEAN_BULK and BULK_GOAL_SURPLUS_KCAL + total < 0:
        return Verdict(False, "below_maintenance_on_bulk")
    return _OK


def _protein(payload: Mapping[str, Any]) -> Verdict:
    low, high = PROTEIN_RANGE_G_PER_KG
    if not low <= _number(payload["g_per_kg"]) <= high:
        return Verdict(False, "protein_out_of_range")
    return _OK


def _deload(payload: Mapping[str, Any]) -> Verdict:
    weeks, factor = _integer(payload["weeks"]), _number(payload["set_factor"])
    if not (
        DELOAD_WEEKS_RANGE[0] <= weeks <= DELOAD_WEEKS_RANGE[1]
        and DELOAD_SET_FACTOR_RANGE[0] <= factor <= DELOAD_SET_FACTOR_RANGE[1]
    ):
        return Verdict(False, "deload_out_of_range")
    return _OK


def _set_cut(payload: Mapping[str, Any]) -> Verdict:
    if _integer(payload["sets_delta"]) != -1:
        return Verdict(False, "cut_too_big")  # one set less, never more
    return _OK


def _stall(payload: Mapping[str, Any]) -> Verdict:
    if not isinstance(payload["exercise_id"], str) or not payload["exercise_id"]:
        raise _Invalid
    if payload["fix"] not in STALL_FIXES:
        raise _Invalid
    return _OK


def validate_recommendation(
    kind: str, payload: Mapping[str, Any], state: UserState
) -> Verdict:
    """Allow a proposal only if it is well formed and inside every hard limit."""
    if kind not in _KEYS:
        return Verdict(False, "unknown_kind")
    if not isinstance(payload, Mapping) or set(payload) != _KEYS[kind]:
        return Verdict(False, "invalid_payload")
    diet = kind in (Kind.CALORIE_CHANGE, Kind.PROTEIN_CHANGE)
    if diet and state.targets_withheld:
        return Verdict(False, "targets_withheld")
    try:
        match kind:
            case Kind.LOAD_CHANGE:
                return _load(payload)
            case Kind.CALORIE_CHANGE:
                return _calories(payload, state)
            case Kind.PROTEIN_CHANGE:
                return _protein(payload)
            case Kind.DELOAD:
                return _deload(payload)
            case Kind.SET_CUT:
                return _set_cut(payload)
            case _:
                return _stall(payload)
    except _Invalid:
        return Verdict(False, "invalid_payload")
