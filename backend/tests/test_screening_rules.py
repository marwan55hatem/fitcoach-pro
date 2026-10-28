import dataclasses
import datetime as dt

import pytest

from app.models.enums import Goal, RiskLevel
from app.rules import body_metrics
from app.rules.screening import (
    LOW_BMI_WITH_CUT,
    REASON_MESSAGES,
    REASON_RISK,
    Note,
    PlanBlock,
    ReasonCode,
    ScreeningAnswers,
    can_receive_plan,
    disordered_eating_flag,
    evaluate_screening,
)

NO = ScreeningAnswers(
    chest_pain=False,
    heart_condition=False,
    blood_pressure_uncontrolled=False,
    fainting_or_dizziness=False,
    pregnant=False,
    recent_surgery=False,
    medication_affecting_exercise=False,
    joint_or_bone_problem=False,
    eating_concern=False,
)


def answers(**yes: bool) -> ScreeningAnswers:
    return dataclasses.replace(NO, **yes)


# --- screening decision -----------------------------------------------------------------


def test_all_no_is_ok() -> None:
    result = evaluate_screening(NO)
    assert result.risk_level is RiskLevel.OK
    assert result.reasons == () and result.notes == ()


@pytest.mark.parametrize(
    ("field", "reason", "risk"),
    [
        ("chest_pain", ReasonCode.CHEST_PAIN, RiskLevel.BLOCKED),
        ("heart_condition", ReasonCode.HEART_CONDITION, RiskLevel.BLOCKED),
        (
            "blood_pressure_uncontrolled",
            ReasonCode.UNCONTROLLED_BLOOD_PRESSURE,
            RiskLevel.BLOCKED,
        ),
        ("fainting_or_dizziness", ReasonCode.FAINTING_OR_DIZZINESS, RiskLevel.BLOCKED),
        ("pregnant", ReasonCode.PREGNANCY, RiskLevel.CAUTION),
        ("recent_surgery", ReasonCode.RECENT_SURGERY, RiskLevel.CAUTION),
        (
            "medication_affecting_exercise",
            ReasonCode.MEDICATION_AFFECTING_EXERCISE,
            RiskLevel.CAUTION,
        ),
    ],
)
def test_each_red_flag_alone(field: str, reason: ReasonCode, risk: RiskLevel) -> None:
    result = evaluate_screening(answers(**{field: True}))
    assert result.risk_level is risk
    assert result.reasons == (reason,)


def test_joint_problem_is_a_note_not_a_risk() -> None:
    result = evaluate_screening(answers(joint_or_bone_problem=True))
    assert result.risk_level is RiskLevel.OK
    assert result.reasons == ()
    assert result.notes == (Note.JOINT_OR_BONE_PROBLEM,)


def test_eating_concern_does_not_change_risk_level() -> None:
    # It is handled by disordered_eating_flag (withholds diet numbers), not by the gate.
    assert evaluate_screening(answers(eating_concern=True)).risk_level is RiskLevel.OK


def test_highest_risk_wins_and_all_reasons_are_listed() -> None:
    result = evaluate_screening(answers(pregnant=True, recent_surgery=True))
    assert result.risk_level is RiskLevel.CAUTION
    assert set(result.reasons) == {ReasonCode.PREGNANCY, ReasonCode.RECENT_SURGERY}

    result = evaluate_screening(answers(pregnant=True, chest_pain=True))
    assert result.risk_level is RiskLevel.BLOCKED
    assert set(result.reasons) == {ReasonCode.PREGNANCY, ReasonCode.CHEST_PAIN}


def test_everything_yes() -> None:
    everything = dataclasses.replace(
        NO, **{f.name: True for f in dataclasses.fields(NO)}
    )
    result = evaluate_screening(everything)
    assert result.risk_level is RiskLevel.BLOCKED
    assert set(result.reasons) == set(ReasonCode)


def test_every_reason_has_a_risk_and_every_code_has_a_message() -> None:
    assert set(REASON_RISK) == set(ReasonCode)
    for code in [*ReasonCode, *Note, *PlanBlock]:
        assert REASON_MESSAGES[code].strip()


# --- disordered eating flag -------------------------------------------------------------


@pytest.mark.parametrize(
    ("concern", "bmi", "goal", "expected"),
    [
        (True, None, None, True),  # answered yes: always flagged
        (True, 24.0, Goal.LEAN_BULK, True),
        (False, 18.4, Goal.CUT, True),  # low BMI + weight loss
        (False, 12.0, Goal.CUT, True),
        (False, LOW_BMI_WITH_CUT, Goal.CUT, False),  # boundary: 18.5 is not "low"
        (False, 18.6, Goal.CUT, False),
        (False, 25.0, Goal.CUT, False),
        (False, 18.4, Goal.MAINTAIN, False),  # low BMI alone is not this flag
        (False, 18.4, Goal.LEAN_BULK, False),
        (False, 18.4, Goal.RECOMP, False),
        (False, None, Goal.CUT, False),  # unknown BMI: cannot flag
        (False, 18.4, None, False),
        (False, None, None, False),
    ],
)
def test_disordered_eating_flag(
    concern: bool, bmi: float | None, goal: Goal | None, expected: bool
) -> None:
    assert (
        disordered_eating_flag(eating_concern=concern, bmi=bmi, goal=goal) is expected
    )


