"""Pending proposals from the coach and the weekly review, and the user's decisions.

Approving re-checks the proposal against the user's CURRENT state, then applies it
(app/services/approvals.py). Approve is idempotent; nothing applies without it.
"""

import datetime as dt
import uuid
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import DbSession, HealthUser
from app.models import Recommendation
from app.models.enums import RecommendationStatus
from app.services import approvals

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


class RecommendationOut(BaseModel):
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    reason: str | None
    source: str
    status: RecommendationStatus
    created_at: dt.datetime
    expires_at: dt.datetime | None
    decided_at: dt.datetime | None
    applied_at: dt.datetime | None
    decision_note: str | None


class RejectOut(RecommendationOut):
    note_saved: bool


class RejectIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    remember: bool = False  # save the reason as a coach note (checked first)


def to_out(rec: Recommendation) -> RecommendationOut:
    return RecommendationOut(
        id=rec.id,
        kind=rec.kind,
        payload=rec.payload,
        reason=rec.explanation,
        source=rec.source,
        status=rec.status,
        created_at=rec.created_at,
        expires_at=rec.expires_at,
        decided_at=rec.decided_at,
        applied_at=rec.applied_at,
        decision_note=rec.decision_note,
    )


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")


def _conflict(code: str, message: str, **extra: str) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT, detail={"code": code, **extra, "message": message}
    )


_NOT_PENDING = ("not_pending", "This was already decided.")
_EXPIRED = ("expired", "This suggestion is too old. A newer review will suggest again.")


@router.get("", response_model=list[RecommendationOut])
def recommendations(
    db: DbSession,
    user: HealthUser,
    status_: Annotated[RecommendationStatus | None, Query(alias="status")] = None,
) -> list[RecommendationOut]:
    if approvals.expire_stale(db, user.id):
        db.commit()
    query = sa.select(Recommendation).where(Recommendation.user_id == user.id)
    if status_ is not None:
        query = query.where(Recommendation.status == status_)
    rows = db.scalars(query.order_by(Recommendation.created_at.desc()).limit(100))
    return [to_out(r) for r in rows]


@router.post("/{rec_id}/reject", response_model=RejectOut)
def reject(
    rec_id: uuid.UUID,
    db: DbSession,
    user: HealthUser,
    body: RejectIn | None = None,
) -> RejectOut:
    data = body or RejectIn()
    try:
        rec, saved = approvals.reject(
            db, user, rec_id, reason=data.reason, remember=data.remember
        )
    except approvals.RecommendationNotFoundError:
        raise _not_found() from None
    except approvals.NotPendingError:
        raise _conflict(*_NOT_PENDING) from None
    except approvals.ExpiredError:
        raise _conflict(*_EXPIRED) from None
    return RejectOut(**to_out(rec).model_dump(), note_saved=saved)


@router.post("/{rec_id}/approve", response_model=RecommendationOut)
def approve(rec_id: uuid.UUID, db: DbSession, user: HealthUser) -> RecommendationOut:
    try:
        rec = approvals.approve(db, user, rec_id)
    except approvals.RecommendationNotFoundError:
        raise _not_found() from None
    except approvals.NotPendingError:
        raise _conflict(*_NOT_PENDING) from None
    except approvals.ExpiredError:
        raise _conflict(*_EXPIRED) from None
    except approvals.NoLongerValidError as exc:
        raise _conflict(
            "no_longer_valid",
            "Your situation changed, so this is no longer safe to apply.",
            reason=exc.reason,
        ) from None
    return to_out(rec)
