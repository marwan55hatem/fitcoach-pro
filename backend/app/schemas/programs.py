import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel

from app.models.enums import ProgramStatus


class GenerateProgramIn(BaseModel):
    use_ai: bool = (
        True  # False builds from the rules only; nothing goes to an AI provider
    )
    template: Literal["four_day_split", "full_body", "push_pull_legs"] | None = None


class ViolationOut(BaseModel):
    code: str
    message: str
    day_index: int | None = None
    muscle: str | None = None


class ProgramExerciseOut(BaseModel):
    id: uuid.UUID
    exercise_id: uuid.UUID
    name: str
    muscle_primary: str
    equipment: str | None
    order: int
    sets: int
    rep_min: int
    rep_max: int
    rest_s: int | None
    notes: str | None
    image_url: str | None  # short-lived signed URL


class ProgramDayOut(BaseModel):
    id: uuid.UUID
    day_index: int
    name: str
    focus: str | None
    rationale: str | None
    estimated_minutes: int
    exercises: list[ProgramExerciseOut]


class ProgramOut(BaseModel):
    id: uuid.UUID
    version: int
    status: ProgramStatus
    generated_by: str
    rationale: str | None
    created_at: dt.datetime
    days: list[ProgramDayOut]
    warnings: list[ViolationOut]
    errors: list[ViolationOut]


class GeneratedProgramOut(ProgramOut):
    source: str  # "ai" or "template"
    fallback_reason: str | None  # why the template was used instead of the AI
