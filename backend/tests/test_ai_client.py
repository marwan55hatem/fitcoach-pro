import datetime as dt
import logging
import uuid
from contextlib import nullcontext
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ai import client as client_module
from app.ai.client import AiClient, get_ai_client
from app.ai.errors import (
    AiBudgetExceededError,
    AiConnectionError,
    AiError,
    AiNotConfiguredError,
    AiOutputError,
    AiProviderError,
    AiRateLimitError,
    AiRefusalError,
    AiServerError,
    AiTimeoutError,
)
from app.ai.types import (
    AiImage,
    AssistantMessage,
    StreamDone,
    Task,
    TextDelta,
    ToolCall,
    ToolResultMessage,
    ToolSpec,
    Usage,
    UserMessage,
)
from app.core.config import Settings, get_settings
from app.main import create_app
from app.models import AiUsage, Profile, User
from tests.fakes.ai import FakeProvider

NOON = dt.datetime(2026, 10, 28, 12, 0, tzinfo=dt.UTC)


class Answer(BaseModel):
    value: int


def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def make_user(db: Session) -> User:
    user = User(email=f"{uuid.uuid4().hex[:8]}@example.com", password_hash="x")
    db.add(user)
    db.flush()
    return user


def make_client(
    db: Session,
    fake: FakeProvider | None,
    *,
    clock=lambda: NOON,
    sleeps: list[float] | None = None,
    **settings,
) -> AiClient:
    return AiClient(
        fake,
        make_settings(**settings),
        lambda: nullcontext(db),  # tests share the rolled-back session
        sleep=(sleeps.append if sleeps is not None else (lambda _s: None)),
        clock=clock,
    )


def usage_rows(db: Session, user: User) -> list[AiUsage]:
    return list(db.scalars(sa.select(AiUsage).where(AiUsage.user_id == user.id)))


ASK = [UserMessage("what is the answer?")]


# --- structured output ------------------------------------------------------------------


def test_structured_returns_the_validated_answer_and_records_usage(db: Session) -> None:
    user, fake = make_user(db), FakeProvider()
    ai = make_client(db, fake)
    fake.queue_structured(Answer(value=42), usage=Usage(1000, 200))

    result = ai.structured(
        user_id=user.id, task=Task.MAIN, schema=Answer, system="sys", messages=ASK
    )
    assert result == Answer(value=42)

    call = fake.calls[0]
    assert (call.kind, call.model, call.system, call.schema) == (
        "structured",
        "gpt-6.1-sol",
        "sys",
        Answer,
    )
    assert call.messages == ASK
    # The provider only ever sees a pseudonymous hash, never the user id.
    assert (
        call.user_ref and len(call.user_ref) == 32 and str(user.id) not in call.user_ref
    )

    (row,) = usage_rows(db, user)
    assert (row.tokens_in, row.tokens_out) == (1000, 200)
    assert float(row.cost_estimate) == pytest.approx(0.004)  # $2/M in + $10/M out


def test_usage_accumulates_in_one_row_per_user_per_day(db: Session) -> None:
    user, fake = make_user(db), FakeProvider()
    ai = make_client(db, fake)
    fake.queue_structured(Answer(value=1), usage=Usage(1000, 200))
    fake.queue_structured(Answer(value=2), usage=Usage(500, 100))
    for _ in range(2):
        ai.structured(
            user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
        )

    (row,) = usage_rows(db, user)
    assert (row.tokens_in, row.tokens_out) == (1500, 300)
    assert float(row.cost_estimate) == pytest.approx(0.006)
    assert ai.tokens_used_today(user.id) == 1800


def test_one_repair_retry_with_feedback_then_success(db: Session) -> None:
    user, fake = make_user(db), FakeProvider()
    ai = make_client(db, fake)
    fake.queue_structured(error="value: field required", usage=Usage(100, 10))
    fake.queue_structured(Answer(value=7), usage=Usage(150, 12))

    assert (
        ai.structured(
            user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
        ).value
        == 7
    )
    assert len(fake.calls) == 2
    assert fake.calls[0].messages == ASK
    repair = fake.calls[1].messages
    assert repair[:-1] == ASK  # original conversation preserved...
    assert (
        isinstance(repair[-1], UserMessage)
        and "value: field required" in repair[-1].text
    )
    (row,) = usage_rows(db, user)
    assert (row.tokens_in, row.tokens_out) == (
        250,
        22,
    )  # both attempts are billed and recorded


def test_two_invalid_answers_raise_after_exactly_one_retry(db: Session) -> None:
    user, fake = make_user(db), FakeProvider()
    ai = make_client(db, fake)
    fake.queue_structured(error="bad one")
    fake.queue_structured(error="bad two")
    with pytest.raises(AiOutputError):
        ai.structured(
            user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
        )
    assert len(fake.calls) == 2
    assert usage_rows(db, user)[0].tokens_in == 200


