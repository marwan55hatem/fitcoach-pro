import io
import json
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
import sqlalchemy as sa
from PIL import Image
from sqlalchemy.orm import Session

from app.models import Exercise
from app.models.enums import Equipment, Experience, Mechanic
from app.seed import exercises as seed
from app.seed.exercises import (
    ATTRIBUTION,
    IMAGE_PREFIX,
    LICENSE,
    DatasetEntry,
    download_dataset,
    import_exercises,
    load_image,
    plan_report,
    resolve_plan,
    safe_path,
    to_record,
)
from app.services.storage import Storage
from tests.exercise_fixtures import ENTRIES, entry, plan_entries, write_dataset
from tests.helpers import delete_me, register_and_login


@pytest.fixture(autouse=True)
def _clean_catalog_storage(storage: Storage) -> None:
    storage.delete_prefix("catalog/")


def catalog(db: Session) -> list[Exercise]:
    return list(db.scalars(sa.select(Exercise).where(Exercise.user_id.is_(None))))


def keys(storage: Storage) -> set[str]:
    paginator = storage._client.get_paginator("list_objects_v2")
    found: set[str] = set()
    for page in paginator.paginate(Bucket=storage.bucket, Prefix="catalog/"):
        found |= {o["Key"] for o in page.get("Contents", [])}
    return found


# --- to_record --------------------------------------------------------------------------


def test_to_record_normalises_everything() -> None:
    record = to_record(
        DatasetEntry.model_validate(
            {
                "id": "X_1",
                "name": "  Weird Press ",
                "level": "expert",
                "mechanic": "compound",
                "equipment": "e-z curl bar",
                "primaryMuscles": ["quadriceps", "glutes"],
                "secondaryMuscles": ["glutes", "lower back", "quadriceps"],
                "instructions": [" step one ", "", "   ", "step two"],
                "category": "Strength",
                "images": ["X_1/0.jpg"],
            }
        )
    )
    assert record.name == "Weird Press"
    assert record.muscle_primary == "quads"
    # The extra "primary" muscle joins the secondaries; no duplicates, never the primary.
    assert record.muscles_secondary == ["glutes", "lower_back"]
    assert record.equipment is Equipment.EZ_BAR
    assert record.level is Experience.ADVANCED and record.mechanic is Mechanic.COMPOUND
    assert record.instructions == ["step one", "step two"]
    assert record.category == "strength"


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"primaryMuscles": []}, "no primary muscle"),
        ({"primaryMuscles": ["pinky toe"]}, "unknown muscle"),
        ({"name": "   "}, "empty name"),
        ({"equipment": "jetpack"}, "unknown equipment"),
        ({"level": "godlike"}, "unknown level"),
        ({"mechanic": "magic"}, "unknown mechanic"),
    ],
)
def test_to_record_rejects_bad_entries(override: dict, message: str) -> None:
    raw = {**entry("Bad", "barbell", "chest"), **override}
    with pytest.raises(ValueError, match=message):
        to_record(DatasetEntry.model_validate(raw))


def test_to_record_caps_untrusted_text() -> None:
    raw = {**entry("Long", "barbell", "chest"), "instructions": ["x" * 5000] * 50}
    record = to_record(DatasetEntry.model_validate(raw))
    assert len(record.instructions) == seed.MAX_STEPS
    assert all(len(s) == seed.MAX_STEP_CHARS for s in record.instructions)


# --- safe paths and images ---------------------------------------------------------------


def test_safe_path(tmp_path: Path) -> None:
    assert (
        safe_path(tmp_path, "Air_Bike/0.jpg")
        == (tmp_path / "Air_Bike" / "0.jpg").resolve()
    )
    for bad in [
        "../evil.jpg",
        "a/../../evil.jpg",
        "/etc/passwd",
        "C:\\Windows\\x.jpg",
        "a\\b.jpg",
        "",
    ]:
        with pytest.raises(ValueError, match="unsafe|escapes"):
            safe_path(tmp_path, bad)


