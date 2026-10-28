import uuid
from dataclasses import dataclass

from fastapi.testclient import TestClient
from httpx import Response

PASSWORD = "correct-horse-battery"


@dataclass
class Account:
    id: str
    email: str
    access_token: str
    refresh_token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


def grant_consents(client: TestClient, account: Account, *types: str) -> None:
    res = client.put(
        "/consents",
        headers=account.headers,
        json={"consents": {t: True for t in types}},
    )
    assert res.status_code == 200, res.text


def delete_me(
    client: TestClient, account: Account, password: str = PASSWORD
) -> Response:
    """DELETE /me with the password confirmation body."""
    return client.request(
        "DELETE", "/me", headers=account.headers, json={"password": password}
    )


def register_and_login(client: TestClient, email: str | None = None) -> Account:
    email = email or f"{uuid.uuid4().hex[:10]}@example.com"
    reg = client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": "T"},
    )
    assert reg.status_code == 201, reg.text
    login = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    tokens = login.json()
    return Account(
        id=reg.json()["id"],
        email=email,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
    )
