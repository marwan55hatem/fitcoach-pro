import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings

_password_hash = PasswordHash.recommended()  # Argon2id
_ALGORITHM = "HS256"
_ACCESS = "access"

# Verified when the email is unknown so login timing does not reveal which emails exist.
_DUMMY_HASH = _password_hash.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, hashed: str | None) -> tuple[bool, str | None]:
    """Return (is_valid, new_hash). new_hash is set when the stored hash should be upgraded."""
    if hashed is None:
        _password_hash.verify(password, _DUMMY_HASH)
        return False, None
    return _password_hash.verify_and_update(password, hashed)


def create_access_token(user_id: uuid.UUID) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "type": _ACCESS,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> uuid.UUID:
    """Return the user id, or raise jwt.InvalidTokenError / ValueError."""
    claims = jwt.decode(
        token,
        get_settings().jwt_secret,
        algorithms=[_ALGORITHM],
        options={"require": ["exp", "sub", "type"]},
    )
    if claims["type"] != _ACCESS:
        raise jwt.InvalidTokenError("wrong token type")
    return uuid.UUID(claims["sub"])


def new_refresh_token() -> tuple[str, str]:
    """Return (raw_token, sha256_hash). Only the hash is stored."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