def test_load_image_accepts_real_images_and_rejects_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jpg = tmp_path / "a.jpg"
    Image.new("RGB", (8, 8)).save(jpg, "JPEG")
    data, ext, content_type = load_image(jpg)
    assert (
        ext == "jpg" and content_type == "image/jpeg" and data.startswith(b"\xff\xd8")
    )

    png = tmp_path / "a.png"
    Image.new("RGB", (8, 8)).save(png, "PNG")
    assert load_image(png)[1:] == ("png", "image/png")

    fake = tmp_path / "fake.jpg"
    fake.write_text("<html>definitely not a picture</html>")
    with pytest.raises(ValueError, match="not a valid image"):
        load_image(fake)

    gif = tmp_path / "a.gif"
    Image.new("P", (8, 8)).save(gif, "GIF")
    with pytest.raises(ValueError, match="unsupported image format"):
        load_image(gif)

    monkeypatch.setattr(seed, "MAX_IMAGE_BYTES", 10)
    with pytest.raises(ValueError, match="too large"):
        load_image(jpg)
    monkeypatch.undo()

    monkeypatch.setattr(seed, "MAX_IMAGE_PIXELS", 10)
    with pytest.raises(ValueError, match="dimensions"):
        load_image(jpg)


def test_oversized_photos_are_downscaled_but_normal_images_are_untouched(
    tmp_path: Path,
) -> None:
    big = tmp_path / "big.png"
    Image.new("RGB", (3200, 2000), (10, 200, 10)).save(big, "PNG")  # a "camera shot"
    data, ext, content_type = load_image(big)
    assert (ext, content_type) == ("jpg", "image/jpeg")
    with Image.open(io.BytesIO(data)) as out:
        assert out.format == "JPEG" and max(out.size) == seed.MAX_IMAGE_SIDE
        assert out.size == (1600, 1000)  # aspect ratio kept

    small = tmp_path / "small.jpg"
    Image.new("RGB", (800, 500)).save(small, "JPEG")
    assert load_image(small)[0] == small.read_bytes()  # bytes unchanged


# --- import -----------------------------------------------------------------------------


def run_import(db: Session, storage: Storage, tmp_path: Path, entries=None, **kwargs):
    data_dir = write_dataset(tmp_path / "data", entries)
    parsed = json.loads((data_dir / "exercises.json").read_text(encoding="utf-8"))
    return import_exercises(db, storage, parsed, data_dir / "exercises", **kwargs)


def test_import_creates_catalog_rows_with_images_and_attribution(
    db: Session, storage: Storage, tmp_path: Path
) -> None:
    report = run_import(db, storage, tmp_path)
    assert (
        report.created == len(ENTRIES) and report.updated == 0 and report.skipped == []
    )
    assert report.images_uploaded == 2 * (len(ENTRIES) - 1)  # Cat Stretch has no images

    rows = {r.name: r for r in catalog(db)}
    bench = rows["Barbell Bench Press - Medium Grip"]
    assert bench.user_id is None
    assert bench.slug == "barbell-bench-press-medium-grip"
    assert bench.muscle_primary == "chest" and bench.muscles_secondary == [
        "shoulders",
        "triceps",
    ]
    assert bench.equipment == "barbell" and bench.level is Experience.INTERMEDIATE
    assert bench.mechanic is Mechanic.COMPOUND and bench.category == "strength"
    assert bench.instructions == [
        "Step one of Barbell Bench Press - Medium Grip.",
        "Step two of Barbell Bench Press - Medium Grip.",
    ]
    assert bench.license == LICENSE and bench.attribution == ATTRIBUTION
    assert "free-exercise-db" in bench.source

    # Images live in the shared catalog area, never under a user's prefix.
    assert bench.image_keys == [
        f"{IMAGE_PREFIX}/barbell-bench-press-medium-grip/0.jpg",
        f"{IMAGE_PREFIX}/barbell-bench-press-medium-grip/1.jpg",
    ]
    assert all(
        k.startswith("catalog/exercises/") for r in rows.values() for k in r.image_keys
    )
    assert set(bench.image_keys) <= keys(storage)
    assert storage.get_bytes(bench.image_keys[0]).startswith(b"\xff\xd8")

    stretch = rows["Cat Stretch"]
    assert stretch.equipment == "body_weight" and stretch.mechanic is None
    assert stretch.category == "stretching" and stretch.image_keys == []
    assert rows["Snatch Pull"].level is Experience.ADVANCED


