"""Reminder rules: when a slot is due, when to skip it, and what it says (pure)."""

import datetime as dt
from zoneinfo import ZoneInfo

from app.rules.reminders import (
    DEFAULTS,
    Facts,
    MessageFacts,
    ReminderType,
    backed_off,
    compose,
    decide,
    default_training_days,
    due_slots,
    in_quiet_hours,
    skip_reason,
)

CAIRO = ZoneInfo("Africa/Cairo")
UTC = dt.UTC
NONE = Facts()


def utc(*args: int) -> dt.datetime:
    return dt.datetime(*args, tzinfo=UTC)


# --- schedules ----------------------------------------------------------------------


def test_default_training_days_are_spread_out() -> None:
    assert default_training_days(3) == (0, 2, 4)
    assert default_training_days(4) == (0, 1, 3, 4)
    assert default_training_days(5) == (0, 1, 2, 3, 4)
    assert default_training_days(2) == (0, 3)
    assert default_training_days(7) == (0, 1, 2, 3, 4, 5, 6)


def test_a_slot_is_due_at_its_local_time_and_shortly_after() -> None:
    schedule = {"days": [2], "time": "18:00"}  # Wednesdays
    # 2026-11-11 is a Wednesday; Cairo is UTC+2 in November.
    on_time = due_slots(ReminderType.WORKOUT, schedule, utc(2026, 11, 11, 16, 0), CAIRO)
    assert [s.at_utc for s in on_time] == [utc(2026, 11, 11, 16, 0)]
    late = due_slots(ReminderType.WORKOUT, schedule, utc(2026, 11, 11, 16, 9), CAIRO)
    assert len(late) == 1  # a missed tick still sends within the grace window
    assert (
        due_slots(ReminderType.WORKOUT, schedule, utc(2026, 11, 11, 15, 59), CAIRO)
        == []
    )
    assert (
        due_slots(ReminderType.WORKOUT, schedule, utc(2026, 11, 11, 16, 30), CAIRO)
        == []
    )
    # Not a training day.
    assert (
        due_slots(ReminderType.WORKOUT, schedule, utc(2026, 11, 12, 16, 0), CAIRO) == []
    )


def test_the_timezone_decides_the_slot() -> None:
    schedule = {"days": [0, 1, 2, 3, 4, 5, 6], "time": "08:00"}
    london = ZoneInfo("Europe/London")
    (slot,) = due_slots(ReminderType.WORKOUT, schedule, utc(2026, 11, 11, 8, 0), london)
    assert slot.local.hour == 8
    assert (
        due_slots(ReminderType.WORKOUT, schedule, utc(2026, 11, 11, 8, 0), CAIRO) == []
    )


def test_meals_have_one_slot_each() -> None:
    schedule = {"times": {"breakfast": "09:00", "lunch": "14:30"}}
    (slot,) = due_slots(ReminderType.MEALS, schedule, utc(2026, 11, 11, 12, 30), CAIRO)
    assert slot.key == "lunch"


def test_weekly_reminders_come_once_a_week() -> None:
    schedule = {"weekday": 4, "time": "08:00"}  # Friday
    assert due_slots(ReminderType.WEIGH_IN, schedule, utc(2026, 11, 13, 6, 0), CAIRO)
    assert not due_slots(
        ReminderType.WEIGH_IN, schedule, utc(2026, 11, 12, 6, 0), CAIRO
    )


def test_review_ready_is_never_scheduled() -> None:
    assert (
        due_slots(ReminderType.REVIEW_READY, {}, utc(2026, 11, 11, 16, 0), CAIRO) == []
    )


def test_daylight_saving_never_doubles_or_drops_a_slot() -> None:
    new_york = ZoneInfo("America/New_York")
    every_day = {"days": [0, 1, 2, 3, 4, 5, 6], "time": "01:30"}

    def fired(start: dt.datetime, hours: int) -> list[dt.datetime]:
        seen: set[dt.datetime] = set()
        for minute in range(hours * 60):
            now = start + dt.timedelta(minutes=minute)
            seen |= {
                s.at_utc
                for s in due_slots(ReminderType.WORKOUT, every_day, now, new_york)
            }
        return sorted(seen)

    # Clocks go back on 2026-11-01: 01:30 happens twice locally, but fires once.
    assert len(fired(utc(2026, 11, 1, 0, 0), 24)) == 1
    # Clocks go forward on 2026-03-08: 02:30 does not exist; a 02:30 slot still fires once.
    gap = {"days": [0, 1, 2, 3, 4, 5, 6], "time": "02:30"}
    seen = {
        s.at_utc
        for minute in range(24 * 60)
        for s in due_slots(
            ReminderType.WORKOUT,
            gap,
            utc(2026, 3, 8, 0, 0) + dt.timedelta(minutes=minute),
            new_york,
        )
    }
    assert len(seen) == 1


# --- quiet hours, skips, cap, back-off ----------------------------------------------


def test_quiet_hours_overnight_and_daytime() -> None:
    night = (dt.time(22, 0), dt.time(7, 0))
    assert in_quiet_hours(dt.time(23, 30), *night)
    assert in_quiet_hours(dt.time(6, 59), *night)
    assert not in_quiet_hours(dt.time(7, 0), *night)
    assert not in_quiet_hours(dt.time(12, 0), *night)
    assert in_quiet_hours(dt.time(14, 0), dt.time(13, 0), dt.time(15, 0))
    assert not in_quiet_hours(dt.time(14, 0), None, None)
    assert not in_quiet_hours(dt.time(14, 0), dt.time(9, 0), dt.time(9, 0))


