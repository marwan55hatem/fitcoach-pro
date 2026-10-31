from dataclasses import replace
from typing import Any

import pytest

from app.models.enums import BodyArea, Equipment, Experience, Severity
from app.rules.exercise_catalog import CatalogExercise
from app.rules.limitations import LimitationFacts
from app.rules.program_validator import (
    VOLUME_RANGES,
    DayDraft,
    ExerciseDraft,
    ProgramContext,
    ProgramDraft,
    errors_in,
    estimated_minutes,
    exercise_problem,
    validate_program,
    warnings_in,
    weekly_sets,
)
from app.rules.templates import (
    FOUR_DAY_SPLIT,
    FULL_BODY,
    PUSH_PULL_LEGS,
    TEMPLATES,
    build_template_program,
    choose_template,
    scheme_for,
    shortlist_for_ai,
    usable_exercises,
)
from tests.program_fixtures import FULL_GYM, catalog

E = Equipment


def context(**overrides: Any) -> ProgramContext:
    base: dict[str, Any] = {
        "catalog": catalog(),
        "equipment": FULL_GYM,
        "limitations": (),
        "experience": Experience.INTERMEDIATE,
        "session_minutes": 75,
        "days_per_week": 4,
    }
    return ProgramContext(**{**base, **overrides})


def item(key: str, sets: int = 3, **overrides: Any) -> ExerciseDraft:
    values = {"rep_min": 8, "rep_max": 12, "rest_s": 90}
    return ExerciseDraft(key, sets, **{**values, **overrides})


BENCH = "barbell-bench-press-medium-grip"
FLYES = "dumbbell-flyes"
CURL = "barbell-curl"
SQUAT = "barbell-full-squat"


def one_day(*exercises: ExerciseDraft, name: str = "Day") -> ProgramDraft:
    return ProgramDraft("r", (DayDraft(name, "f", "r", tuple(exercises)),))


def codes(draft: ProgramDraft, ctx: ProgramContext) -> set[str]:
    return {v.code for v in validate_program(draft, ctx)}


def error_codes(draft: ProgramDraft, ctx: ProgramContext) -> set[str]:
    return {v.code for v in errors_in(validate_program(draft, ctx))}


# --- validator: each rule ---------------------------------------------------------------


def test_a_sound_day_has_no_errors() -> None:
    draft = one_day(item(BENCH, 4), item(FLYES, 3), item(CURL, 3))
    assert error_codes(draft, context()) == set()


def test_unknown_exercise_is_an_error_and_counts_no_volume() -> None:
    draft = one_day(item("made-up-exercise"))
    assert "unknown_exercise" in error_codes(draft, context())
    assert weekly_sets(draft, catalog()) == {}


def test_equipment_must_be_available() -> None:
    ctx = context(equipment=(E.DUMBBELL, E.BENCH))
    assert "equipment_unavailable" in error_codes(one_day(item(BENCH)), ctx)


def test_a_bench_is_required_for_bench_work() -> None:
    ctx = context(equipment=(E.DUMBBELL,))
    draft = one_day(item("incline-dumbbell-press"))
    assert "equipment_unavailable" in error_codes(draft, ctx)
    # Bodyweight is always possible.
    assert "equipment_unavailable" not in error_codes(one_day(item("pushups")), ctx)


def test_limitations_rule_exercises_out() -> None:
    ctx = context(limitations=(LimitationFacts(BodyArea.KNEE, Severity.MILD),))
    assert "limitation" in error_codes(one_day(item(SQUAT)), ctx)
    assert "limitation" not in error_codes(one_day(item(BENCH)), ctx)


def test_inactive_limitations_do_not_apply() -> None:
    ctx = context(limitations=(LimitationFacts(BodyArea.KNEE, Severity.SEVERE, False),))
    assert "limitation" not in error_codes(one_day(item(SQUAT)), ctx)


