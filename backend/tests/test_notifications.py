"""Reminders end to end: settings, devices, the scheduler tick, Telegram linking.

Time is passed in explicitly (frozen). No real Telegram or push service is called.
"""

import datetime as dt
import threading
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api import notifications as api
from app.core.config import Settings
from app.db.session import get_db, get_session_factory
from app.main import create_app
from app.models import (
    Consent,
    MealLog,
    NotificationChannel,
    NotificationLog,
    Profile,
    Reminder,
    TelegramLinkCode,
    User,
    WorkoutSession,
)
from app.models.enums import (
    ChannelType,
    ConsentType,
    MealSlot,
    MealSource,
    NotificationStatus,
)
from app.services import notifications as svc
from app.services.channels import Outgoing, SendResult
from app.services.storage import Storage, get_storage
from tests.helpers import Account, grant_consents, register_and_login
from tests.test_onboarding import profile_payload, screen

UTC = dt.UTC
# Monday 2026-11-09, 18:00 in Cairo (UTC+2): a default training day at the default time.
MONDAY_6PM = dt.datetime(2026, 11, 9, 16, 0, tzinfo=UTC)
PUBLIC = "https://app.example"


class FakeDispatcher:
    def __init__(self) -> None:
        self.sent: list[tuple[ChannelType, dict[str, Any], Outgoing]] = []
        self.result = SendResult(True)
        self.lock = threading.Lock()

    def send(
        self, channel: ChannelType, address: dict[str, Any], message: Outgoing
    ) -> SendResult:
        with self.lock:
            self.sent.append((channel, address, message))
        return self.result


@pytest.fixture
def dispatcher() -> FakeDispatcher:
    return FakeDispatcher()


@pytest.fixture
def client(
    db: Session, storage: Storage, dispatcher: FakeDispatcher
) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_session_factory] = lambda: lambda: nullcontext(db)
    app.dependency_overrides[api.get_dispatcher] = lambda: dispatcher
    with TestClient(app) as test_client:
        yield test_client


def onboard(client: TestClient, *consents: str) -> Account:
    account = register_and_login(client)
    grant_consents(client, account, "health_data", *consents)
    res = client.put(
        "/onboarding/profile", headers=account.headers, json=profile_payload()
    )
    assert res.status_code == 200, res.text
    assert (
        client.post(
            "/onboarding/screening", headers=account.headers, json=screen()
        ).status_code
        == 201
    )
    return account


@pytest.fixture
def user(client: TestClient) -> Account:
    return onboard(client, "notifications")


def subscribe(client: TestClient, account: Account, endpoint: str = "phone"):
    return client.post(
        "/notifications/webpush/subscribe",
        headers=account.headers,
        json={
            "endpoint": f"https://push.example/{endpoint}",
            "keys": {"p256dh": "p" * 20, "auth": "a" * 12},
            "label": "Phone",
        },
    )


def settings_of(client: TestClient, account: Account) -> dict[str, Any]:
    res = client.get("/notifications/settings", headers=account.headers)
    assert res.status_code == 200, res.text
    return res.json()


def run(db: Session, dispatcher: FakeDispatcher, now: dt.datetime) -> svc.TickResult:
    return svc.tick(lambda: nullcontext(db), dispatcher, now=now, public_url=PUBLIC)


def logs(db: Session, account: Account) -> list[NotificationLog]:
    db.expire_all()
    return list(
        db.scalars(
            sa.select(NotificationLog)
            .where(NotificationLog.user_id == uuid.UUID(account.id))
            .order_by(NotificationLog.sent_at)
        )
    )


# --- settings and devices -------------------------------------------------------------


def test_settings_start_from_safe_defaults(client: TestClient, user: Account) -> None:
    body = settings_of(client, user)
    types = {r["type"]: r for r in body["reminders"]}
    assert len(types) == 9 and body["consent"] is True
    assert types["workout"]["enabled"] and types["workout"]["schedule"]["days"] == [
        0,
        1,
        3,
        4,
    ]
    assert not types["meals"]["enabled"] and not types["water"]["enabled"]
    assert body["prefs"] == {
        "quiet_start": "22:00",
        "quiet_end": "07:00",
        "max_per_day": 6,
        "show_details": False,
    }
    assert body["channels"] == [] and body["telegram_available"] is False


