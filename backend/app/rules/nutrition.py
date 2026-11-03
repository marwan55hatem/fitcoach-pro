"""Daily calorie, macro and water targets (pure functions, no I/O).

Calories: BMR (Mifflin-St Jeor, or a confirmed InBody BMR) x activity factor, plus a goal
adjustment and any approved adjustment from nutrition_settings. Protein is set per kg by
goal, fat has a per-kg floor, carbs take the rest. Calories never drop below BMR or a
minimum per sex.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from app.models.enums import ActivityLevel, Goal, Sex

ACTIVITY_FACTORS: dict[ActivityLevel, float] = {
    ActivityLevel.SEDENTARY: 1.2,
    ActivityLevel.LIGHT: 1.375,
    ActivityLevel.MODERATE: 1.55,
    ActivityLevel.ACTIVE: 1.725,
    ActivityLevel.VERY_ACTIVE: 1.9,
}
GOAL_ADJUSTMENT_KCAL: dict[Goal, int] = {
    Goal.LEAN_BULK: 300,  # +250 to +400 keeps fat gain low
    Goal.MAINTAIN: 0,
    Goal.RECOMP: -100,  # near maintenance
    Goal.CUT: -400,  # -300 to -500 keeps the loss sustainable
}
PROTEIN_G_PER_KG: dict[Goal, float] = {
    Goal.LEAN_BULK: 2.0,
    Goal.MAINTAIN: 1.6,
    Goal.RECOMP: 2.0,
    Goal.CUT: 2.2,
}
PROTEIN_RANGE_G_PER_KG = (1.6, 2.2)
FAT_MIN_G_PER_KG = 0.8
FAT_SHARE = 0.25  # of calories, when that is above the per-kg floor
WATER_ML_PER_KG: dict[ActivityLevel, int] = {
    ActivityLevel.SEDENTARY: 35,
    ActivityLevel.LIGHT: 35,
    ActivityLevel.MODERATE: 40,
    ActivityLevel.ACTIVE: 45,
    ActivityLevel.VERY_ACTIVE: 50,
}
MAX_CALORIE_ADJUSTMENT = 500
MIN_KCAL: dict[Sex, int] = {Sex.MALE: 1500, Sex.FEMALE: 1200}
KCAL_ROUNDING = 10


@dataclass(frozen=True)
class NutritionInput:
    weight_kg: float
    height_cm: float
    age: int
    sex: Sex
    activity: ActivityLevel
    goal: Goal
    inbody_bmr_kcal: int | None = None  # only from a CONFIRMED scan
    calorie_adjustment_kcal: int = 0  # from nutrition_settings (approved changes)
    protein_g_per_kg: float | None = None  # override from nutrition_settings


@dataclass(frozen=True)
class NutritionTargets:
    kcal: int
    protein_g: int
    fat_g: int
    carbs_g: int
    water_ml: int
    bmr_kcal: int
    bmr_source: str  # "inbody" or "formula"
    tdee_kcal: int
    goal_adjustment_kcal: int
    calorie_adjustment_kcal: int
    floored: bool  # raised to the safety minimum


@dataclass(frozen=True)
class MealEntry:
    kcal: int | None
    protein_g: float | None
    carbs_g: float | None
    fat_g: float | None
    confirmed: bool


@dataclass(frozen=True)
class DayTotals:
    kcal: int
    protein_g: float
    carbs_g: float
    fat_g: float
    meals_counted: int
    drafts_excluded: int


@dataclass(frozen=True)
class Remaining:
    kcal: int
    protein_g: float
    carbs_g: float
    fat_g: float


def bmr_mifflin(weight_kg: float, height_cm: float, age: int, sex: Sex) -> float:
    if weight_kg <= 0 or height_cm <= 0 or age <= 0:
        raise ValueError("weight, height and age must be positive")
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return base + (5 if sex is Sex.MALE else -161)


def tdee(bmr: float, activity: ActivityLevel) -> float:
    return bmr * ACTIVITY_FACTORS[activity]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def daily_targets(inp: NutritionInput) -> NutritionTargets:
    if inp.inbody_bmr_kcal:
        bmr, source = float(inp.inbody_bmr_kcal), "inbody"
    else:
        bmr = bmr_mifflin(inp.weight_kg, inp.height_cm, inp.age, inp.sex)
        source = "formula"
    expenditure = tdee(bmr, inp.activity)
    goal_adjustment = GOAL_ADJUSTMENT_KCAL[inp.goal]
    adjustment = int(
        _clamp(
            inp.calorie_adjustment_kcal,
            -MAX_CALORIE_ADJUSTMENT,
            MAX_CALORIE_ADJUSTMENT,
        )
    )

    planned = expenditure + goal_adjustment + adjustment
    minimum = max(bmr, MIN_KCAL[inp.sex])
    floored = planned < minimum
    kcal = round(max(planned, minimum) / KCAL_ROUNDING) * KCAL_ROUNDING

    per_kg = inp.protein_g_per_kg or PROTEIN_G_PER_KG[inp.goal]
    protein = round(inp.weight_kg * _clamp(per_kg, *PROTEIN_RANGE_G_PER_KG))
    fat_floor = round(inp.weight_kg * FAT_MIN_G_PER_KG)
    fat = max(fat_floor, round(kcal * FAT_SHARE / 9))
    if kcal - protein * 4 - fat * 9 < 0:
        fat = fat_floor
    carbs = max(0, round((kcal - protein * 4 - fat * 9) / 4))

    return NutritionTargets(
        kcal=kcal,
        protein_g=protein,
        fat_g=fat,
        carbs_g=carbs,
        water_ml=round(inp.weight_kg * WATER_ML_PER_KG[inp.activity]),
        bmr_kcal=round(bmr),
        bmr_source=source,
        tdee_kcal=round(expenditure),
        goal_adjustment_kcal=goal_adjustment,
        calorie_adjustment_kcal=adjustment,
        floored=floored,
    )


def day_totals(meals: Iterable[MealEntry]) -> DayTotals:
    """Totals of confirmed meals only. AI drafts count once the user confirms them."""
    kcal, protein, carbs, fat = 0, 0.0, 0.0, 0.0
    counted = drafts = 0
    for meal in meals:
        if not meal.confirmed:
            drafts += 1
            continue
        counted += 1
        kcal += meal.kcal or 0
        protein += meal.protein_g or 0
        carbs += meal.carbs_g or 0
        fat += meal.fat_g or 0
    return DayTotals(kcal, protein, carbs, fat, counted, drafts)


def remaining(targets: NutritionTargets, totals: DayTotals) -> Remaining:
    """What is left for the day; negative means over the target."""
    return Remaining(
        kcal=targets.kcal - totals.kcal,
        protein_g=targets.protein_g - totals.protein_g,
        carbs_g=targets.carbs_g - totals.carbs_g,
        fat_g=targets.fat_g - totals.fat_g,
    )
