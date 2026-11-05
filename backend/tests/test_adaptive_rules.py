import datetime as dt

import pytest

from app.models.enums import Goal, Mechanic
from app.rules.calories import InbodyChange, calorie_adjustment
from app.rules.limits import (
    MAX_CALORIE_CHANGE_KCAL,
    Kind,
    UserState,
    validate_recommendation,
)
from app.rules.progression import (
    Action,
    SessionResult,
    WorkSet,
    progression,
    stall_detection,
)
from app.rules.recovery import (
    READINESS_LOW,
    Checkin,
    deload,
    readiness,
    session_sets,
)
from app.rules.review import ExerciseHistory, ReviewInput, weekly_review

DAY = dt.date(2026, 11, 2)
C, I = Mechanic.COMPOUND, Mechanic.ISOLATION


def session(weight: float, *reps: int, days: int = 0) -> SessionResult:
    return SessionResult(
        DAY + dt.timedelta(days=days), tuple(WorkSet(weight, r) for r in reps)
    )


def state(**override) -> UserState:
    values = {
        "goal": Goal.LEAN_BULK,
        "weight_kg": 70.0,
        "calorie_adjustment_kcal": 0,
        "protein_g_per_kg": 2.0,
        "targets_withheld": False,
    }
    values.update(override)
    return UserState(**values)


# --- progression ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("history", "mechanic", "action", "weight", "change", "reason"),
    [
        ([], C, Action.KEEP, None, 0, "no_history"),
        ([session(60, 10, 10, 10)], C, Action.ADD, 62.5, 2.5, "top_of_range"),
        ([session(12, 12, 12, 12)], I, Action.ADD, 13, 1, "top_of_range"),
        # The top on every set: one set short means keep the weight.
        ([session(60, 10, 10, 9)], C, Action.KEEP, 60, 0, "in_range"),
        ([session(60, 10, 8, 6)], C, Action.KEEP, 60, 0, "missed_once"),
        # Two failed sessions in a row at the weight: reduce 5 %...
        (
            [session(60, 7, 7, 6), session(60, 7, 6, 6, days=3)],
            C,
            Action.REDUCE,
            57.5,
            -2.5,
            "failed_twice",
        ),
        # ...or 10 % when the reps fell well short.
        (
            [session(60, 5, 4, 4), session(60, 5, 4, 3, days=3)],
            C,
            Action.REDUCE,
            55,
            -5,
            "failed_twice_badly",
        ),
        # A failure at a lighter weight before does not count against today's.
        (
            [session(55, 5, 5, 5), session(60, 7, 6, 6, days=3)],
            C,
            Action.KEEP,
            60,
            0,
            "missed_once",
        ),
        # Small isolation weights drop by one step at least, never below zero.
        (
            [session(4, 6, 6), session(4, 6, 6, days=3)],
            I,
            Action.REDUCE,
            3,
            -1,
            "failed_twice",
        ),
        (
            [session(0.5, 6, 6), session(0.5, 6, 6, days=3)],
            I,
            Action.REDUCE,
            0,
            -0.5,
            "failed_twice",
        ),
        ([session(0, 12, 12, 12)], C, Action.KEEP, 0, 0, "bodyweight"),
        ([SessionResult(DAY, ())], C, Action.KEEP, None, 0, "no_history"),
    ],
)
def test_progression(history, mechanic, action, weight, change, reason) -> None:
    result = progression(history, 8, 10 if mechanic is C else 12, mechanic)
    assert (result.action, result.weight_kg, result.change_kg, result.reason) == (
        action,
        weight,
        change,
        reason,
    )
    assert result.message


def test_progression_uses_the_latest_session() -> None:
    history = [session(60, 10, 10, 10), session(62.5, 9, 8, 8, days=3)]
    assert progression(history, 8, 10, C).action is Action.KEEP


# --- stalls -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("history", "fix"),
    [
        # Same weight and reps three sessions running.
        (
            [
                session(60, 9, 9, 8),
                session(60, 9, 9, 8, days=3),
                session(60, 9, 8, 8, days=6),
            ],
            "vary_or_add_set",
        ),
        # Stuck and failing: drop and build back up.
        (
            [
                session(60, 8, 8, 8),
                session(60, 8, 7, 7, days=3),
                session(60, 7, 7, 6, days=6),
            ],
            "reduce_and_rebuild",
        ),
    ],
)
def test_stall_after_three_sessions_without_progress(history, fix) -> None:
    stall = stall_detection(history, 8, 10)
    assert stall is not None and stall.fix == fix and stall.sessions == 3


