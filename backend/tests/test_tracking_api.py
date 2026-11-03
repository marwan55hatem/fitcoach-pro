import datetime as dt
import logging
from collections.abc import Iterator
from contextlib import nullcontext
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai.client import AiClient, get_ai_client
from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.models import (
    Exercise,
    InbodyScan,
    MealLog,
    NutritionSettings,
    SetLog,
    WeightLog,
)
from app.models.enums import MealSlot, MealSource
from app.rules.program_validator import MUSCLE_GROUPS
from app.services.storage import Storage, get_storage
from tests.fakes.ai import FakeProvider
from tests.helpers import Account, delete_me, grant_consents, register_and_login
from tests.program_fixtures import add_library
from tests.test_onboarding import cairo_today, profile_payload, screen

TODAY = cairo_today()


@pytest.fixture
def library(db: Session) -> dict[str, Exercise]:
    return add_library(db)


@pytest.fixture
def client(db: Session, storage: Storage) -> Iterator[TestClient]:
    app = create_app()
    ai = AiClient(
        FakeProvider(),
        Settings(_env_file=None),
        lambda: nullcontext(db),
        sleep=lambda _s: None,
    )
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_ai_client] = lambda: ai
    with TestClient(app) as test_client:
        yield test_client


def onboard(client: TestClient, **profile: Any) -> Account:
    account = register_and_login(client)
    grant_consents(client, account, "health_data")
    equipment = ["barbell", "dumbbell", "ez_bar", "cable", "machine", "bench"]
    res = client.put(
        "/onboarding/profile",
        headers=account.headers,
        json=profile_payload(equipment=equipment, session_minutes=75, **profile),
    )
    assert res.status_code == 200, res.text
    res = client.post("/onboarding/screening", headers=account.headers, json=screen())
    assert res.status_code == 201, res.text
    return account


@pytest.fixture
def user(client: TestClient, library: dict[str, Exercise]) -> Account:
    return onboard(client)


@pytest.fixture
def program(client: TestClient, user: Account) -> dict[str, Any]:
    res = client.post(
        "/programs/generate", headers=user.headers, json={"use_ai": False}
    )
    assert res.status_code == 201, res.text
    res = client.post(f"/programs/{res.json()['id']}/accept", headers=user.headers)
    assert res.status_code == 200, res.text
    return res.json()


def start(client: TestClient, account: Account, day_id: str | None, **body: Any):
    return client.post(
        "/sessions",
        headers=account.headers,
        json={"program_day_id": day_id, **body},
    )


def started(client: TestClient, account: Account, day: dict[str, Any]) -> dict:
    res = start(client, account, day["id"])
    assert res.status_code == 201, res.text
    return res.json()


def patch_set(client, account, session, set_id, **body):
    return client.patch(
        f"/sessions/{session['id']}/sets/{set_id}", headers=account.headers, json=body
    )


def do_sets(client, account, session, exercise_id, weight, reps) -> None:
    for s in session["sets"]:
        if s["exercise_id"] == exercise_id:
            res = patch_set(
                client,
                account,
                session,
                s["id"],
                weight_kg=weight,
                reps=reps,
                done=True,
            )
            assert res.status_code == 200, res.text


def finish(client, account, session) -> dict:
    res = client.post(f"/sessions/{session['id']}/finish", headers=account.headers)
    assert res.status_code == 200, res.text
    return res.json()


# --- sessions -------------------------------------------------------------------------


def test_start_pre_creates_the_planned_sets(client, user, program) -> None:
    day = program["days"][0]
    session = started(client, user, day)
    expected = sum(e["sets"] for e in day["exercises"])
    assert len(session["sets"]) == expected == session["sets_planned"]
    first = day["exercises"][0]
    planned = [s for s in session["sets"] if s["program_exercise_id"] == first["id"]]
    assert [s["set_number"] for s in planned] == list(range(1, first["sets"] + 1))
    assert all(s["reps"] is None and not s["done"] for s in planned)
    assert planned[0]["target_reps_min"] == first["rep_min"]
    assert planned[0]["target_reps_max"] == first["rep_max"]
    assert planned[0]["target_weight_kg"] is None  # no history yet
    # Sets come back in program order.
    order = [s["program_exercise_id"] for s in session["sets"]]
    ids = [e["id"] for e in day["exercises"]]
    assert sorted(dict.fromkeys(order), key=ids.index) == list(dict.fromkeys(order))
    assert session["performed_on"] == TODAY.isoformat()
    assert session["day_name"] == day["name"]


