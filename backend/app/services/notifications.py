"""Reminders: settings, channels (Telegram, web push devices) and the scheduler's tick.

The tick claims due reminders with SELECT ... FOR UPDATE SKIP LOCKED, decides each slot
with the pure rules in app/rules/reminders.py and writes the log row BEFORE sending:
notification_log is unique per (reminder, slot), so a slot is sent at most once even if
two ticks race. Sending happens after the commit, with no database session held.
"""

import datetime as dt
import hashlib
import logging
import secrets
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Checkin,
    Consent,
    MealLog,
    NotificationChannel,
    NotificationLog,
    NotificationSettings,
    Program,
    ProgressPhoto,
    Recommendation,
    Reminder,
    TelegramLinkCode,
    User,
    WeightLog,
    WorkoutSession,
)
from app.models.enums import (
    ChannelType,
    ConsentType,
    NotificationStatus,
    ProgramStatus,
    RecommendationStatus,
)
from app.rules.reminders import (
    DEFAULTS,
    Facts,
    MessageFacts,
    ReminderType,
    compose,
    decide,
    default_training_days,
    due_slots,
)
from app.rules.training_stats import week_start
from app.services import audit, nutrition, onboarding, training
from app.services.channels import Dispatcher, Outgoing, SendResult

logger = logging.getLogger(__name__)

LINK_CODE_TTL = dt.timedelta(minutes=10)
BATCH = 500
SessionFactory = Callable[[], AbstractContextManager[Session]]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# --- settings -------------------------------------------------------------------------


def prefs(db: Session, user_id: uuid.UUID) -> NotificationSettings:
    row = db.scalar(
        sa.select(NotificationSettings).where(NotificationSettings.user_id == user_id)
    )
    if row is None:
        row = NotificationSettings(
            user_id=user_id,
            quiet_start=dt.time(22, 0),
            quiet_end=dt.time(7, 0),
            max_per_day=6,
            show_details=False,
        )
        db.add(row)
        db.flush()
    return row


def _training_days(db: Session, user_id: uuid.UUID) -> list[int]:
    profile = onboarding.get_profile(db, user_id)
    days = profile.days_per_week if profile and profile.days_per_week else 3
    return list(default_training_days(days))


def ensure_reminders(db: Session, user: User) -> list[Reminder]:
    """Every reminder type for the user, creating missing ones from the defaults."""
    rows = {
        r.type: r
        for r in db.scalars(sa.select(Reminder).where(Reminder.user_id == user.id))
    }
    missing = [t for t in ReminderType if t.value not in rows]
    if missing:
        has_push = db.scalar(
            sa.select(
                sa.exists().where(
                    NotificationChannel.user_id == user.id,
                    NotificationChannel.type == ChannelType.TELEGRAM,
                )
            )
        )
        channel = ChannelType.TELEGRAM if has_push else ChannelType.WEBPUSH
        for rtype in missing:
            default = DEFAULTS[rtype]
            schedule = dict(default.schedule)
            if rtype is ReminderType.WORKOUT:
                schedule["days"] = _training_days(db, user.id)
            row = Reminder(
                user_id=user.id,
                type=rtype.value,
                schedule=schedule,
                channel=channel,
                enabled=default.enabled,
            )
            try:
                with db.begin_nested():
                    db.add(row)
                    db.flush()
            except IntegrityError:  # created by a parallel request
                continue
            rows[rtype.value] = row
    order = [t.value for t in ReminderType]
    return sorted(
        (r for r in rows.values() if r.type in order), key=lambda r: order.index(r.type)
    )


def update_reminder(
    db: Session, user: User, rtype: ReminderType, changes: dict[str, Any]
) -> Reminder:
    rows = {r.type: r for r in ensure_reminders(db, user)}
    row = rows[rtype.value]
    for key, value in changes.items():
        setattr(row, key, value)
    db.commit()
    return row


# --- channels -------------------------------------------------------------------------


def channels(db: Session, user_id: uuid.UUID) -> list[NotificationChannel]:
    return list(
        db.scalars(
            sa.select(NotificationChannel)
            .where(NotificationChannel.user_id == user_id)
            .order_by(NotificationChannel.created_at)
        )
    )


def subscribe_webpush(
    db: Session, user: User, subscription: dict[str, Any], label: str | None
) -> NotificationChannel:
    """Add this browser; a browser already known (even for another account) moves here."""
    endpoint_hash = _hash(subscription["endpoint"])
    db.execute(
        sa.delete(NotificationChannel).where(
            NotificationChannel.endpoint_hash == endpoint_hash
        )
    )
    row = NotificationChannel(
        user_id=user.id,
        type=ChannelType.WEBPUSH,
        address=subscription,
        verified=True,
        endpoint_hash=endpoint_hash,
        label=label,
    )
    db.add(row)
    audit.record(db, "notifications.device_added", user.id, meta={"type": "webpush"})
    db.commit()
    return row


