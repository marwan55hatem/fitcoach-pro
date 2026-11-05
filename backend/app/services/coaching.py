"""Gather a user's current data into the inputs the coaching rules expect.

The rules themselves stay pure (app/rules); this is the only place that reads the
database for them. Used by the coach's tools and, later, the weekly review.
"""

import datetime as dt
import uuid
from collections import defaultdict

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models import (
    Checkin,
    Exercise,
    NutritionSettings,
    SetLog,
    User,
    WeightLog,
    WorkoutSession,
)
from app.models.enums import Goal, Mechanic
from app.rules.calories import InbodyChange
from app.rules.limits import UserState
from app.rules.nutrition import PROTEIN_G_PER_KG
from app.rules.progression import SessionResult, WorkSet
from app.rules.recovery import Checkin as CheckinScores
from app.rules.recovery import readiness
from app.rules.review import ExerciseHistory, ReviewInput
from app.rules.training_stats import week_start
from app.rules.weight_trend import WeightPoint, weekly_change
from app.services import inbody, onboarding, programs

HISTORY_SESSIONS = 6  # per exercise, enough for progression and stall checks
TREND_WEEKS = 3
MAX_HARD_WEEKS = 12


def user_state(db: Session, user: User) -> UserState:
    """The state every proposal is checked against, as it is right now."""
    profile = onboarding.get_profile(db, user.id)
    goal = profile.goal if profile and profile.goal else Goal.MAINTAIN
    weight = onboarding.latest_weight(db, user.id)
    settings = db.scalar(
        sa.select(NutritionSettings).where(NutritionSettings.user_id == user.id)
    )
    per_kg = (
        float(settings.protein_g_per_kg)
        if settings and settings.protein_g_per_kg is not None
        else PROTEIN_G_PER_KG[goal]
    )
    access = onboarding.onboarding_state(db, user).access
    return UserState(
        goal=goal,
        weight_kg=float(weight.weight_kg) if weight else 0.0,
        calorie_adjustment_kcal=settings.calorie_adjustment_kcal if settings else 0,
        protein_g_per_kg=per_kg,
        targets_withheld=not access.numeric_diet_targets,
    )


def _exercise_histories(db: Session, user: User) -> tuple[ExerciseHistory, ...]:
    program = programs.current_program(db, user)
    if program is None:
        return ()
    planned: dict[uuid.UUID, tuple[int, int]] = {}
    for day in program.days:
        for item in day.exercises:
            planned.setdefault(item.exercise_id, (item.rep_min, item.rep_max))
    if not planned:
        return ()
    catalog = {
        e.id: e for e in db.scalars(sa.select(Exercise).where(Exercise.id.in_(planned)))
    }
    rows = db.execute(
        sa.select(SetLog, WorkoutSession.performed_on, WorkoutSession.started_at)
        .join(WorkoutSession, SetLog.workout_session_id == WorkoutSession.id)
        .where(
            SetLog.user_id == user.id,
            SetLog.exercise_id.in_(planned),
            SetLog.done.is_(True),
            SetLog.is_warmup.is_(False),
            WorkoutSession.ended_at.is_not(None),
        )
        .order_by(WorkoutSession.performed_on, WorkoutSession.started_at)
    ).all()
    sessions: dict[uuid.UUID, dict[uuid.UUID, list[WorkSet]]] = defaultdict(dict)
    dates: dict[uuid.UUID, dt.date] = {}
    for row, performed_on, _started in rows:
        sets = sessions[row.exercise_id].setdefault(row.workout_session_id, [])
        sets.append(WorkSet(float(row.weight_kg or 0), row.reps or 0))
        dates[row.workout_session_id] = performed_on
    out: list[ExerciseHistory] = []
    for exercise_id, (rep_min, rep_max) in planned.items():
        exercise = catalog.get(exercise_id)
        if exercise is None:
            continue
        history = tuple(
            SessionResult(dates[sid], tuple(sets))
            for sid, sets in sessions[exercise_id].items()  # in the order they ran
        )[-HISTORY_SESSIONS:]
        out.append(
            ExerciseHistory(
                str(exercise_id),
                exercise.name,
                rep_min,
                rep_max,
                exercise.mechanic or Mechanic.COMPOUND,
                history,
            )
        )
    return tuple(out)


def _weekly_changes(db: Session, user: User, today: dt.date) -> tuple[float, ...]:
    since = today - dt.timedelta(days=7 * (TREND_WEEKS + 2))
    points = [
        WeightPoint(d, float(kg))
        for d, kg in db.execute(
            sa.select(WeightLog.date, WeightLog.weight_kg).where(
                WeightLog.user_id == user.id, WeightLog.date >= since
            )
        )
    ]
    changes = [
        weekly_change(points, as_of=today - dt.timedelta(days=7 * weeks_ago))
        for weeks_ago in range(TREND_WEEKS - 1, -1, -1)
    ]
    return tuple(round(c, 2) for c in changes if c is not None)


def _inbody_change(db: Session, user: User) -> InbodyChange | None:
    scans = inbody.list_scans(db, user, confirmed=True)[:2]  # newest first
    if len(scans) < 2:
        return None
    new, old = scans
    if None in (new.smm_kg, old.smm_kg, new.body_fat_mass_kg, old.body_fat_mass_kg):
        return None
    return InbodyChange(
        float(new.smm_kg - old.smm_kg),  # type: ignore[operator]
        float(new.body_fat_mass_kg - old.body_fat_mass_kg),  # type: ignore[operator]
    )


def _hard_weeks(db: Session, user: User, today: dt.date) -> int:
    """Consecutive finished weeks (before this one) with the planned training done."""
    profile = onboarding.get_profile(db, user.id)
    needed = max(
        2, (profile.days_per_week if profile and profile.days_per_week else 3) - 1
    )
    first_week = week_start(today) - dt.timedelta(weeks=MAX_HARD_WEEKS)
    counts: dict[dt.date, int] = defaultdict(int)
    for (day,) in db.execute(
        sa.select(WorkoutSession.performed_on).where(
            WorkoutSession.user_id == user.id,
            WorkoutSession.ended_at.is_not(None),
            WorkoutSession.performed_on >= first_week,
        )
    ):
        counts[week_start(day)] += 1
    weeks = 0
    week = week_start(today) - dt.timedelta(weeks=1)
    while weeks < MAX_HARD_WEEKS and counts[week] >= needed:
        weeks += 1
        week -= dt.timedelta(weeks=1)
    return weeks


def _readiness_scores(db: Session, user: User, today: dt.date) -> tuple[int, ...]:
    rows = db.scalars(
        sa.select(Checkin)
        .where(Checkin.user_id == user.id, Checkin.date > today - dt.timedelta(days=7))
        .order_by(Checkin.date)
    )
    scores = []
    for c in rows:
        result = readiness(
            [
                CheckinScores(
                    c.date,
                    float(c.sleep_h) if c.sleep_h is not None else None,
                    c.soreness,
                    c.stress,
                    c.energy,
                    c.motivation,
                )
            ]
        )
        if result is not None:
            scores.append(result.score)
    return tuple(scores)


def review_input(db: Session, user: User, today: dt.date | None = None) -> ReviewInput:
    today = today or onboarding.user_today(db, user)
    return ReviewInput(
        state=user_state(db, user),
        exercises=_exercise_histories(db, user),
        weekly_changes=_weekly_changes(db, user, today),
        inbody=_inbody_change(db, user),
        hard_weeks=_hard_weeks(db, user, today),
        readiness_scores=_readiness_scores(db, user, today),
    )
