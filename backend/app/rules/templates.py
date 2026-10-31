"""Split templates and the pure-rules program builder.

The builder is also the safety net: when the AI is unavailable or keeps proposing invalid
programs, the user still gets a sound program built from these templates.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.models.enums import Experience, Mechanic
from app.rules.default_plan import DEFAULT_SPLIT
from app.rules.exercise_catalog import CatalogExercise
from app.rules.program_validator import (
    MAJOR_GROUPS,
    MUSCLE_GROUPS,
    VOLUME_RANGES,
    DayDraft,
    ExerciseDraft,
    ProgramContext,
    ProgramDraft,
    estimated_minutes,
    exercise_problem,
    weekly_sets,
)

MIN_SETS_WHEN_TRIMMING = 2
SHORTLIST_MAJOR = 10
SHORTLIST_MINOR = 4


@dataclass(frozen=True)
class Slot:
    muscle: str
    compound: bool
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class TemplateDay:
    name: str
    focus: str
    slots: tuple[Slot, ...]


@dataclass(frozen=True)
class SplitTemplate:
    key: str
    name: str
    days: tuple[TemplateDay, ...]


@dataclass(frozen=True)
class Scheme:
    sets: int
    rep_min: int
    rep_max: int
    rest_s: int


# (compound scheme, isolation scheme)
SCHEMES: dict[Experience, tuple[Scheme, Scheme]] = {
    Experience.BEGINNER: (Scheme(3, 8, 12, 120), Scheme(2, 10, 15, 75)),
    Experience.INTERMEDIATE: (Scheme(4, 6, 10, 150), Scheme(3, 10, 15, 90)),
    Experience.ADVANCED: (Scheme(4, 5, 8, 180), Scheme(3, 8, 12, 90)),
}

# Muscle and movement type of each DEFAULT_SPLIT slot, in the same order.
_DEFAULT_SLOT_FACTS: tuple[tuple[str, bool], ...] = (
    ("chest", True),
    ("chest", True),
    ("chest", False),
    ("triceps", False),
    ("lats", True),
    ("middle_back", True),
    ("middle_back", True),
    ("shoulders", False),
    ("shoulders", True),
    ("shoulders", False),
    ("biceps", False),
    ("biceps", False),
    ("triceps", False),
    ("quads", True),
    ("hamstrings", True),
    ("quads", True),
    ("hamstrings", False),
    ("calves", False),
)
_DAY_FOCUS = {
    "Day 1 Bench": "Chest and triceps",
    "Day 2 Back": "Back and rear shoulders",
    "Day 3 Arms+Shoulders": "Shoulders and arms",
    "Day 4 Legs": "Legs",
}


def _default_split_days() -> tuple[TemplateDay, ...]:
    days: dict[str, list[Slot]] = {}
    for plan_slot, (muscle, compound) in zip(
        DEFAULT_SPLIT, _DEFAULT_SLOT_FACTS, strict=True
    ):
        days.setdefault(plan_slot.day, []).append(
            Slot(muscle, compound, plan_slot.candidates)
        )
    return tuple(
        TemplateDay(name, _DAY_FOCUS[name], tuple(slots))
        for name, slots in days.items()
    )


_SQUAT = Slot("quads", True, ("Barbell Full Squat", "Barbell Squat", "Leg Press"))
_RDL = Slot("hamstrings", True, ("Romanian Deadlift",))
_BENCH = Slot("chest", True, ("Barbell Bench Press - Medium Grip",))
_INCLINE = Slot("chest", True, ("Incline Dumbbell Press",))
_FLY = Slot("chest", False, ("Dumbbell Flyes", "Cable Crossover", "Butterfly"))
_PULLDOWN = Slot("lats", True, ("Wide-Grip Lat Pulldown", "Pullups"))
_ROW = Slot("middle_back", True, ("Bent Over Barbell Row", "Seated Cable Rows"))
_CABLE_ROW = Slot("middle_back", True, ("Seated Cable Rows",))
_PRESS = Slot("shoulders", True, ("Barbell Shoulder Press", "Dumbbell Shoulder Press"))
_LATERAL = Slot("shoulders", False, ("Side Lateral Raise", "Seated Side Lateral Raise"))
_CURL = Slot("biceps", False, ("Barbell Curl", "Hammer Curls"))
_HAMMER = Slot("biceps", False, ("Hammer Curls", "Alternate Hammer Curl"))
_PUSHDOWN = Slot("triceps", False, ("Triceps Pushdown",))
_SKULL = Slot("triceps", False, ("EZ-Bar Skullcrusher", "Lying Triceps Press"))
_LEG_PRESS = Slot("quads", True, ("Leg Press",))
_LEG_CURL = Slot("hamstrings", False, ("Lying Leg Curls", "Seated Leg Curl"))
_CALF = Slot("calves", False, ("Standing Calf Raises", "Seated Calf Raise"))

FOUR_DAY_SPLIT = SplitTemplate("four_day_split", "4-day split", _default_split_days())
FULL_BODY = SplitTemplate(
    "full_body",
    "Full body (up to 3 days)",
    (
        TemplateDay(
            "Full Body A",
            "Whole body, squat and bench focus",
            (_SQUAT, _BENCH, _ROW, _LATERAL, _CURL, _PUSHDOWN),
        ),
        TemplateDay(
            "Full Body B",
            "Whole body, hinge and press focus",
            (_RDL, _PRESS, _PULLDOWN, _FLY, _HAMMER, _CALF),
        ),
        TemplateDay(
            "Full Body C",
            "Whole body, machine and dumbbell focus",
            (_LEG_PRESS, _INCLINE, _CABLE_ROW, _LEG_CURL, _LATERAL, _SKULL),
        ),
    ),
)
PUSH_PULL_LEGS = SplitTemplate(
    "push_pull_legs",
    "Push / pull / legs + upper / lower (5 days)",
    (
        TemplateDay(
            "Push",
            "Chest, shoulders, triceps",
            (_BENCH, _PRESS, _FLY, _LATERAL, _PUSHDOWN),
        ),
        TemplateDay("Pull", "Back and biceps", (_PULLDOWN, _ROW, _CABLE_ROW, _CURL)),
        TemplateDay(
            "Legs", "Quads, hamstrings, calves", (_SQUAT, _RDL, _LEG_CURL, _CALF)
        ),
        TemplateDay(
            "Upper", "Whole upper body", (_INCLINE, _ROW, _LATERAL, _HAMMER, _SKULL)
        ),
        TemplateDay("Lower", "Whole lower body", (_LEG_PRESS, _RDL, _LEG_CURL, _CALF)),
    ),
)
TEMPLATES: dict[str, SplitTemplate] = {
    t.key: t for t in (FOUR_DAY_SPLIT, FULL_BODY, PUSH_PULL_LEGS)
}


def choose_template(days_per_week: int) -> SplitTemplate:
    """Up to 3 days: full body. 4: the default split. 5 or more: 5 days, keeping rest days."""
    if days_per_week <= 3:
        return FULL_BODY
    return FOUR_DAY_SPLIT if days_per_week == 4 else PUSH_PULL_LEGS


def scheme_for(experience: Experience, compound: bool) -> Scheme:
    return SCHEMES[experience][0 if compound else 1]


def usable_exercises(ctx: ProgramContext) -> list[CatalogExercise]:
    return sorted(
        (e for e in ctx.catalog.values() if exercise_problem(e, ctx) is None),
        key=lambda e: e.name.lower(),
    )


def _pick(
    slot: Slot, usable: Sequence[CatalogExercise], used: set[str]
) -> CatalogExercise | None:
    remaining = [e for e in usable if e.id not in used]
    by_name = {e.name.lower(): e for e in remaining}
    for candidate in slot.candidates:
        if candidate.lower() in by_name:
            return by_name[candidate.lower()]
    wanted = Mechanic.COMPOUND if slot.compound else Mechanic.ISOLATION
    same_muscle = [e for e in remaining if e.muscle_primary == slot.muscle]
    same_muscle.sort(key=lambda e: (e.mechanic is not wanted, e.name.lower()))
    return same_muscle[0] if same_muscle else None


def _fit_session(day: DayDraft, session_minutes: int) -> DayDraft:
    """Trim sets from the end of the day, then whole exercises, until the day fits."""
    exercises = list(day.exercises)
    while exercises:
        trimmed = replace(day, exercises=tuple(exercises))
        if estimated_minutes(trimmed) <= session_minutes:
            return trimmed
        for index in range(len(exercises) - 1, -1, -1):
            if exercises[index].sets > MIN_SETS_WHEN_TRIMMING:
                exercises[index] = replace(
                    exercises[index], sets=exercises[index].sets - 1
                )
                break
        else:
            exercises.pop()
    return replace(day, exercises=())


def _group(muscle: str) -> str:
    return MUSCLE_GROUPS.get(muscle, muscle)


def _volume_trim_target(
    days: list[DayDraft], group: str, ctx: ProgramContext
) -> tuple[int, int] | None:
    """The best exercise to cut for an over-volume group: direct isolation work first."""
    options: list[tuple[bool, bool, int, int]] = []
    for day_index, day in enumerate(days):
        for item_index, item in enumerate(day.exercises):
            exercise = ctx.catalog[item.exercise_id]
            direct = _group(exercise.muscle_primary) == group
            indirect = group in {_group(m) for m in exercise.muscles_secondary}
            removable = item.sets > MIN_SETS_WHEN_TRIMMING or len(day.exercises) > 1
            if (direct or indirect) and removable:
                isolation = exercise.mechanic is Mechanic.ISOLATION
                options.append((not direct, not isolation, -day_index, -item_index))
    if not options:
        return None
    _, _, day_key, item_key = min(options)
    return -day_key, -item_key


def _fit_volume(days: list[DayDraft], ctx: ProgramContext) -> list[DayDraft]:
    """Trim sets until no muscle group is over the weekly maximum.

    Real exercises load several muscles (rows and presses both reach the shoulders), so
    a template that looks balanced by primary muscle can still go over.
    """
    _, maximum = VOLUME_RANGES[ctx.experience]
    days = list(days)
    while True:
        volume = weekly_sets(ProgramDraft("", tuple(days)), ctx.catalog)
        over = [group for group, sets in volume.items() if sets > maximum]
        if not over:
            return days
        target = _volume_trim_target(days, max(over, key=volume.__getitem__), ctx)
        if target is None:
            return days
        day_index, item_index = target
        day = days[day_index]
        items = list(day.exercises)
        if items[item_index].sets > MIN_SETS_WHEN_TRIMMING:
            items[item_index] = replace(
                items[item_index], sets=items[item_index].sets - 1
            )
        else:
            del items[item_index]
        days[day_index] = replace(day, exercises=tuple(items))


def build_template_program(
    template: SplitTemplate, ctx: ProgramContext
) -> ProgramDraft:
    usable = usable_exercises(ctx)
    days: list[DayDraft] = []
    for template_day in template.days[: ctx.days_per_week]:
        used: set[str] = set()
        chosen: list[tuple[CatalogExercise, Slot]] = []
        for slot in template_day.slots:
            exercise = _pick(slot, usable, used)
            if exercise is not None:
                used.add(exercise.id)
                chosen.append((exercise, slot))
        # Substitutes can change the movement type, so keep compounds first.
        chosen.sort(key=lambda pair: pair[0].mechanic is Mechanic.ISOLATION)
        exercises = []
        for exercise, _slot in chosen:
            scheme = scheme_for(
                ctx.experience, exercise.mechanic is not Mechanic.ISOLATION
            )
            exercises.append(
                ExerciseDraft(
                    exercise.id,
                    scheme.sets,
                    scheme.rep_min,
                    scheme.rep_max,
                    scheme.rest_s,
                )
            )
        day = DayDraft(
            template_day.name,
            template_day.focus,
            f"Template day focused on {template_day.focus.lower()}.",
            tuple(exercises),
        )
        fitted = _fit_session(day, ctx.session_minutes)
        if fitted.exercises:
            days.append(fitted)
    days = _fit_volume(days, ctx)
    return ProgramDraft(
        f"{template.name} built from standard rules for your level, equipment and "
        "limitations.",
        tuple(days),
    )


def shortlist_for_ai(
    template: SplitTemplate, ctx: ProgramContext
) -> list[CatalogExercise]:
    """A compact allowed list: template exercises first, then compounds, per muscle group.

    Only exercises the validator would accept are included, and capping them keeps the
    prompt small enough for the provider's per-minute token limit.
    """
    preferred = {
        name.lower()
        for day in template.days
        for slot in day.slots
        for name in slot.candidates
    }
    ranked = sorted(
        usable_exercises(ctx),
        key=lambda e: (
            e.name.lower() not in preferred,
            e.mechanic is not Mechanic.COMPOUND,
            e.name.lower(),
        ),
    )
    counts: dict[str, int] = {}
    shortlist: list[CatalogExercise] = []
    for exercise in ranked:
        group = MUSCLE_GROUPS.get(exercise.muscle_primary, exercise.muscle_primary)
        cap = SHORTLIST_MAJOR if group in MAJOR_GROUPS else SHORTLIST_MINOR
        if counts.get(group, 0) < cap:
            counts[group] = counts.get(group, 0) + 1
            shortlist.append(exercise)
    return shortlist