class ChannelNotFoundError(Exception):
    pass


def remove_channel(db: Session, user: User, channel_id: uuid.UUID) -> None:
    row = db.scalar(
        sa.select(NotificationChannel).where(
            NotificationChannel.id == channel_id, NotificationChannel.user_id == user.id
        )
    )
    if row is None:
        raise ChannelNotFoundError
    db.delete(row)
    audit.record(
        db, "notifications.device_removed", user.id, meta={"type": str(row.type)}
    )
    db.commit()


def create_link_code(
    db: Session, user: User, now: dt.datetime
) -> tuple[str, dt.datetime]:
    """A one-time code for the bot's /start; any older unused code stops working."""
    db.execute(
        sa.delete(TelegramLinkCode).where(
            TelegramLinkCode.user_id == user.id, TelegramLinkCode.used_at.is_(None)
        )
    )
    code = secrets.token_urlsafe(9)  # 12 characters, safe in a /start link
    expires = now + LINK_CODE_TTL
    db.add(TelegramLinkCode(user_id=user.id, code_hash=_hash(code), expires_at=expires))
    db.commit()
    return code, expires


def link_telegram(db: Session, code: str, chat_id: int, now: dt.datetime) -> bool:
    """Use a link code: the chat becomes the user's (only) Telegram channel."""
    row = db.scalar(
        sa.select(TelegramLinkCode)
        .where(TelegramLinkCode.code_hash == _hash(code))
        .with_for_update()
    )
    if row is None or row.used_at is not None or row.expires_at <= now:
        db.rollback()
        return False
    row.used_at = now
    # A chat belongs to one account: unlink it anywhere else, then replace ours.
    db.execute(
        sa.delete(NotificationChannel).where(
            NotificationChannel.type == ChannelType.TELEGRAM,
            sa.or_(
                NotificationChannel.user_id == row.user_id,
                NotificationChannel.address["chat_id"].as_integer() == chat_id,
            ),
        )
    )
    db.add(
        NotificationChannel(
            user_id=row.user_id,
            type=ChannelType.TELEGRAM,
            address={"chat_id": chat_id},
            verified=True,
            label="Telegram",
        )
    )
    audit.record(
        db, "notifications.device_added", row.user_id, meta={"type": "telegram"}
    )
    db.commit()
    return True


START_REPLIES = {
    True: "Linked. Your FitCoach reminders will come here.",
    False: "That code is not valid or has expired. Make a new one in the app.",
}


def handle_telegram_update(
    db: Session, update: dict[str, Any], now: dt.datetime
) -> tuple[int, str] | None:
    """A message to the bot. Returns (chat_id, reply) for /start <code>, else None."""
    message = update.get("message") or {}
    text = message.get("text")
    chat = message.get("chat") or {}
    if not isinstance(text, str) or chat.get("type") != "private":
        return None
    parts = text.strip().split(maxsplit=1)
    if not parts or parts[0] != "/start":
        return None
    chat_id = int(chat["id"])
    code = parts[1] if len(parts) > 1 else ""
    ok = bool(code) and len(code) <= 64 and link_telegram(db, code, chat_id, now)
    return chat_id, START_REPLIES[ok]


def mark_opened(db: Session, user: User, log_id: uuid.UUID, now: dt.datetime) -> None:
    db.execute(
        sa.update(NotificationLog)
        .where(
            NotificationLog.id == log_id,
            NotificationLog.user_id == user.id,
            NotificationLog.opened_at.is_(None),
        )
        .values(opened_at=now)
    )
    db.commit()


# --- facts for the rules --------------------------------------------------------------


