import io
import json
import uuid
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import (
    Exercise,
    Limitation,
    Profile,
    Program,
    ProgramDay,
    ProgramExercise,
)
from app.models.enums import BodyArea, Severity
from app.seed.exercises import import_exercises
from app.services.storage import Storage
from tests.exercise_fixtures import ENTRIES, write_dataset
from tests.helpers import Account, delete_me, register_and_login

CATALOG_SIZE = len(ENTRIES)


@pytest.fixture(autouse=True)
def _clean_catalog_storage(storage: Storage) -> None:
    storage.delete_prefix("catalog/")


@pytest.fixture
def library(db: Session, storage: Storage, tmp_path: Path) -> dict[str, Exercise]:
    data_dir = write_dataset(tmp_path / "data")
    entries = json.loads((data_dir / "exercises.json").read_text(encoding="utf-8"))
    import_exercises(db, storage, entries, data_dir / "exercises")
    return {
        e.name: e
        for e in db.scalars(sa.select(Exercise).where(Exercise.user_id.is_(None)))
    }


@pytest.fixture
def user(client: TestClient, library: dict[str, Exercise]) -> Account:
    return register_and_login(client)


def set_profile(db: Session, account: Account, equipment: list[str]) -> None:
    db.add(Profile(user_id=account.id, equipment=equipment))
    db.flush()


def add_limitation(
    db: Session,
    account: Account,
    area: BodyArea,
    severity: Severity,
    active: bool = True,
) -> None:
    db.add(
        Limitation(
            user_id=account.id, body_area=area.value, severity=severity, active=active
        )
    )
    db.flush()


def listing(client: TestClient, account: Account, **params) -> dict:
    res = client.get("/exercises", headers=account.headers, params=params)
    assert res.status_code == 200, res.text
    return res.json()


def names(payload: dict) -> list[str]:
    key = "items" if "items" in payload else "substitutes"
    return [item["name"] for item in payload[key]]


def subs(client: TestClient, account: Account, exercise: Exercise, **params) -> dict:
    res = client.get(
        f"/exercises/{exercise.id}/substitutes", headers=account.headers, params=params
    )
    assert res.status_code == 200, res.text
    return res.json()


# --- access ------------------------------------------------------------------------------


def test_every_route_requires_auth(
    client: TestClient, library: dict[str, Exercise]
) -> None:
    some_id = library["Pushups"].id
    assert client.get("/exercises").status_code == 401
    assert client.get(f"/exercises/{some_id}").status_code == 401
    assert client.get(f"/exercises/{some_id}/substitutes").status_code == 401
    assert client.post("/exercises", json={}).status_code == 401
    assert client.delete(f"/exercises/{some_id}").status_code == 401


# --- list and filters ---------------------------------------------------------------------


def test_list_returns_the_catalog_sorted_by_name(
    client: TestClient, user: Account
) -> None:
    payload = listing(client, user, limit=200)
    assert payload["total"] == CATALOG_SIZE and len(payload["items"]) == CATALOG_SIZE
    listed = names(payload)
    assert listed == sorted(listed, key=str.lower)
    first = payload["items"][0]
    assert {
        "id",
        "name",
        "slug",
        "muscle_primary",
        "muscles_secondary",
        "equipment",
        "level",
        "mechanic",
        "category",
        "is_custom",
        "image_url",
    } <= set(first)
    assert first["is_custom"] is False


def test_search_is_case_insensitive_substring_and_safe(
    client: TestClient, user: Account
) -> None:
    assert names(listing(client, user, q="dumbbell")) == [
        "Dumbbell Flyes",
        "Dumbbell Shoulder Press",
        "Incline Dumbbell Press",
    ]
    assert (
        names(listing(client, user, q="PRESS"))[0]
        == "Barbell Bench Press - Medium Grip"
    )
    assert listing(client, user, q="zzzzz")["total"] == 0
    # LIKE wildcards in user input are literal characters, not patterns.
    assert listing(client, user, q="%")["total"] == 0
    assert listing(client, user, q="_")["total"] == 0


