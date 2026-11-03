"""Body weight and daily check-ins. Trends come from app/rules/weight_trend.py."""

import datetime as dt
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Checkin, User, WeightLog
from app.rules.weight_trend import (
    WINDOW_DAYS,
    TrendPoint,
    WeightPoint,
    moving_average,
    weekly_change,
)
from app.schemas.body import CheckinIn


def save_weight(db: Session, user: User, date: dt.date, weight_kg: float) -> WeightLog:
    """One weight per day: a second log on the same day replaces the first."""
    value = Decimal(str(round(weight_kg, 2)))
    db.execute(
        insert(WeightLog)
        .values(user_id=user.id, date=date, weight_kg=value)
        .on_conflict_do_update(
            index_elements=[WeightLog.user_id, WeightLog.date],
            set_={"weight_kg": value, "updated_at": sa.func.now()},
        )
    )
    db.commit()
    row = db.scalar(
        sa.select(WeightLog).where(WeightLog.user_id == user.id, WeightLog.date == date)
    )
    assert row is not None
    db.refresh(row)
    return row


def weight_trend(
    db: Session, user: User, *, days: int, today: dt.date
) -> tuple[list[TrendPoint], float | None]:
    """Points in the last `days` days with their averages, and the weekly change."""
    first = today - dt.timedelta(days=days - 1)
    # Earlier logs are read too, so the first point's average has its full window.
    since = min(
        first - dt.timedelta(days=WINDOW_DAYS - 1),
        today - dt.timedelta(days=2 * WINDOW_DAYS - 1),  # for the weekly change
    )
    rows = db.execute(
        sa.select(WeightLog.date, WeightLog.weight_kg).where(
            WeightLog.user_id == user.id,
            WeightLog.date >= since,
            WeightLog.date <= today,
        )
    )
    points = [WeightPoint(d, float(kg)) for d, kg in rows]
    trend = [p for p in moving_average(points) if p.date >= first]
    return trend, weekly_change(points, as_of=today)


def save_checkin(db: Session, user: User, date: dt.date, data: CheckinIn) -> Checkin:
    """One check-in per day; sending again replaces that day's answers."""
    values = data.model_dump(exclude={"date"})
    if values["sleep_h"] is not None:
        values["sleep_h"] = Decimal(str(round(values["sleep_h"], 1)))
    db.execute(
        insert(Checkin)
        .values(user_id=user.id, date=date, **values)
        .on_conflict_do_update(
            index_elements=[Checkin.user_id, Checkin.date],
            set_={**values, "updated_at": sa.func.now()},
        )
    )
    db.commit()
    row = db.scalar(
        sa.select(Checkin).where(Checkin.user_id == user.id, Checkin.date == date)
    )
    assert row is not None
    db.refresh(row)
    return row


def list_checkins(
    db: Session, user: User, *, days: int, today: dt.date
) -> list[Checkin]:
    first = today - dt.timedelta(days=days - 1)
    return list(
        db.scalars(
            sa.select(Checkin)
            .where(Checkin.user_id == user.id, Checkin.date >= first)
            .order_by(Checkin.date.desc())
        )
    )
