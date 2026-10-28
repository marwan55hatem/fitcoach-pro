import datetime as dt
import logging
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import require_diet_targets, require_plan_access
from app.db.session import get_db
from app.main import create_app
from app.models import (
    AuditLog,
    DietaryPreference,
    HealthScreening,
    Limitation,
    Profile,
    User,
    WeightLog,
)
from tests.helpers import Account, grant_consents, register_and_login

CAIRO = ZoneInfo("Africa/Cairo")


def cairo_today() -> dt.date:
    return dt.datetime.now(CAIRO).date()


def years_ago(day: dt.date, years: int) -> dt.date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # Feb 29
        return day.replace(year=day.year - years, day=28)


def profile_payload(**override: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "birth_date": years_ago(cairo_today(), 30).isoformat(),
        "sex": "male",
        "height_cm": 193,
        "weight_kg": 70,
        "goal": "lean_bulk",
        "experience": "intermediate",
        "days_per_week": 4,
        "session_minutes": 60,
        "equipment": ["barbell", "dumbbell", "cable", "machine", "bench"],
        "activity_level": "moderate",
        "timezone": "Africa/Cairo",
        "language": "en",
    }
    payload.update(override)
    return payload


SCREEN_NO: dict[str, Any] = {
    "chest_pain": False,
    "heart_condition": False,
    "blood_pressure_uncontrolled": False,
    "fainting_or_dizziness": False,
    "pregnant": False,
    "recent_surgery": False,
    "medication_affecting_exercise": False,
    "joint_or_bone_problem": False,
    "eating_concern": False,
}


def screen(**yes: Any) -> dict[str, Any]:
    return {**SCREEN_NO, **yes}


@pytest.fixture
def user(client: TestClient) -> Account:
    account = register_and_login(client)
    grant_consents(client, account, "health_data")
    return account


def put(client: TestClient, account: Account, path: str, body: dict[str, Any]):
    return client.put(f"/onboarding/{path}", headers=account.headers, json=body)


def post(client: TestClient, account: Account, path: str, body: dict[str, Any]):
    return client.post(f"/onboarding/{path}", headers=account.headers, json=body)


def get(client: TestClient, account: Account, path: str):
    return client.get(f"/onboarding/{path}", headers=account.headers)


def complete_onboarding(client: TestClient, account: Account, **screening: Any) -> None:
    assert put(client, account, "profile", profile_payload()).status_code == 200
    assert put(client, account, "limitations", {"limitations": []}).status_code == 200
    assert put(client, account, "diet", {"meals_per_day": 4}).status_code == 200
    assert post(client, account, "screening", screen(**screening)).status_code == 201


# --- access control ---------------------------------------------------------------------


def test_every_endpoint_requires_auth(client: TestClient) -> None:
    for method, path in [
        ("PUT", "profile"),
        ("GET", "profile"),
        ("PUT", "limitations"),
        ("GET", "limitations"),
        ("PUT", "diet"),
        ("GET", "diet"),
        ("POST", "screening"),
        ("GET", "screening"),
        ("POST", "clearance"),
        ("GET", "status"),
    ]:
        assert (
            client.request(method, f"/onboarding/{path}", json={}).status_code == 401
        ), path


def test_health_endpoints_need_health_data_consent(client: TestClient) -> None:
    account = register_and_login(client)
    for method, path, body in [
        ("PUT", "profile", profile_payload()),
        ("GET", "profile", None),
        ("PUT", "limitations", {"limitations": []}),
        ("PUT", "diet", {"meals_per_day": 3}),
        ("POST", "screening", screen()),
        ("POST", "clearance", {"confirm": True}),
    ]:
        res = client.request(
            method, f"/onboarding/{path}", headers=account.headers, json=body
        )
        assert res.status_code == 403, path
        assert res.json()["detail"]["consent"] == "health_data"

    # Status must work without consent: it is how the wizard learns consent is missing.
    status = get(client, account, "status")
    assert status.status_code == 200
    assert "consent" in status.json()["missing"]