def test_filters(client: TestClient, user: Account) -> None:
    chest = listing(client, user, muscle="chest")
    assert chest["total"] == 6 and all(
        i["muscle_primary"] == "chest" for i in chest["items"]
    )
    assert names(listing(client, user, equipment="cable")) == [
        "Cable Crossover",
        "Triceps Pushdown",
        "Wide-Grip Lat Pulldown",
    ]
    assert listing(client, user, level="advanced")["total"] == 1
    assert names(listing(client, user, mechanic="isolation")) == [
        "Butterfly",
        "Cable Crossover",
        "Dumbbell Flyes",
        "Seated Side Lateral Raise",
        "Triceps Pushdown",
    ]
    assert names(listing(client, user, category="stretching")) == ["Cat Stretch"]
    assert names(
        listing(
            client, user, muscle="chest", equipment="dumbbell", level="intermediate"
        )
    ) == ["Incline Dumbbell Press"]
    assert (
        listing(client, user, equipment="body_weight")["total"] == 3
    )  # incl. the stretch


@pytest.mark.parametrize(
    "params",
    [
        {"muscle": "pinky"},
        {"equipment": "jetpack"},
        {"level": "god"},
        {"mechanic": "magic"},
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"q": "x" * 101},
    ],
)
def test_list_validates_query_params(
    client: TestClient, user: Account, params: dict
) -> None:
    assert (
        client.get("/exercises", headers=user.headers, params=params).status_code == 422
    )


def test_pagination_is_stable_and_complete(client: TestClient, user: Account) -> None:
    seen: list[str] = []
    for offset in range(0, CATALOG_SIZE, 5):
        page = listing(client, user, limit=5, offset=offset)
        assert (
            page["total"] == CATALOG_SIZE
            and page["limit"] == 5
            and page["offset"] == offset
        )
        seen += names(page)
    assert seen == names(listing(client, user, limit=200))
    assert len(set(seen)) == CATALOG_SIZE  # no duplicates, nothing skipped
    assert listing(client, user, offset=500)["items"] == []


# --- images and detail --------------------------------------------------------------------


def test_signed_image_urls_work_and_are_short_lived(
    client: TestClient, user: Account
) -> None:
    item = next(i for i in listing(client, user, q="Pushups")["items"])
    url = item["image_url"]
    assert url and "X-Amz-Signature" in url
    assert parse_qs(urlparse(url).query)["X-Amz-Expires"] == ["300"]  # five minutes
    image = httpx.get(url)
    assert image.status_code == 200 and image.content.startswith(b"\xff\xd8")

    # The object itself is private: the bare URL without a signature is refused.
    assert httpx.get(url.split("?")[0]).status_code in (401, 403)


def test_detail_has_steps_images_equipment_and_license(
    client: TestClient, user: Account, library: dict[str, Exercise]
) -> None:
    res = client.get(
        f"/exercises/{library['Incline Dumbbell Press'].id}", headers=user.headers
    )
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Incline Dumbbell Press"
    assert body["instructions"] == [
        "Step one of Incline Dumbbell Press.",
        "Step two of Incline Dumbbell Press.",
    ]
    assert len(body["image_urls"]) == 2 and body["image_url"] == body["image_urls"][0]
    assert body["required_equipment"] == ["bench", "dumbbell"]
    assert (
        body["license"] == "Unlicense (public domain)"
        and "free-exercise-db" in body["source"]
    )
    assert "yuhonas" in body["attribution"]
    assert all(httpx.get(u).status_code == 200 for u in body["image_urls"])


def test_exercise_without_images_has_no_urls(
    client: TestClient, user: Account, library: dict[str, Exercise]
) -> None:
    body = client.get(
        f"/exercises/{library['Cat Stretch'].id}", headers=user.headers
    ).json()
    assert body["image_url"] is None and body["image_urls"] == []