def test_only_one_open_session(client, user, program) -> None:
    day = program["days"][0]
    session = started(client, user, day)
    again = start(client, user, day["id"])
    assert again.status_code == 409
    assert again.json()["detail"]["session_id"] == session["id"]
    res = client.get("/sessions/open", headers=user.headers)
    assert res.json()["id"] == session["id"]


def test_day_must_be_in_the_active_program(client, user, program) -> None:
    res = client.post(
        "/programs/generate", headers=user.headers, json={"use_ai": False}
    )
    draft_day = res.json()["days"][0]["id"]
    assert start(client, user, draft_day).status_code == 404


def test_empty_session_and_extra_sets(client, user, library) -> None:
    res = start(client, user, None)
    assert res.status_code == 201
    session = res.json()
    assert session["sets"] == []
    bench = str(library["Barbell Bench Press - Medium Grip"].id)
    for _ in range(2):
        res = client.post(
            f"/sessions/{session['id']}/sets",
            headers=user.headers,
            json={"exercise_id": bench, "weight_kg": 60, "reps": 10, "done": True},
        )
        assert res.status_code == 201, res.text
    assert res.json()["set_number"] == 2
    bad = client.post(
        f"/sessions/{session['id']}/sets",
        headers=user.headers,
        json={"exercise_id": bench, "done": True},
    )
    assert bad.status_code == 422


def test_logging_a_set(client, user, program) -> None:
    session = started(client, user, program["days"][0])
    set_id = session["sets"][0]["id"]
    no_reps = patch_set(client, user, session, set_id, done=True)
    assert no_reps.status_code == 422
    res = patch_set(client, user, session, set_id, weight_kg=80, reps=8, rpe=8.5)
    assert res.status_code == 200
    assert res.json()["done"] is False
    res = patch_set(client, user, session, set_id, done=True)
    body = res.json()
    assert (body["weight_kg"], body["reps"], body["rpe"], body["done"]) == (
        80,
        8,
        8.5,
        True,
    )
    clear = patch_set(client, user, session, set_id, reps=None)
    assert clear.status_code == 422  # a done set keeps its reps
    out_of_range = patch_set(client, user, session, set_id, rpe=11)
    assert out_of_range.status_code == 422


def test_finish_reports_new_records_and_next_targets(client, user, program) -> None:
    day = program["days"][0]
    first = day["exercises"][0]["exercise_id"]

    one = started(client, user, day)
    do_sets(client, user, one, first, 80, 8)
    done = finish(client, user, one)
    assert done["new_records"] == []  # first time is a baseline
    assert done["ended_at"] is not None
    assert done["volume_kg"] == 80 * 8 * day["exercises"][0]["sets"]
    again = client.post(f"/sessions/{one['id']}/finish", headers=user.headers)
    assert again.status_code == 409

    two = started(client, user, day)
    planned = [s for s in two["sets"] if s["exercise_id"] == first]
    assert planned[0]["target_weight_kg"] == 80
    do_sets(client, user, two, first, 82.5, 8)
    assert finish(client, user, two)["new_records"] == [first]

    last = client.get(f"/exercises/{first}/last", headers=user.headers).json()
    assert last["last_date"] == TODAY.isoformat()
    assert {s["weight_kg"] for s in last["last_sets"]} == {82.5}
    assert last["personal_best"]["best_e1rm"]["weight_kg"] == 82.5
    assert last["personal_best"]["best_e1rm"]["estimated_1rm_kg"] == pytest.approx(
        104.5
    )


def test_last_for_a_never_done_exercise(client, user, library) -> None:
    ex = library["Barbell Curl"]
    res = client.get(f"/exercises/{ex.id}/last", headers=user.headers)
    assert res.status_code == 200
    assert res.json() == {
        "exercise_id": str(ex.id),
        "last_date": None,
        "last_sets": [],
        "personal_best": None,
    }


def test_history_and_delete(client, user, program) -> None:
    session = started(client, user, program["days"][0])
    finish(client, user, session)
    res = client.get("/sessions", headers=user.headers).json()
    assert res["total"] == 1
    assert res["items"][0]["id"] == session["id"]
    assert res["items"][0]["sets_done"] == 0
    res = client.delete(f"/sessions/{session['id']}", headers=user.headers)
    assert res.status_code == 204
    assert client.get("/sessions", headers=user.headers).json()["total"] == 0


