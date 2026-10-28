import datetime as dt
import io
import json
import uuid
import zipfile
from decimal import Decimal

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    Consent,
    InbodyScan,
    MealLog,
    RefreshToken,
    StoredFile,
    User,
    WeightLog,
)
from app.models.enums import FileKind, MealSlot, MealSource
from app.services.storage import Storage, user_prefix
from tests.helpers import register_and_login

TODAY = dt.date(2026, 10, 28)
IMAGE_BYTES = b"\x89PNG-fake-image-bytes"


def seed_user_data(db: Session, storage: Storage, user_id: str) -> list[str]:
    """Health rows plus real objects in storage. Returns the object keys."""
    keys = []
    files = []
    for kind in (FileKind.INBODY, FileKind.BODY_PHOTO):
        key = f"{user_prefix(user_id)}{kind.value}/{uuid.uuid4()}.png"
        storage.put_bytes(key, IMAGE_BYTES + kind.value.encode(), "image/png")
        f = StoredFile(
            user_id=user_id,
            storage_key=key,
            content_type="image/png",
            size_bytes=len(IMAGE_BYTES),
            sha256="c" * 64,
            kind=kind,
        )
        db.add(f)
        files.append(f)
        keys.append(key)
    db.flush()
    db.add_all(
        [
            WeightLog(user_id=user_id, date=TODAY, weight_kg=Decimal("79.5")),
            InbodyScan(
                user_id=user_id,
                taken_on=TODAY,
                file_id=files[0].id,
                weight_kg=Decimal("79.5"),
            ),
            MealLog(
                user_id=user_id,
                date=TODAY,
                slot=MealSlot.LUNCH,
                source=MealSource.MANUAL,
                name="Chicken and rice",
                kcal=700,
            ),
        ]
    )
    db.flush()
    return keys


def _zip(res_content: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(res_content))


def test_export_contains_data_files_and_no_secrets(
    client: TestClient, db: Session, storage: Storage
) -> None:
    alice = register_and_login(client, "alice@example.com")
    bob = register_and_login(client, "bob@example.com")
    seed_user_data(db, storage, alice.id)
    seed_user_data(db, storage, bob.id)
    client.put(
        "/consents", headers=alice.headers, json={"consents": {"health_data": True}}
    )

    res = client.get("/me/export", headers=alice.headers)
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"
    assert res.headers["cache-control"] == "no-store"
    archive = _zip(res.content)
    names = set(archive.namelist())

    def table(name: str) -> list[dict]:
        return json.loads(archive.read(f"data/{name}.json"))

    assert table("users")[0]["email"] == "alice@example.com"
    assert "password_hash" not in table("users")[0]
    assert float(table("weight_logs")[0]["weight_kg"]) == 79.5
    assert table("meal_logs")[0]["name"] == "Chicken and rice"
    assert len(table("inbody_scans")) == 1
    assert len(table("consents")) == 1
    assert len(table("files")) == 2

    stored = [n for n in names if n.startswith("files/")]
    assert len(stored) == 2 and all(n.endswith(".png") for n in stored)
    assert all(archive.read(n).startswith(IMAGE_BYTES) for n in stored)

    # Credentials and other users' data never appear anywhere in the archive.
    assert "data/refresh_tokens.json" not in names
    everything = b"".join(archive.read(n) for n in names if n.startswith("data/"))
    assert b"bob@example.com" not in everything and bob.id.encode() not in everything
    assert (
        b"argon2" not in everything and alice.refresh_token.encode() not in everything
    )

    manifest = json.loads(archive.read("manifest.json"))
    assert (
        manifest["files_exported"] == 2 and manifest["files_missing_from_storage"] == []
    )

    assert db.scalars(
        sa.select(AuditLog).where(
            AuditLog.user_id == alice.id, AuditLog.action == "account.export"
        )
    ).first()


