"""Plausibility checks for InBody values read by the AI, and what a change between two
scans means. Pure functions: the AI reads, these rules judge, the user confirms."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

FLAGGED_CONFIDENCE: Final = 0.3  # a value that fails a check is never trusted more
BMI_TOLERANCE: Final = 0.5  # rounding on the printed sheet
PBF_TOLERANCE: Final = 1.5  # percentage points between printed PBF and fat/weight
CHANGE_NOISE_KG: Final = 0.3  # smaller changes are within a scanner's day-to-day noise
SEGMENT_MAX_KG: Final = 100.0

# Values outside these cannot be real (or would not fit the database): they are dropped.
HARD_LIMITS: Final = {
    "weight_kg": (20.0, 500.0),
    "smm_kg": (0.0, 200.0),
    "body_fat_mass_kg": (0.0, 300.0),
    "pbf_pct": (0.0, 100.0),
    "bmi": (5.0, 100.0),
    "tbw_l": (0.0, 200.0),
    "bmr_kcal": (0.0, 10000.0),
    "visceral_fat_level": (1.0, 30.0),
    "inbody_score": (0.0, 100.0),
}


@dataclass(frozen=True)
class ScanValues:
    weight_kg: float | None = None
    smm_kg: float | None = None
    body_fat_mass_kg: float | None = None
    pbf_pct: float | None = None
    bmi: float | None = None
    tbw_l: float | None = None
    bmr_kcal: float | None = None
    visceral_fat_level: float | None = None
    inbody_score: float | None = None


@dataclass(frozen=True)
class Flag:
    field: str
    code: str  # out_of_range | implausible | inconsistent
    message: str


@dataclass(frozen=True)
class Validation:
    values: ScanValues  # out-of-range values removed
    flags: tuple[Flag, ...]


@dataclass(frozen=True)
class Interpretation:
    code: str
    message: str


def _drop_impossible(values: ScanValues) -> tuple[ScanValues, list[Flag]]:
    flags: list[Flag] = []
    cleaned: dict[str, float | None] = {}
    for name, (low, high) in HARD_LIMITS.items():
        value = getattr(values, name)
        if value is not None and not low <= value <= high:
            flags.append(
                Flag(name, "out_of_range", "This number can't be right. Check it.")
            )
            value = None
        cleaned[name] = value
    return ScanValues(**cleaned), flags


def validate_scan(values: ScanValues, height_cm: float | None) -> Validation:
    """Flag values that cannot all be true together. Out-of-range values are removed;
    implausible or inconsistent ones are kept for the user to check."""
    v, flags = _drop_impossible(values)

    if v.pbf_pct is not None and not 3 <= v.pbf_pct <= 60:
        flags.append(
            Flag("pbf_pct", "implausible", "Body fat outside 3 to 60 % is unlikely.")
        )
    if v.smm_kg is not None and v.weight_kg is not None and v.smm_kg >= v.weight_kg:
        flags.append(
            Flag("smm_kg", "implausible", "Muscle mass must be less than body weight.")
        )
    elif (
        v.smm_kg is not None
        and v.body_fat_mass_kg is not None
        and v.weight_kg is not None
        and v.smm_kg + v.body_fat_mass_kg >= v.weight_kg
    ):
        message = "Muscle plus fat mass must be less than body weight."
        flags.append(Flag("smm_kg", "inconsistent", message))
        flags.append(Flag("body_fat_mass_kg", "inconsistent", message))
    if v.bmi is not None and v.weight_kg is not None and height_cm:
        expected = v.weight_kg / (height_cm / 100) ** 2
        if abs(v.bmi - expected) > BMI_TOLERANCE:
            flags.append(
                Flag("bmi", "inconsistent", "BMI doesn't match height and weight.")
            )
    if (
        v.pbf_pct is not None
        and v.body_fat_mass_kg is not None
        and v.weight_kg
        and abs(v.pbf_pct - 100 * v.body_fat_mass_kg / v.weight_kg) > PBF_TOLERANCE
    ):
        flags.append(
            Flag("pbf_pct", "inconsistent", "Body fat % doesn't match fat mass.")
        )
    if v.bmr_kcal is not None and not 1000 <= v.bmr_kcal <= 3500:
        flags.append(
            Flag(
                "bmr_kcal",
                "implausible",
                "A BMR outside 1000 to 3500 kcal is unlikely.",
            )
        )
    return Validation(v, tuple(flags))


def clean_segments(
    name: str, segments: Mapping[str, float | None]
) -> tuple[dict[str, float | None], list[Flag]]:
    """Segment masses (arms, trunk, legs) outside 0 to SEGMENT_MAX_KG are dropped."""
    cleaned: dict[str, float | None] = {}
    flags: list[Flag] = []
    for part, value in segments.items():
        if value is not None and not 0 <= value <= SEGMENT_MAX_KG:
            flags.append(
                Flag(f"{name}.{part}", "out_of_range", "This number can't be right.")
            )
            value = None
        cleaned[part] = value
    return cleaned, flags


def adjust_confidence(
    confidence: Mapping[str, float], flags: Sequence[Flag]
) -> dict[str, float]:
    """Clamp to 0..1, and cap every flagged field at FLAGGED_CONFIDENCE."""
    flagged = {f.field for f in flags}
    adjusted: dict[str, float] = {}
    for name, value in confidence.items():
        clamped = min(1.0, max(0.0, value))
        adjusted[name] = (
            min(clamped, FLAGGED_CONFIDENCE) if name in flagged else clamped
        )
    return adjusted


def interpret_change(
    smm_change_kg: float | None, fat_change_kg: float | None
) -> Interpretation | None:
    """What a change in muscle and fat mass between two scans most likely means."""
    if smm_change_kg is None or fat_change_kg is None:
        return None
    muscle_up = smm_change_kg >= CHANGE_NOISE_KG
    muscle_down = smm_change_kg <= -CHANGE_NOISE_KG
    fat_up = fat_change_kg >= CHANGE_NOISE_KG
    fat_down = fat_change_kg <= -CHANGE_NOISE_KG
    if muscle_up and not fat_up:
        return Interpretation(
            "lean_gain",
            "Muscle went up while fat stayed flat or fell: a good lean gain.",
        )
    if muscle_up:
        return Interpretation(
            "gain_both",
            "Muscle and fat both went up. If fat rises faster than you want, trim "
            "calories slightly.",
        )
    if muscle_down and fat_down:
        return Interpretation(
            "loss_both",
            "Fat went down, but so did muscle. Keep protein high and keep lifting heavy.",
        )
    if muscle_down:
        return Interpretation(
            "muscle_loss",
            "Muscle went down. Check protein, sleep and that you're training consistently.",
        )
    if fat_down:
        return Interpretation(
            "fat_loss", "Fat went down while muscle held steady: a good cut."
        )
    if fat_up:
        return Interpretation(
            "fat_gain", "Fat went up without more muscle. Calories may be a bit high."
        )
    return Interpretation(
        "stable", "Muscle and fat are steady, within the scanner's normal variation."
    )
