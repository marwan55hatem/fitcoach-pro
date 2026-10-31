"""The Groq adapter, driven through the real OpenAI SDK against a mocked HTTP transport."""

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.ai.errors import (
    AiConnectionError,
    AiNotConfiguredError,
    AiOutputError,
    AiProviderError,
    AiRateLimitError,
    AiServerError,
    AiTimeoutError,
)
from app.ai.providers.factory import api_key_for, build_provider
from app.ai.providers.groq_provider import GROQ_BASE_URL, GroqProvider
from app.ai.providers.openai_provider import OpenAiProvider, build_client
from app.ai.types import (
    AiImage,
    AssistantMessage,
    StreamDone,
    TextDelta,
    ToolCall,
    ToolResultMessage,
    ToolSpec,
    Usage,
    UserMessage,
)
from app.core.config import Settings


class Answer(BaseModel):
    value: int


def completion(
    message: dict[str, Any], usage: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1_700_000_000,
        "model": "openai/gpt-oss-120b",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "logprobs": None,
                "message": {"role": "assistant", **message},
            }
        ],
        "usage": usage
        or {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
    }


class Recorder:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.urls: list[str] = []
        self.replies: list[Any] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        self.urls.append(str(request.url))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply, headers={"x-request-id": "req_groq1"})


def make_provider() -> tuple[GroqProvider, Recorder]:
    recorder = Recorder()
    http = httpx.Client(transport=httpx.MockTransport(recorder))
    client = build_client(
        "gsk-test-not-real", 30, http_client=http, base_url=GROQ_BASE_URL
    )
    return GroqProvider(client), recorder


COMMON: dict[str, Any] = {
    "model": "openai/gpt-oss-120b",
    "system": "You are precise.",
    "max_output_tokens": 500,
    "reasoning_effort": None,
    "user_ref": "hashed-user-ref",
}
STRUCTURED: dict[str, Any] = {**COMMON, "schema": Answer}
CHAT: dict[str, Any] = {**COMMON, "tools": []}
ADD_TOOL = ToolSpec("add", "Add numbers.", {"type": "object", "properties": {}})


def error_reply(status: int, code: str, kind: str = "invalid_request_error"):
    body = {"error": {"message": "boom", "type": kind, "code": code}}
    return httpx.Response(status, json=body)


# --- structured -------------------------------------------------------------------------


