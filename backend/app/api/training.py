import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import DbSession, HealthUser, get_owned_or_404, not_in_future
from app.api.deps import require_plan_access as plan_access_check
from app.models import SetLog, User, WorkoutSession
from app.rules.program_validator import VOLUME_RANGES
from app.rules.training_stats import BestSet, personal_best
from app.schemas.training import (
    AddSetIn,
    BestSetOut,
    ExerciseLastOut,
    FinishOut,
    LastSetOut,
    PersonalBestOut,
    SessionList,
    SessionOut,
    SessionSummary,
    SetOut,
    SetPatchIn,
    StartSessionIn,
    VolumeOut,
    WeekVolumeOut,
)
from app.services import exercises as exercise_svc
from app.services import onboarding
from app.services import training as svc

router = APIRouter(prefix="/sessions", tags=["training"])
stats_router = APIRouter(prefix="/stats", tags=["training"])
exercise_router = APIRouter(prefix="/exercises", tags=["training"])


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _set_out(row: SetLog, names: dict[uuid.UUID, str]) -> SetOut:
    return SetOut(
        id=row.id,
        exercise_id=row.exercise_id,
        exercise_name=names.get(row.exercise_id, ""),
        program_exercise_id=row.program_exercise_id,
        set_number=row.set_number,
        weight_kg=_float(row.weight_kg),
        reps=row.reps,
        rpe=_float(row.rpe),
        done=row.done,
        is_warmup=row.is_warmup,
        target_reps_min=row.target_reps_min,
        target_reps_max=row.target_reps_max,
        target_weight_kg=_float(row.target_weight_kg),
    )


def _summary(session: WorkoutSession, day_name: str | None) -> SessionSummary:
    return SessionSummary(
        id=session.id,
        program_day_id=session.program_day_id,
        day_name=day_name,
        performed_on=session.performed_on,
        started_at=session.started_at,
        ended_at=session.ended_at,
        sets_done=sum(1 for s in session.sets if s.done),
        sets_planned=len(session.sets),
        volume_kg=round(svc.session_volume(session), 1),
    )


def _session_out(db: DbSession, session: WorkoutSession) -> SessionOut:
    names = svc.exercise_names(db, [session])
    days = svc.day_names(db, [session])
    day = days.get(session.program_day_id) if session.program_day_id else None
    return SessionOut(
        **_summary(session, day).model_dump(),
        notes=session.notes,
        sets=[_set_out(s, names) for s in svc.ordered_sets(db, session)],
    )


def _owned_session(db: DbSession, session_id: uuid.UUID, user: User) -> WorkoutSession:
    return get_owned_or_404(db, WorkoutSession, session_id, user)


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def start(body: StartSessionIn, db: DbSession, user: HealthUser) -> SessionOut:
    """Start a workout. From a program day, the planned sets are created up front."""
    if body.program_day_id is not None:
        plan_access_check(user, db)  # coaching stops while the screening blocks it
    performed_on = not_in_future(
        body.performed_on or onboarding.user_today(db, user), db, user
    )
    try:
        session = svc.start_session(
            db, user, program_day_id=body.program_day_id, performed_on=performed_on
        )
    except svc.OpenSessionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "session_open",
                "session_id": str(exc.session_id) if exc.session_id else None,
                "message": "Finish or delete your open workout first.",
            },
        ) from None
    except svc.ProgramDayNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That day is not in your active program.",
        ) from None
    return _session_out(db, session)


