"""Body-weight trend: 7-day moving average and week-over-week change (pure)."""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import fmean

WINDOW_DAYS = 7


@dataclass(frozen=True)
class WeightPoint:
    date: dt.date
    weight_kg: float


@dataclass(frozen=True)
class TrendPoint:
    date: dt.date
    weight_kg: float
    average_7d: float


def _in_window(
    points: Iterable[WeightPoint], last: dt.date, days: int = WINDOW_DAYS
) -> list[float]:
    first = last - dt.timedelta(days=days - 1)
    return [p.weight_kg for p in points if first <= p.date <= last]


def moving_average(points: Iterable[WeightPoint]) -> list[TrendPoint]:
    """Each logged day with the mean of the logs in its trailing 7 calendar days.

    Missing days are skipped rather than filled, so a gap never invents a weight.
    """
    ordered = sorted(points, key=lambda p: p.date)
    return [
        TrendPoint(p.date, p.weight_kg, fmean(_in_window(ordered, p.date)))
        for p in ordered
    ]


def weekly_change(points: Iterable[WeightPoint], as_of: dt.date) -> float | None:
    """Mean of the last 7 days minus the mean of the 7 days before. None without both."""
    items = list(points)
    this_week = _in_window(items, as_of)
    last_week = _in_window(items, as_of - dt.timedelta(days=WINDOW_DAYS))
    if not this_week or not last_week:
        return None
    return fmean(this_week) - fmean(last_week)