def test_structured_request_uses_strict_schema_and_no_identity() -> None:
    provider, rec = make_provider()
    rec.replies.append(completion({"content": '{"value": 42}'}))
    image = AiImage("QUJD", "image/jpeg")

    raw = provider.structured(messages=[UserMessage("read", (image,))], **STRUCTURED)

    assert rec.urls[0] == f"{GROQ_BASE_URL}/chat/completions"
    body = rec.requests[0]
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["max_completion_tokens"] == 500
    assert body["user"] == "hashed-user-ref"  # pseudonymous, never a name or email
    assert "store" not in body  # Groq does not support it
    assert "reasoning_effort" not in body  # only sent when requested
    assert body["messages"][0] == {"role": "system", "content": "You are precise."}
    assert body["messages"][1]["content"] == [
        {"type": "text", "text": "read"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
    ]
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert schema["additionalProperties"] is False and schema["required"] == ["value"]

    assert raw.parsed == Answer(value=42) and raw.error is None
    assert raw.usage == Usage(input_tokens=120, output_tokens=30)
    assert raw.request_id == "req_groq1"


def test_text_only_user_messages_stay_plain_strings() -> None:
    provider, rec = make_provider()
    rec.replies.append(completion({"content": "hi"}))
    provider.chat(messages=[UserMessage("hello")], **CHAT)
    assert rec.requests[0]["messages"][1] == {"role": "user", "content": "hello"}


def test_reasoning_effort_is_sent_when_asked() -> None:
    provider, rec = make_provider()
    rec.replies.append(completion({"content": '{"value": 1}'}))
    provider.structured(
        messages=[UserMessage("q")], **{**STRUCTURED, "reasoning_effort": "low"}
    )
    assert rec.requests[0]["reasoning_effort"] == "low"


@pytest.mark.parametrize("content", ['{"value": "not a number"}', "Sure! It is 42."])
def test_invalid_answers_are_reported_for_repair_not_raised(content: str) -> None:
    provider, rec = make_provider()
    rec.replies.append(completion({"content": content}))
    raw = provider.structured(messages=[UserMessage("q")], **STRUCTURED)
    assert raw.parsed is None and raw.error


def test_groq_schema_rejection_is_reported_for_repair() -> None:
    provider, rec = make_provider()
    rec.replies.append(error_reply(400, "json_validate_failed"))
    raw = provider.structured(messages=[UserMessage("q")], **STRUCTURED)
    assert raw.parsed is None and raw.error and "boom" not in raw.error


def test_other_bad_requests_are_provider_errors() -> None:
    provider, rec = make_provider()
    rec.replies.append(error_reply(400, "invalid_prompt"))
    with pytest.raises(AiProviderError):
        provider.structured(messages=[UserMessage("q")], **STRUCTURED)


def test_structured_refusal() -> None:
    provider, rec = make_provider()
    rec.replies.append(completion({"content": None, "refusal": "I can't do that."}))
    raw = provider.structured(messages=[UserMessage("q")], **STRUCTURED)
    assert raw.refusal == "I can't do that." and raw.parsed is None
    assert raw.usage.input_tokens == 120


# --- chat and tools ---------------------------------------------------------------------


def test_tool_call_then_replay_rebuilds_the_conversation() -> None:
    provider, rec = make_provider()
    call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "add", "arguments": '{"a": 19, "b": 23}'},
    }
    rec.replies.append(completion({"content": None, "tool_calls": [call]}))
    rec.replies.append(completion({"content": "The sum is 42."}))
    question = UserMessage("19 + 23?")

    first = provider.chat(messages=[question], **{**CHAT, "tools": [ADD_TOOL]})
    assert rec.requests[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "add",
                "description": "Add numbers.",
                "parameters": ADD_TOOL.parameters,
            },
        }
    ]
    assert first.text == ""
    assert first.tool_calls == (ToolCall("call_1", "add", {"a": 19, "b": 23}),)

    history = [
        question,
        AssistantMessage(first.text, first.tool_calls, first.provider_items),
        ToolResultMessage("call_1", "42"),
    ]
    second = provider.chat(messages=history, **{**CHAT, "tools": [ADD_TOOL]})
    assert second.text == "The sum is 42."

    sent = rec.requests[1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "tool"]
    assert sent[2]["tool_calls"][0]["id"] == "call_1"
    assert json.loads(sent[2]["tool_calls"][0]["function"]["arguments"]) == {
        "a": 19,
        "b": 23,
    }
    assert sent[3] == {"role": "tool", "tool_call_id": "call_1", "content": "42"}


def test_chat_without_tools_omits_the_field() -> None:
    provider, rec = make_provider()
    rec.replies.append(completion({"content": "hello"}))
    assert provider.chat(messages=[UserMessage("hi")], **CHAT).text == "hello"
    assert "tools" not in rec.requests[0]


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]"])
def test_malformed_tool_arguments_are_an_output_error(arguments: str) -> None:
    provider, rec = make_provider()
    call = {
        "id": "c",
        "type": "function",
        "function": {"name": "add", "arguments": arguments},
    }
    rec.replies.append(completion({"content": None, "tool_calls": [call]}))
    with pytest.raises(AiOutputError):
        provider.chat(messages=[UserMessage("q")], **{**CHAT, "tools": [ADD_TOOL]})


# --- streaming --------------------------------------------------------------------------


def sse(chunks: list[dict[str, Any]]) -> httpx.Response:
    text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(
        200,
        content=text.encode(),
        headers={"content-type": "text/event-stream", "x-request-id": "req_stream"},
    )