def test_repair_message_is_capped(db: Session) -> None:
    user, fake = make_user(db), FakeProvider()
    ai = make_client(db, fake)
    fake.queue_structured(error="x" * 5000)
    fake.queue_structured(Answer(value=1))
    ai.structured(
        user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
    )
    assert len(fake.calls[1].messages[-1].text) < 800


def test_refusal_is_not_retried(db: Session) -> None:
    user, fake = make_user(db), FakeProvider()
    ai = make_client(db, fake)
    fake.queue_structured(refusal="I can't help with that", usage=Usage(80, 5))
    with pytest.raises(AiRefusalError):
        ai.structured(
            user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
        )
    assert len(fake.calls) == 1
    assert usage_rows(db, user)[0].tokens_in == 80


# --- retries ----------------------------------------------------------------------------


def test_transient_errors_are_retried_with_growing_backoff(db: Session) -> None:
    user, fake, sleeps = make_user(db), FakeProvider(), []
    ai = make_client(db, fake, sleeps=sleeps)
    fake.queue_error(AiTimeoutError())
    fake.queue_error(AiRateLimitError())
    fake.queue_structured(Answer(value=5))

    result = ai.structured(
        user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
    )
    assert result.value == 5 and len(fake.calls) == 3
    assert len(sleeps) == 2 and 0.5 <= sleeps[0] < 0.8 and 1.0 <= sleeps[1] < 1.3
    assert len(usage_rows(db, user)) == 1  # only the successful call used tokens


@pytest.mark.parametrize(
    "error", [AiTimeoutError, AiConnectionError, AiRateLimitError, AiServerError]
)
def test_retries_give_up_after_the_limit(db: Session, error: type[AiError]) -> None:
    user, fake, sleeps = make_user(db), FakeProvider(), []
    ai = make_client(db, fake, sleeps=sleeps, ai_max_retries=2)
    for _ in range(5):
        fake.queue_error(error())
    with pytest.raises(error):
        ai.structured(
            user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
        )
    assert len(fake.calls) == 3 and len(sleeps) == 2  # 1 try + 2 retries
    assert usage_rows(db, user) == []  # nothing was spent


def test_non_transient_errors_are_not_retried(db: Session) -> None:
    user, fake, sleeps = make_user(db), FakeProvider(), []
    ai = make_client(db, fake, sleeps=sleeps)
    fake.queue_error(AiProviderError())
    with pytest.raises(AiProviderError):
        ai.chat(user_id=user.id, task=Task.MAIN, system="s", messages=ASK)
    assert len(fake.calls) == 1 and sleeps == []


# --- budget -----------------------------------------------------------------------------


def test_budget_blocks_before_calling_the_provider(db: Session) -> None:
    user, fake = make_user(db), FakeProvider()
    ai = make_client(db, fake, ai_daily_token_budget=1000)
    fake.queue_structured(Answer(value=1), usage=Usage(900, 100))  # exactly the budget
    ai.structured(
        user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
    )

    with pytest.raises(AiBudgetExceededError):
        ai.structured(
            user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
        )
    with pytest.raises(AiBudgetExceededError):
        ai.chat(user_id=user.id, task=Task.FAST, system="s", messages=ASK)
    assert len(fake.calls) == 1  # the provider was never called again


def test_budget_allows_calls_while_under_the_limit(db: Session) -> None:
    user, fake = make_user(db), FakeProvider()
    ai = make_client(db, fake, ai_daily_token_budget=1000)
    fake.queue_structured(Answer(value=1), usage=Usage(500, 499))  # 999 used
    fake.queue_structured(Answer(value=2), usage=Usage(5000, 0))  # may overshoot once
    ai.structured(
        user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
    )
    ai.structured(
        user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
    )
    with pytest.raises(AiBudgetExceededError):
        ai.structured(
            user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
        )


def test_budget_is_per_user(db: Session) -> None:
    alice, bob, fake = make_user(db), make_user(db), FakeProvider()
    ai = make_client(db, fake, ai_daily_token_budget=100)
    fake.queue_structured(Answer(value=1), usage=Usage(100, 0))
    fake.queue_structured(Answer(value=2))
    ai.structured(
        user_id=alice.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
    )
    with pytest.raises(AiBudgetExceededError):
        ai.structured(
            user_id=alice.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
        )
    assert (
        ai.structured(
            user_id=bob.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
        ).value
        == 2
    )


def test_budget_resets_the_next_day(db: Session) -> None:
    user, fake = make_user(db), FakeProvider()
    now = [NOON]
    ai = make_client(db, fake, clock=lambda: now[0], ai_daily_token_budget=100)
    fake.queue_structured(Answer(value=1), usage=Usage(100, 0))
    fake.queue_structured(Answer(value=2))
    ai.structured(
        user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
    )
    with pytest.raises(AiBudgetExceededError):
        ai.structured(
            user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
        )

    now[0] = NOON + dt.timedelta(days=1)
    assert ai.tokens_used_today(user.id) == 0
    ai.structured(
        user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
    )
    assert len(usage_rows(db, user)) == 2  # one row per day