def test_skip_rules() -> None:
    today = dt.date(2026, 11, 11)  # Wednesday
    assert (
        skip_reason(
            ReminderType.MEALS, "lunch", Facts(meals_logged={"lunch"}), today, ()
        )
        == "already_logged"
    )
    assert (
        skip_reason(
            ReminderType.MEALS, "dinner", Facts(meals_logged={"lunch"}), today, ()
        )
        is None
    )
    assert (
        skip_reason(
            ReminderType.WATER,
            "",
            Facts(water_ml=2600, water_target_ml=2500),
            today,
            (),
        )
        == "target_met"
    )
    assert (
        skip_reason(
            ReminderType.WATER, "", Facts(water_ml=500, water_target_ml=2500), today, ()
        )
        is None
    )
    assert (
        skip_reason(ReminderType.WORKOUT, "", Facts(trained_on={today}), today, ())
        == "already_trained"
    )
    assert (
        skip_reason(ReminderType.WEIGH_IN, "", Facts(weighed_today=True), today, ())
        == "already_logged"
    )
    assert (
        skip_reason(ReminderType.CHECKIN, "", Facts(checkin_this_week=True), today, ())
        == "already_done"
    )
    assert skip_reason(ReminderType.PROGRESS_PHOTO, "", NONE, today, ()) == "no_consent"
    assert (
        skip_reason(
            ReminderType.PROGRESS_PHOTO, "", Facts(body_photos_consent=True), today, ()
        )
        is None
    )
    assert (
        skip_reason(ReminderType.REVIEW_READY, "", NONE, today, ()) == "nothing_waiting"
    )


def test_missed_workout_nudge_only_after_a_skipped_training_day() -> None:
    today = dt.date(2026, 11, 11)  # Wednesday; yesterday was Tuesday (1)
    yesterday = today - dt.timedelta(days=1)
    assert skip_reason(ReminderType.MISSED_WORKOUT, "", NONE, today, (1, 3)) is None
    assert (
        skip_reason(
            ReminderType.MISSED_WORKOUT,
            "",
            Facts(trained_on={yesterday}),
            today,
            (1, 3),
        )
        == "not_missed"
    )
    assert (
        skip_reason(ReminderType.MISSED_WORKOUT, "", NONE, today, (0, 2))
        == "not_missed"
    )
    assert (
        skip_reason(
            ReminderType.MISSED_WORKOUT, "", Facts(trained_on={today}), today, (1,)
        )
        == "already_trained"
    )


def test_back_off_after_ignored_reminders() -> None:
    now = utc(2026, 11, 11, 12, 0)
    ignored = [(now - dt.timedelta(hours=h), False) for h in (20, 44, 68)]
    assert backed_off(ignored, now)
    # Two days after the last one, try again.
    assert not backed_off([(now - dt.timedelta(hours=49), False)] + ignored[1:], now)
    # Any one opened: no back-off.
    assert not backed_off([(now - dt.timedelta(hours=20), True)] + ignored[1:], now)
    assert not backed_off(ignored[:2], now)


def test_decide_applies_every_rule_in_order() -> None:
    now = utc(2026, 11, 11, 16, 0)
    local = now.astimezone(CAIRO)
    quiet = (dt.time(22, 0), dt.time(7, 0))
    kwargs = {
        "rtype": ReminderType.WORKOUT,
        "slot_key": "",
        "local": local,
        "now": now,
        "facts": NONE,
        "training_days": (2,),
        "quiet": quiet,
        "sent_today": 0,
        "max_per_day": 6,
        "recent": [],
        "snooze_until": None,
    }
    assert decide(**kwargs).send
    assert (
        decide(**{**kwargs, "snooze_until": now + dt.timedelta(hours=1)}).reason
        == "snoozed"
    )
    assert (
        decide(**{**kwargs, "quiet": (dt.time(17, 0), dt.time(19, 0))}).reason
        == "quiet_hours"
    )
    assert decide(**{**kwargs, "sent_today": 6}).reason == "daily_cap"
    assert (
        decide(**{**kwargs, "facts": Facts(trained_on={local.date()})}).reason
        == "already_trained"
    )
    recent = [(now - dt.timedelta(hours=h), False) for h in (20, 44, 68)]
    assert decide(**{**kwargs, "recent": recent}).reason == "backed_off"


# --- messages ------------------------------------------------------------------------


def test_messages_use_facts_and_hide_numbers_unless_asked() -> None:
    facts = MessageFacts(day_name="Legs", first_exercise="Back Squat", kcal_left=900)
    msg = compose(ReminderType.WORKOUT, "", facts, "en", show_details=False, seed=0)
    assert "Legs" in msg.body and "Back Squat" in msg.body and msg.path == "/train"
    lunch = compose(
        ReminderType.MEALS, "lunch", facts, "en", show_details=False, seed=0
    )
    assert "900" not in lunch.body
    detailed = compose(
        ReminderType.MEALS, "lunch", facts, "en", show_details=True, seed=0
    )
    assert "900" in detailed.body
    arabic = compose(ReminderType.WORKOUT, "", facts, "ar", show_details=False, seed=1)
    assert "Back Squat" in arabic.body and any("؀" <= c <= "ۿ" for c in arabic.body)


def test_messages_vary_and_every_type_has_text() -> None:
    bodies = {
        compose(
            ReminderType.WATER, "", MessageFacts(), "en", show_details=False, seed=s
        ).body
        for s in range(5)
    }
    assert len(bodies) > 1
    for rtype in ReminderType:
        for lang in ("en", "ar"):
            msg = compose(
                rtype, "breakfast", MessageFacts(), lang, show_details=True, seed=3
            )
            assert msg.title and msg.body and msg.path.startswith("/")
            assert len(msg.body) <= 160


def test_every_type_has_a_default() -> None:
    assert set(DEFAULTS) == set(ReminderType)
