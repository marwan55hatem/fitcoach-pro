"""InBody scans: the AI reads a draft, the rules flag doubtful values, the user fixes and
confirms. Only confirmed scans are used anywhere else (nutrition, coaching, trends)."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.ai.client import AiClient
from app.ai.inbody import InbodyReading, Reading, Segments, read_inbody
from app.models import InbodyScan, User
from app.models.enums import FileKind
from app.rules.inbody_validation import (
    Flag,
    ScanValues,
    adjust_confidence,
    clean_segments,
    interpret_change,
    validate_scan,
)
from app.schemas.inbody import InbodyEditIn
from app.services import files, onboarding
from app.services.storage import Storage

DRAFT_TTL = dt.timedelta(days=7)
EARLIEST_SCAN = dt.date(2000, 1, 1)
SEGMENT_PARTS = ("left_arm", "right_arm", "trunk", "left_leg", "right_leg")
_INTEGERS = {"bmr_kcal", "visceral_fat_level", "inbody_score"}
# Scan column <- field of the AI's reading.
_READING_FIELDS = {
    "weight_kg": "weight_kg",
    "smm_kg": "skeletal_muscle_mass_kg",
    "body_fat_mass_kg": "body_fat_mass_kg",
    "pbf_pct": "percent_body_fat",
    "bmi": "bmi",
    "tbw_l": "total_body_water_l",
    "bmr_kcal": "bmr_kcal",
    "visceral_fat_level": "visceral_fat_level",
    "inbody_score": "inbody_score",
}
_DATE_MESSAGES = {
    "missing": "No test date could be read. Check the date.",
    "implausible": "The test date can't be right. Check the date.",
}


class ScanNotFoundError(Exception):
    pass


class AlreadyConfirmedError(Exception):
    pass


class WrongFileKindError(Exception):
    pass


# --- converting values ----------------------------------------------------------------


def _store(column: str, value: float | None) -> Any:
    if value is None:
        return None
    return round(value) if column in _INTEGERS else Decimal(str(round(value, 1)))


def _number(value: Decimal | int | None) -> float | None:
    return None if value is None else float(value)


def values_of(scan: InbodyScan) -> ScanValues:
    return ScanValues(**{c: _number(getattr(scan, c)) for c in _READING_FIELDS})


def _segments(reading: Segments | None) -> dict[str, float | None] | None:
    if reading is None:
        return None
    return {part: getattr(reading, part).value for part in SEGMENT_PARTS}


def _scan_date(text: str | None, today: dt.date) -> tuple[dt.date, str | None]:
    """The printed test date, or today with the reason it could not be used."""
    try:
        printed = dt.date.fromisoformat(text) if text else None
    except ValueError:
        printed = None
    if printed is None:
        return today, "missing"
    if printed > today or printed < EARLIEST_SCAN:
        return today, "implausible"
    return printed, None


def _height(db: Session, user_id: uuid.UUID, printed: Any) -> float | None:
    """For the BMI check: the height on the sheet, else the profile's."""
    if isinstance(printed, int | float) and 100 <= printed <= 250:
        return float(printed)
    profile = onboarding.get_profile(db, user_id)
    return float(profile.height_cm) if profile and profile.height_cm else None


def _printed_height(scan: InbodyScan) -> Any:
    return ((scan.ai_raw or {}).get("height_cm") or {}).get("value")


def flags_for(db: Session, scan: InbodyScan) -> list[Flag]:
    """Doubtful values, checked again on every read so edits clear them."""
    meta = scan.extraction_confidence or {}
    height = _height(db, scan.user_id, _printed_height(scan))
    flags = [
        Flag(name, "out_of_range", "This number couldn't be right, so it was left out.")
        for name in meta.get("dropped", [])
    ]
    flags += validate_scan(values_of(scan), height).flags
    issue = meta.get("date_issue")
    if issue in _DATE_MESSAGES:
        flags.insert(0, Flag("taken_on", issue, _DATE_MESSAGES[issue]))
    return flags


def confidence_of(scan: InbodyScan) -> dict[str, float]:
    return dict((scan.extraction_confidence or {}).get("fields", {}))


# --- drafts ---------------------------------------------------------------------------


def _draft_from(
    db: Session, user: User, file_id: uuid.UUID, reading: InbodyReading
) -> InbodyScan:
    raw: dict[str, Reading] = {
        c: getattr(reading, f) for c, f in _READING_FIELDS.items()
    }
    height = _height(db, user.id, reading.height_cm.value)
    checked = validate_scan(ScanValues(**{c: r.value for c, r in raw.items()}), height)
    flags: list[Flag] = list(checked.flags)
    confidence = {c: r.confidence for c, r in raw.items()}

    segmental: dict[str, Any] = {}
    for name, source in (
        ("lean", reading.segmental_lean_kg),
        ("fat", reading.segmental_fat_kg),
    ):
        parts = _segments(source)
        if parts is None or source is None:
            continue
        cleaned, segment_flags = clean_segments(f"segmental_{name}", parts)
        segmental[name] = cleaned
        flags += segment_flags
        for part in SEGMENT_PARTS:
            confidence[f"segmental_{name}.{part}"] = getattr(source, part).confidence

    taken_on, date_issue = _scan_date(
        reading.test_date, onboarding.user_today(db, user)
    )
    scan = InbodyScan(
        user_id=user.id,
        file_id=file_id,
        taken_on=taken_on,
        segmental=segmental or None,
        ai_raw=reading.model_dump(),
        **{c: _store(c, getattr(checked.values, c)) for c in _READING_FIELDS},
    )
    scan.extraction_confidence = {
        "fields": adjust_confidence(confidence, flags),
        "date_issue": date_issue,
        # Left out as impossible: kept as a warning until the user enters a value.
        "dropped": [f.field for f in flags if f.code == "out_of_range"],
    }
    return scan


