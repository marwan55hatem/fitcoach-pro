import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.ai.client import AiClient, get_ai_client
from app.api.deps import (
    CurrentUser,
    DbSession,
    consent_required_error,
    get_owned_or_404,
    require_plan_access,
)
from app.core.rate_limit import ai_limit
from app.models import Program, User
from app.models.enums import ConsentType
from app.rules.program_validator import (
    Violation,
    errors_in,
    estimated_minutes,
    validate_program,
    warnings_in,
)
from app.schemas.programs import (
    GeneratedProgramOut,
    GenerateProgramIn,
    ProgramDayOut,
    ProgramExerciseOut,
    ProgramOut,
    ViolationOut,
)
from app.services import programs as svc
from app.services.consents import has_consent
from app.services.storage import Storage, get_storage

router = APIRouter(prefix="/programs", tags=["programs"])

PlanUser = Annotated[User, Depends(require_plan_access)]
StorageDep = Annotated[Storage, Depends(get_storage)]
AiDep = Annotated[AiClient, Depends(get_ai_client)]
URL_TTL_S = 300


def _violations(items: list[Violation]) -> list[ViolationOut]:
    return [
        ViolationOut(
            code=v.code, message=v.message, day_index=v.day_index, muscle=v.muscle
        )
        for v in items
    ]


def _program_out(
    db: DbSession, user: User, program: Program, storage: Storage
) -> ProgramOut:
    draft = svc.draft_of(program)
    violations = validate_program(draft, svc.build_context(db, user))
    days = []
    for day, day_draft in zip(program.days, draft.days, strict=True):
        exercises = []
        for item in day.exercises:
            keys = item.exercise.image_keys or []
            exercises.append(
                ProgramExerciseOut(
                    id=item.id,
                    exercise_id=item.exercise_id,
                    name=item.exercise.name,
                    muscle_primary=item.exercise.muscle_primary,
                    equipment=item.exercise.equipment,
                    order=item.order,
                    sets=item.sets,
                    rep_min=item.rep_min,
                    rep_max=item.rep_max,
                    rest_s=item.rest_s,
                    notes=item.notes,
                    image_url=storage.presign_get(keys[0], URL_TTL_S) if keys else None,
                )
            )
        days.append(
            ProgramDayOut(
                id=day.id,
                day_index=day.day_index,
                name=day.name,
                focus=day.focus,
                rationale=day.rationale,
                estimated_minutes=round(estimated_minutes(day_draft)),
                exercises=exercises,
            )
        )
    return ProgramOut(
        id=program.id,
        version=program.version,
        status=program.status,
        generated_by=program.generated_by,
        rationale=program.rationale,
        created_at=program.created_at,
        days=days,
        warnings=_violations(warnings_in(violations)),
        errors=_violations(errors_in(violations)),
    )


@router.post(
    "/generate",
    response_model=GeneratedProgramOut,
    status_code=status.HTTP_201_CREATED,
)
@ai_limit
def generate(
    request: Request,
    body: GenerateProgramIn,
    db: DbSession,
    user: PlanUser,
    ai: AiDep,
    storage: StorageDep,
) -> GeneratedProgramOut:
    """Create a draft program. It changes nothing until the user accepts it.

    With `use_ai`, training facts (no name, email or injury notes) go to the AI provider,
    which needs the ai_processing consent. `use_ai: false` uses only the built-in rules.
    """
    if body.use_ai and not has_consent(db, user.id, ConsentType.AI_PROCESSING):
        raise consent_required_error(ConsentType.AI_PROCESSING)
    try:
        program, proposal = svc.generate_program(
            db, ai, user, use_ai=body.use_ai, template_key=body.template
        )
    except svc.NoValidProgramError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "no_valid_program",
                "message": (
                    "No safe program can be built from your equipment and limitations. "
                    "Try adding equipment or changing your schedule."
                ),
            },
        ) from None
    except svc.ProgramConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A program is already being generated. Please try again.",
        ) from None
    return GeneratedProgramOut(
        **_program_out(db, user, program, storage).model_dump(),
        source=proposal.source,
        fallback_reason=proposal.fallback_reason,
    )


@router.get("/current", response_model=ProgramOut)
def get_current(db: DbSession, user: CurrentUser, storage: StorageDep) -> ProgramOut:
    program = svc.current_program(db, user)
    if program is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No active program yet."
        )
    return _program_out(db, user, program, storage)


@router.get("/{program_id}/preview", response_model=ProgramOut)
def preview(
    program_id: uuid.UUID, db: DbSession, user: CurrentUser, storage: StorageDep
) -> ProgramOut:
    program = get_owned_or_404(db, Program, program_id, user)
    return _program_out(db, user, program, storage)


@router.post("/{program_id}/accept", response_model=ProgramOut)
def accept(
    program_id: uuid.UUID, db: DbSession, user: PlanUser, storage: StorageDep
) -> ProgramOut:
    """Make a draft the active program. The previous active program is archived."""
    program = get_owned_or_404(db, Program, program_id, user)
    try:
        svc.accept_program(db, user, program)
    except svc.ProgramNotDraftError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a draft program can be accepted.",
        ) from None
    except svc.ProgramNoLongerValidError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "program_no_longer_valid",
                "message": (
                    "This program no longer fits your profile or limitations. "
                    "Please generate a new one."
                ),
                "violations": [v.model_dump() for v in _violations(exc.violations)],
            },
        ) from None
    except svc.ProgramConflictError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Another change is in progress. Please try again.",
        ) from None
    return _program_out(db, user, program, storage)