@pytest.mark.parametrize(
    "history",
    [
        [session(60, 9, 9, 8), session(60, 9, 9, 8, days=3)],  # only two sessions
        [
            session(60, 9, 9, 8),
            session(60, 9, 9, 8, days=3),
            session(60, 9, 9, 9, days=6),
        ],  # one more rep
        [
            session(60, 9, 9, 8),
            session(60, 9, 9, 8, days=3),
            session(62.5, 8, 8, 8, days=6),
        ],  # heavier
        # Hitting the top every time: the fix is adding weight, not a stall.
        [session(60, 10, 10), session(60, 10, 10, days=3), session(60, 10, 10, days=6)],
    ],
)
def test_no_stall(history) -> None:
    assert stall_detection(history, 8, 10) is None


# --- readiness and deload ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("checkin", "score"),
    [
        (Checkin(DAY, 8, 1, 1, 5, 5), 100),
        (Checkin(DAY, 4, 5, 5, 1, 1), 0),
        (Checkin(DAY, 6, 3, 3, 3, 3), 50),
        (
            Checkin(DAY, 10, None, None, None, None),
            100,
        ),  # sleep past 8 h counts as full
        (Checkin(DAY, None, None, None, 4, None), 75),  # only what was answered counts
    ],
)
def test_readiness_score(checkin: Checkin, score: int) -> None:
    result = readiness([checkin])
    assert result is not None and result.score == score
    assert result.low is (score < READINESS_LOW)


def test_readiness_uses_the_latest_checkin() -> None:
    old = Checkin(DAY, 4, 5, 5, 1, 1)
    new = Checkin(DAY + dt.timedelta(days=1), 8, 1, 1, 5, 5)
    result = readiness([new, old])
    assert result is not None and result.score == 100


def test_no_readiness_without_answers() -> None:
    assert readiness([]) is None
    assert readiness([Checkin(DAY, None, None, None, None, None)]) is None


@pytest.mark.parametrize(
    ("planned", "score", "expected"),
    [(4, 20, 3), (4, 39, 3), (4, 40, 4), (1, 10, 1), (3, None, 3)],
)
def test_low_readiness_cuts_one_set_never_more(planned, score, expected) -> None:
    checkins = (
        []
        if score is None
        else [Checkin(DAY, None, None, None, 1 + score * 4 / 100, None)]
    )
    assert session_sets(planned, readiness(checkins)) == expected


@pytest.mark.parametrize(
    ("hard_weeks", "scores", "reason"),
    [
        (6, [], "long_block"),
        (7, [80, 85, 90], "long_block"),
        (2, [30, 35, 20], "low_readiness"),
        (4, [55, 50, 58], "accumulated_fatigue"),
        (5, [59, 59, 59], "accumulated_fatigue"),
        (3, [55, 50, 58], None),  # not long enough to be fatigue
        (5, [70, 75], None),
        (2, [30, 20], None),  # two low days are not a low week
        (0, [], None),
    ],
)
def test_deload(hard_weeks: int, scores: list[int], reason: str | None) -> None:
    result = deload(hard_weeks, scores)
    assert (result.reason if result else None) == reason
    if result:
        assert result.weeks == 1 and result.set_factor == 0.5


# --- calories ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("goal", "changes", "inbody", "kcal", "reason"),
    [
        (Goal.LEAN_BULK, [0.3, 0.4], None, None, None),  # on target
        (Goal.LEAN_BULK, [0.1, 0.14], None, 250, "gaining_too_slowly"),
        (Goal.LEAN_BULK, [0.3, 0.14], None, None, None),  # one slow week is not a trend
        (Goal.LEAN_BULK, [0.15, 0.15], None, None, None),
        (Goal.LEAN_BULK, [0.3, 0.71], None, -150, "gaining_too_fast"),
        (Goal.LEAN_BULK, [0.3, 0.7], None, None, None),
        (Goal.LEAN_BULK, [0.4, 0.4], InbodyChange(0.2, 1.0), -150, "fat_rising_faster"),
        (Goal.LEAN_BULK, [0.4, 0.4], InbodyChange(0.6, 0.4), None, None),  # lean gain
        (Goal.CUT, [-0.5, -0.5], None, None, None),
        (Goal.CUT, [-0.05, 0.0], None, -150, "losing_too_slowly"),
        (Goal.CUT, [-0.5, -1.1], None, 150, "losing_too_fast"),
        (Goal.MAINTAIN, [0.4, 0.5], None, -100, "drifting_up"),
        (Goal.RECOMP, [-0.4, -0.5], None, 100, "drifting_down"),
        (Goal.MAINTAIN, [0.4, -0.5], None, None, None),
        (Goal.MAINTAIN, [], None, None, None),
    ],
)
def test_calorie_adjustment(goal, changes, inbody, kcal, reason) -> None:
    advice = calorie_adjustment(state(goal=goal), changes, inbody)
    assert (advice.change_kcal if advice else None) == kcal
    assert (advice.reason if advice else None) == reason


