"""Workout sessions and set logging. Statistics come from app/rules/training_stats.py."""

import datetime as dt
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Exercise,
    Program,
    ProgramDay,
    ProgramExercise,
    SetLog,
    User,
    WorkoutSession,
)
from app.models.enums import ProgramStatus
from app.rules.exercise_catalog import CatalogExercise
from app.rules.training_stats import (
    LoggedSet,
    PersonalBest,
    counts_as_work,
    last_top_weight,
    new_records,
    personal_best,
    week_start,
    weekly_volume,
)
from app.schemas.training import AddSetIn, SetPatchIn
from app.services import exercises as exercise_svc


class OpenSessionError(Exception):
    def __init__(self, session_id: uuid.UUID | None) -> None:
        super().__init__("a session is already open")
        self.session_id = session_id


class SessionFinishedError(Exception):
    pass


class ProgramDayNotFoundError(Exception):
    pass


class SetNotFoundError(Exception):
    pass


class DoneWithoutRepsError(Exception):
    pass


class NotASubstituteError(Exception):
    pass


class NothingToSwapError(Exception):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _dec(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _logged(row: SetLog, performed_on: dt.date) -> LoggedSet:
    return LoggedSet(
        exercise_id=str(row.exercise_id),
        weight_kg=float(row.weight_kg) if row.weight_kg is not None else None,
        reps=row.reps,
        done=row.done,
        is_warmup=row.is_warmup,
        performed_on=performed_on,
    )


def logged_sets(
    db: Session,
    user: User,
    *,
    exercise_ids: list[uuid.UUID] | None = None,
    since: dt.date | None = None,
    exclude_session: uuid.UUID | None = None,
    finished_only: bool = False,
) -> list[LoggedSet]:
    """The user's done sets as plain rule inputs."""
    query = (
        sa.select(SetLog, WorkoutSession.performed_on)
        .join(WorkoutSession, SetLog.workout_session_id == WorkoutSession.id)
        .where(SetLog.user_id == user.id, SetLog.done.is_(True))
    )
    if exercise_ids is not None:
        query = query.where(SetLog.exercise_id.in_(exercise_ids))
    if since is not None:
        query = query.where(WorkoutSession.performed_on >= since)
    if exclude_session is not None:
        query = query.where(SetLog.workout_session_id != exclude_session)
    if finished_only:
        query = query.where(WorkoutSession.ended_at.is_not(None))
    return [_logged(row, on) for row, on in db.execute(query)]


def records(
    db: Session, user: User
) -> list[tuple[uuid.UUID, str, PersonalBest, dt.date]]:
    """Personal bests per exercise from finished workouts, newest record first."""
    by_exercise: dict[str, list[LoggedSet]] = defaultdict(list)
    for s in logged_sets(db, user, finished_only=True):
        by_exercise[s.exercise_id].append(s)
    ids = [uuid.UUID(e) for e in by_exercise]
    names: dict[uuid.UUID, str] = {}
    if ids:
        rows = db.execute(
            sa.select(Exercise.id, Exercise.name).where(Exercise.id.in_(ids))
        )
        names = {key: value for key, value in rows}
    found = []
    for exercise_id, sets in by_exercise.items():
        best = personal_best(sets)
        if best is None:
            continue
        dates = [b.date for b in (best.best_e1rm, best.heaviest) if b is not None]
        latest = max(dates, default=max(s.performed_on for s in sets))
        key = uuid.UUID(exercise_id)
        found.append((key, names.get(key, ""), best, latest))
    return sorted(found, key=lambda r: (r[3], r[1]), reverse=True)


def open_session(db: Session, user: User) -> WorkoutSession | None:
    return db.scalar(
        sa.select(WorkoutSession).where(
            WorkoutSession.user_id == user.id, WorkoutSession.ended_at.is_(None)
        )
    )


def _active_program_day(
    db: Session, user: User, program_day_id: uuid.UUID
) -> ProgramDay:
    day = db.scalar(
        sa.select(ProgramDay)
        .join(Program, ProgramDay.program_id == Program.id)
        .where(
            ProgramDay.id == program_day_id,
            ProgramDay.user_id == user.id,
            Program.status == ProgramStatus.ACTIVE,
        )
        .options(selectinload(ProgramDay.exercises))
    )
    if day is None:
        raise ProgramDayNotFoundError
    return day


def _planned_sets(
    db: Session, user: User, exercises: list[ProgramExercise]
) -> list[SetLog]:
    history = logged_sets(db, user, exercise_ids=[pe.exercise_id for pe in exercises])
    by_exercise: dict[str, list[LoggedSet]] = defaultdict(list)
    for s in history:
        by_exercise[s.exercise_id].append(s)

    rows: list[SetLog] = []
    numbers: dict[uuid.UUID, int] = defaultdict(int)  # an exercise may appear twice
    for pe in exercises:
        target = last_top_weight(by_exercise[str(pe.exercise_id)])
        for _ in range(pe.sets):
            numbers[pe.exercise_id] += 1
            rows.append(
                SetLog(
                    user_id=user.id,
                    exercise_id=pe.exercise_id,
                    program_exercise_id=pe.id,
                    set_number=numbers[pe.exercise_id],
                    target_reps_min=pe.rep_min,
                    target_reps_max=pe.rep_max,
                    target_weight_kg=_dec(target),
                )
            )
    return rows


def start_session(
    db: Session,
    user: User,
    *,
    program_day_id: uuid.UUID | None,
    performed_on: dt.date,
) -> WorkoutSession:
    """Start a session; from a program day it pre-creates the planned sets.

    Targets carry the last working weight for each exercise. Progression is decided
    later by the adaptive rules, never here.
    """
    existing = open_session(db, user)
    if existing is not None:
        raise OpenSessionError(existing.id)
    day = _active_program_day(db, user, program_day_id) if program_day_id else None

    session = WorkoutSession(
        user_id=user.id,
        program_day_id=day.id if day else None,
        performed_on=performed_on,
        started_at=_now(),
    )
    if day is not None:
        session.sets = _planned_sets(db, user, list(day.exercises))
    try:
        with db.begin_nested():
            db.add(session)
            db.flush()
    except IntegrityError as exc:  # another start won the race
        raise OpenSessionError(None) from exc
    db.commit()
    return session


def get_set(session: WorkoutSession, set_id: uuid.UUID) -> SetLog:
    for row in session.sets:
        if row.id == set_id:
            return row
    raise SetNotFoundError


def update_set(
    db: Session, session: WorkoutSession, set_id: uuid.UUID, patch: SetPatchIn
) -> SetLog:
    row = get_set(session, set_id)
    changes: dict[str, Any] = patch.model_dump(exclude_unset=True)
    reps = changes.get("reps", row.reps)
    done = row.done if changes.get("done") is None else changes["done"]
    if done and reps is None:
        raise DoneWithoutRepsError
    if "weight_kg" in changes:
        row.weight_kg = _dec(changes["weight_kg"])
    if "rpe" in changes:
        row.rpe = _dec(changes["rpe"])
    if changes.get("is_warmup") is not None:
        row.is_warmup = changes["is_warmup"]
    row.reps = reps
    row.done = done
    db.commit()
    return row


def add_set(db: Session, user: User, session: WorkoutSession, data: AddSetIn) -> SetLog:
    """An extra set (or an exercise not in the plan). Numbered after the existing ones."""
    exercise_svc.get_visible(db, user, data.exercise_id)
    same = [s for s in session.sets if s.exercise_id == data.exercise_id]
    planned = [s.program_exercise_id for s in same if s.program_exercise_id]
    taken = [s.set_number for s in same]
    row = SetLog(
        user_id=user.id,
        workout_session_id=session.id,
        exercise_id=data.exercise_id,
        # An extra set of a planned exercise is shown with that exercise.
        program_exercise_id=planned[-1] if planned else None,
        set_number=max(taken, default=0) + 1,
        weight_kg=_dec(data.weight_kg),
        reps=data.reps,
        rpe=_dec(data.rpe),
        done=data.done,
        is_warmup=data.is_warmup,
    )
    session.sets.append(row)
    db.commit()
    return row


def swap_exercise(
    db: Session,
    user: User,
    session: WorkoutSession,
    program_exercise_id: uuid.UUID,
    exercise_id: uuid.UUID,
) -> None:
    """Do another exercise for the sets of a planned slot that are not done yet.

    Done sets stay with the exercise they were lifted on. The new exercise must be a
    valid substitute for the planned one (same muscle, the user's equipment, no active
    limitation against it); going back to the planned exercise is always allowed.
    Only this workout changes, never the program.
    """
    if session.ended_at is not None:
        raise SessionFinishedError
    planned = db.get(ProgramExercise, program_exercise_id)
    in_slot = [s for s in session.sets if s.program_exercise_id == program_exercise_id]
    if planned is None or planned.user_id != user.id or not in_slot:
        raise SetNotFoundError
    new = exercise_svc.get_visible(db, user, exercise_id)
    if new.id != planned.exercise_id and not exercise_svc.can_substitute(
        db, user, exercise_svc.get_visible(db, user, planned.exercise_id), new
    ):
        raise NotASubstituteError
    remaining = [s for s in in_slot if not s.done]
    if not remaining:
        raise NothingToSwapError

    target = _dec(last_top_weight(logged_sets(db, user, exercise_ids=[new.id])))
    moving = {s.id for s in remaining}
    taken = [
        s.set_number
        for s in session.sets
        if s.exercise_id == new.id and s.id not in moving
    ]
    # Renumber away from the unique (session, exercise, set) key before reassigning.
    for row in remaining:
        row.set_number = -row.set_number
    db.flush()
    for number, row in enumerate(
        sorted(remaining, key=lambda s: -s.set_number), start=max(taken, default=0) + 1
    ):
        row.exercise_id = new.id
        row.set_number = number
        row.weight_kg = None
        row.reps = None
        row.rpe = None
        row.target_weight_kg = target
    db.commit()


def finish_session(db: Session, user: User, session: WorkoutSession) -> set[uuid.UUID]:
    """Close the session and return the exercises where it set a personal best."""
    if session.ended_at is not None:
        raise SessionFinishedError
    session.ended_at = max(_now(), session.started_at or _now())
    db.commit()

    done = [_logged(s, session.performed_on) for s in session.sets if s.done]
    ids = sorted({s.exercise_id for s in session.sets if s.done})
    before = [
        s
        for s in logged_sets(db, user, exercise_ids=ids, exclude_session=session.id)
        if s.performed_on <= session.performed_on
    ]
    return {uuid.UUID(e) for e in new_records(before, done)}


def ordered_sets(db: Session, session: WorkoutSession) -> list[SetLog]:
    """Planned exercises in program order, then unplanned ones in the order added.

    A swapped slot lists the planned exercise's sets before the substitute's.
    """
    planned_ids = {s.program_exercise_id for s in session.sets if s.program_exercise_id}
    order: dict[uuid.UUID, int] = {}
    planned_exercise: dict[uuid.UUID, uuid.UUID] = {}
    if planned_ids:
        rows = db.execute(
            sa.select(
                ProgramExercise.id, ProgramExercise.order, ProgramExercise.exercise_id
            ).where(ProgramExercise.id.in_(planned_ids))
        )
        for key_, value, exercise_id in rows:
            order[key_] = value
            planned_exercise[key_] = exercise_id
    unplanned = len(order) + 1

    def key(s: SetLog) -> tuple[int, dt.datetime, bool, str, int]:
        rank = (
            order.get(s.program_exercise_id, unplanned)
            if s.program_exercise_id
            else unplanned
        )
        swapped = (
            s.program_exercise_id is not None
            and planned_exercise.get(s.program_exercise_id) != s.exercise_id
        )
        return (
            rank,
            s.created_at if rank == unplanned else _EPOCH,
            swapped,
            str(s.exercise_id),
            s.set_number,
        )

    return sorted(session.sets, key=key)


_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)


