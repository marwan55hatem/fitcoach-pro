"""Health screening and plan-access decisions.

Pure and deterministic on purpose: safety decisions must be predictable and testable,
so they are code, never AI. The coach may *explain* these results, never override them.
"""

import enum
from dataclasses import dataclass

from app.models.enums import Goal, RiskLevel

# Below this BMI, a weight-loss goal is treated as a disordered-eating risk.
LOW_BMI_WITH_CUT = 18.5

_RISK_ORDER = {RiskLevel.OK: 0, RiskLevel.CAUTION: 1, RiskLevel.BLOCKED: 2}


class ReasonCode(enum.StrEnum):
    CHEST_PAIN = "chest_pain"
    HEART_CONDITION = "heart_condition"
    UNCONTROLLED_BLOOD_PRESSURE = "uncontrolled_blood_pressure"
    FAINTING_OR_DIZZINESS = "fainting_or_dizziness"
    PREGNANCY = "pregnancy"
    RECENT_SURGERY = "recent_surgery"
    MEDICATION_AFFECTING_EXERCISE = "medication_affecting_exercise"


class Note(enum.StrEnum):
    """Informational: recorded and shown, but does not change the risk level."""

    JOINT_OR_BONE_PROBLEM = "joint_or_bone_problem"


class PlanBlock(enum.StrEnum):
    PROFILE_INCOMPLETE = "profile_incomplete"
    SCREENING_MISSING = "screening_missing"
    CLEARANCE_REQUIRED = "clearance_required"


REASON_RISK: dict[ReasonCode, RiskLevel] = {
    ReasonCode.CHEST_PAIN: RiskLevel.BLOCKED,
    ReasonCode.HEART_CONDITION: RiskLevel.BLOCKED,
    ReasonCode.UNCONTROLLED_BLOOD_PRESSURE: RiskLevel.BLOCKED,
    ReasonCode.FAINTING_OR_DIZZINESS: RiskLevel.BLOCKED,
    ReasonCode.PREGNANCY: RiskLevel.CAUTION,
    ReasonCode.RECENT_SURGERY: RiskLevel.CAUTION,
    ReasonCode.MEDICATION_AFFECTING_EXERCISE: RiskLevel.CAUTION,
}

REASON_MESSAGES: dict[str, str] = {
    ReasonCode.CHEST_PAIN: (
        "Chest pain during activity or at rest needs a doctor's check before "
        "any training plan."
    ),
    ReasonCode.HEART_CONDITION: (
        "A heart condition means exercise should be cleared by your doctor first."
    ),
    ReasonCode.UNCONTROLLED_BLOOD_PRESSURE: (
        "Uncontrolled blood pressure needs medical clearance before training."
    ),
    ReasonCode.FAINTING_OR_DIZZINESS: (
        "Fainting or dizziness should be checked by a doctor before training."
    ),
    ReasonCode.PREGNANCY: (
        "Pregnancy: please get your doctor's or midwife's go-ahead before training."
    ),
    ReasonCode.RECENT_SURGERY: (
        "After recent surgery, please confirm with your surgeon when training is safe."
    ),
    ReasonCode.MEDICATION_AFFECTING_EXERCISE: (
        "Some medications change how your body responds to exercise; "
        "check with your doctor."
    ),
    Note.JOINT_OR_BONE_PROBLEM: (
        "Add the affected area under injuries so exercises can be adapted."
    ),
    PlanBlock.PROFILE_INCOMPLETE: "Finish your profile (basics, goal, schedule, equipment).",
    PlanBlock.SCREENING_MISSING: "Complete the health screening first.",
    PlanBlock.CLEARANCE_REQUIRED: (
        "Your screening needs medical clearance. Confirm it once you have your doctor's OK."
    ),
}


@dataclass(frozen=True)
class ScreeningAnswers:
    chest_pain: bool
    heart_condition: bool
    blood_pressure_uncontrolled: bool
    fainting_or_dizziness: bool
    pregnant: bool
    recent_surgery: bool
    medication_affecting_exercise: bool
    joint_or_bone_problem: bool
    eating_concern: bool


@dataclass(frozen=True)
class ScreeningResult:
    risk_level: RiskLevel
    reasons: tuple[ReasonCode, ...]
    notes: tuple[Note, ...]


def evaluate_screening(answers: ScreeningAnswers) -> ScreeningResult:
    """Highest-risk answer wins. Eating concerns are NOT here: see disordered_eating_flag."""
    reasons: list[ReasonCode] = []
    if answers.chest_pain:
        reasons.append(ReasonCode.CHEST_PAIN)
    if answers.heart_condition:
        reasons.append(ReasonCode.HEART_CONDITION)
    if answers.blood_pressure_uncontrolled:
        reasons.append(ReasonCode.UNCONTROLLED_BLOOD_PRESSURE)
    if answers.fainting_or_dizziness:
        reasons.append(ReasonCode.FAINTING_OR_DIZZINESS)
    if answers.pregnant:
        reasons.append(ReasonCode.PREGNANCY)
    if answers.recent_surgery:
        reasons.append(ReasonCode.RECENT_SURGERY)
    if answers.medication_affecting_exercise:
        reasons.append(ReasonCode.MEDICATION_AFFECTING_EXERCISE)

    risk = max(
        (REASON_RISK[r] for r in reasons),
        key=_RISK_ORDER.__getitem__,
        default=RiskLevel.OK,
    )
    notes = (Note.JOINT_OR_BONE_PROBLEM,) if answers.joint_or_bone_problem else ()
    return ScreeningResult(risk_level=risk, reasons=tuple(reasons), notes=notes)


def disordered_eating_flag(
    *, eating_concern: bool, bmi: float | None, goal: Goal | None
) -> bool:
    """True when numeric diet targets must be withheld.

    Recomputed whenever weight or goal changes: it is not a one-off screening result.
    """
    if eating_concern:
        return True
    return bmi is not None and goal is Goal.CUT and bmi < LOW_BMI_WITH_CUT


@dataclass(frozen=True)
class PlanAccess:
    allowed: bool
    numeric_diet_targets: bool
    blocked_reasons: tuple[PlanBlock, ...]


def can_receive_plan(
    *,
    profile_complete: bool,
    screening_risk: RiskLevel | None,
    clearance_confirmed: bool,
    disordered_eating: bool,
) -> PlanAccess:
    """May this user be given a training program / nutrition plan right now?

    caution and blocked both need the user's confirmation that they have medical
    clearance. The disordered-eating flag never unlocks numeric diet targets, even
    with clearance: training is allowed, numbers are not.
    """
    blocks: list[PlanBlock] = []
    if not profile_complete:
        blocks.append(PlanBlock.PROFILE_INCOMPLETE)
    if screening_risk is None:
        blocks.append(PlanBlock.SCREENING_MISSING)
    elif screening_risk is not RiskLevel.OK and not clearance_confirmed:
        blocks.append(PlanBlock.CLEARANCE_REQUIRED)

    allowed = not blocks
    return PlanAccess(
        allowed=allowed,
        numeric_diet_targets=allowed and not disordered_eating,
        blocked_reasons=tuple(blocks),
    )
