import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models import (
    DietaryPreference,
    HealthScreening,
    Limitation,
    Profile,
    User,
    WeightLog,
)
from app.models.enums import ConsentType, RiskLevel
from app.rules import body_metrics
from app.rules.screening import (
    PlanAccess,
    ScreeningAnswers,
    ScreeningResult,
    can_receive_plan,
    disordered_eating_flag,
    evaluate_screening,
)
from app.schemas.onboarding import (
    DietIn,
    LimitationIn,
    ProfileIn,
    ScreeningIn,
)
from app.services import audit
from app.services.consents import has_consent


class UnderageError(Exception):
    pass


class NoScreeningError(Exception):
    pass


class ClearanceNotNeededError(Exception):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _today(timezone: str) -> dt.date:
    return dt.datetime.now(ZoneInfo(timezone)).date()


DEFAULT_TIMEZONE = "Africa/Cairo"


# --- profile --------------------------------------------------------------------------


def get_profile(db: Session, user_id: uuid.UUID) -> Profile | None:
    return db.scalar(sa.select(Profile).where(Profile.user_id == user_id))


def user_today(db: Session, user: User) -> dt.date:
    """Today's date where the user lives (their profile timezone)."""
    profile = get_profile(db, user.id)
    return _today(profile.timezone if profile else DEFAULT_TIMEZONE)


def latest_weight(db: Session, user_id: uuid.UUID) -> WeightLog | None:
    return db.scalar(
        sa.select(WeightLog)
        .where(WeightLog.user_id == user_id)
        .order_by(WeightLog.date.desc())
        .limit(1)
    )


def save_profile(db: Session, user: User, data: ProfileIn) -> tuple[Profile, WeightLog]:
    """Upsert the profile and record today's weight (in the user's timezone).

    The age check happens before any write, so a rejected under-18 saves nothing.
    """
    today = _today(data.timezone)
    if not body_metrics.is_adult(data.birth_date, today):
        raise UnderageError

    profile = get_profile(db, user.id) or Profile(user_id=user.id)
    profile.birth_date = data.birth_date
    profile.sex = data.sex
    profile.height_cm = Decimal(str(round(data.height_cm, 1)))
    profile.goal = data.goal
    profile.experience = data.experience
    profile.days_per_week = data.days_per_week
    profile.session_minutes = data.session_minutes
    profile.equipment = [e.value for e in data.equipment]
    profile.activity_level = data.activity_level
    profile.timezone = data.timezone
    profile.language = data.language
    db.add(profile)

    weight = db.scalar(
        sa.select(WeightLog).where(
            WeightLog.user_id == user.id, WeightLog.date == today
        )
    ) or WeightLog(user_id=user.id, date=today)
    weight.weight_kg = Decimal(str(round(data.weight_kg, 2)))
    db.add(weight)

    db.commit()
    return profile, weight


# --- limitations and diet -------------------------------------------------------------


def list_limitations(db: Session, user_id: uuid.UUID) -> list[Limitation]:
    return list(
        db.scalars(
            sa.select(Limitation)
            .where(Limitation.user_id == user_id)
            .order_by(Limitation.created_at, Limitation.id)
        )
    )


def replace_limitations(
    db: Session, user: User, items: list[LimitationIn]
) -> list[Limitation]:
    db.execute(sa.delete(Limitation).where(Limitation.user_id == user.id))
    rows = [
        Limitation(
            user_id=user.id,
            body_area=item.body_area.value,
            description=item.description,
            severity=item.severity,
            active=item.active,
        )
        for item in items
    ]
    db.add_all(rows)

    profile = get_profile(db, user.id) or Profile(user_id=user.id)
    profile.limitations_reviewed_at = _now()
    db.add(profile)
    db.commit()
    return list_limitations(db, user.id)


def get_diet(db: Session, user_id: uuid.UUID) -> DietaryPreference | None:
    return db.scalar(
        sa.select(DietaryPreference).where(DietaryPreference.user_id == user_id)
    )


def save_diet(db: Session, user: User, data: DietIn) -> DietaryPreference:
    row = get_diet(db, user.id) or DietaryPreference(user_id=user.id)
    row.allergies = data.allergies
    row.intolerances = data.intolerances
    row.dislikes = data.dislikes
    row.cuisine_hints = data.cuisine_hints
    row.meals_per_day = data.meals_per_day
    row.budget_level = data.budget_level
    db.add(row)
    db.commit()
    return row


