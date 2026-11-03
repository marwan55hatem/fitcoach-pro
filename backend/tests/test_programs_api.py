import logging
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai.client import AiClient, get_ai_client
from app.ai.errors import AiProviderError, AiTimeoutError
from app.ai.program_ai import AiDay, AiExercise, AiProgram
from app.ai.types import AssistantMessage, UserMessage
from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.models import AuditLog, Exercise, Limitation, Profile, Program, ProgramDay
from app.models.enums import BodyArea, ProgramStatus, Severity
from app.services.storage import Storage, get_storage
from tests.fakes.ai import FakeProvider
from tests.helpers import Account, delete_me, grant_consents, register_and_login
from tests.program_fixtures import add_library
from tests.test_onboarding import profile_payload, screen


@pytest.fixture
def library(db: Session) -> dict[str, Exercise]:
    return add_library(db)


@pytest.fixture
def fake() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def client(db: Session, storage: Storage, fake: FakeProvider) -> Iterator[TestClient]:
    app = create_app()
    settings = Settings(_env_file=None)
    ai = AiClient(fake, settings, lambda: nullcontext(db), sleep=lambda _s: None)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_ai_client] = lambda: ai
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def user(client: TestClient, library: dict[str, Exercise]) -> Account:
    account = register_and_login(client)
    grant_consents(client, account, "health_data", "ai_processing")
    equipment = ["barbell", "dumbbell", "ez_bar", "cable", "machine", "bench"]
    res = client.put(
        "/onboarding/profile",
        headers=account.headers,
        json=profile_payload(equipment=equipment, session_minutes=75),
    )
    assert res.status_code == 200, res.text
    res = client.post("/onboarding/screening", headers=account.headers, json=screen())
    assert res.status_code == 201, res.text
    return account


def post(client: TestClient, account: Account, path: str, body: Any = None):
    return client.post(f"/programs{path}", headers=account.headers, json=body or {})


def generate(client: TestClient, account: Account, **body: Any) -> dict[str, Any]:
    res = post(client, account, "/generate", body)
    assert res.status_code == 201, res.text
    return res.json()


def ref(fake: FakeProvider, name: str) -> int:
    """The number the AI would use for this exercise in the prompt it was sent."""
    text = fake.calls[0].messages[0].text
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) == 5 and parts[1] == name:
            return int(parts[0])
    raise AssertionError(f"{name} not offered to the AI")


def ai_program(refs: list[int], *, days: int = 4, sets: int = 4) -> AiProgram:
    exercises = [
        AiExercise(
            exercise_ref=r, sets=sets, rep_min=6, rep_max=10, rest_s=120, notes="Brace."
        )
        for r in refs
    ]
    return AiProgram(
        rationale="A balanced week.",
        days=[
            AiDay(name=f"Day {i}", focus="Focus", rationale="Why.", exercises=exercises)
            for i in range(1, days + 1)
        ],
    )


class AnswerWith(FakeProvider):
    """Answers using the numbers from the prompt, which only exist once it is sent."""

    def __init__(self, build: Any) -> None:
        super().__init__()
        self.build = build

    def structured(self, **kwargs: Any) -> Any:
        self.calls.append(type("Call", (), {"messages": list(kwargs["messages"])})())
        program = self.build(self)
        self.queue_structured(program)
        return self._next()


def use_fake(client: TestClient, db: Session, provider: FakeProvider) -> None:
    ai = AiClient(
        provider,
        Settings(_env_file=None),
        lambda: nullcontext(db),
        sleep=lambda _s: None,
    )
    client.app.dependency_overrides[get_ai_client] = lambda: ai


def valid_ai(provider: FakeProvider) -> AiProgram:
    refs = [
        ref(provider, "Barbell Bench Press - Medium Grip"),
        ref(provider, "Bent Over Barbell Row"),
    ]
    return ai_program(refs, days=2)


# --- access -----------------------------------------------------------------------------


def test_routes_require_auth(client: TestClient) -> None:
    some_id = uuid.uuid4()
    assert client.post("/programs/generate", json={}).status_code == 401
    assert client.get("/programs/current").status_code == 401
    assert client.get(f"/programs/{some_id}/preview").status_code == 401
    assert client.post(f"/programs/{some_id}/accept").status_code == 401