def test_beginners_do_not_get_advanced_exercises() -> None:
    draft = one_day(item("snatch-pull"))
    beginner = context(experience=Experience.BEGINNER)
    assert "level_too_high" in error_codes(draft, beginner)
    assert "level_too_high" not in error_codes(draft, context())


def test_non_strength_exercises_are_rejected() -> None:
    items = catalog()
    items["cat"] = CatalogExercise(
        "cat", "Cat Stretch", "lower_back", (), E.BODY_WEIGHT, None, None, "stretching"
    )
    assert "wrong_category" in error_codes(one_day(item("cat")), context(catalog=items))


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"sets": 0}, "sets_out_of_range"),
        ({"sets": 9}, "sets_out_of_range"),
        ({"rep_min": 0}, "reps_out_of_range"),
        ({"rep_min": 12, "rep_max": 8}, "reps_out_of_range"),
        ({"rep_max": 31}, "reps_out_of_range"),
        ({"rest_s": 29}, "rest_out_of_range"),
        ({"rest_s": 301}, "rest_out_of_range"),
    ],
)
def test_scheme_limits(overrides: dict[str, int], code: str) -> None:
    assert code in error_codes(one_day(item(BENCH, **overrides)), context())


@pytest.mark.parametrize(
    "overrides",
    [
        {"sets": 1},
        {"sets": 8},
        {"rep_min": 1, "rep_max": 30},
        {"rest_s": 30},
        {"rest_s": 300},
    ],
)
def test_scheme_boundaries_are_allowed(overrides: dict[str, int]) -> None:
    found = error_codes(one_day(item(BENCH, **overrides)), context())
    assert not found & {"sets_out_of_range", "reps_out_of_range", "rest_out_of_range"}


def test_compounds_must_come_before_isolation() -> None:
    assert "bad_order" in error_codes(one_day(item(CURL), item(BENCH)), context())
    assert "bad_order" not in error_codes(one_day(item(BENCH), item(CURL)), context())


def test_duplicates_and_empty_days_are_errors() -> None:
    assert "duplicate_exercise" in error_codes(
        one_day(item(BENCH), item(BENCH)), context()
    )
    assert "empty_day" in error_codes(one_day(), context())
    assert error_codes(ProgramDraft("r", ()), context()) == {"no_days"}


def test_too_many_days_is_an_error() -> None:
    day = DayDraft("D", "f", "r", (item(BENCH),))
    assert "too_many_days" in error_codes(
        ProgramDraft("r", (day, day)), context(days_per_week=1)
    )


def test_session_length_estimate_and_limit() -> None:
    day = DayDraft("D", "f", "r", (item(BENCH, 4, rest_s=120),))
    # 5 warm-up + (4 sets x (45 + 120) s + 60 s transition) = 17 minutes
    assert estimated_minutes(day) == pytest.approx(17)
    draft = ProgramDraft("r", (day,))
    assert "session_too_long" in error_codes(draft, context(session_minutes=16))
    assert "session_too_long" not in error_codes(draft, context(session_minutes=17))


def test_weekly_sets_counts_secondary_muscles_as_half_and_groups_the_back() -> None:
    draft = one_day(item(BENCH, 4), item("seated-cable-rows", 2), item("pullups", 2))
    sets = weekly_sets(draft, catalog())
    assert sets["chest"] == 4
    assert sets["triceps"] == 2  # half of the bench sets
    assert sets["shoulders"] == 3  # half of the bench sets plus half of the row sets
    assert sets["back"] == 4  # lats and middle back are one group, counted once
    assert "lats" not in sets and "middle_back" not in sets


def test_a_secondary_muscle_in_the_primary_group_is_not_double_counted() -> None:
    sets = weekly_sets(one_day(item("seated-cable-rows", 4)), catalog())
    assert sets["back"] == 4


