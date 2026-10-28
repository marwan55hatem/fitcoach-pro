"""Which exercises to avoid for a user's injuries / pain areas.

This is a conservative *filter*, not medical advice and never a diagnosis: when a body
area is flagged, exercises that visibly load that area are hidden from suggestions and
swaps. Anything that needs real judgement is a job for a doctor or physio, not this table.

Tiers, by the severity the user reported:
  mild      hide movements that are classic aggravators for that area (name patterns)
  moderate  also hide compound lifts whose PRIMARY muscle loads the area
  severe    also hide anything that uses a loading muscle as primary OR secondary
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.models.enums import BodyArea, Mechanic, Severity


@dataclass(frozen=True)
class AreaRule:
    movement_patterns: tuple[re.Pattern[str], ...]
    loaded_muscles: frozenset[str]


def _patterns(*raw: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in raw)


AREA_RULES: dict[BodyArea, AreaRule] = {
    BodyArea.NECK: AreaRule(
        _patterns(r"neck", r"\bshrugs?\b", r"behind the neck"),
        frozenset({"neck", "traps"}),
    ),
    BodyArea.SHOULDER: AreaRule(
        _patterns(
            r"over ?head",
            r"military",
            r"behind (the )?(neck|head)",
            r"upright",
            r"\bdips?\b",
            r"snatch",
            r"\bjerk\b",
            r"push press",
            r"handstand",
            r"arnold",
        ),
        frozenset({"shoulders"}),
    ),
    BodyArea.ELBOW: AreaRule(
        _patterns(
            r"skull",
            r"french press",
            r"lying triceps",
            r"close[- ]grip",
            r"preacher",
            r"\bdips?\b",
            r"triceps extension",
        ),
        frozenset({"triceps", "biceps", "forearms"}),
    ),
    BodyArea.WRIST_HAND: AreaRule(
        _patterns(
            r"wrist", r"push[- ]?ups?", r"plank", r"handstand", r"\bclean\b", r"snatch"
        ),
        frozenset({"forearms"}),
    ),
    BodyArea.UPPER_BACK: AreaRule(
        _patterns(r"bent[- ]?over", r"t-bar", r"rack pull", r"\bshrugs?\b"),
        frozenset({"middle_back", "traps", "lats"}),
    ),
    BodyArea.LOWER_BACK: AreaRule(
        _patterns(
            r"deadlift",
            r"good ?morning",
            r"bent[- ]?over",
            r"hyperextension",
            r"back extension",
            r"\bclean\b",
            r"snatch",
            r"swing",
            r"stiff[- ]?leg",
            r"barbell .*squat",
            r"^squat",
        ),
        frozenset({"lower_back"}),
    ),
    BodyArea.HIP: AreaRule(
        _patterns(
            r"lunge",
            r"split squat",
            r"sumo",
            r"step[- ]?up",
            r"hip (abduction|adduction)",
        ),
        frozenset({"glutes", "adductors", "abductors"}),
    ),
    BodyArea.KNEE: AreaRule(
        _patterns(
            r"squat",
            r"lunge",
            r"jump",
            r"leg extension",
            r"step[- ]?up",
            r"sissy",
            r"pistol",
            r"hack",
            r"burpee",
            r"leg press",
        ),
        frozenset({"quads"}),
    ),
    BodyArea.ANKLE_FOOT: AreaRule(
        _patterns(r"jump", r"\bhops?\b", r"skip", r"sprint", r"\bbox\b", r"run"),
        frozenset({"calves"}),
    ),
    # "other" needs human judgement: no automatic exclusions.
    BodyArea.OTHER: AreaRule((), frozenset()),
}


@dataclass(frozen=True)
class ExerciseFacts:
    name: str
    muscle_primary: str
    muscles_secondary: tuple[str, ...]
    mechanic: Mechanic | None


@dataclass(frozen=True)
class LimitationFacts:
    body_area: BodyArea
    severity: Severity
    active: bool = True


@dataclass(frozen=True)
class Exclusion:
    area: BodyArea
    reason: str  # "movement" | "loaded_primary" | "loaded_secondary"


def exclusions_for(
    exercise: ExerciseFacts, limitations: Iterable[LimitationFacts]
) -> list[Exclusion]:
    """Every reason this exercise is ruled out. Empty list = fine to suggest."""
    found: list[Exclusion] = []
    for lim in limitations:
        if not lim.active:
            continue
        rule = AREA_RULES[lim.body_area]
        if any(p.search(exercise.name) for p in rule.movement_patterns):
            found.append(Exclusion(lim.body_area, "movement"))
            continue
        primary_loaded = exercise.muscle_primary in rule.loaded_muscles
        if (
            lim.severity in (Severity.MODERATE, Severity.SEVERE)
            and primary_loaded
            and exercise.mechanic is Mechanic.COMPOUND
        ):
            found.append(Exclusion(lim.body_area, "loaded_primary"))
        elif lim.severity is Severity.SEVERE:
            if primary_loaded:
                found.append(Exclusion(lim.body_area, "loaded_primary"))
            elif rule.loaded_muscles & set(exercise.muscles_secondary):
                found.append(Exclusion(lim.body_area, "loaded_secondary"))
    return found


def is_excluded(
    exercise: ExerciseFacts, limitations: Sequence[LimitationFacts]
) -> bool:
    return bool(exclusions_for(exercise, limitations))


def limitations_from_areas(
    areas: Iterable[BodyArea], severity: Severity = Severity.MODERATE
) -> list[LimitationFacts]:
    """Explicit 'avoid these areas' requests are treated as moderate by default."""
    return [LimitationFacts(area, severity) for area in dict.fromkeys(areas)]