def test_unknown_and_malformed_ids(client: TestClient, user: Account) -> None:
    assert (
        client.get(f"/exercises/{uuid.uuid4()}", headers=user.headers).status_code
        == 404
    )
    assert client.get("/exercises/not-a-uuid", headers=user.headers).status_code == 422
    assert (
        client.get(
            f"/exercises/{uuid.uuid4()}/substitutes", headers=user.headers
        ).status_code
        == 404
    )


# --- custom exercises ---------------------------------------------------------------------

CUSTOM = {
    "name": "My Cable Squat",
    "muscle_primary": "quads",
    "muscles_secondary": ["glutes", "glutes"],
    "equipment": "cable",
    "level": "beginner",
    "mechanic": "compound",
    "instructions": ["Hold the handle.", "Squat down."],
}


def test_custom_exercise_lifecycle_and_privacy(
    client: TestClient, user: Account
) -> None:
    created = client.post("/exercises", headers=user.headers, json=CUSTOM)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["is_custom"] is True and body["slug"] == "my-cable-squat"
    assert body["muscles_secondary"] == ["glutes"]  # de-duplicated
    assert body["category"] == "strength" and body["image_urls"] == []
    assert body["required_equipment"] == ["cable"]

    # Visible to the owner in list, search and detail...
    assert "My Cable Squat" in names(listing(client, user, q="cable squat"))
    assert (
        client.get(f"/exercises/{body['id']}", headers=user.headers).status_code == 200
    )

    # ...and invisible, not merely forbidden, to everyone else.
    other = register_and_login(client)
    assert "My Cable Squat" not in names(listing(client, other, limit=200))
    assert listing(client, other, q="cable squat")["total"] == 0
    assert (
        client.get(f"/exercises/{body['id']}", headers=other.headers).status_code == 404
    )
    assert (
        client.get(
            f"/exercises/{body['id']}/substitutes", headers=other.headers
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/exercises/{body['id']}", headers=other.headers).status_code
        == 404
    )
    assert (
        client.get(f"/exercises/{body['id']}", headers=user.headers).status_code == 200
    )

    # Owner can delete it.
    assert (
        client.delete(f"/exercises/{body['id']}", headers=user.headers).status_code
        == 204
    )
    assert (
        client.get(f"/exercises/{body['id']}", headers=user.headers).status_code == 404
    )


def test_custom_slugs_are_unique_per_user_and_may_reuse_catalog_slugs(
    client: TestClient, user: Account, library: dict[str, Exercise]
) -> None:
    slugs = [
        client.post(
            "/exercises", headers=user.headers, json={**CUSTOM, "name": "Dup"}
        ).json()["slug"]
        for _ in range(3)
    ]
    assert slugs == ["dup", "dup-2", "dup-3"]
    # A custom exercise may share a slug with a catalog exercise (different owner scope).
    clash = client.post(
        "/exercises", headers=user.headers, json={**CUSTOM, "name": "Pushups"}
    )
    assert clash.status_code == 201 and clash.json()["slug"] == "pushups"
    # Another user gets their own "dup".
    other = register_and_login(client)
    assert (
        client.post(
            "/exercises", headers=other.headers, json={**CUSTOM, "name": "Dup"}
        ).json()["slug"]
        == "dup"
    )


@pytest.mark.parametrize(
    "override",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 101},
        {"muscle_primary": "pinky"},
        {"muscles_secondary": ["pinky"]},
        {"equipment": "jetpack"},
        {"level": "godlike"},
        {"instructions": ["step"] * 21},
        {"instructions": ["x" * 501]},
    ],
)
def test_custom_exercise_validation(
    client: TestClient, user: Account, override: dict
) -> None:
    res = client.post("/exercises", headers=user.headers, json={**CUSTOM, **override})
    assert res.status_code == 422


def test_catalog_exercises_cannot_be_deleted(
    client: TestClient, user: Account, library: dict[str, Exercise]
) -> None:
    res = client.delete(f"/exercises/{library['Pushups'].id}", headers=user.headers)
    assert res.status_code == 404
    assert (
        client.get(
            f"/exercises/{library['Pushups'].id}", headers=user.headers
        ).status_code
        == 200
    )


