"""Readiness from check-ins, and when to take a lighter week (pure)."""

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import mean
from typing import Final

READINESS_LOW: Final = 40  # below this, one set less per exercise today
FATIGUE_SCORE: Final = 60  # a whole week under this after a long block is fatigue
HARD_WEEKS_DELOAD: Final = 6
HARD_WEEKS_FATIGUE: Final = 4
MIN_WEEK_CHECKINS: Final = 3  # fewer days than this cannot describe a week
SLEEP_RANGE_H: Final = (4.0, 8.0)  # 4 h scores 0, 8 h or more scores full
DELOAD_SET_FACTOR: Final = 0.5


@dataclass(frozen=True)
class Checkin:
    date: dt.date
    sleep_h: float | None
    soreness: float | None  # 1 to 5, higher is worse
    stress: float | None  # 1 to 5, higher is worse
    energy: float | None  # 1 to 5, higher is better
    motivation: float | None  # 1 to 5, higher is better


@dataclass(frozen=True)
class Readiness:
    score: int  # 0 to 100
    low: bool


@dataclass(frozen=True)
class Deload:
    reason: str  # long_block | low_readiness | accumulated_fatigue
    weeks: int
    set_factor: float  # share of the usual sets; the weights stay the same
    message: str


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def readiness(checkins: Sequence[Checkin]) -> Readiness | None:
    """A 0-100 score from the latest check-in; only answered questions count."""
    if not checkins:
        return None
    latest = max(checkins, key=lambda c: c.date)
    parts: list[float] = []
    if latest.sleep_h is not None:
        low, high = SLEEP_RANGE_H
        parts.append(_clamp((latest.sleep_h - low) / (high - low)))
    for good in (latest.energy, latest.motivation):
        if good is not None:
            parts.append(_clamp((good - 1) / 4))
    for bad in (latest.soreness, latest.stress):
        if bad is not None:
            parts.append(_clamp((5 - bad) / 4))
    if not parts:
        return None
    score = round(mean(parts) * 100)
    return Readiness(score, score < READINESS_LOW)


def session_sets(planned: int, today: Readiness | None) -> int:
    """Low readiness takes one set off each exercise today, never more, never to 0."""
    if today is not None and today.low:
        return max(1, planned - 1)
    return planned


def deload(hard_weeks: int, readiness_scores: Sequence[int]) -> Deload | None:
    """A lighter week after a long hard block, or when readiness stayed low a week.

    `hard_weeks` counts consecutive weeks of full training; `readiness_scores` are the
    last seven days' scores.
    """
    week = (
        mean(readiness_scores) if len(readiness_scores) >= MIN_WEEK_CHECKINS else None
    )
    if hard_weeks >= HARD_WEEKS_DELOAD:
        reason = "long_block"
        message = f"{hard_weeks} hard weeks in a row: a lighter week lets you recover and grow."
    elif week is not None and week < READINESS_LOW:
        reason = "low_readiness"
        message = "Your readiness has been low all week: take a lighter week."
    elif week is not None and hard_weeks >= HARD_WEEKS_FATIGUE and week < FATIGUE_SCORE:
        reason = "accumulated_fatigue"
        message = "Several hard weeks and lower readiness: a lighter week now pays off."
    else:
        return None
    return Deload(
        reason, 1, DELOAD_SET_FACTOR, message + " Same weights, half the sets."
    )
