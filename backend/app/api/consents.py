from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.models.enums import ConsentType
from app.schemas.consent import ConsentsResponse, ConsentStatus, ConsentsUpdate
from app.services import consents as consent_service

router = APIRouter(prefix="/consents", tags=["consents"])


def _response(db: DbSession, user: CurrentUser) -> ConsentsResponse:
    latest = consent_service.latest_by_type(db, user.id)
    items = []
    for consent_type in ConsentType:
        row = latest.get(consent_type)
        items.append(
            ConsentStatus(
                type=consent_type,
                granted=row is not None and row.revoked_at is None,
                granted_at=row.granted_at if row else None,
                revoked_at=row.revoked_at if row else None,
            )
        )
    return ConsentsResponse(consents=items)


@router.get("", response_model=ConsentsResponse)
def get_consents(db: DbSession, user: CurrentUser) -> ConsentsResponse:
    return _response(db, user)


@router.put("", response_model=ConsentsResponse)
def put_consents(
    body: ConsentsUpdate, db: DbSession, user: CurrentUser
) -> ConsentsResponse:
    consent_service.set_consents(db, user.id, body.consents)
    return _response(db, user)
