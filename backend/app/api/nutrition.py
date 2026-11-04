import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import DbSession, DietUser, PlanUser, not_in_future
from app.models import MealLog
from app.rules.nutrition import NutritionTargets, remaining
from app.schemas.nutrition import (
    DayOut,
    MealIn,
    MealOut,
    MealPatchIn,
    RemainingOut,
    TargetsOut,
    TotalsOut,
    WaterIn,
)
from app.services import nutrition as svc
from app.services import onboarding

router = APIRouter(prefix="/nutrition", tags=["nutrition"])


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def meal_out(meal: MealLog) -> MealOut:
    return MealOut(
        id=meal.id,
        date=meal.date,
        slot=meal.slot,
        source=meal.source,
        name=meal.name,
        items=list(meal.items or []),
        kcal=meal.kcal,
        kcal_min=meal.kcal_min,
        kcal_max=meal.kcal_max,
        protein_g=_float(meal.protein_g),
        carbs_g=_float(meal.carbs_g),
        fat_g=_float(meal.fat_g),
        confidence=_float(meal.confidence),
        file_id=meal.file_id,
        confirmed=meal.confirmed,
        user_edited=meal.user_edited,
    )


def _targets_out(t: NutritionTargets) -> TargetsOut:
    return TargetsOut(**t.__dict__)


def _owned_meal(db: DbSession, user: PlanUser, meal_id: uuid.UUID) -> MealLog:
    meal = svc.get_meal(db, user, meal_id)
    if meal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return meal


@router.get("/targets", response_model=TargetsOut)
def targets(db: DbSession, user: DietUser) -> TargetsOut:
    """Today's calorie, macro and water targets, computed from the current profile."""
    return _targets_out(svc.targets_for(db, user))


@router.get("/day", response_model=DayOut)
def day(
    db: DbSession,
    user: PlanUser,
    date: Annotated[dt.date | None, Query()] = None,
) -> DayOut:
    """A day's meals and water, totals of confirmed meals, and what is left.

    Targets are null when numeric diet targets are withheld for this profile.
    """
    date = date or onboarding.user_today(db, user)
    meals = svc.meals_on(db, user, date)
    totals = svc.totals_of(meals)
    water = svc.water_on(db, user, date)
    allowed = onboarding.onboarding_state(db, user).access.numeric_diet_targets
    t = svc.targets_for(db, user) if allowed else None
    left = remaining(t, totals) if t else None
    return DayOut(
        date=date,
        meals=[meal_out(m) for m in meals],
        water_ml=water,
        totals=TotalsOut(**totals.__dict__),
        targets=_targets_out(t) if t else None,
        remaining=RemainingOut(**left.__dict__, water_ml=t.water_ml - water)
        if t and left
        else None,
        targets_withheld=not allowed,
    )


@router.post("/meals", response_model=MealOut, status_code=status.HTTP_201_CREATED)
def add_meal(body: MealIn, db: DbSession, user: PlanUser) -> MealOut:
    not_in_future(body.date, db, user)
    return meal_out(svc.add_meal(db, user, body))


@router.patch("/meals/{meal_id}", response_model=MealOut)
def edit_meal(
    meal_id: uuid.UUID, body: MealPatchIn, db: DbSession, user: PlanUser
) -> MealOut:
    return meal_out(svc.update_meal(db, _owned_meal(db, user, meal_id), body))


@router.delete("/meals/{meal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meal(meal_id: uuid.UUID, db: DbSession, user: PlanUser) -> Response:
    svc.delete_meal(db, _owned_meal(db, user, meal_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/days/{date}/water", response_model=WaterIn)
def put_water(date: dt.date, body: WaterIn, db: DbSession, user: PlanUser) -> WaterIn:
    """Set the day's total water (replaces what was logged)."""
    not_in_future(date, db, user)
    return WaterIn(ml=svc.set_water(db, user, date, body.ml))