# --- screening ------------------------------------------------------------------------


def latest_screening(db: Session, user_id: uuid.UUID) -> HealthScreening | None:
    return db.scalar(
        sa.select(HealthScreening)
        .where(HealthScreening.user_id == user_id)
        .order_by(HealthScreening.screened_at.desc(), HealthScreening.created_at.desc())
        .limit(1)
    )


def answers_of(screening: HealthScreening) -> ScreeningAnswers:
    a: dict[str, Any] = screening.answers
    return ScreeningAnswers(
        chest_pain=bool(a.get("chest_pain")),
        heart_condition=bool(a.get("heart_condition")),
        blood_pressure_uncontrolled=bool(a.get("blood_pressure_uncontrolled")),
        fainting_or_dizziness=bool(a.get("fainting_or_dizziness")),
        pregnant=bool(a.get("pregnant")),
        recent_surgery=bool(a.get("recent_surgery")),
        medication_affecting_exercise=bool(a.get("medication_affecting_exercise")),
        joint_or_bone_problem=bool(a.get("joint_or_bone_problem")),
        eating_concern=bool(a.get("eating_concern")),
    )


def submit_screening(db: Session, user: User, data: ScreeningIn) -> HealthScreening:
    """Append a new screening. Clearance carries over only if the risk level is unchanged."""
    answers = data.model_dump()  # includes free_text: stored as data, never logged
    result = evaluate_screening(
        ScreeningAnswers(**{k: v for k, v in answers.items() if k != "free_text"})
    )
    previous = latest_screening(db, user.id)
    keeps_clearance = (
        previous is not None
        and previous.clearance_confirmed
        and previous.risk_level == result.risk_level
        and result.risk_level is not RiskLevel.OK
    )
    row = HealthScreening(
        user_id=user.id,
        answers=answers,
        risk_level=result.risk_level,
        screened_at=_now(),
        clearance_confirmed=keeps_clearance,
    )
    db.add(row)
    audit.record(db, "onboarding.screening_submitted", user.id)
    db.commit()
    return row


def confirm_clearance(db: Session, user: User) -> HealthScreening:
    screening = latest_screening(db, user.id)
    if screening is None:
        raise NoScreeningError
    if screening.risk_level is RiskLevel.OK:
        raise ClearanceNotNeededError
    screening.clearance_confirmed = True
    audit.record(db, "onboarding.clearance_confirmed", user.id)
    db.commit()
    return screening


# --- derived state (always computed live, never stored) -------------------------------


@dataclass(frozen=True)
class OnboardingState:
    profile_complete: bool
    limitations_done: bool
    diet_done: bool
    health_consent: bool
    screening: HealthScreening | None
    result: ScreeningResult | None
    disordered_eating: bool
    access: PlanAccess


def _profile_complete(profile: Profile | None, has_weight: bool) -> bool:
    return (
        profile is not None
        and has_weight
        and all(
            value is not None
            for value in (
                profile.birth_date,
                profile.sex,
                profile.height_cm,
                profile.goal,
                profile.experience,
                profile.days_per_week,
                profile.session_minutes,
                profile.activity_level,
            )
        )
        and bool(profile.equipment)
    )


def onboarding_state(db: Session, user: User) -> OnboardingState:
    profile = get_profile(db, user.id)
    weight = latest_weight(db, user.id)
    screening = latest_screening(db, user.id)
    result = evaluate_screening(answers_of(screening)) if screening else None

    bmi_value: float | None = None
    if weight is not None and profile is not None and profile.height_cm:
        bmi_value = body_metrics.bmi(float(weight.weight_kg), float(profile.height_cm))
    eating = disordered_eating_flag(
        eating_concern=answers_of(screening).eating_concern if screening else False,
        bmi=bmi_value,
        goal=profile.goal if profile else None,
    )

    complete = _profile_complete(profile, weight is not None)
    access = can_receive_plan(
        profile_complete=complete,
        screening_risk=screening.risk_level if screening else None,
        clearance_confirmed=bool(screening and screening.clearance_confirmed),
        disordered_eating=eating,
    )
    return OnboardingState(
        profile_complete=complete,
        limitations_done=bool(profile and profile.limitations_reviewed_at),
        diet_done=get_diet(db, user.id) is not None,
        health_consent=has_consent(db, user.id, ConsentType.HEALTH_DATA),
        screening=screening,
        result=result,
        disordered_eating=eating,
        access=access,
    )