def _facts(db: Session, user: User, today: dt.date) -> Facts:
    uid = user.id
    trained = set(
        db.scalars(
            sa.select(WorkoutSession.performed_on).where(
                WorkoutSession.user_id == uid,
                WorkoutSession.ended_at.is_not(None),
                WorkoutSession.performed_on >= today - dt.timedelta(days=1),
            )
        )
    )
    meals = {
        str(slot)
        for slot in db.scalars(
            sa.select(MealLog.slot).where(
                MealLog.user_id == uid,
                MealLog.date == today,
                MealLog.confirmed.is_(True),
            )
        )
    }
    monday = week_start(today)
    water_target = None
    try:
        water_target = nutrition.targets_for(db, user).water_ml
    except nutrition.ProfileIncompleteError:
        pass

    def exists(query: Any) -> bool:
        return bool(db.scalar(sa.select(sa.exists().where(*query))))

    return Facts(
        trained_on=frozenset(trained),
        meals_logged=frozenset(meals),
        water_ml=nutrition.water_on(db, user, today),
        water_target_ml=water_target,
        weighed_today=exists([WeightLog.user_id == uid, WeightLog.date == today]),
        photo_this_week=exists(
            [ProgressPhoto.user_id == uid, ProgressPhoto.taken_on >= monday]
        ),
        checkin_this_week=exists([Checkin.user_id == uid, Checkin.date >= monday]),
        body_photos_consent=_has_consent(db, uid, ConsentType.BODY_PHOTOS),
        review_pending=db.scalar(
            sa.select(sa.func.count()).where(
                Recommendation.user_id == uid,
                Recommendation.status == RecommendationStatus.PENDING,
            )
        )
        or 0,
    )


def _message_facts(db: Session, user: User, today: dt.date) -> MessageFacts:
    """Names for a workout reminder; numbers only when diet targets may be shown."""
    day_name = first = None
    program = db.scalar(
        sa.select(Program).where(
            Program.user_id == user.id, Program.status == ProgramStatus.ACTIVE
        )
    )
    if program is not None and program.days:
        last_day = db.scalar(
            sa.select(WorkoutSession.program_day_id)
            .where(
                WorkoutSession.user_id == user.id,
                WorkoutSession.program_day_id.is_not(None),
            )
            .order_by(
                WorkoutSession.performed_on.desc(), WorkoutSession.started_at.desc()
            )
            .limit(1)
        )
        ids = [d.id for d in program.days]
        index = (ids.index(last_day) + 1) % len(ids) if last_day in ids else 0
        day = program.days[index]
        day_name = day.name
        if day.exercises:
            names = training.exercise_names_by_id(db, [day.exercises[0].exercise_id])
            first = names.get(day.exercises[0].exercise_id)
    kcal_left = water_left = None
    if onboarding.onboarding_state(db, user).access.numeric_diet_targets:
        try:
            targets = nutrition.targets_for(db, user)
            eaten = nutrition.totals_of(nutrition.meals_on(db, user, today)).kcal
            kcal_left = max(0, targets.kcal - eaten)
            water_left = max(0, targets.water_ml - nutrition.water_on(db, user, today))
        except nutrition.ProfileIncompleteError:
            pass
    return MessageFacts(day_name, first, kcal_left, water_left)


def _has_consent(db: Session, user_id: uuid.UUID, kind: ConsentType) -> bool:
    return bool(
        db.scalar(
            sa.select(
                sa.exists().where(
                    Consent.user_id == user_id,
                    Consent.type == kind,
                    Consent.revoked_at.is_(None),
                )
            )
        )
    )


def _local_midnight_utc(now: dt.datetime, tz: ZoneInfo) -> dt.datetime:
    local = now.astimezone(tz)
    return dt.datetime.combine(local.date(), dt.time(), tzinfo=tz).astimezone(dt.UTC)


def _quiet(reminder: Reminder, pref: NotificationSettings) -> tuple[Any, Any]:
    if reminder.quiet_hours:
        return (
            dt.time.fromisoformat(reminder.quiet_hours["start"]),
            dt.time.fromisoformat(reminder.quiet_hours["end"]),
        )
    return pref.quiet_start, pref.quiet_end


def link_for(public_url: str, language: str, path: str, log_id: uuid.UUID) -> str:
    lang = "ar" if language == "ar" else "en"
    return f"{public_url.rstrip('/')}/{lang}{path}?n={log_id}"


# --- the tick -------------------------------------------------------------------------


@dataclass
class _Job:
    log_id: uuid.UUID
    user_id: uuid.UUID
    targets: list[tuple[uuid.UUID, ChannelType, dict[str, Any]]]
    message: Outgoing


@dataclass
class TickResult:
    sent: int = 0
    skipped: int = 0
    failed: int = 0
    reasons: dict[str, int] = field(default_factory=lambda: defaultdict(int))


@dataclass
class _UserContext:
    user: User
    tz: ZoneInfo
    language: str
    pref: NotificationSettings
    training_days: list[int]
    targets: dict[ChannelType, list[NotificationChannel]]
    _facts: Facts | None = None
    _message: MessageFacts | None = None


