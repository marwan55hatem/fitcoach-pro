"""Pending proposals from the coach (and, later, the weekly review).

Approving re-checks the proposal against the user's CURRENT state. Applying approved
changes arrives with the weekly review step; until then approve answers 501 after
the check, so nothing is ever changed by accident.
"""

import datetime as dt
import uuid
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.api.deps import DbSession, HealthUser
from app.models import Recommendation, User
from app.models.enums import RecommendationStatus
from app.rules.limits import validate_recommendation
from app.services import coaching

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


class RecommendationOut(BaseModel):
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    reason: str | None
    status: RecommendationStatus
    created_at: dt.datetime


def _out(rec: Recommendation) -> RecommendationOut:
    return RecommendationOut(
        id=rec.id,
        kind=rec.kind,
        payload=rec.payload,
        reason=rec.explanation,
        status=rec.status,
        created_at=rec.created_at,
    )


def _owned(db: DbSession, user: User, rec_id: uuid.UUID) -> Recommendation:
    rec = db.scalar(
        sa.select(Recommendation).where(
            Recommendation.id == rec_id, Recommendation.user_id == user.id
        )
    )
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return rec


def _pending(rec: Recommendation) -> None:
    if rec.status is not RecommendationStatus.PENDING:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "not_pending", "message": "This was already decided."},
        )


@router.get("", response_model=list[RecommendationOut])
def recommendations(
    db: DbSession,
    user: HealthUser,
    status_: Annotated[RecommendationStatus | None, Query(alias="status")] = None,
) -> list[RecommendationOut]:
    query = sa.select(Recommendation).where(Recommendation.user_id == user.id)
    if status_ is not None:
        query = query.where(Recommendation.status == status_)
    rows = db.scalars(query.order_by(Recommendation.created_at.desc()).limit(100))
    return [_out(r) for r in rows]


@router.post("/{rec_id}/reject", response_model=RecommendationOut)
def reject(rec_id: uuid.UUID, db: DbSession, user: HealthUser) -> RecommendationOut:
    rec = _owned(db, user, rec_id)
    _pending(rec)
    rec.status = RecommendationStatus.REJECTED
    db.commit()
    return _out(rec)


@router.post("/{rec_id}/approve", response_model=RecommendationOut)
def approve(rec_id: uuid.UUID, db: DbSession, user: HealthUser) -> RecommendationOut:
    rec = _owned(db, user, rec_id)
    _pending(rec)
    verdict = validate_recommendation(
        rec.kind, rec.payload, coaching.user_state(db, user)
    )
    if not verdict.allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "no_longer_valid",
                "reason": verdict.reason,
                "message": "Your situation changed, so this is no longer safe to apply.",
            },
        )
    # Applying lands with the weekly review (next step). Until then, never change data.
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        detail={
            "code": "approval_not_ready",
            "message": "Applying approved changes arrives in the next update.",
        },
    )
