"""Property tests: for any input, nothing the rules allow ever breaks a hard limit."""

import datetime as dt
import math
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from app.models.enums import Goal, Mechanic
from app.rules.calories import InbodyChange, calorie_adjustment
from app.rules.limits import (
    BULK_GOAL_SURPLUS_KCAL,
    DELOAD_SET_FACTOR_RANGE,
    DELOAD_WEEKS_RANGE,
    MAX_CALORIE_CHANGE_KCAL,
    MAX_DECREASE_KG_FLOOR,
    MAX_DECREASE_SHARE,
    MAX_INCREASE_KG,
    MAX_TOTAL_ADJUSTMENT_KCAL,
    PROTEIN_RANGE_G_PER_KG,
    UserState,
    validate_recommendation,
)
from app.rules.progression import Action, SessionResult, WorkSet, progression
from app.rules.recovery import Checkin, readiness, session_sets
from app.rules.review import ExerciseHistory, ReviewInput, propose, weekly_review

DAY = dt.date(2026, 1, 5)

weights = st.one_of(
    st.just(0.0),
    st.floats(min_value=0.5, max_value=300, allow_nan=False).map(
        lambda w: round(w * 2) / 2
    ),
)
reps = st.integers(min_value=0, max_value=30)
sessions = st.lists(
    st.builds(
        lambda i, w, rs: SessionResult(
            DAY + dt.timedelta(days=3 * i), tuple(WorkSet(w, r) for r in rs)
        ),
        st.integers(0, 50),
        weights,
        st.lists(reps, min_size=1, max_size=6),
    ),
    max_size=6,
).map(lambda xs: sorted(xs, key=lambda s: s.performed_on))
rep_ranges = st.tuples(st.integers(1, 15), st.integers(0, 10)).map(
    lambda t: (t[0], t[0] + t[1])
)
mechanics = st.sampled_from(list(Mechanic))
states = st.builds(
    UserState,
    goal=st.sampled_from(list(Goal)),
    weight_kg=st.floats(40, 200),
    calorie_adjustment_kcal=st.integers(
        -MAX_TOTAL_ADJUSTMENT_KCAL, MAX_TOTAL_ADJUSTMENT_KCAL
    ),
    protein_g_per_kg=st.floats(*PROTEIN_RANGE_G_PER_KG),
    targets_withheld=st.booleans(),
)
changes = st.lists(st.floats(-2, 2, allow_nan=False), max_size=6)
inbody = st.one_of(
    st.none(), st.builds(InbodyChange, st.floats(-3, 3), st.floats(-3, 3))
)
scores = st.lists(st.integers(0, 100), max_size=7)


def within_limits(kind: str, payload: dict[str, Any], s: UserState) -> bool:
    """An independent statement of the hard limits, to check the validator against."""
    if kind == "load_change":
        delta = payload["to_kg"] - payload["from_kg"]
        if payload["to_kg"] < 0:
            return False
        if payload["action"] == "add":
            return 0 < delta <= MAX_INCREASE_KG
        return (
            0
            < -delta
            <= max(MAX_DECREASE_SHARE * payload["from_kg"], MAX_DECREASE_KG_FLOOR)
        )
    if kind == "calorie_change":
        c = payload["change_kcal"]
        total = s.calorie_adjustment_kcal + c
        return (
            not s.targets_withheld
            and 0 < abs(c) <= MAX_CALORIE_CHANGE_KCAL
            and abs(total) <= MAX_TOTAL_ADJUSTMENT_KCAL
            and (s.goal is not Goal.LEAN_BULK or BULK_GOAL_SURPLUS_KCAL + total >= 0)
        )
    if kind == "protein_change":
        low, high = PROTEIN_RANGE_G_PER_KG
        return not s.targets_withheld and low <= payload["g_per_kg"] <= high
    if kind == "deload":
        return (
            DELOAD_WEEKS_RANGE[0] <= payload["weeks"] <= DELOAD_WEEKS_RANGE[1]
            and DELOAD_SET_FACTOR_RANGE[0]
            <= payload["set_factor"]
            <= DELOAD_SET_FACTOR_RANGE[1]
        )
    if kind == "set_cut":
        return payload["sets_delta"] == -1
    return kind == "stall_fix"


