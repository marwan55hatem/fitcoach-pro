from typing import Annotated

import sqlalchemy as sa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_owned_or_404, require_consent
from app.db.session import get_db
from app.main import create_app
from app.models import AuditLog, Consent, StoredFile, User
from app.models.enums import ConsentType, FileKind
from tests.helpers import register_and_login


def _by_type(res_json: dict) -> dict[str, dict]:
    return {c["type"]: c for c in res_json["consents"]}


def test_get_consents_lists_all_types_not_granted_by_default(
    client: TestClient,
) -> None:
    account = register_and_login(client)
    res = client.get("/consents", headers=account.headers)
    assert res.status_code == 200
    consents = _by_type(res.json())
    assert set(consents) == {t.value for t in ConsentType}
    assert not any(c["granted"] for c in consents.values())


def test_consents_require_auth(client: TestClient) -> None:
    assert client.get("/consents").status_code == 401
    assert (
        client.put("/consents", json={"consents": {"health_data": True}}).status_code
        == 401
    )


def test_grant_and_revoke_consent_keeps_history_and_audits(
    client: TestClient, db: Session
) -> None:
    account = register_and_login(client)

    res = client.put(
        "/consents",
        headers=account.headers,
        json={"consents": {"health_data": True, "ai_processing": True}},
    )
    assert res.status_code == 200
    consents = _by_type(res.json())
    assert consents["health_data"]["granted"] and consents["ai_processing"]["granted"]
    assert not consents["body_photos"]["granted"]

    # Granting twice does not create duplicate active rows.
    client.put(
        "/consents", headers=account.headers, json={"consents": {"health_data": True}}
    )

    res = client.put(
        "/consents", headers=account.headers, json={"consents": {"health_data": False}}
    )
    health = _by_type(res.json())["health_data"]
    assert health["granted"] is False and health["revoked_at"] is not None

    rows = db.scalars(
        sa.select(Consent).where(
            Consent.user_id == account.id, Consent.type == ConsentType.HEALTH_DATA
        )
    ).all()
    assert len(rows) == 1 and rows[0].revoked_at is not None  # revoked, not deleted

    actions = db.scalars(
        sa.select(AuditLog.action).where(AuditLog.user_id == account.id)
    ).all()
    assert "consent.granted" in actions and "consent.revoked" in actions

    # Re-granting after a revoke starts a fresh consent record.
    client.put(
        "/consents", headers=account.headers, json={"consents": {"health_data": True}}
    )
    assert _by_type(client.get("/consents", headers=account.headers).json())[
        "health_data"
    ]["granted"]


def test_consent_rejects_unknown_type(client: TestClient) -> None:
    account = register_and_login(client)
    res = client.put(
        "/consents", headers=account.headers, json={"consents": {"mind_reading": True}}
    )
    assert res.status_code == 422


def test_users_only_see_their_own_consents(client: TestClient) -> None:
    alice = register_and_login(client)
    bob = register_and_login(client)
    client.put(
        "/consents", headers=alice.headers, json={"consents": {"body_photos": True}}
    )
    assert (
        _by_type(client.get("/consents", headers=bob.headers).json())["body_photos"][
            "granted"
        ]
        is False
    )


def _protected_app(db: Session) -> FastAPI:
    """Stand-in for future photo/InBody/AI endpoints, wired exactly as they will be."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db

    @app.post("/_test/photos")
    def upload_photo(
        user: Annotated[User, Depends(require_consent(ConsentType.BODY_PHOTOS))],
    ) -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/_test/files/{file_id}")
    def get_file(
        file_id: str,
        user: Annotated[User, Depends(get_current_user)],
        session: Annotated[Session, Depends(get_db)],
    ) -> dict[str, str]:
        import uuid

        return {
            "id": str(
                get_owned_or_404(session, StoredFile, uuid.UUID(file_id), user).id
            )
        }

    return app


def test_require_consent_returns_403_with_clear_message(
    client: TestClient, db: Session
) -> None:
    account = register_and_login(client)
    guarded = TestClient(_protected_app(db))

    assert guarded.post("/_test/photos").status_code == 401  # not logged in

    denied = guarded.post("/_test/photos", headers=account.headers)
    assert denied.status_code == 403
    detail = denied.json()["detail"]
    assert detail["code"] == "consent_required" and detail["consent"] == "body_photos"
    assert "body_photos" in detail["message"] and "PUT /consents" in detail["message"]

    client.put(
        "/consents", headers=account.headers, json={"consents": {"body_photos": True}}
    )
    assert guarded.post("/_test/photos", headers=account.headers).status_code == 200

    # Consent for a different type does not unlock it; revoking locks it again.
    client.put(
        "/consents", headers=account.headers, json={"consents": {"body_photos": False}}
    )
    assert guarded.post("/_test/photos", headers=account.headers).status_code == 403


def test_cross_user_access_is_denied(client: TestClient, db: Session) -> None:
    alice = register_and_login(client)
    bob = register_and_login(client)
    f = StoredFile(
        user_id=alice.id,
        storage_key=f"users/{alice.id}/x",
        content_type="image/png",
        size_bytes=1,
        sha256="b" * 64,
        kind=FileKind.BODY_PHOTO,
    )
    db.add(f)
    db.flush()

    guarded = TestClient(_protected_app(db))
    assert guarded.get(f"/_test/files/{f.id}", headers=alice.headers).status_code == 200
    # Bob gets the same 404 as for a file that does not exist: no information leak.
    assert guarded.get(f"/_test/files/{f.id}", headers=bob.headers).status_code == 404
    assert guarded.get(f"/_test/files/{f.id}").status_code == 401
