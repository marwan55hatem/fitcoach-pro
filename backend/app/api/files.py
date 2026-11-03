import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.api.deps import CurrentUser, DbSession, consent_required_error
from app.core.rate_limit import upload_limit
from app.models import StoredFile, User
from app.models.enums import FileKind
from app.rules.uploads import REQUIRED_CONSENT, upload_cap
from app.services import files as svc
from app.services.consents import has_consent
from app.services.storage import Storage, get_storage

router = APIRouter(prefix="/files", tags=["files"])

StorageDep = Annotated[Storage, Depends(get_storage)]
URL_TTL_S = 300  # signed links live five minutes


class FileOut(BaseModel):
    id: uuid.UUID
    kind: FileKind
    content_type: str
    size_bytes: int
    sha256: str
    created_at: dt.datetime


class FileLinksOut(BaseModel):
    url: str
    thumbnail_url: str | None
    expires_in: int  # seconds


def _out(row: StoredFile) -> FileOut:
    return FileOut(
        id=row.id,
        kind=row.kind,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        created_at=row.created_at,
    )


def _error(code: int, key: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": key, "message": message})


def _too_large() -> HTTPException:
    return _error(
        status.HTTP_413_CONTENT_TOO_LARGE,
        "too_large",
        "The file is too big: up to 12 MB for photos and 10 MB for PDFs.",
    )


def _uploader(kind: FileKind, db: DbSession, user: CurrentUser) -> User:
    """Each kind of file needs its own consent (body photos need body_photos, ...)."""
    needed = REQUIRED_CONSENT[kind]
    if not has_consent(db, user.id, needed):
        raise consent_required_error(needed)
    db.commit()  # release the connection: the upload itself can take a while
    return user


Uploader = Annotated[User, Depends(_uploader)]


async def _read_capped(request: Request, cap: int) -> bytes:
    """Read the body, stopping as soon as it passes the cap (not after it all arrived)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > cap:
        raise _too_large()
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > cap:
            raise _too_large()
    return bytes(body)


@router.post("", response_model=FileOut, status_code=status.HTTP_201_CREATED)
@upload_limit
async def upload(
    request: Request, kind: FileKind, user: Uploader, db: DbSession, storage: StorageDep
) -> FileOut:
    """Upload one photo as the raw request body (`?kind=inbody|body_photo|food|chat`).

    The type is decided by the content, never the name or Content-Type: JPEG, PNG, WebP
    or HEIC, plus PDF for InBody scans (the first page is kept as an image). Metadata
    such as GPS is removed, the photo is turned upright and resized, and it is stored
    privately with a thumbnail.
    """
    data = await _read_capped(request, upload_cap(kind))
    try:
        row = await run_in_threadpool(
            svc.store_upload, db, storage, user.id, kind, data
        )
    except svc.UnsupportedTypeError:
        raise _error(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "unsupported_type",
            "Upload a JPEG, PNG, WebP or HEIC photo (or a PDF for InBody scans).",
        ) from None
    except svc.InvalidImageError:
        raise _error(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "invalid_image",
            "This file can't be opened as an image. Try another photo.",
        ) from None
    except svc.TooLargeError:
        raise _too_large() from None
    return _out(row)


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")


@router.get("/{file_id}/url", response_model=FileLinksOut)
def links(
    file_id: uuid.UUID, db: DbSession, user: CurrentUser, storage: StorageDep
) -> FileLinksOut:
    """Short-lived signed links to the photo and its thumbnail."""
    try:
        row = svc.get_owned(db, user.id, file_id)
    except svc.StoredFileNotFoundError:
        raise _not_found() from None
    url, thumbnail_url = svc.signed_urls(storage, row, URL_TTL_S)
    return FileLinksOut(url=url, thumbnail_url=thumbnail_url, expires_in=URL_TTL_S)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(
    file_id: uuid.UUID, db: DbSession, user: CurrentUser, storage: StorageDep
) -> Response:
    """Delete the photo and its thumbnail. Meals and scans that used it are kept."""
    try:
        row = svc.get_owned(db, user.id, file_id)
    except svc.StoredFileNotFoundError:
        raise _not_found() from None
    svc.delete_file(db, storage, row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
