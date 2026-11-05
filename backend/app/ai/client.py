"""The single entry point for every AI call in the app.

Features never talk to a vendor SDK. They call AiClient, which owns:
  - the per-user daily token budget (checked before every call),
  - usage and cost recording in ai_usage (own transaction, so it survives caller errors),
  - retries with backoff for transient failures,
  - validation of structured output, with one repair retry,
  - log hygiene: only task, model, token counts, latency and request id are ever logged.
    Prompts, images, answers, tool arguments and validation errors are never logged.
"""

import datetime as dt
import hashlib
import itertools
import logging
import random
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager
from decimal import Decimal
from functools import lru_cache
from typing import TypeVar
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.ai.errors import (
    AiBudgetExceededError,
    AiError,
    AiNotConfiguredError,
    AiOutputError,
    AiRefusalError,
)
from app.ai.models import DEFAULT_MODELS, estimate_cost
from app.ai.providers.base import Effort, Provider
from app.ai.types import (
    ChatResult,
    Messages,
    RawStructured,
    StreamDone,
    StreamEvent,
    Task,
    ToolSpec,
    Usage,
    UserMessage,
)
from app.core.config import Settings, get_settings
from app.models import AiUsage, Profile

logger = logging.getLogger("app.ai")

T = TypeVar("T", bound=BaseModel)
SessionFactory = Callable[[], AbstractContextManager[Session]]

DEFAULT_TZ = "Africa/Cairo"
BACKOFF_BASE_S = 0.5
REPAIR_PROMPT = (
    "Your previous answer did not match the required format ({problem}). "
    "Answer again, matching the required schema exactly."
)


def _user_ref(user_id: uuid.UUID) -> str:
    """Pseudonymous id sent to the provider for abuse monitoring: a hash, not the user id."""
    return hashlib.sha256(f"fitcoach:{user_id}".encode()).hexdigest()[:32]


