import datetime as dt
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, OwnedBase, enum_col
from app.models.enums import MessageRole, RecommendationStatus


class Conversation(OwnedBase, Base):
    __tablename__ = "conversations"

    title: Mapped[str | None] = mapped_column(sa.String(200))
    # A rolling summary of the older messages keeps long chats cheap.
    summary: Mapped[str | None] = mapped_column(sa.Text)
    summarized_count: Mapped[int] = mapped_column(server_default=sa.text("0"))
    # Set by a red flag: the coach stays careful on that topic (app/rules/redflags.py).
    safety_mode: Mapped[str | None] = mapped_column(sa.String(32))

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        order_by="Message.created_at",
        cascade="all, delete-orphan",
    )


class Message(OwnedBase, Base):
    """Chat message. Contains personal data: never log its content."""

    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[MessageRole] = mapped_column(enum_col(MessageRole))
    content: Mapped[str] = mapped_column(sa.Text)
    tool_calls: Mapped[list[Any] | None]
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL")
    )
    red_flag: Mapped[str | None] = mapped_column(sa.String(32))  # category, if any
    partial: Mapped[bool] = mapped_column(
        server_default=sa.false()
    )  # cut off mid-reply

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (
        sa.Index(
            "ix_messages_conversation_id_created_at", "conversation_id", "created_at"
        ),
    )


class CoachNote(OwnedBase, Base):
    """A key fact the coach remembers about the user. User-viewable and user-editable."""

    __tablename__ = "coach_notes"

    key: Mapped[str] = mapped_column(sa.String(100))
    value: Mapped[str] = mapped_column(sa.Text)
    source: Mapped[str] = mapped_column(sa.String(16), server_default="ai")  # ai | user

    __table_args__ = (
        sa.UniqueConstraint("user_id", "key"),
        sa.CheckConstraint("source IN ('ai', 'user')", name="source_valid"),
    )


class Recommendation(OwnedBase, Base):
    """An AI *proposal*. The rules engine validates it; the user approves it before it applies."""

    __tablename__ = "recommendations"

    kind: Mapped[str] = mapped_column(sa.String(64))
    payload: Mapped[dict[str, Any]]
    explanation: Mapped[str | None] = mapped_column(sa.Text)
    validation: Mapped[dict[str, Any] | None]  # rules-engine verdict
    status: Mapped[RecommendationStatus] = mapped_column(
        enum_col(RecommendationStatus),
        server_default=RecommendationStatus.PENDING.value,
    )
    applied_at: Mapped[dt.datetime | None]
    source: Mapped[str] = mapped_column(
        sa.String(16), server_default=sa.text("'coach'")
    )  # coach | review
    review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("weekly_reviews.id", ondelete="SET NULL"), index=True
    )
    expires_at: Mapped[dt.datetime | None]
    decided_at: Mapped[dt.datetime | None]
    decision_note: Mapped[str | None] = mapped_column(sa.Text)  # why it was rejected

    __table_args__ = (
        sa.Index("ix_recommendations_user_id_status", "user_id", "status"),
        sa.CheckConstraint(
            "applied_at IS NULL OR status = 'approved'", name="applied_only_if_approved"
        ),
    )


class WeeklyReview(OwnedBase, Base):
    """One run of the weekly review: a short summary; its proposals are recommendations."""

    __tablename__ = "weekly_reviews"

    run_on: Mapped[dt.date]  # the user's local date; one run per day
    summary: Mapped[str | None] = mapped_column(sa.Text)  # None: no AI text
    written_by: Mapped[str] = mapped_column(sa.String(16))  # ai | rules

    __table_args__ = (sa.UniqueConstraint("user_id", "run_on"),)