def test_volume_counts_done_sets_per_muscle(client, user, program, db) -> None:
    day = program["days"][0]
    session = started(client, user, day)
    first = day["exercises"][0]
    do_sets(client, user, session, first["exercise_id"], 60, 10)
    finish(client, user, session)
    res = client.get("/stats/volume?weeks=2", headers=user.headers).json()
    assert len(res["weeks"]) == 2
    this_week = res["weeks"][0]
    assert (
        this_week["week_start"]
        == (TODAY - dt.timedelta(days=TODAY.weekday())).isoformat()
    )
    muscle = MUSCLE_GROUPS.get(first["muscle_primary"], first["muscle_primary"])
    assert this_week["sets_by_muscle"][muscle] == first["sets"]
    assert res["weeks"][1]["sets_by_muscle"] == {}
    assert (res["target_min"], res["target_max"]) == (10, 20)


def test_other_users_session_is_404(client, user, program, library) -> None:
    session = started(client, user, program["days"][0])
    other = onboard(client)
    set_id = session["sets"][0]["id"]
    for method, path, body in [
        ("GET", f"/sessions/{session['id']}", None),
        ("PATCH", f"/sessions/{session['id']}/sets/{set_id}", {"reps": 5}),
        ("POST", f"/sessions/{session['id']}/finish", None),
        ("DELETE", f"/sessions/{session['id']}", None),
    ]:
        res = client.request(method, path, headers=other.headers, json=body)
        assert res.status_code == 404, (method, path)
    # A set id from another session is not found either.
    mine = start(client, other, None).json()
    res = patch_set(client, other, mine, set_id, reps=5)
    assert res.status_code == 404


def test_training_needs_health_consent(client, library) -> None:
    account = register_and_login(client)
    assert client.get("/sessions", headers=account.headers).status_code == 403
    assert start(client, account, None).status_code == 403
    assert client.get("/stats/volume", headers=account.headers).status_code == 403


def test_program_session_is_blocked_when_screening_blocks(client, user, program):
    res = client.post(
        "/onboarding/screening", headers=user.headers, json=screen(chest_pain=True)
    )
    assert res.status_code == 201
    blocked = start(client, user, program["days"][0]["id"])
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "plan_access_denied"


def test_future_session_date_is_rejected(client, user) -> None:
    tomorrow = (TODAY + dt.timedelta(days=1)).isoformat()
    assert start(client, user, None, performed_on=tomorrow).status_code == 422


# --- nutrition ------------------------------------------------------------------------


def meal(**override: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "date": TODAY.isoformat(),
        "slot": "breakfast",
        "name": "Eggs and foul",
        "items": [{"name": "eggs", "grams": 120}, {"name": "foul"}],
        "kcal": 650,
        "protein_g": 38.5,
        "carbs_g": 60,
        "fat_g": 25,
    }
    body.update(override)
    return body


def test_targets_for_70kg_193cm_lean_bulk(client, user) -> None:
    res = client.get("/nutrition/targets", headers=user.headers)
    assert res.status_code == 200, res.text
    t = res.json()
    assert 2850 <= t["kcal"] <= 3150
    assert t["protein_g"] >= 140
    assert t["fat_g"] >= 56
    assert t["bmr_source"] == "formula"
    assert t["water_ml"] == 70 * 40


def test_targets_follow_goal_and_weight_changes(client, user) -> None:
    before = client.get("/nutrition/targets", headers=user.headers).json()
    res = client.put(
        "/onboarding/profile", headers=user.headers, json=profile_payload(goal="cut")
    )
    assert res.status_code == 200
    cut = client.get("/nutrition/targets", headers=user.headers).json()
    assert before["kcal"] - cut["kcal"] >= 550
    client.post("/weight", headers=user.headers, json={"weight_kg": 80})
    heavier = client.get("/nutrition/targets", headers=user.headers).json()
    assert heavier["kcal"] > cut["kcal"]
    assert heavier["protein_g"] > cut["protein_g"]


def test_confirmed_inbody_bmr_and_settings_are_used(client, user, db) -> None:
    base = client.get("/nutrition/targets", headers=user.headers).json()
    db.add(
        InbodyScan(
            user_id=user.id, taken_on=TODAY, bmr_kcal=1500, confirmed=False
        )  # a draft: ignored
    )
    db.flush()
    assert client.get("/nutrition/targets", headers=user.headers).json() == base
    db.add(
        InbodyScan(
            user_id=user.id,
            taken_on=TODAY,
            bmr_kcal=1650,
            confirmed=True,
            confirmed_at=dt.datetime.now(dt.UTC),
        )
    )
    db.add(
        NutritionSettings(
            user_id=user.id, calorie_adjustment_kcal=-100, protein_g_per_kg=Decimal(2)
        )
    )
    db.flush()
    t = client.get("/nutrition/targets", headers=user.headers).json()
    assert t["bmr_source"] == "inbody"
    assert t["bmr_kcal"] == 1650
    assert t["calorie_adjustment_kcal"] == -100
    assert t["protein_g"] == 140