def test_import_is_idempotent(db: Session, storage: Storage, tmp_path: Path) -> None:
    first = run_import(db, storage, tmp_path)
    before = {r.slug: (r.id, r.image_keys) for r in catalog(db)}
    stored = keys(storage)

    second = run_import(db, storage, tmp_path)
    assert second.created == 0 and second.updated == first.created
    assert (
        second.images_uploaded == 0 and second.images_existing == first.images_uploaded
    )
    assert {
        r.slug: (r.id, r.image_keys) for r in catalog(db)
    } == before  # same rows, same ids
    assert keys(storage) == stored


def test_import_updates_changed_data_in_place(
    db: Session, storage: Storage, tmp_path: Path
) -> None:
    run_import(db, storage, tmp_path)
    original = {r.slug: r.id for r in catalog(db)}

    changed = [dict(e) for e in ENTRIES]
    changed[0]["name"] = "Renamed Bench"
    changed[0]["level"] = "beginner"
    report = run_import(db, storage, tmp_path / "again", entries=changed)
    assert report.created == 0
    row = db.scalar(
        sa.select(Exercise).where(Exercise.slug == "barbell-bench-press-medium-grip")
    )
    assert row.name == "Renamed Bench" and row.level is Experience.BEGINNER
    assert row.id == original["barbell-bench-press-medium-grip"]


def test_import_skips_bad_entries_but_keeps_going(
    db: Session, storage: Storage, tmp_path: Path
) -> None:
    good = entry("Good Curl", "barbell", "biceps", mechanic="isolation")
    bad = [
        {**entry("No Muscle", "barbell", "chest"), "primaryMuscles": []},
        {**entry("Weird Muscle", "barbell", "chest"), "primaryMuscles": ["spleen"]},
        {"id": "no-name"},
        "not even a dict",
        {
            **entry("Good Curl", "barbell", "biceps"),
            "id": "Good_Curl",
        },  # duplicate slug
    ]
    report = run_import(db, storage, tmp_path, entries=[good, *bad])
    assert report.created == 1
    assert len(report.skipped) == 5
    reasons = " | ".join(reason for _, reason in report.skipped)
    assert "no primary muscle" in reasons and "unknown muscle" in reasons
    assert "duplicate slug" in reasons
    assert [r.name for r in catalog(db)] == ["Good Curl"]


def test_import_reports_bad_images_and_keeps_the_exercise(
    db: Session, storage: Storage, tmp_path: Path
) -> None:
    data_dir = write_dataset(tmp_path / "data", [entry("Cable Row", "cable", "lats")])
    (data_dir / "exercises" / "Cable_Row" / "1.jpg").write_text("not an image")
    parsed = json.loads((data_dir / "exercises.json").read_text(encoding="utf-8"))
    parsed[0]["images"] = [
        *parsed[0]["images"],
        "Cable_Row/missing.jpg",
        "../../outside.jpg",
    ]

    report = import_exercises(db, storage, parsed, data_dir / "exercises")
    assert report.created == 1 and report.images_uploaded == 1
    problems = dict(report.image_problems)
    assert "not a valid image" in problems["Cable_Row/1.jpg"]
    assert "Cable_Row/missing.jpg" in problems
    assert "unsafe image path" in problems["../../outside.jpg"]
    row = catalog(db)[0]
    assert row.image_keys == ["catalog/exercises/cable-row/0.jpg"]


def test_import_without_images_keeps_existing_image_keys(
    db: Session, storage: Storage, tmp_path: Path
) -> None:
    run_import(db, storage, tmp_path)
    keys_before = {r.slug: r.image_keys for r in catalog(db)}
    data_dir = tmp_path / "data"
    parsed = json.loads((data_dir / "exercises.json").read_text(encoding="utf-8"))
    report = import_exercises(db, storage, parsed, None)
    assert report.images_uploaded == 0 and report.updated == len(ENTRIES)
    assert {r.slug: r.image_keys for r in catalog(db)} == keys_before


