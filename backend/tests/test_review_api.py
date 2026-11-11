"""The weekly review: rules propose, the (mocked) AI writes, items wait for approval."""

import datetime as dt
import uuid

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai.errors import AiTimeoutError
from app.ai.review import ReviewWriting
from app.models import AuditLog, Recommendation, WeeklyReview
from app.models.enums import RecommendationStatus
from app.rules.limits import Kind
from app.rules.review import Recommendation as Proposal
from app.services import review as review_svc
from tests.fakes.ai import FakeProvider
from tests.helpers import Account, grant_consents, register_and_login
from tests.test_approvals_api import client, fake, user  # noqa: F401 (fixtures)
from tests.test_coach_api import new_user

CALORIES = Proposal(
    Kind.CALORIE_CHANGE, {"change_kcal": 150}, "gain_too_slow", "Gaining too slowly."
)
DELOAD = Proposal(
    Kind.DELOAD,
    {"weeks": 1, "set_factor": 0.5},
    "long_block",
    "Many hard weeks in a row.",
)


@pytest.fixture
def proposals(monkeypatch: pytest.MonkeyPatch) -> list[Proposal]:
    """What the rules suggest this week (the rules have their own tests)."""
    found = [CALORIES, DELOAD]
    monkeypatch.setattr(review_svc, "weekly_review", lambda _inp: list(found))
    return found


def run(client: TestClient, account: Account):  # noqa: F811 (shadows the fixture)
    return client.post("/review/run", headers=account.headers)


def test_a_review_stores_pending_items_with_the_ai_words(
    client: TestClient,  # noqa: F811
    db: Session,
    fake: FakeProvider,  # noqa: F811
    user: Account,  # noqa: F811
    proposals: list[Proposal],
) -> None:
    fake.queue_structured(
        ReviewWriting(
            summary="Steady work these weeks. Keep showing up.",
            reasons=[
                "A little more food helps you grow.",
                "A lighter week to recover.",
            ],
        )
    )
    res = run(client, user)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["written_by"] == "ai"
    assert body["summary"] == "Steady work these weeks. Keep showing up."
    items = {r["kind"]: r for r in body["recommendations"]}
    assert items["calorie_change"]["payload"] == {"change_kcal": 150}
    assert items["calorie_change"]["reason"] == "A little more food helps you grow."
    assert all(
        r["status"] == "pending" and r["source"] == "review" for r in items.values()
    )
    expires = dt.datetime.fromisoformat(items["deload"]["expires_at"])
    assert (
        dt.timedelta(days=13, hours=23)
        < expires - dt.datetime.now(dt.UTC)
        <= dt.timedelta(days=14)
    )

    # The AI saw the data fenced as untrusted, with the system prompt's own rules.
    (call,) = fake.calls
    assert call.schema is ReviewWriting
    assert "weekly_review_data" in call.messages[0].text

    latest = client.get("/review/latest", headers=user.headers).json()
    assert latest["id"] == body["id"] and len(latest["recommendations"]) == 2
    entry = db.scalar(sa.select(AuditLog).where(AuditLog.action == "review.run"))
    assert entry.meta == {"proposals": 2, "superseded": 0, "written_by": "ai"}


def test_the_ai_cannot_change_or_add_numbers(
    client: TestClient,  # noqa: F811
    fake: FakeProvider,  # noqa: F811
    user: Account,  # noqa: F811
    proposals: list[Proposal],
) -> None:
    fake.queue_structured(
        ReviewWriting(
            summary="You should eat 3000 kcal now.",
            reasons=["Add 500 kcal a day.", "Rest well.", "An extra one."],
        )
    )
    body = run(client, user).json()
    assert body["summary"] is None and body["written_by"] == "rules"
    items = {r["kind"]: r for r in body["recommendations"]}
    # Reasons that do not match the proposals one to one are not used at all.
    assert items["calorie_change"]["reason"] == CALORIES.message
    assert items["calorie_change"]["payload"] == {"change_kcal": 150}


