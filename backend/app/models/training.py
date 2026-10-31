import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, OwnedBase, TimestampMixin, enum_col
from app.models.enums import Experience, Mechanic, ProgramStatus


class Exercise(IdMixin, TimestampMixin, Base):
    """Exercise library. user_id NULL = shared catalog; set = the user's custom exercise."""

    __tablename__ = "exercises"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(200))
    slug: Mapped[str] = mapped_column(sa.String(200))
    muscle_primary: Mapped[str] = mapped_column(sa.String(64), index=True)
    muscles_secondary: Mapped[list[Any]] = mapped_column(
        server_default=sa.text("'[]'::jsonb")
    )
    equipment: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    level: Mapped[Experience | None] = mapped_column(enum_col(Experience))
    mechanic: Mapped[Mechanic | None] = mapped_column(enum_col(Mechanic))
    # strength | powerlifting | olympic weightlifting | strongman | plyometrics | stretching | cardio
    category: Mapped[str | None] = mapped_column(sa.String(32), index=True)
    instructions: Mapped[list[Any]] = mapped_column(
        server_default=sa.text("'[]'::jsonb")
    )
    image_keys: Mapped[list[Any]] = mapped_column(server_default=sa.text("'[]'::jsonb"))
    source: Mapped[str | None] = mapped_column(sa.String(200))
    license: Mapped[str | None] = mapped_column(sa.String(100))
    attribution: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (
        sa.UniqueConstraint("user_id", "slug"),
        # NULLs are distinct in a plain unique constraint, so guard the shared catalog separately.
        sa.Index(
            "uq_exercises_slug_catalog",
            "slug",
            unique=True,
            postgresql_where=sa.text("user_id IS NULL"),
        ),
    )


class Program(OwnedBase, Base):
    __tablename__ = "programs"

    version: Mapped[int]
    status: Mapped[ProgramStatus] = mapped_column(
        enum_col(ProgramStatus), server_default=ProgramStatus.DRAFT.value
    )
    generated_by: Mapped[str] = mapped_column(sa.String(32))  # ai | user | template
    rationale: Mapped[str | None] = mapped_column(sa.Text)
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendations.id", ondelete="SET NULL")
    )

    days: Mapped[list["ProgramDay"]] = relationship(
        back_populates="program",
        order_by="ProgramDay.day_index",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        sa.UniqueConstraint("user_id", "version"),
        sa.Index("ix_programs_user_id_status", "user_id", "status"),
        # At most one active program per user.
        sa.Index(
            "uq_programs_one_active_per_user",
            "user_id",
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
        ),
        sa.CheckConstraint("version >= 1", name="version_positive"),
    )


class ProgramDay(OwnedBase, Base):
    __tablename__ = "program_days"

    program_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("programs.id", ondelete="CASCADE"), index=True
    )
    day_index: Mapped[int]
    name: Mapped[str] = mapped_column(sa.String(100))
    focus: Mapped[str | None] = mapped_column(sa.String(200))
    rationale: Mapped[str | None] = mapped_column(sa.Text)

    program: Mapped[Program] = relationship(back_populates="days")
    exercises: Mapped[list["ProgramExercise"]] = relationship(
        back_populates="day",
        order_by="ProgramExercise.order",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        sa.UniqueConstraint("program_id", "day_index"),
        sa.CheckConstraint("day_index >= 1", name="day_index_positive"),
    )


class ProgramExercise(OwnedBase, Base):
    __tablename__ = "program_exercises"

    program_day_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("program_days.id", ondelete="CASCADE"), index=True
    )
    exercise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exercises.id", ondelete="RESTRICT")
    )
    order: Mapped[int]
    sets: Mapped[int]
    rep_min: Mapped[int]
    rep_max: Mapped[int]
    rest_s: Mapped[int | None]
    notes: Mapped[str | None] = mapped_column(sa.Text)

    day: Mapped[ProgramDay] = relationship(back_populates="exercises")
    exercise: Mapped[Exercise] = relationship()

    __table_args__ = (
        sa.UniqueConstraint("program_day_id", "order"),
        sa.CheckConstraint("sets BETWEEN 1 AND 20", name="sets_range"),
        sa.CheckConstraint("rep_min >= 1 AND rep_max >= rep_min", name="rep_range"),
        sa.CheckConstraint("rest_s IS NULL OR rest_s >= 0", name="rest_non_negative"),
    )


class WorkoutSession(OwnedBase, Base):
    __tablename__ = "workout_sessions"

    program_day_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("program_days.id", ondelete="SET NULL")
    )
    performed_on: Mapped[dt.date]
    started_at: Mapped[dt.datetime | None]
    ended_at: Mapped[dt.datetime | None]
    notes: Mapped[str | None] = mapped_column(sa.Text)

    sets: Mapped[list["SetLog"]] = relationship(
        back_populates="session",
        order_by="SetLog.set_number",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        sa.Index("ix_workout_sessions_user_id_performed_on", "user_id", "performed_on"),
        sa.CheckConstraint(
            "ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at",
            name="ended_after_started",
        ),
    )


class SetLog(OwnedBase, Base):
    __tablename__ = "set_logs"

    workout_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workout_sessions.id", ondelete="CASCADE"), index=True
    )
    exercise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("exercises.id", ondelete="RESTRICT")
    )
    program_exercise_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("program_exercises.id", ondelete="SET NULL")
    )
    set_number: Mapped[int]
    reps: Mapped[int]
    weight_kg: Mapped[Decimal | None] = mapped_column(sa.Numeric(6, 2))
    rpe: Mapped[Decimal | None] = mapped_column(sa.Numeric(3, 1))
    is_warmup: Mapped[bool] = mapped_column(server_default=sa.false())

    session: Mapped[WorkoutSession] = relationship(back_populates="sets")

    __table_args__ = (
        sa.UniqueConstraint("workout_session_id", "exercise_id", "set_number"),
        sa.Index("ix_set_logs_user_id_exercise_id", "user_id", "exercise_id"),
        sa.CheckConstraint("reps >= 0", name="reps_non_negative"),
        sa.CheckConstraint(
            "weight_kg IS NULL OR weight_kg >= 0", name="weight_non_negative"
        ),
        sa.CheckConstraint("rpe IS NULL OR rpe BETWEEN 1 AND 10", name="rpe_range"),
    )
