import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, OwnedBase


class AiUsage(OwnedBase, Base):
    """Per-user, per-day token usage and estimated cost (for budgets and rate limits)."""

    __tablename__ = "ai_usage"

    date: Mapped[dt.date]
    tokens_in: Mapped[int] = mapped_column(sa.BigInteger, server_default="0")
    tokens_out: Mapped[int] = mapped_column(sa.BigInteger, server_default="0")
    cost_estimate: Mapped[Decimal] = mapped_column(
        sa.Numeric(10, 4), server_default="0"
    )

    __table_args__ = (
        sa.UniqueConstraint("user_id", "date"),
        sa.CheckConstraint(
            "tokens_in >= 0 AND tokens_out >= 0", name="tokens_non_negative"
        ),
    )


class AuditLog(IdMixin, Base):
    """Append-only record of sensitive actions (consent, export, delete, approvals).

    metadata must never contain health data or image content. user_id is kept
    (SET NULL) when the user is deleted so the deletion itself stays auditable.
    """

    __tablename__ = "audit_log"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(sa.String(64))
    entity_type: Mapped[str | None] = mapped_column(sa.String(64))
    entity_id: Mapped[uuid.UUID | None]
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata")
    created_at: Mapped[dt.datetime] = mapped_column(server_default=sa.func.now())

    __table_args__ = (
        sa.Index("ix_audit_log_user_id_created_at", "user_id", "created_at"),
        sa.Index("ix_audit_log_action_created_at", "action", "created_at"),
    )