def test_reminder_settings_are_validated(client: TestClient, user: Account) -> None:
    ok = client.put(
        "/notifications/reminders/meals",
        headers=user.headers,
        json={"enabled": True, "meal_times": {"lunch": "13:30"}},
    )
    assert ok.status_code == 200 and ok.json()["schedule"]["times"] == {
        "lunch": "13:30"
    }
    for path, body in [
        ("meals", {"time": "13:00"}),
        ("workout", {"time": "25:00"}),
        ("workout", {"days": [7]}),
        ("water", {"meal_times": {"lunch": "13:00"}}),
        ("checkin", {"days": [1]}),
    ]:
        res = client.put(
            f"/notifications/reminders/{path}", headers=user.headers, json=body
        )
        assert res.status_code == 422, (path, body)
    snoozed = client.put(
        "/notifications/reminders/water",
        headers=user.headers,
        json={"snooze_hours": 24},
    )
    assert snoozed.json()["snooze_until"] is not None


def test_several_push_devices_and_one_browser_per_account(
    client: TestClient, user: Account
) -> None:
    assert subscribe(client, user, "phone").status_code == 201
    assert subscribe(client, user, "laptop").status_code == 201
    assert len(settings_of(client, user)["channels"]) == 2
    # The same browser subscribing for another account moves to that account.
    other = onboard(client, "notifications")
    assert subscribe(client, other, "phone").status_code == 201
    assert len(settings_of(client, user)["channels"]) == 1
    device = settings_of(client, other)["channels"][0]
    res = client.delete(f"/notifications/channels/{device['id']}", headers=user.headers)
    assert res.status_code == 404  # not yours
    res = client.delete(
        f"/notifications/channels/{device['id']}", headers=other.headers
    )
    assert res.status_code == 204


def test_devices_need_the_notifications_consent(client: TestClient) -> None:
    account = onboard(client)
    assert subscribe(client, account).status_code == 403
    assert (
        client.post("/notifications/test", headers=account.headers).status_code == 403
    )


# --- the tick -------------------------------------------------------------------------


def test_a_due_reminder_is_sent_exactly_once(
    client: TestClient, db: Session, dispatcher: FakeDispatcher, user: Account
) -> None:
    subscribe(client, user)
    settings_of(client, user)
    first = run(db, dispatcher, MONDAY_6PM)
    assert first.sent == 1
    ((channel, address, message),) = dispatcher.sent
    assert channel is ChannelType.WEBPUSH and address["endpoint"].endswith("/phone")
    (log,) = logs(db, user)
    assert log.status is NotificationStatus.SENT and log.type == "workout"
    assert message.url == f"{PUBLIC}/en/train?n={log.id}" and message.tag == "workout"
    # The next minutes are inside the grace window: still only one.
    run(db, dispatcher, MONDAY_6PM + dt.timedelta(minutes=1))
    run(db, dispatcher, MONDAY_6PM + dt.timedelta(minutes=5))
    assert len(dispatcher.sent) == 1


def test_no_reminder_off_schedule(
    client: TestClient, db: Session, dispatcher: FakeDispatcher, user: Account
) -> None:
    subscribe(client, user)
    settings_of(client, user)
    run(db, dispatcher, MONDAY_6PM - dt.timedelta(minutes=1))
    run(db, dispatcher, MONDAY_6PM + dt.timedelta(days=2))  # Wednesday: rest day
    assert dispatcher.sent == []


def test_quiet_hours_skip(
    client: TestClient, db: Session, dispatcher: FakeDispatcher, user: Account
) -> None:
    subscribe(client, user)
    client.put(
        "/notifications/prefs",
        headers=user.headers,
        json={"quiet_start": "17:00", "quiet_end": "19:00"},
    )
    settings_of(client, user)
    result = run(db, dispatcher, MONDAY_6PM)
    assert result.sent == 0 and dispatcher.sent == []
    (log,) = logs(db, user)
    assert (log.status, log.reason) == (NotificationStatus.SKIPPED, "quiet_hours")


