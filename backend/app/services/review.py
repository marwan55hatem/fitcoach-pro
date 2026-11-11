"""The weekly review: the rules propose, the AI explains, the user decides.

run_weekly_review gathers the last four weeks, asks rules.weekly_review for proposals
and stores them as pending recommendations that expire after 14 days. A new review
expires the previous review's undecided items. The AI (optional: it needs the
ai_processing consent) only writes the summary and reasons; it never sets a number.
Runs at most once per user per local day. The Sunday job is wired in the scheduler.
"""

import datetime as dt
import logging
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.client import AiClient
from app.ai.errors import AiError
from app.ai.review import ReviewText, write_review
from app.models import (
    Checkin,
    MealLog,
    Recommendation,
    User,
    WeeklyReview,
    WorkoutSession,
)
from app.models.enums import ConsentType, RecommendationStatus
from app.rules.review import Recommendation as Proposal
from app.rules.review import ReviewInput, weekly_review
from app.services import approvals, audit, coaching, consents, onboarding

logger = logging.getLogger(__name__)

WINDOW_DAYS = 28


class AlreadyRanTodayError(Exception):
    pass


def latest(db: Session, user: User) -> WeeklyReview | None:
    return db.scalar(
        sa.select(WeeklyReview)
        .where(WeeklyReview.user_id == user.id)
        .order_by(WeeklyReview.run_on.desc())
        .limit(1)
    )


def items_of(db: Session, review: WeeklyReview) -> list[Recommendation]:
    return list(
        db.scalars(
            sa.select(Recommendation)
            .where(Recommendation.review_id == review.id)
            .order_by(Recommendation.created_at, Recommendation.id)
        )
    )


def _ran_on(db: Session, user_id: uuid.UUID, day: dt.date) -> bool:
    return bool(
        db.scalar(
            sa.select(
                sa.exists().where(
                    WeeklyReview.user_id == user_id, WeeklyReview.run_on == day
                )
            )
        )
    )


def _facts(db: Session, user: User, inp: ReviewInput, today: dt.date) -> dict[str, Any]:
    """Four weeks in a few plain numbers, for the AI's summary only."""
    since = today - dt.timedelta(days=WINDOW_DAYS)
    meal_days = db.scalar(
        sa.select(sa.func.count(sa.distinct(MealLog.date))).where(
            MealLog.user_id == user.id,
            MealLog.confirmed.is_(True),
            MealLog.date > since,
        )
    )
    checkins = db.scalar(
        sa.select(sa.func.count()).where(
            Checkin.user_id == user.id, Checkin.date > since
        )
    )
    workouts = db.scalar(
        sa.select(sa.func.count()).where(
            WorkoutSession.user_id == user.id,
            WorkoutSession.ended_at.is_not(None),
            WorkoutSession.performed_on > since,
        )
    )
    return {
        "goal": str(inp.state.goal),
        "workouts_in_4_weeks": workouts or 0,
        "full_training_weeks_in_a_row": inp.hard_weeks,
        "days_with_logged_meals": meal_days or 0,
        "checkins": checkins or 0,
        "weekly_weight_change_kg": list(inp.weekly_changes),
        "readiness_last_7_days": list(inp.readiness_scores),
        "inbody_change": (
            {
                "muscle_kg": round(inp.inbody.smm_change_kg, 1),
                "fat_kg": round(inp.inbody.fat_change_kg, 1),
            }
            if inp.inbody
            else None
        ),
    }


def _for_ai(proposals: list[Proposal], names: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    for p in proposals:
        item: dict[str, Any] = {
            "kind": p.kind.value,
            "change": dict(p.payload),
            "rule": p.message,
        }
        exercise = names.get(str(p.payload.get("exercise_id", "")))
        if exercise:
            item["exercise"] = exercise
        out.append(item)
    return out


def run_weekly_review(db: Session, ai: AiClient, user_id: uuid.UUID) -> WeeklyReview:
    user = db.get(User, user_id)
    assert user is not None
    today = onboarding.user_today(db, user)
    if _ran_on(db, user_id, today):
        raise AlreadyRanTodayError
    inp = coaching.review_input(db, user, today)
    proposals = weekly_review(inp)
    use_ai = consents.has_consent(db, user_id, ConsentType.AI_PROCESSING)
    profile = onboarding.get_profile(db, user_id)
    language = profile.language if profile else "en"
    facts = _facts(db, user, inp, today)
    names = {e.exercise_id: e.name for e in inp.exercises}
    db.commit()  # release the DB connection: the AI call can take many seconds

    text = ReviewText(None, (None,) * len(proposals))
    if use_ai:
        try:
            text = write_review(
                ai,
                user_id=user_id,
                language=language,
                facts=facts,
                proposals=_for_ai(proposals, names),
            )
        except AiError as exc:  # the rules' own words still work
            logger.warning("weekly_review_ai_failed error=%s", type(exc).__name__)

    now = dt.datetime.now(dt.UTC)
    # Undecided items from an earlier review are replaced by this one.
    superseded = db.execute(
        sa.update(Recommendation)
        .where(
            Recommendation.user_id == user_id,
            Recommendation.source == "review",
            Recommendation.status == RecommendationStatus.PENDING,
        )
        .values(status=RecommendationStatus.EXPIRED, decided_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount  # type: ignore[attr-defined]
    approvals.expire_stale(db, user_id)
    review = WeeklyReview(
        user_id=user_id,
        run_on=today,
        summary=text.summary,
        written_by="ai" if text.summary else "rules",
    )
    try:
        with db.begin_nested():
            db.add(review)
            db.flush()
    except IntegrityError as exc:  # another run won the race
        db.rollback()
        raise AlreadyRanTodayError from exc
    for index, (proposal, reason) in enumerate(
        zip(proposals, text.reasons, strict=True)
    ):
        db.add(
            Recommendation(
                user_id=user_id,
                kind=proposal.kind.value,
                payload=dict(proposal.payload),  # the rules' numbers, untouched
                explanation=reason or proposal.message,
                validation={"allowed": True, "reason": "ok", "rule": proposal.reason},
                source="review",
                review_id=review.id,
                expires_at=approvals.expiry_from(now),
                # One microsecond apart, so they list in the rules' order.
                created_at=now + dt.timedelta(microseconds=index),
            )
        )
    audit.record(
        db,
        "review.run",
        user_id,
        entity_type="weekly_review",
        entity_id=review.id,
        meta={
            "proposals": len(proposals),
            "superseded": superseded,
            "written_by": review.written_by,
        },
    )
    db.commit()
    return review