def test_import_limit(db: Session, storage: Storage, tmp_path: Path) -> None:
    report = run_import(db, storage, tmp_path, limit=3)
    assert report.created == 3 and len(catalog(db)) == 3


def test_catalog_survives_account_deletion(
    client, db: Session, storage: Storage, tmp_path: Path
) -> None:
    run_import(db, storage, tmp_path)
    stored = keys(storage)
    account = register_and_login(client)
    assert delete_me(client, account).status_code == 204
    assert len(catalog(db)) == len(ENTRIES)
    assert keys(storage) == stored  # deleting a user sweeps users/<id>/ only


# --- plan coverage ----------------------------------------------------------------------


def test_plan_is_fully_covered_when_the_library_has_every_slot(
    db: Session, storage: Storage, tmp_path: Path
) -> None:
    run_import(db, storage, tmp_path, entries=plan_entries())
    resolutions = resolve_plan(db)
    assert len(resolutions) == 18
    assert all(r.matched and r.has_images for r in resolutions)
    assert "MISSING" not in plan_report(resolutions)


def test_plan_reports_missing_and_uses_fallback_names(
    db: Session, storage: Storage, tmp_path: Path
) -> None:
    entries = [
        e
        for e in plan_entries()
        if e["name"] not in {"Dumbbell Flyes", "Face Pull", "Seated Cable Rows"}
    ]
    entries.append(
        entry("Cable Crossover", "cable", "chest", mechanic="isolation")
    )  # fallback
    entries.append(
        {**entry("Seated Cable Rows", "cable", "lats"), "images": []}
    )  # no images
    run_import(db, storage, tmp_path, entries=entries)

    by_label = {r.label: r for r in resolve_plan(db)}
    assert (
        by_label["Chest fly / cable fly"].matched == "Cable Crossover"
    )  # 2nd choice used
    assert by_label["Face pull"].matched is None
    assert by_label["Seated cable row"].matched == "Seated Cable Rows"
    assert by_label["Seated cable row"].has_images is False
    report = plan_report(list(by_label.values()))
    assert report.count("MISSING") == 2  # the absent one and the one without images
    assert "(no images)" in report


# --- download (untrusted network data) --------------------------------------------------


def test_download_is_resumable_and_never_writes_outside_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(seed.time, "sleep", lambda _s: None)
    dataset = [
        {**entry("Alpha", "barbell", "chest")},
        {
            **entry("Beta", "barbell", "chest"),
            "images": ["../../escape.jpg", "/abs.jpg"],
        },
        {**entry("Gamma", "barbell", "chest"), "images": ["Gamma/0.jpg"]},
    ]
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        requested.append(path)
        assert seed.DATASET_REF in path  # always the pinned commit
        if path.endswith("/dist/exercises.json"):
            return httpx.Response(200, content=json.dumps(dataset).encode())
        if path.endswith("Gamma/0.jpg"):
            return httpx.Response(404)
        return httpx.Response(200, content=b"\xff\xd8\xff-fake-jpeg-bytes")

    cache = tmp_path / "cache"
    transport = httpx.MockTransport(handler)
    download_dataset(cache, workers=2, transport=transport)

    assert (cache / "exercises.json").exists()
    assert (cache / "exercises" / "Alpha" / "0.jpg").exists()
    assert (cache / "exercises" / "Alpha" / "1.jpg").exists()
    assert not (cache / "exercises" / "Gamma" / "0.jpg").exists()
    assert "Gamma/0.jpg" in capsys.readouterr().out  # failure is reported
    # Nothing escaped the cache directory, and unsafe paths were never even requested.
    assert not (tmp_path / "escape.jpg").exists()
    assert not any("escape" in p or "abs.jpg" in p for p in requested)

    requested.clear()
    download_dataset(cache, workers=2, transport=transport)
    # Second run: no JSON refetch, no re-download of cached images, only the failed one retries.
    assert not any(p.endswith("exercises.json") for p in requested)
    assert not any("Alpha" in p for p in requested)
    assert any("Gamma/0.jpg" in p for p in requested)
