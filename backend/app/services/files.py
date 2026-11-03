"""Private uploads: checked by content, cleaned and resized, stored under the user's prefix.

Every stored image is re-encoded as a JPEG, which drops all metadata (EXIF, GPS, camera,
ICC profile, comments), after being turned upright and fitted inside MAX_SIDE. A small
thumbnail sits next to it. Bytes never touch the database or the logs.
"""

import datetime as dt
import hashlib
import io
import uuid
from dataclasses import dataclass

import pillow_heif
import pypdfium2 as pdfium
import sqlalchemy as sa
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.ai.types import AiImage
from app.ai.untrusted import MAX_INPUT_PIXELS, prepare_image
from app.models import InbodyScan, MealLog, StoredFile
from app.models.enums import FileKind
from app.rules.uploads import (
    MAX_SIDE,
    SNIFF_BYTES,
    THUMBNAIL_SIDE,
    SniffedType,
    is_allowed,
    size_limit,
    sniff_type,
)
from app.services.storage import Storage, user_prefix

pillow_heif.register_heif_opener()

JPEG_QUALITY = 88
THUMBNAIL_QUALITY = 80
_PIL_FORMATS = {
    SniffedType.JPEG: "JPEG",
    SniffedType.PNG: "PNG",
    SniffedType.WEBP: "WEBP",
    SniffedType.HEIC: "HEIF",
}


class UploadError(Exception):
    pass


class UnsupportedTypeError(UploadError):
    """Not a file type this kind of upload accepts."""


class InvalidImageError(UploadError):
    """Looks like an accepted type but cannot be decoded safely."""


class TooLargeError(UploadError):
    pass


class StoredFileNotFoundError(Exception):
    pass


class NotForAiError(Exception):
    """Body progress photos are never sent to an AI provider."""


@dataclass(frozen=True)
class Processed:
    original: bytes
    thumbnail: bytes


def _pdf_first_page(data: bytes) -> Image.Image:
    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise InvalidImageError from exc
    try:
        if len(doc) == 0:
            raise InvalidImageError
        page = doc[0]
        try:
            width, height = page.get_size()
            if width <= 0 or height <= 0:
                raise InvalidImageError
            # Render straight at the stored size: sharp text, no huge bitmap first.
            scale = min(MAX_SIDE / max(width, height), 8.0)
            return page.render(scale=scale).to_pil()
        finally:
            page.close()
    finally:
        doc.close()


def _decode(data: bytes, sniffed: SniffedType) -> Image.Image:
    if sniffed is SniffedType.PDF:
        return _pdf_first_page(data)
    try:
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
            fmt = probe.format
        if fmt != _PIL_FORMATS[sniffed] or width * height > MAX_INPUT_PIXELS:
            raise InvalidImageError
        img = Image.open(io.BytesIO(data))
        img.load()
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        SyntaxError,
        ValueError,
    ) as exc:
        raise InvalidImageError from exc
    return img


