"""Validates any proposed training program, whoever wrote it (AI, template or user).

Pure code on purpose: the AI may propose, but only these rules decide what is safe to show.
Errors block a program. Warnings are shown but allowed, because an injury or a short session
can legitimately leave a muscle under the usual weekly volume.
"""

import enum
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from app.models.enums import Equipment, Experience, Mechanic
from app.rules.exercise_catalog import (
    STRENGTH_CATEGORIES,
    CatalogExercise,
    is_available,
)
from app.rules.limitations import ExerciseFacts, LimitationFacts, exclusions_for

MIN_SETS, MAX_SETS = 1, 8
MIN_REPS, MAX_REPS = 1, 30
MIN_REST_S, MAX_REST_S = 30, 300
SECONDS_PER_SET = 45
TRANSITION_S = 60
WARMUP_MIN = 5
SECONDARY_SET_WEIGHT = 0.5

MUSCLE_GROUPS = {"lats": "back", "middle_back": "back"}
MAJOR_GROUPS = frozenset(
    {"chest", "back", "shoulders", "quads", "hamstrings", "biceps", "triceps"}
)
# Weekly hard sets per muscle group: (minimum, maximum).
VOLUME_RANGES: dict[Experience, tuple[int, int]] = {
    Experience.BEGINNER: (6, 14),
    Experience.INTERMEDIATE: (10, 20),
    Experience.ADVANCED: (12, 24),
}


