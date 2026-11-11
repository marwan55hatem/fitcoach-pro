"""The coach's tools: READ tools return the user's own data, WRITE tools only create
PENDING proposals or checked notes. Nothing here applies a change.

Every tool is bound to the current user by the server; the model never chooses whose
data it sees. Results go back to the model as untrusted data.
"""

import datetime as dt
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.ai.types import ToolSpec
from app.ai.untrusted import wrap_untrusted
from app.models import CoachNote, Recommendation, User
from app.models.enums import RecommendationStatus
from app.rules.coach_notes import check_note
from app.rules.limits import Kind, validate_recommendation
from app.rules.review import weekly_review
from app.rules.training_stats import personal_best
from app.services import (
    approvals,
    body,
    coaching,
    inbody,
    nutrition,
    onboarding,
    programs,
    training,
)
from app.services import exercises as exercise_svc

PROGRAM_KINDS = (Kind.LOAD_CHANGE, Kind.DELOAD, Kind.SET_CUT, Kind.STALL_FIX)
NUTRITION_KINDS = (Kind.CALORIE_CHANGE, Kind.PROTEIN_CHANGE)
MAX_DAYS = 90


# --- arguments ------------------------------------------------------------------------


class _NoArgs(BaseModel):
    pass


class _Limit(BaseModel):
    limit: int = Field(default=5, ge=1, le=10)


class _Days(BaseModel):
    days: int = Field(default=14, ge=1, le=MAX_DAYS)


class _Exercise(BaseModel):
    exercise_id: uuid.UUID


class _Date(BaseModel):
    date: dt.date


class _Proposal(BaseModel):
    kind: str
    payload: dict[str, Any]
    reason: str = Field(max_length=500)


class _Note(BaseModel):
    key: str = Field(max_length=200)
    value: str = Field(max_length=1000)


@dataclass(frozen=True)
class ToolOutcome:
    content: str  # what the model reads (fenced as untrusted data)
    proposal: dict[str, Any] | None = None  # a pending recommendation to show


Handler = Callable[[Session, User, Any], Any]


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


# --- READ tools -------------------------------------------------------------------------


def get_profile(db: Session, user: User, _: _NoArgs) -> dict[str, Any]:
    """Training facts only: never the name or email."""
    profile = onboarding.get_profile(db, user.id)
    state = onboarding.onboarding_state(db, user)
    weight = onboarding.latest_weight(db, user.id)
    today = onboarding.user_today(db, user)
    if profile is None:
        return {"profile": None}
    age = (
        today.year
        - profile.birth_date.year
        - (
            (today.month, today.day)
            < (profile.birth_date.month, profile.birth_date.day)
        )
        if profile.birth_date
        else None
    )
    return {
        "age": age,
        "sex": profile.sex,
        "height_cm": _num(profile.height_cm),
        "weight_kg": _num(weight.weight_kg) if weight else None,
        "goal": profile.goal,
        "experience": profile.experience,
        "days_per_week": profile.days_per_week,
        "session_minutes": profile.session_minutes,
        "equipment": list(profile.equipment or []),
        "limitations": [
            {"area": lim.body_area, "severity": lim.severity}
            for lim in onboarding.list_limitations(db, user.id)
            if lim.active
        ],
        "health_check": state.result.risk_level if state.result else None,
        "training_allowed": state.access.allowed,
        "diet_numbers_allowed": state.access.numeric_diet_targets,
        "today": today.isoformat(),
    }


def get_program(db: Session, user: User, _: _NoArgs) -> dict[str, Any]:
    program = programs.current_program(db, user)
    if program is None:
        return {"program": None}
    names = training.exercise_names_by_id(
        db, [e.exercise_id for d in program.days for e in d.exercises]
    )
    return {
        "version": program.version,
        "days": [
            {
                "day": d.day_index,
                "name": d.name,
                "exercises": [
                    {
                        "exercise_id": str(e.exercise_id),
                        "name": names.get(e.exercise_id, ""),
                        "sets": e.sets,
                        "reps": f"{e.rep_min}-{e.rep_max}",
                        "rest_s": e.rest_s,
                    }
                    for e in d.exercises
                ],
            }
            for d in program.days
        ],
    }


