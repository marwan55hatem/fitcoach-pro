"""Approve or reject a proposal, from the coach or the weekly review.

Approving re-checks the proposal against the user's CURRENT state (it may have changed
since it was made) and only then applies it:
- load_change and set_cut: a NEW program version (the old one is archived);
- calorie_change and protein_change: nutrition_settings;
- deload: a lighter period scheduled on the active program;
- stall_fix: advice only, nothing to apply.
Approve is idempotent: approving an approved item returns it unchanged.
"""

import datetime as dt
import uuid
from collections.abc import Callable
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models import (
    CoachNote,
    NutritionSettings,
    Program,
    ProgramDay,
    ProgramExercise,
    Recommendation,
    User,
)
from app.models.enums import ProgramStatus, RecommendationStatus
from app.rules.coach_notes import check_note
from app.rules.limits import Kind, validate_recommendation
from app.rules.program_validator import errors_in, validate_program
from app.rules.training_stats import last_top_weight
from app.services import audit, coaching, onboarding, programs, training

PENDING_DAYS = 14
MAX_NOTE_CHARS = 500
WEIGHT_TOLERANCE_KG = 0.01


class RecommendationNotFoundError(Exception):
    pass


class NotPendingError(Exception):
    pass


class ExpiredError(Exception):
    pass


class NoLongerValidError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def expiry_from(now: dt.datetime) -> dt.datetime:
    return now + dt.timedelta(days=PENDING_DAYS)


def _is_stale(rec: Recommendation, now: dt.datetime) -> bool:
    deadline = rec.expires_at or rec.created_at + dt.timedelta(days=PENDING_DAYS)
    return deadline <= now


