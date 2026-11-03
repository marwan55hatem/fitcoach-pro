import datetime as dt
import uuid
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

Score = Annotated[int, Field(ge=1, le=5)]


class WeightIn(BaseModel):
    weight_kg: float = Field(ge=30, le=300)
    date: dt.date | None = None  # defaults to today in the user's timezone


class WeightPointOut(BaseModel):
    date: dt.date
    weight_kg: float
    average_7d: float


class WeightHistoryOut(BaseModel):
    points: list[WeightPointOut]
    latest_kg: float | None
    average_7d: float | None
    weekly_change_kg: float | None  # last 7 days vs the 7 before; null without both


class CheckinIn(BaseModel):
    date: dt.date | None = None
    sleep_h: float | None = Field(default=None, ge=0, le=24)
    soreness: Score | None = None
    stress: Score | None = None
    energy: Score | None = None
    motivation: Score | None = None
    notes: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None
    ) = None


class CheckinOut(BaseModel):
    id: uuid.UUID
    date: dt.date
    sleep_h: float | None
    soreness: int | None
    stress: int | None
    energy: int | None
    motivation: int | None
    notes: str | None
