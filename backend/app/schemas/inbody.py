import datetime as dt
import uuid
from typing import Annotated, Any

from pydantic import BaseModel, Field

from app.rules.inbody_validation import HARD_LIMITS, SEGMENT_MAX_KG


def _limited(name: str) -> Any:
    low, high = HARD_LIMITS[name]
    return Field(default=None, ge=low, le=high)


SegmentKg = Annotated[float | None, Field(ge=0, le=SEGMENT_MAX_KG)]


class AnalyzeIn(BaseModel):
    file_id: uuid.UUID  # an uploaded file of kind "inbody"


class Segments(BaseModel):
    left_arm: SegmentKg = None
    right_arm: SegmentKg = None
    trunk: SegmentKg = None
    left_leg: SegmentKg = None
    right_leg: SegmentKg = None


class FlagOut(BaseModel):
    field: str
    code: str  # out_of_range | implausible | inconsistent | missing
    message: str


class InbodyOut(BaseModel):
    id: uuid.UUID
    taken_on: dt.date
    file_id: uuid.UUID | None
    confirmed: bool
    confirmed_at: dt.datetime | None
    created_at: dt.datetime
    weight_kg: float | None
    smm_kg: float | None
    body_fat_mass_kg: float | None
    pbf_pct: float | None
    bmi: float | None
    tbw_l: float | None
    bmr_kcal: int | None
    visceral_fat_level: int | None
    inbody_score: int | None
    segmental_lean: Segments | None
    segmental_fat: Segments | None
    # How sure the reading is, per field (0 to 1); 1 for values the user typed.
    confidence: dict[str, float]
    flags: list[FlagOut]  # values to check before confirming


class InbodyEditIn(BaseModel):
    """Fix a draft. Only the fields sent are changed; null clears a value."""

    taken_on: dt.date | None = None
    weight_kg: float | None = _limited("weight_kg")
    smm_kg: float | None = _limited("smm_kg")
    body_fat_mass_kg: float | None = _limited("body_fat_mass_kg")
    pbf_pct: float | None = _limited("pbf_pct")
    bmi: float | None = _limited("bmi")
    tbw_l: float | None = _limited("tbw_l")
    bmr_kcal: int | None = _limited("bmr_kcal")
    visceral_fat_level: int | None = _limited("visceral_fat_level")
    inbody_score: int | None = _limited("inbody_score")
    segmental_lean: Segments | None = None
    segmental_fat: Segments | None = None


class TrendPoint(BaseModel):
    date: dt.date
    weight_kg: float | None
    smm_kg: float | None
    body_fat_mass_kg: float | None
    pbf_pct: float | None


class TrendChange(BaseModel):
    from_date: dt.date
    to_date: dt.date
    days: int
    weight_kg: float | None  # differences, latest minus the one before
    smm_kg: float | None
    body_fat_mass_kg: float | None
    pbf_pct: float | None
    interpretation_code: str | None
    interpretation: str | None


class TrendOut(BaseModel):
    points: list[TrendPoint]  # confirmed scans, oldest first
    change: TrendChange | None  # between the last two; null with fewer than two
