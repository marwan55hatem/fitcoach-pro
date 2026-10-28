"""Import the open exercise library into the catalog (idempotent).

    python -m app.seed.exercises                 # download (pinned commit) + import
    python -m app.seed.exercises --from-dir DIR  # DIR holds exercises.json + exercises/<id>/<n>.jpg
    python -m app.seed.exercises --check-plan    # also verify the default 4-day split is covered

Dataset: free-exercise-db (https://github.com/yuhonas/free-exercise-db), Unlicense
(public domain), derived from wrkout/exercises.json (also Unlicense). The download is pinned
to a commit so imports are reproducible. Everything in the dataset is treated as untrusted
input: fields are validated, image paths cannot escape the data directory, and every image
is verified as a real, bounded-size picture before it reaches storage.
"""

import argparse
import io
import json
import sys
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx
import sqlalchemy as sa
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from app.models import Exercise
from app.models.enums import Equipment, Experience, Mechanic
from app.rules.default_plan import DEFAULT_SPLIT
from app.rules.exercise_catalog import (
    normalize_equipment,
    normalize_level,
    normalize_mechanic,
    normalize_muscle,
    slugify,
)
from app.services.storage import Storage

DATASET_REPO = "yuhonas/free-exercise-db"
DATASET_REF = "f00c92c7dcf1216a928a52c3706c7ce8e2f71ed5"  # pinned commit
RAW_BASE = f"https://raw.githubusercontent.com/{DATASET_REPO}/{DATASET_REF}"

SOURCE = "free-exercise-db (https://github.com/yuhonas/free-exercise-db)"
LICENSE = "Unlicense (public domain)"
ATTRIBUTION = (
    "Exercise data and images from free-exercise-db by yuhonas, derived from "
    "wrkout/exercises.json by Ollie Jennings. Public domain (Unlicense)."
)

IMAGE_PREFIX = "catalog/exercises"  # shared catalog area, NOT under users/<id>/
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PIXELS = 30_000_000  # hard cap against decompression bombs
MAX_IMAGE_SIDE = 1600  # larger images are downscaled to this on import
MAX_STEPS = 30
MAX_STEP_CHARS = 1000
COMMIT_EVERY = 100
_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "free-exercise-db"


class DatasetEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    category: str | None = None
    level: str | None = None
    mechanic: str | None = None
    equipment: str | None = None
    primaryMuscles: list[str] = []
    secondaryMuscles: list[str] = []
    instructions: list[str] = []
    images: list[str] = []


@dataclass(frozen=True)
class ExerciseRecord:
    dataset_id: str
    name: str
    category: str | None
    muscle_primary: str
    muscles_secondary: list[str]
    equipment: Equipment
    level: Experience | None
    mechanic: Mechanic | None
    instructions: list[str]
    images: list[str]


def to_record(entry: DatasetEntry) -> ExerciseRecord:
    """Validate and normalise one dataset entry. Raises ValueError with the reason."""
    name = entry.name.strip()
    if not name:
        raise ValueError("empty name")
    if not entry.primaryMuscles:
        raise ValueError("no primary muscle")
    primaries = [normalize_muscle(m) for m in entry.primaryMuscles]
    secondaries = [normalize_muscle(m) for m in entry.secondaryMuscles]
    muscle_primary = primaries[0]
    # Extra "primary" muscles become secondary: our model has exactly one primary.
    rest = [
        m for m in dict.fromkeys(primaries[1:] + secondaries) if m != muscle_primary
    ]
    steps = [s.strip()[:MAX_STEP_CHARS] for s in entry.instructions if s and s.strip()]
    return ExerciseRecord(
        dataset_id=entry.id,
        name=name[:200],
        category=entry.category.strip().lower()[:32] if entry.category else None,
        muscle_primary=muscle_primary,
        muscles_secondary=rest,
        equipment=normalize_equipment(entry.equipment),
        level=normalize_level(entry.level),
        mechanic=normalize_mechanic(entry.mechanic),
        instructions=steps[:MAX_STEPS],
        images=list(entry.images),
    )


