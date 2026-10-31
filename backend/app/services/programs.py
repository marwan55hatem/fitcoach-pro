"""Generate, store and accept training programs.

A generated program is only a draft. Nothing becomes the user's active program until they
accept it, and it is re-validated at that moment because their injuries, equipment or
schedule may have changed since it was generated.
"""

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.client import AiClient
from app.ai.program_ai import (
    ProgramProposal,
    TrainingProfile,
    propose_program,
    template_proposal,
)
from app.models import Program, ProgramDay, ProgramExercise, User
from app.models.enums import Experience, ProgramStatus
from app.rules import body_metrics
from app.rules.program_validator import (
    DayDraft,
    ExerciseDraft,
    ProgramContext,
    ProgramDraft,
    Violation,
    errors_in,
    validate_program,
)
from app.rules.templates import TEMPLATES, SplitTemplate, choose_template
from app.services import audit, onboarding
from app.services.exercises import (
    active_limitations,
    strength_catalog,
    user_equipment,
)


class NoValidProgramError(Exception):
    pass


class ProgramConflictError(Exception):
    pass


class ProgramNotDraftError(Exception):
    pass


class ProgramNoLongerValidError(Exception):
    def __init__(self, violations: list[Violation]) -> None:
        super().__init__("program failed re-validation")
        self.violations = violations


def build_context(db: Session, user: User) -> ProgramContext:
    profile = onboarding.get_profile(db, user.id)
    return ProgramContext(
        catalog=strength_catalog(db, user),
        equipment=tuple(user_equipment(db, user)),
        limitations=tuple(active_limitations(db, user)),
        experience=(profile.experience if profile else None) or Experience.BEGINNER,
        session_minutes=(profile.session_minutes if profile else None) or 60,
        days_per_week=(profile.days_per_week if profile else None) or 3,
    )


def _training_profile(db: Session, user: User) -> TrainingProfile:
    profile = onboarding.get_profile(db, user.id)
    weight = onboarding.latest_weight(db, user.id)
    assert profile is not None and weight is not None  # guaranteed by plan access
    assert profile.birth_date is not None
    today = dt.datetime.now(ZoneInfo(profile.timezone)).date()
    return TrainingProfile(
        age=body_metrics.age_years(profile.birth_date, today),
        sex=str(profile.sex),
        height_cm=float(profile.height_cm or 0),
        weight_kg=float(weight.weight_kg),
        goal=str(profile.goal),
        experience=str(profile.experience),
        days_per_week=profile.days_per_week or 3,
        session_minutes=profile.session_minutes or 60,
    )


def _save_draft(db: Session, user_id: uuid.UUID, proposal: ProgramProposal) -> Program:
    """Store the proposal as the user's only draft, replacing any unaccepted one."""
    db.execute(
        sa.delete(Program).where(
            Program.user_id == user_id, Program.status == ProgramStatus.DRAFT
        )
    )
    latest = db.scalar(
        sa.select(sa.func.max(Program.version)).where(Program.user_id == user_id)
    )
    program = Program(
        user_id=user_id,
        version=(latest or 0) + 1,
        status=ProgramStatus.DRAFT,
        generated_by=proposal.source,
        rationale=proposal.draft.rationale,
    )
    for day_index, day in enumerate(proposal.draft.days, start=1):
        row = ProgramDay(
            user_id=user_id,
            day_index=day_index,
            name=day.name[:100],
            focus=day.focus[:200],
            rationale=day.rationale,
        )
        for order, item in enumerate(day.exercises, start=1):
            row.exercises.append(
                ProgramExercise(
                    user_id=user_id,
                    exercise_id=uuid.UUID(item.exercise_id),
                    order=order,
                    sets=item.sets,
                    rep_min=item.rep_min,
                    rep_max=item.rep_max,
                    rest_s=item.rest_s,
                    notes=item.notes,
                )
            )
        program.days.append(row)
    try:
        with db.begin_nested():
            db.add(program)
            db.flush()
    except IntegrityError as exc:  # two generations raced for the same version
        raise ProgramConflictError from exc
    audit.record(
        db,
        "program.generated",
        user_id,
        entity_type="program",
        entity_id=program.id,
        meta={"source": proposal.source},
    )
    db.commit()
    return program


def generate_program(
    db: Session,
    ai: AiClient,
    user: User,
    *,
    use_ai: bool,
    template_key: str | None,
) -> tuple[Program, ProgramProposal]:
    user_id = user.id
    ctx = build_context(db, user)
    profile_days = ctx.days_per_week
    template: SplitTemplate = (
        TEMPLATES[template_key] if template_key else choose_template(profile_days)
    )
    if use_ai:
        training_profile = _training_profile(db, user)
        db.commit()  # release the DB connection: the AI call can take many seconds
        proposal = propose_program(
            ai,
            user_id=user_id,
            profile=training_profile,
            template=template,
            ctx=ctx,
        )
    else:
        proposal = template_proposal(template, ctx, None)

    if not proposal.draft.days or errors_in(validate_program(proposal.draft, ctx)):
        raise NoValidProgramError
    return _save_draft(db, user_id, proposal), proposal


def current_program(db: Session, user: User) -> Program | None:
    return db.scalar(
        sa.select(Program).where(
            Program.user_id == user.id, Program.status == ProgramStatus.ACTIVE
        )
    )


def draft_of(program: Program) -> ProgramDraft:
    return ProgramDraft(
        program.rationale or "",
        tuple(
            DayDraft(
                day.name,
                day.focus or "",
                day.rationale or "",
                tuple(
                    ExerciseDraft(
                        str(item.exercise_id),
                        item.sets,
                        item.rep_min,
                        item.rep_max,
                        item.rest_s or 0,
                        item.notes,
                    )
                    for item in day.exercises
                ),
            )
            for day in program.days
        ),
    )


def accept_program(db: Session, user: User, program: Program) -> Program:
    """Activate a draft in one transaction, archiving the previous active program."""
    if program.status is not ProgramStatus.DRAFT:
        raise ProgramNotDraftError
    errors = errors_in(validate_program(draft_of(program), build_context(db, user)))
    if errors:
        raise ProgramNoLongerValidError(errors)
    try:
        with db.begin_nested():
            # Archive first: a partial unique index allows only one active program.
            db.execute(
                sa.update(Program)
                .where(
                    Program.user_id == user.id, Program.status == ProgramStatus.ACTIVE
                )
                .values(status=ProgramStatus.ARCHIVED)
            )
            program.status = ProgramStatus.ACTIVE
            db.flush()
    except IntegrityError as exc:
        db.refresh(program)
        raise ProgramConflictError from exc
    audit.record(
        db,
        "program.accepted",
        user.id,
        entity_type="program",
        entity_id=program.id,
    )
    db.commit()
    return program
