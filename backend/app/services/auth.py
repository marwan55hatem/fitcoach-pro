import datetime as dt
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.models import RefreshToken, User
from app.services import audit


class EmailTakenError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class InvalidRefreshTokenError(Exception):
    pass


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def register_user(
    db: Session, email: str, password: str, display_name: str | None
) -> User:
    user = User(
        email=email.lower(),
        password_hash=hash_password(password),
        display_name=display_name,
    )
    try:
        with db.begin_nested():
            db.add(user)
            db.flush()
    except IntegrityError as exc:
        raise EmailTakenError from exc
    audit.record(db, "auth.register", user.id)
    db.commit()
    return user


def _issue_tokens(
    db: Session, user_id: uuid.UUID, family_id: uuid.UUID | None = None
) -> tuple[TokenPair, RefreshToken]:
    settings = get_settings()
    raw, token_hash = new_refresh_token()
    row = RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        family_id=family_id or uuid.uuid4(),
        expires_at=_now() + dt.timedelta(days=settings.refresh_token_days),
    )
    db.add(row)
    db.flush()
    pair = TokenPair(
        access_token=create_access_token(user_id),
        refresh_token=raw,
        expires_in=settings.access_token_minutes * 60,
    )
    return pair, row


def login(db: Session, email: str, password: str) -> TokenPair:
    user = db.scalar(sa.select(User).where(User.email == email.lower()))
    valid, new_hash = verify_password(password, user.password_hash if user else None)
    if user is None or not valid or not user.is_active:
        raise InvalidCredentialsError
    if new_hash:
        user.password_hash = new_hash
    user.last_login_at = _now()
    pair, _ = _issue_tokens(db, user.id)
    db.commit()
    return pair


def _revoke_family(db: Session, family_id: uuid.UUID) -> None:
    db.execute(
        sa.update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


def rotate_refresh_token(db: Session, raw_token: str) -> TokenPair:
    row = db.scalar(
        sa.select(RefreshToken)
        .where(RefreshToken.token_hash == hash_refresh_token(raw_token))
        .with_for_update()
    )
    if row is None:
        raise InvalidRefreshTokenError
    if row.revoked_at is not None:
        # A used token came back: assume it leaked and kill every token in its family.
        _revoke_family(db, row.family_id)
        audit.record(
            db, "auth.refresh_reuse_detected", row.user_id, entity_type="refresh_token"
        )
        db.commit()
        raise InvalidRefreshTokenError
    if row.expires_at <= _now():
        raise InvalidRefreshTokenError
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise InvalidRefreshTokenError

    row.revoked_at = _now()
    pair, new_row = _issue_tokens(db, row.user_id, row.family_id)
    row.replaced_by_id = new_row.id
    db.commit()
    return pair


def logout(db: Session, raw_token: str) -> None:
    """Revoke the token's whole family. Unknown tokens are ignored (idempotent)."""
    row = db.scalar(
        sa.select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(raw_token)
        )
    )
    if row is not None:
        _revoke_family(db, row.family_id)
        db.commit()