def test_day_totals_exclude_drafts(client, user, db) -> None:
    res = client.post("/nutrition/meals", headers=user.headers, json=meal())
    assert res.status_code == 201, res.text
    assert res.json()["confirmed"] is True
    client.post(
        "/nutrition/meals",
        headers=user.headers,
        json=meal(slot="bed", name="Milk", kcal=150, protein_g=8, carbs_g=12, fat_g=8),
    )
    db.add(
        MealLog(
            user_id=user.id,
            date=TODAY,
            slot=MealSlot.LUNCH,
            source=MealSource.PHOTO,
            name="Koshary (AI draft)",
            kcal=900,
            protein_g=Decimal(25),
            confirmed=False,
        )
    )
    db.flush()
    client.put(
        f"/nutrition/days/{TODAY.isoformat()}/water",
        headers=user.headers,
        json={"ml": 1500},
    )

    day = client.get("/nutrition/day", headers=user.headers).json()
    assert [m["slot"] for m in day["meals"]] == ["breakfast", "lunch", "bed"]
    assert day["totals"]["kcal"] == 800
    assert day["totals"]["protein_g"] == 46.5
    assert day["totals"]["meals_counted"] == 2
    assert day["totals"]["drafts_excluded"] == 1
    assert day["water_ml"] == 1500
    assert day["remaining"]["kcal"] == day["targets"]["kcal"] - 800
    assert day["remaining"]["water_ml"] == day["targets"]["water_ml"] - 1500
    assert day["targets_withheld"] is False


def test_water_put_replaces_the_total(client, user) -> None:
    path = f"/nutrition/days/{TODAY.isoformat()}/water"
    client.put(path, headers=user.headers, json={"ml": 1000})
    client.put(path, headers=user.headers, json={"ml": 2250})
    day = client.get("/nutrition/day", headers=user.headers).json()
    assert day["water_ml"] == 2250
    client.put(path, headers=user.headers, json={"ml": 0})
    assert client.get("/nutrition/day", headers=user.headers).json()["water_ml"] == 0


def test_edit_and_delete_meal(client, user, db) -> None:
    created = client.post("/nutrition/meals", headers=user.headers, json=meal()).json()
    res = client.patch(
        f"/nutrition/meals/{created['id']}",
        headers=user.headers,
        json={"kcal": 700, "slot": "snack"},
    )
    assert res.status_code == 200
    assert (res.json()["kcal"], res.json()["slot"]) == (700, "snack")
    draft = MealLog(
        user_id=user.id,
        date=TODAY,
        slot=MealSlot.LUNCH,
        source=MealSource.PHOTO,
        name="Draft",
        kcal=500,
        confirmed=False,
    )
    db.add(draft)
    db.flush()
    edited = client.patch(
        f"/nutrition/meals/{draft.id}", headers=user.headers, json={"kcal": 450}
    ).json()
    assert edited["user_edited"] is True
    assert edited["confirmed"] is False  # editing never confirms an AI draft
    res = client.delete(f"/nutrition/meals/{created['id']}", headers=user.headers)
    assert res.status_code == 204
    assert (
        client.delete(
            f"/nutrition/meals/{created['id']}", headers=user.headers
        ).status_code
        == 404
    )


def test_meals_of_another_user_are_404(client, user, library) -> None:
    created = client.post("/nutrition/meals", headers=user.headers, json=meal()).json()
    other = onboard(client)
    path = f"/nutrition/meals/{created['id']}"
    assert (
        client.patch(path, headers=other.headers, json={"kcal": 1}).status_code == 404
    )
    assert client.delete(path, headers=other.headers).status_code == 404
    assert client.get("/nutrition/day", headers=other.headers).json()["meals"] == []


def test_meal_validation(client, user) -> None:
    future = (TODAY + dt.timedelta(days=1)).isoformat()
    for bad in [meal(kcal=-1), meal(slot="brunch"), meal(name=""), meal(date=future)]:
        res = client.post("/nutrition/meals", headers=user.headers, json=bad)
        assert res.status_code == 422, bad