def _context(db: Session, user: User) -> _UserContext:
    profile = onboarding.get_profile(db, user.id)
    workout = db.scalar(
        sa.select(Reminder).where(
            Reminder.user_id == user.id, Reminder.type == ReminderType.WORKOUT.value
        )
    )
    days = (workout.schedule.get("days") if workout else None) or _training_days(
        db, user.id
    )
    targets: dict[ChannelType, list[NotificationChannel]] = defaultdict(list)
    for ch in channels(db, user.id):
        if ch.verified:
            targets[ch.type].append(ch)
    return _UserContext(
        user=user,
        tz=ZoneInfo(profile.timezone if profile else onboarding.DEFAULT_TIMEZONE),
        language=profile.language if profile else "en",
        pref=prefs(db, user.id),
        training_days=list(days),
        targets=targets,
    )


def _claim(
    db: Session, now: dt.datetime, public_url: str, result: TickResult
) -> list[_Job]:
    consented = sa.exists().where(
        Consent.user_id == Reminder.user_id,
        Consent.type == ConsentType.NOTIFICATIONS,
        Consent.revoked_at.is_(None),
    )
    reminders = db.scalars(
        sa.select(Reminder)
        .where(
            Reminder.enabled.is_(True),
            Reminder.type != ReminderType.REVIEW_READY.value,
            consented,
        )
        .order_by(Reminder.user_id, Reminder.id)
        .limit(BATCH)
        .with_for_update(skip_locked=True)
    ).all()
    contexts: dict[uuid.UUID, _UserContext] = {}
    jobs: list[_Job] = []
    for reminder in reminders:
        try:
            rtype = ReminderType(reminder.type)
        except ValueError:
            continue
        if reminder.user_id not in contexts:
            user = db.get(User, reminder.user_id)
            assert user is not None
            contexts[reminder.user_id] = _context(db, user)
        ctx = contexts[reminder.user_id]
        for slot in due_slots(rtype, reminder.schedule, now, ctx.tz, ctx.training_days):
            taken = db.scalar(
                sa.select(
                    sa.exists().where(
                        NotificationLog.reminder_id == reminder.id,
                        NotificationLog.scheduled_for == slot.at_utc,
                    )
                )
            )
            if taken:
                continue
            if ctx._facts is None:
                ctx._facts = _facts(db, ctx.user, slot.local.date())
            sent_today = db.scalar(
                sa.select(sa.func.count()).where(
                    NotificationLog.user_id == ctx.user.id,
                    NotificationLog.status.in_(
                        [NotificationStatus.SENT, NotificationStatus.SENDING]
                    ),
                    NotificationLog.reminder_id.is_not(None),
                    NotificationLog.sent_at >= _local_midnight_utc(now, ctx.tz),
                )
            )
            recent = [
                (sent_at, opened is not None)
                for sent_at, opened in db.execute(
                    sa.select(NotificationLog.sent_at, NotificationLog.opened_at)
                    .where(
                        NotificationLog.user_id == ctx.user.id,
                        NotificationLog.type == rtype.value,
                        NotificationLog.status == NotificationStatus.SENT,
                    )
                    .order_by(NotificationLog.sent_at.desc())
                    .limit(3)
                )
            ]
            decision = decide(
                rtype=rtype,
                slot_key=slot.key,
                local=slot.local,
                now=now,
                facts=ctx._facts,
                training_days=ctx.training_days,
                quiet=_quiet(reminder, ctx.pref),
                sent_today=sent_today or 0,
                max_per_day=ctx.pref.max_per_day,
                recent=recent,
                snooze_until=reminder.snooze_until,
            )
            targets = ctx.targets.get(reminder.channel, [])
            reason = decision.reason
            if decision.send and not targets:
                reason = "no_channel"
            send = decision.send and bool(targets)
            log = NotificationLog(
                user_id=ctx.user.id,
                reminder_id=reminder.id,
                scheduled_for=slot.at_utc,
                sent_at=now,
                type=rtype.value,
                channel=reminder.channel,
                status=NotificationStatus.SENDING
                if send
                else NotificationStatus.SKIPPED,
                reason=None if send else reason,
            )
            try:
                with db.begin_nested():
                    db.add(log)
                    db.flush()
            except IntegrityError:
                continue  # another tick claimed this slot first
            if not send:
                result.skipped += 1
                result.reasons[reason or "skipped"] += 1
                continue
            if ctx._message is None:
                ctx._message = _message_facts(db, ctx.user, slot.local.date())
            text = compose(
                rtype,
                slot.key,
                ctx._message,
                ctx.language,
                show_details=ctx.pref.show_details,
                seed=slot.local.toordinal(),
            )
            jobs.append(
                _Job(
                    log.id,
                    ctx.user.id,
                    [(t.id, t.type, dict(t.address)) for t in targets],
                    Outgoing(
                        text.title,
                        text.body,
                        link_for(public_url, ctx.language, text.path, log.id),
                        rtype.value,
                    ),
                )
            )
    return jobs


