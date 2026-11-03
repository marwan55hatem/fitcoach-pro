import datetime as dt

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, OwnedBase, enum_col
from app.models.enums import FileKind


class StoredFile(OwnedBase, Base):
    """Metadata for an object in private storage. Bytes never live in the DB.

    Soft-deleted (deleted_at) first so the object can be purged from storage,
    then the row can be removed.
    """

    __tablename__ = "files"

    storage_key: Mapped[str] = mapped_column(sa.String(512), unique=True)
    # A small preview (400 px) next to the original; files from before it may lack one.
    thumbnail_key: Mapped[str | None] = mapped_column(sa.String(512), unique=True)
    content_type: Mapped[str] = mapped_column(sa.String(100))
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger)
    sha256: Mapped[str] = mapped_column(sa.String(64))
    kind: Mapped[FileKind] = mapped_column(enum_col(FileKind))
    deleted_at: Mapped[dt.datetime | None]

    __table_args__ = (
        sa.Index("ix_files_user_id_kind", "user_id", "kind"),
        sa.CheckConstraint("size_bytes >= 0", name="size_non_negative"),
        sa.CheckConstraint("char_length(sha256) = 64", name="sha256_length"),
    )
