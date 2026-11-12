"""Reminders as pure functions: when a slot is due, whether to skip it, what it says.

The scheduler (app/jobs/scheduler.py) gathers the facts from the database and calls
these; nothing here reads the clock, the database or the network.
"""

import datetime as dt
import enum
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
from zoneinfo import ZoneInfo

GRACE: Final = dt.timedelta(minutes=10)  # a slot still fires if a tick was missed
BACKOFF_AFTER: Final = 3  # ignored reminders of one type in a row
BACKOFF_PAUSE: Final = dt.timedelta(hours=48)
MAX_BODY_CHARS: Final = 160
EVERY_DAY: Final = (0, 1, 2, 3, 4, 5, 6)


class ReminderType(enum.StrEnum):
    WORKOUT = "workout"
    PRE_WORKOUT_MEAL = "pre_workout_meal"
    MEALS = "meals"
    WATER = "water"
    WEIGH_IN = "weigh_in"
    PROGRESS_PHOTO = "progress_photo"
    CHECKIN = "checkin"
    MISSED_WORKOUT = "missed_workout"
    REVIEW_READY = "review_ready"  # sent when a weekly review has suggestions


@dataclass(frozen=True)
class Default:
    enabled: bool
    schedule: Mapping[str, Any]


# Weekly ones default to Egypt's weekend (Friday = 4) or Thursday evening (3).
DEFAULTS: Final[dict[ReminderType, Default]] = {
    ReminderType.WORKOUT: Default(True, {"time": "18:00"}),  # days: the user's
    ReminderType.PRE_WORKOUT_MEAL: Default(False, {"time": "16:30"}),
    ReminderType.MEALS: Default(
        False, {"times": {"breakfast": "09:00", "lunch": "14:30", "dinner": "20:30"}}
    ),
    ReminderType.WATER: Default(False, {"times": ["11:00", "15:00", "19:00"]}),
    ReminderType.WEIGH_IN: Default(True, {"weekday": 4, "time": "09:00"}),
    ReminderType.PROGRESS_PHOTO: Default(False, {"weekday": 4, "time": "10:00"}),
    ReminderType.CHECKIN: Default(True, {"weekday": 3, "time": "20:00"}),
    ReminderType.MISSED_WORKOUT: Default(True, {"time": "12:00"}),
    ReminderType.REVIEW_READY: Default(True, {}),
}


def default_training_days(days_per_week: int) -> tuple[int, ...]:
    """Training weekdays (Monday = 0) spread over the week, with rest days between."""
    n = max(1, min(7, days_per_week))
    spread = {
        1: (0,),
        2: (0, 3),
        3: (0, 2, 4),
        4: (0, 1, 3, 4),
        5: (0, 1, 2, 3, 4),
        6: (0, 1, 2, 3, 4, 5),
        7: EVERY_DAY,
    }
    return spread[n]


# --- slots ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Slot:
    key: str  # which meal or water time; "" for single-slot reminders
    local: dt.datetime  # in the user's timezone
    at_utc: dt.datetime


def _time(text: str) -> dt.time:
    hours, minutes = text.split(":")
    return dt.time(int(hours), int(minutes))


def _slot(key: str, day: dt.date, at: str, tz: ZoneInfo) -> Slot:
    # fold=0: a time that happens twice (clocks back) is the first one; a time that
    # never happens (clocks forward) maps to one real instant. Either way: one slot.
    local = dt.datetime.combine(day, _time(at), tzinfo=tz)
    at_utc = local.astimezone(dt.UTC)
    return Slot(key, at_utc.astimezone(tz), at_utc)


def _slots_on(
    rtype: ReminderType,
    schedule: Mapping[str, Any],
    day: dt.date,
    tz: ZoneInfo,
    training_days: Collection[int],
) -> list[Slot]:
    weekday = day.weekday()
    match rtype:
        case ReminderType.WORKOUT:
            days = schedule.get("days", training_days)
            return [_slot("", day, schedule["time"], tz)] if weekday in days else []
        case ReminderType.PRE_WORKOUT_MEAL:
            return (
                [_slot("", day, schedule["time"], tz)]
                if weekday in training_days
                else []
            )
        case ReminderType.MEALS:
            return [_slot(k, day, t, tz) for k, t in schedule.get("times", {}).items()]
        case ReminderType.WATER:
            return [_slot(t, day, t, tz) for t in schedule.get("times", [])]
        case ReminderType.WEIGH_IN | ReminderType.PROGRESS_PHOTO | ReminderType.CHECKIN:
            if weekday != schedule.get("weekday"):
                return []
            return [_slot("", day, schedule["time"], tz)]
        case ReminderType.MISSED_WORKOUT:
            return [_slot("", day, schedule["time"], tz)]
        case _:
            return []  # review_ready is sent by the weekly review, not on a clock