def create_draft(
    db: Session, storage: Storage, ai: AiClient, user: User, file_id: uuid.UUID
) -> InbodyScan:
    """Read an uploaded InBody file into a draft. Only the image goes to the AI."""
    stored = files.get_owned(db, user.id, file_id)  # StoredFileNotFoundError -> 404
    if stored.kind is not FileKind.INBODY:
        raise WrongFileKindError
    user_id = user.id
    purge_old_drafts(db, now=dt.datetime.now(dt.UTC))
    image = files.load_for_ai(db, storage, user_id, file_id)  # releases the connection
    reading = read_inbody(ai, user_id=user_id, image=image)  # slow: no DB held
    scan = _draft_from(db, user, file_id, reading)
    db.add(scan)
    db.commit()
    return scan


def get_owned(db: Session, user: User, scan_id: uuid.UUID) -> InbodyScan:
    scan = db.scalar(
        sa.select(InbodyScan).where(
            InbodyScan.id == scan_id, InbodyScan.user_id == user.id
        )
    )
    if scan is None:
        raise ScanNotFoundError
    return scan


def list_scans(db: Session, user: User, *, confirmed: bool) -> list[InbodyScan]:
    return list(
        db.scalars(
            sa.select(InbodyScan)
            .where(InbodyScan.user_id == user.id, InbodyScan.confirmed.is_(confirmed))
            .order_by(InbodyScan.taken_on.desc(), InbodyScan.created_at.desc())
        )
    )


def edit_draft(db: Session, scan: InbodyScan, edit: InbodyEditIn) -> InbodyScan:
    """Apply the user's corrections. A typed value is fully trusted (confidence 1)."""
    if scan.confirmed:
        raise AlreadyConfirmedError
    changes = edit.model_dump(exclude_unset=True)
    meta = dict(scan.extraction_confidence or {})
    confidence = dict(meta.get("fields", {}))
    edited: set[str] = set()
    for column in _READING_FIELDS:
        if column in changes:
            setattr(scan, column, _store(column, changes[column]))
            confidence[column] = 1.0
            edited.add(column)
    segmental = dict(scan.segmental or {})
    for name in ("lean", "fat"):
        key = f"segmental_{name}"
        if key not in changes:
            continue
        if changes[key] is None:
            segmental.pop(name, None)
        else:
            segmental[name] = changes[key]
        for part in SEGMENT_PARTS:
            confidence[f"{key}.{part}"] = 1.0
            edited.add(f"{key}.{part}")
    scan.segmental = segmental or None
    if changes.get("taken_on") is not None:
        scan.taken_on = changes["taken_on"]
        meta["date_issue"] = None
    meta["dropped"] = [f for f in meta.get("dropped", []) if f not in edited]
    meta["fields"] = confidence
    scan.extraction_confidence = meta
    db.commit()
    return scan


def confirm(db: Session, scan: InbodyScan) -> InbodyScan:
    if scan.confirmed:
        raise AlreadyConfirmedError
    scan.confirmed = True
    scan.confirmed_at = dt.datetime.now(dt.UTC)
    db.commit()
    return scan


def purge_old_drafts(db: Session, *, now: dt.datetime) -> int:
    """Drafts nobody confirmed within a week are deleted (the uploaded file stays)."""
    result = db.execute(
        sa.delete(InbodyScan).where(
            InbodyScan.confirmed.is_(False), InbodyScan.created_at < now - DRAFT_TTL
        )
    )
    db.commit()
    return result.rowcount or 0


# --- trend ----------------------------------------------------------------------------


def _difference(after: float | None, before: float | None) -> float | None:
    return None if after is None or before is None else round(after - before, 1)


def trend(db: Session, user: User) -> dict[str, Any]:
    scans = list(reversed(list_scans(db, user, confirmed=True)))  # oldest first
    points = [
        {
            "date": s.taken_on,
            "weight_kg": _number(s.weight_kg),
            "smm_kg": _number(s.smm_kg),
            "body_fat_mass_kg": _number(s.body_fat_mass_kg),
            "pbf_pct": _number(s.pbf_pct),
        }
        for s in scans
    ]
    if len(points) < 2:
        return {"points": points, "change": None}
    before, after = points[-2], points[-1]
    deltas = {
        k: _difference(after[k], before[k])
        for k in ("weight_kg", "smm_kg", "body_fat_mass_kg", "pbf_pct")
    }
    meaning = interpret_change(deltas["smm_kg"], deltas["body_fat_mass_kg"])
    return {
        "points": points,
        "change": {
            "from_date": before["date"],
            "to_date": after["date"],
            "days": (after["date"] - before["date"]).days,
            **deltas,
            "interpretation_code": meaning.code if meaning else None,
            "interpretation": meaning.message if meaning else None,
        },
    }
