"""The OpenAI adapter, driven through the real SDK against a mocked HTTP transport."""

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
    AiQuotaError,
    AiRateLimitError,
    AiServerError,
    AiTimeoutError,
)
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


class Answer(BaseModel):
    value: int


def message_item(text: str) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def response_body(
    output: list[dict[str, Any]], usage: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1_700_000_000,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "metadata": {},
        "model": "gpt-6.1-sol",
        "output": output,
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "temperature": 1.0,
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1.0,
        "truncation": "disabled",
        "store": False,
        "usage": usage
        or {
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "input_tokens_details": {"cached_tokens": 64},
            "output_tokens_details": {"reasoning_tokens": 10},
        },
    }


class Recorder:
    """A mocked OpenAI endpoint that records each request body and replays queued replies."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.replies: list[Any] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply, headers={"x-request-id": "req_abc123"})


def make_provider() -> tuple[OpenAiProvider, Recorder]:
    recorder = Recorder()
    http = httpx.Client(transport=httpx.MockTransport(recorder))
    return OpenAiProvider(
        build_client("sk-test-not-real", 30, http_client=http)
    ), recorder


COMMON: dict[str, Any] = {
    "model": "gpt-6.1-sol",
    "system": "You are precise.",
    "max_output_tokens": 500,
    "reasoning_effort": None,
    "user_ref": "hashed-user-ref",
}
STRUCTURED: dict[str, Any] = {**COMMON, "schema": Answer}
CHAT: dict[str, Any] = {**COMMON, "tools": []}


def error_reply(
    status: int, code: str, kind: str = "invalid_request_error"
) -> httpx.Response:
    body = {"error": {"message": "boom", "type": kind, "code": code, "param": None}}
    return httpx.Response(status, json=body)


# --- structured -------------------------------------------------------------------------


def test_structured_request_is_private_and_well_formed() -> None:
    provider, rec = make_provider()
    rec.replies.append(response_body([message_item('{"value": 42}')]))
    image = AiImage("QUJD", "image/jpeg")

    raw = provider.structured(
        messages=[UserMessage("read this", (image,))], **STRUCTURED
    )

    body = rec.requests[0]
    assert body["model"] == "gpt-6.1-sol"
    assert body["store"] is False  # nothing is kept server-side
    assert body["instructions"] == "You are precise."
    assert body["max_output_tokens"] == 500
    assert (
        body["safety_identifier"] == "hashed-user-ref"
    )  # pseudonymous, never email/name
    assert "previous_response_id" not in body and "conversation" not in body
    assert "reasoning" not in body  # only sent when requested
    content = body["input"][0]["content"]
    assert content[0] == {"type": "input_text", "text": "read this"}
    assert content[1] == {
        "type": "input_image",
        "image_url": "data:image/jpeg;base64,QUJD",
        "detail": "high",
    }
    fmt = body["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    assert fmt["schema"]["properties"]["value"]["type"] == "integer"

    assert raw.parsed == Answer(value=42) and raw.refusal is None and raw.error is None
    assert raw.usage == Usage(input_tokens=120, output_tokens=30, cached_tokens=64)
    assert raw.request_id == "req_abc123"


def test_structured_sends_reasoning_effort_when_asked() -> None:
    provider, rec = make_provider()
    rec.replies.append(response_body([message_item('{"value": 1}')]))
    provider.structured(
        messages=[UserMessage("q")], **{**STRUCTURED, "reasoning_effort": "low"}
    )
    assert rec.requests[0]["reasoning"] == {"effort": "low"}


def test_structured_invalid_answer_is_reported_not_raised() -> None:
    provider, rec = make_provider()
    rec.replies.append(response_body([message_item('{"value": "not a number"}')]))
    raw = provider.structured(messages=[UserMessage("q")], **STRUCTURED)
    assert raw.parsed is None and raw.error and raw.refusal is None


def test_structured_garbage_text_is_reported_not_raised() -> None:
    provider, rec = make_provider()
    rec.replies.append(response_body([message_item("Sure! The answer is 42.")]))
    raw = provider.structured(messages=[UserMessage("q")], **STRUCTURED)
    assert raw.parsed is None and raw.error


def test_structured_refusal() -> None:
    provider, rec = make_provider()
    refusal = {
        "id": "msg_1",
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [{"type": "refusal", "refusal": "I can't help with that."}],
    }
    rec.replies.append(response_body([refusal]))
    raw = provider.structured(messages=[UserMessage("q")], **STRUCTURED)
    assert raw.parsed is None and raw.refusal == "I can't help with that."
    assert raw.usage.input_tokens == 120  # still billed, still reported


# --- chat, tools, stateless replay ------------------------------------------------------

REASONING = {
    "id": "rs_1",
    "type": "reasoning",
    "summary": [],
    "encrypted_content": "ENC-STATE",
}
FUNCTION_CALL = {
    "id": "fc_1",
    "type": "function_call",
    "status": "completed",
    "call_id": "call_1",
    "name": "add",
    "arguments": '{"a": 19, "b": 23}',
}
ADD_TOOL = ToolSpec(
    "add", "Add two integers.", {"type": "object", "properties": {"a": {}, "b": {}}}
)


def test_chat_tool_call_then_stateless_replay() -> None:
    provider, rec = make_provider()
    rec.replies.append(response_body([REASONING, FUNCTION_CALL]))
    rec.replies.append(response_body([message_item("The sum is 42.")]))
    question = UserMessage("19 + 23?")

    first = provider.chat(messages=[question], **{**CHAT, "tools": [ADD_TOOL]})
    tools = rec.requests[0]["tools"]
    assert tools == [
        {
            "type": "function",
            "name": "add",
            "description": "Add two integers.",
            "parameters": ADD_TOOL.parameters,
            "strict": False,
        }
    ]
    assert rec.requests[0]["store"] is False
    assert first.tool_calls == (ToolCall("call_1", "add", {"a": 19, "b": 23}),)
    assert first.text == ""
    assert first.provider_items[0]["encrypted_content"] == "ENC-STATE"

    history = [
        question,
        AssistantMessage(first.text, first.tool_calls, first.provider_items),
        ToolResultMessage("call_1", "42"),
    ]
    second = provider.chat(messages=history, **{**CHAT, "tools": [ADD_TOOL]})
    assert second.text == "The sum is 42." and second.tool_calls == ()

    sent = rec.requests[1]["input"]
    assert sent[0]["role"] == "user"
    # The previous turn's items are replayed untouched (reasoning included), exactly once.
    assert [item.get("type") for item in sent[1:]] == [
        "reasoning",
        "function_call",
        "function_call_output",
    ]
    assert (
        sent[1]["encrypted_content"] == "ENC-STATE" and sent[2]["call_id"] == "call_1"
    )
    assert sent[3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "42",
    }


def test_assistant_message_without_provider_items_is_rebuilt() -> None:
    provider, rec = make_provider()
    rec.replies.append(response_body([message_item("ok")]))
    history = [
        UserMessage("hi"),
        AssistantMessage("calling", (ToolCall("c9", "add", {"a": 1, "b": 2}),)),
        ToolResultMessage("c9", "3"),
    ]
    provider.chat(messages=history, **CHAT)
    sent = rec.requests[0]["input"]
    assert sent[1] == {"role": "assistant", "content": "calling"}
    assert sent[2] == {
        "type": "function_call",
        "call_id": "c9",
        "name": "add",
        "arguments": '{"a": 1, "b": 2}',
    }
    assert sent[3]["type"] == "function_call_output"


def test_chat_without_tools_omits_the_tools_field() -> None:
    provider, rec = make_provider()
    rec.replies.append(response_body([message_item("hello")]))
    result = provider.chat(messages=[UserMessage("hi")], **CHAT)
    assert "tools" not in rec.requests[0]
    assert result.text == "hello" and result.usage.cached_tokens == 64


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]"])
def test_malformed_tool_arguments_are_an_output_error(arguments: str) -> None:
    provider, rec = make_provider()
    rec.replies.append(response_body([{**FUNCTION_CALL, "arguments": arguments}]))
    with pytest.raises(AiOutputError):
        provider.chat(messages=[UserMessage("q")], **{**CHAT, "tools": [ADD_TOOL]})


# --- streaming --------------------------------------------------------------------------


def sse(events: list[dict[str, Any]]) -> httpx.Response:
    text = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(
        200, content=text.encode(), headers={"content-type": "text/event-stream"}
    )


def test_stream_yields_deltas_then_the_final_result() -> None:
    provider, rec = make_provider()
    final = response_body([message_item("Hello world")])
    in_progress = {**final, "status": "in_progress", "output": [], "usage": None}
    item = {**message_item(""), "status": "in_progress", "content": []}
    part = {"type": "output_text", "text": "", "annotations": []}
    rec.replies.append(
        sse(
            [
                {
                    "type": "response.created",
                    "sequence_number": 0,
                    "response": in_progress,
                },
                {
                    "type": "response.output_item.added",
                    "sequence_number": 1,
                    "output_index": 0,
                    "item": item,
                },
                {
                    "type": "response.content_part.added",
                    "sequence_number": 2,
                    "output_index": 0,
                    "item_id": "msg_1",
                    "content_index": 0,
                    "part": part,
                },
                {
                    "type": "response.output_text.delta",
                    "sequence_number": 3,
                    "output_index": 0,
                    "item_id": "msg_1",
                    "content_index": 0,
                    "delta": "Hello ",
                    "logprobs": [],
                },
                {
                    "type": "response.output_text.delta",
                    "sequence_number": 4,
                    "output_index": 0,
                    "item_id": "msg_1",
                    "content_index": 0,
                    "delta": "world",
                    "logprobs": [],
                },
                {
                    "type": "response.output_text.done",
                    "sequence_number": 5,
                    "output_index": 0,
                    "item_id": "msg_1",
                    "content_index": 0,
                    "text": "Hello world",
                    "logprobs": [],
                },
                {
                    "type": "response.content_part.done",
                    "sequence_number": 6,
                    "output_index": 0,
                    "item_id": "msg_1",
                    "content_index": 0,
                    "part": {**part, "text": "Hello world"},
                },
                {
                    "type": "response.output_item.done",
                    "sequence_number": 7,
                    "output_index": 0,
                    "item": message_item("Hello world"),
                },
                {"type": "response.completed", "sequence_number": 8, "response": final},
            ]
        )
    )
    events = list(provider.stream_chat(messages=[UserMessage("hi")], **CHAT))
    assert rec.requests[0]["stream"] is True and rec.requests[0]["store"] is False
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hello ", "world"]
    done = events[-1]
    assert isinstance(done, StreamDone) and done.result.text == "Hello world"
    assert done.result.usage == Usage(120, 30, 64)


# --- error mapping ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (error_reply(429, "rate_limit_exceeded", "rate_limit_error"), AiRateLimitError),
        (error_reply(429, "insufficient_quota", "insufficient_quota"), AiQuotaError),
        (
            error_reply(429, "credit_balance_exhausted", "insufficient_quota"),
            AiQuotaError,
        ),
        (
            error_reply(401, "invalid_api_key", "invalid_request_error"),
            AiNotConfiguredError,
        ),
        (error_reply(403, "forbidden"), AiNotConfiguredError),
        (error_reply(400, "invalid_prompt"), AiProviderError),
        (error_reply(404, "model_not_found"), AiProviderError),
        (error_reply(500, "server_error", "server_error"), AiServerError),
        (error_reply(503, "overloaded", "server_error"), AiServerError),
        (httpx.ReadTimeout("slow"), AiTimeoutError),
        (httpx.ConnectError("no route"), AiConnectionError),
    ],
)
def test_vendor_errors_map_to_our_errors_without_leaking_detail(
    reply: Any, expected: type[Exception]
) -> None:
    provider, rec = make_provider()
    rec.replies.append(reply)
    with pytest.raises(expected) as caught:
        provider.chat(messages=[UserMessage("secret question")], **CHAT)
    assert (
        len(rec.requests) == 1
    )  # the SDK never retries on its own: AiClient owns retries
    assert "boom" not in str(caught.value) and "secret question" not in str(
        caught.value
    )


def test_structured_errors_map_the_same_way() -> None:
    provider, rec = make_provider()
    rec.replies.append(error_reply(429, "rate_limit_exceeded", "rate_limit_error"))
    with pytest.raises(AiRateLimitError):
        provider.structured(messages=[UserMessage("q")], **STRUCTURED)
