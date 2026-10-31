"""Groq adapter (OpenAI-compatible Chat Completions, through the OpenAI SDK).

Chat Completions is used because that is where Groq documents strict Structured Outputs.
Requests are stateless by nature: Groq does not support `store`, and prompts stay out of its
logs only when Zero Data Retention is turned on in the Groq console (Data Controls). The
`user` field carries our pseudonymous hash, never a name or email.

Groq's strict mode cannot be combined with tools or streaming, which is why `structured`
and `chat` are separate calls here. Vision models accept at most 3 images per request.
"""

import json
from collections.abc import Iterator, Sequence
from types import SimpleNamespace
from typing import Any

import openai
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.ai.providers.base import Effort
from app.ai.providers.openai_provider import map_error, parse_tool_arguments
from app.ai.types import (
    AssistantMessage,
    ChatResult,
    Messages,
    RawStructured,
    StreamDone,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolResultMessage,
    ToolSpec,
    Usage,
    UserMessage,
)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
_INVALID_JSON_CODES = {"json_validate_failed"}


class GroqProvider:
    name = "groq"

    def __init__(self, client: OpenAI) -> None:
        self.client = client

    # --- translation ------------------------------------------------------------------

    @staticmethod
    def _messages(system: str, messages: Messages) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for message in messages:
            if isinstance(message, UserMessage):
                content: str | list[dict[str, Any]] = message.text
                if message.images:
                    content = [{"type": "text", "text": message.text}]
                    content += [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{img.media_type};base64,{img.data_b64}"
                            },
                        }
                        for img in message.images
                    ]
                out.append({"role": "user", "content": content})
            elif isinstance(message, AssistantMessage):
                # Chat Completions is rebuilt from the neutral fields: Groq needs nothing
                # opaque replayed between tool-call turns.
                turn: dict[str, Any] = {"role": "assistant", "content": message.text}
                if message.tool_calls:
                    turn["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in message.tool_calls
                    ]
                out.append(turn)
            elif isinstance(message, ToolResultMessage):
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": message.content,
                    }
                )
        return out

    @staticmethod
    def _tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    @staticmethod
    def _usage(usage: Any) -> Usage:
        if usage is None:
            return Usage()
        details = getattr(usage, "prompt_tokens_details", None)
        return Usage(
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            cached_tokens=int(getattr(details, "cached_tokens", 0) or 0),
        )

    @staticmethod
    def _chunk_usage(chunk: Any) -> Any:
        """Usage arrives on a final chunk, at the top level or under `x_groq`."""
        usage = getattr(chunk, "usage", None)
        if usage:
            return usage
        extra = getattr(chunk, "x_groq", None)
        raw = extra.get("usage") if isinstance(extra, dict) else None
        return SimpleNamespace(**raw) if raw else None

    @staticmethod
    def _common(
        model: str,
        system: str,
        messages: Messages,
        max_output_tokens: int,
        reasoning_effort: Effort | None,
        user_ref: str | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model,
            "messages": GroqProvider._messages(system, messages),
            "max_completion_tokens": max_output_tokens,
        }
        if reasoning_effort:
            params["reasoning_effort"] = reasoning_effort
        if user_ref:
            params["user"] = user_ref
        return params

    # --- Provider interface -----------------------------------------------------------

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
    ) -> RawStructured:
        params = self._common(
            model, system, messages, max_output_tokens, reasoning_effort, user_ref
        )
        try:
            response = self.client.chat.completions.parse(
                response_format=schema, **params
            )
        except ValidationError as exc:
            # The model answered, but not in our schema: let AiClient retry with feedback.
            return RawStructured(error=str(exc))
        except openai.LengthFinishReasonError as exc:
            # Cut off at the token limit: the tokens were still spent, so report them.
            return RawStructured(
                error="the answer was cut off", usage=self._usage(exc.completion.usage)
            )
        except openai.BadRequestError as exc:
            if getattr(exc, "code", None) in _INVALID_JSON_CODES:
                return RawStructured(error="the answer did not match the JSON schema")
            raise map_error(exc) from None
        except openai.OpenAIError as exc:
            raise map_error(exc) from None

        usage = self._usage(response.usage)
        request_id = getattr(response, "_request_id", None)
        message = response.choices[0].message
        if getattr(message, "refusal", None):
            return RawStructured(
                refusal=str(message.refusal), usage=usage, request_id=request_id
            )
        if message.parsed is None:
            return RawStructured(
                error="no parsable output", usage=usage, request_id=request_id
            )
        return RawStructured(parsed=message.parsed, usage=usage, request_id=request_id)

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
    ) -> ChatResult:
        params = self._common(
            model, system, messages, max_output_tokens, reasoning_effort, user_ref
        )
        if tools:
            params["tools"] = self._tools(tools)
        try:
            response = self.client.chat.completions.create(**params)
        except openai.OpenAIError as exc:
            raise map_error(exc) from None

        message = response.choices[0].message
        return ChatResult(
            text=message.content or "",
            tool_calls=tuple(
                ToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=parse_tool_arguments(call.function.arguments),
                )
                for call in message.tool_calls or []
            ),
            refusal=getattr(message, "refusal", None),
            usage=self._usage(response.usage),
            request_id=getattr(response, "_request_id", None),
        )

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
    ) -> Iterator[StreamEvent]:
        params = self._common(
            model, system, messages, max_output_tokens, reasoning_effort, user_ref
        )
        if tools:
            params["tools"] = self._tools(tools)

        text: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        usage = Usage()
        request_id: str | None = None
        try:
            stream = self.client.chat.completions.create(
                stream=True, stream_options={"include_usage": True}, **params
            )
            request_id = stream.response.headers.get("x-request-id")
            for chunk in stream:
                raw_usage = self._chunk_usage(chunk)
                if raw_usage:
                    usage = self._usage(raw_usage)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.content:
                    text.append(delta.content)
                    yield TextDelta(delta.content)
                for call in delta.tool_calls or []:
                    entry = calls.setdefault(
                        call.index, {"id": "", "name": "", "arguments": ""}
                    )
                    entry["id"] = call.id or entry["id"]
                    if call.function:
                        entry["name"] = call.function.name or entry["name"]
                        entry["arguments"] += call.function.arguments or ""
        except openai.OpenAIError as exc:
            raise map_error(exc) from None

        yield StreamDone(
            ChatResult(
                text="".join(text),
                tool_calls=tuple(
                    ToolCall(
                        id=entry["id"],
                        name=entry["name"],
                        arguments=parse_tool_arguments(entry["arguments"]),
                    )
                    for _, entry in sorted(calls.items())
                ),
                usage=usage,
                request_id=request_id,
            )
        )
