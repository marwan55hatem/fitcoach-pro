"""The weekly review: run it by hand (once a day) and read the latest one."""

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.ai.client import AiClient, get_ai_client
from app.api.deps import DbSession, HealthUser
from app.api.recommendations import RecommendationOut, to_out
from app.core.rate_limit import ai_limit
from app.models import WeeklyReview
from app.services import review as svc

router = APIRouter(prefix="/review", tags=["review"])
AiDep = Annotated[AiClient, Depends(get_ai_client)]


class ReviewOut(BaseModel):
    id: uuid.UUID
    run_on: dt.date
    summary: str | None  # None: no AI text, the app shows its own
    written_by: str
    recommendations: list[RecommendationOut]


def _out(db: DbSession, review: WeeklyReview) -> ReviewOut:
    return ReviewOut(
        id=review.id,
        run_on=review.run_on,
        summary=review.summary,
        written_by=review.written_by,
        recommendations=[to_out(r) for r in svc.items_of(db, review)],
    )


@router.post("/run", response_model=ReviewOut, status_code=status.HTTP_201_CREATED)
@ai_limit
def run(request: Request, db: DbSession, user: HealthUser, ai: AiDep) -> ReviewOut:
    """Review the last four weeks now. Proposals wait for approval; once per day."""
    try:
        review = svc.run_weekly_review(db, ai, user.id)
    except svc.AlreadyRanTodayError:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "review_already_ran",
                "message": "You already had a review today. Try again tomorrow.",
            },
        ) from None
    return _out(db, review)


@router.get("/latest", response_model=ReviewOut | None)
def latest(db: DbSession, user: HealthUser) -> ReviewOut | None:
    review = svc.latest(db, user)
    return _out(db, review) if review else None
