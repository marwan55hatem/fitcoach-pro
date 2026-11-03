import datetime as dt
import uuid
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.models.enums import MealSlot, MealSource

Name = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class MealItemIn(BaseModel):
    name: Name
    grams: float | None = Field(default=None, gt=0, le=5000)


class MealIn(BaseModel):
    date: dt.date
    slot: MealSlot
    name: Name
    items: list[MealItemIn] = Field(default_factory=list, max_length=50)
    kcal: int = Field(ge=0, le=6000)
    protein_g: float = Field(ge=0, le=500)
    carbs_g: float = Field(ge=0, le=1000)
    fat_g: float = Field(ge=0, le=500)


class MealPatchIn(BaseModel):
    """Only the fields sent are changed."""

    slot: MealSlot | None = None
    name: Name | None = None
    items: list[MealItemIn] | None = Field(default=None, max_length=50)
    kcal: int | None = Field(default=None, ge=0, le=6000)
    protein_g: float | None = Field(default=None, ge=0, le=500)
    carbs_g: float | None = Field(default=None, ge=0, le=1000)
    fat_g: float | None = Field(default=None, ge=0, le=500)


class MealOut(BaseModel):
    id: uuid.UUID
    date: dt.date
    slot: MealSlot
    source: MealSource
    name: str
    items: list[dict[str, object]]
    kcal: int | None
    protein_g: float | None
    carbs_g: float | None
    fat_g: float | None
    confirmed: bool  # drafts (AI-read meals) do not count until confirmed
    user_edited: bool


class WaterIn(BaseModel):
    ml: int = Field(ge=0, le=15000)  # the day's total


class TargetsOut(BaseModel):
    kcal: int
    protein_g: int
    fat_g: int
    carbs_g: int
    water_ml: int
    bmr_kcal: int
    bmr_source: str  # "inbody" (latest confirmed scan) or "formula"
    tdee_kcal: int
    goal_adjustment_kcal: int
    calorie_adjustment_kcal: int
    floored: bool  # raised to the safety minimum


class TotalsOut(BaseModel):
    kcal: int
    protein_g: float
    carbs_g: float
    fat_g: float
    meals_counted: int
    drafts_excluded: int


class RemainingOut(BaseModel):
    kcal: int
    protein_g: float
    carbs_g: float
    fat_g: float
    water_ml: int


class DayOut(BaseModel):
    date: dt.date
    meals: list[MealOut]
    water_ml: int
    totals: TotalsOut
    # Null when numeric targets are withheld for this profile.
    targets: TargetsOut | None
    remaining: RemainingOut | None
    targets_withheld: bool
