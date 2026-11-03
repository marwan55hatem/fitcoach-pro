from dataclasses import replace

import pytest

from app.models.enums import ActivityLevel, Goal, Sex
from app.rules.nutrition import (
    MAX_CALORIE_ADJUSTMENT,
    MealEntry,
    NutritionInput,
    bmr_mifflin,
    daily_targets,
    day_totals,
    remaining,
    tdee,
)

# A 30-year-old man, 193 cm, 70 kg, moderately active, on a lean bulk.
ME = NutritionInput(
    weight_kg=70,
    height_cm=193,
    age=30,
    sex=Sex.MALE,
    activity=ActivityLevel.MODERATE,
    goal=Goal.LEAN_BULK,
)


def test_bmr_mifflin_st_jeor_male_and_female() -> None:
    assert bmr_mifflin(70, 193, 30, Sex.MALE) == pytest.approx(1761.25)
    assert bmr_mifflin(60, 165, 30, Sex.FEMALE) == pytest.approx(1320.25)


def test_bmr_rejects_impossible_inputs() -> None:
    with pytest.raises(ValueError):
        bmr_mifflin(0, 180, 30, Sex.MALE)
    with pytest.raises(ValueError):
        bmr_mifflin(70, 180, 0, Sex.MALE)


@pytest.mark.parametrize(
    ("activity", "factor"),
    [
        (ActivityLevel.SEDENTARY, 1.2),
        (ActivityLevel.LIGHT, 1.375),
        (ActivityLevel.MODERATE, 1.55),
        (ActivityLevel.ACTIVE, 1.725),
        (ActivityLevel.VERY_ACTIVE, 1.9),
    ],
)
def test_tdee_uses_activity_factor(activity: ActivityLevel, factor: float) -> None:
    assert tdee(1000, activity) == pytest.approx(1000 * factor)


def test_lean_bulk_for_70kg_193cm_man_is_about_3000_kcal() -> None:
    t = daily_targets(ME)
    assert 2850 <= t.kcal <= 3150
    assert t.protein_g >= 140
    assert t.fat_g >= 0.8 * 70
    assert t.carbs_g > 0
    assert t.bmr_source == "formula"
    assert 250 <= t.goal_adjustment_kcal <= 400


def test_macros_add_up_to_the_calories() -> None:
    t = daily_targets(ME)
    assert t.protein_g * 4 + t.carbs_g * 4 + t.fat_g * 9 == pytest.approx(
        t.kcal, abs=15
    )


@pytest.mark.parametrize(
    ("goal", "low", "high"),
    [
        (Goal.LEAN_BULK, 250, 400),
        (Goal.CUT, -500, -300),
        (Goal.RECOMP, -150, 0),
        (Goal.MAINTAIN, 0, 0),
    ],
)
def test_goal_adjustment_ranges(goal: Goal, low: int, high: int) -> None:
    t = daily_targets(replace(ME, goal=goal))
    assert low <= t.goal_adjustment_kcal <= high
    assert t.kcal == pytest.approx(t.tdee_kcal + t.goal_adjustment_kcal, abs=10)


@pytest.mark.parametrize("goal", list(Goal))
def test_protein_between_1_6_and_2_2_g_per_kg(goal: Goal) -> None:
    t = daily_targets(replace(ME, goal=goal))
    assert 1.6 * 70 <= t.protein_g <= 2.2 * 70


def test_cut_has_more_protein_per_kg_than_maintenance() -> None:
    cut = daily_targets(replace(ME, goal=Goal.CUT))
    maintain = daily_targets(replace(ME, goal=Goal.MAINTAIN))
    assert cut.protein_g > maintain.protein_g


def test_goal_change_moves_calories() -> None:
    bulk = daily_targets(ME)
    cut = daily_targets(replace(ME, goal=Goal.CUT))
    assert bulk.kcal - cut.kcal >= 550


def test_confirmed_inbody_bmr_is_preferred() -> None:
    t = daily_targets(replace(ME, inbody_bmr_kcal=1650))
    assert t.bmr_source == "inbody"
    assert t.bmr_kcal == 1650
    assert t.tdee_kcal == round(1650 * 1.55)


def test_settings_adjustment_is_applied_and_capped() -> None:
    base = daily_targets(ME)
    plus = daily_targets(replace(ME, calorie_adjustment_kcal=150))
    assert plus.kcal == base.kcal + 150
    huge = daily_targets(replace(ME, calorie_adjustment_kcal=5000))
    assert huge.calorie_adjustment_kcal == MAX_CALORIE_ADJUSTMENT
    assert huge.kcal == base.kcal + MAX_CALORIE_ADJUSTMENT


def test_protein_override_is_clamped_to_the_safe_range() -> None:
    assert daily_targets(replace(ME, protein_g_per_kg=1.8)).protein_g == 126
    assert daily_targets(replace(ME, protein_g_per_kg=5.0)).protein_g == 154
    assert daily_targets(replace(ME, protein_g_per_kg=0.5)).protein_g == 112


def test_calories_never_go_below_bmr_or_the_minimum() -> None:
    small = NutritionInput(
        weight_kg=45,
        height_cm=150,
        age=60,
        sex=Sex.FEMALE,
        activity=ActivityLevel.SEDENTARY,
        goal=Goal.CUT,
        calorie_adjustment_kcal=-500,
    )
    t = daily_targets(small)
    assert t.floored
    assert t.kcal >= max(t.bmr_kcal, 1200)
    assert t.carbs_g >= 0
    assert t.fat_g >= round(0.8 * 45)
    assert not daily_targets(ME).floored


@pytest.mark.parametrize(
    ("activity", "per_kg"),
    [(ActivityLevel.SEDENTARY, 35), (ActivityLevel.VERY_ACTIVE, 50)],
)
def test_water_35_to_50_ml_per_kg(activity: ActivityLevel, per_kg: int) -> None:
    assert daily_targets(replace(ME, activity=activity)).water_ml == 70 * per_kg


def test_day_totals_exclude_unconfirmed_drafts() -> None:
    meals = [
        MealEntry(kcal=600, protein_g=40, carbs_g=60, fat_g=20, confirmed=True),
        MealEntry(kcal=900, protein_g=10, carbs_g=100, fat_g=40, confirmed=False),
        MealEntry(kcal=300, protein_g=None, carbs_g=30, fat_g=None, confirmed=True),
    ]
    totals = day_totals(meals)
    assert totals.kcal == 900
    assert totals.protein_g == 40
    assert totals.carbs_g == 90
    assert totals.fat_g == 20
    assert totals.meals_counted == 2
    assert totals.drafts_excluded == 1


def test_day_totals_of_an_empty_day_are_zero() -> None:
    totals = day_totals([])
    assert (totals.kcal, totals.protein_g, totals.meals_counted) == (0, 0, 0)


def test_remaining_can_go_negative_when_over_target() -> None:
    t = daily_targets(ME)
    over = day_totals(
        [
            MealEntry(
                kcal=t.kcal + 200, protein_g=10, carbs_g=10, fat_g=10, confirmed=True
            )
        ]
    )
    left = remaining(t, over)
    assert left.kcal == -200
    assert left.protein_g == t.protein_g - 10
