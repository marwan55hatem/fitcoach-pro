"""Pure exercise-catalog logic: vocabulary normalisation, equipment, substitutes.

No I/O. The importer, the API and (later) the program validator all share this module
so "what equipment does this need" and "what can replace this" are defined once.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.models.enums import Equipment, Experience, Mechanic

MUSCLE_MAP: dict[str, str] = {
    "abdominals": "abs",
    "abductors": "abductors",
    "adductors": "adductors",
    "biceps": "biceps",
    "calves": "calves",
    "chest": "chest",
    "forearms": "forearms",
    "glutes": "glutes",
    "hamstrings": "hamstrings",
    "lats": "lats",
    "lower back": "lower_back",
    "middle back": "middle_back",
    "neck": "neck",
    "quadriceps": "quads",
    "shoulders": "shoulders",
    "traps": "traps",
    "triceps": "triceps",
}
MUSCLES: frozenset[str] = frozenset(MUSCLE_MAP.values())

_EQUIPMENT_MAP: dict[str | None, Equipment] = {
    None: Equipment.BODY_WEIGHT,  # no equipment listed: nothing needed
    "body only": Equipment.BODY_WEIGHT,
    "dumbbell": Equipment.DUMBBELL,
    "barbell": Equipment.BARBELL,
    "e-z curl bar": Equipment.EZ_BAR,
    "cable": Equipment.CABLE,
    "machine": Equipment.MACHINE,
    "kettlebells": Equipment.KETTLEBELL,
    "bands": Equipment.BANDS,
    "medicine ball": Equipment.OTHER,
    "exercise ball": Equipment.OTHER,
    "foam roll": Equipment.OTHER,
    "other": Equipment.OTHER,
}

_LEVEL_MAP = {
    "beginner": Experience.BEGINNER,
    "intermediate": Experience.INTERMEDIATE,
    "expert": Experience.ADVANCED,
    "advanced": Experience.ADVANCED,
}

STRENGTH_CATEGORIES = frozenset({"strength", "powerlifting"})

_BENCH_WORDS = re.compile(
    r"\b(bench|incline|decline|preacher|skull ?crushers?|lying)\b", re.IGNORECASE
)
_PULL_UP_WORDS = re.compile(
    r"\b(pull[- ]?ups?|chin[- ]?ups?|hanging|muscle[- ]?ups?|toes-to-bar)\b",
    re.IGNORECASE,
)
_BENCH_NEEDING = frozenset({Equipment.BARBELL, Equipment.DUMBBELL, Equipment.EZ_BAR})


def normalize_muscle(raw: str) -> str:
    key = raw.strip().lower()
    if key not in MUSCLE_MAP:
        raise ValueError(f"unknown muscle: {raw!r}")
    return MUSCLE_MAP[key]


def normalize_equipment(raw: str | None) -> Equipment:
    key = raw.strip().lower() if raw else None
    if key not in _EQUIPMENT_MAP:
        raise ValueError(f"unknown equipment: {raw!r}")
    return _EQUIPMENT_MAP[key]


def normalize_level(raw: str | None) -> Experience | None:
    if raw is None:
        return None
    key = raw.strip().lower()
    if key not in _LEVEL_MAP:
        raise ValueError(f"unknown level: {raw!r}")
    return _LEVEL_MAP[key]


def normalize_mechanic(raw: str | None) -> Mechanic | None:
    if raw is None:
        return None
    key = raw.strip().lower()
    try:
        return Mechanic(key)
    except ValueError:
        raise ValueError(f"unknown mechanic: {raw!r}") from None


def slugify(text: str) -> str:
    """'3_4_Sit-Up' -> '3-4-sit-up'. ASCII only, hyphen separated."""
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def required_equipment(name: str, equipment: Equipment) -> frozenset[Equipment]:
    """Everything needed to do the exercise.

    The dataset lists one primary item, so well-known extras are inferred from the name:
    free-weight benching/incline/preacher work needs a bench, and bodyweight pulling
    movements need a bar.
    """
    needed = {equipment}
    if equipment in _BENCH_NEEDING and _BENCH_WORDS.search(name):
        needed.add(Equipment.BENCH)
    if equipment in {Equipment.BODY_WEIGHT, Equipment.OTHER} and _PULL_UP_WORDS.search(
        name
    ):
        needed = {Equipment.PULL_UP_BAR}
    if name.lower().startswith("bench dip"):
        needed = {Equipment.BENCH}
    return frozenset(needed)


def is_available(
    required: Iterable[Equipment | str], user_equipment: Iterable[Equipment | str]
) -> bool:
    """Bodyweight is always available; everything else must be owned by the user."""
    have = {Equipment(e) for e in user_equipment} | {Equipment.BODY_WEIGHT}
    return all(Equipment(r) in have for r in required)


@dataclass(frozen=True)
class CatalogExercise:
    """The facts substitution needs, decoupled from the ORM."""

    id: str
    name: str
    muscle_primary: str
    muscles_secondary: tuple[str, ...]
    equipment: Equipment
    level: Experience | None
    mechanic: Mechanic | None
    category: str | None

    @property
    def required_equipment(self) -> frozenset[Equipment]:
        return required_equipment(self.name, self.equipment)


def _category_compatible(target: str | None, candidate: str | None) -> bool:
    if target in STRENGTH_CATEGORIES:
        return candidate in STRENGTH_CATEGORIES
    return candidate == target


def score_substitute(target: CatalogExercise, candidate: CatalogExercise) -> int:
    score = 0
    if target.mechanic and candidate.mechanic == target.mechanic:
        score += 3
    if candidate.equipment == target.equipment:
        score += 2
    if target.level and candidate.level == target.level:
        score += 1
    shared = set(target.muscles_secondary) & set(candidate.muscles_secondary)
    return score + min(len(shared), 2)


def is_substitute(
    target: CatalogExercise,
    candidate: CatalogExercise,
    *,
    user_equipment: Iterable[Equipment | str],
    excluded_ids: frozenset[str] = frozenset(),
) -> bool:
    """Whether `candidate` may replace `target`: same primary muscle, a compatible
    category, usable with the user's equipment and not ruled out by a limitation."""
    return (
        candidate.id != target.id
        and candidate.id not in excluded_ids
        and candidate.muscle_primary == target.muscle_primary
        and _category_compatible(target.category, candidate.category)
        and is_available(candidate.required_equipment, user_equipment)
    )


def rank_substitutes(
    target: CatalogExercise,
    candidates: Sequence[CatalogExercise],
    *,
    user_equipment: Iterable[Equipment | str],
    excluded_ids: frozenset[str] = frozenset(),
    limit: int = 10,
) -> list[CatalogExercise]:
    """Alternatives for the same primary muscle, usable with the user's equipment.

    Callers pass `excluded_ids` (exercises ruled out by the user's limitations), so the
    injury logic stays in app.rules.limitations and this stays about equipment/similarity.
    """
    equipment = list(user_equipment)
    eligible = [
        c
        for c in candidates
        if is_substitute(target, c, user_equipment=equipment, excluded_ids=excluded_ids)
    ]
    eligible.sort(key=lambda c: (-score_substitute(target, c), c.name.lower()))
    return eligible[:limit]