def _deliver(
    factory: SessionFactory, jobs: Iterable[_Job], dispatcher: Dispatcher
) -> tuple[int, int]:
    """Send outside any database session, then record the outcome."""
    outcomes: list[tuple[_Job, bool, list[uuid.UUID]]] = []
    for job in jobs:
        delivered = False
        dead: list[uuid.UUID] = []
        for channel_id, channel_type, address in job.targets:
            res: SendResult = dispatcher.send(channel_type, address, job.message)
            delivered = delivered or res.ok
            if res.dead:
                dead.append(channel_id)
        outcomes.append((job, delivered, dead))
    if not outcomes:
        return 0, 0
    with factory() as db:
        for job, delivered, dead in outcomes:
            db.execute(
                sa.update(NotificationLog)
                .where(NotificationLog.id == job.log_id)
                .values(
                    status=NotificationStatus.SENT
                    if delivered
                    else NotificationStatus.FAILED
                )
            )
            if dead:
                db.execute(
                    sa.delete(NotificationChannel).where(
                        NotificationChannel.id.in_(dead)
                    )
                )
                logger.info("notification_channels_removed count=%d", len(dead))
        db.commit()
    sent = sum(1 for _, ok, _ in outcomes if ok)
    return sent, len(outcomes) - sent


def tick(
    factory: SessionFactory,
    dispatcher: Dispatcher,
    *,
    now: dt.datetime,
    public_url: str,
) -> TickResult:
    """One scheduler tick: claim due slots, decide, log, then send."""
    result = TickResult()
    with factory() as db:
        jobs = _claim(db, now, public_url, result)
        db.commit()  # the slots are claimed; release the locks and the connection
    result.sent, result.failed = _deliver(factory, jobs, dispatcher)
    return result


def send_now(
    factory: SessionFactory,
    dispatcher: Dispatcher,
    *,
    user_id: uuid.UUID,
    rtype: ReminderType | None,
    now: dt.datetime,
    public_url: str,
    slot: dt.datetime | None = None,
) -> int:
    """Send one notification now (a test, or review_ready). Returns devices reached.

    rtype None is a test to every device, ignoring quiet hours and the daily cap.
    review_ready respects consent, the reminder setting, quiet hours and its slot.
    """
    with factory() as db:
        user = db.get(User, user_id)
        if user is None or not _has_consent(db, user_id, ConsentType.NOTIFICATIONS):
            return 0
        ctx = _context(db, user)
        reminder = None
        if rtype is not None:
            reminder = next(
                (r for r in ensure_reminders(db, user) if r.type == rtype.value), None
            )
            local = now.astimezone(ctx.tz)
            if (
                reminder is None
                or not reminder.enabled
                or decide(
                    rtype=rtype,
                    slot_key="",
                    local=local,
                    now=now,
                    facts=_facts(db, user, local.date()),
                    training_days=ctx.training_days,
                    quiet=_quiet(reminder, ctx.pref),
                    sent_today=0,
                    max_per_day=ctx.pref.max_per_day,
                    recent=[],
                    snooze_until=reminder.snooze_until,
                ).send
                is False
            ):
                db.commit()
                return 0
            targets = ctx.targets.get(reminder.channel, [])
        else:
            targets = [t for ts in ctx.targets.values() for t in ts]
        if not targets:
            db.commit()
            return 0
        log = NotificationLog(
            user_id=user_id,
            reminder_id=reminder.id if reminder else None,
            scheduled_for=slot,
            sent_at=now,
            type=rtype.value if rtype else "test",
            channel=targets[0].type,
            status=NotificationStatus.SENDING,
        )
        try:
            with db.begin_nested():
                db.add(log)
                db.flush()
        except IntegrityError:
            db.commit()
            return 0
        if rtype is None:
            body = (
                "اختبار: الإشعارات شغّالة."
                if ctx.language == "ar"
                else "Test: notifications work."
            )
            path, tag = "/settings/notifications", "test"
        else:
            text = compose(
                rtype, "", MessageFacts(), ctx.language, show_details=False, seed=0
            )
            body, path, tag = text.body, text.path, rtype.value
        job = _Job(
            log.id,
            user_id,
            [(t.id, t.type, dict(t.address)) for t in targets],
            Outgoing(
                "FitCoach", body, link_for(public_url, ctx.language, path, log.id), tag
            ),
        )
        db.commit()
    sent, _failed = _deliver(factory, [job], dispatcher)
    return len(job.targets) if sent else 0
