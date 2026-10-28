import datetime as dt

import jwt
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.models import AuditLog, RefreshToken, User
from tests.helpers import PASSWORD, register_and_login


def test_register_creates_user_with_argon2_hash(
    client: TestClient, db: Session
) -> None:
    res = client.post(
        "/auth/register",
        json={
            "email": "New.User@Example.com",
            "password": PASSWORD,
            "display_name": "Nu",
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["email"] == "new.user@example.com"  # normalised
    assert "password" not in body and "password_hash" not in body

    stored = db.scalars(
        sa.select(User).where(User.email == "new.user@example.com")
    ).one()
    assert stored.password_hash.startswith("$argon2")
    assert PASSWORD not in stored.password_hash
    assert db.scalars(
        sa.select(AuditLog).where(AuditLog.action == "auth.register")
    ).first()


def test_register_duplicate_email_conflicts(client: TestClient) -> None:
    register_and_login(client, "dup@example.com")
    res = client.post(
        "/auth/register", json={"email": "DUP@example.com", "password": PASSWORD}
    )
    assert res.status_code == 409


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "a@example.com", "password": "short"},
    ],
)
def test_register_validates_input(client: TestClient, payload: dict[str, str]) -> None:
    assert client.post("/auth/register", json=payload).status_code == 422


def test_login_and_me(client: TestClient) -> None:
    account = register_and_login(client, "me@example.com")
    res = client.get("/auth/me", headers=account.headers)
    assert res.status_code == 200
    assert res.json()["email"] == "me@example.com"


def test_login_bad_password_and_unknown_user_look_identical(client: TestClient) -> None:
    register_and_login(client, "real@example.com")
    bad_pw = client.post(
        "/auth/login", json={"email": "real@example.com", "password": "wrong-password"}
    )
    unknown = client.post(
        "/auth/login", json={"email": "ghost@example.com", "password": "wrong-password"}
    )
    assert bad_pw.status_code == unknown.status_code == 401
    assert bad_pw.json() == unknown.json()


def test_me_requires_valid_token(client: TestClient) -> None:
    assert client.get("/auth/me").status_code == 401
    assert (
        client.get("/auth/me", headers={"Authorization": "Bearer garbage"}).status_code
        == 401
    )

    expired = jwt.encode(
        {
            "sub": "00000000-0000-0000-0000-000000000000",
            "type": "access",
            "exp": dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1),
        },
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    assert (
        client.get(
            "/auth/me", headers={"Authorization": f"Bearer {expired}"}
        ).status_code
        == 401
    )


def test_token_for_deleted_user_is_rejected(client: TestClient, db: Session) -> None:
    account = register_and_login(client)
    db.execute(sa.delete(User).where(User.email == account.email))
    assert client.get("/auth/me", headers=account.headers).status_code == 401


def test_refresh_token_is_stored_hashed(client: TestClient, db: Session) -> None:
    account = register_and_login(client)
    rows = db.scalars(sa.select(RefreshToken)).all()
    assert rows and all(account.refresh_token != r.token_hash for r in rows)


def test_refresh_rotates_and_old_token_stops_working(client: TestClient) -> None:
    account = register_and_login(client)
    first = client.post("/auth/refresh", json={"refresh_token": account.refresh_token})
    assert first.status_code == 200
    new_refresh = first.json()["refresh_token"]
    assert new_refresh != account.refresh_token
    assert (
        client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {first.json()['access_token']}"},
        ).status_code
        == 200
    )

    # Replaying the first (now used) token is rejected...
    replay = client.post("/auth/refresh", json={"refresh_token": account.refresh_token})
    assert replay.status_code == 401
    # ...and, because reuse signals theft, the new token in that family is revoked too.
    assert (
        client.post("/auth/refresh", json={"refresh_token": new_refresh}).status_code
        == 401
    )


def test_refresh_rejects_unknown_and_expired_tokens(
    client: TestClient, db: Session
) -> None:
    assert (
        client.post("/auth/refresh", json={"refresh_token": "nope"}).status_code == 401
    )

    account = register_and_login(client)
    db.execute(
        sa.update(RefreshToken).values(
            expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
        )
    )
    assert (
        client.post(
            "/auth/refresh", json={"refresh_token": account.refresh_token}
        ).status_code
        == 401
    )


def test_logout_revokes_refresh_token(client: TestClient) -> None:
    account = register_and_login(client)
    assert (
        client.post(
            "/auth/logout", json={"refresh_token": account.refresh_token}
        ).status_code
        == 204
    )
    assert (
        client.post(
            "/auth/refresh", json={"refresh_token": account.refresh_token}
        ).status_code
        == 401
    )


def test_auth_endpoints_are_rate_limited(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_auth", "3/minute")
    limiter.enabled = True
    limiter.reset()

    codes = [
        client.post(
            "/auth/login", json={"email": "x@example.com", "password": "wrong-password"}
        ).status_code
        for _ in range(5)
    ]
    assert codes[:3] == [401, 401, 401]
    assert codes[3:] == [429, 429]
