"""Meals, water and daily targets. The numbers come from app/rules/nutrition.py."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models import InbodyScan, MealLog, NutritionSettings, User, WaterLog
from app.models.enums import MealSlot, MealSource
from app.rules import body_metrics
from app.rules.nutrition import (
    DayTotals,
    MealEntry,
    NutritionInput,
    NutritionTargets,
    daily_targets,
    day_totals,
)
from app.schemas.nutrition import MealIn, MealPatchIn
from app.services import onboarding

SLOT_ORDER = [
    MealSlot.BREAKFAST,
    MealSlot.SNACK,
    MealSlot.LUNCH,
    MealSlot.PRE_WORKOUT,
    MealSlot.POST_WORKOUT,
    MealSlot.DINNER,
    MealSlot.BED,
]


class ProfileIncompleteError(Exception):
    pass


def _dec(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(round(value, 1)))


def get_settings(db: Session, user: User) -> NutritionSettings | None:
    return db.scalar(
        sa.select(NutritionSettings).where(NutritionSettings.user_id == user.id)
    )


def confirmed_inbody_bmr(db: Session, user: User) -> int | None:
    """BMR from the latest confirmed InBody scan; drafts are never used."""
    return db.scalar(
        sa.select(InbodyScan.bmr_kcal)
        .where(
            InbodyScan.user_id == user.id,
            InbodyScan.confirmed.is_(True),
            InbodyScan.bmr_kcal.is_not(None),
        )
        .order_by(InbodyScan.taken_on.desc(), InbodyScan.created_at.desc())
        .limit(1)
    )


def targets_for(db: Session, user: User) -> NutritionTargets:
    profile = onboarding.get_profile(db, user.id)
    weight = onboarding.latest_weight(db, user.id)
    if (
        profile is None
        or weight is None
        or profile.birth_date is None
        or profile.sex is None
        or profile.height_cm is None
        or profile.activity_level is None
        or profile.goal is None
    ):
        raise ProfileIncompleteError
    settings = get_settings(db, user)
    today = onboarding.user_today(db, user)
    return daily_targets(
        NutritionInput(
            weight_kg=float(weight.weight_kg),
            height_cm=float(profile.height_cm),
            age=body_metrics.age_years(profile.birth_date, today),
            sex=profile.sex,
            activity=profile.activity_level,
            goal=profile.goal,
            inbody_bmr_kcal=confirmed_inbody_bmr(db, user),
            calorie_adjustment_kcal=settings.calorie_adjustment_kcal if settings else 0,
            protein_g_per_kg=float(settings.protein_g_per_kg)
            if settings and settings.protein_g_per_kg is not None
            else None,
        )
    )


def meals_on(db: Session, user: User, date: dt.date) -> list[MealLog]:
    rows = list(
        db.scalars(
            sa.select(MealLog)
            .where(MealLog.user_id == user.id, MealLog.date == date)
            .order_by(MealLog.created_at, MealLog.id)
        )
    )
    return sorted(rows, key=lambda m: SLOT_ORDER.index(m.slot))


def totals_of(meals: list[MealLog]) -> DayTotals:
    return day_totals(
        MealEntry(
            kcal=m.kcal,
            protein_g=float(m.protein_g) if m.protein_g is not None else None,
            carbs_g=float(m.carbs_g) if m.carbs_g is not None else None,
            fat_g=float(m.fat_g) if m.fat_g is not None else None,
            confirmed=m.confirmed,
        )
        for m in meals
    )


def water_on(db: Session, user: User, date: dt.date) -> int:
    total = db.scalar(
        sa.select(sa.func.coalesce(sa.func.sum(WaterLog.ml), 0)).where(
            WaterLog.user_id == user.id, WaterLog.date == date
        )
    )
    return int(total or 0)


def add_meal(db: Session, user: User, data: MealIn) -> MealLog:
    """A meal the user typed in. It is the user's own data, so it counts immediately."""
    row = MealLog(
        user_id=user.id,
        date=data.date,
        slot=data.slot,
        source=MealSource.MANUAL,
        name=data.name,
        items=[item.model_dump(exclude_none=True) for item in data.items],
        kcal=data.kcal,
        protein_g=_dec(data.protein_g),
        carbs_g=_dec(data.carbs_g),
        fat_g=_dec(data.fat_g),
        confirmed=True,
    )
    db.add(row)
    db.commit()
    return row


def update_meal(db: Session, meal: MealLog, patch: MealPatchIn) -> MealLog:
    """Edit a meal. Editing an AI draft marks it user-edited but does not confirm it."""
    changes: dict[str, Any] = patch.model_dump(exclude_unset=True, exclude_none=True)
    for field in ("protein_g", "carbs_g", "fat_g"):
        if field in changes:
            setattr(meal, field, _dec(changes.pop(field)))
    if "items" in changes:
        meal.items = [item.model_dump(exclude_none=True) for item in patch.items or []]
        changes.pop("items")
    for field, value in changes.items():
        setattr(meal, field, value)
    if meal.source is not MealSource.MANUAL:
        meal.user_edited = True
    db.commit()
    return meal


def delete_meal(db: Session, meal: MealLog) -> None:
    db.delete(meal)
    db.commit()


def set_water(db: Session, user: User, date: dt.date, ml: int) -> int:
    """Replace the day's water with one total."""
    db.execute(
        sa.delete(WaterLog).where(WaterLog.user_id == user.id, WaterLog.date == date)
    )
    if ml > 0:
        db.add(WaterLog(user_id=user.id, date=date, ml=ml))
    db.commit()
    return ml


def get_meal(db: Session, user: User, meal_id: uuid.UUID) -> MealLog | None:
    return db.scalar(
        sa.select(MealLog).where(MealLog.id == meal_id, MealLog.user_id == user.id)
    )