@pytest.mark.parametrize(
    ("experience", "over"),
    [
        (Experience.BEGINNER, 15),
        (Experience.INTERMEDIATE, 21),
        (Experience.ADVANCED, 25),
    ],
)
def test_weekly_volume_over_the_maximum_is_an_error(
    experience: Experience, over: int
) -> None:
    days = []
    remaining = over
    while remaining:
        sets = min(8, remaining)
        days.append(DayDraft("D", "f", "r", (item("butterfly", sets),)))
        remaining -= sets
    ctx = context(experience=experience, days_per_week=7, session_minutes=240)
    assert "volume_too_high" in error_codes(ProgramDraft("r", tuple(days)), ctx)


def test_volume_at_the_maximum_is_allowed() -> None:
    maximum = VOLUME_RANGES[Experience.BEGINNER][1]
    days = (
        DayDraft("A", "f", "r", (item("butterfly", 8),)),
        DayDraft("B", "f", "r", (item("butterfly", maximum - 8),)),
    )
    ctx = context(experience=Experience.BEGINNER, session_minutes=240)
    assert "volume_too_high" not in error_codes(ProgramDraft("r", days), ctx)


def test_low_volume_is_only_a_warning() -> None:
    violations = validate_program(one_day(item(BENCH, 3)), context())
    warnings = warnings_in(violations)
    assert {v.code for v in warnings} == {"volume_low"}
    assert {v.muscle for v in warnings} >= {"quads", "back"}
    assert errors_in(violations) == []


# --- templates --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "template"),
    [
        (1, FULL_BODY),
        (2, FULL_BODY),
        (3, FULL_BODY),
        (4, FOUR_DAY_SPLIT),
        (5, PUSH_PULL_LEGS),
        (6, PUSH_PULL_LEGS),
        (7, PUSH_PULL_LEGS),
    ],
)
def test_template_choice_by_days(days: int, template: Any) -> None:
    assert choose_template(days) is template


def test_the_default_split_has_the_four_planned_days() -> None:
    assert [d.name for d in FOUR_DAY_SPLIT.days] == [
        "Day 1 Bench",
        "Day 2 Back",
        "Day 3 Arms+Shoulders",
        "Day 4 Legs",
    ]
    assert sum(len(d.slots) for d in FOUR_DAY_SPLIT.days) == 18


def test_schemes_match_experience() -> None:
    assert scheme_for(Experience.BEGINNER, True).sets == 3
    assert scheme_for(Experience.INTERMEDIATE, False).rep_max == 15
    assert scheme_for(Experience.ADVANCED, True).rest_s == 180


@pytest.mark.parametrize("experience", list(Experience))
@pytest.mark.parametrize("key", sorted(TEMPLATES))
@pytest.mark.parametrize("minutes", [30, 45, 60, 75, 90, 120])
def test_every_template_builds_a_valid_program(
    key: str, experience: Experience, minutes: int
) -> None:
    template = TEMPLATES[key]
    ctx = context(
        experience=experience,
        session_minutes=minutes,
        days_per_week=len(template.days),
    )
    draft = build_template_program(template, ctx)
    assert len(draft.days) == len(template.days)
    assert errors_in(validate_program(draft, ctx)) == []


def test_days_are_limited_to_the_users_schedule() -> None:
    ctx = context(days_per_week=2)
    draft = build_template_program(FULL_BODY, ctx)
    assert [d.name for d in draft.days] == ["Full Body A", "Full Body B"]


def test_the_template_uses_the_planned_exercises_in_a_full_gym() -> None:
    ctx = context(session_minutes=120)
    draft = build_template_program(FOUR_DAY_SPLIT, ctx)
    first = [ctx.catalog[e.exercise_id].name for e in draft.days[0].exercises]
    assert first[:2] == ["Barbell Bench Press - Medium Grip", "Incline Dumbbell Press"]


def test_short_sessions_are_trimmed_to_fit() -> None:
    ctx = context(session_minutes=30)
    draft = build_template_program(FOUR_DAY_SPLIT, ctx)
    assert all(estimated_minutes(day) <= 30 for day in draft.days)
    assert errors_in(validate_program(draft, ctx)) == []