def test_calorie_changes_respect_the_limits() -> None:
    # Already +400 on a bulk: +250 more would pass the 500 cap, so only +100 is offered.
    advice = calorie_adjustment(state(calorie_adjustment_kcal=400), [0.1, 0.1], None)
    assert advice is not None and advice.change_kcal == 100
    # At the cap there is nothing left to add.
    assert (
        calorie_adjustment(state(calorie_adjustment_kcal=500), [0.1, 0.1], None) is None
    )
    # A bulk never drops below maintenance (the goal's +300 plus the adjustment >= 0).
    advice = calorie_adjustment(state(calorie_adjustment_kcal=-200), [0.3, 0.9], None)
    assert advice is not None and advice.change_kcal == -100
    assert (
        calorie_adjustment(state(calorie_adjustment_kcal=-300), [0.3, 0.9], None)
        is None
    )


def test_no_calorie_advice_when_targets_are_withheld() -> None:
    assert calorie_adjustment(state(targets_withheld=True), [0.1, 0.1], None) is None


# --- validation of any proposal (rules or AI) -------------------------------------------


@pytest.mark.parametrize(
    ("kind", "payload", "user", "allowed", "reason"),
    [
        (
            "load_change",
            {"exercise_id": "x", "action": "add", "from_kg": 60, "to_kg": 62.5},
            {},
            True,
            "ok",
        ),
        (
            "load_change",
            {"exercise_id": "x", "action": "add", "from_kg": 60, "to_kg": 65},
            {},
            False,
            "increase_too_big",
        ),
        (
            "load_change",
            {"exercise_id": "x", "action": "add", "from_kg": 60, "to_kg": 60},
            {},
            False,
            "wrong_direction",
        ),
        (
            "load_change",
            {"exercise_id": "x", "action": "reduce", "from_kg": 60, "to_kg": 54},
            {},
            True,
            "ok",
        ),
        (
            "load_change",
            {"exercise_id": "x", "action": "reduce", "from_kg": 60, "to_kg": 50},
            {},
            False,
            "decrease_too_big",
        ),
        (
            "load_change",
            {"exercise_id": "x", "action": "reduce", "from_kg": 4, "to_kg": 1.5},
            {},
            True,
            "ok",
        ),
        (
            "load_change",
            {"exercise_id": "x", "action": "reduce", "from_kg": 60, "to_kg": -1},
            {},
            False,
            "invalid_payload",
        ),
        (
            "load_change",
            {"exercise_id": "", "action": "add", "from_kg": 60, "to_kg": 62.5},
            {},
            False,
            "invalid_payload",
        ),
        (
            "load_change",
            {"exercise_id": "x", "action": "add", "from_kg": "60", "to_kg": 62.5},
            {},
            False,
            "invalid_payload",
        ),
        (
            "load_change",
            {"exercise_id": "x", "action": "add", "from_kg": 60, "to_kg": 62.5, "x": 1},
            {},
            False,
            "invalid_payload",
        ),
        ("calorie_change", {"change_kcal": 250}, {}, True, "ok"),
        (
            "calorie_change",
            {"change_kcal": MAX_CALORIE_CHANGE_KCAL + 1},
            {},
            False,
            "change_too_big",
        ),
        ("calorie_change", {"change_kcal": 0}, {}, False, "invalid_payload"),
        ("calorie_change", {"change_kcal": True}, {}, False, "invalid_payload"),
        ("calorie_change", {"change_kcal": 2.5}, {}, False, "invalid_payload"),
        (
            "calorie_change",
            {"change_kcal": 200},
            {"calorie_adjustment_kcal": 400},
            False,
            "total_out_of_range",
        ),
        (
            "calorie_change",
            {"change_kcal": -200},
            {"calorie_adjustment_kcal": -200},
            False,
            "below_maintenance_on_bulk",
        ),
        (
            "calorie_change",
            {"change_kcal": -200},
            {"calorie_adjustment_kcal": -200, "goal": Goal.CUT},
            True,
            "ok",
        ),
        (
            "calorie_change",
            {"change_kcal": 100},
            {"targets_withheld": True},
            False,
            "targets_withheld",
        ),
        ("protein_change", {"g_per_kg": 1.8}, {}, True, "ok"),
        ("protein_change", {"g_per_kg": 1.5}, {}, False, "protein_out_of_range"),
        ("protein_change", {"g_per_kg": 2.3}, {}, False, "protein_out_of_range"),
        (
            "protein_change",
            {"g_per_kg": 1.8},
            {"targets_withheld": True},
            False,
            "targets_withheld",
        ),
        ("deload", {"weeks": 1, "set_factor": 0.5}, {}, True, "ok"),
        ("deload", {"weeks": 3, "set_factor": 0.5}, {}, False, "deload_out_of_range"),
        ("deload", {"weeks": 1, "set_factor": 0.1}, {}, False, "deload_out_of_range"),
        ("set_cut", {"sets_delta": -1}, {}, True, "ok"),
        ("set_cut", {"sets_delta": -2}, {}, False, "cut_too_big"),
        ("stall_fix", {"exercise_id": "x", "fix": "vary_or_add_set"}, {}, True, "ok"),
        (
            "stall_fix",
            {"exercise_id": "", "fix": "vary_or_add_set"},
            {},
            False,
            "invalid_payload",
        ),
        (
            "load_change",
            {"exercise_id": "x", "action": "double", "from_kg": 60, "to_kg": 62.5},
            {},
            False,
            "invalid_payload",
        ),
        (
            "stall_fix",
            {"exercise_id": "x", "fix": "take_a_month_off"},
            {},
            False,
            "invalid_payload",
        ),
        ("delete_account", {}, {}, False, "unknown_kind"),
    ],
)
def test_validate_recommendation(kind, payload, user, allowed, reason) -> None:
    verdict = validate_recommendation(kind, payload, state(**user))
    assert (verdict.allowed, verdict.reason) == (allowed, reason)


