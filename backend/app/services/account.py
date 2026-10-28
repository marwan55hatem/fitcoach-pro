import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models import StoredFile, User
from app.services import audit
from app.services.storage import Storage, user_prefix


def delete_account(db: Session, storage: Storage, user: User) -> None:
    """Hard-delete the user, every row they own and every stored object.

    Storage goes first: if it fails nothing is deleted from the DB, so the request
    can simply be retried and no orphaned health data is left behind. The audit
    entry carries only the pseudonymous user id and a count, never personal content.
    """
    user_id = user.id
    keys = db.scalars(
        sa.select(StoredFile.storage_key).where(StoredFile.user_id == user_id)
    ).all()
    for key in keys:
        storage.delete(key)
    swept = storage.delete_prefix(
        user_prefix(user_id)
    )  # also catches unregistered objects

    # FK ON DELETE CASCADE removes all of the user's rows; audit_log.user_id becomes NULL.
    db.execute(sa.delete(User).where(User.id == user_id))
    db.expunge_all()
    audit.record(
        db,
        "account.delete",
        None,
        entity_type="user",
        entity_id=user_id,
        meta={"files_deleted": len(keys), "orphans_swept": swept},
    )
    db.commit()
