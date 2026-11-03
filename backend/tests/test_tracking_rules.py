import datetime as dt

import pytest

from app.models.enums import Equipment, Experience, Mechanic
from app.rules.exercise_catalog import CatalogExercise
from app.rules.training_stats import (
    LoggedSet,
    counts_as_work,
    estimated_1rm,
    last_top_weight,
    new_records,
    personal_best,
    week_start,
    weekly_volume,
)
from app.rules.weight_trend import WeightPoint, moving_average, weekly_change

D = dt.date(2026, 11, 2)  # a Monday


def day(offset: int) -> dt.date:
    return D + dt.timedelta(days=offset)


# --- weight trend ---------------------------------------------------------------------


def test_moving_average_of_one_point_is_the_point() -> None:
    [only] = moving_average([WeightPoint(D, 70.0)])
    assert only.average_7d == 70.0


def test_moving_average_uses_a_trailing_7_day_calendar_window() -> None:
    points = [WeightPoint(day(i), 70.0 + i) for i in range(10)]
    trend = moving_average(points)
    assert trend[6].average_7d == pytest.approx(73.0)  # days 0..6
    assert trend[9].average_7d == pytest.approx(76.0)  # days 3..9


def test_moving_average_skips_missing_days_and_sorts() -> None:
    points = [WeightPoint(day(6), 72.0), WeightPoint(day(0), 70.0)]
    trend = moving_average(points)
    assert [p.date for p in trend] == [day(0), day(6)]
    assert trend[1].average_7d == pytest.approx(71.0)
    gap = moving_average([WeightPoint(day(0), 70.0), WeightPoint(day(7), 72.0)])
    assert gap[1].average_7d == 72.0  # day 0 is outside day 7's window


def test_weekly_change_compares_this_week_with_last_week() -> None:
    points = [WeightPoint(day(i), 70.0) for i in range(7)] + [
        WeightPoint(day(i), 70.5) for i in range(7, 14)
    ]
    assert weekly_change(points, as_of=day(13)) == pytest.approx(0.5)


def test_weekly_change_needs_both_weeks() -> None:
    assert weekly_change([], as_of=D) is None
    assert weekly_change([WeightPoint(D, 70.0)], as_of=D) is None
    old = [WeightPoint(day(i), 70.0) for i in range(7)]
    assert weekly_change(old, as_of=day(30)) is None


def test_weekly_change_with_sparse_logs() -> None:
    points = [WeightPoint(day(0), 70.0), WeightPoint(day(10), 69.4)]
    assert weekly_change(points, as_of=day(12)) == pytest.approx(-0.6)


# --- training stats -------------------------------------------------------------------


def logged(
    exercise: str = "bench",
    weight: float | None = 80,
    reps: int | None = 8,
    *,
    done: bool = True,
    warmup: bool = False,
    on: dt.date = D,
) -> LoggedSet:
    return LoggedSet(exercise, weight, reps, done, warmup, on)


def test_estimated_1rm_epley() -> None:
    assert estimated_1rm(100, 1) == 100
    assert estimated_1rm(80, 8) == pytest.approx(80 * (1 + 8 / 30))
    assert estimated_1rm(80, 13) is None  # too many reps to estimate
    assert estimated_1rm(0, 5) is None
    assert estimated_1rm(None, 5) is None


def test_only_done_working_sets_with_reps_count() -> None:
    assert counts_as_work(logged())
    assert not counts_as_work(logged(done=False))
    assert not counts_as_work(logged(warmup=True))
    assert not counts_as_work(logged(reps=None))
    assert not counts_as_work(logged(reps=0))


def test_personal_best_picks_best_estimate_and_heaviest() -> None:
    sets = [
        logged(weight=100, reps=3, on=day(0)),  # e1rm 110
        logged(weight=90, reps=8, on=day(3)),  # e1rm 114
        logged(weight=120, reps=1, done=False),  # not done: ignored
        logged(weight=60, reps=10, warmup=True),  # warm-up: ignored
    ]
    best = personal_best(sets)
    assert best is not None
    assert best.best_e1rm.weight_kg == 90
    assert best.best_e1rm.date == day(3)
    assert best.heaviest.weight_kg == 100
    assert personal_best([logged(done=False)]) is None


def test_bodyweight_sets_have_no_best_estimate_but_most_reps() -> None:
    best = personal_best([logged(weight=None, reps=12), logged(weight=None, reps=15)])
    assert best is not None
    assert best.best_e1rm is None
    assert best.heaviest is None
    assert best.most_reps == 15


def test_new_records_only_when_beating_the_previous_best() -> None:
    before = [logged(weight=80, reps=8, on=day(0))]
    assert new_records(before, [logged(weight=82.5, reps=8, on=day(7))]) == {"bench"}
    assert new_records(before, [logged(weight=80, reps=8, on=day(7))]) == set()
    # A first-ever session is not a record (nothing to beat).
    assert new_records([], [logged(weight=40, reps=5)]) == set()


def test_last_top_weight_is_the_heaviest_set_of_the_latest_session() -> None:
    sets = [
        logged(weight=100, reps=5, on=day(0)),
        logged(weight=82.5, reps=8, on=day(7)),
        logged(weight=85, reps=6, on=day(7)),
        logged(weight=90, reps=6, on=day(9), done=False),
    ]
    assert last_top_weight(sets) == 85
    assert last_top_weight([]) is None
    assert last_top_weight([logged(weight=None)]) is None


def test_week_start_is_monday() -> None:
    assert week_start(day(6)) == D
    assert week_start(day(7)) == day(7)


CATALOG = {
    "bench": CatalogExercise(
        "bench",
        "Bench press",
        "chest",
        ("triceps", "shoulders"),
        Equipment.BARBELL,
        Experience.INTERMEDIATE,
        Mechanic.COMPOUND,
        "strength",
    ),
    "row": CatalogExercise(
        "row",
        "Barbell row",
        "middle_back",
        ("lats", "biceps"),
        Equipment.BARBELL,
        Experience.INTERMEDIATE,
        Mechanic.COMPOUND,
        "strength",
    ),
}


def test_weekly_volume_counts_secondary_as_half_and_groups_back() -> None:
    sets = [logged("bench", on=day(0)) for _ in range(4)] + [
        logged("row", on=day(2)) for _ in range(3)
    ]
    sets.append(logged("bench", on=day(1), done=False))
    sets.append(logged("bench", on=day(8)))  # next week
    volume = weekly_volume(sets, CATALOG)
    week = volume[D]
    assert week["chest"] == 4
    assert week["triceps"] == 2
    assert week["back"] == 3  # middle_back primary; lats secondary is the same group
    assert week["biceps"] == 1.5
    assert volume[day(7)]["chest"] == 1


def test_weekly_volume_ignores_unknown_exercises() -> None:
    assert weekly_volume([logged("gone")], CATALOG) == {}
