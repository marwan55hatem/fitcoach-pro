import enum
import uuid
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.models.enums import Equipment, Experience, Mechanic
from app.rules.exercise_catalog import MUSCLES

# Query/body type for the canonical muscle vocabulary.
Muscle = enum.StrEnum("Muscle", {m: m for m in sorted(MUSCLES)})

Step = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]


class ExerciseSummary(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    muscle_primary: str
    muscles_secondary: list[str]
    equipment: str | None
    level: Experience | None
    mechanic: Mechanic | None
    category: str | None
    is_custom: bool
    image_url: str | None  # short-lived signed URL


class ExerciseDetail(ExerciseSummary):
    instructions: list[str]
    image_urls: list[str]  # short-lived signed URLs
    required_equipment: list[str]
    source: str | None
    license: str | None
    attribution: str | None


class ExerciseList(BaseModel):
    items: list[ExerciseSummary]
    total: int
    limit: int
    offset: int


class SubstitutesOut(BaseModel):
    exercise_id: uuid.UUID
    equipment_used: list[str]
    avoid_areas_used: list[str]
    substitutes: list[ExerciseSummary]


class CustomExerciseIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    muscle_primary: Muscle
    muscles_secondary: list[Muscle] = Field(default_factory=list, max_length=6)
    equipment: Equipment = Equipment.BODY_WEIGHT
    level: Experience | None = None
    mechanic: Mechanic | None = None
    instructions: list[Step] = Field(default_factory=list, max_length=20)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value

    @field_validator("muscles_secondary")
    @classmethod
    def _unique_secondary(cls, value: list[Muscle]) -> list[Muscle]:
        return list(dict.fromkeys(value))
