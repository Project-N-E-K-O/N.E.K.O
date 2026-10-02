"""The screen-history request view reaches every provider call site.

Only the provider transport is faked (``self.llm.astream``, or the genai
``generate_content_stream``). ``stream_text`` / ``prompt_ephemeral``, the
visible filter, the tool loop and the executor are production code, so a
provider call site that sends ``messages`` instead of the request view turns
these red.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import main_logic.omni_offline_client._genai_support as _ofc_genai
from main_logic.tool_calling import ToolDefinition, ToolImage, ToolResult
from tests.unit.test_offline_provider_frame_publish import _make_client, _png_b64
from tests.unit.test_tool_calling import (
    _GenaiChunk, _GenaiFunctionCall, _GenaiPart,
)
from utils.llm_client import AIMessage, HumanMessage, LLMStreamChunk
from utils.screen_comment_guard import project_screen_history

pytestmark = pytest.mark.unit

_COMMENT_A = "屏幕搭话 蓝色小车停在一棵大树旁边，树叶的影子落在了车顶上。"
_COMMENT_B = "屏幕搭话 远处的红色小车正在缓慢经过桥面，桥下的河水十分平静。"
_BODY_A = _COMMENT_A[len("屏幕搭话 "):]


def _text(content, finish=None):
    return LLMStreamChunk(content=content, finish_reason=finish)


def _tool_calls(*ids):
    return LLMStreamChunk(content="", finish_reason="tool_calls", tool_call_deltas=[
        {"index": i, "id": call_id, "type": "function",
         "function": {"name": "lookup", "arguments": "{}"}}
        for i, call_id in enumerate(ids)
    ])


def _client(provider="openai", *, handler=None, cap=2):
    """A stream_text/prompt_ephemeral-capable client over the real tool loop.

    ``client.script`` lists one provider response per request: a list of
    chunks, or an exception raised before the first chunk. ``client.requests``
    records what each request carried: OpenAI messages, or genai ``contents``.
    """
    client, _ = _make_client()
    del client._astream_visible_with_tools  # the real filter + tool loop
    client._publish_provider_frames = MagicMock()
    # Bus copies are out of scope; close them so none is left un-awaited.
    client._fire_bus_task = lambda coro: coro.close()
    client.master_name = "M"
    client.lanlan_name = "L"
    client.enable_response_guard = False
    client._recent_responses = []
    client._max_recent_responses = 5
    client._repetition_threshold = 0.8
    client.on_text_delta = AsyncMock()
    client.on_response_done = AsyncMock()
    client.on_proactive_done = AsyncMock()
    client._notify_reasoning_done = AsyncMock()
    client.max_tool_iterations = cap
    client.on_tool_call = handler
    client.on_tool_round_start = None
    client._tool_definitions = [ToolDefinition(
        name="lookup", description="lookup",
        parameters={"type": "object", "properties": {}},
    )]
    client._openai_tools_unsupported = False
    client._openai_tools_unsupported_with_images = False
    client._genai_tools_unsupported = False
    client._use_genai_sdk = provider == "gemini"
    client._genai_client = None
    client.script = []
    client.requests = []

    def next_step():
        steps = client.script
        return steps[min(len(client.requests) - 1, len(steps) - 1)]

    async def play(step):
        for item in step:
            yield item

    def astream(messages, **kwargs):
        client.requests.append(list(messages))
        step = next_step()

        async def run():
            if isinstance(step, BaseException):
                raise step
            async for chunk in play(step):
                yield chunk
        return run()

    client.llm = SimpleNamespace(astream=astream, max_completion_tokens=100)
    if provider == "gemini":
        client.model = "gemini-2.5-flash"
        client.api_key = "fake"
        client._ensure_genai_client = lambda: None

        class _Models:
            @staticmethod
            async def generate_content_stream(**kwargs):
                client.requests.append(kwargs["contents"])
                return play(next_step())

        client._genai_client = SimpleNamespace(aio=SimpleNamespace(models=_Models()))
    return client


@pytest.fixture(autouse=True)
def _genai_available(monkeypatch):
    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", MagicMock())


def _poisoned(client):
    client._conversation_history += [
        HumanMessage(content="聊"), AIMessage(content=_COMMENT_A), AIMessage(content=_COMMENT_B),
    ]


def _assert_rewritten(payload):
    text = json.dumps(payload, ensure_ascii=False, default=repr)
    assert "屏幕搭话" not in text, "no source label reaches the provider"
    assert "红色小车" not in text, "the second comment is left out"
    assert _BODY_A in text, "the first comment stays, unlabelled"


async def _noop_tool(call):
    return ToolResult(call_id=call.call_id, name=call.name, output={})


@pytest.mark.parametrize("with_image", [False, True])
async def test_openai_tool_loop_and_forced_final_both_send_the_request_view(with_image):
    """Request 1 is the tool loop, request 2 the forced-final call (cap=1).
    A tool image appends a {"role": "user"} turn in place; the forced-final
    request must still find the run before the real user turn."""
    image = ToolImage(data_b64=_png_b64(4, 4, (1, 2, 3)), mime="image/png")

    async def handler(call):
        return ToolResult(call_id=call.call_id, name=call.name, output={},
                          images=[image] if with_image else [])

    client = _client(handler=handler, cap=1)
    _poisoned(client)
    client.script = [[_tool_calls("c1")], [_text("好"), _text("", "stop")]]
    await client.stream_text("继续")

    assert len(client.requests) == 2
    for payload in client.requests:
        _assert_rewritten(payload)
    assert AIMessage(content=_COMMENT_A) in client._conversation_history, "saved as is"
    assert AIMessage(content=_COMMENT_B) in client._conversation_history, "saved as is"


async def test_tools_refusal_retry_sends_the_request_view():
    client = _client(handler=_noop_tool)
    _poisoned(client)
    client.script = [RuntimeError("this model does not support tools"),
                     [_text("好"), _text("", "stop")]]
    await client.stream_text("继续")
    assert len(client.requests) == 2
    for payload in client.requests:
        _assert_rewritten(payload)


async def test_gemini_tool_loop_and_forced_final_both_send_the_request_view():
    client = _client("gemini", handler=_noop_tool, cap=1)
    _poisoned(client)
    client.script = [
        [_GenaiChunk([_GenaiPart(function_call=_GenaiFunctionCall("lookup", id_="c1"))])],
        [_GenaiChunk([_GenaiPart(text="ok")])],
    ]
    await client.stream_text("go on")

    assert len(client.requests) == 2
    for contents in client.requests:
        _assert_rewritten(contents)


async def test_persisted_ephemeral_replies_are_marked_as_independent_deliveries():
    """Callbacks and greetings answer an instruction, not the user, so the
    guard must not join them with the reply to the user's turn."""
    client = _client()
    client._conversation_history += [HumanMessage(content="聊"), AIMessage(content="正常回复。")]
    for comment in (_COMMENT_A, _COMMENT_B):
        client.script = [[_text(comment), _text("", "stop")]]
        client.requests.clear()
        assert await client.prompt_ephemeral("callback")
    delivered = client._conversation_history[-2:]
    assert [m.additional_kwargs for m in delivered] == [{"dialog_source": "proactive"}] * 2
    messages = client._conversation_history + [HumanMessage(content="继续")]
    # Never cut as one chain: each delivery keeps its comment, unlabelled.
    projected = [m.content for m in project_screen_history(messages)]
    assert projected[-3:] == [_BODY_A, _COMMENT_B[len("屏幕搭话 "):], "继续"]


async def test_text_streamed_this_turn_reaches_the_next_tool_round_uncut():
    """The tool loop appends this turn's streamed text with its tool calls;
    the user already saw every comment in it, so the next request keeps them
    all (no cut, or the model says one again) and only drops the labels,
    while older history is still cut."""
    client = _client(handler=_noop_tool)
    _poisoned(client)
    client.script = [[_text(_COMMENT_A + _COMMENT_B), _tool_calls("c1")],
                     [_text("好"), _text("", "stop")]]
    await client.stream_text("继续")

    assert len(client.requests) == 2
    current_turn = json.dumps(client.requests[1][-2:], ensure_ascii=False, default=repr)
    assert _BODY_A in current_turn and _COMMENT_B[len("屏幕搭话 "):] in current_turn, current_turn
    assert "屏幕搭话" not in json.dumps(client.requests[1], ensure_ascii=False, default=repr)
    history = client.requests[1][:-3]
    assert not [m for m in history if "红色小车" in json.dumps(m, ensure_ascii=False, default=repr)]
