import datetime as dt
import io

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from app.models import ProgressPhoto, StoredFile
from app.services.storage import Storage
from tests.helpers import Account, grant_consents, register_and_login


@pytest.fixture
def user(client: TestClient) -> Account:
    account = register_and_login(client)
    grant_consents(client, account, "health_data", "body_photos")
    return account


def jpeg(color: str = "gray") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (600, 900), color).save(out, "JPEG")
    return out.getvalue()


def upload(client: TestClient, account: Account, kind: str = "body_photo") -> str:
    res = client.post(
        f"/files?kind={kind}",
        headers={**account.headers, "content-type": "application/octet-stream"},
        content=jpeg(),
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def add(client: TestClient, account: Account, view: str = "front", **body):
    return client.post(
        "/progress-photos",
        headers=account.headers,
        json={"file_id": upload(client, account), "view": view, **body},
    )


def test_add_and_list_with_private_links(client: TestClient, user: Account) -> None:
    earlier = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    res = add(client, user, "front", taken_on=earlier, notes="start of the bulk")
    assert res.status_code == 201, res.text
    first = res.json()
    assert (first["view"], first["taken_on"], first["notes"]) == (
        "front",
        earlier,
        "start of the bulk",
    )
    assert first["expires_in"] == 300
    assert httpx.get(first["thumbnail_url"]).status_code == 200
    assert add(client, user, "side").status_code == 201

    listed = client.get("/progress-photos", headers=user.headers).json()
    assert [p["view"] for p in listed] == ["side", "front"]  # newest first
    fronts = client.get("/progress-photos?view=front", headers=user.headers).json()
    assert [p["id"] for p in fronts] == [first["id"]]


def test_a_second_photo_of_the_same_view_and_day_replaces_the_first(
    client: TestClient, db: Session, storage: Storage, user: Account
) -> None:
    old = add(client, user, "back").json()
    old_file = db.get(StoredFile, old["file_id"])
    assert old_file is not None
    old_key = old_file.storage_key
    new = add(client, user, "back").json()
    listed = client.get("/progress-photos?view=back", headers=user.headers).json()
    assert [p["id"] for p in listed] == [new["id"]]
    assert not storage.exists(old_key)  # the replaced photo is gone, not orphaned


def test_delete_removes_the_photo_file(
    client: TestClient, db: Session, storage: Storage, user: Account
) -> None:
    photo = add(client, user).json()
    stored = db.get(StoredFile, photo["file_id"])
    assert stored is not None
    keys = [stored.storage_key, stored.thumbnail_key]
    res = client.delete(f"/progress-photos/{photo['id']}", headers=user.headers)
    assert res.status_code == 204
    db.expire_all()
    assert db.get(ProgressPhoto, photo["id"]) is None
    assert not any(storage.exists(k) for k in keys if k)


def test_needs_the_body_photos_consent(client: TestClient) -> None:
    account = register_and_login(client)
    grant_consents(client, account, "health_data")
    assert client.get("/progress-photos", headers=account.headers).status_code == 403


def test_only_body_photo_files(client: TestClient, user: Account) -> None:
    res = client.post(
        "/progress-photos",
        headers=user.headers,
        json={"file_id": upload(client, user, kind="food"), "view": "front"},
    )
    assert res.status_code == 422 and res.json()["detail"]["code"] == "wrong_file_kind"
    future = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    assert add(client, user, taken_on=future).status_code == 422


def test_photos_are_private(client: TestClient, user: Account) -> None:
    photo = add(client, user).json()
    other = register_and_login(client)
    grant_consents(client, other, "health_data", "body_photos")
    assert client.get("/progress-photos", headers=other.headers).json() == []
    res = client.delete(f"/progress-photos/{photo['id']}", headers=other.headers)
    assert res.status_code == 404
    res = client.post(
        "/progress-photos",
        headers=other.headers,
        json={"file_id": photo["file_id"], "view": "side"},
    )
    assert res.status_code == 404
