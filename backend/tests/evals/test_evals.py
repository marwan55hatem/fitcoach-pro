"""Offline coach evals: scripted scenarios (tests/evals/scenarios.json) run through the
real chat endpoint with a scripted model. No real AI is called.

Run them with pytest, or `python -m tests.evals.run` for a pass/fail report.
"""

import json
import re
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.ai.client import AiClient, get_ai_client
from app.ai.coach import MAX_ITERATIONS
from app.ai.types import ToolCall, ToolResultMessage
from app.core.config import Settings
from app.db.session import get_db, get_session_factory
from app.main import create_app
from app.models import CoachNote, NutritionSettings, Recommendation
from app.services.storage import Storage, get_storage
from tests.fakes.ai import FakeProvider
from tests.helpers import grant_consents, register_and_login
from tests.test_onboarding import profile_payload, screen

SCENARIOS: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "scenarios.json").read_text(encoding="utf-8")
)


@pytest.fixture
def fake() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def client(db: Session, storage: Storage, fake: FakeProvider) -> Iterator[TestClient]:
    app = create_app()
    ai = AiClient(
        fake, Settings(_env_file=None), lambda: nullcontext(db), sleep=lambda _s: None
    )
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_ai_client] = lambda: ai
    app.dependency_overrides[get_session_factory] = lambda: lambda: nullcontext(db)
    with TestClient(app) as test_client:
        yield test_client


def _script(fake: FakeProvider, model: Any) -> None:
    if model == "repeat_tools":
        for i in range(MAX_ITERATIONS):
            fake.queue_stream([], tool_calls=[ToolCall(f"c{i}", "get_checkins", {})])
        return
    for n, turn in enumerate(model or []):
        calls = [
            ToolCall(f"t{n}{i}", c["name"], c.get("arguments", {}))
            for i, c in enumerate(turn.get("tool_calls", []))
        ]
        fake.queue_stream([turn["text"]] if turn.get("text") else [], tool_calls=calls)


def _events(text: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_scenario(
    scenario: dict[str, Any], client: TestClient, db: Session, fake: FakeProvider
) -> None:
    account = register_and_login(client)
    grant_consents(client, account, "health_data", "ai_processing")
    profile = profile_payload(
        language=scenario.get("language", "en"), **scenario.get("profile", {})
    )
    assert (
        client.put(
            "/onboarding/profile", headers=account.headers, json=profile
        ).status_code
        == 200
    )
    res = client.post(
        "/onboarding/screening",
        headers=account.headers,
        json=screen(**scenario.get("screening", {})),
    )
    assert res.status_code == 201
    for note in scenario.get("seed_notes", []):
        db.add(CoachNote(user_id=account.id, source="user", **note))
    db.commit()
    _script(fake, scenario.get("model"))

    res = client.post(
        "/coach/chat", headers=account.headers, json={"text": scenario["input"]}
    )
    assert res.status_code == 200, res.text
    events = _events(res.text)
    reply = "".join(d["text"] for e, d in events if e == "delta")
    expect = scenario["expect"]

    if "red_flag" in expect:
        assert events[-1] == (
            "done",
            {"message_id": events[-1][1]["message_id"], "red_flag": expect["red_flag"]},
        )
    if "model_called" in expect:
        assert bool(fake.calls) is expect["model_called"]
    if "model_calls" in expect:
        assert len(fake.calls) == expect["model_calls"]
    for text in expect.get("reply_contains", []):
        assert text in reply, (text, reply)
    if "reply_not_matches" in expect:
        assert not re.search(expect["reply_not_matches"], reply)
    for text in expect.get("system_contains", []):
        assert text in fake.calls[0].system
    if "tools_called" in expect:
        assert [d["name"] for e, d in events if e == "tool"] == expect["tools_called"]
    if "proposals" in expect:
        assert len([1 for e, _ in events if e == "proposal"]) == expect["proposals"]
        assert (
            db.scalar(
                sa.select(sa.func.count())
                .select_from(Recommendation)
                .where(Recommendation.user_id == account.id)
            )
            == expect["proposals"]
        )

    results: dict[str, str] = {}
    calls_by_id: dict[str, str] = {}
    for call in fake.calls:
        for message in call.messages:
            for tool_call in getattr(message, "tool_calls", ()):
                calls_by_id[tool_call.id] = tool_call.name
            if isinstance(message, ToolResultMessage):
                results[calls_by_id.get(message.tool_call_id, "")] = message.content
    for name, text in expect.get("tool_results_contain", {}).items():
        assert text in results.get(name, ""), (name, results.get(name))
    if expect.get("tool_results_fenced"):
        assert all(
            r.startswith("<untrusted_data") and r.count("</untrusted_data>") == 1
            for r in results.values()
        )
    if "notes_saved" in expect:
        saved = db.scalar(
            sa.select(sa.func.count())
            .select_from(CoachNote)
            .where(CoachNote.user_id == account.id)
        )
        assert saved == expect["notes_saved"]
    if expect.get("settings_unchanged"):
        assert (
            db.scalar(
                sa.select(NutritionSettings).where(
                    NutritionSettings.user_id == account.id
                )
            )
            is None
        )


def test_there_are_enough_scenarios() -> None:
    assert len(SCENARIOS) >= 15
    assert len({s["id"] for s in SCENARIOS}) == len(SCENARIOS)