def safe_path(base: Path, relative: str) -> Path:
    """Join `relative` onto `base`, rejecting anything that could leave it.

    The check is purely lexical (no filesystem resolution), so it is deterministic and
    race-free while threads are creating directories.
    """
    parts = PurePosixPath(relative).parts
    if (
        not relative
        or relative.startswith("/")
        or "\\" in relative
        or ":" in relative
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError(f"unsafe image path: {relative!r}")
    return base.joinpath(*parts)


def load_image(path: Path) -> tuple[bytes, str, str]:
    """Return (bytes, extension, content_type) for a verified, bounded image.

    Rejects non-images and absurd dimensions. Images with a long side above MAX_IMAGE_SIDE
    (a few dataset photos are 5000 px camera shots) are downscaled and re-encoded as JPEG.
    """
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise ValueError(f"image too large ({size} bytes)")
    data = path.read_bytes()
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format or ""
            width, height = img.size
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValueError("not a valid image") from exc
    if fmt not in _FORMATS:
        raise ValueError(f"unsupported image format {fmt!r}")
    if width * height > MAX_IMAGE_PIXELS:
        raise ValueError("image dimensions too large")
    ext, content_type = _FORMATS[fmt]
    if max(width, height) > MAX_IMAGE_SIDE:
        try:
            with Image.open(io.BytesIO(data)) as img:
                img = img.convert("RGB")
                img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
                out = io.BytesIO()
                img.save(out, "JPEG", quality=85, optimize=True)
        except (OSError, SyntaxError) as exc:
            raise ValueError("could not downscale image") from exc
        return out.getvalue(), "jpg", "image/jpeg"
    return data, ext, content_type


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    images_uploaded: int = 0
    images_existing: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (dataset id, reason)
    image_problems: list[tuple[str, str]] = field(
        default_factory=list
    )  # (path, reason)

    @property
    def total(self) -> int:
        return self.created + self.updated

    def summary(self) -> str:
        exercises = (
            f"exercises: {self.created} created, {self.updated} updated, "
            f"{len(self.skipped)} skipped"
        )
        images = (
            f"images:    {self.images_uploaded} uploaded, "
            f"{self.images_existing} already stored, {len(self.image_problems)} problems"
        )
        lines = [exercises, images]
        lines += [f"  skipped {i}: {reason}" for i, reason in self.skipped[:20]]
        lines += [
            f"  image problem {p}: {reason}" for p, reason in self.image_problems[:20]
        ]
        return "\n".join(lines)


def _store_images(
    storage: Storage,
    slug: str,
    images: Sequence[str],
    images_dir: Path,
    report: ImportReport,
) -> list[str]:
    keys: list[str] = []
    for index, relative in enumerate(images):
        try:
            data, ext, content_type = load_image(safe_path(images_dir, relative))
        except (OSError, ValueError) as exc:
            report.image_problems.append((relative, str(exc)))
            continue
        key = f"{IMAGE_PREFIX}/{slug}/{index}.{ext}"
        if storage.exists(key):
            report.images_existing += 1
        else:
            storage.put_bytes(key, data, content_type)
            report.images_uploaded += 1
        keys.append(key)
    return keys


def import_exercises(
    db: Session,
    storage: Storage,
    entries: Sequence[Any],
    images_dir: Path | None,
    *,
    limit: int | None = None,
) -> ImportReport:
    """Upsert catalog exercises (user_id NULL) by slug. Safe to run repeatedly."""
    report = ImportReport()
    seen_slugs: set[str] = set()
    for raw in entries[:limit]:
        raw_id = str(raw.get("id", "?")) if isinstance(raw, dict) else "?"
        try:
            record = to_record(DatasetEntry.model_validate(raw))
            slug = slugify(record.dataset_id)
            if not slug:
                raise ValueError("empty slug")
            if slug in seen_slugs:
                raise ValueError(f"duplicate slug {slug!r}")
        except (ValidationError, ValueError) as exc:
            report.skipped.append((raw_id, str(exc).splitlines()[0][:200]))
            continue
        seen_slugs.add(slug)

        row = db.scalar(
            sa.select(Exercise).where(Exercise.user_id.is_(None), Exercise.slug == slug)
        )
        if row is None:
            row = Exercise(user_id=None, slug=slug, image_keys=[])
            report.created += 1
        else:
            report.updated += 1
        row.name = record.name
        row.category = record.category
        row.muscle_primary = record.muscle_primary
        row.muscles_secondary = record.muscles_secondary
        row.equipment = record.equipment.value
        row.level = record.level
        row.mechanic = record.mechanic
        row.instructions = record.instructions
        row.source = SOURCE
        row.license = LICENSE
        row.attribution = ATTRIBUTION
        if images_dir is not None:
            row.image_keys = _store_images(
                storage, slug, record.images, images_dir, report
            )
        db.add(row)
        if report.total % COMMIT_EVERY == 0:
            db.commit()
    db.commit()
    return report


# --- the default plan must be covered by the library -----------------------------------


@dataclass(frozen=True)
class PlanResolution:
    day: str
    label: str
    matched: str | None
    has_images: bool


def resolve_plan(db: Session) -> list[PlanResolution]:
    results: list[PlanResolution] = []
    for slot in DEFAULT_SPLIT:
        match: Exercise | None = None
        for candidate in slot.candidates:
            match = db.scalar(
                sa.select(Exercise).where(
                    Exercise.user_id.is_(None),
                    sa.func.lower(Exercise.name) == candidate.lower(),
                )
            )
            if match is not None:
                break
        results.append(
            PlanResolution(
                day=slot.day,
                label=slot.label,
                matched=match.name if match else None,
                has_images=bool(match and match.image_keys),
            )
        )
    return results


def plan_report(resolutions: Sequence[PlanResolution]) -> str:
    lines = []
    for r in resolutions:
        status = "OK     " if r.matched and r.has_images else "MISSING"
        extra = "" if r.has_images or not r.matched else " (no images)"
        lines.append(
            f"  [{status}] {r.day:<22} {r.label:<24} -> {r.matched or '-'}{extra}"
        )
    return "\n".join(lines)


# --- download (pinned, resumable) -------------------------------------------------------


def _fetch(client: httpx.Client, url: str, attempts: int = 3) -> bytes:
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(url)
            response.raise_for_status()
            return response.content
        except httpx.HTTPError:
            if attempt == attempts:
                raise
            time.sleep(attempt)
    raise AssertionError("unreachable")


def download_dataset(
    cache_dir: Path,
    *,
    workers: int = 8,
    transport: httpx.BaseTransport | None = None,
) -> Path:
    """Fetch exercises.json and images into cache_dir (skips files already cached)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    json_path = cache_dir / "exercises.json"
    with httpx.Client(timeout=30, follow_redirects=True, transport=transport) as client:
        if not json_path.exists():
            json_path.write_bytes(_fetch(client, f"{RAW_BASE}/dist/exercises.json"))
        entries = json.loads(json_path.read_text(encoding="utf-8"))
        images_dir = cache_dir / "exercises"
        wanted: list[str] = []
        for entry in entries:
            for relative in entry.get("images", []) if isinstance(entry, dict) else []:
                try:
                    if not safe_path(images_dir, relative).exists():
                        wanted.append(relative)
                except ValueError:
                    continue  # unsafe path: never fetched, reported at import time

        def fetch_one(relative: str) -> str | None:
            try:
                target = safe_path(images_dir, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(
                    _fetch(client, f"{RAW_BASE}/exercises/{quote(relative)}")
                )
            except (httpx.HTTPError, ValueError, OSError) as exc:
                return f"{relative}: {exc}"
            return None

        failures = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for done, result in enumerate(pool.map(fetch_one, wanted), start=1):
                if result:
                    failures.append(result)
                if done % 200 == 0:
                    print(f"  downloaded {done}/{len(wanted)} images", flush=True)
    if failures:
        print(f"{len(failures)} images failed to download (re-run to retry):")
        for line in failures[:10]:
            print(f"  {line}")
    return cache_dir


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import the exercise library.")
    parser.add_argument(
        "--from-dir", type=Path, help="local dataset directory (no download)"
    )
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--limit", type=int, help="import only the first N entries")
    parser.add_argument("--no-images", action="store_true", help="skip images")
    parser.add_argument(
        "--check-plan", action="store_true", help="report plan coverage"
    )
    args = parser.parse_args(argv)

    # Imported here so unit tests can import this module without a configured database.
    from app.db.session import SessionLocal
    from app.services.storage import make_storage

    data_dir = args.from_dir or download_dataset(args.cache_dir)
    entries = json.loads((data_dir / "exercises.json").read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        print("exercises.json must contain a list", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        report = import_exercises(
            db,
            make_storage(),
            entries,
            None if args.no_images else data_dir / "exercises",
            limit=args.limit,
        )
        print(report.summary())
        if args.check_plan:
            resolutions = resolve_plan(db)
            print("\nDefault 4-day split coverage:")
            print(plan_report(resolutions))
            if any(not (r.matched and r.has_images) for r in resolutions):
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
