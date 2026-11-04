"""Checks for food estimates (from the AI or typed in): calories should roughly match
the macros, a range must be in order, and one item can only be so big. Pure functions."""

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

KCAL_PER_G: Final = {"protein_g": 4, "carbs_g": 4, "fat_g": 9}
MACRO_TOLERANCE: Final = 0.25  # share of the macro calories
MIN_TOLERANCE_KCAL: Final = 60  # small items: a few grams of rounding is not an error
# The most one item on a plate can plausibly be.
ITEM_CAPS: Final = {
    "kcal": 3000,
    "protein_g": 200.0,
    "carbs_g": 400.0,
    "fat_g": 250.0,
    "grams": 3000.0,
}


@dataclass(frozen=True)
class FoodItem:
    name: str
    grams: float | None
    kcal_min: int
    kcal_max: int
    protein_g: float
    carbs_g: float
    fat_g: float


@dataclass(frozen=True)
class FoodFlag:
    field: str
    code: str  # negative | range_order | too_large | macro_mismatch
    message: str


@dataclass(frozen=True)
class MealTotals:
    kcal: int  # midpoint of the range
    kcal_min: int
    kcal_max: int
    protein_g: float
    carbs_g: float
    fat_g: float


def macro_kcal(protein_g: float, carbs_g: float, fat_g: float) -> float:
    return protein_g * 4 + carbs_g * 4 + fat_g * 9


def midpoint(kcal_min: int, kcal_max: int) -> int:
    return (kcal_min + kcal_max + 1) // 2


def check_item(food: FoodItem) -> tuple[FoodItem, tuple[FoodFlag, ...]]:
    """Fix what can be fixed safely (negatives, a reversed range) and flag the rest."""
    flags: list[FoodFlag] = []
    changes: dict[str, float | int] = {}
    for field in ("kcal_min", "kcal_max", "protein_g", "carbs_g", "fat_g", "grams"):
        value = getattr(food, field)
        if value is not None and value < 0:
            changes[field] = 0
            flags.append(FoodFlag(field, "negative", "A negative amount was set to 0."))
    food = dataclasses.replace(food, **changes)

    if food.kcal_min > food.kcal_max:
        food = dataclasses.replace(food, kcal_min=food.kcal_max, kcal_max=food.kcal_min)
        flags.append(
            FoodFlag("kcal_min", "range_order", "The calorie range was reversed.")
        )

    caps = {**ITEM_CAPS, "kcal_max": ITEM_CAPS["kcal"]}
    for field in ("kcal_max", "protein_g", "carbs_g", "fat_g", "grams"):
        value = getattr(food, field)
        if value is not None and value > caps[field]:
            flags.append(
                FoodFlag(field, "too_large", "That's more than one item can hold.")
            )

    from_macros = macro_kcal(food.protein_g, food.carbs_g, food.fat_g)
    tolerance = max(MIN_TOLERANCE_KCAL, MACRO_TOLERANCE * from_macros)
    if abs(midpoint(food.kcal_min, food.kcal_max) - from_macros) > tolerance:
        flags.append(
            FoodFlag(
                "kcal_min",
                "macro_mismatch",
                "Calories don't add up from protein, carbs and fat.",
            )
        )
    return food, tuple(flags)


def meal_totals(items: Sequence[FoodItem]) -> MealTotals:
    low = sum(i.kcal_min for i in items)
    high = sum(i.kcal_max for i in items)
    return MealTotals(
        kcal=midpoint(low, high),
        kcal_min=low,
        kcal_max=high,
        protein_g=round(sum(i.protein_g for i in items), 1),
        carbs_g=round(sum(i.carbs_g for i in items), 1),
        fat_g=round(sum(i.fat_g for i in items), 1),
    )