def test_generate_needs_finished_onboarding(
    client: TestClient, library: dict[str, Exercise]
) -> None:
    account = register_and_login(client)
    grant_consents(client, account, "health_data", "ai_processing")
    res = post(client, account, "/generate")
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "plan_access_denied"


def test_blocked_screening_cannot_generate_until_clearance(
    client: TestClient, user: Account, fake: FakeProvider
) -> None:
    headers = user.headers
    client.post("/onboarding/screening", headers=headers, json=screen(chest_pain=True))
    res = post(client, user, "/generate", {"use_ai": False})
    assert res.status_code == 403
    assert res.json()["detail"]["reasons"] == ["clearance_required"]
    client.post("/onboarding/clearance", headers=headers, json={"confirm": True})
    assert post(client, user, "/generate", {"use_ai": False}).status_code == 201


def test_ai_needs_ai_consent_but_rules_only_does_not(
    client: TestClient, user: Account, fake: FakeProvider
) -> None:
    client.put(
        "/consents", headers=user.headers, json={"consents": {"ai_processing": False}}
    )
    denied = post(client, user, "/generate")
    assert denied.status_code == 403
    assert denied.json()["detail"]["consent"] == "ai_processing"
    assert fake.calls == []  # nothing left the server

    res = post(client, user, "/generate", {"use_ai": False})
    assert res.status_code == 201
    assert res.json()["source"] == "template" and fake.calls == []


# --- generation with the AI -------------------------------------------------------------


def test_valid_ai_program_is_stored_as_a_draft_only(
    client: TestClient, db: Session, user: Account
) -> None:
    use_fake(client, db, AnswerWith(valid_ai))
    body = generate(client, user)

    assert body["source"] == "ai" and body["fallback_reason"] is None
    assert body["generated_by"] == "ai" and body["status"] == "draft"
    assert body["rationale"] == "A balanced week."
    assert body["version"] == 1 and len(body["days"]) == 2
    day = body["days"][0]
    assert day["rationale"] == "Why." and day["estimated_minutes"] > 5
    assert day["exercises"][0]["name"] == "Barbell Bench Press - Medium Grip"
    assert day["exercises"][0]["notes"] == "Brace."
    assert body["errors"] == []
    assert {w["code"] for w in body["warnings"]} == {"volume_low"}

    # Nothing is active until the user accepts it.
    assert client.get("/programs/current", headers=user.headers).status_code == 404
    assert db.scalars(sa.select(AuditLog.action)).all().count("program.generated") == 1


def test_the_prompt_carries_no_identity_or_injury_text(
    client: TestClient, db: Session, user: Account
) -> None:
    db.add(
        Limitation(
            user_id=user.id,
            body_area=BodyArea.OTHER.value,
            severity=Severity.MILD,
            description="SENTINEL private injury story",
        )
    )
    db.flush()
    provider = AnswerWith(valid_ai)
    use_fake(client, db, provider)
    generate(client, user)

    message = provider.calls[0].messages[0]
    assert isinstance(message, UserMessage)
    assert user.email not in message.text and str(user.id) not in message.text
    assert "SENTINEL" not in message.text
    profile = db.scalars(sa.select(Profile).where(Profile.user_id == user.id)).one()
    assert profile.birth_date.isoformat() not in message.text
    assert '"age": 30' in message.text or '"age": 29' in message.text


def test_invalid_ai_program_gets_one_retry_with_the_violations(
    client: TestClient, db: Session, user: Account
) -> None:
    answers = iter([[9999], None])

    def build(provider: FakeProvider) -> AiProgram:
        bad = next(answers)
        return ai_program(bad, days=1) if bad else valid_ai(provider)

    provider = AnswerWith(build)
    use_fake(client, db, provider)
    body = generate(client, user)

    assert body["source"] == "ai" and len(provider.calls) == 2
    retry = provider.calls[1].messages
    assert isinstance(retry[1], AssistantMessage)
    assert "not in the allowed list" in retry[2].text