def test_injuries_swap_exercises_rather_than_failing() -> None:
    ctx = context(limitations=(LimitationFacts(BodyArea.KNEE, Severity.MILD),))
    draft = build_template_program(FOUR_DAY_SPLIT, ctx)
    names = {ctx.catalog[e.exercise_id].name for d in draft.days for e in d.exercises}
    assert "Barbell Full Squat" not in names and "Leg Press" not in names
    assert errors_in(validate_program(draft, ctx)) == []


def test_limited_equipment_substitutes_within_the_muscle() -> None:
    ctx = context(equipment=(E.DUMBBELL, E.BENCH), session_minutes=90)
    draft = build_template_program(FOUR_DAY_SPLIT, ctx)
    assert errors_in(validate_program(draft, ctx)) == []
    used = {ctx.catalog[e.exercise_id] for d in draft.days for e in d.exercises}
    assert all(e.equipment in {E.DUMBBELL, E.BODY_WEIGHT} for e in used)


def test_bodyweight_only_still_produces_something_or_nothing_invalid() -> None:
    ctx = context(equipment=(), session_minutes=60)
    draft = build_template_program(FOUR_DAY_SPLIT, ctx)
    assert errors_in(validate_program(replace(draft), ctx)) == []


def test_an_empty_catalog_gives_no_days() -> None:
    assert build_template_program(FOUR_DAY_SPLIT, context(catalog={})).days == ()


def test_substitutes_never_put_a_compound_after_an_isolation() -> None:
    items = catalog()
    # Removing the planned fly forces a substitute for the isolation slot.
    del items["dumbbell-flyes"], items["cable-crossover"], items["butterfly"]
    ctx = context(catalog=items)
    assert "bad_order" not in codes(build_template_program(FOUR_DAY_SPLIT, ctx), ctx)


def test_overlapping_secondary_volume_is_trimmed_under_the_maximum() -> None:
    # Rows and presses all count half for shoulders: the regression found on real data.
    ctx = context(experience=Experience.BEGINNER, session_minutes=120)
    draft = build_template_program(FOUR_DAY_SPLIT, ctx)
    assert weekly_sets(draft, ctx.catalog)["shoulders"] <= 14
    assert errors_in(validate_program(draft, ctx)) == []


def test_volume_trimming_cuts_direct_isolation_work_first() -> None:
    ctx = context(experience=Experience.BEGINNER, session_minutes=120)
    draft = build_template_program(FOUR_DAY_SPLIT, ctx)
    names = {ctx.catalog[e.exercise_id].name for d in draft.days for e in d.exercises}
    assert "Barbell Bench Press - Medium Grip" in names
    assert "Barbell Shoulder Press" in names


# --- AI shortlist -----------------------------------------------------------------------


def test_shortlist_only_contains_exercises_the_validator_accepts() -> None:
    ctx = context(
        equipment=(E.DUMBBELL, E.BENCH),
        limitations=(LimitationFacts(BodyArea.KNEE, Severity.MILD),),
        experience=Experience.BEGINNER,
    )
    shortlist = shortlist_for_ai(FOUR_DAY_SPLIT, ctx)
    assert shortlist
    assert all(exercise_problem(e, ctx) is None for e in shortlist)
    assert set(shortlist) <= set(usable_exercises(ctx))


def test_shortlist_is_capped_per_muscle_and_prefers_the_template() -> None:
    items = catalog()
    for number in range(30):
        key = f"extra-chest-{number}"
        items[key] = replace(items["butterfly"], id=key, name=f"Aaa Chest {number}")
    ctx = context(catalog=items)
    shortlist = shortlist_for_ai(FOUR_DAY_SPLIT, ctx)
    chest = [e for e in shortlist if e.muscle_primary == "chest"]
    assert len(chest) == 10
    assert "Barbell Bench Press - Medium Grip" in {e.name for e in chest}
    assert shortlist[0].name != "Aaa Chest 0"  # template exercises rank first
