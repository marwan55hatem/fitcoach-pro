"""Workout statistics: estimated 1RM, personal bests and weekly volume (pure)."""

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.rules.exercise_catalog import CatalogExercise
from app.rules.program_validator import MUSCLE_GROUPS, SECONDARY_SET_WEIGHT

MAX_REPS_FOR_ESTIMATE = 12  # Epley gets unreliable above this


@dataclass(frozen=True)
class LoggedSet:
    exercise_id: str
    weight_kg: float | None
    reps: int | None
    done: bool
    is_warmup: bool
    performed_on: dt.date


@dataclass(frozen=True)
class BestSet:
    weight_kg: float
    reps: int
    date: dt.date
    estimated_1rm_kg: float | None


@dataclass(frozen=True)
class PersonalBest:
    best_e1rm: BestSet | None
    heaviest: BestSet | None
    most_reps: int


def counts_as_work(s: LoggedSet) -> bool:
    """A completed working set: done, not a warm-up, with at least one rep."""
    return s.done and not s.is_warmup and bool(s.reps)


def estimated_1rm(weight_kg: float | None, reps: int | None) -> float | None:
    """Epley estimate. None for body weight, zero load or more than 12 reps."""
    if not weight_kg or weight_kg <= 0 or not reps or reps > MAX_REPS_FOR_ESTIMATE:
        return None
    if reps == 1:
        return float(weight_kg)
    return weight_kg * (1 + reps / 30)


def _best_set(s: LoggedSet) -> BestSet:
    assert s.weight_kg is not None and s.reps is not None
    return BestSet(
        s.weight_kg, s.reps, s.performed_on, estimated_1rm(s.weight_kg, s.reps)
    )


def personal_best(sets: Iterable[LoggedSet]) -> PersonalBest | None:
    work = [s for s in sets if counts_as_work(s)]
    if not work:
        return None
    estimated = [s for s in work if estimated_1rm(s.weight_kg, s.reps) is not None]
    loaded = [s for s in work if s.weight_kg]
    best = max(
        estimated,
        key=lambda s: estimated_1rm(s.weight_kg, s.reps) or 0.0,
        default=None,
    )
    heaviest = max(loaded, key=lambda s: s.weight_kg or 0.0, default=None)
    return PersonalBest(
        best_e1rm=_best_set(best) if best else None,
        heaviest=_best_set(heaviest) if heaviest else None,
        most_reps=max(s.reps or 0 for s in work),
    )


def _score(best: PersonalBest) -> tuple[float, int]:
    e1rm = best.best_e1rm.estimated_1rm_kg if best.best_e1rm else None
    return (e1rm or 0.0, best.most_reps)


def new_records(
    previous: Iterable[LoggedSet], session: Iterable[LoggedSet]
) -> set[str]:
    """Exercises where this session beat the earlier best (estimated 1RM, else reps).

    A first-ever session for an exercise sets a baseline, not a record.
    """
    before: dict[str, list[LoggedSet]] = defaultdict(list)
    for s in previous:
        before[s.exercise_id].append(s)
    now: dict[str, list[LoggedSet]] = defaultdict(list)
    for s in session:
        now[s.exercise_id].append(s)

    records = set()
    for exercise_id, sets in now.items():
        old, new = personal_best(before[exercise_id]), personal_best(sets)
        if old is None or new is None:
            continue
        old_e1rm, old_reps = _score(old)
        new_e1rm, new_reps = _score(new)
        if new_e1rm > old_e1rm or (new_e1rm == old_e1rm == 0 and new_reps > old_reps):
            records.add(exercise_id)
    return records


def last_top_weight(sets: Iterable[LoggedSet]) -> float | None:
    """Heaviest working weight on the most recent day this exercise was done."""
    loaded = [s for s in sets if counts_as_work(s) and s.weight_kg]
    if not loaded:
        return None
    latest = max(s.performed_on for s in loaded)
    return max(s.weight_kg or 0.0 for s in loaded if s.performed_on == latest)


def week_start(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def weekly_volume(
    sets: Iterable[LoggedSet], catalog: Mapping[str, CatalogExercise]
) -> dict[dt.date, dict[str, float]]:
    """Completed hard sets per muscle group per week (Monday start).

    Counted the same way as the program validator: primary 1, each secondary 0.5.
    """
    weeks: dict[dt.date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for s in sets:
        exercise = catalog.get(s.exercise_id)
        if exercise is None or not counts_as_work(s):
            continue
        totals = weeks[week_start(s.performed_on)]
        primary = MUSCLE_GROUPS.get(exercise.muscle_primary, exercise.muscle_primary)
        totals[primary] += 1
        secondary = {MUSCLE_GROUPS.get(m, m) for m in exercise.muscles_secondary}
        for muscle in secondary - {primary}:
            totals[muscle] += SECONDARY_SET_WEIGHT
    return {week: dict(totals) for week, totals in weeks.items()}