class AiClient:
    def __init__(
        self,
        provider: Provider | None,
        settings: Settings,
        session_factory: SessionFactory,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._session_factory = session_factory
        self._sleep = sleep
        self._clock = clock

    # --- configuration ----------------------------------------------------------------

    def model_for(self, task: Task) -> str:
        configured = {
            Task.MAIN: self._settings.ai_model_main,
            Task.VISION: self._settings.ai_model_vision,
            Task.FAST: self._settings.ai_model_fast,
        }[task]
        return configured or DEFAULT_MODELS[self._settings.ai_provider][task]

    def _require_provider(self) -> Provider:
        if self._provider is None:
            raise AiNotConfiguredError("no API key configured")
        return self._provider

    # --- budget and usage (own sessions, own transactions) ----------------------------

    def _usage_date(self, db: Session, user_id: uuid.UUID) -> dt.date:
        tz_name = db.scalar(
            sa.select(Profile.timezone).where(Profile.user_id == user_id)
        )
        return self._clock().astimezone(ZoneInfo(tz_name or DEFAULT_TZ)).date()

    def tokens_used_today(self, user_id: uuid.UUID) -> int:
        with self._session_factory() as db:
            used = db.scalar(
                sa.select(AiUsage.tokens_in + AiUsage.tokens_out).where(
                    AiUsage.user_id == user_id,
                    AiUsage.date == self._usage_date(db, user_id),
                )
            )
        return int(used or 0)

    def ensure_available(self, user_id: uuid.UUID) -> None:
        """Fail early, before a response starts streaming: no key, or budget used up."""
        self._require_provider()
        self._check_budget(user_id)

    def _check_budget(self, user_id: uuid.UUID) -> None:
        if self.tokens_used_today(user_id) >= self._settings.ai_daily_token_budget:
            raise AiBudgetExceededError("daily token budget used up")

    def _record(
        self,
        user_id: uuid.UUID,
        task: Task,
        model: str,
        usage: Usage,
        started: float,
        request_id: str | None,
    ) -> None:
        cost = estimate_cost(model, usage, self._settings.ai_price_overrides)
        with self._session_factory() as db:
            today = self._usage_date(db, user_id)
            insert = pg_insert(AiUsage).values(
                user_id=user_id,
                date=today,
                tokens_in=usage.input_tokens,
                tokens_out=usage.output_tokens,
                cost_estimate=cost or Decimal(0),
            )
            db.execute(
                insert.on_conflict_do_update(
                    index_elements=["user_id", "date"],
                    set_={
                        "tokens_in": AiUsage.tokens_in + insert.excluded.tokens_in,
                        "tokens_out": AiUsage.tokens_out + insert.excluded.tokens_out,
                        "cost_estimate": AiUsage.cost_estimate
                        + insert.excluded.cost_estimate,
                        "updated_at": sa.func.now(),
                    },
                )
            )
            db.commit()
        logger.info(
            "ai_call task=%s provider=%s model=%s tokens_in=%d tokens_out=%d cached=%d "
            "cost_usd=%s latency_ms=%d request_id=%s",
            task.value,
            getattr(self._provider, "name", "none"),
            model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cached_tokens,
            "unknown" if cost is None else cost,
            int((time.monotonic() - started) * 1000),
            request_id or "-",
        )

    # --- retries ----------------------------------------------------------------------

    def _with_retries(self, call: Callable[[], T]) -> T:
        attempts = self._settings.ai_max_retries + 1
        for attempt in range(attempts):
            try:
                return call()
            except AiError as exc:
                if not exc.transient or attempt == attempts - 1:
                    raise
                logger.warning(
                    "ai_retry error=%s attempt=%d", type(exc).__name__, attempt + 1
                )
                self._sleep(BACKOFF_BASE_S * (2**attempt) + random.uniform(0, 0.25))
        raise AssertionError("unreachable")

    # --- public API -------------------------------------------------------------------

    def structured(
        self,
        *,
        user_id: uuid.UUID,
        task: Task,
        schema: type[T],
        system: str,
        messages: Messages,
        max_output_tokens: int = 2000,
        reasoning_effort: Effort | None = None,
    ) -> T:
        """A validated answer in `schema`. One repair retry if the first answer is invalid."""
        provider = self._require_provider()
        model = self.model_for(task)
        problem = "unusable output"
        for attempt in (1, 2):
            # A repair attempt resends the original messages plus one corrective message.
            sent: list = list(messages)
            if attempt == 2:
                sent.append(UserMessage(REPAIR_PROMPT.format(problem=problem[:500])))
            self._check_budget(user_id)
            started = time.monotonic()

            def call(sent: list = sent) -> RawStructured:
                return provider.structured(
                    model=model,
                    system=system,
                    messages=sent,
                    schema=schema,
                    max_output_tokens=max_output_tokens,
                    reasoning_effort=reasoning_effort,
                    user_ref=_user_ref(user_id),
                )

            raw = self._with_retries(call)
            self._record(user_id, task, model, raw.usage, started, raw.request_id)
            if raw.refusal is not None:
                raise AiRefusalError("model refused")
            if raw.parsed is not None:
                assert isinstance(raw.parsed, schema)
                return raw.parsed
            problem = raw.error or problem
            logger.warning("ai_invalid_output task=%s attempt=%d", task.value, attempt)
        raise AiOutputError("invalid output after one repair attempt")

    def chat(
        self,
        *,
        user_id: uuid.UUID,
        task: Task,
        system: str,
        messages: Messages,
        tools: Sequence[ToolSpec] = (),
        max_output_tokens: int = 2000,
        reasoning_effort: Effort | None = None,
    ) -> ChatResult:
        """One model turn, possibly requesting tool calls (the caller runs the loop)."""
        provider = self._require_provider()
        model = self.model_for(task)
        self._check_budget(user_id)
        started = time.monotonic()
        result = self._with_retries(
            lambda: provider.chat(
                model=model,
                system=system,
                messages=messages,
                tools=tools,
                max_output_tokens=max_output_tokens,
                reasoning_effort=reasoning_effort,
                user_ref=_user_ref(user_id),
            )
        )
        self._record(user_id, task, model, result.usage, started, result.request_id)
        if result.refusal is not None and not result.text and not result.tool_calls:
            raise AiRefusalError("model refused")
        return result

    def stream_chat(
        self,
        *,
        user_id: uuid.UUID,
        task: Task,
        system: str,
        messages: Messages,
        tools: Sequence[ToolSpec] = (),
        max_output_tokens: int = 2000,
        reasoning_effort: Effort | None = None,
    ) -> Iterator[StreamEvent]:
        """Stream text deltas, then a final StreamDone. Usage is recorded when it finishes.

        A transient failure BEFORE the first event (a busy provider, a timeout) is
        retried like any call. Once anything has been yielded it is never retried:
        restarting would repeat words the user has already seen.
        """
        provider = self._require_provider()
        model = self.model_for(task)
        self._check_budget(user_id)
        started = time.monotonic()

        def open_stream() -> tuple[StreamEvent, Iterator[StreamEvent]]:
            stream = iter(
                provider.stream_chat(
                    model=model,
                    system=system,
                    messages=messages,
                    tools=tools,
                    max_output_tokens=max_output_tokens,
                    reasoning_effort=reasoning_effort,
                    user_ref=_user_ref(user_id),
                )
            )
            return next(stream), stream

        first, rest = self._with_retries(open_stream)
        for event in itertools.chain([first], rest):
            if isinstance(event, StreamDone):
                self._record(
                    user_id,
                    task,
                    model,
                    event.result.usage,
                    started,
                    event.result.request_id,
                )
            yield event


# --- wiring -----------------------------------------------------------------------------


@lru_cache
def get_ai_client() -> AiClient:
    """FastAPI dependency / app-wide instance. Built lazily so tests and tooling can import
    the app without an API key; a missing key surfaces as AiNotConfiguredError per call."""
    from app.ai.providers.factory import build_provider
    from app.db.session import SessionLocal

    settings = get_settings()
    return AiClient(build_provider(settings), settings, SessionLocal)
