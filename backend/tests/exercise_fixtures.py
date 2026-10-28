"""Small dataset in the shape of free-exercise-db, plus tiny generated JPEGs."""

import json
from pathlib import Path
from typing import Any

from PIL import Image

from app.rules.default_plan import DEFAULT_SPLIT


def entry(
    name: str,
    equipment: str | None,
    primary: str,
    secondary: tuple[str, ...] = (),
    *,
    level: str = "beginner",
    mechanic: str | None = "compound",
    category: str = "strength",
    images: int = 2,
) -> dict[str, Any]:
    dataset_id = name.replace(" ", "_")
    return {
        "id": dataset_id,
        "name": name,
        "force": "push",
        "level": level,
        "mechanic": mechanic,
        "equipment": equipment,
        "primaryMuscles": [primary],
        "secondaryMuscles": list(secondary),
        "instructions": [f"Step one of {name}.", f"Step two of {name}."],
        "category": category,
        "images": [f"{dataset_id}/{i}.jpg" for i in range(images)],
    }


ENTRIES: list[dict[str, Any]] = [
    entry(
        "Barbell Bench Press - Medium Grip",
        "barbell",
        "chest",
        ("shoulders", "triceps"),
        level="intermediate",
    ),
    entry(
        "Incline Dumbbell Press",
        "dumbbell",
        "chest",
        ("shoulders", "triceps"),
        level="intermediate",
    ),
    entry("Dumbbell Flyes", "dumbbell", "chest", ("shoulders",), mechanic="isolation"),
    entry(
        "Cable Crossover",
        "cable",
        "chest",
        ("shoulders",),
        level="intermediate",
        mechanic="isolation",
    ),
    entry("Butterfly", "machine", "chest", ("shoulders",), mechanic="isolation"),
    entry("Pushups", "body only", "chest", ("shoulders", "triceps")),
    entry("Triceps Pushdown", "cable", "triceps", mechanic="isolation"),
    entry(
        "Barbell Shoulder Press",
        "barbell",
        "shoulders",
        ("triceps",),
        level="intermediate",
    ),
    entry(
        "Dumbbell Shoulder Press",
        "dumbbell",
        "shoulders",
        ("triceps",),
        level="intermediate",
    ),
    entry(
        "Standing Military Press",
        "barbell",
        "shoulders",
        ("triceps",),
        level="intermediate",
    ),
    entry("Seated Side Lateral Raise", "dumbbell", "shoulders", mechanic="isolation"),
    entry("Wide-Grip Lat Pulldown", "cable", "lats", ("biceps", "middle back")),
    entry("Pullups", "body only", "lats", ("biceps",), level="intermediate"),
    entry(
        "Barbell Full Squat",
        "barbell",
        "quadriceps",
        ("glutes", "hamstrings"),
        level="intermediate",
    ),
    entry("Leg Press", "machine", "quadriceps", ("glutes", "hamstrings")),
    entry(
        "Romanian Deadlift",
        "barbell",
        "hamstrings",
        ("glutes", "lower back"),
        level="intermediate",
    ),
    entry(
        "Snatch Pull",
        "barbell",
        "hamstrings",
        ("traps",),
        level="expert",
        category="olympic weightlifting",
    ),
    entry(
        "Cat Stretch",
        None,
        "lower back",
        mechanic=None,
        category="stretching",
        images=0,
    ),
]


def plan_entries() -> list[dict[str, Any]]:
    """One entry per slot of the default split (first candidate name)."""
    return [entry(slot.candidates[0], "barbell", "chest") for slot in DEFAULT_SPLIT]


def write_dataset(directory: Path, entries: list[dict[str, Any]] | None = None) -> Path:
    """Write exercises.json and a valid tiny JPEG for every image path. Returns the directory."""
    entries = ENTRIES if entries is None else entries
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "exercises.json").write_text(json.dumps(entries), encoding="utf-8")
    for e in entries:
        if not isinstance(e, dict):  # deliberately malformed entries in some tests
            continue
        for relative in e.get("images", []):
            target = directory / "exercises" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 8), (200, 40, 40)).save(target, "JPEG")
    return directory
