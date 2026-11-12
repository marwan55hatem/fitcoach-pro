"""Reminder settings, notification devices (web push, Telegram) and the Telegram webhook."""

import datetime as dt
import hmac
import logging
import re
import uuid
from functools import lru_cache
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, HttpUrl, field_validator

from app.api.deps import CurrentUser, DbSession, require_consent
from app.core.config import Settings, get_settings
from app.core.rate_limit import auth_limit
from app.db.session import get_session_factory
from app.models import NotificationChannel, Reminder, User
from app.models.enums import ChannelType, ConsentType
from app.rules.reminders import ReminderType
from app.services import consents
from app.services import notifications as svc
from app.services.channels import Dispatcher, RealDispatcher, Telegram, TelegramError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/notifications", tags=["notifications"])

NotifyUser = Annotated[User, Depends(require_consent(ConsentType.NOTIFICATIONS))]
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MEAL_KEYS = ("breakfast", "lunch", "dinner")
MAX_WATER_TIMES = 6


def get_dispatcher() -> Dispatcher:
    return RealDispatcher(get_settings())


DispatcherDep = Annotated[Dispatcher, Depends(get_dispatcher)]


@lru_cache(maxsize=4)
def _bot_username(token: str) -> str | None:
    try:
        return Telegram(token).username()
    except (TelegramError, httpx.HTTPError):  # a link without the name still works
        return None


def _hhmm(value: str) -> str:
    if not _HHMM.match(value):
        raise ValueError("use HH:MM")
    return value


# --- schemas ----------------------------------------------------------------------------


class ReminderOut(BaseModel):
    type: ReminderType
    enabled: bool
    channel: ChannelType
    schedule: dict[str, Any]
    snooze_until: dt.datetime | None


class ChannelOut(BaseModel):
    id: uuid.UUID
    type: ChannelType
    label: str | None
    created_at: dt.datetime


class PrefsOut(BaseModel):
    quiet_start: str | None
    quiet_end: str | None
    max_per_day: int
    show_details: bool


class SettingsOut(BaseModel):
    consent: bool
    reminders: list[ReminderOut]
    channels: list[ChannelOut]
    prefs: PrefsOut
    webpush_public_key: str | None
    telegram_available: bool


class PrefsIn(BaseModel):
    quiet_start: str | None = None  # both None: no quiet hours
    quiet_end: str | None = None
    max_per_day: int = Field(default=6, ge=1, le=20)
    show_details: bool = False

    _times = field_validator("quiet_start", "quiet_end")(
        lambda v: v if v is None else _hhmm(v)
    )


class ReminderIn(BaseModel):
    enabled: bool | None = None
    channel: ChannelType | None = None
    time: str | None = None
    days: list[int] | None = Field(default=None, max_length=7)
    weekday: int | None = Field(default=None, ge=0, le=6)
    meal_times: dict[str, str] | None = None
    water_times: list[str] | None = Field(default=None, max_length=MAX_WATER_TIMES)
    snooze_hours: int | None = Field(default=None, ge=0, le=168)  # 0 ends a snooze

    _time = field_validator("time")(lambda v: v if v is None else _hhmm(v))

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int] | None) -> list[int] | None:
        if v is not None and any(d < 0 or d > 6 for d in v):
            raise ValueError("days are 0 (Monday) to 6 (Sunday)")
        return sorted(set(v)) if v is not None else None

    @field_validator("meal_times")
    @classmethod
    def _meals(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is None:
            return None
        if not set(v) <= set(MEAL_KEYS):
            raise ValueError("meals are breakfast, lunch and dinner")
        return {k: _hhmm(t) for k, t in v.items()}

    @field_validator("water_times")
    @classmethod
    def _water(cls, v: list[str] | None) -> list[str] | None:
        return sorted({_hhmm(t) for t in v}) if v is not None else None


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=10, max_length=200)
    auth: str = Field(min_length=8, max_length=100)