@given(sessions, rep_ranges, mechanics)
def test_progression_never_jumps_more_than_the_limits(
    history, rep_range, mechanic
) -> None:
    result = progression(history, *rep_range, mechanic)
    if result.action is Action.KEEP:
        assert result.change_kg == 0
        return
    before = result.weight_kg - result.change_kg
    payload = {
        "exercise_id": "x",
        "action": result.action.value,
        "from_kg": before,
        "to_kg": result.weight_kg,
    }
    assert result.weight_kg >= 0
    assert within_limits(
        "load_change", payload, UserState(Goal.MAINTAIN, 70, 0, 1.6, False)
    )


@given(states, changes, inbody)
def test_calorie_advice_is_always_within_the_limits(s, weekly, change) -> None:
    advice = calorie_adjustment(s, weekly, change)
    if advice is not None:
        assert within_limits("calorie_change", {"change_kcal": advice.change_kcal}, s)


@given(
    st.lists(
        st.builds(
            Checkin,
            st.just(DAY),
            st.one_of(st.none(), st.floats(0, 24)),
            *[st.one_of(st.none(), st.integers(1, 5))] * 4,
        ),
        max_size=3,
    ),
    st.integers(1, 20),
)
def test_readiness_cuts_at_most_one_set(checkins, planned) -> None:
    result = readiness(checkins)
    if result is not None:
        assert 0 <= result.score <= 100
    assert planned - 1 <= session_sets(planned, result) <= planned
    assert session_sets(planned, result) >= 1


@settings(max_examples=150)
@given(
    states,
    st.lists(st.tuples(sessions, rep_ranges, mechanics), max_size=5),
    changes,
    inbody,
    st.integers(0, 12),
    scores,
)
def test_every_review_proposal_is_allowed(
    s, exercises, weekly, change, hard, ready
) -> None:
    inp = ReviewInput(
        state=s,
        exercises=tuple(
            ExerciseHistory(f"ex{i}", f"Exercise {i}", lo, hi, m, tuple(h))
            for i, (h, (lo, hi), m) in enumerate(exercises)
        ),
        weekly_changes=tuple(weekly),
        inbody=change,
        hard_weeks=hard,
        readiness_scores=tuple(ready),
    )
    for rec in propose(inp):
        verdict = validate_recommendation(rec.kind.value, rec.payload, s)
        assert verdict.allowed, (rec, verdict)
        assert within_limits(rec.kind.value, dict(rec.payload), s)
    assert weekly_review(inp) == propose(inp)


numbers = st.one_of(
    st.integers(-2000, 2000),
    st.floats(-2000, 2000, allow_nan=False),
    st.just(math.nan),
    st.just(math.inf),
    st.booleans(),
    st.just("60"),
    st.none(),
)


# Well-formed payloads for every kind, with any numbers at all: they reach the limits.
proposals = st.one_of(
    st.tuples(
        st.just("load_change"),
        st.fixed_dictionaries(
            {
                "exercise_id": st.just("x"),
                "action": st.sampled_from(["add", "reduce"]),
                "from_kg": numbers,
                "to_kg": numbers,
            }
        ),
    ),
    st.tuples(
        st.just("calorie_change"), st.fixed_dictionaries({"change_kcal": numbers})
    ),
    st.tuples(st.just("protein_change"), st.fixed_dictionaries({"g_per_kg": numbers})),
    st.tuples(
        st.just("deload"),
        st.fixed_dictionaries({"weeks": numbers, "set_factor": numbers}),
    ),
    st.tuples(st.just("set_cut"), st.fixed_dictionaries({"sets_delta": numbers})),
    st.tuples(
        st.just("stall_fix"),
        st.fixed_dictionaries(
            {
                "exercise_id": st.just("x"),
                "fix": st.sampled_from(["vary_or_add_set", "nonsense"]),
            }
        ),
    ),
)


@settings(max_examples=400)
@given(proposals, states)
def test_the_validator_refuses_anything_outside_the_limits(proposal, s) -> None:
    kind, payload = proposal
    """Whatever an AI might write, an allowed proposal is always inside the limits."""
    verdict = validate_recommendation(kind, payload, s)
    if verdict.allowed:
        assert within_limits(kind, payload, s)