def test_exercise_used_by_a_program_cannot_be_deleted(
    client: TestClient, db: Session, user: Account
) -> None:
    custom = client.post("/exercises", headers=user.headers, json=CUSTOM).json()
    program = Program(user_id=user.id, version=1, generated_by="user")
    day = ProgramDay(user_id=user.id, day_index=1, name="Legs")
    day.exercises.append(
        ProgramExercise(
            user_id=user.id,
            exercise_id=custom["id"],
            order=1,
            sets=3,
            rep_min=8,
            rep_max=12,
        )
    )
    program.days.append(day)
    db.add(program)
    db.flush()

    res = client.delete(f"/exercises/{custom['id']}", headers=user.headers)
    assert res.status_code == 409
    assert (
        client.get(f"/exercises/{custom['id']}", headers=user.headers).status_code
        == 200
    )


# --- substitutes --------------------------------------------------------------------------


def test_substitutes_use_profile_equipment_by_default(
    client: TestClient, db: Session, user: Account, library: dict[str, Exercise]
) -> None:
    bench = library["Barbell Bench Press - Medium Grip"]

    # No profile: bodyweight only.
    assert names(subs(client, user, bench)) == ["Pushups"]

    set_profile(db, user, ["dumbbell", "bench"])
    result = subs(client, user, bench)
    assert names(result) == ["Incline Dumbbell Press", "Pushups", "Dumbbell Flyes"]
    assert result["equipment_used"] == ["dumbbell", "bench"]
    assert bench.name not in names(result)

    # Without the bench, the incline press (needs a bench) disappears.
    db.execute(
        sa.update(Profile)
        .where(Profile.user_id == user.id)
        .values(equipment=["dumbbell"])
    )
    assert "Incline Dumbbell Press" not in names(subs(client, user, bench))


def test_substitutes_equipment_parameter_overrides_the_profile(
    client: TestClient, db: Session, user: Account, library: dict[str, Exercise]
) -> None:
    set_profile(db, user, ["dumbbell", "bench"])
    result = subs(
        client,
        user,
        library["Barbell Bench Press - Medium Grip"],
        equipment=["cable", "machine"],
    )
    # Scores: Pushups 5 (compound, shares both secondaries), Crossover 2, Butterfly 1.
    assert names(result) == ["Pushups", "Cable Crossover", "Butterfly"]
    assert result["equipment_used"] == ["cable", "machine"]


def test_substitutes_are_same_muscle_and_strength_only(
    client: TestClient, db: Session, user: Account, library: dict[str, Exercise]
) -> None:
    set_profile(
        db, user, ["barbell", "dumbbell", "cable", "machine", "bench", "pull_up_bar"]
    )
    for target in ("Barbell Full Squat", "Pullups", "Romanian Deadlift"):
        muscle = library[target].muscle_primary
        for item in subs(client, user, library[target])["substitutes"]:
            assert item["muscle_primary"] == muscle and item["name"] != target
            assert item["category"] in ("strength", "powerlifting")
    # A stretch target is not offered strength swaps.
    assert "Romanian Deadlift" not in names(subs(client, user, library["Cat Stretch"]))


def test_substitutes_skip_exercises_ruled_out_by_active_limitations(
    client: TestClient, db: Session, user: Account, library: dict[str, Exercise]
) -> None:
    set_profile(db, user, ["barbell", "dumbbell", "bench"])
    ohp = library["Barbell Shoulder Press"]
    # Scores: Military 7 (same barbell), Dumbbell press 5, lateral raise 0.
    assert names(subs(client, user, ohp)) == [
        "Standing Military Press",
        "Dumbbell Shoulder Press",
        "Seated Side Lateral Raise",
    ]

    add_limitation(db, user, BodyArea.SHOULDER, Severity.MODERATE)
    result = subs(client, user, ohp)
    # Compound shoulder lifts and the military press are out; the isolation raise stays.
    assert names(result) == ["Seated Side Lateral Raise"]
    assert result["avoid_areas_used"] == ["shoulder"]