def test_revoking_consent_blocks_further_writes(
    client: TestClient, user: Account
) -> None:
    assert put(client, user, "profile", profile_payload()).status_code == 200
    client.put(
        "/consents", headers=user.headers, json={"consents": {"health_data": False}}
    )
    assert put(client, user, "profile", profile_payload()).status_code == 403


# --- profile ----------------------------------------------------------------------------


def test_profile_saved_with_weight_log(
    client: TestClient, db: Session, user: Account
) -> None:
    res = put(client, user, "profile", profile_payload(weight_kg=70.456))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["height_cm"] == 193.0 and body["weight_kg"] == 70.46
    assert body["timezone"] == "Africa/Cairo" and body["language"] == "en"

    profile = db.scalars(sa.select(Profile).where(Profile.user_id == user.id)).one()
    assert profile.goal == "lean_bulk" and profile.days_per_week == 4
    log = db.scalars(sa.select(WeightLog).where(WeightLog.user_id == user.id)).one()
    assert log.date == cairo_today() and float(log.weight_kg) == 70.46


def test_resubmitting_profile_upserts_without_duplicates(
    client: TestClient, db: Session, user: Account
) -> None:
    put(client, user, "profile", profile_payload(weight_kg=70))
    res = put(client, user, "profile", profile_payload(weight_kg=71.5, days_per_week=5))
    assert res.json()["weight_kg"] == 71.5 and res.json()["days_per_week"] == 5

    assert (
        len(db.scalars(sa.select(Profile).where(Profile.user_id == user.id)).all()) == 1
    )
    logs = db.scalars(sa.select(WeightLog).where(WeightLog.user_id == user.id)).all()
    assert len(logs) == 1 and float(logs[0].weight_kg) == 71.5


def test_weight_log_uses_the_users_timezone(
    client: TestClient, db: Session, user: Account
) -> None:
    tz = "Pacific/Kiritimati"  # UTC+14: a different calendar day than Cairo for hours
    put(client, user, "profile", profile_payload(timezone=tz))
    log = db.scalars(sa.select(WeightLog).where(WeightLog.user_id == user.id)).one()
    assert log.date == dt.datetime.now(ZoneInfo(tz)).date()


def test_equipment_is_deduplicated(client: TestClient, user: Account) -> None:
    res = put(
        client,
        user,
        "profile",
        profile_payload(equipment=["barbell", "barbell", "dumbbell"]),
    )
    assert res.json()["equipment"] == ["barbell", "dumbbell"]


def test_under_18_is_rejected_and_nothing_is_saved(
    client: TestClient, db: Session, user: Account
) -> None:
    today = cairo_today()
    almost_18 = years_ago(today, 18) + dt.timedelta(days=1)  # turns 18 tomorrow
    res = put(
        client, user, "profile", profile_payload(birth_date=almost_18.isoformat())
    )
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "underage"
    assert "18" in res.json()["detail"]["message"]

    assert (
        db.scalars(sa.select(Profile).where(Profile.user_id == user.id)).first() is None
    )
    assert (
        db.scalars(sa.select(WeightLog).where(WeightLog.user_id == user.id)).first()
        is None
    )

    # Turning 18 today is allowed.
    ok = put(
        client,
        user,
        "profile",
        profile_payload(birth_date=years_ago(today, 18).isoformat()),
    )
    assert ok.status_code == 200


def test_under_18_cannot_overwrite_an_existing_profile(
    client: TestClient, db: Session, user: Account
) -> None:
    put(client, user, "profile", profile_payload(weight_kg=70))
    kid = (cairo_today() - dt.timedelta(days=365 * 16)).isoformat()
    assert (
        put(
            client, user, "profile", profile_payload(birth_date=kid, weight_kg=99)
        ).status_code
        == 422
    )
    log = db.scalars(sa.select(WeightLog).where(WeightLog.user_id == user.id)).one()
    assert float(log.weight_kg) == 70