def delete_session(db: Session, session: WorkoutSession) -> None:
    db.delete(session)
    db.commit()


def list_sessions(
    db: Session, user: User, *, limit: int, offset: int
) -> tuple[list[WorkoutSession], int]:
    base = sa.select(WorkoutSession).where(WorkoutSession.user_id == user.id)
    total = db.scalar(sa.select(sa.func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.options(selectinload(WorkoutSession.sets))
        .order_by(
            WorkoutSession.performed_on.desc(),
            WorkoutSession.started_at.desc().nulls_last(),
        )
        .limit(limit)
        .offset(offset)
    )
    return list(rows), total


def session_volume(session: WorkoutSession) -> float:
    return sum(
        float(s.weight_kg or 0) * (s.reps or 0)
        for s in session.sets
        if counts_as_work(_logged(s, session.performed_on))
    )


def day_names(db: Session, sessions: list[WorkoutSession]) -> dict[uuid.UUID, str]:
    ids = {s.program_day_id for s in sessions if s.program_day_id}
    if not ids:
        return {}
    rows = db.execute(
        sa.select(ProgramDay.id, ProgramDay.name).where(ProgramDay.id.in_(ids))
    )
    return {key: value for key, value in rows}


def exercise_names(db: Session, sessions: list[WorkoutSession]) -> dict[uuid.UUID, str]:
    ids = {row.exercise_id for s in sessions for row in s.sets}
    if not ids:
        return {}
    rows = db.execute(sa.select(Exercise.id, Exercise.name).where(Exercise.id.in_(ids)))
    return {key: value for key, value in rows}


def last_performance(
    db: Session, user: User, exercise_id: uuid.UUID
) -> tuple[dt.date | None, list[SetLog], list[LoggedSet]]:
    """The most recent session's working sets for an exercise, plus the full history.

    Only finished workouts count, so "last time" stays put while today's is in progress.
    """
    rows = db.execute(
        sa.select(SetLog, WorkoutSession.performed_on)
        .join(WorkoutSession, SetLog.workout_session_id == WorkoutSession.id)
        .where(
            SetLog.user_id == user.id,
            SetLog.exercise_id == exercise_id,
            SetLog.done.is_(True),
            WorkoutSession.ended_at.is_not(None),
        )
        .order_by(
            WorkoutSession.performed_on.desc(),
            WorkoutSession.started_at.desc().nulls_last(),
            SetLog.set_number,
        )
    ).all()
    history = [_logged(row, on) for row, on in rows]
    work = [(row, on) for row, on in rows if counts_as_work(_logged(row, on))]
    if not work:
        return None, [], history
    last_row, last_date = work[0]
    latest = last_row.workout_session_id
    return last_date, [r for r, _ in work if r.workout_session_id == latest], history


def volume_by_week(
    db: Session, user: User, *, weeks: int, today: dt.date
) -> dict[dt.date, dict[str, float]]:
    since = week_start(today) - dt.timedelta(weeks=weeks - 1)
    sets = logged_sets(db, user, since=since)
    ids = {uuid.UUID(s.exercise_id) for s in sets}
    catalog: dict[str, CatalogExercise] = {}
    if ids:
        for ex in db.scalars(sa.select(Exercise).where(Exercise.id.in_(ids))):
            catalog[str(ex.id)] = exercise_svc.catalog_view(ex)
    volume = weekly_volume(sets, catalog)
    return {
        since + dt.timedelta(weeks=i): volume.get(since + dt.timedelta(weeks=i), {})
        for i in range(weeks)
    }
