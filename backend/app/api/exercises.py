import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, DbSession
from app.models import Exercise
from app.models.enums import BodyArea, Equipment, Experience, Mechanic
from app.rules.exercise_catalog import required_equipment
from app.schemas.exercises import (
    CustomExerciseIn,
    ExerciseDetail,
    ExerciseList,
    ExerciseSummary,
    Muscle,
    SubstitutesOut,
)
from app.services import exercises as svc
from app.services.storage import Storage, get_storage

router = APIRouter(prefix="/exercises", tags=["exercises"])

StorageDep = Annotated[Storage, Depends(get_storage)]
URL_TTL_S = 300  # signed image links live five minutes


def _urls(ex: Exercise, storage: Storage) -> list[str]:
    return [storage.presign_get(key, URL_TTL_S) for key in (ex.image_keys or [])]


def _summary(ex: Exercise, storage: Storage) -> ExerciseSummary:
    first = ex.image_keys[0] if ex.image_keys else None
    return ExerciseSummary(
        id=ex.id,
        name=ex.name,
        slug=ex.slug,
        muscle_primary=ex.muscle_primary,
        muscles_secondary=list(ex.muscles_secondary or []),
        equipment=ex.equipment,
        level=ex.level,
        mechanic=ex.mechanic,
        category=ex.category,
        is_custom=ex.user_id is not None,
        image_url=storage.presign_get(first, URL_TTL_S) if first else None,
    )


def _detail(ex: Exercise, storage: Storage) -> ExerciseDetail:
    equipment = Equipment(ex.equipment or Equipment.BODY_WEIGHT.value)
    return ExerciseDetail(
        **_summary(ex, storage).model_dump(),
        instructions=list(ex.instructions or []),
        image_urls=_urls(ex, storage),
        required_equipment=sorted(
            e.value for e in required_equipment(ex.name, equipment)
        ),
        source=ex.source,
        license=ex.license,
        attribution=ex.attribution,
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="Exercise not found"
    )


@router.get("", response_model=ExerciseList)
def list_exercises(
    db: DbSession,
    user: CurrentUser,
    storage: StorageDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    muscle: Muscle | None = None,
    equipment: Equipment | None = None,
    level: Experience | None = None,
    mechanic: Mechanic | None = None,
    category: Annotated[str | None, Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ExerciseList:
    rows, total = svc.list_exercises(
        db,
        user,
        q=q,
        muscle=muscle.value if muscle else None,
        equipment=equipment,
        level=level,
        mechanic=mechanic,
        category=category,
        limit=limit,
        offset=offset,
    )
    return ExerciseList(
        items=[_summary(r, storage) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ExerciseDetail, status_code=status.HTTP_201_CREATED)
def create_custom_exercise(
    body: CustomExerciseIn, db: DbSession, user: CurrentUser, storage: StorageDep
) -> ExerciseDetail:
    """A custom exercise only its owner can see (and use in their programs)."""
    return _detail(svc.create_custom(db, user, body), storage)


@router.get("/{exercise_id}", response_model=ExerciseDetail)
def get_exercise(
    exercise_id: uuid.UUID, db: DbSession, user: CurrentUser, storage: StorageDep
) -> ExerciseDetail:
    try:
        return _detail(svc.get_visible(db, user, exercise_id), storage)
    except svc.ExerciseNotFoundError:
        raise _not_found() from None


@router.get("/{exercise_id}/substitutes", response_model=SubstitutesOut)
def get_substitutes(
    exercise_id: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    storage: StorageDep,
    equipment: Annotated[list[Equipment] | None, Query()] = None,
    avoid_areas: Annotated[list[BodyArea] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=30)] = 10,
) -> SubstitutesOut:
    """Alternatives for the same muscle.

    `equipment` defaults to your profile's equipment. Your own active injuries always apply;
    `avoid_areas` adds extra body areas to avoid for this request.
    """
    try:
        target = svc.get_visible(db, user, exercise_id)
    except svc.ExerciseNotFoundError:
        raise _not_found() from None
    rows, equipment_used, areas_used = svc.find_substitutes(
        db, user, target, equipment=equipment, avoid_areas=avoid_areas, limit=limit
    )
    return SubstitutesOut(
        exercise_id=target.id,
        equipment_used=[e.value for e in equipment_used],
        avoid_areas_used=[a.value for a in areas_used],
        substitutes=[_summary(r, storage) for r in rows],
    )


@router.delete("/{exercise_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_custom_exercise(
    exercise_id: uuid.UUID, db: DbSession, user: CurrentUser
) -> Response:
    """Delete one of your custom exercises (catalog exercises cannot be deleted)."""
    try:
        svc.delete_custom(db, user, exercise_id)
    except svc.ExerciseNotFoundError:
        raise _not_found() from None
    except svc.ExerciseInUseError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This exercise is used in a program or workout log and cannot be deleted.",
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
