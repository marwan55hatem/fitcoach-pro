"""A realistic small catalog covering every template slot, for program tests."""

from sqlalchemy.orm import Session

from app.models import Exercise
from app.models.enums import Equipment, Experience, Mechanic
from app.rules.exercise_catalog import CatalogExercise, slugify

E = Equipment
C, I = Mechanic.COMPOUND, Mechanic.ISOLATION
BEG, INT, ADV = Experience.BEGINNER, Experience.INTERMEDIATE, Experience.ADVANCED

# name, equipment, primary, secondary, mechanic, level
ROWS: list[tuple[str, Equipment, str, tuple[str, ...], Mechanic, Experience]] = [
    (
        "Barbell Bench Press - Medium Grip",
        E.BARBELL,
        "chest",
        ("shoulders", "triceps"),
        C,
        BEG,
    ),
    ("Incline Dumbbell Press", E.DUMBBELL, "chest", ("shoulders", "triceps"), C, INT),
    ("Dumbbell Flyes", E.DUMBBELL, "chest", (), I, BEG),
    ("Cable Crossover", E.CABLE, "chest", (), I, INT),
    ("Butterfly", E.MACHINE, "chest", (), I, BEG),
    ("Pushups", E.BODY_WEIGHT, "chest", ("shoulders", "triceps"), C, BEG),
    ("Triceps Pushdown", E.CABLE, "triceps", (), I, BEG),
    ("EZ-Bar Skullcrusher", E.EZ_BAR, "triceps", (), I, INT),
    ("Lying Triceps Press", E.EZ_BAR, "triceps", (), I, INT),
    ("Wide-Grip Lat Pulldown", E.CABLE, "lats", ("biceps", "shoulders"), C, BEG),
    ("Pullups", E.BODY_WEIGHT, "lats", ("biceps",), C, INT),
    (
        "Bent Over Barbell Row",
        E.BARBELL,
        "middle_back",
        ("biceps", "lats", "shoulders"),
        C,
        BEG,
    ),
    (
        "Seated Cable Rows",
        E.CABLE,
        "middle_back",
        ("biceps", "lats", "shoulders"),
        C,
        BEG,
    ),
    ("Face Pull", E.CABLE, "shoulders", ("middle_back",), C, INT),
    ("Barbell Shoulder Press", E.BARBELL, "shoulders", ("triceps",), C, INT),
    ("Dumbbell Shoulder Press", E.DUMBBELL, "shoulders", ("triceps",), C, INT),
    ("Side Lateral Raise", E.DUMBBELL, "shoulders", (), I, BEG),
    ("Barbell Curl", E.BARBELL, "biceps", (), I, BEG),
    ("Hammer Curls", E.DUMBBELL, "biceps", (), I, BEG),
    (
        "Barbell Full Squat",
        E.BARBELL,
        "quads",
        ("calves", "glutes", "hamstrings", "lower_back"),
        C,
        INT,
    ),
    ("Leg Press", E.MACHINE, "quads", ("glutes", "hamstrings"), C, BEG),
    (
        "Romanian Deadlift",
        E.BARBELL,
        "hamstrings",
        ("calves", "glutes", "lower_back"),
        C,
        INT,
    ),
    ("Lying Leg Curls", E.MACHINE, "hamstrings", (), I, BEG),
    ("Seated Leg Curl", E.MACHINE, "hamstrings", (), I, BEG),
    ("Standing Calf Raises", E.MACHINE, "calves", (), I, BEG),
    ("Snatch Pull", E.BARBELL, "hamstrings", ("traps",), C, ADV),
]
FULL_GYM = (E.BARBELL, E.DUMBBELL, E.EZ_BAR, E.CABLE, E.MACHINE, E.BENCH)


def catalog() -> dict[str, CatalogExercise]:
    items = {}
    for name, equipment, primary, secondary, mechanic, level in ROWS:
        key = slugify(name)
        items[key] = CatalogExercise(
            id=key,
            name=name,
            muscle_primary=primary,
            muscles_secondary=secondary,
            equipment=equipment,
            level=level,
            mechanic=mechanic,
            category="strength",
        )
    return items


def add_library(db: Session) -> dict[str, Exercise]:
    """Insert ROWS as catalog exercises (no images), keyed by name."""
    rows = {}
    for name, equipment, primary, secondary, mechanic, level in ROWS:
        row = Exercise(
            name=name,
            slug=f"t-{slugify(name)}",
            muscle_primary=primary,
            muscles_secondary=list(secondary),
            equipment=equipment.value,
            level=level,
            mechanic=mechanic,
            category="strength",
            image_keys=[],
            instructions=[],
        )
        db.add(row)
        rows[name] = row
    db.flush()
    return rows
