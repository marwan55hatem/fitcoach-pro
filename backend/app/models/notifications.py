import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OwnedBase, enum_col
from app.models.enums import ChannelType, NotificationStatus


class Reminder(OwnedBase, Base):
    """One reminder type per user (app/rules/reminders.py lists the types)."""

    __tablename__ = "reminders"

    type: Mapped[str] = mapped_column(
        sa.String(64)
    )  # workout | meals | water | weigh_in | checkin ...
    schedule: Mapped[dict[str, Any]]  # {"time": "18:00", "days": [0, 2, 4]} etc.
    channel: Mapped[ChannelType] = mapped_column(enum_col(ChannelType))
    quiet_hours: Mapped[dict[str, Any] | None]  # overrides the user's quiet hours
    enabled: Mapped[bool] = mapped_column(server_default=sa.true())
    snooze_until: Mapped[dt.datetime | None]

    __table_args__ = (
        sa.Index("ix_reminders_user_id_enabled", "user_id", "enabled"),
        sa.UniqueConstraint("user_id", "type"),
    )


class NotificationSettings(OwnedBase, Base):
    """The user's general notification preferences."""

    __tablename__ = "notification_settings"

    quiet_start: Mapped[dt.time | None] = mapped_column(
        sa.Time, server_default=sa.text("'22:00'")
    )
    quiet_end: Mapped[dt.time | None] = mapped_column(
        sa.Time, server_default=sa.text("'07:00'")
    )
    max_per_day: Mapped[int] = mapped_column(server_default=sa.text("6"))
    # Off by default: no calories or other numbers in a notification's text.
    show_details: Mapped[bool] = mapped_column(server_default=sa.false())

    __table_args__ = (
        sa.UniqueConstraint("user_id"),
        sa.CheckConstraint("max_per_day BETWEEN 1 AND 20", name="max_per_day_range"),
    )


class NotificationChannel(OwnedBase, Base):
    """A place to send to: a web push device (several allowed) or one Telegram chat."""

    __tablename__ = "notification_channels"

    type: Mapped[ChannelType] = mapped_column(enum_col(ChannelType))
    address: Mapped[dict[str, Any]]  # telegram chat id / web-push subscription
    verified: Mapped[bool] = mapped_column(server_default=sa.false())
    # sha256 of a web push endpoint: one row per browser, whoever subscribed last.
    endpoint_hash: Mapped[str | None] = mapped_column(sa.String(64), unique=True)
    label: Mapped[str | None] = mapped_column(sa.String(100))  # e.g. "Android phone"

    __table_args__ = (
        sa.Index(
            "uq_notification_channels_one_telegram",
            "user_id",
            unique=True,
            postgresql_where=sa.text("type = 'telegram'"),
        ),
        sa.CheckConstraint(
            "type <> 'webpush' OR endpoint_hash IS NOT NULL",
            name="webpush_has_endpoint",
        ),
    )


class TelegramLinkCode(OwnedBase, Base):
    """A short-lived, one-time code the user sends to the bot (/start <code>)."""

    __tablename__ = "telegram_link_codes"

    code_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    expires_at: Mapped[dt.datetime]
    used_at: Mapped[dt.datetime | None]


class NotificationLog(OwnedBase, Base):
    __tablename__ = "notification_log"

    reminder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reminders.id", ondelete="SET NULL")
    )
    sent_at: Mapped[dt.datetime] = mapped_column(server_default=sa.func.now())
    # The reminder slot (UTC). Unique per reminder: a slot is never sent twice.
    scheduled_for: Mapped[dt.datetime | None]
    type: Mapped[str] = mapped_column(sa.String(64))
    channel: Mapped[ChannelType] = mapped_column(enum_col(ChannelType))
    status: Mapped[NotificationStatus] = mapped_column(enum_col(NotificationStatus))
    reason: Mapped[str | None] = mapped_column(sa.String(32))  # why it was skipped
    opened_at: Mapped[dt.datetime | None]  # the user tapped it

    __table_args__ = (
        sa.Index("ix_notification_log_user_id_sent_at", "user_id", "sent_at"),
        sa.UniqueConstraint("reminder_id", "scheduled_for"),
    )
