import uuid
from collections.abc import Callable
from typing import Annotated, TypeVar

import jwt
import sqlalchemy as sa
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import User
from app.models.enums import ConsentType
from app.services.consents import has_consent

DbSession = Annotated[Session, Depends(get_db)]

_bearer = HTTPBearer(auto_error=False)


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: DbSession,
) -> User:
    if credentials is None:
        raise _unauthorized()
    try:
        user_id = decode_access_token(credentials.credentials)
    except (jwt.InvalidTokenError, ValueError):
        raise _unauthorized("Invalid or expired token") from None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("Invalid or expired token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_consent(consent_type: ConsentType) -> Callable[..., User]:
    """Dependency factory: 403 unless the user has an active consent of this type.

    Use on every photo, InBody and AI endpoint, e.g.
    `user: Annotated[User, Depends(require_consent(ConsentType.BODY_PHOTOS))]`.
    """

    def dependency(user: CurrentUser, db: DbSession) -> User:
        if not has_consent(db, user.id, consent_type):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "consent_required",
                    "consent": consent_type.value,
                    "message": (
                        f"Your consent for '{consent_type.value}' is required for this "
                        "feature. Grant it with PUT /consents."
                    ),
                },
            )
        return user

    return dependency


ModelT = TypeVar("ModelT")


def get_owned_or_404(
    db: Session, model: type[ModelT], object_id: uuid.UUID, user: User
) -> ModelT:
    """Fetch a user-owned row by id. Other users' rows are indistinguishable from missing."""
    row = db.scalar(
        sa.select(model).where(
            model.id == object_id,  # type: ignore[attr-defined]
            model.user_id == user.id,  # type: ignore[attr-defined]
        )
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return row
