import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.ai.client import AiClient, get_ai_client
from app.api.deps import DbSession, PlanUser, not_in_future, require_consent
from app.api.nutrition import meal_out
from app.models import MealLog, User
from app.models.enums import ConsentType
from app.schemas.food import (
    EstimateIn,
    FoodDraftOut,
    FoodFlagOut,
    FoodLogIn,
    PersonalFoodIn,
    PersonalFoodOut,
)
from app.schemas.nutrition import MealOut
from app.services import files, onboarding
from app.services import food as svc
from app.services.storage import Storage, get_storage

router = APIRouter(prefix="/food", tags=["food"])

AiDep = Annotated[AiClient, Depends(get_ai_client)]
StorageDep = Annotated[Storage, Depends(get_storage)]
HealthConsent = Annotated[User, Depends(require_consent(ConsentType.HEALTH_DATA))]
AiConsent = Annotated[User, Depends(require_consent(ConsentType.AI_PROCESSING))]


def _draft_out(meal: MealLog) -> FoodDraftOut:
    return FoodDraftOut(
        **meal_out(meal).model_dump(),
        questions=svc.questions_of(meal),
        flags=[
            FoodFlagOut(item=index, field=f.field, code=f.code, message=f.message)
            for index, f in svc.flags_of(meal)
        ],
    )


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")


def _owned(db: DbSession, user: User, meal_id: uuid.UUID) -> MealLog:
    try:
        return svc.get_meal(db, user, meal_id)
    except svc.MealNotFoundError:
        raise _not_found() from None


@router.post(
    "/estimate", response_model=FoodDraftOut, status_code=status.HTTP_201_CREATED
)
def estimate(
    body: EstimateIn,
    db: DbSession,
    _health: HealthConsent,
    _ai: AiConsent,
    user: PlanUser,
    ai: AiDep,
    storage: StorageDep,
) -> FoodDraftOut:
    """Estimate a meal from a photo and/or a description. Returns a DRAFT with a calorie
    range: it counts only after you check it with PUT /food/logs/{id}."""
    date = not_in_future(body.date or onboarding.user_today(db, user), db, user)
    try:
        meal = svc.estimate(
            db,
            storage,
            ai,
            user,
            file_id=body.file_id,
            text=body.text,
            date=date,
            slot=body.slot,
        )
    except files.StoredFileNotFoundError:
        raise _not_found() from None
    except svc.WrongFileKindError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "wrong_file_kind",
                "message": "Upload the photo as a food photo first.",
            },
        ) from None
    except svc.NoFoodError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "no_food",
                "message": "No food was found in this photo. Try another one.",
            },
        ) from None
    except svc.UnclearError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "unclear",
                "message": "The meal couldn't be made out. Take a clearer photo or "
                "describe it.",
                "questions": exc.questions,
            },
        ) from None
    return _draft_out(meal)


@router.put("/logs/{meal_id}", response_model=MealOut)
def save(meal_id: uuid.UUID, body: FoodLogIn, db: DbSession, user: PlanUser) -> MealOut:
    """Save your checked version of a meal (from now on it counts)."""
    meal = _owned(db, user, meal_id)
    not_in_future(body.date, db, user)
    return meal_out(svc.save(db, meal, body))


@router.post("/logs", response_model=MealOut, status_code=status.HTTP_201_CREATED)
def add(body: FoodLogIn, db: DbSession, user: PlanUser) -> MealOut:
    """Log a meal you type in yourself. It counts at once."""
    not_in_future(body.date, db, user)
    return meal_out(svc.add_manual(db, user, body))


@router.post(
    "/personal", response_model=PersonalFoodOut, status_code=status.HTTP_201_CREATED
)
def save_personal(
    body: PersonalFoodIn, db: DbSession, user: PlanUser
) -> PersonalFoodOut:
    """Keep a checked meal as one of your own foods, to log it again quickly."""
    meal = _owned(db, user, body.meal_log_id)
    try:
        option = svc.save_personal(db, user, meal, body.name)
    except svc.NotConfirmedError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Check and save the meal before keeping it as your own food.",
        ) from None
    return PersonalFoodOut(**svc.personal_out(option))


@router.get("/personal", response_model=list[PersonalFoodOut])
def personal(db: DbSession, user: PlanUser) -> list[PersonalFoodOut]:
    return [
        PersonalFoodOut(**svc.personal_out(o)) for o in svc.personal_foods(db, user)
    ]
