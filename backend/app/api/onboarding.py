from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import CurrentUser, DbSession, require_consent
from app.models import User
from app.models.enums import ConsentType, RiskLevel
from app.rules.screening import REASON_MESSAGES, PlanAccess
from app.schemas.onboarding import (
    ClearanceIn,
    DietIn,
    DietOut,
    LimitationOut,
    LimitationsIn,
    LimitationsOut,
    OnboardingStatus,
    PlanAccessOut,
    ProfileIn,
    ProfileOut,
    Reason,
    ScreeningIn,
    ScreeningOut,
    StepStatus,
)
from app.services import onboarding as svc

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

# Profile, injuries, diet and screening answers are health data: consent comes first.
HealthUser = Annotated[User, Depends(require_consent(ConsentType.HEALTH_DATA))]


def _reasons(codes: tuple[str, ...]) -> list[Reason]:
    return [Reason(code=str(c), message=REASON_MESSAGES[str(c)]) for c in codes]


def _access_out(access: PlanAccess) -> PlanAccessOut:
    return PlanAccessOut(
        allowed=access.allowed,
        numeric_diet_targets=access.numeric_diet_targets,
        blocked_reasons=_reasons(access.blocked_reasons),
    )


def _screening_out(state: svc.OnboardingState) -> ScreeningOut:
    screening, result = state.screening, state.result
    assert screening is not None and result is not None
    return ScreeningOut(
        id=screening.id,
        screened_at=screening.screened_at,
        risk_level=screening.risk_level,
        reasons=_reasons(result.reasons),
        notes=_reasons(result.notes),
        disordered_eating_flag=state.disordered_eating,
        clearance_required=screening.risk_level is not RiskLevel.OK,
        clearance_confirmed=screening.clearance_confirmed,
    )


def _profile_out(db: DbSession, user: User) -> ProfileOut:
    profile = svc.get_profile(db, user.id)
    weight = svc.latest_weight(db, user.id)
    return ProfileOut(
        birth_date=profile.birth_date if profile else None,
        sex=profile.sex if profile else None,
        height_cm=float(profile.height_cm) if profile and profile.height_cm else None,
        weight_kg=float(weight.weight_kg) if weight else None,
        goal=profile.goal if profile else None,
        experience=profile.experience if profile else None,
        days_per_week=profile.days_per_week if profile else None,
        session_minutes=profile.session_minutes if profile else None,
        equipment=list(profile.equipment) if profile else [],
        activity_level=profile.activity_level if profile else None,
        timezone=profile.timezone if profile else "Africa/Cairo",
        language=profile.language if profile else "en",
    )


@router.put("/profile", response_model=ProfileOut)
def put_profile(body: ProfileIn, db: DbSession, user: HealthUser) -> ProfileOut:
    try:
        svc.save_profile(db, user, body)
    except svc.UnderageError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "underage",
                "message": "You must be at least 18 years old to use FitCoach.",
            },
        ) from None
    return _profile_out(db, user)


@router.get("/profile", response_model=ProfileOut)
def get_profile(db: DbSession, user: HealthUser) -> ProfileOut:
    return _profile_out(db, user)


@router.put("/limitations", response_model=LimitationsOut)
def put_limitations(
    body: LimitationsIn, db: DbSession, user: HealthUser
) -> LimitationsOut:
    rows = svc.replace_limitations(db, user, body.limitations)
    return LimitationsOut(
        reviewed=True,
        limitations=[
            LimitationOut.model_validate(r, from_attributes=True) for r in rows
        ],
    )


@router.get("/limitations", response_model=LimitationsOut)
def get_limitations(db: DbSession, user: HealthUser) -> LimitationsOut:
    profile = svc.get_profile(db, user.id)
    rows = svc.list_limitations(db, user.id)
    return LimitationsOut(
        reviewed=bool(profile and profile.limitations_reviewed_at),
        limitations=[
            LimitationOut.model_validate(r, from_attributes=True) for r in rows
        ],
    )


@router.put("/diet", response_model=DietOut)
def put_diet(body: DietIn, db: DbSession, user: HealthUser) -> DietOut:
    return DietOut.model_validate(svc.save_diet(db, user, body), from_attributes=True)


@router.get("/diet", response_model=DietOut | None)
def get_diet(db: DbSession, user: HealthUser) -> DietOut | None:
    row = svc.get_diet(db, user.id)
    return DietOut.model_validate(row, from_attributes=True) if row else None


@router.post(
    "/screening", response_model=ScreeningOut, status_code=status.HTTP_201_CREATED
)
def post_screening(body: ScreeningIn, db: DbSession, user: HealthUser) -> ScreeningOut:
    svc.submit_screening(db, user, body)
    return _screening_out(svc.onboarding_state(db, user))


@router.get("/screening", response_model=ScreeningOut | None)
def get_screening(db: DbSession, user: HealthUser) -> ScreeningOut | None:
    state = svc.onboarding_state(db, user)
    return _screening_out(state) if state.screening else None


@router.post("/clearance", response_model=ScreeningOut)
def post_clearance(body: ClearanceIn, db: DbSession, user: HealthUser) -> ScreeningOut:
    try:
        svc.confirm_clearance(db, user)
    except svc.NoScreeningError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Complete the health screening first.",
        ) from None
    except svc.ClearanceNotNeededError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your screening does not require medical clearance.",
        ) from None
    return _screening_out(svc.onboarding_state(db, user))


@router.get("/status", response_model=OnboardingStatus)
def get_status(db: DbSession, user: CurrentUser) -> OnboardingStatus:
    state = svc.onboarding_state(db, user)
    steps = [
        StepStatus(key="consent", completed=state.health_consent, required=True),
        StepStatus(key="profile", completed=state.profile_complete, required=True),
        StepStatus(key="limitations", completed=state.limitations_done, required=True),
        StepStatus(key="diet", completed=state.diet_done, required=True),
        StepStatus(
            key="screening", completed=state.screening is not None, required=True
        ),
    ]
    clearance_required = bool(
        state.screening and state.screening.risk_level is not RiskLevel.OK
    )
    clearance_confirmed = bool(state.screening and state.screening.clearance_confirmed)
    if clearance_required:
        steps.append(
            StepStatus(key="clearance", completed=clearance_confirmed, required=True)
        )
    missing = [s.key for s in steps if s.required and not s.completed]
    return OnboardingStatus(
        steps=steps,
        missing=missing,
        onboarding_complete=not missing,
        risk_level=state.screening.risk_level if state.screening else None,
        clearance_required=clearance_required,
        clearance_confirmed=clearance_confirmed,
        disordered_eating_flag=state.disordered_eating,
        plan_access=_access_out(state.access),
    )