def test_export_reports_files_missing_from_storage(
    client: TestClient, db: Session, storage: Storage
) -> None:
    alice = register_and_login(client)
    keys = seed_user_data(db, storage, alice.id)
    storage.delete(keys[0])

    archive = _zip(client.get("/me/export", headers=alice.headers).content)
    manifest = json.loads(archive.read("manifest.json"))
    assert (
        manifest["files_exported"] == 1
        and len(manifest["files_missing_from_storage"]) == 1
    )


def test_export_requires_auth(client: TestClient) -> None:
    assert client.get("/me/export").status_code == 401


def test_delete_removes_db_rows_and_storage_objects(
    client: TestClient, db: Session, storage: Storage
) -> None:
    alice = register_and_login(client, "alice@example.com")
    bob = register_and_login(client, "bob@example.com")
    alice_keys = seed_user_data(db, storage, alice.id)
    bob_keys = seed_user_data(db, storage, bob.id)
    orphan = f"{user_prefix(alice.id)}chat/orphan-not-in-db.png"
    storage.put_bytes(orphan, b"orphan", "image/png")  # object with no files row
    client.put(
        "/consents", headers=alice.headers, json={"consents": {"health_data": True}}
    )
    assert all(storage.exists(k) for k in [*alice_keys, orphan])

    res = client.delete("/me", headers=alice.headers)
    assert res.status_code == 204

    # Every DB row owned by alice is gone...
    for model in (User,):
        assert db.scalars(sa.select(model).where(model.id == alice.id)).first() is None
    for model in (StoredFile, WeightLog, InbodyScan, MealLog, Consent, RefreshToken):
        assert (
            db.scalars(sa.select(model).where(model.user_id == alice.id)).first()
            is None
        )
    # ...and so is every stored object, including the one the DB did not know about.
    assert not any(storage.exists(k) for k in [*alice_keys, orphan])

    # Bob is untouched, rows and objects.
    assert db.scalars(sa.select(WeightLog).where(WeightLog.user_id == bob.id)).one()
    assert all(storage.exists(k) for k in bob_keys)

    # Alice's tokens are dead.
    assert client.get("/auth/me", headers=alice.headers).status_code == 401
    assert (
        client.post(
            "/auth/refresh", json={"refresh_token": alice.refresh_token}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "correct-horse-battery"},
        ).status_code
        == 401
    )


def test_delete_writes_audit_entry_without_personal_content(
    client: TestClient, db: Session, storage: Storage
) -> None:
    alice = register_and_login(client, "alice@example.com")
    seed_user_data(db, storage, alice.id)
    client.delete("/me", headers=alice.headers)

    entry = db.scalars(
        sa.select(AuditLog).where(AuditLog.action == "account.delete")
    ).one()
    assert entry.user_id is None
    assert entry.entity_type == "user" and str(entry.entity_id) == alice.id
    assert entry.meta == {"files_deleted": 2, "orphans_swept": 0}
    assert "alice" not in json.dumps(entry.meta)

    # Earlier audit rows about alice (e.g. registration) are anonymised, not left linked.
    assert (
        db.scalars(sa.select(AuditLog).where(AuditLog.user_id == alice.id)).first()
        is None
    )


def test_delete_keeps_account_if_storage_fails(
    client: TestClient, db: Session, storage: Storage, monkeypatch
) -> None:
    alice = register_and_login(client)
    seed_user_data(db, storage, alice.id)

    def boom(self: Storage, key: str) -> None:
        raise RuntimeError("storage down")

    monkeypatch.setattr(Storage, "delete", boom)
    failing = TestClient(client.app, raise_server_exceptions=False)
    assert failing.delete("/me", headers=alice.headers).status_code == 500
    monkeypatch.undo()

    # Nothing was deleted from the DB, so the request can be retried.
    assert db.get(User, alice.id) is not None
    assert client.delete("/me", headers=alice.headers).status_code == 204


def test_delete_requires_auth(client: TestClient) -> None:
    assert client.delete("/me").status_code == 401
