from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, HealthUser, not_in_future
from app.models import Checkin
from app.schemas.body import (
    CheckinIn,
    CheckinOut,
    WeightHistoryOut,
    WeightIn,
    WeightPointOut,
)
from app.services import body as svc
from app.services import onboarding

router = APIRouter(tags=["body"])


def _checkin_out(row: Checkin) -> CheckinOut:
    return CheckinOut(
        id=row.id,
        date=row.date,
        sleep_h=float(row.sleep_h) if row.sleep_h is not None else None,
        soreness=row.soreness,
        stress=row.stress,
        energy=row.energy,
        motivation=row.motivation,
        notes=row.notes,
    )


def _history(db: DbSession, user: HealthUser, days: int) -> WeightHistoryOut:
    trend, change = svc.weight_trend(
        db, user, days=days, today=onboarding.user_today(db, user)
    )
    last = trend[-1] if trend else None
    return WeightHistoryOut(
        points=[
            WeightPointOut(
                date=p.date, weight_kg=p.weight_kg, average_7d=round(p.average_7d, 2)
            )
            for p in trend
        ],
        latest_kg=last.weight_kg if last else None,
        average_7d=round(last.average_7d, 2) if last else None,
        weekly_change_kg=round(change, 2) if change is not None else None,
    )


@router.post("/weight", response_model=WeightHistoryOut)
def log_weight(body: WeightIn, db: DbSession, user: HealthUser) -> WeightHistoryOut:
    """Log a weight (one per day; logging again that day replaces it)."""
    date = not_in_future(body.date or onboarding.user_today(db, user), db, user)
    svc.save_weight(db, user, date, body.weight_kg)
    return _history(db, user, 90)


@router.get("/weight", response_model=WeightHistoryOut)
def weight_history(
    db: DbSession,
    user: HealthUser,
    days: Annotated[int, Query(ge=1, le=730)] = 90,
) -> WeightHistoryOut:
    """Weights with a 7-day moving average and the change versus the week before."""
    return _history(db, user, days)


@router.post("/checkins", response_model=CheckinOut)
def checkin(body: CheckinIn, db: DbSession, user: HealthUser) -> CheckinOut:
    """Today's (or a past day's) check-in. One per day; sending again replaces it."""
    date = not_in_future(body.date or onboarding.user_today(db, user), db, user)
    return _checkin_out(svc.save_checkin(db, user, date, body))


@router.get("/checkins", response_model=list[CheckinOut])
def checkins(
    db: DbSession,
    user: HealthUser,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> list[CheckinOut]:
    return [
        _checkin_out(c)
        for c in svc.list_checkins(
            db, user, days=days, today=onboarding.user_today(db, user)
        )
    ]