@router.get("", response_model=SessionList)
def history(
    db: DbSession,
    user: HealthUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SessionList:
    sessions, total = svc.list_sessions(db, user, limit=limit, offset=offset)
    days = svc.day_names(db, sessions)
    return SessionList(
        items=[
            _summary(s, days.get(s.program_day_id) if s.program_day_id else None)
            for s in sessions
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/open", response_model=SessionOut)
def get_open(db: DbSession, user: HealthUser) -> SessionOut:
    session = svc.open_session(db, user)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No open workout.")
    return _session_out(db, session)


@router.get("/{session_id}", response_model=SessionOut)
def get_session(session_id: uuid.UUID, db: DbSession, user: HealthUser) -> SessionOut:
    return _session_out(db, _owned_session(db, session_id, user))


@router.patch("/{session_id}/sets/{set_id}", response_model=SetOut)
def patch_set(
    session_id: uuid.UUID,
    set_id: uuid.UUID,
    body: SetPatchIn,
    db: DbSession,
    user: HealthUser,
) -> SetOut:
    session = _owned_session(db, session_id, user)
    try:
        row = svc.update_set(db, session, set_id, body)
    except svc.SetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found") from None
    except svc.DoneWithoutRepsError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Enter the reps before marking a set done.",
        ) from None
    return _set_out(row, svc.exercise_names(db, [session]))


@router.post(
    "/{session_id}/sets", response_model=SetOut, status_code=status.HTTP_201_CREATED
)
def add_set(
    session_id: uuid.UUID, body: AddSetIn, db: DbSession, user: HealthUser
) -> SetOut:
    session = _owned_session(db, session_id, user)
    try:
        row = svc.add_set(db, user, session, body)
    except exercise_svc.ExerciseNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found") from None
    return _set_out(row, svc.exercise_names(db, [session]))


@router.post("/{session_id}/finish", response_model=FinishOut)
def finish(session_id: uuid.UUID, db: DbSession, user: HealthUser) -> FinishOut:
    session = _owned_session(db, session_id, user)
    try:
        records = svc.finish_session(db, user, session)
    except svc.SessionFinishedError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="This workout is already finished."
        ) from None
    return FinishOut(
        **_session_out(db, session).model_dump(), new_records=sorted(records)
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(session_id: uuid.UUID, db: DbSession, user: HealthUser) -> Response:
    svc.delete_session(db, _owned_session(db, session_id, user))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _best(value: BestSet | None) -> BestSetOut | None:
    if value is None:
        return None
    return BestSetOut(
        weight_kg=value.weight_kg,
        reps=value.reps,
        date=value.date,
        estimated_1rm_kg=round(value.estimated_1rm_kg, 1)
        if value.estimated_1rm_kg is not None
        else None,
    )


@exercise_router.get("/{exercise_id}/last", response_model=ExerciseLastOut)
def exercise_last(
    exercise_id: uuid.UUID, db: DbSession, user: HealthUser
) -> ExerciseLastOut:
    """The last time this exercise was done, and the user's personal best."""
    try:
        exercise_svc.get_visible(db, user, exercise_id)
    except exercise_svc.ExerciseNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found") from None
    last_date, last_sets, history_sets = svc.last_performance(db, user, exercise_id)
    best = personal_best(history_sets)
    return ExerciseLastOut(
        exercise_id=exercise_id,
        last_date=last_date,
        last_sets=[
            LastSetOut(
                set_number=s.set_number,
                weight_kg=_float(s.weight_kg),
                reps=s.reps or 0,
                rpe=_float(s.rpe),
            )
            for s in last_sets
        ],
        personal_best=PersonalBestOut(
            best_e1rm=_best(best.best_e1rm),
            heaviest=_best(best.heaviest),
            most_reps=best.most_reps,
        )
        if best
        else None,
    )


@stats_router.get("/volume", response_model=VolumeOut)
def volume(
    db: DbSession,
    user: HealthUser,
    weeks: Annotated[int, Query(ge=1, le=26)] = 4,
) -> VolumeOut:
    """Completed hard sets per muscle per week (secondary muscles count half)."""
    by_week = svc.volume_by_week(
        db, user, weeks=weeks, today=onboarding.user_today(db, user)
    )
    profile = onboarding.get_profile(db, user.id)
    experience = profile.experience if profile else None
    target = VOLUME_RANGES.get(experience) if experience else None
    return VolumeOut(
        weeks=[
            WeekVolumeOut(week_start=week, sets_by_muscle=dict(sorted(sets.items())))
            for week, sets in sorted(by_week.items(), reverse=True)
        ],
        target_min=target[0] if target else None,
        target_max=target[1] if target else None,
    )
