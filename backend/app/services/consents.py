import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models import Consent
from app.models.enums import ConsentType
from app.services import audit


def active_consent_types(db: Session, user_id: uuid.UUID) -> dict[ConsentType, Consent]:
    rows = db.scalars(
        sa.select(Consent)
        .where(Consent.user_id == user_id, Consent.revoked_at.is_(None))
        .order_by(Consent.granted_at)
    )
    return {c.type: c for c in rows}


def has_consent(db: Session, user_id: uuid.UUID, consent_type: ConsentType) -> bool:
    return (
        db.scalar(
            sa.select(
                sa.exists().where(
                    Consent.user_id == user_id,
                    Consent.type == consent_type,
                    Consent.revoked_at.is_(None),
                )
            )
        )
        is True
    )


def latest_by_type(db: Session, user_id: uuid.UUID) -> dict[ConsentType, Consent]:
    """Most recent consent row per type (granted or revoked)."""
    rows = db.scalars(
        sa.select(Consent)
        .where(Consent.user_id == user_id)
        .order_by(Consent.granted_at)
    )
    return {c.type: c for c in rows}  # later rows overwrite earlier ones


def set_consents(
    db: Session, user_id: uuid.UUID, changes: dict[ConsentType, bool]
) -> None:
    """Grant or revoke. Granting an already-active consent is a no-op; revoking keeps history."""
    active = active_consent_types(db, user_id)
    now = dt.datetime.now(dt.UTC)
    for consent_type, granted in changes.items():
        current = active.get(consent_type)
        if granted and current is None:
            db.add(Consent(user_id=user_id, type=consent_type, granted_at=now))
        elif not granted and current is not None:
            current.revoked_at = now
        else:
            continue
        audit.record(
            db,
            "consent.granted" if granted else "consent.revoked",
            user_id,
            entity_type="consent",
            meta={"type": consent_type.value},
        )
    db.commit()