def get_recent_sessions(db: Session, user: User, args: _Limit) -> dict[str, Any]:
    sessions, _ = training.list_sessions(db, user, limit=args.limit, offset=0)
    days = training.day_names(db, sessions)
    names = training.exercise_names(db, sessions)
    out = []
    for s in sessions:
        best: dict[str, dict[str, Any]] = {}
        for row in s.sets:
            if not row.done or row.is_warmup:
                continue
            name = names.get(row.exercise_id, "")
            current = best.get(name)
            weight = float(row.weight_kg or 0)
            if current is None or (weight, row.reps or 0) > (
                current["weight_kg"],
                current["reps"],
            ):
                best[name] = {"weight_kg": weight, "reps": row.reps or 0}
        out.append(
            {
                "date": s.performed_on.isoformat(),
                "day": days.get(s.program_day_id) if s.program_day_id else None,
                "finished": s.ended_at is not None,
                "sets_done": sum(1 for r in s.sets if r.done),
                "sets_planned": len(s.sets),
                "best_set_per_exercise": best,
            }
        )
    return {"sessions": out}


def get_exercise_history(db: Session, user: User, args: _Exercise) -> dict[str, Any]:
    exercise = exercise_svc.get_visible(db, user, args.exercise_id)  # scoped
    last_date, last_sets, history = training.last_performance(db, user, exercise.id)
    best = personal_best(history)
    return {
        "exercise": exercise.name,
        "last_date": last_date.isoformat() if last_date else None,
        "last_sets": [
            {"weight_kg": _num(s.weight_kg), "reps": s.reps} for s in last_sets
        ],
        "heaviest_kg": best.heaviest.weight_kg if best and best.heaviest else None,
        "best_estimated_1rm_kg": round(best.best_e1rm.estimated_1rm_kg, 1)
        if best and best.best_e1rm and best.best_e1rm.estimated_1rm_kg
        else None,
        "sessions_logged": len({s.performed_on for s in history}),
    }


def get_weight_trend(db: Session, user: User, args: _Days) -> dict[str, Any]:
    today = onboarding.user_today(db, user)
    points, weekly = body.weight_trend(db, user, days=args.days, today=today)
    return {
        "points": [
            {
                "date": p.date.isoformat(),
                "kg": p.weight_kg,
                "avg_7d": round(p.average_7d, 2),
            }
            for p in points
        ],
        "weekly_change_kg": round(weekly, 2) if weekly is not None else None,
    }


def get_inbody_trend(db: Session, user: User, _: _NoArgs) -> dict[str, Any]:
    """Confirmed scans only: drafts are never used."""
    trend = inbody.trend(db, user)
    return json.loads(json.dumps(trend, default=str))


def _withheld() -> dict[str, Any]:
    return {
        "withheld": True,
        "note": "Numeric diet targets are withheld for this user by the health check. "
        "Do not discuss calories, macros or weights of food.",
    }


def get_nutrition_summary(db: Session, user: User, args: _Days) -> dict[str, Any]:
    if not onboarding.onboarding_state(db, user).access.numeric_diet_targets:
        return _withheld()
    today = onboarding.user_today(db, user)
    days = min(args.days, 14)
    targets = nutrition.targets_for(db, user)
    out = []
    for offset in range(days):
        day = today - dt.timedelta(days=offset)
        totals = nutrition.totals_of(nutrition.meals_on(db, user, day))
        out.append(
            {
                "date": day.isoformat(),
                "kcal": totals.kcal,
                "protein_g": round(totals.protein_g),
                "meals_logged": totals.meals_counted,
            }
        )
    return {
        "targets": {"kcal": targets.kcal, "protein_g": targets.protein_g},
        "days": out,
    }


def get_food_log(db: Session, user: User, args: _Date) -> dict[str, Any]:
    numbers = onboarding.onboarding_state(db, user).access.numeric_diet_targets
    meals = nutrition.meals_on(db, user, args.date)
    return {
        "date": args.date.isoformat(),
        "meals": [
            {
                "slot": m.slot,
                "name": m.name,
                "confirmed": m.confirmed,
                **({"kcal": m.kcal, "protein_g": _num(m.protein_g)} if numbers else {}),
            }
            for m in meals
        ],
        **({} if numbers else {"diet_numbers": "withheld by the health check"}),
    }