# --- plan access ------------------------------------------------------------------------


def access(
    *,
    complete: bool = True,
    risk: RiskLevel | None = RiskLevel.OK,
    cleared: bool = False,
    eating: bool = False,
):
    return can_receive_plan(
        profile_complete=complete,
        screening_risk=risk,
        clearance_confirmed=cleared,
        disordered_eating=eating,
    )


def test_ok_user_gets_everything() -> None:
    a = access()
    assert a.allowed and a.numeric_diet_targets and a.blocked_reasons == ()


def test_missing_screening_blocks() -> None:
    a = access(risk=None)
    assert not a.allowed and not a.numeric_diet_targets
    assert a.blocked_reasons == (PlanBlock.SCREENING_MISSING,)


def test_incomplete_profile_blocks() -> None:
    a = access(complete=False)
    assert not a.allowed
    assert a.blocked_reasons == (PlanBlock.PROFILE_INCOMPLETE,)


def test_both_blocks_are_reported() -> None:
    a = access(complete=False, risk=None)
    assert a.blocked_reasons == (
        PlanBlock.PROFILE_INCOMPLETE,
        PlanBlock.SCREENING_MISSING,
    )


@pytest.mark.parametrize("risk", [RiskLevel.CAUTION, RiskLevel.BLOCKED])
def test_caution_and_blocked_need_clearance(risk: RiskLevel) -> None:
    denied = access(risk=risk, cleared=False)
    assert not denied.allowed
    assert denied.blocked_reasons == (PlanBlock.CLEARANCE_REQUIRED,)

    granted = access(risk=risk, cleared=True)
    assert granted.allowed and granted.numeric_diet_targets


def test_clearance_flag_is_irrelevant_for_ok() -> None:
    assert access(risk=RiskLevel.OK, cleared=True).allowed


def test_clearance_does_not_rescue_a_missing_screening_or_profile() -> None:
    assert not access(risk=None, cleared=True).allowed
    assert not access(complete=False, risk=RiskLevel.BLOCKED, cleared=True).allowed


@pytest.mark.parametrize(
    ("risk", "cleared"),
    [(RiskLevel.OK, False), (RiskLevel.CAUTION, True), (RiskLevel.BLOCKED, True)],
)
def test_disordered_eating_never_unlocks_diet_numbers(
    risk: RiskLevel, cleared: bool
) -> None:
    a = access(risk=risk, cleared=cleared, eating=True)
    assert a.allowed  # training is still fine
    assert not a.numeric_diet_targets


# --- body metrics -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("born", "today", "age"),
    [
        (dt.date(2008, 10, 28), dt.date(2026, 10, 28), 18),  # birthday today
        (dt.date(2008, 10, 28), dt.date(2026, 10, 27), 17),  # day before
        (dt.date(2008, 10, 29), dt.date(2026, 10, 28), 17),  # tomorrow
        (dt.date(2000, 1, 1), dt.date(2026, 10, 28), 26),
        (dt.date(2008, 2, 29), dt.date(2026, 2, 28), 17),  # leap-day birthday ...
        (dt.date(2008, 2, 29), dt.date(2026, 3, 1), 18),  # ... reached on Mar 1
        (dt.date(2008, 2, 29), dt.date(2028, 2, 29), 20),
        (dt.date(2026, 10, 28), dt.date(2026, 10, 28), 0),
    ],
)
def test_age_years(born: dt.date, today: dt.date, age: int) -> None:
    assert body_metrics.age_years(born, today) == age


def test_is_adult_boundary() -> None:
    today = dt.date(2026, 10, 28)
    assert body_metrics.is_adult(dt.date(2008, 10, 28), today)
    assert not body_metrics.is_adult(dt.date(2008, 10, 29), today)
    assert body_metrics.is_adult(dt.date(2008, 10, 29), today, minimum_age=17)


def test_age_rejects_future_birth_date() -> None:
    with pytest.raises(ValueError, match="future"):
        body_metrics.age_years(dt.date(2030, 1, 1), dt.date(2026, 10, 28))


def test_bmi_values_and_bad_input() -> None:
    assert body_metrics.bmi(70, 175) == pytest.approx(22.857, abs=0.001)
    assert body_metrics.bmi(45, 180) == pytest.approx(13.889, abs=0.001)
    for weight, height in [(0, 175), (70, 0), (-1, 175), (70, -5)]:
        with pytest.raises(ValueError, match="positive"):
            body_metrics.bmi(weight, height)
