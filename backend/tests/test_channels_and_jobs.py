"""Channel error handling and the scheduler's helpers, with no network."""

import datetime as dt
import json
import uuid
from typing import Any

import httpx
import pytest
from pywebpush import WebPushException
from requests import Response
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.jobs import scheduler
from app.jobs.vapid import new_keys
from app.models import Consent, Profile, User, WeeklyReview
from app.models.enums import ChannelType, ConsentType
from app.services import channels
from app.services.channels import Outgoing, RealDispatcher, Telegram

MESSAGE = Outgoing(
    "FitCoach", "Time to train.", "https://app.example/en/train?n=1", "workout"
)


def telegram_answering(status: int, body: dict[str, Any]) -> tuple[Telegram, list[Any]]:
    seen: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(status, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Telegram("123:abc", client), seen


def test_telegram_send_and_a_blocked_bot() -> None:
    ok, seen = telegram_answering(200, {"ok": True, "result": {}})
    assert ok.send(42, MESSAGE).ok
    path, body = seen[0]
    assert path == "/bot123:abc/sendMessage"
    assert body["chat_id"] == 42 and body["text"].endswith(MESSAGE.url)

    blocked, _ = telegram_answering(403, {"ok": False, "description": "blocked"})
    assert blocked.send(42, MESSAGE) == channels.SendResult(False, dead=True)
    busy, _ = telegram_answering(429, {"ok": False})
    assert busy.send(42, MESSAGE) == channels.SendResult(False, dead=False)


def _push_error(status: int) -> WebPushException:
    response = Response()
    response.status_code = status
    return WebPushException("push failed", response=response)


@pytest.mark.parametrize(("status", "dead"), [(410, True), (404, True), (500, False)])
def test_webpush_errors(
    monkeypatch: pytest.MonkeyPatch, status: int, dead: bool
) -> None:
    def fail(**_kwargs: Any) -> None:
        raise _push_error(status)

    monkeypatch.setattr(channels, "webpush", fail)
    settings = Settings(_env_file=None, vapid_private_key="key")
    result = channels.send_webpush(
        {"endpoint": "https://push.example/x"}, MESSAGE, settings
    )
    assert result == channels.SendResult(False, dead=dead)


def test_webpush_payload_is_minimal(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}
    monkeypatch.setattr(channels, "webpush", lambda **kw: sent.update(kw))
    settings = Settings(_env_file=None, vapid_private_key="key")
    assert channels.send_webpush({"endpoint": "e"}, MESSAGE, settings).ok
    assert json.loads(sent["data"]) == {
        "title": "FitCoach",
        "body": "Time to train.",
        "url": MESSAGE.url,
        "tag": "workout",
    }
    assert sent["vapid_claims"] == {"sub": settings.vapid_subject}


def test_without_keys_nothing_is_sent() -> None:
    dispatcher = RealDispatcher(Settings(_env_file=None))
    assert not dispatcher.send(ChannelType.WEBPUSH, {"endpoint": "e"}, MESSAGE).ok
    assert not dispatcher.send(ChannelType.TELEGRAM, {"chat_id": 1}, MESSAGE).ok


def test_vapid_keys_have_the_browser_format() -> None:
    public, private = new_keys()
    assert len(public) == 87 and len(private) == 43  # 65 and 32 bytes, base64url
    assert new_keys() != (public, private)


def test_webhook_mode_needs_a_long_secret() -> None:
    with pytest.raises(ValueError, match="TELEGRAM_WEBHOOK_SECRET"):
        Settings(
            _env_file=None, telegram_mode="webhook", telegram_webhook_secret="short"
        )


def test_the_scheduler_has_its_jobs() -> None:
    jobs = scheduler.build(Settings(_env_file=None)).get_jobs()
    assert {j.func.__name__ for j in jobs} == {
        "reminders_job",
        "weekly_review_job",
        "cleanup_job",
    }


def test_weekly_review_runs_on_sunday_evening_local_time(db: Session) -> None:
    user = User(email=f"{uuid.uuid4().hex[:10]}@example.com", password_hash="x")
    db.add(user)
    db.flush()
    db.add_all(
        [
            Consent(user_id=user.id, type=ConsentType.HEALTH_DATA),
            Profile(user_id=user.id, timezone="Africa/Cairo"),
        ]
    )
    db.flush()
    sunday_7pm_cairo = dt.datetime(2026, 11, 15, 17, 0, tzinfo=dt.UTC)
    assert user.id in scheduler.users_due_for_review(db, sunday_7pm_cairo)
    assert user.id not in scheduler.users_due_for_review(
        db, sunday_7pm_cairo - dt.timedelta(hours=1)
    )
    assert user.id not in scheduler.users_due_for_review(
        db, sunday_7pm_cairo - dt.timedelta(days=1)
    )
    db.add(
        WeeklyReview(user_id=user.id, run_on=dt.date(2026, 11, 15), written_by="rules")
    )
    db.flush()
    assert user.id not in scheduler.users_due_for_review(db, sunday_7pm_cairo)