def _flatten(img: Image.Image) -> Image.Image:
    """RGB with any transparency laid on white (JPEG has no alpha)."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, "white")
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return img.convert("RGB")


def _jpeg(img: Image.Image, quality: int) -> bytes:
    out = io.BytesIO()
    # No exif=, icc_profile= or comment=: the new file carries no metadata at all.
    img.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()


def process_upload(data: bytes, kind: FileKind) -> Processed:
    """Check and clean an upload. CPU only: no database, storage or network."""
    sniffed = sniff_type(data[:SNIFF_BYTES])
    if sniffed is None or not is_allowed(kind, sniffed):
        raise UnsupportedTypeError
    if len(data) > size_limit(sniffed):
        raise TooLargeError
    img = _decode(data, sniffed)
    try:
        upright = _flatten(ImageOps.exif_transpose(img))
        upright.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        thumb = upright.copy()
        thumb.thumbnail((THUMBNAIL_SIDE, THUMBNAIL_SIDE), Image.Resampling.LANCZOS)
        return Processed(_jpeg(upright, JPEG_QUALITY), _jpeg(thumb, THUMBNAIL_QUALITY))
    except (OSError, ValueError) as exc:
        raise InvalidImageError from exc
    finally:
        img.close()


def _as_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _remove_objects(storage: Storage, keys: list[str | None]) -> None:
    for key in keys:
        if key:
            storage.delete(key)


def store_upload(
    db: Session,
    storage: Storage,
    user_id: uuid.UUID | str,
    kind: FileKind,
    data: bytes,
) -> StoredFile:
    """Process, store both objects, then record the row.

    The caller has already ended its transaction, so no database connection is held
    while the image is processed and sent to storage.
    """
    processed = process_upload(data, kind)
    owner = _as_uuid(user_id)
    base = f"{user_prefix(owner)}{kind.value}/{uuid.uuid4()}"
    key, thumbnail_key = f"{base}.jpg", f"{base}_thumb.jpg"
    storage.put_bytes(key, processed.original, "image/jpeg")
    try:
        storage.put_bytes(thumbnail_key, processed.thumbnail, "image/jpeg")
        row = StoredFile(
            user_id=owner,
            storage_key=key,
            thumbnail_key=thumbnail_key,
            content_type="image/jpeg",
            size_bytes=len(processed.original),
            sha256=hashlib.sha256(processed.original).hexdigest(),
            kind=kind,
        )
        db.add(row)
        db.commit()
    except Exception:
        db.rollback()
        _remove_objects(storage, [key, thumbnail_key])  # no orphaned health data
        raise
    return row


def get_owned(
    db: Session, user_id: uuid.UUID | str, file_id: uuid.UUID | str
) -> StoredFile:
    """The user's file, unless it is deleted or belongs to someone else."""
    try:
        wanted = _as_uuid(file_id)
    except ValueError:
        raise StoredFileNotFoundError from None
    row = db.scalar(
        sa.select(StoredFile).where(
            StoredFile.id == wanted,
            StoredFile.user_id == _as_uuid(user_id),
            StoredFile.deleted_at.is_(None),
        )
    )
    if row is None:
        raise StoredFileNotFoundError
    return row


def signed_urls(
    storage: Storage, row: StoredFile, ttl_s: int
) -> tuple[str, str | None]:
    thumb = storage.presign_get(row.thumbnail_key, ttl_s) if row.thumbnail_key else None
    return storage.presign_get(row.storage_key, ttl_s), thumb


def _purge(db: Session, storage: Storage, row: StoredFile) -> None:
    _remove_objects(storage, [row.storage_key, row.thumbnail_key])
    db.delete(row)  # progress_photos rows go with it (ON DELETE CASCADE)
    db.commit()


def delete_file(db: Session, storage: Storage, row: StoredFile) -> None:
    """Hide the file and detach it from meals and scans first, then remove the objects
    and the row. If storage fails, the hidden row is finished by purge_deleted."""
    row.deleted_at = dt.datetime.now(dt.UTC)
    for model in (MealLog, InbodyScan):
        db.execute(sa.update(model).where(model.file_id == row.id).values(file_id=None))
    db.commit()  # also releases the connection before the storage calls
    _purge(db, storage, row)


def purge_deleted(db: Session, storage: Storage, limit: int = 100) -> int:
    """Finish deletions that storage interrupted. Returns how many were completed."""
    rows = db.scalars(
        sa.select(StoredFile)
        .where(StoredFile.deleted_at.is_not(None))
        .order_by(StoredFile.deleted_at)
        .limit(limit)
    ).all()
    for row in rows:
        _purge(db, storage, row)
    return len(rows)


def load_for_ai(
    db: Session, storage: Storage, user_id: uuid.UUID | str, file_id: uuid.UUID | str
) -> AiImage:
    """A stored image, size-limited and re-encoded, ready for an AI call.

    Body photos are refused: they never leave our storage for an AI provider.
    """
    row = get_owned(db, user_id, file_id)
    if row.kind is FileKind.BODY_PHOTO:
        raise NotForAiError
    key = row.storage_key
    db.commit()  # release the connection before the storage read
    data = storage.get_bytes(key)
    if data is None:
        raise StoredFileNotFoundError
    return prepare_image(data)