def test_a_failed_ai_call_still_stores_the_review(
    client: TestClient,  # noqa: F811
    fake: FakeProvider,  # noqa: F811
    user: Account,  # noqa: F811
    proposals: list[Proposal],
) -> None:
    fake.queue_error(AiTimeoutError("slow"))
    fake.queue_error(AiTimeoutError("slow"))
    fake.queue_error(AiTimeoutError("slow"))
    res = run(client, user)
    assert res.status_code == 201, res.text
    assert res.json()["written_by"] == "rules"
    assert len(res.json()["recommendations"]) == 2


def test_without_ai_consent_no_model_is_called(
    client: TestClient,  # noqa: F811
    fake: FakeProvider,  # noqa: F811
    proposals: list[Proposal],
) -> None:
    account = new_user(client, "health_data")
    res = run(client, account)
    assert res.status_code == 201 and res.json()["written_by"] == "rules"
    assert fake.calls == []


def test_one_review_a_day(
    client: TestClient,  # noqa: F811
    fake: FakeProvider,  # noqa: F811
    user: Account,  # noqa: F811
    proposals: list[Proposal],
) -> None:
    fake.queue_structured(ReviewWriting(summary="Good.", reasons=["a", "b"]))
    assert run(client, user).status_code == 201
    again = run(client, user)
    assert again.status_code == 429
    assert again.json()["detail"]["code"] == "review_already_ran"


def test_a_new_review_expires_the_old_undecided_items(
    client: TestClient,  # noqa: F811
    db: Session,
    fake: FakeProvider,  # noqa: F811
    user: Account,  # noqa: F811
    proposals: list[Proposal],
) -> None:
    fake.queue_structured(ReviewWriting(summary="Good.", reasons=["a", "b"]))
    first = run(client, user).json()
    approved = first["recommendations"][0]
    assert (
        client.post(
            f"/recommendations/{approved['id']}/approve", headers=user.headers
        ).status_code
        == 200
    )
    coach_item = Recommendation(
        user_id=uuid.UUID(user.id),
        kind="calorie_change",
        payload={"change_kcal": 100},
        source="coach",
    )
    db.add(coach_item)
    # Move the first review to last week, so another may run today.
    db.execute(
        sa.update(WeeklyReview).values(
            run_on=WeeklyReview.run_on - dt.timedelta(days=7)
        )
    )
    db.commit()

    fake.queue_structured(ReviewWriting(summary="Good.", reasons=["a", "b"]))
    second = run(client, user)
    assert second.status_code == 201, second.text
    db.expire_all()
    statuses = {
        r.id: r.status
        for r in db.scalars(
            sa.select(Recommendation).where(
                Recommendation.review_id == uuid.UUID(first["id"])
            )
        )
    }
    assert statuses[uuid.UUID(approved["id"])] is RecommendationStatus.APPROVED
    assert RecommendationStatus.EXPIRED in statuses.values()
    # The coach's own suggestions are not the review's to replace.
    assert db.get(Recommendation, coach_item.id).status is RecommendationStatus.PENDING
    assert (
        client.get("/review/latest", headers=user.headers).json()["id"]
        == second.json()["id"]
    )


def test_reviews_are_private(
    client: TestClient,  # noqa: F811
    fake: FakeProvider,  # noqa: F811
    user: Account,  # noqa: F811
    proposals: list[Proposal],
) -> None:
    fake.queue_structured(ReviewWriting(summary="Good.", reasons=["a", "b"]))
    assert run(client, user).status_code == 201
    other = new_user(client, "health_data")
    assert client.get("/review/latest", headers=other.headers).json() is None
    assert client.get("/recommendations", headers=other.headers).json() == []


def test_a_review_needs_health_consent(client: TestClient) -> None:  # noqa: F811
    account = register_and_login(client)
    grant_consents(client, account, "ai_processing")
    assert run(client, account).status_code == 403


def test_with_no_data_there_is_nothing_to_propose(
    client: TestClient,  # noqa: F811
    fake: FakeProvider,  # noqa: F811
    user: Account,  # noqa: F811
) -> None:
    fake.queue_structured(ReviewWriting(summary="A fresh start.", reasons=[]))
    body = run(client, user).json()
    assert body["recommendations"] == [] and body["summary"] == "A fresh start."
