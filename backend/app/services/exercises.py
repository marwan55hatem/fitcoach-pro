import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Exercise, Limitation, Profile, User
from app.models.enums import BodyArea, Equipment, Experience, Mechanic, Severity
from app.rules.exercise_catalog import (
    CatalogExercise,
    rank_substitutes,
    slugify,
)
from app.rules.limitations import (
    ExerciseFacts,
    LimitationFacts,
    is_excluded,
    limitations_from_areas,
)
from app.schemas.exercises import CustomExerciseIn

SUBSTITUTE_CANDIDATE_CAP = 500


class ExerciseNotFoundError(Exception):
    pass


class ExerciseInUseError(Exception):
    pass


def _visible(user: User) -> sa.ColumnElement[bool]:
    """Shared catalog rows (user_id NULL) plus the user's own custom exercises."""
    return sa.or_(Exercise.user_id.is_(None), Exercise.user_id == user.id)


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_exercises(
    db: Session,
    user: User,
    *,
    q: str | None,
    muscle: str | None,
    equipment: Equipment | None,
    level: Experience | None,
    mechanic: Mechanic | None,
    category: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Exercise], int]:
    conditions = [_visible(user)]
    if q:
        conditions.append(
            Exercise.name.ilike(f"%{_like_escape(q.strip())}%", escape="\\")
        )
    if muscle:
        conditions.append(Exercise.muscle_primary == muscle)
    if equipment:
        conditions.append(Exercise.equipment == equipment.value)
    if level:
        conditions.append(Exercise.level == level)
    if mechanic:
        conditions.append(Exercise.mechanic == mechanic)
    if category:
        conditions.append(Exercise.category == category)

    total = (
        db.scalar(sa.select(sa.func.count()).select_from(Exercise).where(*conditions))
        or 0
    )
    rows = db.scalars(
        sa.select(Exercise)
        .where(*conditions)
        .order_by(sa.func.lower(Exercise.name), Exercise.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(rows), total


def get_visible(db: Session, user: User, exercise_id: uuid.UUID) -> Exercise:
    """Catalog or own exercise; someone else's custom exercise looks like a missing one."""
    row = db.scalar(
        sa.select(Exercise).where(Exercise.id == exercise_id, _visible(user))
    )
    if row is None:
        raise ExerciseNotFoundError
    return row


def _catalog_view(ex: Exercise) -> CatalogExercise:
    return CatalogExercise(
        id=str(ex.id),
        name=ex.name,
        muscle_primary=ex.muscle_primary,
        muscles_secondary=tuple(ex.muscles_secondary or ()),
        equipment=Equipment(ex.equipment or Equipment.BODY_WEIGHT.value),
        level=ex.level,
        mechanic=ex.mechanic,
        category=ex.category,
    )


def _facts(ex: Exercise) -> ExerciseFacts:
    return ExerciseFacts(
        name=ex.name,
        muscle_primary=ex.muscle_primary,
        muscles_secondary=tuple(ex.muscles_secondary or ()),
        mechanic=ex.mechanic,
    )


def user_equipment(db: Session, user: User) -> list[Equipment]:
    profile = db.scalar(sa.select(Profile).where(Profile.user_id == user.id))
    return [Equipment(e) for e in (profile.equipment if profile else [])]


def active_limitations(db: Session, user: User) -> list[LimitationFacts]:
    rows = db.scalars(
        sa.select(Limitation).where(
            Limitation.user_id == user.id, Limitation.active.is_(True)
        )
    )
    result: list[LimitationFacts] = []
    for row in rows:
        try:
            result.append(
                LimitationFacts(BodyArea(row.body_area), Severity(row.severity), True)
            )
        except (
            ValueError
        ):  # an area stored before the vocabulary existed: ignore safely
            continue
    return result


def find_substitutes(
    db: Session,
    user: User,
    target: Exercise,
    *,
    equipment: Sequence[Equipment] | None,
    avoid_areas: Sequence[BodyArea] | None,
    limit: int,
) -> tuple[list[Exercise], list[Equipment], list[BodyArea]]:
    """Same primary muscle, usable with the equipment, not ruled out by any limitation.

    The user's own active limitations ALWAYS apply; `avoid_areas` adds to them, so a
    request can never surface something the user's injuries exclude.
    """
    equipment_used = (
        list(equipment) if equipment is not None else user_equipment(db, user)
    )
    limitations = active_limitations(db, user)
    limitations += limitations_from_areas(avoid_areas or [])
    areas_used = list(dict.fromkeys(lim.body_area for lim in limitations))

    candidates = db.scalars(
        sa.select(Exercise)
        .where(_visible(user), Exercise.muscle_primary == target.muscle_primary)
        .order_by(sa.func.lower(Exercise.name))
        .limit(SUBSTITUTE_CANDIDATE_CAP)
    ).all()
    by_id = {str(c.id): c for c in candidates}
    excluded = frozenset(
        cid for cid, c in by_id.items() if is_excluded(_facts(c), limitations)
    )

    ranked = rank_substitutes(
        _catalog_view(target),
        [_catalog_view(c) for c in candidates],
        user_equipment=equipment_used,
        excluded_ids=excluded,
        limit=limit,
    )
    return [by_id[r.id] for r in ranked], equipment_used, areas_used


def create_custom(db: Session, user: User, data: CustomExerciseIn) -> Exercise:
    base = slugify(data.name) or "exercise"
    taken = set(
        db.scalars(
            sa.select(Exercise.slug).where(
                Exercise.user_id == user.id,
                Exercise.slug.like(f"{_like_escape(base)}%", escape="\\"),
            )
        )
    )
    slug, n = base, 1
    while slug in taken:
        n += 1
        slug = f"{base}-{n}"

    row = Exercise(
        user_id=user.id,
        name=data.name,
        slug=slug,
        muscle_primary=data.muscle_primary.value,
        muscles_secondary=[m.value for m in data.muscles_secondary],
        equipment=data.equipment.value,
        level=data.level,
        mechanic=data.mechanic,
        category="strength",
        instructions=list(data.instructions),
        image_keys=[],
        source="user",
    )
    db.add(row)
    db.commit()
    return row


def delete_custom(db: Session, user: User, exercise_id: uuid.UUID) -> None:
    row = db.scalar(
        sa.select(Exercise).where(
            Exercise.id == exercise_id, Exercise.user_id == user.id
        )
    )
    if row is None:  # catalog rows and other users' rows are not deletable
        raise ExerciseNotFoundError
    try:
        with db.begin_nested():
            db.delete(row)
            db.flush()
    except IntegrityError as exc:  # referenced by a program or a logged set
        raise ExerciseInUseError from exc
    db.commit()
