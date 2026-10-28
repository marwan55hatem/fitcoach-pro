import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OwnedBase


class RefreshToken(OwnedBase, Base):
    """Opaque refresh token (stored hashed) with rotation.

    Each use revokes the token and issues a new one in the same family. Presenting an
    already-revoked token means it leaked, so the whole family is revoked.
    """

    __tablename__ = "refresh_tokens"

    token_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    expires_at: Mapped[dt.datetime]
    revoked_at: Mapped[dt.datetime | None]
    replaced_by_id: Mapped[uuid.UUID | None]
