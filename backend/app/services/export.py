"""Data export (GDPR-style portability): a ZIP with every row the user owns plus their files."""

import datetime as dt
import json
import mimetypes
import tempfile
import uuid
import zipfile
from decimal import Decimal
from typing import IO, Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import StoredFile, User
from app.services.storage import Storage

# Never exported: credentials / session state.
_EXCLUDED_TABLES = {"refresh_tokens"}
_EXCLUDED_COLUMNS = {"users": {"password_hash"}}
_SPOOL_BYTES = 32 * 1024 * 1024


def _json_default(value: Any) -> Any:
    if isinstance(value, uuid.UUID | Decimal):
        return str(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    raise TypeError(f"not serializable: {type(value)}")


def _user_tables() -> list[sa.Table]:
    return [
        t
        for t in Base.metadata.sorted_tables
        if t.name not in _EXCLUDED_TABLES and (t.name == "users" or "user_id" in t.c)
    ]


def _rows(db: Session, table: sa.Table, user_id: uuid.UUID) -> list[dict[str, Any]]:
    owner = table.c.id if table.name == "users" else table.c.user_id
    hidden = _EXCLUDED_COLUMNS.get(table.name, set())
    result = db.execute(sa.select(table).where(owner == user_id)).mappings()
    return [{k: v for k, v in row.items() if k not in hidden} for row in result]


def build_export(db: Session, user: User, storage: Storage) -> IO[bytes]:
    """Return a seekable temp file holding the ZIP, positioned at 0. Caller closes it."""
    out = tempfile.SpooledTemporaryFile(max_size=_SPOOL_BYTES)  # noqa: SIM115
    counts: dict[str, int] = {}
    missing_files: list[str] = []
    exported_files = 0

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for table in _user_tables():
            rows = _rows(db, table, user.id)
            counts[table.name] = len(rows)
            archive.writestr(
                f"data/{table.name}.json",
                json.dumps(rows, default=_json_default, indent=2),
            )

        files = db.scalars(
            sa.select(StoredFile).where(
                StoredFile.user_id == user.id, StoredFile.deleted_at.is_(None)
            )
        )
        for f in files:
            content = storage.get_bytes(f.storage_key)
            if content is None:
                missing_files.append(str(f.id))
                continue
            ext = mimetypes.guess_extension(f.content_type) or ""
            archive.writestr(f"files/{f.id}{ext}", content)
            exported_files += 1

        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "exported_at": dt.datetime.now(dt.UTC).isoformat(),
                    "row_counts": counts,
                    "files_exported": exported_files,
                    "files_missing_from_storage": missing_files,
                    "note": "files/<id>.<ext> matches the id in data/files.json",
                },
                indent=2,
            ),
        )

    out.seek(0)
    return out
