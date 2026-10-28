"""Provider-neutral types. Nothing here knows about OpenAI (or any SDK)."""

import enum
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


class Task(enum.StrEnum):
    """What a call is for; each task maps to a model (settings or provider default)."""

    MAIN = "main"  # coach chat, program generation
    VISION = "vision"  # reading InBody printouts and food photos
    FAST = "fast"  # cheap classification


@dataclass(frozen=True)
class AiImage:
    data_b64: str
    media_type: str


@dataclass(frozen=True)
class UserMessage:
    text: str
    images: tuple[AiImage, ...] = ()


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AssistantMessage:
    """A previous assistant turn. `provider_items` are opaque items the provider needs
    replayed on the next turn (for example reasoning items); pass them back untouched."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    provider_items: tuple[Any, ...] = ()


@dataclass(frozen=True)
class ToolResultMessage:
    tool_call_id: str
    content: str


Message = UserMessage | AssistantMessage | ToolResultMessage


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0  # includes cached tokens
    output_tokens: int = 0  # includes reasoning tokens
    cached_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cached_tokens + other.cached_tokens,
        )

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class RawStructured:
    """One provider attempt at a structured answer. Exactly one of parsed/refusal/error."""

    parsed: BaseModel | None = None
    refusal: str | None = None
    error: str | None = None  # why validation failed (never logged)
    usage: Usage = field(default_factory=Usage)
    request_id: str | None = None


@dataclass(frozen=True)
class ChatResult:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    provider_items: tuple[Any, ...] = ()
    refusal: str | None = None
    usage: Usage = field(default_factory=Usage)
    request_id: str | None = None


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class StreamDone:
    result: ChatResult


StreamEvent = TextDelta | StreamDone

Messages = Sequence[Message]