def test_inactive_limitations_do_not_apply(
    client: TestClient, db: Session, user: Account, library: dict[str, Exercise]
) -> None:
    set_profile(db, user, ["barbell", "dumbbell"])
    add_limitation(db, user, BodyArea.SHOULDER, Severity.SEVERE, active=False)
    result = subs(client, user, library["Barbell Shoulder Press"])
    assert (
        "Dumbbell Shoulder Press" in names(result) and result["avoid_areas_used"] == []
    )


def test_avoid_areas_adds_to_limitations_and_never_replaces_them(
    client: TestClient, db: Session, user: Account, library: dict[str, Exercise]
) -> None:
    set_profile(db, user, ["barbell", "dumbbell"])
    ohp = library["Barbell Shoulder Press"]
    # Explicit area alone (no stored limitation) is enough.
    assert names(subs(client, user, ohp, avoid_areas=["shoulder"])) == [
        "Seated Side Lateral Raise"
    ]

    # A stored shoulder limitation still applies when the request names a different area.
    add_limitation(db, user, BodyArea.SHOULDER, Severity.MODERATE)
    result = subs(client, user, ohp, avoid_areas=["knee"])
    assert names(result) == ["Seated Side Lateral Raise"]
    assert result["avoid_areas_used"] == ["shoulder", "knee"]


def test_another_users_limitations_never_leak_in(
    client: TestClient, db: Session, user: Account, library: dict[str, Exercise]
) -> None:
    set_profile(db, user, ["barbell", "dumbbell"])
    other = register_and_login(client)
    add_limitation(db, other, BodyArea.SHOULDER, Severity.SEVERE)
    assert "Dumbbell Shoulder Press" in names(
        subs(client, user, library["Barbell Shoulder Press"])
    )


def test_substitutes_param_validation_and_limit(
    client: TestClient, db: Session, user: Account, library: dict[str, Exercise]
) -> None:
    bench = library["Barbell Bench Press - Medium Grip"]
    base = f"/exercises/{bench.id}/substitutes"
    for params in (
        {"equipment": "jetpack"},
        {"avoid_areas": "elbowz"},
        {"limit": 0},
        {"limit": 31},
    ):
        assert client.get(base, headers=user.headers, params=params).status_code == 422
    set_profile(db, user, ["dumbbell", "bench", "cable", "machine"])
    assert len(subs(client, user, bench, limit=2)["substitutes"]) == 2


# --- privacy: export and delete -----------------------------------------------------------


def test_export_contains_only_the_users_custom_exercises(
    client: TestClient, user: Account
) -> None:
    client.post("/exercises", headers=user.headers, json=CUSTOM)
    other = register_and_login(client)
    client.post(
        "/exercises",
        headers=other.headers,
        json={**CUSTOM, "name": "Other Secret Move"},
    )

    archive = zipfile.ZipFile(
        io.BytesIO(client.get("/me/export", headers=user.headers).content)
    )
    exported = json.loads(archive.read("data/exercises.json"))
    assert [e["name"] for e in exported] == [
        "My Cable Squat"
    ]  # not the catalog, not others'
    assert b"Other Secret Move" not in archive.read("data/exercises.json")


def test_deleting_an_account_removes_custom_exercises_but_not_the_catalog(
    client: TestClient, db: Session, user: Account, storage: Storage
) -> None:
    custom = client.post("/exercises", headers=user.headers, json=CUSTOM).json()
    assert delete_me(client, user).status_code == 204
    assert db.get(Exercise, uuid.UUID(custom["id"])) is None
    assert (
        db.scalar(
            sa.select(sa.func.count())
            .select_from(Exercise)
            .where(Exercise.user_id.is_(None))
        )
        == CATALOG_SIZE
    )
    assert storage.exists("catalog/exercises/pushups/0.jpg")
