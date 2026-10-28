"""The default 4-day split as pure data: Bench / Back / Arms+Shoulders / Legs.

Each slot lists acceptable library exercise names in priority order (exact dataset names,
case-insensitive). The program templates (later step) and the library import check share
this, so "does the library cover the plan" has one definition.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanSlot:
    day: str
    label: str
    candidates: tuple[str, ...]


DEFAULT_SPLIT: tuple[PlanSlot, ...] = (
    # Day 1: Bench (chest focus)
    PlanSlot(
        "Day 1 Bench", "Barbell bench press", ("Barbell Bench Press - Medium Grip",)
    ),
    PlanSlot("Day 1 Bench", "Incline dumbbell press", ("Incline Dumbbell Press",)),
    PlanSlot(
        "Day 1 Bench",
        "Chest fly / cable fly",
        ("Dumbbell Flyes", "Cable Crossover", "Flat Bench Cable Flyes", "Butterfly"),
    ),
    PlanSlot(
        "Day 1 Bench",
        "Triceps pushdown",
        ("Triceps Pushdown", "Triceps Pushdown - Rope Attachment"),
    ),
    # Day 2: Back
    PlanSlot("Day 2 Back", "Lat pulldown", ("Wide-Grip Lat Pulldown",)),
    PlanSlot("Day 2 Back", "Barbell row", ("Bent Over Barbell Row",)),
    PlanSlot("Day 2 Back", "Seated cable row", ("Seated Cable Rows",)),
    PlanSlot("Day 2 Back", "Face pull", ("Face Pull",)),
    # Day 3: Arms and shoulders
    PlanSlot(
        "Day 3 Arms+Shoulders",
        "Overhead shoulder press",
        ("Barbell Shoulder Press", "Dumbbell Shoulder Press"),
    ),
    PlanSlot(
        "Day 3 Arms+Shoulders",
        "Lateral raise",
        (
            "Side Lateral Raise",
            "Seated Side Lateral Raise",
            "Cable Seated Lateral Raise",
        ),
    ),
    PlanSlot("Day 3 Arms+Shoulders", "Barbell curl", ("Barbell Curl",)),
    PlanSlot(
        "Day 3 Arms+Shoulders", "Hammer curl", ("Hammer Curls", "Alternate Hammer Curl")
    ),
    PlanSlot(
        "Day 3 Arms+Shoulders",
        "Skull crushers",
        ("EZ-Bar Skullcrusher", "Lying Triceps Press"),
    ),
    # Day 4: Legs
    PlanSlot("Day 4 Legs", "Back squat", ("Barbell Full Squat", "Barbell Squat")),
    PlanSlot("Day 4 Legs", "Romanian deadlift", ("Romanian Deadlift",)),
    PlanSlot("Day 4 Legs", "Leg press", ("Leg Press",)),
    PlanSlot("Day 4 Legs", "Leg curl", ("Lying Leg Curls", "Seated Leg Curl")),
    PlanSlot("Day 4 Legs", "Calf raise", ("Standing Calf Raises", "Seated Calf Raise")),
)
