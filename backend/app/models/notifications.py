import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OwnedBase, enum_col
from app.models.enums import ChannelType, NotificationStatus


class Reminder(OwnedBase, Base):
    __tablename__ = "reminders"

    type: Mapped[str] = mapped_column(
        sa.String(64)
    )  # workout | meal | weigh_in | checkin ...
    schedule: Mapped[dict[str, Any]]
    channel: Mapped[ChannelType] = mapped_column(enum_col(ChannelType))
    quiet_hours: Mapped[dict[str, Any] | None]
    enabled: Mapped[bool] = mapped_column(server_default=sa.true())

    __table_args__ = (sa.Index("ix_reminders_user_id_enabled", "user_id", "enabled"),)


class NotificationChannel(OwnedBase, Base):
    __tablename__ = "notification_channels"

    type: Mapped[ChannelType] = mapped_column(enum_col(ChannelType))
    address: Mapped[dict[str, Any]]  # telegram chat id / web-push subscription
    verified: Mapped[bool] = mapped_column(server_default=sa.false())

    __table_args__ = (sa.UniqueConstraint("user_id", "type"),)


class NotificationLog(OwnedBase, Base):
    __tablename__ = "notification_log"

    reminder_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reminders.id", ondelete="SET NULL")
    )
    sent_at: Mapped[dt.datetime] = mapped_column(server_default=sa.func.now())
    type: Mapped[str] = mapped_column(sa.String(64))
    channel: Mapped[ChannelType] = mapped_column(enum_col(ChannelType))
    status: Mapped[NotificationStatus] = mapped_column(enum_col(NotificationStatus))

    __table_args__ = (
        sa.Index("ix_notification_log_user_id_sent_at", "user_id", "sent_at"),
    )