def get_checkins(db: Session, user: User, args: _Days) -> dict[str, Any]:
    today = onboarding.user_today(db, user)
    rows = body.list_checkins(db, user, days=min(args.days, 30), today=today)
    return {
        "checkins": [
            {
                "date": c.date.isoformat(),
                "sleep_h": _num(c.sleep_h),
                "soreness": c.soreness,
                "stress": c.stress,
                "energy": c.energy,
                "motivation": c.motivation,
            }
            for c in rows
        ]
    }


def get_coach_notes(db: Session, user: User, _: _NoArgs) -> dict[str, Any]:
    notes = db.scalars(
        sa.select(CoachNote).where(CoachNote.user_id == user.id).order_by(CoachNote.key)
    )
    return {"notes": [{"key": n.key, "value": n.value} for n in notes]}


def get_rule_recommendations(db: Session, user: User, _: _NoArgs) -> dict[str, Any]:
    recs = weekly_review(coaching.review_input(db, user))
    return {
        "recommendations": [
            {"kind": r.kind.value, "payload": dict(r.payload), "reason": r.reason}
            for r in recs
        ]
    }


# --- WRITE tools (pending only) ---------------------------------------------------------


def _propose(
    db: Session, user: User, args: _Proposal, allowed_kinds: tuple[Kind, ...]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if args.kind not in {k.value for k in allowed_kinds}:
        return {"status": "refused", "reason": "wrong_kind"}, None
    if args.kind in (Kind.LOAD_CHANGE, Kind.STALL_FIX):
        try:
            exercise_svc.get_visible(
                db, user, uuid.UUID(str(args.payload.get("exercise_id")))
            )
        except (ValueError, exercise_svc.ExerciseNotFoundError):
            return {"status": "refused", "reason": "unknown_exercise"}, None
    verdict = validate_recommendation(
        args.kind, args.payload, coaching.user_state(db, user)
    )
    if not verdict.allowed:
        return {"status": "refused", "reason": verdict.reason}, None
    rec = Recommendation(
        user_id=user.id,
        kind=args.kind,
        payload=dict(args.payload),
        explanation=args.reason,
        validation={"allowed": True, "reason": verdict.reason, "source": "coach"},
        status=RecommendationStatus.PENDING,
        source="coach",
        expires_at=approvals.expiry_from(dt.datetime.now(dt.UTC)),
    )
    db.add(rec)
    db.commit()
    card = {
        "id": str(rec.id),
        "kind": rec.kind,
        "payload": rec.payload,
        "reason": rec.explanation,
    }
    return {"status": "pending_user_approval", "id": str(rec.id)}, card


def propose_program_change(db: Session, user: User, args: _Proposal):
    return _propose(db, user, args, PROGRAM_KINDS)


def propose_nutrition_change(db: Session, user: User, args: _Proposal):
    return _propose(db, user, args, NUTRITION_KINDS)


def save_coach_note(db: Session, user: User, args: _Note) -> dict[str, Any]:
    problem = check_note(args.key, args.value)
    if problem is not None:
        return {"status": "refused", "reason": problem}
    note = db.scalar(
        sa.select(CoachNote).where(
            CoachNote.user_id == user.id, CoachNote.key == args.key
        )
    )
    if note is None:
        db.add(CoachNote(user_id=user.id, key=args.key, value=args.value, source="ai"))
    else:
        note.value, note.source = args.value, "ai"
    db.commit()
    return {"status": "saved", "visible_to_user": True}


# --- registry ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    args: type[BaseModel]
    handler: Handler
    writes: bool = False


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return schema


def _tool(
    name: str, description: str, args: type[BaseModel], handler, writes=False
) -> Tool:
    return Tool(ToolSpec(name, description, _schema(args)), args, handler, writes)


_PROPOSAL_HELP = (
    "kind is one of {kinds}. payload shapes: load_change {{exercise_id, action: "
    "add|reduce, from_kg, to_kg}}; deload {{weeks, set_factor}}; set_cut "
    "{{sets_delta: -1}}; stall_fix {{exercise_id, fix: vary_or_add_set|"
    "reduce_and_rebuild}}; calorie_change {{change_kcal}}; protein_change "
    "{{g_per_kg}}. It is checked against hard limits and waits for the user's approval."
)

TOOLS: dict[str, Tool] = {
    t.spec.name: t
    for t in [
        _tool(
            "get_profile",
            "The user's training facts, limits and health-check status.",
            _NoArgs,
            get_profile,
        ),
        _tool(
            "get_program",
            "The active program: days, exercises, sets and rep ranges.",
            _NoArgs,
            get_program,
        ),
        _tool(
            "get_recent_sessions",
            "Recent workouts with the best set per exercise.",
            _Limit,
            get_recent_sessions,
        ),
        _tool(
            "get_exercise_history",
            "Last time and personal bests for one exercise (id from get_program).",
            _Exercise,
            get_exercise_history,
        ),
        _tool(
            "get_weight_trend",
            "Daily weights with the 7-day average and the weekly change.",
            _Days,
            get_weight_trend,
        ),
        _tool(
            "get_inbody_trend",
            "Confirmed InBody scans over time and what the last change means.",
            _NoArgs,
            get_inbody_trend,
        ),
        _tool(
            "get_nutrition_summary",
            "Daily calories and protein against targets (confirmed meals).",
            _Days,
            get_nutrition_summary,
        ),
        _tool(
            "get_food_log",
            "The meals logged on one date (YYYY-MM-DD).",
            _Date,
            get_food_log,
        ),
        _tool(
            "get_checkins",
            "Recent check-ins: sleep, soreness, stress, energy, motivation.",
            _Days,
            get_checkins,
        ),
        _tool(
            "get_coach_notes",
            "What you remember about the user.",
            _NoArgs,
            get_coach_notes,
        ),
        _tool(
            "get_rule_recommendations",
            "What the coaching rules suggest this week.",
            _NoArgs,
            get_rule_recommendations,
        ),
        _tool(
            "propose_program_change",
            "Propose a training change. "
            + _PROPOSAL_HELP.format(kinds="load_change, deload, set_cut, stall_fix"),
            _Proposal,
            propose_program_change,
            writes=True,
        ),
        _tool(
            "propose_nutrition_change",
            "Propose a nutrition change. "
            + _PROPOSAL_HELP.format(kinds="calorie_change, protein_change"),
            _Proposal,
            propose_nutrition_change,
            writes=True,
        ),
        _tool(
            "save_coach_note",
            "Remember a short, lasting fact about the user (max 200 characters, shown to them).",
            _Note,
            save_coach_note,
            writes=True,
        ),
    ]
}
TOOL_SPECS: tuple[ToolSpec, ...] = tuple(t.spec for t in TOOLS.values())


def run_tool(
    db: Session, user_id: uuid.UUID, name: str, arguments: dict[str, Any]
) -> ToolOutcome:
    """Run one tool for this user. Errors become a short message the model can read."""
    tool = TOOLS.get(name)
    if tool is None:
        return ToolOutcome(wrap_untrusted(json.dumps({"error": "unknown_tool"}), name))
    user = db.get(User, user_id)
    if user is None:
        return ToolOutcome(wrap_untrusted(json.dumps({"error": "no_user"}), name))
    try:
        args = tool.args.model_validate(arguments or {})
    except ValidationError:
        return ToolOutcome(
            wrap_untrusted(json.dumps({"error": "invalid_arguments"}), name)
        )
    try:
        result = tool.handler(db, user, args)
    except (exercise_svc.ExerciseNotFoundError, nutrition.ProfileIncompleteError):
        db.rollback()
        return ToolOutcome(wrap_untrusted(json.dumps({"error": "not_found"}), name))
    proposal = None
    if isinstance(result, tuple):
        result, proposal = result
    return ToolOutcome(
        wrap_untrusted(
            json.dumps(result, default=str, ensure_ascii=False), f"tool_{name}"
        ),
        proposal,
    )