def test_the_usage_day_follows_the_users_timezone(db: Session) -> None:
    user, fake = make_user(db), FakeProvider()
    db.add(Profile(user_id=user.id, timezone="Pacific/Kiritimati"))  # UTC+14
    db.flush()
    late_utc = dt.datetime(
        2026, 10, 28, 20, 0, tzinfo=dt.UTC
    )  # already the 29th in Kiritimati
    ai = make_client(db, fake, clock=lambda: late_utc)
    fake.queue_structured(Answer(value=1))
    ai.structured(
        user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
    )
    assert usage_rows(db, user)[0].date == dt.date(2026, 10, 29)


# --- configuration ----------------------------------------------------------------------


def test_missing_provider_is_a_clear_error_and_records_nothing(db: Session) -> None:
    user = make_user(db)
    ai = make_client(db, None)
    with pytest.raises(AiNotConfiguredError):
        ai.structured(
            user_id=user.id, task=Task.MAIN, schema=Answer, system="s", messages=ASK
        )
    with pytest.raises(AiNotConfiguredError):
        ai.chat(user_id=user.id, task=Task.MAIN, system="s", messages=ASK)
    with pytest.raises(AiNotConfiguredError):
        list(ai.stream_chat(user_id=user.id, task=Task.MAIN, system="s", messages=ASK))
    assert usage_rows(db, user) == []


def test_model_per_task_defaults_and_overrides(db: Session) -> None:
    ai = make_client(db, FakeProvider(), ai_model_vision="custom-vision")
    assert ai.model_for(Task.MAIN) == "gpt-6.1-sol"
    assert ai.model_for(Task.FAST) == "gpt-6-luna"
    assert ai.model_for(Task.VISION) == "custom-vision"


def test_tasks_use_their_own_models_and_unknown_prices_cost_zero(db: Session) -> None:
    user, fake = make_user(db), FakeProvider()
    ai = make_client(db, fake, ai_model_fast="mystery-model")
    fake.queue_chat("hi", usage=Usage(10, 5))
    fake.queue_structured(Answer(value=1), usage=Usage(100, 20))
    ai.chat(user_id=user.id, task=Task.FAST, system="s", messages=ASK)
    ai.structured(
        user_id=user.id, task=Task.VISION, schema=Answer, system="s", messages=ASK
    )
    assert [c.model for c in fake.calls] == ["mystery-model", "gpt-6.1-sol"]
    (row,) = usage_rows(db, user)
    assert row.tokens_in == 110
    assert float(row.cost_estimate) == pytest.approx(
        (2 * 100 + 10 * 20) / 1e6
    )  # unknown model: $0


def test_get_ai_client_without_a_key_has_no_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "openai_api_key", "")
    get_ai_client.cache_clear()
    try:
        assert get_ai_client()._provider is None
        monkeypatch.setattr(get_settings(), "openai_api_key", "sk-test-not-real")
        get_ai_client.cache_clear()
        provider = get_ai_client()._provider
        assert provider is not None and provider.name == "openai"
    finally:
        get_ai_client.cache_clear()


# --- chat and streaming -----------------------------------------------------------------


def test_chat_passes_tools_and_returns_calls(db: Session) -> None:
    user, fake = make_user(db), FakeProvider()
    ai = make_client(db, fake)
    tool = ToolSpec("get_weight", "Latest weight", {"type": "object", "properties": {}})
    call = ToolCall("call_1", "get_weight", {})
    fake.queue_chat("", tool_calls=[call], usage=Usage(300, 40))
    fake.queue_chat("You weigh 70 kg.", usage=Usage(350, 12))

    first = ai.chat(
        user_id=user.id, task=Task.MAIN, system="coach", messages=ASK, tools=[tool]
    )
    assert first.tool_calls == (call,) and first.provider_items

    history = [
        *ASK,
        AssistantMessage(first.text, first.tool_calls, first.provider_items),
        ToolResultMessage("call_1", "70"),
    ]
    second = ai.chat(
        user_id=user.id, task=Task.MAIN, system="coach", messages=history, tools=[tool]
    )
    assert second.text == "You weigh 70 kg."
    assert fake.calls[0].tools == [tool] and fake.calls[1].messages[
        -1
    ] == ToolResultMessage("call_1", "70")
    assert usage_rows(db, user)[0].tokens_in == 650