class SubscribeIn(BaseModel):
    endpoint: HttpUrl
    keys: SubscriptionKeys
    label: str | None = Field(default=None, max_length=100)

    @field_validator("endpoint")
    @classmethod
    def _https(cls, v: HttpUrl) -> HttpUrl:
        if v.scheme != "https" or len(str(v)) > 1000:
            raise ValueError("a push endpoint is an https URL")
        return v


class LinkCodeOut(BaseModel):
    code: str
    expires_at: dt.datetime
    link: str | None  # opens the bot with the code filled in


class TestOut(BaseModel):
    devices: int


def _reminder_out(r: Reminder) -> ReminderOut:
    return ReminderOut(
        type=ReminderType(r.type),
        enabled=r.enabled,
        channel=r.channel,
        schedule=r.schedule,
        snooze_until=r.snooze_until,
    )


def _channel_out(c: NotificationChannel) -> ChannelOut:
    return ChannelOut(id=c.id, type=c.type, label=c.label, created_at=c.created_at)


def _fmt(t: dt.time | None) -> str | None:
    return t.strftime("%H:%M") if t else None


# --- settings ---------------------------------------------------------------------------


@router.get("/settings", response_model=SettingsOut)
def settings_(db: DbSession, user: CurrentUser) -> SettingsOut:
    settings = get_settings()
    reminders = svc.ensure_reminders(db, user)
    pref = svc.prefs(db, user.id)
    out = SettingsOut(
        consent=consents.has_consent(db, user.id, ConsentType.NOTIFICATIONS),
        reminders=[_reminder_out(r) for r in reminders],
        channels=[_channel_out(c) for c in svc.channels(db, user.id)],
        prefs=PrefsOut(
            quiet_start=_fmt(pref.quiet_start),
            quiet_end=_fmt(pref.quiet_end),
            max_per_day=pref.max_per_day,
            show_details=pref.show_details,
        ),
        webpush_public_key=settings.vapid_public_key or None,
        telegram_available=bool(settings.telegram_bot_token),
    )
    db.commit()
    return out


@router.put("/prefs", response_model=PrefsOut)
def put_prefs(body: PrefsIn, db: DbSession, user: CurrentUser) -> PrefsOut:
    if (body.quiet_start is None) != (body.quiet_end is None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Set both quiet hours, or neither.",
        )
    pref = svc.prefs(db, user.id)
    pref.quiet_start = (
        dt.time.fromisoformat(body.quiet_start) if body.quiet_start else None
    )
    pref.quiet_end = dt.time.fromisoformat(body.quiet_end) if body.quiet_end else None
    pref.max_per_day = body.max_per_day
    pref.show_details = body.show_details
    db.commit()
    return PrefsOut(
        quiet_start=body.quiet_start,
        quiet_end=body.quiet_end,
        max_per_day=pref.max_per_day,
        show_details=pref.show_details,
    )


def _schedule_change(
    rtype: ReminderType, row: Reminder, body: ReminderIn
) -> dict[str, Any]:
    schedule = dict(row.schedule)
    if body.time is not None:
        if rtype in (ReminderType.MEALS, ReminderType.WATER, ReminderType.REVIEW_READY):
            raise ValueError("this reminder has no single time")
        schedule["time"] = body.time
    if body.days is not None:
        if rtype is not ReminderType.WORKOUT or not body.days:
            raise ValueError("only the workout reminder has training days")
        schedule["days"] = body.days
    if body.weekday is not None:
        if rtype not in (
            ReminderType.WEIGH_IN,
            ReminderType.PROGRESS_PHOTO,
            ReminderType.CHECKIN,
        ):
            raise ValueError("only weekly reminders have a weekday")
        schedule["weekday"] = body.weekday
    if body.meal_times is not None:
        if rtype is not ReminderType.MEALS:
            raise ValueError("only the meal reminder has meal times")
        schedule["times"] = body.meal_times
    if body.water_times is not None:
        if rtype is not ReminderType.WATER:
            raise ValueError("only the water reminder has water times")
        schedule["times"] = body.water_times
    return schedule