def test_skip_when_already_trained_today(
    client: TestClient, db: Session, dispatcher: FakeDispatcher, user: Account
) -> None:
    subscribe(client, user)
    settings_of(client, user)
    db.add(
        WorkoutSession(
            user_id=uuid.UUID(user.id),
            performed_on=dt.date(2026, 11, 9),
            started_at=MONDAY_6PM - dt.timedelta(hours=8),
            ended_at=MONDAY_6PM - dt.timedelta(hours=7),
        )
    )
    db.commit()
    run(db, dispatcher, MONDAY_6PM)
    assert dispatcher.sent == [] and logs(db, user)[0].reason == "already_trained"


def test_meal_reminders_skip_logged_meals(
    client: TestClient, db: Session, dispatcher: FakeDispatcher, user: Account
) -> None:
    subscribe(client, user)
    client.put(
        "/notifications/reminders/meals",
        headers=user.headers,
        json={"enabled": True, "meal_times": {"lunch": "14:00", "dinner": "20:00"}},
    )
    db.add(
        MealLog(
            user_id=uuid.UUID(user.id),
            date=dt.date(2026, 11, 10),
            slot=MealSlot.LUNCH,
            source=MealSource.MANUAL,
            name="Rice and chicken",
            kcal=600,
            confirmed=True,
        )
    )
    db.commit()
    run(db, dispatcher, dt.datetime(2026, 11, 10, 12, 0, tzinfo=UTC))  # 14:00 Cairo
    assert dispatcher.sent == []
    run(db, dispatcher, dt.datetime(2026, 11, 10, 18, 0, tzinfo=UTC))  # 20:00 Cairo
    ((_, _, message),) = dispatcher.sent
    assert "dinner" in message.body and message.url.startswith(f"{PUBLIC}/en/food?n=")
    assert "kcal" not in message.body  # numbers only when the user turns details on


def test_the_daily_cap(
    client: TestClient, db: Session, dispatcher: FakeDispatcher, user: Account
) -> None:
    subscribe(client, user)
    client.put(
        "/notifications/prefs",
        headers=user.headers,
        json={"max_per_day": 1, "quiet_start": None, "quiet_end": None},
    )
    reminders = {r["type"]: r for r in settings_of(client, user)["reminders"]}
    assert reminders
    water = db.scalar(
        sa.select(Reminder).where(
            Reminder.user_id == uuid.UUID(user.id), Reminder.type == "water"
        )
    )
    db.add(
        NotificationLog(
            user_id=uuid.UUID(user.id),
            reminder_id=water.id,
            scheduled_for=MONDAY_6PM - dt.timedelta(hours=3),
            sent_at=MONDAY_6PM - dt.timedelta(hours=3),
            type="water",
            channel=ChannelType.WEBPUSH,
            status=NotificationStatus.SENT,
        )
    )
    db.commit()
    run(db, dispatcher, MONDAY_6PM)
    assert dispatcher.sent == [] and logs(db, user)[-1].reason == "daily_cap"


def test_ignored_reminders_back_off_and_opening_resets(
    client: TestClient, db: Session, dispatcher: FakeDispatcher, user: Account
) -> None:
    subscribe(client, user)
    settings_of(client, user)
    workout = db.scalar(
        sa.select(Reminder).where(
            Reminder.user_id == uuid.UUID(user.id), Reminder.type == "workout"
        )
    )
    old = []
    for hours in (24, 72, 96):
        row = NotificationLog(
            user_id=uuid.UUID(user.id),
            reminder_id=workout.id,
            scheduled_for=MONDAY_6PM - dt.timedelta(hours=hours),
            sent_at=MONDAY_6PM - dt.timedelta(hours=hours),
            type="workout",
            channel=ChannelType.WEBPUSH,
            status=NotificationStatus.SENT,
        )
        db.add(row)
        old.append(row)
    db.commit()
    run(db, dispatcher, MONDAY_6PM)
    assert dispatcher.sent == [] and logs(db, user)[-1].reason == "backed_off"

    # Tapping one (the app reports it) brings reminders back.
    res = client.post(f"/notifications/{old[0].id}/opened", headers=user.headers)
    assert res.status_code == 204
    run(
        db, dispatcher, MONDAY_6PM + dt.timedelta(days=1)
    )  # Tuesday, also a training day
    assert len(dispatcher.sent) == 1


