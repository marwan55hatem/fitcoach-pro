import datetime as dt
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, OwnedBase, TimestampMixin, enum_col
from app.models.enums import (
    ActivityLevel,
    BudgetLevel,
    ConsentType,
    Experience,
    Goal,
    RiskLevel,
    Severity,
    Sex,
    Units,
)


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(sa.String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(sa.String(255))
    display_name: Mapped[str | None] = mapped_column(sa.String(100))
    is_active: Mapped[bool] = mapped_column(server_default=sa.true())
    last_login_at: Mapped[dt.datetime | None]

    profile: Mapped["Profile | None"] = relationship(
        back_populates="user", uselist=False
    )

    __table_args__ = (
        sa.CheckConstraint("email = lower(email)", name="email_lowercase"),
    )


class Consent(OwnedBase, Base):
    """A consent grant. Revoking sets revoked_at; a re-grant adds a new row."""

    __tablename__ = "consents"

    type: Mapped[ConsentType] = mapped_column(enum_col(ConsentType))
    granted_at: Mapped[dt.datetime] = mapped_column(server_default=sa.func.now())
    revoked_at: Mapped[dt.datetime | None]

    __table_args__ = (
        sa.UniqueConstraint("user_id", "type", "granted_at"),
        sa.Index("ix_consents_user_id_type", "user_id", "type"),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at",
            name="revoked_after_granted",
        ),
    )


class Profile(OwnedBase, Base):
    __tablename__ = "profiles"

    birth_date: Mapped[dt.date | None]
    sex: Mapped[Sex | None] = mapped_column(enum_col(Sex))
    height_cm: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 1))
    goal: Mapped[Goal | None] = mapped_column(enum_col(Goal))
    experience: Mapped[Experience | None] = mapped_column(enum_col(Experience))
    days_per_week: Mapped[int | None]
    session_minutes: Mapped[int | None]
    equipment: Mapped[list[Any]] = mapped_column(server_default=sa.text("'[]'::jsonb"))
    activity_level: Mapped[ActivityLevel | None] = mapped_column(
        enum_col(ActivityLevel)
    )
    timezone: Mapped[str] = mapped_column(sa.String(64), server_default="Africa/Cairo")
    language: Mapped[str] = mapped_column(sa.String(8), server_default="en")
    # Set when the user has answered the injuries step (an empty list is a valid answer).
    limitations_reviewed_at: Mapped[dt.datetime | None]
    units: Mapped[Units] = mapped_column(
        enum_col(Units), server_default=Units.METRIC.value
    )

    user: Mapped[User] = relationship(back_populates="profile")

    __table_args__ = (
        sa.UniqueConstraint("user_id"),
        sa.CheckConstraint("days_per_week BETWEEN 1 AND 7", name="days_per_week_range"),
        sa.CheckConstraint(
            "session_minutes BETWEEN 10 AND 240", name="session_minutes_range"
        ),
        sa.CheckConstraint("height_cm BETWEEN 80 AND 260", name="height_range"),
    )


class HealthScreening(OwnedBase, Base):
    """Onboarding / re-screening answers. Latest row by screened_at is current."""

    __tablename__ = "health_screening"

    answers: Mapped[dict[str, Any]]
    risk_level: Mapped[RiskLevel] = mapped_column(enum_col(RiskLevel))
    screened_at: Mapped[dt.datetime] = mapped_column(server_default=sa.func.now())
    clearance_confirmed: Mapped[bool] = mapped_column(server_default=sa.false())

    __table_args__ = (
        sa.Index("ix_health_screening_user_id_screened_at", "user_id", "screened_at"),
    )


class Limitation(OwnedBase, Base):
    __tablename__ = "limitations"

    body_area: Mapped[str] = mapped_column(sa.String(64))
    description: Mapped[str | None] = mapped_column(sa.Text)
    severity: Mapped[Severity] = mapped_column(enum_col(Severity))
    active: Mapped[bool] = mapped_column(server_default=sa.true())

    __table_args__ = (sa.Index("ix_limitations_user_id_active", "user_id", "active"),)


class DietaryPreference(OwnedBase, Base):
    __tablename__ = "dietary_preferences"

    allergies: Mapped[list[Any]] = mapped_column(server_default=sa.text("'[]'::jsonb"))
    intolerances: Mapped[list[Any]] = mapped_column(
        server_default=sa.text("'[]'::jsonb")
    )
    dislikes: Mapped[list[Any]] = mapped_column(server_default=sa.text("'[]'::jsonb"))
    budget_level: Mapped[BudgetLevel | None] = mapped_column(enum_col(BudgetLevel))
    meals_per_day: Mapped[int | None]
    cuisine_hints: Mapped[list[Any]] = mapped_column(
        server_default=sa.text("'[]'::jsonb")
    )

    __table_args__ = (
        sa.UniqueConstraint("user_id"),
        sa.CheckConstraint(
            "meals_per_day BETWEEN 1 AND 10", name="meals_per_day_range"
        ),
    )