def test_ai_that_keeps_breaking_rules_falls_back_to_the_template(
    client: TestClient, db: Session, user: Account
) -> None:
    provider = AnswerWith(lambda _p: ai_program([9999], days=1))
    use_fake(client, db, provider)
    body = generate(client, user)

    assert len(provider.calls) == 2  # exactly one retry
    assert body["source"] == "template" and body["fallback_reason"] == "ai_invalid"
    assert body["generated_by"] == "template" and len(body["days"]) == 4
    assert body["errors"] == []


def test_unsafe_ai_volume_is_rejected(
    client: TestClient, db: Session, user: Account
) -> None:
    def too_much(provider: FakeProvider) -> AiProgram:
        return ai_program(
            [ref(provider, "Barbell Bench Press - Medium Grip")], days=4, sets=8
        )

    use_fake(client, db, AnswerWith(too_much))
    body = generate(client, user)
    assert body["source"] == "template" and body["fallback_reason"] == "ai_invalid"


@pytest.mark.parametrize("error", [AiTimeoutError, AiProviderError])
def test_ai_failure_falls_back_to_the_template(
    client: TestClient, user: Account, fake: FakeProvider, error: type[Exception]
) -> None:
    for _ in range(4):
        fake.queue_error(error())
    body = generate(client, user)
    assert body["source"] == "template"
    assert body["fallback_reason"] == "ai_unavailable"


def test_a_missing_ai_key_still_gives_a_program(
    client: TestClient, db: Session, user: Account
) -> None:
    use_fake(client, db, None)
    body = generate(client, user)
    assert body["source"] == "template"
    assert body["fallback_reason"] == "ai_unavailable"


def test_injured_users_never_get_excluded_exercises(
    client: TestClient, db: Session, user: Account, fake: FakeProvider
) -> None:
    client.put(
        "/onboarding/limitations",
        headers=user.headers,
        json={"limitations": [{"body_area": "knee", "severity": "moderate"}]},
    )
    fake.queue_error(AiProviderError())
    body = generate(client, user)
    names = {e["name"] for d in body["days"] for e in d["exercises"]}
    assert "Barbell Full Squat" not in names and "Leg Press" not in names


