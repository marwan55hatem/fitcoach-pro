import pytest

from app.rules.food_validation import (
    ITEM_CAPS,
    FoodItem,
    check_item,
    macro_kcal,
    meal_totals,
    midpoint,
)


def item(**override: float | None) -> FoodItem:
    values: dict = {
        "name": "Koshari",
        "grams": 450.0,
        "kcal_min": 700,
        "kcal_max": 850,
        "protein_g": 22.0,
        "carbs_g": 140.0,
        "fat_g": 15.0,
    }
    values.update(override)
    return FoodItem(**values)


def codes(food: FoodItem) -> set[str]:
    return {f.code for f in check_item(food)[1]}


def test_macro_kcal_is_4_4_9() -> None:
    assert macro_kcal(10, 20, 5) == 10 * 4 + 20 * 4 + 5 * 9


def test_midpoint_rounds() -> None:
    assert midpoint(700, 850) == 775
    assert midpoint(101, 102) == 102  # half rounds up, never to a fake .5 kcal


def test_a_sensible_item_passes() -> None:
    fixed, flags = check_item(item())
    assert flags == () and fixed == item()


@pytest.mark.parametrize(
    ("kcal_min", "kcal_max", "flagged"),
    [
        (700, 850, False),  # 4/4/9 gives 783
        (560, 640, False),  # 600 midpoint: 23 % below 783, still within tolerance
        (300, 400, True),  # far below what the macros add up to
        (1200, 1400, True),  # far above
    ],
)
def test_calories_must_roughly_match_the_macros(
    kcal_min: int, kcal_max: int, flagged: bool
) -> None:
    assert (
        "macro_mismatch" in codes(item(kcal_min=kcal_min, kcal_max=kcal_max))
    ) is flagged


def test_small_items_get_a_fixed_tolerance() -> None:
    # A spoon of oil: 120 kcal from 13.5 g fat; tiny absolute errors must not flag.
    oil = item(
        name="Oil",
        grams=14,
        kcal_min=100,
        kcal_max=130,
        protein_g=0,
        carbs_g=0,
        fat_g=13.5,
    )
    assert "macro_mismatch" not in codes(oil)


def test_a_reversed_range_is_put_in_order() -> None:
    fixed, flags = check_item(item(kcal_min=850, kcal_max=700))
    assert (fixed.kcal_min, fixed.kcal_max) == (700, 850)
    assert "range_order" in {f.code for f in flags}


def test_negative_numbers_become_zero() -> None:
    fixed, flags = check_item(item(protein_g=-3.0, kcal_min=-10, kcal_max=850))
    assert fixed.protein_g == 0 and fixed.kcal_min == 0
    assert "negative" in {f.code for f in flags}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kcal_max", ITEM_CAPS["kcal"] + 1),
        ("protein_g", ITEM_CAPS["protein_g"] + 1),
        ("carbs_g", ITEM_CAPS["carbs_g"] + 1),
        ("fat_g", ITEM_CAPS["fat_g"] + 1),
        ("grams", ITEM_CAPS["grams"] + 1),
    ],
)
def test_one_item_can_only_be_so_big(field: str, value: float) -> None:
    flags = check_item(item(**{field: value}))[1]
    assert any(f.code == "too_large" and f.field == field for f in flags)


def test_meal_totals_add_ranges_and_macros() -> None:
    items = [
        item(),
        item(
            name="Baladi bread",
            grams=100,
            kcal_min=240,
            kcal_max=290,
            protein_g=9,
            carbs_g=50,
            fat_g=1.5,
        ),
    ]
    totals = meal_totals(items)
    assert (totals.kcal_min, totals.kcal_max) == (940, 1140)
    assert totals.kcal == 1040  # the midpoint of the meal's range
    assert (totals.protein_g, totals.carbs_g, totals.fat_g) == (31.0, 190.0, 16.5)


def test_meal_totals_of_nothing() -> None:
    totals = meal_totals([])
    assert (totals.kcal, totals.kcal_min, totals.kcal_max, totals.protein_g) == (
        0,
        0,
        0,
        0,
    )
