from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.deps import CurrentUser, DbSession
from app.core.rate_limit import auth_limit
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect email or password",
    headers={"WWW-Authenticate": "Bearer"},
)


def _tokens(pair: auth_service.TokenPair) -> TokenResponse:
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
@auth_limit
def register(request: Request, body: RegisterRequest, db: DbSession) -> UserOut:
    try:
        user = auth_service.register_user(
            db, body.email, body.password, body.display_name
        )
    except auth_service.EmailTakenError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        ) from None
    return UserOut.model_validate(user)


@router.post("/login", response_model=TokenResponse)
@auth_limit
def login(request: Request, body: LoginRequest, db: DbSession) -> TokenResponse:
    try:
        return _tokens(auth_service.login(db, body.email, body.password))
    except auth_service.InvalidCredentialsError:
        raise _INVALID_CREDENTIALS from None


@router.post("/refresh", response_model=TokenResponse)
@auth_limit
def refresh(request: Request, body: RefreshRequest, db: DbSession) -> TokenResponse:
    try:
        return _tokens(auth_service.rotate_refresh_token(db, body.refresh_token))
    except auth_service.InvalidRefreshTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@auth_limit
def logout(request: Request, body: RefreshRequest, db: DbSession) -> Response:
    auth_service.logout(db, body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
