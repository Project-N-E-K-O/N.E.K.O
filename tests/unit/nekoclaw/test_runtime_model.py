"""The model adapter: loop messages to the wire and stream chunks to loop events."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from nekoclaw.runtime.loop.harness import SimpleCancellationToken
from nekoclaw.runtime.loop.messages import (
    AssistantMessage,
    CustomMessage,
    ImageContent,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from nekoclaw.runtime.loop.provider_events import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantStartEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from nekoclaw.runtime.model import (
    REASONING_FIELD_SIGNATURE,
    ContextOverflowError,
    LLMClientProvider,
    ModelRequestError,
    is_context_overflow,
    to_wire_messages,
    usage_from_metadata,
)
from nekoclaw.runtime.protocol import ModelConfig
from nekoclaw.runtime.tools.base import INVALID_ARGUMENTS_KEY
from tests.unit.nekoclaw.fake_openai_server import (
    FakeOpenAIServer,
    sse_finish,
    sse_reasoning,
    sse_text,
    sse_tool_call,
    sse_usage,
)
from tests.unit.nekoclaw.fakes import ScriptedLLM, call, finish, say, think, usage
from utils.llm_client.messages import LLMStreamChunk


def _provider(llm: ScriptedLLM, **config) -> LLMClientProvider:  # noqa: ANN003
    async def factory() -> ScriptedLLM:
        return llm

    return LLMClientProvider(ModelConfig(model="fake-model", **config), client_factory=factory)


async def _events(provider: LLMClientProvider, messages: list | None = None, signal=None) -> list:  # noqa: ANN001
    stream = provider.stream_response(
        model="fake-model", system="SYSTEM", messages=messages or [UserMessage(content="hi")], tools=[], signal=signal
    )
    return [event async for event in stream]


def _wire(messages: list, **kwargs) -> list[dict]:  # noqa: ANN003
    options = {"anthropic": False, "supports_images": True, "part_max_tokens": 64_000}
    options.update(kwargs)
    return to_wire_messages("SYSTEM", messages, **options)


# ---------------------------------------------------------------------------
# Messages to the wire
# ---------------------------------------------------------------------------


def test_transcript_becomes_openai_format_messages() -> None:
    signature = {"google": {"thought_signature": "abc"}}
    messages = [
        UserMessage(content="Find it."),
        AssistantMessage(
            content=[
                ThinkingContent(thinking="Plan: grep.", thinking_signature=REASONING_FIELD_SIGNATURE),
                TextContent(text="Searching."),
                ToolCall(id="c1", name="grep", arguments={"pattern": "x"}, thought_signature=json.dumps(signature)),
            ]
        ),
        ToolResultMessage(tool_call_id="c1", tool_name="grep", content="No matches found"),
        CustomMessage(custom_type="notice:job", content="background job job-1 finished"),
        AssistantMessage(content=[ThinkingContent(thinking="private", thinking_signature=REASONING_FIELD_SIGNATURE), TextContent(text="Nothing.")]),
    ]
    assert _wire(messages) == [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "Find it."},
        {
            "role": "assistant",
            "content": "Searching.",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "grep", "arguments": '{"pattern": "x"}'},
                    "extra_content": signature,
                }
            ],
            "reasoning_content": "Plan: grep.",
        },
        {"role": "tool", "tool_call_id": "c1", "content": "No matches found"},
        {"role": "user", "content": "background job job-1 finished"},
        # Reasoning is only sent back on the turn that called tools.
        {"role": "assistant", "content": "Nothing."},
    ]


def test_reasoning_written_inside_think_tags_is_not_sent_back() -> None:
    message = AssistantMessage(
        content=[ThinkingContent(thinking="tagged"), ToolCall(id="c1", name="read", arguments={})]
    )
    assert "reasoning_content" not in _wire([message])[1]


def test_failed_tool_results_read_as_errors() -> None:
    result = ToolResultMessage(tool_call_id="c1", tool_name="read", content='cannot read "x": not found', is_error=True)
    assert _wire([result])[1]["content"] == 'Error: cannot read "x": not found'
    already = ToolResultMessage(tool_call_id="c1", tool_name="read", content="Error: boom", is_error=True)
    assert _wire([already])[1]["content"] == "Error: boom"
    empty = ToolResultMessage(tool_call_id="c1", tool_name="read", content="")
    assert _wire([empty])[1]["content"] == "(empty result)"


def test_tool_images_follow_the_tool_results_for_openai_and_ride_inside_for_anthropic() -> None:
    image = ImageContent(data="QUJD", mime_type="image/png")
    turn = [
        AssistantMessage(content=[ToolCall(id="a", name="read_image", arguments={}), ToolCall(id="b", name="read", arguments={})]),
        ToolResultMessage(tool_call_id="a", tool_name="read_image", content=[TextContent(text="image/png image"), image]),
        ToolResultMessage(tool_call_id="b", tool_name="read", content="text"),
        UserMessage(content="next"),
    ]
    openai = _wire(turn)
    assert [message["role"] for message in openai] == ["system", "assistant", "tool", "tool", "user", "user"]
    assert openai[2]["content"] == "image/png image"
    assert openai[4]["content"][1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}
    assert "read_image call a" in openai[4]["content"][0]["text"]

    anthropic = _wire(turn, anthropic=True)
    assert [message["role"] for message in anthropic] == ["system", "assistant", "tool", "tool", "user"]
    assert anthropic[2]["content"][1]["type"] == "image_url"

    blind = _wire(turn, supports_images=False)
    assert blind[2]["content"] == "image/png image\n[image omitted: the model does not accept images]"


def test_an_oversized_text_part_is_cut_before_it_reaches_the_wire() -> None:
    from utils.tokenize import count_tokens

    huge = " ".join(f"w{n}" for n in range(20_000))
    wire = _wire([UserMessage(content=huge)], part_max_tokens=2_000)
    assert count_tokens(wire[1]["content"]) <= 2_000
    assert wire[1]["content"].startswith("w0 w1") and wire[1]["content"].endswith("w19999")


def test_unparsable_tool_arguments_are_sent_back_as_valid_json() -> None:
    message = AssistantMessage(content=[ToolCall(id="c1", name="read", arguments={INVALID_ARGUMENTS_KEY: '{"file_path": '})])
    arguments = _wire([message])[1]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(arguments) == {INVALID_ARGUMENTS_KEY: '{"file_path": '}


# ---------------------------------------------------------------------------
# Stream to events
# ---------------------------------------------------------------------------


async def test_chunks_become_ordered_loop_events() -> None:
    llm = ScriptedLLM(
        [
            [
                think("Let me "),
                think("look."),
                say("Read"),
                say("ing."),
                LLMStreamChunk(content="", tool_call_deltas=[{"index": 0, "id": "c1", "type": "function", "function": {"name": "read", "arguments": '{"file_'}}]),
                LLMStreamChunk(content="", tool_call_deltas=[{"index": 0, "id": "", "type": "function", "function": {"name": "", "arguments": 'path": "a.txt"}'}}]),
                call("glob", {"pattern": "*"}, "c2", index=1),
                finish("tool_calls"),
                usage(120, 30),
            ]
        ]
    )
    events = await _events(_provider(llm))
    assert isinstance(events[0], AssistantStartEvent)
    assert [type(e).__name__ for e in events if isinstance(e, (ThinkingDeltaEvent, TextDeltaEvent))] == [
        "ThinkingDeltaEvent",
        "ThinkingDeltaEvent",
        "TextDeltaEvent",
        "TextDeltaEvent",
    ]
    assert len([e for e in events if isinstance(e, ToolCallStartEvent)]) == 2
    assert [e.delta for e in events if isinstance(e, ToolCallDeltaEvent)] == ['{"file_', 'path": "a.txt"}', '{"pattern": "*"}']
    ends = [e.tool_call for e in events if isinstance(e, ToolCallEndEvent)]
    assert [(c.id, c.name, c.arguments) for c in ends] == [
        ("c1", "read", {"file_path": "a.txt"}),
        ("c2", "glob", {"pattern": "*"}),
    ]
    done = events[-1]
    assert isinstance(done, AssistantDoneEvent) and done.reason == "toolUse"
    message = done.message
    assert message.thinking_text == "Let me look." and message.text == "Reading."
    assert message.content[0].thinking_signature == REASONING_FIELD_SIGNATURE
    assert (message.usage.input, message.usage.output, message.usage.total_tokens) == (120, 30, 150)
    # The request carried the system prompt and no tools.
    assert llm.requests[0][0] == {"role": "system", "content": "SYSTEM"}
    assert llm.overrides[0]["tools"] is None


async def test_finish_reasons_and_broken_tool_calls() -> None:
    plain = await _events(_provider(ScriptedLLM([[say("ok"), finish("stop")]])))
    assert plain[-1].reason == "stop"

    cut = await _events(
        _provider(
            ScriptedLLM(
                [[say("partial"), LLMStreamChunk(content="", tool_call_deltas=[{"index": 0, "id": "c1", "type": "function", "function": {"name": "write", "arguments": '{"file_path": "a", "cont'}}]), finish("length")]]
            )
        )
    )
    # A call cut off by the output limit is not run with half its arguments.
    assert cut[-1].reason == "length" and cut[-1].message.tool_calls == ()

    garbled = await _events(_provider(ScriptedLLM([[call("read", "{not json", "c1"), finish("tool_calls")]])))
    assert garbled[-1].message.tool_calls[0].arguments == {INVALID_ARGUMENTS_KEY: "{not json"}

    nameless = await _events(
        _provider(ScriptedLLM([[LLMStreamChunk(content="", tool_call_deltas=[{"index": 0, "id": "c1", "type": "function", "function": {"name": "", "arguments": "{}"}}]), finish("tool_calls")]]))
    )
    assert nameless[-1].reason == "stop" and nameless[-1].message.tool_calls == ()

    missing_reason = await _events(_provider(ScriptedLLM([[call("read", {"file_path": "a"})]])))
    assert missing_reason[-1].reason == "toolUse"


async def test_a_leading_think_span_in_the_content_is_kept_out_of_the_text() -> None:
    llm = ScriptedLLM([[say("<thi"), say("nk>reason"), say("ing</th"), say("ink>\nAnswer "), say("text <think>literal</think>"), finish()]])
    events = await _events(_provider(llm))
    message = events[-1].message
    assert message.thinking_text == "reasoning"
    assert message.text == "Answer text <think>literal</think>"
    assert message.content[0].thinking_signature is None
    assert "".join(e.delta for e in events if isinstance(e, TextDeltaEvent)) == message.text

    untouched = await _events(_provider(ScriptedLLM([[say("No tag here, just <b>html</b>."), finish()]])))
    assert untouched[-1].message.text == "No tag here, just <b>html</b>."


def test_usage_is_read_from_both_provider_shapes() -> None:
    openai = usage_from_metadata(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "total_tokens": 1050,
            "prompt_tokens_details": {"cached_tokens": 800},
            "completion_tokens_details": {"reasoning_tokens": 20},
        }
    )
    assert (openai.input, openai.cache_read, openai.output, openai.reasoning, openai.total_tokens) == (200, 800, 50, 20, 1050)
    anthropic = usage_from_metadata(
        {
            "input_tokens": 30,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 70,
            "output_tokens": 40,
            "prompt_tokens": 1000,
            "completion_tokens": 40,
            "total_tokens": 1040,
        }
    )
    assert (anthropic.input, anthropic.cache_read, anthropic.cache_write, anthropic.output) == (30, 900, 70, 40)


# ---------------------------------------------------------------------------
# Cancellation and failures
# ---------------------------------------------------------------------------


async def test_cancelling_stops_a_stalled_stream_and_closes_it() -> None:
    async def stall() -> None:
        await asyncio.Event().wait()

    llm = ScriptedLLM([[say("partial"), stall]])
    signal = SimpleCancellationToken()
    provider = _provider(llm)
    stream = provider.stream_response(
        model="fake-model", system="", messages=[UserMessage(content="hi")], tools=[], signal=signal
    )
    collected = []
    started = time.monotonic()

    async def consume() -> None:
        async for event in stream:
            collected.append(event)
            if isinstance(event, TextDeltaEvent):
                # Cancel a little later, when the adapter is already waiting on
                # the stalled stream: no further chunk will ever wake it, so only
                # its own check of the token can end the wait.
                asyncio.get_running_loop().call_later(0.4, signal.cancel)

    await asyncio.wait_for(consume(), 10)
    elapsed = time.monotonic() - started
    final = collected[-1]
    assert isinstance(final, AssistantErrorEvent) and final.reason == "aborted"
    assert final.error.stop_reason == "aborted" and final.error.text == "partial"
    assert llm.closed_streams == 1
    assert elapsed < 5


async def test_a_request_is_not_sent_once_cancelled() -> None:
    llm = ScriptedLLM([[say("never")]])
    signal = SimpleCancellationToken()
    signal.cancel()
    events = await _events(_provider(llm), signal=signal)
    assert isinstance(events[-1], AssistantErrorEvent) and events[-1].reason == "aborted"
    assert llm.requests == []


async def test_provider_failures_become_error_responses_and_overflow_is_raised() -> None:
    failed = await _events(_provider(ScriptedLLM([[RuntimeError("connection reset")]])))
    assert isinstance(failed[-1], AssistantErrorEvent)
    assert failed[-1].error.stop_reason == "error"
    assert failed[-1].error.error_message == "RuntimeError: connection reset"

    with pytest.raises(ContextOverflowError):
        await _events(_provider(ScriptedLLM([[RuntimeError("This model's maximum context length is 4096 tokens")]])))

    # After output started, an overflow-looking failure is an ordinary error:
    # the response cannot be retried transparently any more.
    late = await _events(_provider(ScriptedLLM([[say("some"), RuntimeError("prompt is too long")]])))
    assert isinstance(late[-1], AssistantErrorEvent) and late[-1].error.text == "some"


@pytest.mark.parametrize(
    "message",
    [
        "This model's maximum context length is 128000 tokens. However, you requested 130000 tokens",
        "Error code: 400 - {'error': {'code': 'context_length_exceeded'}}",
        "prompt is too long: 210000 tokens > 200000 maximum",
        "The input token count (40000) exceeds the maximum number of tokens allowed (32768).",
        "InvalidParameter: Range of input length should be [1, 129024]",
        "Please reduce the length of the messages.",
    ],
)
def test_overflow_messages_of_several_providers_are_recognized(message: str) -> None:
    assert is_context_overflow(RuntimeError(message))


def test_other_errors_are_not_mistaken_for_overflow() -> None:
    assert not is_context_overflow(RuntimeError("Rate limit reached for requests"))
    assert not is_context_overflow(RuntimeError("Incorrect API key provided"))

    class Unavailable(RuntimeError):
        status_code = 503

    assert not is_context_overflow(Unavailable("context window service unavailable"))


async def test_complete_returns_the_message_or_raises() -> None:
    provider = _provider(ScriptedLLM([[say("summary text"), finish(), usage(10, 5)], [RuntimeError("boom")]]))
    reply = await provider.complete(system="S", messages=[UserMessage(content="x")], tools=[], max_tokens=321)
    assert reply.text == "summary text" and reply.usage.output == 5
    with pytest.raises(ModelRequestError, match="boom"):
        await provider.complete(system="S", messages=[UserMessage(content="x")], tools=[], max_tokens=10)


# ---------------------------------------------------------------------------
# Through the real client, against a local http endpoint
# ---------------------------------------------------------------------------


async def test_real_client_round_trip_over_plain_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    signature = {"google": {"thought_signature": "c2ln"}}

    def respond(body: dict) -> list[dict]:
        if not any(message["role"] == "tool" for message in body["messages"]):
            return [
                sse_reasoning("I should read the file."),
                sse_text("Reading."),
                sse_tool_call("read", '{"file_path":', "call_9", extra_content=signature),
                sse_tool_call("", ' "a.txt"}', ""),
                sse_finish("tool_calls"),
                sse_usage(50, 12),
            ]
        return [sse_text("It says hello."), sse_finish("stop"), sse_usage(80, 6)]

    from nekoclaw.runtime.loop.tools import AgentTool, AgentToolResult

    async def execute(tool_call_id, arguments, signal=None, on_update=None):  # noqa: ANN001, ANN202
        return AgentToolResult(content="hello")

    tool = AgentTool(
        name="read",
        label="read",
        description="Read a file.",
        parameters={"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]},
        execute_fn=execute,
    )

    with FakeOpenAIServer(respond) as server:
        provider = LLMClientProvider(
            ModelConfig(model="fake-model", base_url=server.base_url, api_key="sk-test-key", max_output_tokens=777)
        )
        try:
            first = [
                event
                async for event in provider.stream_response(
                    model="fake-model", system="SYSTEM", messages=[UserMessage(content="read a.txt")], tools=[tool]
                )
            ]
            assistant = first[-1].message
            assert first[-1].reason == "toolUse"
            assert assistant.thinking_text == "I should read the file."
            tool_call = assistant.tool_calls[0]
            assert (tool_call.id, tool_call.name, tool_call.arguments) == ("call_9", "read", {"file_path": "a.txt"})
            assert json.loads(tool_call.thought_signature) == signature
            assert (assistant.usage.input, assistant.usage.output) == (50, 12)

            history = [
                UserMessage(content="read a.txt"),
                assistant,
                ToolResultMessage(tool_call_id="call_9", tool_name="read", content="hello"),
            ]
            second = [
                event
                async for event in provider.stream_response(
                    model="fake-model", system="SYSTEM", messages=history, tools=[tool]
                )
            ]
            assert second[-1].message.text == "It says hello."
        finally:
            await provider.aclose()

        assert len(server.requests) == 2
        request = server.requests[0]
        assert request["model"] == "fake-model" and request["stream"] is True
        assert request["stream_options"] == {"include_usage": True}
        assert request["max_completion_tokens"] == 777
        assert request["tools"][0]["function"]["name"] == "read"
        assert "temperature" not in request
        # The key travelled in the request, to an http:// endpoint.
        assert server.headers[0]["authorization"] == "Bearer sk-test-key"
        assert server.base_url.startswith("http://127.0.0.1:")
        # What the provider handed out comes back unchanged on the next request.
        replayed = server.requests[1]["messages"][2]
        assert replayed["role"] == "assistant"
        assert replayed["reasoning_content"] == "I should read the file."
        assert replayed["tool_calls"][0]["extra_content"] == signature
        assert replayed["tool_calls"][0]["function"]["arguments"] == '{"file_path": "a.txt"}'
        assert server.requests[1]["messages"][3] == {"role": "tool", "tool_call_id": "call_9", "content": "hello"}