def due_slots(
    rtype: ReminderType,
    schedule: Mapping[str, Any],
    now: dt.datetime,
    tz: ZoneInfo,
    training_days: Collection[int] = EVERY_DAY,
    window: dt.timedelta = GRACE,
) -> list[Slot]:
    """Slots that came due in (now - window, now]."""
    today = now.astimezone(tz).date()
    found = []
    for day in (today - dt.timedelta(days=1), today):  # a slot just before midnight
        for slot in _slots_on(rtype, schedule, day, tz, training_days):
            if now - window < slot.at_utc <= now:
                found.append(slot)
    return found


# --- deciding -------------------------------------------------------------------------


@dataclass(frozen=True)
class Facts:
    """What the user already did, for skip rules. Dates are in the user's timezone."""

    trained_on: Collection[dt.date] = frozenset()  # finished workouts, last two days
    meals_logged: Collection[str] = frozenset()  # meal slots logged today
    water_ml: int = 0
    water_target_ml: int | None = None
    weighed_today: bool = False
    photo_this_week: bool = False
    checkin_this_week: bool = False
    body_photos_consent: bool = False
    review_pending: int = 0


def in_quiet_hours(t: dt.time, start: dt.time | None, end: dt.time | None) -> bool:
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= t < end
    return t >= start or t < end  # overnight, e.g. 22:00 to 07:00


def skip_reason(
    rtype: ReminderType,
    slot_key: str,
    facts: Facts,
    today: dt.date,
    training_days: Collection[int],
) -> str | None:
    """Why this reminder is not needed today, or None if it is."""
    trained_today = today in facts.trained_on
    match rtype:
        case ReminderType.WORKOUT | ReminderType.PRE_WORKOUT_MEAL:
            return "already_trained" if trained_today else None
        case ReminderType.MEALS:
            return "already_logged" if slot_key in facts.meals_logged else None
        case ReminderType.WATER:
            target = facts.water_target_ml
            return "target_met" if target and facts.water_ml >= target else None
        case ReminderType.WEIGH_IN:
            return "already_logged" if facts.weighed_today else None
        case ReminderType.PROGRESS_PHOTO:
            if not facts.body_photos_consent:
                return "no_consent"
            return "already_done" if facts.photo_this_week else None
        case ReminderType.CHECKIN:
            return "already_done" if facts.checkin_this_week else None
        case ReminderType.MISSED_WORKOUT:
            if trained_today:
                return "already_trained"
            yesterday = today - dt.timedelta(days=1)
            planned = yesterday.weekday() in training_days
            return (
                None if planned and yesterday not in facts.trained_on else "not_missed"
            )
        case _:
            return None if facts.review_pending > 0 else "nothing_waiting"


def backed_off(recent: Sequence[tuple[dt.datetime, bool]], now: dt.datetime) -> bool:
    """After several ignored reminders of one type, pause for two days.

    `recent`: (sent_at, opened) of this type's last sends, newest first.
    """
    last = recent[:BACKOFF_AFTER]
    if len(last) < BACKOFF_AFTER or any(opened for _, opened in last):
        return False
    return now - last[0][0] < BACKOFF_PAUSE


@dataclass(frozen=True)
class Decision:
    send: bool
    reason: str | None = None


def decide(
    *,
    rtype: ReminderType,
    slot_key: str,
    local: dt.datetime,
    now: dt.datetime,
    facts: Facts,
    training_days: Collection[int],
    quiet: tuple[dt.time | None, dt.time | None],
    sent_today: int,
    max_per_day: int,
    recent: Sequence[tuple[dt.datetime, bool]],
    snooze_until: dt.datetime | None,
) -> Decision:
    if snooze_until is not None and now < snooze_until:
        return Decision(False, "snoozed")
    if in_quiet_hours(local.time(), *quiet):
        return Decision(False, "quiet_hours")
    reason = skip_reason(rtype, slot_key, facts, local.date(), training_days)
    if reason is not None:
        return Decision(False, reason)
    if sent_today >= max_per_day:
        return Decision(False, "daily_cap")
    if backed_off(recent, now):
        return Decision(False, "backed_off")
    return Decision(True)


# --- messages -------------------------------------------------------------------------


@dataclass(frozen=True)
class MessageFacts:
    """Facts from the database for the text. Names are shown; numbers only if asked."""

    day_name: str | None = None
    first_exercise: str | None = None
    kcal_left: int | None = None
    water_left_ml: int | None = None


@dataclass(frozen=True)
class Message:
    title: str
    body: str
    path: str  # where a tap opens, without the language prefix


MEAL_NAMES: Final = {
    "en": {"breakfast": "breakfast", "lunch": "lunch", "dinner": "dinner"},
    "ar": {"breakfast": "الفطار", "lunch": "الغدا", "dinner": "العشا"},
}
PATHS: Final = {
    ReminderType.WORKOUT: "/train",
    ReminderType.PRE_WORKOUT_MEAL: "/food",
    ReminderType.MEALS: "/food",
    ReminderType.WATER: "/today",
    ReminderType.WEIGH_IN: "/today",
    ReminderType.PROGRESS_PHOTO: "/progress",
    ReminderType.CHECKIN: "/today",
    ReminderType.MISSED_WORKOUT: "/train",
    ReminderType.REVIEW_READY: "/today",
}

