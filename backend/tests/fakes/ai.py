"""FakeProvider: a scripted AI provider that every AI test in the project uses.

    fake = FakeProvider()
    fake.queue_structured(MyModel(...), usage=Usage(100, 20))       # a valid answer
    fake.queue_structured(error="missing field x")                   # an invalid answer
    fake.queue_structured(refusal="no")                              # a refusal
    fake.queue_error(AiTimeoutError())                               # a provider failure
    fake.queue_chat(text="hi", tool_calls=[ToolCall(...)])           # a chat turn
    fake.queue_stream(["Hel", "lo"])                                 # a streamed reply
    fake.queue_stream([], tool_calls=[ToolCall(...)])                # a streamed tool turn

Every call is recorded in `fake.calls` so tests can assert what would have been sent.
"""

from collections import deque
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.ai.types import (
    ChatResult,
    Messages,
    RawStructured,
    StreamDone,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolSpec,
    Usage,
)

DEFAULT_USAGE = Usage(input_tokens=100, output_tokens=20)


@dataclass
class Call:
    kind: str  # structured | chat | stream
    model: str
    system: str
    messages: list[Any]
    tools: list[ToolSpec] = field(default_factory=list)
    schema: type[BaseModel] | None = None
    user_ref: str | None = None


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self._queue: deque[Any] = deque()
        self.calls: list[Call] = []

    # --- scripting --------------------------------------------------------------------

    def queue_structured(
        self,
        parsed: BaseModel | None = None,
        *,
        error: str | None = None,
        refusal: str | None = None,
        usage: Usage = DEFAULT_USAGE,
    ) -> None:
        self._queue.append(RawStructured(parsed, refusal, error, usage, "req_fake"))

    def queue_chat(
        self,
        text: str = "",
        *,
        tool_calls: Sequence[ToolCall] = (),
        usage: Usage = DEFAULT_USAGE,
    ) -> None:
        items = tuple({"type": "fake_item", "text": text} for _ in [0])
        self._queue.append(
            ChatResult(text, tuple(tool_calls), items, None, usage, "req_fake")
        )

    def queue_stream(
        self,
        chunks: Sequence[str],
        *,
        tool_calls: Sequence[ToolCall] = (),
        usage: Usage = DEFAULT_USAGE,
    ) -> None:
        self._queue.append(("stream", list(chunks), usage, tuple(tool_calls)))

    def queue_error(self, error: Exception) -> None:
        self._queue.append(error)

    # --- Provider interface -----------------------------------------------------------

    def _next(self) -> Any:
        if not self._queue:
            raise AssertionError("FakeProvider: nothing queued for this call")
        item = self._queue.popleft()
        if isinstance(item, Exception):
            raise item
        return item

    def structured(
        self,
        *,
        model: str,
        system: str,
        messages: Messages,
        schema: type[BaseModel],
        max_output_tokens: int,
        reasoning_effort: str | None,
        user_ref: str | None,
    ) -> RawStructured:
        self.calls.append(
            Call(
                "structured",
                model,
                system,
                list(messages),
                schema=schema,
                user_ref=user_ref,
            )
        )
        return self._next()

    def chat(
        self,
        *,
        model: str,
        system: str,
        messages: Messages,
        tools: Sequence[ToolSpec],
        max_output_tokens: int,
        reasoning_effort: str | None,
        user_ref: str | None,
    ) -> ChatResult:
        self.calls.append(
            Call("chat", model, system, list(messages), list(tools), user_ref=user_ref)
        )
        return self._next()

    def stream_chat(
        self,
        *,
        model: str,
        system: str,
        messages: Messages,
        tools: Sequence[ToolSpec],
        max_output_tokens: int,
        reasoning_effort: str | None,
        user_ref: str | None,
    ) -> Iterator[StreamEvent]:
        self.calls.append(
            Call(
                "stream", model, system, list(messages), list(tools), user_ref=user_ref
            )
        )
        _, chunks, usage, tool_calls = self._next()
        for chunk in chunks:
            yield TextDelta(chunk)
        yield StreamDone(
            ChatResult("".join(chunks), tool_calls, (), None, usage, "req_fake")
        )
