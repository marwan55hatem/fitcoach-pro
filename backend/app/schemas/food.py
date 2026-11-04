import datetime as dt
import uuid
from typing import Annotated, Self

from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.models.enums import MealSlot
from app.schemas.nutrition import MealOut, Name

Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]


class EstimateIn(BaseModel):
    """A photo (an uploaded `food` file), a description, or both (the text is a note)."""

    file_id: uuid.UUID | None = None
    text: Description | None = None
    date: dt.date | None = None  # defaults to today in the user's timezone
    slot: MealSlot

    @model_validator(mode="after")
    def _photo_or_text(self) -> Self:
        if self.file_id is None and self.text is None:
            raise ValueError("send a photo (file_id) or a description (text)")
        return self


class FoodItem(BaseModel):
    name: Name
    grams: float | None = Field(default=None, ge=0, le=5000)
    units: Annotated[str, StringConstraints(max_length=60)] | None = None
    kcal_min: int | None = Field(default=None, ge=0, le=6000)
    kcal_max: int | None = Field(default=None, ge=0, le=6000)
    protein_g: float | None = Field(default=None, ge=0, le=500)
    carbs_g: float | None = Field(default=None, ge=0, le=1000)
    fat_g: float | None = Field(default=None, ge=0, le=500)


class FoodLogIn(BaseModel):
    """A meal as the user checked it. Send kcal_min and kcal_max together to keep an
    estimate's range; leave both out for an exact number."""

    date: dt.date
    slot: MealSlot
    name: Name
    items: list[FoodItem] = Field(default_factory=list, max_length=50)
    kcal: int = Field(ge=0, le=6000)
    kcal_min: int | None = Field(default=None, ge=0, le=6000)
    kcal_max: int | None = Field(default=None, ge=0, le=6000)
    protein_g: float = Field(ge=0, le=500)
    carbs_g: float = Field(ge=0, le=1000)
    fat_g: float = Field(ge=0, le=500)

    @model_validator(mode="after")
    def _range(self) -> Self:
        if (self.kcal_min is None) != (self.kcal_max is None):
            raise ValueError("send kcal_min and kcal_max together")
        if (
            self.kcal_min is not None
            and self.kcal_max is not None
            and not self.kcal_min <= self.kcal <= self.kcal_max
        ):
            raise ValueError("kcal must be within kcal_min and kcal_max")
        return self


class FoodFlagOut(BaseModel):
    item: int | None  # index in items, or null for the whole meal
    field: str
    code: str  # negative | range_order | too_large | macro_mismatch
    message: str


class FoodDraftOut(MealOut):
    questions: list[str]  # what would make the estimate better
    flags: list[FoodFlagOut]  # numbers to check before confirming


class PersonalFoodIn(BaseModel):
    meal_log_id: uuid.UUID  # a confirmed meal to keep as one of your foods
    name: Name | None = None  # defaults to the meal's name


class PersonalFoodOut(BaseModel):
    id: uuid.UUID
    name: str
    slot: MealSlot | None
    items: list[dict[str, object]]
    kcal: int
    protein_g: float
    carbs_g: float
    fat_g: float
    created_at: dt.datetime