class Severity(enum.StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ExerciseDraft:
    exercise_id: str
    sets: int
    rep_min: int
    rep_max: int
    rest_s: int
    notes: str | None = None


@dataclass(frozen=True)
class DayDraft:
    name: str
    focus: str
    rationale: str
    exercises: tuple[ExerciseDraft, ...]


@dataclass(frozen=True)
class ProgramDraft:
    rationale: str
    days: tuple[DayDraft, ...]


@dataclass(frozen=True)
class ProgramContext:
    catalog: Mapping[str, CatalogExercise]
    equipment: tuple[Equipment, ...]
    limitations: tuple[LimitationFacts, ...]
    experience: Experience
    session_minutes: int
    days_per_week: int


@dataclass(frozen=True)
class Violation:
    code: str
    severity: Severity
    message: str
    day_index: int | None = None
    exercise_id: str | None = None
    muscle: str | None = None


def exercise_problem(exercise: CatalogExercise, ctx: ProgramContext) -> str | None:
    """Why this exercise cannot be used for this user, or None if it is fine."""
    if exercise.category not in STRENGTH_CATEGORIES:
        return "wrong_category"
    if not is_available(exercise.required_equipment, ctx.equipment):
        return "equipment_unavailable"
    if ctx.experience is Experience.BEGINNER and exercise.level is Experience.ADVANCED:
        return "level_too_high"
    facts = ExerciseFacts(
        exercise.name,
        exercise.muscle_primary,
        exercise.muscles_secondary,
        exercise.mechanic,
    )
    if exclusions_for(facts, ctx.limitations):
        return "limitation"
    return None


def estimated_minutes(day: DayDraft) -> float:
    seconds = sum(
        ex.sets * (SECONDS_PER_SET + ex.rest_s) + TRANSITION_S for ex in day.exercises
    )
    return WARMUP_MIN + seconds / 60


def weekly_sets(
    draft: ProgramDraft, catalog: Mapping[str, CatalogExercise]
) -> dict[str, float]:
    """Hard sets per muscle group: primary counts 1, secondary counts 0.5."""
    totals: dict[str, float] = defaultdict(float)
    for day in draft.days:
        for item in day.exercises:
            exercise = catalog.get(item.exercise_id)
            if exercise is None:
                continue
            primary = MUSCLE_GROUPS.get(
                exercise.muscle_primary, exercise.muscle_primary
            )
            totals[primary] += item.sets
            secondary = {
                MUSCLE_GROUPS.get(m, m) for m in exercise.muscles_secondary
            } - {primary}
            for muscle in secondary:
                totals[muscle] += item.sets * SECONDARY_SET_WEIGHT
    return dict(totals)


def _check_exercise(
    item: ExerciseDraft, day_index: int, ctx: ProgramContext
) -> list[Violation]:
    found: list[Violation] = []

    def error(code: str, message: str) -> None:
        found.append(
            Violation(code, Severity.ERROR, message, day_index, item.exercise_id)
        )

    exercise = ctx.catalog.get(item.exercise_id)
    if exercise is None:
        error("unknown_exercise", "This exercise is not in the allowed list.")
        return found
    problem = exercise_problem(exercise, ctx)
    if problem == "wrong_category":
        error(problem, f"{exercise.name} is not a strength exercise.")
    elif problem == "equipment_unavailable":
        error(problem, f"{exercise.name} needs equipment the user does not have.")
    elif problem == "level_too_high":
        error(problem, f"{exercise.name} is too advanced for a beginner.")
    elif problem == "limitation":
        error(problem, f"{exercise.name} is ruled out by an active limitation.")
    if not MIN_SETS <= item.sets <= MAX_SETS:
        error("sets_out_of_range", f"{exercise.name}: sets must be 1 to {MAX_SETS}.")
    if not MIN_REPS <= item.rep_min <= item.rep_max <= MAX_REPS:
        error("reps_out_of_range", f"{exercise.name}: rep range is not valid.")
    if not MIN_REST_S <= item.rest_s <= MAX_REST_S:
        error(
            "rest_out_of_range",
            f"{exercise.name}: rest must be {MIN_REST_S} to {MAX_REST_S} seconds.",
        )
    return found


def _check_day(day: DayDraft, day_index: int, ctx: ProgramContext) -> list[Violation]:
    found: list[Violation] = []
    if not day.exercises:
        return [
            Violation("empty_day", Severity.ERROR, "A day has no exercises.", day_index)
        ]

    seen: set[str] = set()
    isolation_seen = False
    for item in day.exercises:
        found += _check_exercise(item, day_index, ctx)
        if item.exercise_id in seen:
            found.append(
                Violation(
                    "duplicate_exercise",
                    Severity.ERROR,
                    "An exercise appears twice in one day.",
                    day_index,
                    item.exercise_id,
                )
            )
        seen.add(item.exercise_id)
        exercise = ctx.catalog.get(item.exercise_id)
        if exercise is None:
            continue
        if exercise.mechanic is Mechanic.ISOLATION:
            isolation_seen = True
        elif exercise.mechanic is Mechanic.COMPOUND and isolation_seen:
            found.append(
                Violation(
                    "bad_order",
                    Severity.ERROR,
                    f"{exercise.name} is a compound lift after an isolation exercise. "
                    "Big compound lifts come first.",
                    day_index,
                    item.exercise_id,
                )
            )

    minutes = estimated_minutes(day)
    if minutes > ctx.session_minutes:
        found.append(
            Violation(
                "session_too_long",
                Severity.ERROR,
                f"'{day.name}' takes about {minutes:.0f} minutes; the limit is "
                f"{ctx.session_minutes}.",
                day_index,
            )
        )
    return found


def validate_program(draft: ProgramDraft, ctx: ProgramContext) -> list[Violation]:
    found: list[Violation] = []
    if not draft.days:
        return [
            Violation("no_days", Severity.ERROR, "The program has no training days.")
        ]
    if len(draft.days) > ctx.days_per_week:
        found.append(
            Violation(
                "too_many_days",
                Severity.ERROR,
                f"{len(draft.days)} training days, but the user trains "
                f"{ctx.days_per_week} days a week.",
            )
        )
    for index, day in enumerate(draft.days, start=1):
        found += _check_day(day, index, ctx)

    minimum, maximum = VOLUME_RANGES[ctx.experience]
    volume = weekly_sets(draft, ctx.catalog)
    for group, sets in sorted(volume.items()):
        if sets > maximum:
            found.append(
                Violation(
                    "volume_too_high",
                    Severity.ERROR,
                    f"{group}: {sets:g} weekly sets, the maximum is {maximum}.",
                    muscle=group,
                )
            )
    for group in sorted(MAJOR_GROUPS):
        sets = volume.get(group, 0.0)
        if sets < minimum:
            found.append(
                Violation(
                    "volume_low",
                    Severity.WARNING,
                    f"{group}: {sets:g} weekly sets, the usual minimum is {minimum}.",
                    muscle=group,
                )
            )
    return found


def errors_in(violations: list[Violation]) -> list[Violation]:
    return [v for v in violations if v.severity is Severity.ERROR]


def warnings_in(violations: list[Violation]) -> list[Violation]:
    return [v for v in violations if v.severity is Severity.WARNING]
