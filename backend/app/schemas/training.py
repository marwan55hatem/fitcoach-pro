import datetime as dt
import uuid

from pydantic import BaseModel, Field, model_validator


class StartSessionIn(BaseModel):
    program_day_id: uuid.UUID | None = None  # None starts an empty session
    performed_on: dt.date | None = None  # defaults to today in the user's timezone


class SetPatchIn(BaseModel):
    """Only the fields sent are changed. Send null to clear a value."""

    weight_kg: float | None = Field(default=None, ge=0, le=1000)
    reps: int | None = Field(default=None, ge=0, le=100)
    rpe: float | None = Field(default=None, ge=1, le=10)
    done: bool | None = None
    is_warmup: bool | None = None


class SwapIn(BaseModel):
    program_exercise_id: uuid.UUID  # the planned slot whose remaining sets change
    exercise_id: uuid.UUID  # the exercise to do instead


class AddSetIn(BaseModel):
    exercise_id: uuid.UUID
    weight_kg: float | None = Field(default=None, ge=0, le=1000)
    reps: int | None = Field(default=None, ge=0, le=100)
    rpe: float | None = Field(default=None, ge=1, le=10)
    done: bool = False
    is_warmup: bool = False

    @model_validator(mode="after")
    def _done_needs_reps(self) -> "AddSetIn":
        if self.done and self.reps is None:
            raise ValueError("a done set needs reps")
        return self


class SetOut(BaseModel):
    id: uuid.UUID
    exercise_id: uuid.UUID
    exercise_name: str
    program_exercise_id: uuid.UUID | None
    set_number: int
    weight_kg: float | None
    reps: int | None
    rpe: float | None
    done: bool
    is_warmup: bool
    target_reps_min: int | None
    target_reps_max: int | None
    target_weight_kg: float | None


class SessionSummary(BaseModel):
    id: uuid.UUID
    program_day_id: uuid.UUID | None
    day_name: str | None
    performed_on: dt.date
    started_at: dt.datetime | None
    ended_at: dt.datetime | None
    sets_done: int
    sets_planned: int
    volume_kg: float  # sum of weight x reps over done working sets


class SessionOut(SessionSummary):
    notes: str | None
    sets: list[SetOut]


class FinishOut(SessionOut):
    new_records: list[uuid.UUID]  # exercises where this session set a personal best


class SessionList(BaseModel):
    items: list[SessionSummary]
    total: int
    limit: int
    offset: int


class BestSetOut(BaseModel):
    weight_kg: float
    reps: int
    date: dt.date
    estimated_1rm_kg: float | None


class PersonalBestOut(BaseModel):
    best_e1rm: BestSetOut | None
    heaviest: BestSetOut | None
    most_reps: int


class LastSetOut(BaseModel):
    set_number: int
    weight_kg: float | None
    reps: int
    rpe: float | None


class ExerciseLastOut(BaseModel):
    exercise_id: uuid.UUID
    last_date: dt.date | None
    last_sets: list[LastSetOut]
    personal_best: PersonalBestOut | None


class RecordOut(PersonalBestOut):
    exercise_id: uuid.UUID
    name: str
    date: dt.date  # when the latest of these bests was set


class WeekVolumeOut(BaseModel):
    week_start: dt.date
    sets_by_muscle: dict[str, float]


class VolumeOut(BaseModel):
    weeks: list[WeekVolumeOut]
    target_min: int | None  # weekly sets per muscle for the user's level
    target_max: int | None