def test_logs_never_contain_the_prompt_or_program(
    client: TestClient,
    db: Session,
    user: Account,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    use_fake(client, db, AnswerWith(lambda _p: ai_program([9999], days=1)))
    generate(client, user)
    assert "program_ai_rejected" in caplog.text  # the metadata is there
    assert "unknown_exercise" in caplog.text
    assert "A balanced week." not in caplog.text
    assert "Barbell Bench Press" not in caplog.text


# --- template selection -----------------------------------------------------------------


def test_rules_only_uses_the_default_four_day_split(
    client: TestClient, user: Account
) -> None:
    body = generate(client, user, use_ai=False)
    assert [d["name"] for d in body["days"]] == [
        "Day 1 Bench",
        "Day 2 Back",
        "Day 3 Arms+Shoulders",
        "Day 4 Legs",
    ]
    assert body["errors"] == []


def test_a_template_can_be_requested_and_is_limited_to_the_schedule(
    client: TestClient, user: Account
) -> None:
    body = generate(client, user, use_ai=False, template="push_pull_legs")
    assert [d["name"] for d in body["days"]] == ["Push", "Pull", "Legs", "Upper"]


def test_unknown_template_is_rejected(client: TestClient, user: Account) -> None:
    res = post(client, user, "/generate", {"template": "mystery"})
    assert res.status_code == 422


def test_impossible_programs_are_refused_clearly(
    client: TestClient, db: Session, user: Account
) -> None:
    db.execute(sa.update(Exercise).values(category="stretching"))
    res = post(client, user, "/generate", {"use_ai": False})
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "no_valid_program"


# --- accepting --------------------------------------------------------------------------


def test_accept_activates_and_appears_as_current(
    client: TestClient, db: Session, user: Account
) -> None:
    draft = generate(client, user, use_ai=False)
    res = post(client, user, f"/{draft['id']}/accept")
    assert res.status_code == 200 and res.json()["status"] == "active"

    current = client.get("/programs/current", headers=user.headers)
    assert current.status_code == 200 and current.json()["id"] == draft["id"]
    actions = db.scalars(sa.select(AuditLog.action)).all()
    assert "program.accepted" in actions


def test_accepting_a_new_version_archives_the_old_one(
    client: TestClient, db: Session, user: Account
) -> None:
    first = generate(client, user, use_ai=False)
    post(client, user, f"/{first['id']}/accept")
    second = generate(client, user, use_ai=False)
    assert second["version"] == 2

    assert post(client, user, f"/{second['id']}/accept").status_code == 200
    statuses = dict(
        db.execute(
            sa.select(Program.version, Program.status).where(Program.user_id == user.id)
        ).all()
    )
    assert statuses == {1: ProgramStatus.ARCHIVED, 2: ProgramStatus.ACTIVE}
    assert client.get("/programs/current", headers=user.headers).json()["version"] == 2


def test_regenerating_replaces_the_unaccepted_draft(
    client: TestClient, db: Session, user: Account
) -> None:
    first = generate(client, user, use_ai=False)
    second = generate(client, user, use_ai=False)
    assert second["version"] == 1
    rows = db.scalars(sa.select(Program).where(Program.user_id == user.id)).all()
    assert [r.id for r in rows] == [uuid.UUID(second["id"])]
    assert (
        client.get(f"/programs/{first['id']}/preview", headers=user.headers).status_code
        == 404
    )
    assert (
        db.scalars(
            sa.select(ProgramDay).where(ProgramDay.program_id == uuid.UUID(first["id"]))
        ).first()
        is None
    )


def test_only_drafts_can_be_accepted(client: TestClient, user: Account) -> None:
    draft = generate(client, user, use_ai=False)
    assert post(client, user, f"/{draft['id']}/accept").status_code == 200
    assert post(client, user, f"/{draft['id']}/accept").status_code == 409


def test_accept_revalidates_against_todays_limitations(
    client: TestClient, user: Account
) -> None:
    draft = generate(client, user, use_ai=False)
    client.put(
        "/onboarding/limitations",
        headers=user.headers,
        json={"limitations": [{"body_area": "knee", "severity": "severe"}]},
    )
    res = post(client, user, f"/{draft['id']}/accept")
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "program_no_longer_valid"
    assert any(v["code"] == "limitation" for v in detail["violations"])
    assert client.get("/programs/current", headers=user.headers).status_code == 404


def test_accept_needs_plan_access_at_that_moment(
    client: TestClient, user: Account
) -> None:
    draft = generate(client, user, use_ai=False)
    client.post(
        "/onboarding/screening", headers=user.headers, json=screen(chest_pain=True)
    )
    assert post(client, user, f"/{draft['id']}/accept").status_code == 403


def test_preview_shows_new_problems_after_a_change(
    client: TestClient, user: Account
) -> None:
    draft = generate(client, user, use_ai=False)
    client.put(
        "/onboarding/limitations",
        headers=user.headers,
        json={"limitations": [{"body_area": "knee", "severity": "severe"}]},
    )
    res = client.get(f"/programs/{draft['id']}/preview", headers=user.headers)
    assert res.status_code == 200
    assert any(e["code"] == "limitation" for e in res.json()["errors"])


# --- isolation and privacy --------------------------------------------------------------


def test_programs_are_private_to_their_owner(client: TestClient, user: Account) -> None:
    draft = generate(client, user, use_ai=False)
    other = register_and_login(client)
    grant_consents(client, other, "health_data")
    client.put("/onboarding/profile", headers=other.headers, json=profile_payload())
    client.post("/onboarding/screening", headers=other.headers, json=screen())

    url = f"/programs/{draft['id']}"
    assert client.get(f"{url}/preview", headers=other.headers).status_code == 404
    assert client.post(f"{url}/accept", headers=other.headers).status_code == 404
    assert client.get("/programs/current", headers=other.headers).status_code == 404


def test_missing_program_is_a_404(client: TestClient, user: Account) -> None:
    missing = uuid.uuid4()
    res = client.get(f"/programs/{missing}/preview", headers=user.headers)
    assert res.status_code == 404
    assert post(client, user, f"/{missing}/accept").status_code == 404


def test_deleting_the_account_removes_programs(
    client: TestClient, db: Session, user: Account
) -> None:
    draft = generate(client, user, use_ai=False)
    post(client, user, f"/{draft['id']}/accept")
    assert delete_me(client, user).status_code == 204
    assert (
        db.scalars(sa.select(Program).where(Program.user_id == user.id)).first() is None
    )