# Variants per type and language; "detail" lines are used only with show_details.
_TEXT: Final[dict[str, dict[str, dict[str, list[str]]]]] = {
    "en": {
        "workout": {
            "named": [
                "{day} today. First up: {exercise}.",
                "Time to train: {day}, starting with {exercise}.",
                "Your {day} session is ready. {exercise} first.",
            ],
            "plain": ["Training day today. Your session is ready.", "Time to train."],
        },
        "pre_workout_meal": {
            "plain": [
                "Training soon: have a meal with carbs and protein now.",
                "Fuel up: eat something before your workout.",
            ]
        },
        "meals": {
            "detail": ["Time for {meal}. {kcal} kcal left today."],
            "plain": ["Time for {meal}. Log it in one tap.", "Don't forget {meal}."],
        },
        "water": {
            "detail": ["Water break: {water} ml to go today."],
            "plain": [
                "Water break.",
                "Have a glass of water.",
                "Time to drink some water.",
            ],
        },
        "weigh_in": {
            "plain": [
                "Weigh-in day: before breakfast, after the bathroom.",
                "Quick weigh-in this morning keeps your trend accurate.",
            ]
        },
        "progress_photo": {
            "plain": ["Progress photo day.", "Take this week's progress photo."]
        },
        "checkin": {
            "plain": [
                "How was your week? Your check-in takes a minute.",
                "Weekly check-in time: sleep, energy, soreness.",
            ]
        },
        "missed_workout": {
            "plain": [
                "Missed yesterday? No problem. Fit it in today.",
                "Yesterday's session is still waiting. A short one counts.",
            ]
        },
        "review_ready": {
            "plain": [
                "Your weekly review is ready. Approve or skip the suggestions.",
                "New suggestions from your coach are waiting.",
            ]
        },
    },
    "ar": {
        "workout": {
            "named": [
                "النهارده {day}. أول تمرين: {exercise}.",
                "يلا تمرين: {day}، هنبدأ بـ {exercise}.",
                "تمرين {day} جاهز. {exercise} الأول.",
            ],
            "plain": ["النهارده يوم تمرين. التمرين جاهز.", "يلا وقت التمرين."],
        },
        "pre_workout_meal": {
            "plain": [
                "التمرين قرّب: كُل وجبة فيها كارب وبروتين دلوقتي.",
                "كُل حاجة قبل التمرين.",
            ]
        },
        "meals": {
            "detail": ["وقت {meal}. فاضلك {kcal} سعر النهارده."],
            "plain": ["وقت {meal}. سجّله بضغطة.", "ماتنساش {meal}."],
        },
        "water": {
            "detail": ["اشرب مية: فاضلك {water} مل النهارده."],
            "plain": ["اشرب مية.", "كوباية مية دلوقتي.", "وقت تشرب مية."],
        },
        "weigh_in": {
            "plain": [
                "يوم الوزن: قبل الفطار وبعد الحمام.",
                "وزن سريع الصبح بيخلّي المتابعة مظبوطة.",
            ]
        },
        "progress_photo": {"plain": ["يوم صورة التقدّم.", "صوّر صورة الأسبوع."]},
        "checkin": {
            "plain": [
                "أسبوعك كان عامل إيه؟ المتابعة بتاخد دقيقة.",
                "وقت متابعة الأسبوع: النوم والطاقة والشد.",
            ]
        },
        "missed_workout": {
            "plain": [
                "فاتك تمرين امبارح؟ مفيش مشكلة. اعمله النهارده.",
                "تمرين امبارح لسه مستنيك. حتى تمرين قصير بيفرق.",
            ]
        },
        "review_ready": {
            "plain": [
                "مراجعة الأسبوع جاهزة. وافق أو ارفض الاقتراحات.",
                "فيه اقتراحات جديدة من الكوتش مستنياك.",
            ]
        },
    },
}
TITLE: Final = {"en": "FitCoach", "ar": "FitCoach"}


def compose(
    rtype: ReminderType,
    slot_key: str,
    facts: MessageFacts,
    language: str,
    *,
    show_details: bool,
    seed: int,
) -> Message:
    """A short, varied message. `seed` (e.g. the day number) picks the variant."""
    lang = "ar" if language == "ar" else "en"
    texts = _TEXT[lang][rtype.value]
    values: dict[str, Any] = {
        "meal": MEAL_NAMES[lang].get(slot_key, MEAL_NAMES[lang]["lunch"]),
        "day": facts.day_name,
        "exercise": facts.first_exercise,
        "kcal": facts.kcal_left,
        "water": facts.water_left_ml,
    }
    if rtype is ReminderType.WORKOUT and facts.day_name and facts.first_exercise:
        group = "named"
    elif (
        show_details
        and "detail" in texts
        and (
            (rtype is ReminderType.MEALS and facts.kcal_left is not None)
            or (rtype is ReminderType.WATER and facts.water_left_ml is not None)
        )
    ):
        group = "detail"
    else:
        group = "plain"
    variants = texts[group]
    body = variants[seed % len(variants)].format(**values)
    return Message(TITLE[lang], body[:MAX_BODY_CHARS], PATHS[rtype])