def expire_stale(db: Session, user_id: uuid.UUID) -> int:
    """Mark the user's pending items past their deadline as expired. Not committed."""
    now = _now()
    result = db.execute(
        sa.update(Recommendation)
        .where(
            Recommendation.user_id == user_id,
            Recommendation.status == RecommendationStatus.PENDING,
            sa.or_(
                Recommendation.expires_at <= now,
                sa.and_(
                    Recommendation.expires_at.is_(None),
                    Recommendation.created_at <= now - dt.timedelta(days=PENDING_DAYS),
                ),
            ),
        )
        .values(status=RecommendationStatus.EXPIRED, decided_at=now)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount  # type: ignore[attr-defined, no-any-return]


def _locked(db: Session, user: User, rec_id: uuid.UUID) -> Recommendation:
    """The user's item, row-locked so two taps cannot both apply it."""
    rec = db.scalar(
        sa.select(Recommendation)
        .where(Recommendation.id == rec_id, Recommendation.user_id == user.id)
        .with_for_update()
    )
    if rec is None:
        raise RecommendationNotFoundError
    return rec


def _check_open(db: Session, rec: Recommendation, now: dt.datetime) -> None:
    if rec.status is not RecommendationStatus.PENDING:
        raise NotPendingError
    if _is_stale(rec, now):
        rec.status, rec.decided_at = RecommendationStatus.EXPIRED, now
        db.commit()
        raise ExpiredError


# --- applying -------------------------------------------------------------------------


def _active(db: Session, user: User) -> Program:
    program = programs.current_program(db, user)
    if program is None:
        raise NoLongerValidError("no_program")
    return program


def _new_version(
    db: Session,
    user: User,
    old: Program,
    rec: Recommendation,
    change: Callable[[ProgramExercise], None],
) -> Program:
    """Copy the active program with `change` applied to each exercise; activate it."""
    latest = db.scalar(
        sa.select(sa.func.max(Program.version)).where(Program.user_id == user.id)
    )
    new = Program(
        user_id=user.id,
        version=(latest or 0) + 1,
        status=ProgramStatus.ACTIVE,
        generated_by="adjustment",
        rationale=old.rationale,
        recommendation_id=rec.id,
        deload_until=old.deload_until,
        deload_set_factor=old.deload_set_factor,
    )
    for day in old.days:
        copy = ProgramDay(
            user_id=user.id,
            day_index=day.day_index,
            name=day.name,
            focus=day.focus,
            rationale=day.rationale,
        )
        for item in day.exercises:
            exercise = ProgramExercise(
                user_id=user.id,
                exercise_id=item.exercise_id,
                order=item.order,
                sets=item.sets,
                rep_min=item.rep_min,
                rep_max=item.rep_max,
                rest_s=item.rest_s,
                notes=item.notes,
                target_weight_kg=item.target_weight_kg,
            )
            change(exercise)
            copy.exercises.append(exercise)
        new.days.append(copy)
    if errors_in(
        validate_program(programs.draft_of(new), programs.build_context(db, user))
    ):
        raise NoLongerValidError("program_invalid")
    # Archive first: a partial unique index allows only one active program.
    old.status = ProgramStatus.ARCHIVED
    db.flush()
    db.add(new)
    db.flush()
    audit.record(
        db,
        "program.adjusted",
        user.id,
        entity_type="program",
        entity_id=new.id,
        meta={"kind": rec.kind, "version": new.version},
    )
    return new


def _current_target(db: Session, user: User, item: ProgramExercise) -> float | None:
    if item.target_weight_kg is not None:
        return float(item.target_weight_kg)
    return last_top_weight(
        training.logged_sets(db, user, exercise_ids=[item.exercise_id])
    )


def _apply_load(db: Session, user: User, rec: Recommendation) -> None:
    program = _active(db, user)
    exercise_id = str(rec.payload["exercise_id"])
    items = [
        e
        for d in program.days
        for e in d.exercises
        if str(e.exercise_id) == exercise_id
    ]
    if not items:
        raise NoLongerValidError("not_in_program")
    current = _current_target(db, user, items[0])
    if (
        current is not None
        and abs(current - float(rec.payload["from_kg"])) > WEIGHT_TOLERANCE_KG
    ):
        raise NoLongerValidError("load_changed")  # trained since the proposal
    target = Decimal(str(rec.payload["to_kg"])).quantize(Decimal("0.01"))

    def change(item: ProgramExercise) -> None:
        if str(item.exercise_id) == exercise_id:
            item.target_weight_kg = target

    _new_version(db, user, program, rec, change)


def _apply_set_cut(db: Session, user: User, rec: Recommendation) -> None:
    delta = int(rec.payload["sets_delta"])

    def change(item: ProgramExercise) -> None:
        item.sets = max(1, item.sets + delta)

    _new_version(db, user, _active(db, user), rec, change)


def _settings(db: Session, user: User) -> NutritionSettings:
    row = db.scalar(
        sa.select(NutritionSettings).where(NutritionSettings.user_id == user.id)
    )
    if row is None:
        row = NutritionSettings(user_id=user.id, calorie_adjustment_kcal=0)
        db.add(row)
    return row


def _apply_deload(db: Session, user: User, rec: Recommendation) -> None:
    program = _active(db, user)
    weeks = int(rec.payload["weeks"])
    program.deload_until = onboarding.user_today(db, user) + dt.timedelta(
        days=7 * weeks - 1
    )
    program.deload_set_factor = Decimal(str(rec.payload["set_factor"])).quantize(
        Decimal("0.01")
    )


def _apply(db: Session, user: User, rec: Recommendation) -> bool:
    """Apply an approved item. Returns False when there is nothing to apply."""
    match rec.kind:
        case Kind.LOAD_CHANGE:
            _apply_load(db, user, rec)
        case Kind.SET_CUT:
            _apply_set_cut(db, user, rec)
        case Kind.CALORIE_CHANGE:
            settings = _settings(db, user)
            settings.calorie_adjustment_kcal = (
                settings.calorie_adjustment_kcal or 0
            ) + int(rec.payload["change_kcal"])
        case Kind.PROTEIN_CHANGE:
            _settings(db, user).protein_g_per_kg = Decimal(
                str(rec.payload["g_per_kg"])
            ).quantize(Decimal("0.01"))
        case Kind.DELOAD:
            _apply_deload(db, user, rec)
        case _:
            return False  # stall advice: nothing to change
    return True


# --- public ---------------------------------------------------------------------------


def approve(db: Session, user: User, rec_id: uuid.UUID) -> Recommendation:
    rec = _locked(db, user, rec_id)
    if rec.status is RecommendationStatus.APPROVED:
        db.commit()  # release the lock
        return rec
    now = _now()
    _check_open(db, rec, now)
    verdict = validate_recommendation(
        rec.kind, rec.payload, coaching.user_state(db, user)
    )
    if not verdict.allowed:
        db.rollback()
        raise NoLongerValidError(verdict.reason)
    try:
        applied = _apply(db, user, rec)
    except NoLongerValidError:
        db.rollback()
        raise
    rec.status, rec.decided_at = RecommendationStatus.APPROVED, now
    rec.applied_at = now if applied else None
    audit.record(
        db,
        "recommendation.approved",
        user.id,
        entity_type="recommendation",
        entity_id=rec.id,
        meta={"kind": rec.kind, "source": rec.source},
    )
    db.commit()
    return rec


def reject(
    db: Session,
    user: User,
    rec_id: uuid.UUID,
    *,
    reason: str | None = None,
    remember: bool = False,
) -> tuple[Recommendation, bool]:
    """Reject an item; returns it and whether the reason was saved as a coach note."""
    rec = _locked(db, user, rec_id)
    now = _now()
    _check_open(db, rec, now)
    note = (reason or "").strip()[:MAX_NOTE_CHARS] or None
    rec.status, rec.decided_at, rec.decision_note = (
        RecommendationStatus.REJECTED,
        now,
        note,
    )
    saved = False
    if remember and note:
        key = f"declined_{rec.kind}"
        if check_note(key, note) is None:
            existing = db.scalar(
                sa.select(CoachNote).where(
                    CoachNote.user_id == user.id, CoachNote.key == key
                )
            )
            if existing is None:
                db.add(CoachNote(user_id=user.id, key=key, value=note, source="user"))
            else:
                existing.value, existing.source = note, "user"
            saved = True
    audit.record(
        db,
        "recommendation.rejected",
        user.id,
        entity_type="recommendation",
        entity_id=rec.id,
        meta={
            "kind": rec.kind,
            "source": rec.source,
            "with_reason": note is not None,
            "note_saved": saved,
        },
    )
    db.commit()
    return rec, saved