@router.put("/reminders/{rtype}", response_model=ReminderOut)
def put_reminder(
    rtype: ReminderType, body: ReminderIn, db: DbSession, user: CurrentUser
) -> ReminderOut:
    row = next(r for r in svc.ensure_reminders(db, user) if r.type == rtype.value)
    try:
        changes: dict[str, Any] = {"schedule": _schedule_change(rtype, row, body)}
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from None
    if body.enabled is not None:
        changes["enabled"] = body.enabled
    if body.channel is not None:
        changes["channel"] = body.channel
    if body.snooze_hours is not None:
        changes["snooze_until"] = (
            dt.datetime.now(dt.UTC) + dt.timedelta(hours=body.snooze_hours)
            if body.snooze_hours
            else None
        )
    return _reminder_out(svc.update_reminder(db, user, rtype, changes))


# --- devices ----------------------------------------------------------------------------


@router.post(
    "/webpush/subscribe", response_model=ChannelOut, status_code=status.HTTP_201_CREATED
)
def subscribe(body: SubscribeIn, db: DbSession, user: NotifyUser) -> ChannelOut:
    subscription = {"endpoint": str(body.endpoint), "keys": body.keys.model_dump()}
    return _channel_out(svc.subscribe_webpush(db, user, subscription, body.label))


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove(channel_id: uuid.UUID, db: DbSession, user: CurrentUser) -> Response:
    try:
        svc.remove_channel(db, user, channel_id)
    except svc.ChannelNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/telegram/link-code",
    response_model=LinkCodeOut,
    status_code=status.HTTP_201_CREATED,
)
@auth_limit
def link_code(request: Request, db: DbSession, user: NotifyUser) -> LinkCodeOut:
    """A one-time code (10 minutes). Send it to the bot: /start <code>."""
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "telegram_not_configured",
                "message": "Telegram is not set up on this server yet.",
            },
        )
    code, expires = svc.create_link_code(db, user, dt.datetime.now(dt.UTC))
    name = _bot_username(settings.telegram_bot_token)
    return LinkCodeOut(
        code=code,
        expires_at=expires,
        link=f"https://t.me/{name}?start={code}" if name else None,
    )


@router.post("/telegram/webhook", include_in_schema=False)
def telegram_webhook(
    update: dict[str, Any],
    db: DbSession,
    secret: Annotated[
        str | None, Header(alias="X-Telegram-Bot-Api-Secret-Token")
    ] = None,
) -> dict[str, bool]:
    settings: Settings = get_settings()
    expected = settings.telegram_webhook_secret
    if (
        settings.telegram_mode != "webhook"
        or not expected
        or not secret
        or not hmac.compare_digest(secret, expected)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    reply = svc.handle_telegram_update(db, update, dt.datetime.now(dt.UTC))
    if reply is not None:
        try:
            Telegram(settings.telegram_bot_token).send_text(*reply)
        except TelegramError as exc:
            logger.warning("telegram_reply_failed status=%s", exc.status)
    return {"ok": True}


@router.post("/test", response_model=TestOut)
@auth_limit
def test_notification(
    request: Request,
    user: NotifyUser,
    dispatcher: DispatcherDep,
    factory: Annotated[Any, Depends(get_session_factory)],
    db: DbSession,
) -> TestOut:
    db.commit()  # release the request's connection before sending
    devices = svc.send_now(
        factory,
        dispatcher,
        user_id=user.id,
        rtype=None,
        now=dt.datetime.now(dt.UTC),
        public_url=get_settings().public_app_url,
    )
    return TestOut(devices=devices)


@router.post("/{log_id}/opened", status_code=status.HTTP_204_NO_CONTENT)
def opened(log_id: uuid.UUID, db: DbSession, user: CurrentUser) -> Response:
    svc.mark_opened(db, user, log_id, dt.datetime.now(dt.UTC))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