def test_withdrawn_consent_stops_reminders(
    client: TestClient, db: Session, dispatcher: FakeDispatcher, user: Account
) -> None:
    subscribe(client, user)
    settings_of(client, user)
    res = client.put(
        "/consents", headers=user.headers, json={"consents": {"notifications": False}}
    )
    assert res.status_code == 200, res.text
    run(db, dispatcher, MONDAY_6PM)
    assert dispatcher.sent == [] and logs(db, user) == []


def test_without_a_device_nothing_is_sent(
    client: TestClient, db: Session, dispatcher: FakeDispatcher, user: Account
) -> None:
    settings_of(client, user)
    run(db, dispatcher, MONDAY_6PM)
    assert dispatcher.sent == [] and logs(db, user)[0].reason == "no_channel"


def test_dead_subscriptions_are_removed(
    client: TestClient, db: Session, dispatcher: FakeDispatcher, user: Account
) -> None:
    subscribe(client, user)
    settings_of(client, user)
    dispatcher.result = SendResult(False, dead=True)
    result = run(db, dispatcher, MONDAY_6PM)
    assert result.failed == 1
    assert logs(db, user)[0].status is NotificationStatus.FAILED
    assert settings_of(client, user)["channels"] == []


def test_the_test_button(
    client: TestClient, dispatcher: FakeDispatcher, user: Account
) -> None:
    assert client.post("/notifications/test", headers=user.headers).json() == {
        "devices": 0
    }
    subscribe(client, user)
    assert client.post("/notifications/test", headers=user.headers).json() == {
        "devices": 1
    }
    ((_, _, message),) = dispatcher.sent
    assert message.tag == "test" and "/en/settings/notifications?n=" in message.url


def test_review_ready_is_sent_once_per_review(
    client: TestClient, db: Session, dispatcher: FakeDispatcher, user: Account
) -> None:
    from app.models import Recommendation
    from app.rules.reminders import ReminderType

    subscribe(client, user)
    db.add(
        Recommendation(
            user_id=uuid.UUID(user.id),
            kind="calorie_change",
            payload={"change_kcal": 100},
        )
    )
    db.commit()
    slot = dt.datetime(2026, 11, 8, tzinfo=UTC)

    def send() -> int:
        return svc.send_now(
            lambda: nullcontext(db),
            dispatcher,
            user_id=uuid.UUID(user.id),
            rtype=ReminderType.REVIEW_READY,
            now=dt.datetime(2026, 11, 8, 17, 0, tzinfo=UTC),  # Sunday 19:00 Cairo
            public_url=PUBLIC,
            slot=slot,
        )

    assert send() == 1
    assert send() == 0
    assert len(dispatcher.sent) == 1


# --- Telegram -------------------------------------------------------------------------


@pytest.fixture
def telegram_on(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(
        _env_file=None,
        telegram_bot_token="123:abc",
        telegram_mode="webhook",
        telegram_webhook_secret="s" * 40,
    )
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr(api, "_bot_username", lambda _token: "fitcoach_bot")
    sent: list[tuple[int, str]] = []
    monkeypatch.setattr(
        api.Telegram, "send_text", lambda _self, chat, text: sent.append((chat, text))
    )
    settings.__dict__["_sent"] = sent
    return settings


def start(code: str, chat_id: int = 555) -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "text": f"/start {code}",
            "chat": {"id": chat_id, "type": "private"},
        },
    }


def test_linking_telegram_with_a_one_time_code(
    client: TestClient, db: Session, user: Account, telegram_on: Settings
) -> None:
    res = client.post("/notifications/telegram/link-code", headers=user.headers)
    assert res.status_code == 201, res.text
    code = res.json()["code"]
    assert res.json()["link"] == f"https://t.me/fitcoach_bot?start={code}"
    # Only the hash is stored.
    assert db.scalar(sa.select(TelegramLinkCode.code_hash)) != code

    now = dt.datetime.now(UTC)
    assert svc.handle_telegram_update(db, start(code), now) == (
        555,
        svc.START_REPLIES[True],
    )
    (channel,) = [
        c for c in settings_of(client, user)["channels"] if c["type"] == "telegram"
    ]
    assert channel["label"] == "Telegram"
    # One use only.
    assert svc.handle_telegram_update(db, start(code), now) == (
        555,
        svc.START_REPLIES[False],
    )
    # Linking again replaces the chat: still one Telegram channel.
    second = client.post(
        "/notifications/telegram/link-code", headers=user.headers
    ).json()["code"]
    svc.handle_telegram_update(db, start(second, chat_id=777), now)
    rows = db.scalars(
        sa.select(NotificationChannel).where(
            NotificationChannel.type == ChannelType.TELEGRAM
        )
    ).all()
    assert [r.address for r in rows] == [{"chat_id": 777}]


