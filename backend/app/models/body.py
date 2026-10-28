import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OwnedBase, enum_col
from app.models.enums import PhotoView


class WeightLog(OwnedBase, Base):
    __tablename__ = "weight_logs"

    date: Mapped[dt.date]
    weight_kg: Mapped[Decimal] = mapped_column(sa.Numeric(5, 2))

    __table_args__ = (
        sa.UniqueConstraint("user_id", "date"),
        sa.CheckConstraint("weight_kg BETWEEN 20 AND 500", name="weight_range"),
    )


class InbodyScan(OwnedBase, Base):
    """InBody scan. Fields are AI-extracted drafts until confirmed=true by the user."""

    __tablename__ = "inbody_scans"

    taken_on: Mapped[dt.date]
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL")
    )
    weight_kg: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 1))
    smm_kg: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 1))
    body_fat_mass_kg: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 1))
    pbf_pct: Mapped[Decimal | None] = mapped_column(sa.Numeric(4, 1))
    bmi: Mapped[Decimal | None] = mapped_column(sa.Numeric(4, 1))
    tbw_l: Mapped[Decimal | None] = mapped_column(sa.Numeric(5, 1))
    bmr_kcal: Mapped[int | None]
    visceral_fat_level: Mapped[int | None]
    inbody_score: Mapped[int | None]
    segmental: Mapped[dict[str, Any] | None]
    extraction_confidence: Mapped[dict[str, Any] | None]
    confirmed: Mapped[bool] = mapped_column(server_default=sa.false())
    confirmed_at: Mapped[dt.datetime | None]
    ai_raw: Mapped[dict[str, Any] | None]

    __table_args__ = (
        sa.Index("ix_inbody_scans_user_id_taken_on", "user_id", "taken_on"),
        sa.CheckConstraint(
            "pbf_pct IS NULL OR pbf_pct BETWEEN 0 AND 100", name="pbf_range"
        ),
        sa.CheckConstraint(
            "inbody_score IS NULL OR inbody_score BETWEEN 0 AND 100", name="score_range"
        ),
        sa.CheckConstraint(
            "visceral_fat_level IS NULL OR visceral_fat_level BETWEEN 1 AND 30",
            name="visceral_range",
        ),
        sa.CheckConstraint(
            "confirmed = (confirmed_at IS NOT NULL)", name="confirmed_has_timestamp"
        ),
    )


class ProgressPhoto(OwnedBase, Base):
    __tablename__ = "progress_photos"

    taken_on: Mapped[dt.date]
    view: Mapped[PhotoView] = mapped_column(enum_col(PhotoView))
    file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("files.id", ondelete="CASCADE")
    )
    notes: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (
        sa.UniqueConstraint("user_id", "taken_on", "view"),
        sa.Index("ix_progress_photos_user_id_taken_on", "user_id", "taken_on"),
    )


class Checkin(OwnedBase, Base):
    __tablename__ = "checkins"

    date: Mapped[dt.date]
    sleep_h: Mapped[Decimal | None] = mapped_column(sa.Numeric(3, 1))
    soreness: Mapped[int | None]
    stress: Mapped[int | None]
    energy: Mapped[int | None]
    motivation: Mapped[int | None]
    notes: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (
        sa.UniqueConstraint("user_id", "date"),
        sa.CheckConstraint(
            "sleep_h IS NULL OR sleep_h BETWEEN 0 AND 24", name="sleep_range"
        ),
        sa.CheckConstraint(
            "soreness IS NULL OR soreness BETWEEN 1 AND 5", name="soreness_range"
        ),
        sa.CheckConstraint(
            "stress IS NULL OR stress BETWEEN 1 AND 5", name="stress_range"
        ),
        sa.CheckConstraint(
            "energy IS NULL OR energy BETWEEN 1 AND 5", name="energy_range"
        ),
        sa.CheckConstraint(
            "motivation IS NULL OR motivation BETWEEN 1 AND 5", name="motivation_range"
        ),
    )
