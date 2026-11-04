import datetime as dt
import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.api.deps import DbSession, not_in_future, require_consent
from app.models import ProgressPhoto, User
from app.models.enums import ConsentType, FileKind, PhotoView
from app.services import files, onboarding
from app.services.storage import Storage, get_storage

router = APIRouter(prefix="/progress-photos", tags=["progress"])

StorageDep = Annotated[Storage, Depends(get_storage)]
# Body photos have their own consent. They are never sent to an AI provider.
PhotoUser = Annotated[User, Depends(require_consent(ConsentType.BODY_PHOTOS))]
URL_TTL_S = 300


class PhotoIn(BaseModel):
    file_id: uuid.UUID  # an uploaded file of kind "body_photo"
    view: PhotoView
    taken_on: dt.date | None = None  # defaults to today
    notes: str | None = Field(default=None, max_length=500)


class PhotoOut(BaseModel):
    id: uuid.UUID
    taken_on: dt.date
    view: PhotoView
    notes: str | None
    file_id: uuid.UUID
    url: str  # signed, short-lived
    thumbnail_url: str | None
    expires_in: int


def _out(db: DbSession, storage: Storage, photo: ProgressPhoto) -> PhotoOut:
    stored = files.get_owned(db, photo.user_id, photo.file_id)
    url, thumbnail_url = files.signed_urls(storage, stored, URL_TTL_S)
    return PhotoOut(
        id=photo.id,
        taken_on=photo.taken_on,
        view=photo.view,
        notes=photo.notes,
        file_id=photo.file_id,
        url=url,
        thumbnail_url=thumbnail_url,
        expires_in=URL_TTL_S,
    )


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")


@router.post("", response_model=PhotoOut, status_code=status.HTTP_201_CREATED)
def add(body: PhotoIn, db: DbSession, user: PhotoUser, storage: StorageDep) -> PhotoOut:
    """Add a progress photo. A second photo of the same view on the same day replaces
    the first (and deletes it)."""
    try:
        stored = files.get_owned(db, user.id, body.file_id)
    except files.StoredFileNotFoundError:
        raise _not_found() from None
    if stored.kind is not FileKind.BODY_PHOTO:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "wrong_file_kind",
                "message": "Upload the photo as a body photo first.",
            },
        )
    taken_on = not_in_future(body.taken_on or onboarding.user_today(db, user), db, user)
    existing = db.scalar(
        sa.select(ProgressPhoto).where(
            ProgressPhoto.user_id == user.id,
            ProgressPhoto.taken_on == taken_on,
            ProgressPhoto.view == body.view,
        )
    )
    if existing is not None and existing.file_id != stored.id:
        old = files.get_owned(db, user.id, existing.file_id)
        files.delete_file(db, storage, old)  # its photo row goes with it
    photo = ProgressPhoto(
        user_id=user.id,
        taken_on=taken_on,
        view=body.view,
        file_id=stored.id,
        notes=body.notes,
    )
    db.add(photo)
    db.commit()
    return _out(db, storage, photo)


@router.get("", response_model=list[PhotoOut])
def photos(
    db: DbSession,
    user: PhotoUser,
    storage: StorageDep,
    view: Annotated[PhotoView | None, Query()] = None,
) -> list[PhotoOut]:
    """Your progress photos, newest first, with signed links valid for 5 minutes."""
    query = sa.select(ProgressPhoto).where(ProgressPhoto.user_id == user.id)
    if view is not None:
        query = query.where(ProgressPhoto.view == view)
    rows = db.scalars(
        query.order_by(ProgressPhoto.taken_on.desc(), ProgressPhoto.created_at.desc())
    )
    return [_out(db, storage, p) for p in rows]


@router.delete("/{photo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(
    photo_id: uuid.UUID, db: DbSession, user: PhotoUser, storage: StorageDep
) -> Response:
    """Delete the photo and its stored image."""
    photo = db.scalar(
        sa.select(ProgressPhoto).where(
            ProgressPhoto.id == photo_id, ProgressPhoto.user_id == user.id
        )
    )
    if photo is None:
        raise _not_found()
    files.delete_file(db, storage, files.get_owned(db, user.id, photo.file_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
