"""Food logging: the AI estimates a draft, the rules check it, the user confirms.
Only confirmed meals count in the day's totals (app/rules/nutrition.py)."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.ai.client import AiClient
from app.ai.food import AiFoodEstimate, estimate_from_photo, estimate_from_text
from app.models import MealLog, MealOption, User
from app.models.enums import FileKind, MealSlot, MealSource
from app.rules.food_validation import FoodItem, check_item, meal_totals
from app.schemas.food import FoodLogIn
from app.services import files
from app.services.storage import Storage

DRAFT_TTL = dt.timedelta(hours=24)
FLAGGED_CONFIDENCE = 0.5  # an estimate the rules had to flag is never shown as sure


class NoFoodError(Exception):
    pass


class UnclearError(Exception):
    def __init__(self, questions: list[str]) -> None:
        super().__init__("could not tell what the meal is")
        self.questions = questions


class WrongFileKindError(Exception):
    pass


class MealNotFoundError(Exception):
    pass


class NotConfirmedError(Exception):
    pass


def _dec(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(round(value, 1)))


def _float(value: Decimal | None) -> float:
    return 0.0 if value is None else float(value)


# --- drafts ---------------------------------------------------------------------------


def _draft(
    user: User,
    answer: AiFoodEstimate,
    *,
    date: dt.date,
    slot: MealSlot,
    source: MealSource,
    file_id: uuid.UUID | None,
) -> MealLog:
    if not answer.food_found:
        raise NoFoodError
    if not answer.items:
        raise UnclearError(answer.questions[:5])

    checked: list[FoodItem] = []
    flags: list[dict[str, Any]] = []
    stored_items: list[dict[str, Any]] = []
    for index, raw in enumerate(answer.items):
        item, item_flags = check_item(
            FoodItem(
                raw.name,
                raw.grams,
                raw.kcal_min,
                raw.kcal_max,
                raw.protein_g,
                raw.carbs_g,
                raw.fat_g,
            )
        )
        checked.append(item)
        flags += [
            {"item": index, "field": f.field, "code": f.code, "message": f.message}
            for f in item_flags
        ]
        stored = {
            "name": item.name[:200],
            "grams": item.grams,
            "units": raw.units[:60] if raw.units else None,
            "kcal_min": item.kcal_min,
            "kcal_max": item.kcal_max,
            "protein_g": item.protein_g,
            "carbs_g": item.carbs_g,
            "fat_g": item.fat_g,
        }
        # Same shape as a saved meal's items, so an unchanged confirm is not an edit.
        stored_items.append({k: v for k, v in stored.items() if v is not None})
    totals = meal_totals(checked)
    confidence = min(1.0, max(0.0, answer.confidence))
    if flags:
        confidence = min(confidence, FLAGGED_CONFIDENCE)
    return MealLog(
        user_id=user.id,
        date=date,
        slot=slot,
        source=source,
        name=(answer.meal_name or checked[0].name)[:200],
        items=stored_items,
        kcal=totals.kcal,
        kcal_min=totals.kcal_min,
        kcal_max=totals.kcal_max,
        protein_g=_dec(totals.protein_g),
        carbs_g=_dec(totals.carbs_g),
        fat_g=_dec(totals.fat_g),
        confidence=Decimal(str(round(confidence, 2))),
        ai_raw={
            **answer.model_dump(),
            "flags": flags,
            "questions": answer.questions[:5],
        },
        file_id=file_id,
        confirmed=False,
    )


def estimate(
    db: Session,
    storage: Storage,
    ai: AiClient,
    user: User,
    *,
    file_id: uuid.UUID | None,
    text: str | None,
    date: dt.date,
    slot: MealSlot,
) -> MealLog:
    """A DRAFT meal from a photo (with an optional note) or a description."""
    if file_id is not None:
        stored = files.get_owned(db, user.id, file_id)  # StoredFileNotFoundError
        if stored.kind is not FileKind.FOOD:
            raise WrongFileKindError
    user_id = user.id
    purge_old_drafts(db, now=dt.datetime.now(dt.UTC))
    if file_id is not None:
        image = files.load_for_ai(db, storage, user_id, file_id)  # releases the DB
        answer = estimate_from_photo(ai, user_id=user_id, image=image, note=text)
        source = MealSource.PHOTO
    else:
        db.commit()  # release the connection during the AI call
        answer = estimate_from_text(ai, user_id=user_id, text=text or "")
        source = MealSource.TEXT
    meal = _draft(user, answer, date=date, slot=slot, source=source, file_id=file_id)
    db.add(meal)
    db.commit()
    return meal


def purge_old_drafts(db: Session, *, now: dt.datetime) -> int:
    """Estimates nobody confirmed within a day are deleted (the photo stays)."""
    result = db.execute(
        sa.delete(MealLog).where(
            MealLog.confirmed.is_(False), MealLog.created_at < now - DRAFT_TTL
        )
    )
    db.commit()
    return result.rowcount or 0


# --- saving ---------------------------------------------------------------------------


def get_meal(db: Session, user: User, meal_id: uuid.UUID) -> MealLog:
    meal = db.scalar(
        sa.select(MealLog).where(MealLog.id == meal_id, MealLog.user_id == user.id)
    )
    if meal is None:
        raise MealNotFoundError
    return meal


def _values(data: FoodLogIn) -> dict[str, Any]:
    return {
        "date": data.date,
        "slot": data.slot,
        "name": data.name,
        "items": [i.model_dump(exclude_none=True) for i in data.items],
        "kcal": data.kcal,
        "kcal_min": data.kcal_min,
        "kcal_max": data.kcal_max,
        "protein_g": _dec(data.protein_g),
        "carbs_g": _dec(data.carbs_g),
        "fat_g": _dec(data.fat_g),
    }


def save(db: Session, meal: MealLog, data: FoodLogIn) -> MealLog:
    """Store the user's checked version and confirm it. Any change marks it edited."""
    values = _values(data)
    changed = any(getattr(meal, k) != v for k, v in values.items())
    for key, value in values.items():
        setattr(meal, key, value)
    if changed and meal.source is not MealSource.MANUAL:
        meal.user_edited = True
    meal.confirmed = True
    db.commit()
    return meal