@pytest.mark.parametrize(
    "override",
    [
        {"days_per_week": 0},
        {"days_per_week": 8},
        {"weight_kg": 29},
        {"weight_kg": 301},
        {"height_cm": 99},
        {"height_cm": 251},
        {"session_minutes": 19},
        {"session_minutes": 181},
        {"timezone": "Mars/Olympus"},
        {"equipment": []},
        {"equipment": ["jetpack"]},
        {"birth_date": "2999-01-01"},
        {"birth_date": "1850-01-01"},
        {"language": "fr"},
        {"goal": "get_huge"},
        {"sex": "robot"},
    ],
)
def test_profile_validation_ranges(
    client: TestClient, user: Account, override: dict[str, Any]
) -> None:
    assert put(client, user, "profile", profile_payload(**override)).status_code == 422


def test_profile_boundaries_are_accepted(client: TestClient, user: Account) -> None:
    edge = profile_payload(
        days_per_week=7, weight_kg=30, height_cm=100, session_minutes=180
    )
    assert put(client, user, "profile", edge).status_code == 200
    edge = profile_payload(
        days_per_week=1, weight_kg=300, height_cm=250, session_minutes=20
    )
    assert put(client, user, "profile", edge).status_code == 200


# --- limitations and diet ---------------------------------------------------------------


def test_limitations_replace_all_and_reviewed_flag(
    client: TestClient, db: Session, user: Account
) -> None:
    before = get(client, user, "limitations").json()
    assert before == {"reviewed": False, "limitations": []}

    two = {
        "limitations": [
            {
                "body_area": "shoulder",
                "severity": "moderate",
                "description": "pinches overhead",
            },
            {"body_area": "knee", "severity": "mild"},
        ]
    }
    res = put(client, user, "limitations", two)
    assert res.status_code == 200
    assert res.json()["reviewed"] is True and len(res.json()["limitations"]) == 2

    # Replace-all: the second submission fully replaces the first.
    res = put(
        client,
        user,
        "limitations",
        {"limitations": [{"body_area": "lower_back", "severity": "severe"}]},
    )
    areas = [item["body_area"] for item in res.json()["limitations"]]
    assert areas == ["lower_back"]
    assert (
        len(
            db.scalars(sa.select(Limitation).where(Limitation.user_id == user.id)).all()
        )
        == 1
    )

    # An empty list is a valid answer ("no injuries") and still counts as reviewed.
    res = put(client, user, "limitations", {"limitations": []})
    assert res.json() == {"reviewed": True, "limitations": []}
    assert get(client, user, "limitations").json()["reviewed"] is True


@pytest.mark.parametrize(
    "item",
    [
        {"body_area": "elbowz", "severity": "mild"},
        {"body_area": "knee", "severity": "catastrophic"},
        {"body_area": "knee", "severity": "mild", "description": "x" * 501},
    ],
)
def test_limitations_validation(
    client: TestClient, user: Account, item: dict[str, Any]
) -> None:
    assert put(client, user, "limitations", {"limitations": [item]}).status_code == 422