def test_targets_withheld_for_disordered_eating_flag(client, library) -> None:
    account = onboard(client)
    res = client.post(
        "/onboarding/screening",
        headers=account.headers,
        json=screen(eating_concern=True),
    )
    assert res.status_code == 201
    targets = client.get("/nutrition/targets", headers=account.headers)
    assert targets.status_code == 403
    assert targets.json()["detail"]["code"] == "diet_targets_withheld"
    day = client.get("/nutrition/day", headers=account.headers)
    assert day.status_code == 200
    body = day.json()
    assert body["targets"] is None
    assert body["remaining"] is None
    assert body["targets_withheld"] is True
    assert "kcal" not in str(body["targets"])


def test_nutrition_needs_plan_access(client, library) -> None:
    account = register_and_login(client)
    grant_consents(client, account, "health_data")
    for path in ("/nutrition/targets", "/nutrition/day"):
        res = client.get(path, headers=account.headers)
        assert res.status_code == 403
        assert res.json()["detail"]["code"] == "plan_access_denied"


# --- body -----------------------------------------------------------------------------


def test_weight_upserts_per_day_and_trends(client, user, db) -> None:
    for offset, kg in [(13, 70.0), (10, 70.2), (6, 70.6), (3, 70.8), (0, 71.0)]:
        day = (TODAY - dt.timedelta(days=offset)).isoformat()
        res = client.post(
            "/weight", headers=user.headers, json={"weight_kg": kg, "date": day}
        )
        assert res.status_code == 200, res.text
    res = client.post("/weight", headers=user.headers, json={"weight_kg": 71.2})
    body = res.json()
    assert body["latest_kg"] == 71.2  # today's log was replaced
    assert [p["date"] for p in body["points"]][-1] == TODAY.isoformat()
    assert body["average_7d"] == pytest.approx((70.6 + 70.8 + 71.2) / 3, abs=0.01)
    assert body["weekly_change_kg"] == pytest.approx(
        (70.6 + 70.8 + 71.2) / 3 - 70.1, abs=0.01
    )
    count = db.scalar(
        sa.select(sa.func.count())
        .select_from(WeightLog)
        .where(WeightLog.user_id == user.id)
    )
    assert count == 5


def test_weight_with_a_single_point(client, user) -> None:
    body = client.get("/weight", headers=user.headers).json()
    assert len(body["points"]) == 1  # the onboarding weight
    assert body["average_7d"] == body["latest_kg"] == 70
    assert body["weekly_change_kg"] is None


def test_weight_validation(client, user) -> None:
    future = (TODAY + dt.timedelta(days=1)).isoformat()
    for bad in [{"weight_kg": 10}, {"weight_kg": 70, "date": future}]:
        assert client.post("/weight", headers=user.headers, json=bad).status_code == 422


def test_checkins_upsert_and_list(client, user, caplog) -> None:
    secret = "sentinel-checkin-note-4711"
    with caplog.at_level(logging.DEBUG):
        res = client.post(
            "/checkins",
            headers=user.headers,
            json={"sleep_h": 7.5, "soreness": 2, "energy": 4, "notes": secret},
        )
    assert res.status_code == 200, res.text
    assert res.json()["notes"] == secret
    assert secret not in caplog.text
    res = client.post("/checkins", headers=user.headers, json={"stress": 3})
    body = res.json()
    assert (body["stress"], body["sleep_h"], body["notes"]) == (3, None, None)
    yesterday = (TODAY - dt.timedelta(days=1)).isoformat()
    client.post(
        "/checkins", headers=user.headers, json={"date": yesterday, "energy": 5}
    )
    items = client.get("/checkins", headers=user.headers).json()
    assert [c["date"] for c in items] == [TODAY.isoformat(), yesterday]
    bad = client.post("/checkins", headers=user.headers, json={"soreness": 6})
    assert bad.status_code == 422


def test_body_needs_health_consent(client) -> None:
    account = register_and_login(client)
    for method, path in [("GET", "/weight"), ("GET", "/checkins")]:
        assert client.request(method, path, headers=account.headers).status_code == 403
    res = client.post("/weight", headers=account.headers, json={"weight_kg": 70})
    assert res.status_code == 403


def test_account_deletion_removes_tracking_data(client, user, program, db) -> None:
    session = started(client, user, program["days"][0])
    client.post("/nutrition/meals", headers=user.headers, json=meal())
    client.post("/checkins", headers=user.headers, json={"energy": 3})
    assert delete_me(client, user).status_code == 204
    left = db.scalar(
        sa.select(sa.func.count())
        .select_from(SetLog)
        .where(SetLog.workout_session_id == session["id"])
    )
    assert left == 0
    meals = sa.select(sa.func.count()).select_from(MealLog)
    assert db.scalar(meals.where(MealLog.user_id == user.id)) == 0