def chunk(delta: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
    choices = (
        []
        if delta is None
        else [{"index": 0, "delta": delta, "finish_reason": None, "logprobs": None}]
    )
    return {
        "id": "c1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "m",
        "choices": choices,
        **extra,
    }


USAGE = {"prompt_tokens": 50, "completion_tokens": 7, "total_tokens": 57}


@pytest.mark.parametrize(
    "final",
    [chunk(None, usage=USAGE), chunk({}, x_groq={"usage": USAGE})],
    ids=["top-level usage", "x_groq usage"],
)
def test_stream_yields_deltas_then_the_result_with_usage(final: dict[str, Any]) -> None:
    provider, rec = make_provider()
    rec.replies.append(
        sse([chunk({"content": "Hello "}), chunk({"content": "world"}), final])
    )
    events = list(provider.stream_chat(messages=[UserMessage("hi")], **CHAT))

    assert rec.requests[0]["stream"] is True
    assert rec.requests[0]["stream_options"] == {"include_usage": True}
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hello ", "world"]
    done = events[-1]
    assert isinstance(done, StreamDone)
    assert done.result.text == "Hello world"
    assert done.result.usage == Usage(50, 7)
    assert done.result.request_id == "req_stream"


def test_stream_assembles_tool_calls_from_fragments() -> None:
    provider, rec = make_provider()
    first = {
        "index": 0,
        "id": "call_9",
        "type": "function",
        "function": {"name": "add", "arguments": '{"a":'},
    }
    second = {"index": 0, "function": {"arguments": " 1}"}}
    rec.replies.append(
        sse([chunk({"tool_calls": [first]}), chunk({"tool_calls": [second]})])
    )
    events = list(provider.stream_chat(messages=[UserMessage("q")], **CHAT))
    assert events[-1].result.tool_calls == (ToolCall("call_9", "add", {"a": 1}),)


# --- errors -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (error_reply(429, "rate_limit_exceeded", "tokens"), AiRateLimitError),
        (error_reply(401, "invalid_api_key"), AiNotConfiguredError),
        (error_reply(403, "forbidden"), AiNotConfiguredError),
        (error_reply(413, "request_too_large"), AiProviderError),
        (error_reply(404, "model_not_found"), AiProviderError),
        (error_reply(500, "server_error", "server_error"), AiServerError),
        (error_reply(503, "overloaded", "server_error"), AiServerError),
        (httpx.ReadTimeout("slow"), AiTimeoutError),
        (httpx.ConnectError("no route"), AiConnectionError),
    ],
)
def test_vendor_errors_map_without_leaking_detail(
    reply: Any, expected: type[Exception]
) -> None:
    provider, rec = make_provider()
    rec.replies.append(reply)
    with pytest.raises(expected) as caught:
        provider.chat(messages=[UserMessage("secret question")], **CHAT)
    assert len(rec.requests) == 1  # AiClient owns retries, the SDK never retries
    assert "boom" not in str(caught.value)
    assert "secret question" not in str(caught.value)


def test_stream_errors_map_too() -> None:
    provider, rec = make_provider()
    rec.replies.append(error_reply(429, "rate_limit_exceeded", "tokens"))
    with pytest.raises(AiRateLimitError):
        list(provider.stream_chat(messages=[UserMessage("q")], **CHAT))


# --- provider selection -----------------------------------------------------------------


def settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_groq_is_the_default_provider_and_uses_its_own_key() -> None:
    s = settings(groq_api_key="gsk-x", openai_api_key="sk-y")
    assert s.ai_provider == "groq" and api_key_for(s) == "gsk-x"
    provider = build_provider(s)
    assert isinstance(provider, GroqProvider)
    assert str(provider.client.base_url).rstrip("/") == GROQ_BASE_URL


def test_openai_can_still_be_selected() -> None:
    s = settings(ai_provider="openai", groq_api_key="gsk-x", openai_api_key="sk-y")
    assert isinstance(build_provider(s), OpenAiProvider)


@pytest.mark.parametrize("provider", ["groq", "openai"])
def test_a_missing_key_gives_no_provider(provider: str) -> None:
    other = {"openai_api_key": "sk-y"} if provider == "groq" else {"groq_api_key": "g"}
    keys = {"groq_api_key": "", "openai_api_key": "", **other}
    assert build_provider(settings(ai_provider=provider, **keys)) is None


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="ai_provider"):
        settings(ai_provider="mystery")