def add_manual(db: Session, user: User, data: FoodLogIn) -> MealLog:
    """A meal typed in by the user: their own numbers, so it counts at once."""
    meal = MealLog(
        user_id=user.id, source=MealSource.MANUAL, confirmed=True, **_values(data)
    )
    db.add(meal)
    db.commit()
    return meal


# --- personal foods (meal_options owned by the user) ----------------------------------


def save_personal(
    db: Session, user: User, meal: MealLog, name: str | None
) -> MealOption:
    if not meal.confirmed:
        raise NotConfirmedError
    option = MealOption(
        user_id=user.id,
        name=name or meal.name,
        slot=meal.slot,
        items=list(meal.items or []),
        kcal=meal.kcal or 0,
        protein_g=meal.protein_g or Decimal(0),
        carbs_g=meal.carbs_g or Decimal(0),
        fat_g=meal.fat_g or Decimal(0),
        tags=["personal"],
    )
    db.add(option)
    db.commit()
    return option


def personal_foods(db: Session, user: User) -> list[MealOption]:
    return list(
        db.scalars(
            sa.select(MealOption)
            .where(MealOption.user_id == user.id)
            .order_by(sa.func.lower(MealOption.name), MealOption.created_at)
        )
    )


def personal_out(option: MealOption) -> dict[str, Any]:
    return {
        "id": option.id,
        "name": option.name,
        "slot": option.slot,
        "items": list(option.items or []),
        "kcal": option.kcal,
        "protein_g": _float(option.protein_g),
        "carbs_g": _float(option.carbs_g),
        "fat_g": _float(option.fat_g),
        "created_at": option.created_at,
    }
