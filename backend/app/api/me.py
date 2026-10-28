from collections.abc import Iterator
from typing import IO, Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, DbSession
from app.core.rate_limit import auth_limit
from app.core.security import verify_password
from app.schemas.auth import DeleteAccountRequest
from app.services import audit
from app.services.account import delete_account
from app.services.export import build_export
from app.services.storage import Storage, get_storage

router = APIRouter(prefix="/me", tags=["me"])

StorageDep = Annotated[Storage, Depends(get_storage)]


def _stream(file: IO[bytes]) -> Iterator[bytes]:
    try:
        while chunk := file.read(1024 * 1024):
            yield chunk
    finally:
        file.close()


@router.get("/export")
def export_data(
    user: CurrentUser, db: DbSession, storage: StorageDep
) -> StreamingResponse:
    """ZIP with all of the user's data as JSON plus all of their files."""
    archive = build_export(db, user, storage)
    audit.record(db, "account.export", user.id)
    db.commit()
    return StreamingResponse(
        _stream(archive),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="fitcoach-export.zip"',
            "Cache-Control": "no-store",
        },
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
@auth_limit  # also throttles password guessing with a stolen access token
def delete_me(
    request: Request,
    body: DeleteAccountRequest,
    user: CurrentUser,
    db: DbSession,
    storage: StorageDep,
) -> Response:
    """Irreversibly delete the account, all data and all stored files.

    Requires the current password so a stolen access token alone cannot wipe an account.
    """
    valid, _ = verify_password(body.password, user.password_hash)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
        )
    delete_account(db, storage, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
