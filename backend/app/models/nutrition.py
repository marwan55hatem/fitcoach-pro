import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, OwnedBase, TimestampMixin, enum_col
from app.models.enums import MealSlot, MealSource


class MealOption(IdMixin, TimestampMixin, Base):
    """Catalog meal suggestion. user_id NULL = shared catalog; set = the user's own."""

    __tablename__ = "meal_options"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(sa.String(200))
    slot: Mapped[MealSlot | None] = mapped_column(enum_col(MealSlot))
    items: Mapped[list[Any]] = mapped_column(server_default=sa.text("'[]'::jsonb"))
    kcal: Mapped[int]
    protein_g: Mapped[Decimal] = mapped_column(sa.Numeric(7, 1))
    carbs_g: Mapped[Decimal] = mapped_column(sa.Numeric(7, 1))
    fat_g: Mapped[Decimal] = mapped_column(sa.Numeric(7, 1))
    tags: Mapped[list[Any]] = mapped_column(server_default=sa.text("'[]'::jsonb"))

    __table_args__ = (sa.CheckConstraint("kcal >= 0", name="kcal_non_negative"),)


class MealLog(OwnedBase, Base):
    """A logged meal. AI-derived entries (photo/text) are drafts until confirmed=true."""

    __tablename__ = "meal_logs"

    date: Mapped[dt.date]
    slot: Mapped[MealSlot] = mapped_column(enum_col(MealSlot))
    source: Mapped[MealSource] = mapped_column(enum_col(MealSource))
    name: Mapped[str] = mapped_column(sa.String(200))
    items: Mapped[list[Any]] = mapped_column(server_default=sa.text("'[]'::jsonb"))
    kcal: Mapped[int | None]
    protein_g: Mapped[Decimal | None] = mapped_column(sa.Numeric(7, 1))
    carbs_g: Mapped[Decimal | None] = mapped_column(sa.Numeric(7, 1))
    fat_g: Mapped[Decimal | None] = mapped_column(sa.Numeric(7, 1))
    confidence: Mapped[Decimal | None] = mapped_column(sa.Numeric(3, 2))
    ai_raw: Mapped[dict[str, Any] | None]
    user_edited: Mapped[bool] = mapped_column(server_default=sa.false())
    confirmed: Mapped[bool] = mapped_column(server_default=sa.false())
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL")
    )
    meal_option_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("meal_options.id", ondelete="SET NULL")
    )

    __table_args__ = (
        sa.Index("ix_meal_logs_user_id_date", "user_id", "date"),
        sa.CheckConstraint("kcal IS NULL OR kcal >= 0", name="kcal_non_negative"),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1", name="confidence_range"
        ),
    )


class WaterLog(OwnedBase, Base):
    __tablename__ = "water_logs"

    date: Mapped[dt.date]
    ml: Mapped[int]
    logged_at: Mapped[dt.datetime] = mapped_column(server_default=sa.func.now())

    __table_args__ = (
        sa.Index("ix_water_logs_user_id_date", "user_id", "date"),
        sa.CheckConstraint("ml > 0", name="ml_positive"),
    )


class NutritionSettings(OwnedBase, Base):
    """Per-user changes to the computed targets. Only approved adjustments write here."""

    __tablename__ = "nutrition_settings"

    calorie_adjustment_kcal: Mapped[int] = mapped_column(server_default=sa.text("0"))
    protein_g_per_kg: Mapped[Decimal | None] = mapped_column(sa.Numeric(3, 2))

    __table_args__ = (
        sa.UniqueConstraint("user_id"),
        sa.CheckConstraint(
            "calorie_adjustment_kcal BETWEEN -1000 AND 1000",
            name="calorie_adjustment_range",
        ),
        sa.CheckConstraint(
            "protein_g_per_kg IS NULL OR protein_g_per_kg BETWEEN 1 AND 3",
            name="protein_range",
        ),
    )