def test_stream_yields_deltas_then_done_and_records_usage_once(db: Session) -> None:
    user, fake = make_user(db), FakeProvider()
    ai = make_client(db, fake)
    fake.queue_stream(["Hel", "lo ", "there"], usage=Usage(200, 30))

    events = list(
        ai.stream_chat(user_id=user.id, task=Task.MAIN, system="s", messages=ASK)
    )
    assert [e.text for e in events if isinstance(e, TextDelta)] == [
        "Hel",
        "lo ",
        "there",
    ]
    assert (
        isinstance(events[-1], StreamDone) and events[-1].result.text == "Hello there"
    )
    (row,) = usage_rows(db, user)
    assert (row.tokens_in, row.tokens_out) == (200, 30)


def test_stream_checks_the_budget_before_starting(db: Session) -> None:
    user, fake = make_user(db), FakeProvider()
    ai = make_client(db, fake, ai_daily_token_budget=10)
    fake.queue_chat("x", usage=Usage(10, 0))
    ai.chat(user_id=user.id, task=Task.MAIN, system="s", messages=ASK)
    with pytest.raises(AiBudgetExceededError):
        list(ai.stream_chat(user_id=user.id, task=Task.MAIN, system="s", messages=ASK))
    assert len(fake.calls) == 1


# --- HTTP error mapping -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (AiNotConfiguredError, 503, "ai_not_configured"),
        (AiBudgetExceededError, 429, "ai_budget_exceeded"),
        (AiRefusalError, 422, "ai_refused"),
        (AiOutputError, 502, "ai_bad_output"),
        (AiTimeoutError, 504, "ai_timeout"),
        (AiConnectionError, 503, "ai_unreachable"),
        (AiRateLimitError, 503, "ai_busy"),
        (AiServerError, 502, "ai_server_error"),
        (AiProviderError, 502, "ai_provider_error"),
    ],
)
def test_ai_errors_become_clean_json_without_internal_detail(
    error: type[AiError], status: int, code: str
) -> None:
    app: FastAPI = create_app()

    @app.get("/_test/ai")
    def boom() -> None:
        raise error("SENTINEL-internal-provider-detail sk-secret prompt text")

    res = TestClient(app).get("/_test/ai")
    assert res.status_code == status
    detail = res.json()["detail"]
    assert detail["code"] == code and detail["message"]
    assert "SENTINEL" not in res.text and "sk-secret" not in res.text


# --- log hygiene ------------------------------------------------------------------------

SENTINEL = "SENTINEL-health-value-do-not-log-7f3a"


def test_nothing_sensitive_is_ever_logged(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    user, fake = make_user(db), FakeProvider()
    ai = make_client(db, fake)
    image = AiImage(f"{SENTINEL}-image-bytes", "image/jpeg")
    messages = [UserMessage(f"note: {SENTINEL}", (image,))]
    tool = ToolSpec("lookup", f"desc {SENTINEL}", {"type": "object"})

    fake.queue_structured(
        error=f"bad output echoing {SENTINEL}"
    )  # invalid -> repair path
    fake.queue_structured(Answer(value=1))
    fake.queue_chat("", tool_calls=[ToolCall("c1", "lookup", {"q": SENTINEL})])
    fake.queue_stream([f"reply {SENTINEL}"])
    fake.queue_error(AiProviderError(f"provider said: {SENTINEL}"))
    fake.queue_error(AiTimeoutError(f"slow {SENTINEL}"))
    fake.queue_error(AiTimeoutError(f"slow {SENTINEL}"))
    fake.queue_error(AiTimeoutError(f"slow {SENTINEL}"))

    ai.structured(
        user_id=user.id,
        task=Task.VISION,
        schema=Answer,
        system=f"sys {SENTINEL}",
        messages=messages,
    )
    ai.chat(
        user_id=user.id,
        task=Task.MAIN,
        system=SENTINEL,
        messages=messages,
        tools=[tool],
    )
    list(
        ai.stream_chat(
            user_id=user.id, task=Task.MAIN, system=SENTINEL, messages=messages
        )
    )
    with pytest.raises(AiProviderError):
        ai.chat(user_id=user.id, task=Task.MAIN, system=SENTINEL, messages=messages)
    with pytest.raises(AiTimeoutError):  # retried twice, then raised
        ai.chat(user_id=user.id, task=Task.MAIN, system=SENTINEL, messages=messages)

    assert SENTINEL not in caplog.text
    # ...while the useful metadata is there.
    assert "ai_call task=vision" in caplog.text and "request_id=req_fake" in caplog.text
    assert "ai_retry" in caplog.text and "ai_invalid_output" in caplog.text
    assert "gpt-6.1-sol" in caplog.text


def test_client_module_never_logs_with_f_strings_of_content() -> None:
    """Guard rail: the logger calls in client.py only format metadata arguments."""
    text = Path(client_module.__file__).read_text(encoding="utf-8")
    assert 'logger.info(f"' not in text and 'logger.warning(f"' not in text
