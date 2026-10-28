from collections.abc import Iterator, Sequence
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

from app.ai.types import ChatResult, Messages, RawStructured, StreamEvent, ToolSpec

T = TypeVar("T", bound=BaseModel)
Effort = Literal["low", "medium", "high"]


class Provider(Protocol):
    """One AI vendor behind a stable interface.

    Implementations translate our neutral types to the vendor SDK and map vendor errors to
    app.ai.errors. They do no retrying, budgeting or logging: AiClient owns that, so every
    provider behaves the same.
    """

    name: str

    def structured(
        self,
        *,
        model: str,
        system: str,
        messages: Messages,
        schema: type[BaseModel],
        max_output_tokens: int,
        reasoning_effort: Effort | None,
        user_ref: str | None,
    ) -> RawStructured: ...

    def chat(
        self,
        *,
        model: str,
        system: str,
        messages: Messages,
        tools: Sequence[ToolSpec],
        max_output_tokens: int,
        reasoning_effort: Effort | None,
        user_ref: str | None,
    ) -> ChatResult: ...

    def stream_chat(
        self,
        *,
        model: str,
        system: str,
        messages: Messages,
        tools: Sequence[ToolSpec],
        max_output_tokens: int,
        reasoning_effort: Effort | None,
        user_ref: str | None,
    ) -> Iterator[StreamEvent]: ...