def test_every_kind_is_handled() -> None:
    assert {k.value for k in Kind} == {
        "load_change",
        "calorie_change",
        "protein_change",
        "deload",
        "set_cut",
        "stall_fix",
    }


# --- the weekly review ------------------------------------------------------------------


def history(
    name: str, *sessions: SessionResult, mechanic: Mechanic = C
) -> ExerciseHistory:
    return ExerciseHistory(name, name.title(), 8, 10, mechanic, tuple(sessions))


def review(**override) -> ReviewInput:
    values = {
        "state": state(),
        "exercises": (),
        "weekly_changes": (0.3, 0.3),
        "inbody": None,
        "hard_weeks": 1,
        "readiness_scores": (70, 75, 80),
    }
    values.update(override)
    return ReviewInput(**values)


def test_weekly_review_bundles_every_rule() -> None:
    recs = weekly_review(
        review(
            exercises=(
                history("bench", session(60, 10, 10, 10)),
                history("row", session(50, 7, 6), session(50, 6, 6, days=3)),
                history(
                    "curl",
                    session(12, 9, 9),
                    session(12, 9, 9, days=3),
                    session(12, 9, 9, days=6),
                    mechanic=I,
                ),
                history("press", session(40, 9, 8)),
            ),
            weekly_changes=(0.1, 0.1),
        )
    )
    kinds = [(r.kind, r.payload.get("exercise_id"), r.reason) for r in recs]
    assert kinds == [
        (Kind.LOAD_CHANGE, "bench", "top_of_range"),
        (Kind.LOAD_CHANGE, "row", "failed_twice"),
        (Kind.STALL_FIX, "curl", "stalled"),
        (Kind.CALORIE_CHANGE, None, "gaining_too_slowly"),
    ]
    assert recs[0].payload == {
        "exercise_id": "bench",
        "action": "add",
        "from_kg": 60,
        "to_kg": 62.5,
    }
    assert recs[3].payload == {"change_kcal": 250}
    assert all(r.message for r in recs)


def test_a_deload_week_holds_back_weight_increases() -> None:
    recs = weekly_review(
        review(exercises=(history("bench", session(60, 10, 10, 10)),), hard_weeks=6)
    )
    assert [r.kind for r in recs] == [Kind.DELOAD]
    assert recs[0].payload == {"weeks": 1, "set_factor": 0.5}


def test_a_quiet_week_has_no_recommendations() -> None:
    assert weekly_review(review(exercises=(history("bench", session(60, 9, 9)),))) == []