def test_expired_and_unknown_codes_fail(
    client: TestClient, db: Session, user: Account, telegram_on: Settings
) -> None:
    code = client.post(
        "/notifications/telegram/link-code", headers=user.headers
    ).json()["code"]
    later = dt.datetime.now(UTC) + dt.timedelta(minutes=11)
    assert (
        svc.handle_telegram_update(db, start(code), later)[1]
        == svc.START_REPLIES[False]
    )
    assert (
        svc.handle_telegram_update(db, start("nope"), later)[1]
        == svc.START_REPLIES[False]
    )
    group = {"message": {"text": f"/start {code}", "chat": {"id": 1, "type": "group"}}}
    assert svc.handle_telegram_update(db, group, later) is None


def test_the_webhook_needs_the_secret(
    client: TestClient, db: Session, user: Account, telegram_on: Settings
) -> None:
    code = client.post(
        "/notifications/telegram/link-code", headers=user.headers
    ).json()["code"]
    url = "/notifications/telegram/webhook"
    assert client.post(url, json=start(code)).status_code == 404
    bad = {"X-Telegram-Bot-Api-Secret-Token": "wrong"}
    assert client.post(url, json=start(code), headers=bad).status_code == 404
    good = {"X-Telegram-Bot-Api-Secret-Token": "s" * 40}
    assert client.post(url, json=start(code), headers=good).json() == {"ok": True}
    assert telegram_on.__dict__["_sent"] == [(555, svc.START_REPLIES[True])]


def test_link_code_without_a_bot(client: TestClient, user: Account) -> None:
    res = client.post("/notifications/telegram/link-code", headers=user.headers)
    assert (
        res.status_code == 503
        and res.json()["detail"]["code"] == "telegram_not_configured"
    )


# --- two schedulers at once -----------------------------------------------------------


def test_two_ticks_racing_send_each_slot_once(engine: sa.Engine) -> None:
    """Real, separate connections: two scheduler processes ticking at the same moment."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        user = User(email=f"{uuid.uuid4().hex[:10]}@example.com", password_hash="x")
        db.add(user)
        db.flush()
        db.add_all(
            [
                Consent(user_id=user.id, type=ConsentType.NOTIFICATIONS),
                Profile(
                    user_id=user.id,
                    timezone="Africa/Cairo",
                    days_per_week=7,
                    language="en",
                ),
                Reminder(
                    user_id=user.id,
                    type="workout",
                    schedule={"time": "18:00", "days": [0, 1, 2, 3, 4, 5, 6]},
                    channel=ChannelType.WEBPUSH,
                    enabled=True,
                ),
                NotificationChannel(
                    user_id=user.id,
                    type=ChannelType.WEBPUSH,
                    address={"endpoint": "https://push.example/race", "keys": {}},
                    verified=True,
                    endpoint_hash=uuid.uuid4().hex,
                ),
            ]
        )
        db.commit()
        user_id = user.id
    try:
        for attempt in range(3):
            dispatcher = FakeDispatcher()
            now = MONDAY_6PM + dt.timedelta(days=attempt)
            barrier = threading.Barrier(2)

            def tick(
                barrier: threading.Barrier = barrier,
                dispatcher: FakeDispatcher = dispatcher,
                now: dt.datetime = now,
            ) -> None:
                barrier.wait()
                svc.tick(factory, dispatcher, now=now, public_url=PUBLIC)

            threads = [threading.Thread(target=tick) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert len(dispatcher.sent) == 1, attempt
    finally:
        with factory() as db:
            db.execute(sa.delete(User).where(User.id == user_id))
            db.commit()
