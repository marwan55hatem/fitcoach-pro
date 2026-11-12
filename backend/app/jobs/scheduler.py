"""The background scheduler: its own process, never inside the API workers.

    python -m app.jobs.scheduler

- every minute: due reminders (several schedulers may run; the database makes each
  reminder slot go out at most once);
- every 15 minutes: the Sunday-evening weekly review in each user's timezone, then a
  "review ready" notification when it has suggestions;
- every hour: cleanup of old AI drafts and interrupted file deletions;
- Telegram: long polling for /start in development, or nothing in webhook mode (the API
  receives the updates).
"""

import datetime as dt
import logging
import signal
import uuid
from types import FrameType
from zoneinfo import ZoneInfo

import httpx
import sqlalchemy as sa
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy.orm import Session

from app.ai.client import get_ai_client
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.models import Consent, Profile, WeeklyReview
from app.models.enums import ConsentType
from app.rules.reminders import ReminderType
from app.services import files, food, inbody, notifications, review
from app.services.channels import RealDispatcher, Telegram, TelegramError
from app.services.storage import get_storage

logger = logging.getLogger("app.jobs")

REVIEW_WEEKDAY = 6  # Sunday
REVIEW_HOUR = 19  # from 7 pm local time


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def reminders_job(settings: Settings, dispatcher: RealDispatcher) -> None:
    result = notifications.tick(
        SessionLocal, dispatcher, now=_now(), public_url=settings.public_app_url
    )
    if result.sent or result.failed or result.skipped:
        logger.info(
            "reminders sent=%d failed=%d skipped=%d",
            result.sent,
            result.failed,
            result.skipped,
        )


def users_due_for_review(db: Session, now: dt.datetime) -> list[uuid.UUID]:
    """Users whose local time is Sunday evening and who have no review today."""
    rows = db.execute(
        sa.select(Profile.user_id, Profile.timezone).where(
            sa.exists().where(
                Consent.user_id == Profile.user_id,
                Consent.type == ConsentType.HEALTH_DATA,
                Consent.revoked_at.is_(None),
            )
        )
    ).all()
    due = []
    for user_id, timezone in rows:
        local = now.astimezone(ZoneInfo(timezone or "Africa/Cairo"))
        if local.weekday() != REVIEW_WEEKDAY or local.hour < REVIEW_HOUR:
            continue
        ran = db.scalar(
            sa.select(
                sa.exists().where(
                    WeeklyReview.user_id == user_id,
                    WeeklyReview.run_on == local.date(),
                )
            )
        )
        if not ran:
            due.append(user_id)
    return due


def weekly_review_job(settings: Settings, dispatcher: RealDispatcher) -> None:
    now = _now()
    with SessionLocal() as db:
        due = users_due_for_review(db, now)
    for user_id in due:
        try:
            with SessionLocal() as db:
                done = review.run_weekly_review(db, get_ai_client(), user_id)
                waiting = len(review.items_of(db, done))
                slot = dt.datetime.combine(done.run_on, dt.time(), tzinfo=dt.UTC)
        except review.AlreadyRanTodayError:
            continue
        except Exception:
            logger.exception("weekly_review_failed")
            continue
        if waiting:
            notifications.send_now(
                SessionLocal,
                dispatcher,
                user_id=user_id,
                rtype=ReminderType.REVIEW_READY,
                now=_now(),
                public_url=settings.public_app_url,
                slot=slot,
            )


def cleanup_job() -> None:
    now = _now()
    with SessionLocal() as db:
        meals = food.purge_old_drafts(db, now=now)
        scans = inbody.purge_old_drafts(db, now=now)
        db.commit()
    with SessionLocal() as db:
        purged = files.purge_deleted(db, get_storage())
    if meals or scans or purged:
        logger.info("cleanup meals=%d scans=%d files=%d", meals, scans, purged)


class TelegramPoller:
    """Development only: ask Telegram for new messages and answer /start <code>."""

    def __init__(self, telegram: Telegram) -> None:
        self._telegram = telegram
        self._offset: int | None = None

    def run_once(self) -> None:
        try:
            updates = self._telegram.updates(self._offset)
        except (TelegramError, httpx.HTTPError) as exc:  # try again in a moment
            logger.warning("telegram_poll_failed error=%s", type(exc).__name__)
            return
        for update in updates:
            self._offset = int(update["update_id"]) + 1
            with SessionLocal() as db:
                reply = notifications.handle_telegram_update(db, update, _now())
            if reply is not None:
                try:
                    self._telegram.send_text(*reply)
                except TelegramError as exc:
                    logger.warning("telegram_reply_failed status=%s", exc.status)


def build(settings: Settings) -> BlockingScheduler:
    dispatcher = RealDispatcher(settings)
    scheduler = BlockingScheduler(timezone="UTC")
    common = {"max_instances": 1, "coalesce": True}
    scheduler.add_job(
        reminders_job, "cron", second=5, args=[settings, dispatcher], **common
    )
    scheduler.add_job(
        weekly_review_job,
        "cron",
        minute="*/15",
        second=30,
        args=[settings, dispatcher],
        **common,
    )
    scheduler.add_job(cleanup_job, "cron", minute=17, **common)
    if settings.telegram_bot_token:
        telegram = Telegram(settings.telegram_bot_token)
        if settings.telegram_mode == "polling":
            try:
                telegram.delete_webhook()  # polling and a webhook cannot both be set
            except TelegramError as exc:
                logger.warning("telegram_delete_webhook_failed status=%s", exc.status)
            poller = TelegramPoller(telegram)
            scheduler.add_job(poller.run_once, "interval", seconds=5, **common)
        elif settings.telegram_webhook_url:
            telegram.set_webhook(
                settings.telegram_webhook_url, settings.telegram_webhook_secret
            )
    return scheduler


def main() -> None:
    configure_logging()
    settings = get_settings()
    scheduler = build(settings)

    def stop(_signum: int, _frame: FrameType | None) -> None:
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    logger.info("scheduler started")
    scheduler.start()


if __name__ == "__main__":
    main()
