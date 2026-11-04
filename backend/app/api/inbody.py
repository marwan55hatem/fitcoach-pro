import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.ai.client import AiClient, get_ai_client
from app.api.deps import DbSession, HealthUser, not_in_future, require_consent
from app.models import InbodyScan, User
from app.models.enums import ConsentType
from app.schemas.inbody import (
    AnalyzeIn,
    FlagOut,
    InbodyEditIn,
    InbodyOut,
    Segments,
    TrendOut,
)
from app.services import files
from app.services import inbody as svc
from app.services.storage import Storage, get_storage

router = APIRouter(prefix="/inbody", tags=["inbody"])

AiDep = Annotated[AiClient, Depends(get_ai_client)]
StorageDep = Annotated[Storage, Depends(get_storage)]
# Reading a scan sends the image to an AI provider: that needs its own consent too.
AiConsent = Annotated[User, Depends(require_consent(ConsentType.AI_PROCESSING))]


def _float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _segments(scan: InbodyScan, name: str) -> Segments | None:
    parts = (scan.segmental or {}).get(name)
    return Segments(**parts) if parts else None


def _out(db: DbSession, scan: InbodyScan) -> InbodyOut:
    return InbodyOut(
        id=scan.id,
        taken_on=scan.taken_on,
        file_id=scan.file_id,
        confirmed=scan.confirmed,
        confirmed_at=scan.confirmed_at,
        created_at=scan.created_at,
        weight_kg=_float(scan.weight_kg),
        smm_kg=_float(scan.smm_kg),
        body_fat_mass_kg=_float(scan.body_fat_mass_kg),
        pbf_pct=_float(scan.pbf_pct),
        bmi=_float(scan.bmi),
        tbw_l=_float(scan.tbw_l),
        bmr_kcal=scan.bmr_kcal,
        visceral_fat_level=scan.visceral_fat_level,
        inbody_score=scan.inbody_score,
        segmental_lean=_segments(scan, "lean"),
        segmental_fat=_segments(scan, "fat"),
        confidence=svc.confidence_of(scan),
        flags=[
            FlagOut(field=f.field, code=f.code, message=f.message)
            for f in svc.flags_for(db, scan)
        ],
    )


def _owned(db: DbSession, user: User, scan_id: uuid.UUID) -> InbodyScan:
    try:
        return svc.get_owned(db, user, scan_id)
    except svc.ScanNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found") from None


def _confirmed_conflict() -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT, detail="This scan is already confirmed."
    )


@router.post("/analyze", response_model=InbodyOut, status_code=status.HTTP_201_CREATED)
def analyze(
    body: AnalyzeIn,
    db: DbSession,
    user: HealthUser,
    _ai_consent: AiConsent,
    ai: AiDep,
    storage: StorageDep,
) -> InbodyOut:
    """Read an uploaded InBody scan (photo or PDF) into a DRAFT.

    Only the image is sent to the AI provider. Nothing is used until you check the
    values (see `flags`) and confirm.
    """
    try:
        scan = svc.create_draft(db, storage, ai, user, body.file_id)
    except files.StoredFileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found") from None
    except svc.WrongFileKindError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "wrong_file_kind",
                "message": "Upload the scan as an InBody file first.",
            },
        ) from None
    return _out(db, scan)


@router.get("", response_model=list[InbodyOut])
def confirmed_scans(db: DbSession, user: HealthUser) -> list[InbodyOut]:
    """Your confirmed scans, newest first. Drafts are listed separately."""
    return [_out(db, s) for s in svc.list_scans(db, user, confirmed=True)]


@router.get("/drafts", response_model=list[InbodyOut])
def drafts(db: DbSession, user: HealthUser) -> list[InbodyOut]:
    """Scans read by the AI that you have not confirmed yet (kept for 7 days)."""
    return [_out(db, s) for s in svc.list_scans(db, user, confirmed=False)]


@router.get("/trend", response_model=TrendOut)
def trend(db: DbSession, user: HealthUser) -> TrendOut:
    """Weight, muscle and fat over your confirmed scans, and what the last change means."""
    return TrendOut.model_validate(svc.trend(db, user))


@router.get("/{scan_id}", response_model=InbodyOut)
def get_scan(scan_id: uuid.UUID, db: DbSession, user: HealthUser) -> InbodyOut:
    return _out(db, _owned(db, user, scan_id))


@router.put("/{scan_id}", response_model=InbodyOut)
def edit(
    scan_id: uuid.UUID, body: InbodyEditIn, db: DbSession, user: HealthUser
) -> InbodyOut:
    """Correct a draft before confirming it."""
    scan = _owned(db, user, scan_id)
    if body.taken_on is not None:
        not_in_future(body.taken_on, db, user)
    try:
        return _out(db, svc.edit_draft(db, scan, body))
    except svc.AlreadyConfirmedError:
        raise _confirmed_conflict() from None


@router.post("/{scan_id}/confirm", response_model=InbodyOut)
def confirm(scan_id: uuid.UUID, db: DbSession, user: HealthUser) -> InbodyOut:
    """You checked the values: from now on this scan is used (for example its BMR)."""
    try:
        return _out(db, svc.confirm(db, _owned(db, user, scan_id)))
    except svc.AlreadyConfirmedError:
        raise _confirmed_conflict() from None