def test_diet_upsert_and_normalisation(
    client: TestClient, db: Session, user: Account
) -> None:
    assert get(client, user, "diet").json() is None

    res = put(
        client,
        user,
        "diet",
        {
            "allergies": ["Peanuts", " peanuts ", "Shellfish"],
            "dislikes": ["liver"],
            "meals_per_day": 5,
            "budget_level": "medium",
            "cuisine_hints": ["egyptian"],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["allergies"] == ["peanuts", "shellfish"]

    put(client, user, "diet", {"meals_per_day": 3})
    rows = db.scalars(
        sa.select(DietaryPreference).where(DietaryPreference.user_id == user.id)
    ).all()
    assert len(rows) == 1 and rows[0].meals_per_day == 3 and rows[0].allergies == []
    assert get(client, user, "diet").json()["meals_per_day"] == 3


@pytest.mark.parametrize("meals", [0, 9])
def test_diet_validation(client: TestClient, user: Account, meals: int) -> None:
    assert put(client, user, "diet", {"meals_per_day": meals}).status_code == 422
    assert (
        put(
            client, user, "diet", {"meals_per_day": 3, "budget_level": "free"}
        ).status_code
        == 422
    )


# --- screening --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("yes", "risk", "reason_codes"),
    [
        ({}, "ok", []),
        ({"chest_pain": True}, "blocked", ["chest_pain"]),
        ({"heart_condition": True}, "blocked", ["heart_condition"]),
        (
            {"blood_pressure_uncontrolled": True},
            "blocked",
            ["uncontrolled_blood_pressure"],
        ),
        ({"fainting_or_dizziness": True}, "blocked", ["fainting_or_dizziness"]),
        ({"pregnant": True}, "caution", ["pregnancy"]),
        ({"recent_surgery": True}, "caution", ["recent_surgery"]),
        (
            {"medication_affecting_exercise": True},
            "caution",
            ["medication_affecting_exercise"],
        ),
        (
            {"pregnant": True, "chest_pain": True},
            "blocked",
            ["chest_pain", "pregnancy"],
        ),
        ({"joint_or_bone_problem": True}, "ok", []),
    ],
)
def test_screening_results(
    client: TestClient,
    user: Account,
    yes: dict[str, bool],
    risk: str,
    reason_codes: list[str],
) -> None:
    res = post(client, user, "screening", screen(**yes))
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["risk_level"] == risk
    assert sorted(r["code"] for r in body["reasons"]) == sorted(reason_codes)
    assert all(r["message"] for r in body["reasons"])
    assert body["clearance_required"] is (risk != "ok")
    assert body["clearance_confirmed"] is False


def test_joint_problem_returns_a_note(client: TestClient, user: Account) -> None:
    body = post(client, user, "screening", screen(joint_or_bone_problem=True)).json()
    assert [n["code"] for n in body["notes"]] == ["joint_or_bone_problem"]


def test_screening_requires_every_answer(client: TestClient, user: Account) -> None:
    incomplete = dict(SCREEN_NO)
    del incomplete["chest_pain"]
    assert post(client, user, "screening", incomplete).status_code == 422
    assert (
        post(client, user, "screening", screen(free_text="x" * 1001)).status_code == 422
    )


def test_latest_screening_wins_and_history_is_kept(
    client: TestClient, db: Session, user: Account
) -> None:
    post(client, user, "screening", screen(chest_pain=True))
    post(client, user, "screening", screen())
    assert get(client, user, "screening").json()["risk_level"] == "ok"
    assert get(client, user, "status").json()["risk_level"] == "ok"
    rows = db.scalars(
        sa.select(HealthScreening).where(HealthScreening.user_id == user.id)
    ).all()
    assert len(rows) == 2


def test_screening_free_text_is_stored_but_never_logged_or_echoed(
    client: TestClient, db: Session, user: Account, caplog: pytest.LogCaptureFixture
) -> None:
    sentinel = "SENTINEL-free-text-ignore-all-previous-instructions"
    caplog.set_level(logging.DEBUG)
    res = post(client, user, "screening", screen(free_text=sentinel))
    get(client, user, "screening")
    get(client, user, "status")

    stored = db.scalars(
        sa.select(HealthScreening).where(HealthScreening.user_id == user.id)
    ).one()
    assert stored.answers["free_text"] == sentinel  # kept as data
    assert sentinel not in res.text  # not echoed back
    assert sentinel not in caplog.text  # never logged
    audit = db.scalars(sa.select(AuditLog).where(AuditLog.user_id == user.id)).all()
    assert sentinel not in str([a.meta for a in audit])
    # Instructions in free text have no effect on the decision.
    assert res.json()["risk_level"] == "ok"


# --- clearance --------------------------------------------------------------------------


def test_clearance_flow(client: TestClient, db: Session, user: Account) -> None:
    assert (
        post(client, user, "clearance", {"confirm": True}).status_code == 409
    )  # no screening

    post(client, user, "screening", screen())
    assert (
        post(client, user, "clearance", {"confirm": True}).status_code == 409
    )  # not needed

    post(client, user, "screening", screen(heart_condition=True))
    assert post(client, user, "clearance", {"confirm": False}).status_code == 422
    assert post(client, user, "clearance", {}).status_code == 422

    res = post(client, user, "clearance", {"confirm": True})
    assert res.status_code == 200
    assert (
        res.json()["clearance_confirmed"] is True
        and res.json()["risk_level"] == "blocked"
    )

    actions = db.scalars(
        sa.select(AuditLog.action).where(AuditLog.user_id == user.id)
    ).all()
    assert "onboarding.clearance_confirmed" in actions


def test_new_screening_resets_clearance_only_when_risk_level_changes(
    client: TestClient, user: Account
) -> None:
    post(client, user, "screening", screen(chest_pain=True))
    post(client, user, "clearance", {"confirm": True})

    # Same level (blocked) with different reasons: clearance carries over.
    again = post(client, user, "screening", screen(heart_condition=True)).json()
    assert again["risk_level"] == "blocked" and again["clearance_confirmed"] is True

    # Level drops to caution: reset.
    lower = post(client, user, "screening", screen(pregnant=True)).json()
    assert lower["risk_level"] == "caution" and lower["clearance_confirmed"] is False

    # Cleared at caution, then it rises to blocked: reset again.
    post(client, user, "clearance", {"confirm": True})
    higher = post(client, user, "screening", screen(chest_pain=True)).json()
    assert higher["clearance_confirmed"] is False

    # Back to ok and then to blocked again: old clearance is gone.
    post(client, user, "clearance", {"confirm": True})
    post(client, user, "screening", screen())
    final = post(client, user, "screening", screen(chest_pain=True)).json()
    assert final["clearance_confirmed"] is False


# --- status -----------------------------------------------------------------------------


def test_status_progression(client: TestClient, user: Account) -> None:
    status = get(client, user, "status").json()
    assert status["missing"] == ["profile", "limitations", "diet", "screening"]
    assert not status["onboarding_complete"] and status["risk_level"] is None
    assert status["plan_access"]["allowed"] is False

    put(client, user, "profile", profile_payload())
    assert get(client, user, "status").json()["missing"] == [
        "limitations",
        "diet",
        "screening",
    ]
    put(client, user, "limitations", {"limitations": []})
    put(client, user, "diet", {"meals_per_day": 4})
    assert get(client, user, "status").json()["missing"] == ["screening"]

    post(client, user, "screening", screen())
    status = get(client, user, "status").json()
    assert status["missing"] == [] and status["onboarding_complete"] is True
    assert status["plan_access"] == {
        "allowed": True,
        "numeric_diet_targets": True,
        "blocked_reasons": [],
    }
    assert [s["key"] for s in status["steps"]] == [
        "consent",
        "profile",
        "limitations",
        "diet",
        "screening",
    ]


def test_status_for_blocked_user_adds_clearance_step(
    client: TestClient, user: Account
) -> None:
    complete_onboarding(client, user, chest_pain=True)
    status = get(client, user, "status").json()
    assert status["missing"] == ["clearance"] and not status["onboarding_complete"]
    assert status["clearance_required"] is True
    assert [r["code"] for r in status["plan_access"]["blocked_reasons"]] == [
        "clearance_required"
    ]

    post(client, user, "clearance", {"confirm": True})
    status = get(client, user, "status").json()
    assert status["missing"] == [] and status["onboarding_complete"] is True
    assert status["plan_access"]["allowed"] is True


def test_status_is_isolated_between_users(client: TestClient, user: Account) -> None:
    complete_onboarding(client, user)
    other = register_and_login(client)
    grant_consents(client, other, "health_data")
    status = get(client, other, "status").json()
    assert status["missing"] == ["profile", "limitations", "diet", "screening"]
    assert get(client, other, "profile").json()["weight_kg"] is None
    assert get(client, other, "limitations").json()["limitations"] == []
    assert get(client, other, "diet").json() is None
    assert get(client, other, "screening").json() is None


# --- the dependencies feature endpoints will use ----------------------------------------


def guarded_app(db: Session) -> TestClient:
    """Stand-ins for the program and nutrition endpoints, wired exactly as they will be."""
    app: FastAPI = create_app()
    app.dependency_overrides[get_db] = lambda: db

    @app.post("/_test/program")
    def program(user: Annotated[User, Depends(require_plan_access)]) -> dict[str, str]:
        return {"ok": "yes"}

    @app.get("/_test/targets")
    def targets(user: Annotated[User, Depends(require_diet_targets)]) -> dict[str, str]:
        return {"ok": "yes"}

    return TestClient(app)


def test_plan_access_dependency_blocks_until_cleared(
    client: TestClient, db: Session, user: Account
) -> None:
    guarded = guarded_app(db)
    assert guarded.post("/_test/program").status_code == 401

    # Nothing done yet.
    res = guarded.post("/_test/program", headers=user.headers)
    assert res.status_code == 403
    detail = res.json()["detail"]
    assert detail["code"] == "plan_access_denied"
    assert set(detail["reasons"]) == {"profile_incomplete", "screening_missing"}

    put(client, user, "profile", profile_payload())
    post(client, user, "screening", screen(chest_pain=True))
    res = guarded.post("/_test/program", headers=user.headers)
    assert res.status_code == 403 and res.json()["detail"]["reasons"] == [
        "clearance_required"
    ]

    post(client, user, "clearance", {"confirm": True})
    assert guarded.post("/_test/program", headers=user.headers).status_code == 200


def test_plan_access_needs_health_consent(
    client: TestClient, db: Session, user: Account
) -> None:
    complete_onboarding(client, user)
    guarded = guarded_app(db)
    assert guarded.post("/_test/program", headers=user.headers).status_code == 200
    client.put(
        "/consents", headers=user.headers, json={"consents": {"health_data": False}}
    )
    res = guarded.post("/_test/program", headers=user.headers)
    assert res.status_code == 403 and res.json()["detail"]["code"] == "consent_required"


def test_diet_targets_withheld_for_eating_concern_even_with_clearance(
    client: TestClient, db: Session, user: Account
) -> None:
    complete_onboarding(client, user, eating_concern=True, pregnant=True)
    post(client, user, "clearance", {"confirm": True})
    guarded = guarded_app(db)

    assert (
        guarded.post("/_test/program", headers=user.headers).status_code == 200
    )  # training ok
    res = guarded.get("/_test/targets", headers=user.headers)
    assert res.status_code == 403
    assert res.json()["detail"]["code"] == "diet_targets_withheld"
    status = get(client, user, "status").json()
    assert status["disordered_eating_flag"] is True
    assert status["plan_access"]["numeric_diet_targets"] is False


def test_low_bmi_with_cut_goal_is_reevaluated_live(
    client: TestClient, db: Session, user: Account
) -> None:
    guarded = guarded_app(db)
    complete_onboarding(client, user)
    assert guarded.get("/_test/targets", headers=user.headers).status_code == 200

    # BMI 13.9 (45 kg, 180 cm) with a cut goal: numbers withheld immediately.
    put(
        client,
        user,
        "profile",
        profile_payload(weight_kg=45, height_cm=180, goal="cut"),
    )
    assert guarded.get("/_test/targets", headers=user.headers).status_code == 403
    assert guarded.post("/_test/program", headers=user.headers).status_code == 200

    # Changing the goal lifts the withholding: no stored flag to clear.
    put(
        client,
        user,
        "profile",
        profile_payload(weight_kg=45, height_cm=180, goal="lean_bulk"),
    )
    assert guarded.get("/_test/targets", headers=user.headers).status_code == 200

    # A healthy weight with a cut goal is fine too.
    put(
        client,
        user,
        "profile",
        profile_payload(weight_kg=75, height_cm=180, goal="cut"),
    )
    assert guarded.get("/_test/targets", headers=user.headers).status_code == 200


def test_deleting_the_account_removes_onboarding_data(
    client: TestClient, db: Session, user: Account
) -> None:
    from tests.helpers import delete_me

    complete_onboarding(client, user, pregnant=True)
    assert delete_me(client, user).status_code == 204
    for model in (Profile, Limitation, DietaryPreference, HealthScreening, WeightLog):
        assert (
            db.scalars(sa.select(model).where(model.user_id == user.id)).first() is None
        )
    assert db.scalars(sa.select(User).where(User.id == user.id)).first() is None
