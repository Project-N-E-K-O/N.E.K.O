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
    is_image_refusal,
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


# ---------------------------------------------------------------------------
# A model that turns out to take no pictures
# ---------------------------------------------------------------------------


class _Rejected(RuntimeError):
    """A provider error that carries the HTTP status it came with."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


IMAGE_REFUSAL = "Invalid content type. image_url is only supported by certain models."


@pytest.mark.parametrize(
    "message",
    [
        IMAGE_REFUSAL,
        "Error code: 400 - {'error': {'message': 'Invalid content type: image_url', 'type': 'invalid_request_error'}}",
        "Failed to deserialize the JSON body into the target type: messages[2]: unknown variant `image_url`, "
        "expected `text` at line 1 column 3181",
        "messages[2].content[1]: expected 'text', found 'image_url'",
        "This model does not support image input.",
        "The model `qwen-plus` doesn't support vision",
        "Model cannot support multimodal messages",
        "Image input is not supported for this model",
        "Vision inputs are not supported by the selected deployment",
        "image input for this model is not enabled",
        "Multi-modal content is not supported",
        "No endpoints found that support image input",
        "当前模型不支持图片输入",
        "该模型暂不支持多模态输入，请更换模型",
        "图像输入暂不支持",
    ],
)
def test_refusals_of_pictures_by_several_providers_are_recognized(message: str) -> None:
    assert is_image_refusal(RuntimeError(message))
    assert is_image_refusal(_Rejected(message, 400))


@pytest.mark.parametrize(
    "message",
    [
        "Failed to download image from the given URL: connection timed out",
        "Image size exceeds the limit of 20 MB",
        "Invalid image data: the base64 string could not be decoded",
        "You uploaded an unsupported image. Please make sure your image has of one the following formats: "
        "['png', 'jpeg', 'gif', 'webp'].",
        "image_url must be a valid URL or a data URL",
        "The image could not be processed, please try again",
        "Too many images in the request: at most 20 are allowed",
        "No endpoints found that support tool use",
        "No endpoints found matching your data policy",
        "图片下载失败，请稍后重试",
        "Rate limit reached for requests",
        "This model's maximum context length is 128000 tokens",
        "Incorrect API key provided",
    ],
)
def test_errors_that_merely_mention_a_picture_are_not_taken_for_a_refusal(message: str) -> None:
    assert not is_image_refusal(RuntimeError(message))
    assert not is_image_refusal(_Rejected(message, 400))


@pytest.mark.parametrize(("status", "expected"), [(400, True), (404, True), (415, True), (422, True), (401, False), (429, False), (500, False), (503, False)])
def test_a_refusal_of_pictures_comes_with_a_status_that_blames_the_request(status: int, expected: bool) -> None:
    assert is_image_refusal(_Rejected(IMAGE_REFUSAL, status)) is expected


def _with_a_screenshot() -> list:
    """A conversation whose last tool result carries a picture."""
    return [
        UserMessage(content="what is on the screen"),
        AssistantMessage(content=[ToolCall(id="c1", name="computer", arguments={"action": "screenshot"})]),
        ToolResultMessage(
            tool_call_id="c1",
            tool_name="computer",
            content=[TextContent(text="the desktop"), ImageContent(data="QUJD", mime_type="image/jpeg")],
        ),
    ]


def _pictures(request: list[dict]) -> int:
    """How many pictures one request carries."""
    return sum(
        1
        for message in request
        if isinstance(message["content"], list)
        for part in message["content"]
        if part["type"] == "image_url"
    )


def _seeing(llm: ScriptedLLM) -> tuple[LLMClientProvider, list[str]]:
    """A provider for a model configured to see pictures, and the list its refusal callback writes to."""
    told: list[str] = []
    provider = _provider(llm, supports_images=True)
    provider.on_images_refused = lambda: told.append("refused")
    return provider, told


async def test_a_request_refused_for_its_pictures_is_sent_once_more_without_them() -> None:
    llm = ScriptedLLM(
        [[_Rejected(IMAGE_REFUSAL, 400)], [say("A desktop."), finish(), usage(90, 4)], [say("Still a desktop."), finish()]]
    )
    provider, told = _seeing(llm)
    assert provider.supports_images is True

    events = await _events(provider, _with_a_screenshot())

    # The loop gets the answer to the second request and nothing of the first.
    assert [type(event).__name__ for event in events if isinstance(event, (AssistantStartEvent, AssistantErrorEvent))] == [
        "AssistantStartEvent"
    ]
    done = events[-1]
    assert isinstance(done, AssistantDoneEvent) and done.message.text == "A desktop."
    assert done.message.usage.input == 90
    # Sent twice: with the picture, then with a placeholder in its place and all else the same.
    assert [_pictures(request) for request in llm.requests] == [1, 0]
    assert llm.requests[0][:3] == llm.requests[1][:3]
    assert llm.requests[1][-1] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "the desktop\n[image omitted: the model does not accept images]",
    }
    assert llm.overrides[0] == llm.overrides[1]
    # The refused stream was closed, and whoever configured the model was told once.
    assert llm.closed_streams == 2
    assert told == ["refused"]
    assert provider.supports_images is False

    later = await _events(provider, _with_a_screenshot())

    # From here on no request carries a picture, and nobody is told again.
    assert later[-1].message.text == "Still a desktop."
    assert [_pictures(request) for request in llm.requests] == [1, 0, 0]
    assert told == ["refused"]


class _Stream:
    """One response of ``_Streams``: a plain iterator, so that closing it can be seen."""

    def __init__(self, owner: "_Streams", number: int, steps: list) -> None:
        self._owner = owner
        self._number = number
        self._steps = steps

    def __aiter__(self) -> "_Stream":
        return self

    async def __anext__(self):  # noqa: ANN204
        if not self._steps:
            raise StopAsyncIteration
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    async def aclose(self) -> None:
        self._owner.log.append(f"stream {self._number} closed")


class _Streams:
    """A client that writes down, in order, each request it gets and each stream that is closed."""

    def __init__(self, turns: list[list]) -> None:
        self._turns = list(turns)
        self.log: list[str] = []

    def astream(self, messages: list[dict], **overrides) -> _Stream:  # noqa: ANN003
        del messages, overrides
        number = sum(1 for line in self.log if line.endswith("requested")) + 1
        self.log.append(f"stream {number} requested")
        return _Stream(self, number, self._turns.pop(0))


async def test_the_refused_stream_is_closed_before_the_request_is_sent_again() -> None:
    client = _Streams([[RuntimeError(IMAGE_REFUSAL)], [say("A desktop."), finish()]])

    async def factory() -> _Streams:
        return client

    provider = LLMClientProvider(ModelConfig(model="fake-model", supports_images=True), client_factory=factory)

    events = await _events(provider, _with_a_screenshot())

    assert events[-1].message.text == "A desktop."
    # The connection of the refused request is not left open behind the second one.
    assert client.log == ["stream 1 requested", "stream 1 closed", "stream 2 requested", "stream 2 closed"]


async def test_a_one_shot_request_refused_for_its_pictures_is_sent_again_too() -> None:
    llm = ScriptedLLM([[RuntimeError(IMAGE_REFUSAL)], [say("summary text"), finish()]])
    provider, told = _seeing(llm)

    reply = await provider.complete(system="S", messages=_with_a_screenshot(), tools=[], max_tokens=64)

    assert reply.text == "summary text"
    assert [_pictures(request) for request in llm.requests] == [1, 0]
    assert told == ["refused"]


async def test_a_refusal_is_an_ordinary_failure_when_the_request_carried_no_picture() -> None:
    llm = ScriptedLLM([[RuntimeError(IMAGE_REFUSAL)], [say("never sent")]])
    provider, told = _seeing(llm)

    events = await _events(provider, [UserMessage(content="hi")])

    assert isinstance(events[-1], AssistantErrorEvent)
    assert events[-1].error.error_message == f"RuntimeError: {IMAGE_REFUSAL}"
    assert len(llm.requests) == 1
    assert told == [] and provider.supports_images is True


async def test_a_model_configured_without_pictures_is_never_sent_one_and_never_asked_again() -> None:
    llm = ScriptedLLM([[RuntimeError(IMAGE_REFUSAL)], [say("never sent")]])
    told: list[str] = []
    provider = _provider(llm)
    provider.on_images_refused = lambda: told.append("refused")
    assert provider.supports_images is False

    events = await _events(provider, _with_a_screenshot())

    # The picture was a placeholder already, so the error has another cause.
    assert isinstance(events[-1], AssistantErrorEvent)
    assert [_pictures(request) for request in llm.requests] == [0]
    assert told == []


async def test_a_refusal_after_output_began_is_not_sent_again() -> None:
    llm = ScriptedLLM([[say("I see "), RuntimeError(IMAGE_REFUSAL)], [say("never sent")]])
    provider, told = _seeing(llm)

    events = await _events(provider, _with_a_screenshot())

    # What was said cannot be taken back, so the response fails as it stands.
    assert isinstance(events[-1], AssistantErrorEvent) and events[-1].error.text == "I see "
    assert len(llm.requests) == 1
    assert told == [] and provider.supports_images is True


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("connection reset by peer"),
        _Rejected("Rate limit reached for requests", 429),
        # The words of a refusal, with a status that says the provider is at fault.
        _Rejected(IMAGE_REFUSAL, 503),
        RuntimeError("Failed to download image from the given URL"),
    ],
)
async def test_a_failure_of_another_kind_is_not_sent_again_and_pictures_stay(error: Exception) -> None:
    llm = ScriptedLLM([[error], [say("A desktop."), finish()]])
    provider, told = _seeing(llm)

    events = await _events(provider, _with_a_screenshot())

    assert isinstance(events[-1], AssistantErrorEvent)
    assert len(llm.requests) == 1
    assert told == [] and provider.supports_images is True
    # The next request carries its pictures as before.
    again = await _events(provider, _with_a_screenshot())
    assert again[-1].message.text == "A desktop."
    assert _pictures(llm.requests[1]) == 1


async def test_a_request_refused_again_without_its_pictures_fails_and_is_not_sent_a_third_time() -> None:
    llm = ScriptedLLM([[RuntimeError(IMAGE_REFUSAL)], [RuntimeError(IMAGE_REFUSAL)], [say("never sent")]])
    provider, told = _seeing(llm)

    events = await _events(provider, _with_a_screenshot())

    assert isinstance(events[-1], AssistantErrorEvent)
    assert [_pictures(request) for request in llm.requests] == [1, 0]
    assert told == ["refused"]


async def test_nobody_to_tell_and_a_teller_that_fails_do_not_stop_the_second_request() -> None:
    def broken() -> None:
        raise RuntimeError("the host is gone")

    for callback in (None, broken):
        llm = ScriptedLLM([[RuntimeError(IMAGE_REFUSAL)], [say("A desktop."), finish()]])
        provider = _provider(llm, supports_images=True)
        provider.on_images_refused = callback

        events = await _events(provider, _with_a_screenshot())

        assert isinstance(events[-1], AssistantDoneEvent) and events[-1].message.text == "A desktop."
        assert provider.supports_images is False


def test_the_refusal_callback_can_be_given_when_the_provider_is_built() -> None:
    told: list[str] = []

    async def factory() -> ScriptedLLM:
        return ScriptedLLM()

    provider = LLMClientProvider(
        ModelConfig(model="fake-model", supports_images=True),
        client_factory=factory,
        on_images_refused=lambda: told.append("refused"),
    )

    provider._images_refused()

    assert told == ["refused"] and provider.supports_images is False


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
