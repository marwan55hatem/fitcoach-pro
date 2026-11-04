import dataclasses

import pytest

from app.rules.inbody_validation import (
    FLAGGED_CONFIDENCE,
    ScanValues,
    adjust_confidence,
    clean_segments,
    interpret_change,
    validate_scan,
)

GOOD = ScanValues(
    weight_kg=70.0,
    smm_kg=33.0,
    body_fat_mass_kg=10.5,
    pbf_pct=15.0,
    bmi=18.8,
    tbw_l=43.0,
    bmr_kcal=1720,
    visceral_fat_level=3,
    inbody_score=78,
)
HEIGHT = 193.0


def codes(values: ScanValues, height: float | None = HEIGHT) -> set[tuple[str, str]]:
    return {(f.field, f.code) for f in validate_scan(values, height).flags}


def test_a_plausible_scan_has_no_flags() -> None:
    result = validate_scan(GOOD, HEIGHT)
    assert result.flags == ()
    assert result.values == GOOD


def test_empty_scan_has_no_flags() -> None:
    assert validate_scan(ScanValues(), None).flags == ()


@pytest.mark.parametrize(
    ("pbf", "flagged"),
    [(2.9, True), (3.0, False), (60.0, False), (60.1, True)],
)
def test_body_fat_percent_range(pbf: float, flagged: bool) -> None:
    # Keep fat mass consistent so only the range is tested.
    values = dataclasses.replace(GOOD, pbf_pct=pbf, body_fat_mass_kg=70 * pbf / 100)
    assert (("pbf_pct", "implausible") in codes(values)) is flagged


def test_muscle_must_be_less_than_weight() -> None:
    values = dataclasses.replace(GOOD, smm_kg=71.0)
    assert ("smm_kg", "implausible") in codes(values)


def test_muscle_and_fat_together_must_be_less_than_weight() -> None:
    values = dataclasses.replace(GOOD, smm_kg=45.0, body_fat_mass_kg=25.0, pbf_pct=35.7)
    assert ("smm_kg", "inconsistent") in codes(values)
    assert ("body_fat_mass_kg", "inconsistent") in codes(values)


@pytest.mark.parametrize(
    ("bmi", "flagged"),
    [(18.8, False), (19.2, False), (18.3, False), (19.5, True), (17.5, True)],
)
def test_bmi_matches_height_and_weight(bmi: float, flagged: bool) -> None:
    # 70 / 1.93^2 = 18.79; half a point of tolerance for rounding on the sheet.
    values = dataclasses.replace(GOOD, bmi=bmi)
    assert (("bmi", "inconsistent") in codes(values)) is flagged


def test_bmi_without_height_is_not_checked() -> None:
    values = dataclasses.replace(GOOD, bmi=30.0)
    assert ("bmi", "inconsistent") not in codes(values, None)


def test_body_fat_percent_matches_fat_mass() -> None:
    # 10.5 / 70 = 15 %; 25 % with that fat mass cannot be right.
    values = dataclasses.replace(GOOD, pbf_pct=25.0)
    assert ("pbf_pct", "inconsistent") in codes(values)


@pytest.mark.parametrize(
    ("bmr", "flagged"),
    [(999, True), (1000, False), (3500, False), (3501, True)],
)
def test_bmr_range(bmr: int, flagged: bool) -> None:
    values = dataclasses.replace(GOOD, bmr_kcal=bmr)
    assert (("bmr_kcal", "implausible") in codes(values)) is flagged


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("visceral_fat_level", 45),
        ("visceral_fat_level", 0),
        ("inbody_score", 140),
        ("pbf_pct", 120.0),
        ("weight_kg", 7.0),
        ("weight_kg", 950.0),
        ("bmr_kcal", -5),
        ("smm_kg", -1.0),
    ],
)
def test_impossible_values_are_dropped(field: str, value: float) -> None:
    result = validate_scan(dataclasses.replace(GOOD, **{field: value}), HEIGHT)
    assert getattr(result.values, field) is None  # never stored as a number
    assert (field, "out_of_range") in {(f.field, f.code) for f in result.flags}


def test_impossible_segments_are_dropped() -> None:
    cleaned, flags = clean_segments(
        "segmental_lean",
        {"left_arm": 3.2, "trunk": 260.0, "left_leg": -1.0, "right_leg": None},
    )
    assert cleaned == {
        "left_arm": 3.2,
        "trunk": None,
        "left_leg": None,
        "right_leg": None,
    }
    assert {(f.field, f.code) for f in flags} == {
        ("segmental_lean.trunk", "out_of_range"),
        ("segmental_lean.left_leg", "out_of_range"),
    }


def test_flagged_fields_lose_confidence() -> None:
    confidence = {"weight_kg": 0.98, "smm_kg": 0.9, "pbf_pct": 0.2, "bmi": 1.4}
    flags = validate_scan(dataclasses.replace(GOOD, smm_kg=71.0), HEIGHT).flags
    adjusted = adjust_confidence(confidence, flags)
    assert adjusted["weight_kg"] == 0.98
    assert adjusted["smm_kg"] == FLAGGED_CONFIDENCE
    assert adjusted["pbf_pct"] == 0.2  # already lower than the cap
    assert adjusted["bmi"] == 1.0  # clamped to 0..1


@pytest.mark.parametrize(
    ("smm_change", "fat_change", "code"),
    [
        (0.8, 0.0, "lean_gain"),
        (0.8, -0.5, "lean_gain"),
        (0.8, 1.2, "gain_both"),
        (0.0, -1.0, "fat_loss"),
        (-0.8, -1.5, "loss_both"),
        (-0.8, 0.1, "muscle_loss"),
        (-0.8, 1.0, "muscle_loss"),
        (0.1, 1.0, "fat_gain"),
        (0.2, -0.2, "stable"),
    ],
)
def test_interpret_change(smm_change: float, fat_change: float, code: str) -> None:
    result = interpret_change(smm_change, fat_change)
    assert result.code == code
    assert result.message  # always a plain sentence to show


def test_interpret_change_needs_both_numbers() -> None:
    assert interpret_change(None, 1.0) is None
    assert interpret_change(0.5, None) is None
