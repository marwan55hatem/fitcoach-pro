"""OpenAI adapter (Responses API).

Privacy: every request sets store=False, so OpenAI does not keep the response for later
retrieval, and sends only a pseudonymous safety_identifier (a hash), never a name or email.
Because nothing is stored server-side, multi-turn tool use replays the output items
(including encrypted reasoning) that the previous turn returned.
"""

import json
from collections.abc import Iterator, Sequence
from typing import Any

import httpx
import openai
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.ai.errors import (
    AiConnectionError,
    AiNotConfiguredError,
    AiOutputError,
    AiProviderError,
    AiRateLimitError,
    AiServerError,
    AiTimeoutError,
)
from app.ai.providers.base import Effort
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


def build_client(
    api_key: str, timeout_s: float, http_client: httpx.Client | None = None
) -> OpenAI:
    # max_retries=0: AiClient owns retries so behaviour is identical across providers.
    return OpenAI(
        api_key=api_key, timeout=timeout_s, max_retries=0, http_client=http_client
    )


class OpenAiProvider:
    name = "openai"

    def __init__(self, client: OpenAI) -> None:
        self.client = client

    # --- translation ------------------------------------------------------------------

    @staticmethod
    def _input_items(messages: Messages) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in messages:
            if isinstance(message, UserMessage):
                content: list[dict[str, Any]] = [
                    {"type": "input_text", "text": message.text}
                ]
                content += [
                    {
                        "type": "input_image",
                        "image_url": f"data:{img.media_type};base64,{img.data_b64}",
                        "detail": "high",
                    }
                    for img in message.images
                ]
                items.append({"role": "user", "content": content})
            elif isinstance(message, AssistantMessage):
                if message.provider_items:
                    # Replay exactly what the model produced (reasoning, calls, text).
                    items.extend(message.provider_items)
                else:
                    if message.text:
                        items.append({"role": "assistant", "content": message.text})
                    items.extend(
                        {
                            "type": "function_call",
                            "call_id": call.id,
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        }
                        for call in message.tool_calls
                    )
            elif isinstance(message, ToolResultMessage):
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": message.content,
                    }
                )
        return items

    @staticmethod
    def _tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
                "strict": False,
            }
            for t in tools
        ]

    @staticmethod
    def _usage(response: Any) -> Usage:
        usage = getattr(response, "usage", None)
        if usage is None:
            return Usage()
        details = getattr(usage, "input_tokens_details", None)
        return Usage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cached_tokens=int(getattr(details, "cached_tokens", 0) or 0),
        )

    @staticmethod
    def _refusal(response: Any) -> str | None:
        for item in getattr(response, "output", None) or []:
            if getattr(item, "type", None) != "message":
                continue
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", None) == "refusal":
                    return str(getattr(part, "refusal", "") or "refused")
        return None

    @staticmethod
    def _tool_calls(response: Any) -> tuple[ToolCall, ...]:
        calls: list[ToolCall] = []
        for item in getattr(response, "output", None) or []:
            if getattr(item, "type", None) != "function_call":
                continue
            try:
                arguments = json.loads(item.arguments or "{}")
            except json.JSONDecodeError as exc:
                raise AiOutputError("tool call arguments were not valid JSON") from exc
            if not isinstance(arguments, dict):
                raise AiOutputError("tool call arguments were not an object")
            calls.append(ToolCall(id=item.call_id, name=item.name, arguments=arguments))
        return tuple(calls)

    @staticmethod
    def _items(response: Any) -> tuple[Any, ...]:
        return tuple(
            item.model_dump(exclude_none=True)
            for item in getattr(response, "output", None) or []
        )

    @classmethod
    def _chat_result(cls, response: Any) -> ChatResult:
        return ChatResult(
            text=getattr(response, "output_text", "") or "",
            tool_calls=cls._tool_calls(response),
            provider_items=cls._items(response),
            refusal=cls._refusal(response),
            usage=cls._usage(response),
            request_id=getattr(response, "_request_id", None),
        )

    def _common(
        self,
        model: str,
        system: str,
        max_output_tokens: int,
        reasoning_effort: Effort | None,
        user_ref: str | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model,
            "instructions": system,
            "max_output_tokens": max_output_tokens,
            "store": False,
        }
        if reasoning_effort:
            params["reasoning"] = {"effort": reasoning_effort}
        if user_ref:
            params["safety_identifier"] = user_ref
        return params

    # --- error mapping ----------------------------------------------------------------

    @staticmethod
    def _map_error(exc: Exception) -> Exception:
        if isinstance(exc, openai.APITimeoutError):
            return AiTimeoutError()
        if isinstance(exc, openai.APIConnectionError):
            return AiConnectionError()
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return AiNotConfiguredError()
        if isinstance(exc, openai.RateLimitError):
            if getattr(exc, "code", None) == "insufficient_quota":
                return AiProviderError()  # billing problem, retrying will not help
            return AiRateLimitError()
        if isinstance(exc, openai.APIStatusError):
            return AiServerError() if exc.status_code >= 500 else AiProviderError()
        if isinstance(
            exc, (openai.LengthFinishReasonError, openai.ContentFilterFinishReasonError)
        ):
            return AiOutputError()
        return AiProviderError()

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
            model, system, max_output_tokens, reasoning_effort, user_ref
        )
        try:
            response = self.client.responses.parse(
                input=self._input_items(messages), text_format=schema, **params
            )
        except ValidationError as exc:
            # The model answered, but not in our schema: let AiClient retry with feedback.
            return RawStructured(error=str(exc))
        except openai.OpenAIError as exc:
            raise self._map_error(exc) from None

        usage = self._usage(response)
        request_id = getattr(response, "_request_id", None)
        refusal = self._refusal(response)
        if refusal is not None:
            return RawStructured(refusal=refusal, usage=usage, request_id=request_id)
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            return RawStructured(
                error="no parsable output", usage=usage, request_id=request_id
            )
        return RawStructured(parsed=parsed, usage=usage, request_id=request_id)

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
            model, system, max_output_tokens, reasoning_effort, user_ref
        )
        if tools:
            params["tools"] = self._tools(tools)
        try:
            response = self.client.responses.create(
                input=self._input_items(messages), **params
            )
        except openai.OpenAIError as exc:
            raise self._map_error(exc) from None
        return self._chat_result(response)

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
            model, system, max_output_tokens, reasoning_effort, user_ref
        )
        if tools:
            params["tools"] = self._tools(tools)
        try:
            with self.client.responses.stream(
                input=self._input_items(messages), **params
            ) as stream:
                for event in stream:
                    if event.type == "response.output_text.delta":
                        yield TextDelta(event.delta)
                final = stream.get_final_response()
        except openai.OpenAIError as exc:
            raise self._map_error(exc) from None
        yield StreamDone(self._chat_result(final))
