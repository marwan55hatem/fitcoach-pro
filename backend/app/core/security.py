from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings

_password_hash = PasswordHash.recommended()
_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _password_hash.verify(password, hashed)


def create_access_token(subject: str, expires_minutes: int = 30) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=expires_minutes)
    return jwt.encode(
        {"sub": subject, "exp": expire}, get_settings().jwt_secret, algorithm=_ALGORITHM
    )


def decode_access_token(token: str) -> dict[str, object]:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=[_ALGORITHM])
