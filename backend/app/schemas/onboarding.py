import datetime as dt
import uuid
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.models.enums import (
    ActivityLevel,
    BodyArea,
    BudgetLevel,
    Equipment,
    Experience,
    Goal,
    RiskLevel,
    Severity,
    Sex,
)

ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)
]


def _dedupe(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value.lower(), None)
    return list(seen)


class ProfileIn(BaseModel):
    birth_date: dt.date
    sex: Sex
    height_cm: float = Field(ge=100, le=250)
    weight_kg: float = Field(ge=30, le=300)
    goal: Goal
    experience: Experience
    days_per_week: int = Field(ge=1, le=7)
    session_minutes: int = Field(ge=20, le=180)
    equipment: list[Equipment] = Field(min_length=1, max_length=len(Equipment))
    activity_level: ActivityLevel
    timezone: str = "Africa/Cairo"
    language: Literal["en", "ar"] = "en"

    @field_validator("birth_date")
    @classmethod
    def _plausible_birth_date(cls, value: dt.date) -> dt.date:
        if value.year < 1900 or value > dt.date.today():
            raise ValueError("birth_date is not plausible")
        return value

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise ValueError("unknown timezone") from exc
        return value

    @field_validator("equipment")
    @classmethod
    def _unique_equipment(cls, value: list[Equipment]) -> list[Equipment]:
        return list(dict.fromkeys(value))


class ProfileOut(BaseModel):
    birth_date: dt.date | None
    sex: Sex | None
    height_cm: float | None
    weight_kg: float | None
    goal: Goal | None
    experience: Experience | None
    days_per_week: int | None
    session_minutes: int | None
    equipment: list[str]
    activity_level: ActivityLevel | None
    timezone: str
    language: str


class LimitationIn(BaseModel):
    body_area: BodyArea
    description: str | None = Field(default=None, max_length=500)
    severity: Severity
    active: bool = True


class LimitationsIn(BaseModel):
    """Replace-all: the submitted list becomes the user's complete list (may be empty)."""

    limitations: list[LimitationIn] = Field(max_length=20)


class LimitationOut(LimitationIn):
    id: uuid.UUID


class LimitationsOut(BaseModel):
    reviewed: bool
    limitations: list[LimitationOut]


class DietIn(BaseModel):
    allergies: list[ShortText] = Field(default_factory=list, max_length=30)
    intolerances: list[ShortText] = Field(default_factory=list, max_length=30)
    dislikes: list[ShortText] = Field(default_factory=list, max_length=30)
    cuisine_hints: list[ShortText] = Field(default_factory=list, max_length=30)
    meals_per_day: int = Field(ge=1, le=8)
    budget_level: BudgetLevel | None = None

    @field_validator("allergies", "intolerances", "dislikes", "cuisine_hints")
    @classmethod
    def _dedupe_lists(cls, value: list[str]) -> list[str]:
        return _dedupe(value)


class DietOut(DietIn):
    pass


class ScreeningIn(BaseModel):
    """PAR-Q style answers. Every question must be answered explicitly."""

    chest_pain: bool
    heart_condition: bool
    blood_pressure_uncontrolled: bool
    fainting_or_dizziness: bool
    pregnant: bool
    recent_surgery: bool
    medication_affecting_exercise: bool
    joint_or_bone_problem: bool
    eating_concern: bool
    free_text: str | None = Field(default=None, max_length=1000)


class Reason(BaseModel):
    code: str
    message: str


class ScreeningOut(BaseModel):
    id: uuid.UUID
    screened_at: dt.datetime
    risk_level: RiskLevel
    reasons: list[Reason]
    notes: list[Reason]
    disordered_eating_flag: bool
    clearance_required: bool
    clearance_confirmed: bool


class ClearanceIn(BaseModel):
    """The user states they have medical clearance. Must be an explicit true."""

    confirm: Literal[True]


class StepStatus(BaseModel):
    key: str
    completed: bool
    required: bool


class PlanAccessOut(BaseModel):
    allowed: bool
    numeric_diet_targets: bool
    blocked_reasons: list[Reason]


class OnboardingStatus(BaseModel):
    steps: list[StepStatus]
    missing: list[str]
    onboarding_complete: bool
    risk_level: RiskLevel | None
    clearance_required: bool
    clearance_confirmed: bool
    disordered_eating_flag: bool
    plan_access: PlanAccessOut
