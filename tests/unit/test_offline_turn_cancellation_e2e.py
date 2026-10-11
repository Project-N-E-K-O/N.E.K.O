"""Whole offline turns: cancellation, history commits and the request view.

Only the provider transport is faked (``self.llm.astream``, or the genai
``generate_content_stream``). ``stream_text`` / ``prompt_ephemeral``, the
visible filter, the tool loop and the executor are production code, so a lost
``_response_generation`` hop, a provider call site that sends ``messages``
instead of the request view, or a history commit in the wrong place turns
these red. Each test says which of those it pins.
"""
import asyncio
import json
import queue
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import main_logic.omni_offline_client._genai_support as _ofc_genai
import main_logic.omni_offline_client._streaming as _ofc_streaming
from main_logic.core.turn import TurnMixin
from main_logic.tool_calling import ToolDefinition, ToolImage, ToolResult
from tests.unit.test_offline_provider_frame_publish import _make_client, _png_b64
from tests.unit.test_tool_calling import (
    _GenaiChunk, _GenaiFunctionCall, _GenaiPart,
)
from utils.llm_client import AIMessage, HumanMessage, LLMStreamChunk

pytestmark = pytest.mark.unit


def _text(content, finish=None):
    return LLMStreamChunk(content=content, finish_reason=finish)


def _tool_calls(*ids, text=""):
    return LLMStreamChunk(content=text, finish_reason="tool_calls", tool_call_deltas=[
        {"index": i, "id": call_id, "type": "function",
         "function": {"name": "lookup", "arguments": "{}"}}
        for i, call_id in enumerate(ids)
    ])


def _client(provider="openai", *, handler=None, cap=2):
    """A stream_text/prompt_ephemeral-capable client over the real tool loop.

    ``client.script`` lists one provider response per request: a list of
    chunks (callables are awaited in place, to cancel mid-stream) or an
    exception raised before the first chunk. ``client.requests`` records what
    each request carried: OpenAI messages, or genai ``contents``.
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
    client._user_language_provider = lambda: "zh"
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
            if callable(item):
                await item()
            else:
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


def _gemini_calls(*ids, text=None):
    parts = [_GenaiPart(text=text)] if text else []
    parts += [_GenaiPart(function_call=_GenaiFunctionCall("lookup", id_=i)) for i in ids]
    return _GenaiChunk(parts)


def _history_shape(client):
    shape = []
    for message in client._conversation_history[1:]:
        if isinstance(message, dict):
            shape.append((message["role"], message.get("content"),
                          [c["id"] for c in message.get("tool_calls", [])] or None))
        else:
            shape.append((message.type, message.content, None))
    return shape


def _emitted(client):
    return [call.args[0] for call in client.on_text_delta.await_args_list]


# ── History commits on cancellation ─────────────────────────────────────────

async def test_cancel_after_emitting_keeps_the_visible_reply_in_its_own_turn():
    """Text the user already saw stays in history, before the turn that
    cancelled it; nothing produced after the cancellation is emitted."""
    client = _client()

    async def interrupt():
        await client.handle_interruption()
        client._conversation_history.append(HumanMessage(content="Q2"))

    client.script = [[
        _text("Hello there, I was about to "), _text("say something long"),
        interrupt, _text(" and then some"), _text("", "stop"),
    ]]
    await client.stream_text("Q1")

    assert _emitted(client) == ["Hello there, I was about to ", "say something long"]
    assert _history_shape(client) == [
        ("human", "Q1", None),
        ("ai", "Hello there, I was about to say something long", None),
        ("human", "Q2", None),
    ]
    client.on_response_done.assert_not_awaited()


@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("entry", ["stream_text", "prompt_ephemeral"])
async def test_cancel_during_a_tool_keeps_the_executed_call_and_stops(provider, entry):
    """The e2e token wiring: stream_text / prompt_ephemeral hand their
    generation down to the tool loop. Cancelled inside the first tool, the
    provider is asked exactly once, the second call never runs, and the call
    that did run keeps its record (it already had its side effect)."""
    executed = []

    async def handler(call):
        executed.append(call.call_id)
        await client.handle_interruption()
        return ToolResult(call_id=call.call_id, name=call.name, output={"sent": True})

    client = _client(provider, handler=handler)
    if provider == "gemini":
        client.script = [[_gemini_calls("c1", "c2", text="好的，我这就发")]]
    else:
        client.script = [[_text("好的，我这就发"), _tool_calls("c1", "c2")]]
    if entry == "stream_text":
        await client.stream_text("帮我发消息")
    else:
        assert await client.prompt_ephemeral("callback") is True

    assert executed == ["c1"]
    assert len(client.requests) == 1, "no provider request after the cancellation"
    client.on_response_done.assert_not_awaited()
    client.on_proactive_done.assert_not_awaited()
    if entry == "stream_text":
        assert _history_shape(client) == [
            ("human", "帮我发消息", None),
            ("assistant", "好的，我这就发", ["c1"]),
            ("tool", json.dumps({"sent": True}), None),
        ], "the pre-tool text is committed once, inside the kept round"


@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_cancel_before_any_call_commits_the_pretool_text_as_the_reply(provider):
    """No call ran: the round is dropped, the text the user heard is not."""
    executed = []

    async def handler(call):
        executed.append(call.call_id)
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    client = _client(provider, handler=handler)
    client.on_tool_round_start = client.handle_interruption
    if provider == "gemini":
        client.script = [[_gemini_calls("c1", text="好的，我这就发")]]
    else:
        client.script = [[_text("好的，我这就发"), _tool_calls("c1")]]
    await client.stream_text("帮我发消息")

    assert executed == []
    assert len(client.requests) == 1
    assert _history_shape(client) == [
        ("human", "帮我发消息", None), ("ai", "好的，我这就发", None),
    ]


async def test_replacement_and_tool_image_slots_survive_a_shifted_history():
    """A concurrent commit that inserts before this turn moves its messages;
    the long prompt must still become the short replacement and the tool
    image's base64 must still be swapped out, found by identity."""
    image = ToolImage(data_b64=_png_b64(4, 4, (10, 20, 30)), mime="image/png")

    async def handler(call):
        # Another turn's late commit lands ahead of this one.
        client._conversation_history.insert(1, AIMessage(content="late"))
        return ToolResult(call_id=call.call_id, name=call.name, output={}, images=[image])

    client = _client(handler=handler)
    client.script = [[_tool_calls("c1")], [_text("看到了"), _text("", "stop")]]
    await client.stream_text("long prompt " * 20, history_replacement_text="short")

    history = client._conversation_history
    assert [m.content for m in history if isinstance(m, HumanMessage)] == ["short"]
    assert image.data_b64 not in json.dumps(
        [m if isinstance(m, dict) else m.content for m in history], ensure_ascii=False,
    )
    assert len(client.requests) == 2
    assert image.data_b64 in json.dumps(client.requests[1], ensure_ascii=False, default=repr)


# ── The request view reaches every provider call site ───────────────────────

def _with_pending_round(client):
    """Seed another turn's tool round that is still executing (or was cut
    mid-batch): it sits in the shared history with its call unanswered.
    Returns that assistant turn."""
    pending = {"role": "assistant", "content": "我查一下", "tool_calls": [{
        "id": "pending", "type": "function",
        "function": {"name": "pending_lookup", "arguments": "{}"},
    }]}
    client._conversation_history += [HumanMessage(content="查一下"), pending]
    return pending


def _assert_paired(payload):
    text = json.dumps(payload, ensure_ascii=False, default=repr)
    assert "pending_lookup" not in text, "the unanswered call reached the provider"
    assert "我查一下" in text, "its text stays as a plain assistant turn"


@pytest.mark.parametrize("with_image", [False, True])
async def test_openai_tool_loop_and_forced_final_both_drop_an_unanswered_round(with_image):
    """Request 1 is the tool loop, request 2 the forced-final call (cap=1).
    A tool image appends a {"role": "user"} turn in place."""
    image = ToolImage(data_b64=_png_b64(4, 4, (1, 2, 3)), mime="image/png")

    async def handler(call):
        return ToolResult(call_id=call.call_id, name=call.name, output={},
                          images=[image] if with_image else [])

    client = _client(handler=handler, cap=1)
    pending = _with_pending_round(client)
    client.script = [[_tool_calls("c1")], [_text("好"), _text("", "stop")]]
    await client.stream_text("继续")

    assert len(client.requests) == 2
    for payload in client.requests:
        _assert_paired(payload)
    assert any(m is pending for m in client._conversation_history), "saved as is"
    assert pending["tool_calls"][0]["id"] == "pending"


async def _noop_tool(call):
    return ToolResult(call_id=call.call_id, name=call.name, output={})


async def test_tools_refusal_retry_drops_an_unanswered_round():
    client = _client(handler=_noop_tool)
    _with_pending_round(client)
    client.script = [RuntimeError("this model does not support tools"),
                     [_text("好"), _text("", "stop")]]
    await client.stream_text("继续")
    assert len(client.requests) == 2
    for payload in client.requests:
        _assert_paired(payload)


async def test_gemini_tool_loop_and_forced_final_both_drop_an_unanswered_round():
    async def handler(call):
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    client = _client("gemini", handler=handler, cap=1)
    _with_pending_round(client)
    client.script = [[_gemini_calls("c1")], [_GenaiChunk([_GenaiPart(text="ok")])]]
    await client.stream_text("go on")

    assert len(client.requests) == 2
    for contents in client.requests:
        _assert_paired(contents)


# ── Individual cancellation checks the loops rely on ────────────────────────

async def test_cancel_during_tools_refusal_prevents_the_retry():
    client = _client(handler=_noop_tool)

    async def refuse():
        await client.handle_interruption()
        raise RuntimeError("this model does not support tools")

    client.script = [[refuse]]
    await client.stream_text("hi")
    assert len(client.requests) == 1


@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("cap", [1, 2])
async def test_cancel_while_the_round_sentinel_is_consumed_stops_the_next_request(
    provider, cap,
):
    """A round ends at the sentinel. A cancellation while the consumer handles
    it must stop the next request: the forced-final one after the last
    allowed round (cap=1), the next loop round otherwise (cap=2)."""
    async def handler(call):
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    client = _client(provider, handler=handler, cap=cap)
    client.script = ([[_gemini_calls("c1")], [_GenaiChunk([_GenaiPart(text="late")])]]
                     if provider == "gemini"
                     else [[_tool_calls("c1")], [_text("late"), _text("", "stop")]])
    generation = client._begin_response_generation()
    seen = []
    async for chunk in client._astream_with_tools(
        [HumanMessage(content="hi")], _response_generation=generation,
    ):
        seen.append(chunk)
        if getattr(chunk, "tool_round_persisted", False):
            await client.cancel_response()
    assert len(client.requests) == 1
    assert not [c for c in seen if getattr(c, "content", "")]


@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_cancel_between_tool_calls_stops_the_batch(provider):
    executed = []

    async def handler(call):
        executed.append(call.call_id)
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    client = _client(provider, handler=handler)
    client._on_tool_result = None
    client.script = ([[_gemini_calls("c1", "c2")]] if provider == "gemini"
                     else [[_tool_calls("c1", "c2")]])
    real = client.on_tool_call

    async def cancelling(call):
        result = await real(call)
        await client.cancel_response()
        return result

    client.on_tool_call = cancelling
    generation = client._begin_response_generation()
    messages = [HumanMessage(content="hi")]
    _ = [c async for c in client._astream_with_tools(messages, _response_generation=generation)]
    assert executed == ["c1"]
    assert [m["tool_call_id"] for m in messages if isinstance(m, dict) and m["role"] == "tool"] == ["c1"]
    await asyncio.sleep(0)


# ── Where a cancelled reply lands (second review round) ─────────────────────

async def test_cancelled_proactive_reply_goes_before_the_interrupting_user_turn():
    """prompt_ephemeral's instruction is never saved, so the reply is anchored
    to the last message the turn saw; the interrupter's HumanMessage that was
    appended meanwhile stays after it."""
    client = _client()
    client._conversation_history.append(HumanMessage(content="earlier"))

    async def interrupt():
        await client.handle_interruption()
        client._conversation_history.append(HumanMessage(content="Q-new"))

    client.script = [[_text("刚才看到"), interrupt, _text("late"), _text("", "stop")]]
    assert await client.prompt_ephemeral("callback") is True
    assert _history_shape(client) == [
        ("human", "earlier", None), ("ai", "刚才看到", None), ("human", "Q-new", None),
    ]


async def test_cancel_during_the_prefix_flush_commits_before_the_interrupter():
    """A short reply sits whole in the name-prefix buffer and is emitted by
    the end-of-stream flush; a cancellation during that emit is caught at
    the commit, not only right after the stream loop."""
    client = _client()
    client._prefix_buffer_size = 100

    async def on_text_delta(text, is_first, **_kw):
        await client.handle_interruption()
        client._conversation_history.append(HumanMessage(content="Q2"))

    client.on_text_delta = AsyncMock(side_effect=on_text_delta)
    client.script = [[_text("短回复"), _text("", "stop")]]
    await client.stream_text("Q1")
    assert _history_shape(client) == [
        ("human", "Q1", None), ("ai", "短回复", None), ("human", "Q2", None),
    ]
    # No longer live at its commit: kept out of the repetition check too.
    assert client._recent_responses == []
    client.on_response_done.assert_not_awaited()


_OVER_BUDGET = "先说一句。再说第二句，然后还有很多很多没说完的话"


def _length_guarded_client(held_by):
    """A client whose whole reply is still held at the end of the stream, so
    the end-of-stream flush is where the length guard trips and where the
    recovery is first emitted: a reply shorter than the name-prefix buffer,
    or a Focus turn on a leak-prone model that never closed a think block."""
    client = _client()
    client.enable_response_guard = True
    client.max_response_length = 8
    if held_by == "think_stripper":
        client.model = "qwen3.5-plus"
    else:
        client._prefix_buffer_size = 100
    client.script = [[_text(_OVER_BUDGET), _text("", "stop")]]
    return client, held_by == "think_stripper"


@pytest.mark.parametrize("held_by", ["name_prefix", "think_stripper"])
async def test_a_length_recovery_cut_in_the_flush_commits_before_the_interrupter(held_by):
    """The recovery written by the length guard decides where it goes at the
    moment it writes, like the regular commit: cancelled during the flush's
    emit, it lands before the interrupter's message and stays out of the
    repetition check."""
    client, thinking_on = _length_guarded_client(held_by)

    async def on_text_delta(text, is_first, **_kw):
        await client.handle_interruption()
        client._conversation_history.append(HumanMessage(content="Q2"))

    client.on_text_delta = AsyncMock(side_effect=on_text_delta)
    await client.stream_text("Q1", thinking_on=thinking_on)
    assert _emitted(client) == ["先说一句。"]
    assert _history_shape(client) == [
        ("human", "Q1", None), ("ai", "先说一句。", None), ("human", "Q2", None),
    ]
    assert client._recent_responses == []
    client.on_response_done.assert_not_awaited()


@pytest.mark.parametrize("held_by", ["name_prefix", "think_stripper"])
async def test_a_live_length_recovery_from_the_flush_is_appended_and_counted(held_by):
    client, thinking_on = _length_guarded_client(held_by)
    await client.stream_text("Q1", thinking_on=thinking_on)
    assert _emitted(client) == ["先说一句。"]
    assert _history_shape(client) == [("human", "Q1", None), ("ai", "先说一句。", None)]
    assert client._recent_responses == ["先说一句。"]
    client.on_response_done.assert_awaited_once()


@pytest.mark.parametrize("content", ["", "   "])
def test_an_empty_cancelled_reply_is_never_written(content):
    """Some providers reject an empty assistant message."""
    client = _client()
    anchor = HumanMessage(content="Q1")
    client._conversation_history.append(anchor)
    before = list(client._conversation_history)
    client._commit_cancelled_reply(anchor, AIMessage(content=content), 1)
    assert client._conversation_history == before


# ── What handle_interruption reports (third review round) ───────────────────

async def test_interruption_reports_false_when_nothing_is_live():
    client = _client()
    assert await client.handle_interruption() == ""


async def test_interruption_reports_true_for_a_live_stream():
    client = _client()
    seen = []

    async def interrupt():
        seen.append(await client.handle_interruption())

    client.script = [[_text("hi"), interrupt, _text("", "stop")]]
    await client.stream_text("Q1")
    assert seen == ["response"]


async def test_interruption_claims_a_finished_reply_awaiting_its_completion():
    """Finished and committed, still in the cleanup await: the interruption
    claims the completion (the interrupting turn closes it) instead of a late
    callback landing inside the new turn. The reply was delivered, so the
    call still reports True."""
    client = _client()
    seen = []

    async def cleanup(_owner):
        seen.append(await client.handle_interruption())

    client._notify_reasoning_done = cleanup
    client.script = [[_text("说完了。"), _text("", "stop")]]
    assert await client.prompt_ephemeral("callback", completion_mode="response") is True
    assert seen == ["response"]
    client.on_response_done.assert_not_awaited()


async def test_interruption_leaves_a_running_completion_alone():
    """Once the completion callback started, it owns the turn end: the
    interruption must report False so no second turn end is sent."""
    client = _client()
    seen = []

    async def proactive_done(_committed):
        seen.append(await client.handle_interruption())

    client.on_proactive_done = AsyncMock(side_effect=proactive_done)
    client.script = [[_text("说完了。"), _text("", "stop")]]
    assert await client.prompt_ephemeral("callback") is True
    assert seen == [""]
    client.on_proactive_done.assert_awaited_once()


async def test_a_turn_cancelled_before_its_first_chunk_still_publishes_its_frames():
    """stream_text publishes the turn's frames on the first chunk the
    provider sends. Cancelled while the request was in flight, the tool loop
    still hands that first (empty) chunk up, so the frames the provider did
    receive reach the plugin bus; nothing is shown."""
    client = _client()

    async def interrupt():
        await client.handle_interruption()

    client.script = [[interrupt, _text("late"), _text("", "stop")]]
    await client.stream_text("look", turn_images=[_png_b64(4, 4, (9, 9, 9))])
    client._publish_provider_frames.assert_called_once()
    assert _emitted(client) == []


# ── Request view pairs tool rounds (third review round) ─────────────────────

def _call(call_id):
    return {"id": call_id, "type": "function",
            "function": {"name": "lookup", "arguments": "{}"}}


def _reply(call_id):
    return {"role": "tool", "tool_call_id": call_id, "name": "lookup", "content": "{}"}


def test_an_unanswered_tool_call_round_is_dropped_from_the_request_view():
    """Round A still waits on a slow tool when turn B builds its request:
    [.., A.assistant(tool_calls), B.user] is a 400 on OpenAI-compatible
    endpoints. The request view keeps A's text and drops the dangling call;
    the saved history is untouched."""
    client = _client()
    pending = {"role": "assistant", "content": "我查一下", "tool_calls": [_call("c1")]}
    messages = [HumanMessage(content="A"), pending, HumanMessage(content="B")]
    view = client._dialog_messages_for_provider(messages)
    assert view[1] == {"role": "assistant", "content": "我查一下"}
    assert view[0] is messages[0] and view[2] is messages[2]
    assert messages[1] is pending and pending["tool_calls"] == [_call("c1")]


def test_a_partly_answered_round_keeps_only_answered_calls():
    client = _client()
    turn = {"role": "assistant", "content": "", "tool_calls": [_call("c1"), _call("c2")]}
    messages = [HumanMessage(content="A"), turn, _reply("c1"), HumanMessage(content="B")]
    view = client._dialog_messages_for_provider(messages)
    assert view[1]["tool_calls"] == [_call("c1")]
    assert view[2] is messages[2] and len(view) == 4


def test_a_textless_unanswered_round_and_orphan_replies_are_dropped():
    client = _client()
    messages = [HumanMessage(content="A"),
                {"role": "assistant", "content": "", "tool_calls": [_call("c1")]},
                HumanMessage(content="B"), _reply("zz")]
    view = client._dialog_messages_for_provider(messages)
    assert view == [messages[0], messages[2]]


def test_a_complete_round_leaves_the_request_view_untouched():
    client = _client()
    messages = [HumanMessage(content="A"),
                {"role": "assistant", "content": "", "tool_calls": [_call("c1")]},
                _reply("c1"),
                {"role": "user", "content": [{"type": "text", "text": "tool image"}]},
                HumanMessage(content="B")]
    assert client._dialog_messages_for_provider(messages) is messages


async def test_the_interrupting_turn_never_sends_the_pending_round():
    """End to end: A is inside a slow tool when B arrives and requests."""
    release = asyncio.Event()

    async def slow_tool(call):
        await release.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    client = _client(handler=slow_tool)
    client.script = [[_text("我查一下"), _tool_calls("c1")],
                     [_text("好的"), _text("", "stop")]]
    turn_a = asyncio.create_task(client.stream_text("A"))
    for _ in range(50):
        if len(client.requests) == 1 and any(
            isinstance(m, dict) and m.get("tool_calls") for m in client._conversation_history
        ):
            break
        await asyncio.sleep(0)
    await client.handle_interruption()
    await client.stream_text("B")
    release.set()
    await turn_a
    b_request = client.requests[1]
    assert not [m for m in b_request if isinstance(m, dict) and m.get("tool_calls")]


async def test_a_kept_cancelled_round_holds_only_the_text_that_was_shown():
    """The pre-tool text sits unshown in the name-prefix buffer when the
    turn is cancelled inside the tool: the kept round must not carry it."""
    async def handler(call):
        await client.handle_interruption()
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    client = _client(handler=handler)
    client._prefix_buffer_size = 100
    client.script = [[_text("好的，我这就发"), _tool_calls("c1")]]
    await client.stream_text("帮我发消息")
    assert _emitted(client) == []
    assert _history_shape(client) == [
        ("human", "帮我发消息", None),
        ("assistant", "", ["c1"]),
        ("tool", "{}", None),
    ]


def _round_texts(client):
    return {m["tool_calls"][0]["id"]: m["content"] for m in client._conversation_history
            if isinstance(m, dict) and m.get("tool_calls")}


async def _until(condition):
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("never reached")


@pytest.mark.parametrize("later", ["typed", "callback"])
async def test_trimming_a_cancelled_round_never_touches_the_interrupters_round(later):
    """A is cancelled inside a slow tool; B runs its own tool round and
    finishes before A's handler returns. Trimming A's kept round touches A's
    round only. A callback reply (``prompt_ephemeral``) saves its round like
    a typed turn does: its pre-tool text lives in that round, not again in
    its reply."""
    release = asyncio.Event()

    async def handler(call):
        if call.call_id == "a1":
            await release.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    client = _client(handler=handler)
    client._prefix_buffer_size = 100
    client.script = [
        [_text("A在查"), _tool_calls("a1")],
        [_text("B也在查"), _tool_calls("b1")],
        [_text("B查完了。"), _text("", "stop")],
    ]
    turn_a = asyncio.create_task(client.stream_text("A"))
    await _until(lambda: _round_texts(client))
    await client.handle_interruption()
    if later == "typed":
        await client.stream_text("B")
    else:
        assert await client.prompt_ephemeral("callback") is True
    release.set()
    await turn_a
    assert _round_texts(client) == {"a1": "", "b1": "B也在查"}
    if later == "callback":
        assert _history_shape(client)[-1] == ("ai", "B查完了。", None)
        assert _emitted(client) == ["B也在查", "B查完了。"]


async def test_a_cut_stream_never_takes_a_round_saved_in_its_setup_window():
    """R1 is still live while R2 saves its user message and awaits the
    transcript send; R1 reaches its tool round in that window, so the round
    sits after R2's user message. R2 begins (displacing R1), shows text and
    its task is cancelled outright. What R2 showed is its own reply: R1's
    round keeps R1's text."""
    r2_saved, r1_parked, cut_point, never = (asyncio.Event() for _ in range(4))

    async def handler(call):
        r1_parked.set()
        await never.wait()

    async def transcript(_text_):
        r2_saved.set()
        await asyncio.wait_for(r1_parked.wait(), 5)

    async def parked():
        cut_point.set()
        await never.wait()

    client = _client(handler=handler)
    client.script = [
        [_text("R1在查"), r2_saved.wait, _tool_calls("r1")],
        [_text("R2说到一半，"), parked, _text("late"), _text("", "stop")],
    ]
    turn_1 = asyncio.create_task(client.stream_text("R1"))
    await _until(lambda: client.requests)
    turn_2 = asyncio.ensure_future(
        client.stream_text("R2", input_transcript_callback=transcript))
    await asyncio.wait_for(cut_point.wait(), 5)
    await client.handle_interruption()
    turn_2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn_2
    assert _history_shape(client) == [
        ("human", "R1", None),
        ("human", "R2", None),
        ("assistant", "R1在查", ["r1"]),
        ("ai", "R2说到一半，", None),
    ]
    turn_1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn_1
    # R1's round ran no call and went; what R1 showed stays in R1's turn.
    assert _history_shape(client) == [
        ("human", "R1", None),
        ("ai", "R1在查", None),
        ("human", "R2", None),
        ("ai", "R2说到一半，", None),
    ]


@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("prefix", [0, 100])
async def test_a_cut_round_after_a_later_user_message_takes_the_shown_text(provider, prefix):
    """R1's first round a1 finishes live; its second round a2 lands after
    R2's user message (R2's setup window), runs a2x and parks in a2y. R2 runs
    its own round and reply; then R1's task is cut before a2's sentinel. a2
    holds what R1 showed after a1 (nothing when the name-prefix buffer held
    it back), with no second copy as a reply; a1 and R2's round keep theirs."""
    r2_saved, a2y_parked, never = (asyncio.Event() for _ in range(3))

    async def handler(call):
        if call.call_id == "a2y":
            a2y_parked.set()
            await never.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    async def transcript(_text_):
        r2_saved.set()
        await asyncio.wait_for(a2y_parked.wait(), 5)

    client = _client(provider, handler=handler, cap=3)
    client._prefix_buffer_size = prefix
    if provider == "gemini":
        client.script = [
            [_gemini_calls("a1", text="R1第一段先说")],
            [_GenaiChunk([_GenaiPart(text="R1二")]), r2_saved.wait,
             _gemini_calls("a2x", "a2y")],
            [_gemini_calls("b1", text="R2也在查")],
            [_GenaiChunk([_GenaiPart(text="R2查完了。")])],
        ]
    else:
        client.script = [
            [_text("R1第一段先说"), _tool_calls("a1")],
            [_text("R1二"), r2_saved.wait, _tool_calls("a2x", "a2y")],
            [_text("R2也在查"), _tool_calls("b1")],
            [_text("R2查完了。"), _text("", "stop")],
        ]
    turn_1 = asyncio.create_task(client.stream_text("R1"))
    await _until(lambda: len(client.requests) >= 2)
    await client.stream_text("R2", input_transcript_callback=transcript)
    a1_text = _round_texts(client)["a1"]
    turn_1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn_1
    assert _round_texts(client) == {
        "a1": a1_text, "a2x": "R1二" if prefix == 0 else "", "b1": "R2也在查",
    }
    assert [s for s in _history_shape(client) if s[0] == "ai"] == [
        ("ai", "R2查完了。", None),
    ]


@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_a_cancelled_round_saved_after_a_later_user_message_is_still_trimmed(provider):
    """The same window, but R1's handler returns after R2 finished its own
    tool round and reply. R1's kept round is R1's although R2's user message
    comes first: it holds only what R1 showed (nothing, the name-prefix
    buffer held it back), and R2's round keeps R2's text."""
    r2_saved, r1_parked, release = (asyncio.Event() for _ in range(3))

    async def handler(call):
        if call.call_id == "r1":
            r1_parked.set()
            await release.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    async def transcript(_text_):
        r2_saved.set()
        await asyncio.wait_for(r1_parked.wait(), 5)

    client = _client(provider, handler=handler)
    client._prefix_buffer_size = 100
    if provider == "gemini":
        client.script = [
            [_GenaiChunk([_GenaiPart(text="R1在查")]), r2_saved.wait, _gemini_calls("r1")],
            [_gemini_calls("r2", text="R2也在查")],
            [_GenaiChunk([_GenaiPart(text="R2查完了。")])],
        ]
    else:
        client.script = [
            [_text("R1在查"), r2_saved.wait, _tool_calls("r1")],
            [_text("R2也在查"), _tool_calls("r2")],
            [_text("R2查完了。"), _text("", "stop")],
        ]
    turn_1 = asyncio.create_task(client.stream_text("R1"))
    await _until(lambda: client.requests)
    await client.stream_text("R2", input_transcript_callback=transcript)
    release.set()
    await turn_1
    assert _round_texts(client) == {"r1": "", "r2": "R2也在查"}


async def test_a_tools_refusal_retry_cancelled_in_flight_still_publishes():
    """The retry after a tools refusal follows the first attempt's rule:
    the caller publishes on the first chunk, then checks cancellation."""
    client = _client(handler=_noop_tool)

    async def interrupt():
        await client.handle_interruption()

    client.script = [RuntimeError("this model does not support tools"),
                     [interrupt, _text("late"), _text("", "stop")]]
    await client.stream_text("look", turn_images=[_png_b64(4, 4, (7, 7, 7))])
    assert len(client.requests) == 2
    client._publish_provider_frames.assert_called_once()
    assert _emitted(client) == []


# ── Fourth review round ─────────────────────────────────────────────────────

@pytest.mark.parametrize("provider,cap", [("openai", 2), ("openai", 0), ("gemini", 2)])
async def test_a_reasoning_only_start_cancelled_still_publishes_the_turn(provider, cap):
    """The stream opens with reasoning, which is never yielded; the user
    interrupts during it. The provider saw the turn's frames, so they are
    published all the same, and nothing is shown."""
    client = _client(provider, handler=_noop_tool, cap=cap)

    async def thinking(active):
        if active:
            await client.handle_interruption()

    client.on_thinking_active = thinking
    if provider == "gemini":
        thought = _GenaiPart(text="thinking")
        thought.thought = True
        client.script = [[_GenaiChunk([thought]), _GenaiChunk([_GenaiPart(text="late")])]]
    else:
        client.script = [[LLMStreamChunk(content="", reasoning_content="thinking"),
                          _text("late"), _text("", "stop")]]
    await client.stream_text("look", turn_images=[_png_b64(4, 4, (3, 3, 3))])
    client._publish_provider_frames.assert_called_once()
    assert _emitted(client) == []


@pytest.mark.parametrize("cap", [2, 0])
async def test_a_reasoning_only_last_chunk_cancelled_still_publishes_the_turn(cap):
    """Like the case above, but the reasoning chunk is the stream's last: no
    later chunk re-checks the generation, so the check right after the
    thinking pulse (tool loop and forced-final alike) must hand callers the
    answered chunk they publish on."""
    client = _client("openai", handler=_noop_tool, cap=cap)

    async def thinking(active):
        if active:
            await client.handle_interruption()

    client.on_thinking_active = thinking
    client.script = [[LLMStreamChunk(content="", reasoning_content="thinking")]]
    await client.stream_text("look", turn_images=[_png_b64(4, 4, (3, 3, 3))])
    client._publish_provider_frames.assert_called_once()
    assert _emitted(client) == []


async def test_an_interrupted_proactive_reply_reports_its_agent_callback_kind():
    """prompt_ephemeral's proactive completion is handle_proactive_complete,
    which closes with 'turn end agent_callback'; an interruption reports
    that kind so the core closes the turn the same way."""
    client = _client()
    seen = []

    async def interrupt():
        seen.append(await client.handle_interruption())

    client.script = [[_text("回调说到一半"), interrupt, _text("", "stop")]]
    await client.prompt_ephemeral("callback")
    assert seen == ["agent_callback"]
    client.on_proactive_done.assert_not_awaited()


async def test_a_raising_status_send_leaves_no_claimable_completion():
    """The completion-pending mark lives only for the cleanup await; if that
    await raises, the next interruption must not claim a phantom turn."""
    client = _client()
    client.on_status_message = AsyncMock(side_effect=RuntimeError("socket gone"))
    client.script = [[_text("", "stop")]]
    with pytest.raises(RuntimeError):
        await client.stream_text("hi")
    assert await client.handle_interruption() == ""


async def test_the_answered_chunk_of_a_cancelled_turn_sets_no_first_token_time(monkeypatch):
    """The empty chunk a cancelled tool loop hands up carries no output, so
    it must not be recorded as the turn's first token."""
    recorded = []
    monkeypatch.setattr(
        "utils.instrument.histogram", lambda name, value, *a, **k: recorded.append(name),
    )
    client = _client(handler=_noop_tool)

    async def thinking(active):
        if active:
            await client.handle_interruption()

    client.on_thinking_active = thinking
    client.script = [[LLMStreamChunk(content="", reasoning_content="thinking"),
                      _text("late"), _text("", "stop")]]
    await client.stream_text("hi")
    assert "llm_ttft_ms" not in recorded
    # A normal turn still records it.
    client.on_thinking_active = None
    client.script = [[_text("好"), _text("", "stop")]]
    client.requests.clear()
    await client.stream_text("again")
    assert "llm_ttft_ms" in recorded


# ── Fifth review round: who owns an interrupted turn ────────────────────────

@pytest.mark.parametrize("entry", ["stream_text", "response", "proactive"])
async def test_a_session_close_mid_reply_still_runs_the_completion(entry):
    """close() takes nothing over: nobody else closes that turn, so its
    completion runs (turn end, request id, TTS), as on main."""
    client = _client()

    async def close_mid_reply():
        client._cancel_response_generation()

    client.script = [[_text("说到一半"), close_mid_reply, _text("late"), _text("", "stop")]]
    if entry == "stream_text":
        await client.stream_text("Q1")
        client.on_response_done.assert_awaited_once()
    elif entry == "response":
        await client.prompt_ephemeral("avatar", completion_mode="response")
        client.on_response_done.assert_awaited_once()
    else:
        await client.prompt_ephemeral("callback")
        client.on_proactive_done.assert_awaited_once()
    assert _emitted(client) == ["说到一半"]


async def test_an_interrupter_takes_the_turn_over_and_leaves_no_record():
    client = _client()

    async def interrupt():
        assert await client.handle_interruption() == "response"

    client.script = [[_text("hi"), interrupt, _text("", "stop")]]
    await client.stream_text("Q1")
    client.on_response_done.assert_not_awaited()
    assert getattr(client, "_interrupter_owned_generations", set()) == set()


async def test_a_taken_over_turn_leaves_nothing_for_a_second_interruption():
    """Once an interrupter took the turn over, its cleanup await is no
    claim window: a second interruption meanwhile finds nothing to take, or
    it would close the turn again (and take the new turn's request id)."""
    client = _client()
    seen = []

    async def interrupt():
        seen.append(await client.handle_interruption())

    async def cleanup(_owner):
        seen.append(await client.handle_interruption())

    client._notify_reasoning_done = cleanup
    client.script = [[_text("说到一半"), interrupt, _text("late"), _text("", "stop")]]
    await client.prompt_ephemeral("avatar", completion_mode="response")
    assert seen == ["response", ""]
    client.on_response_done.assert_not_awaited()


async def test_the_completion_window_reads_as_busy():
    """Finished but not yet completed is not idle: a proactive start or an
    owed wrap-up must wait for that completion."""
    client = _client()
    seen = []

    async def cleanup(_owner):
        seen.append(client._is_responding)

    client._notify_reasoning_done = cleanup
    client.script = [[_text("说完了。"), _text("", "stop")]]
    await client.prompt_ephemeral("callback")
    assert seen == [True]
    assert client._is_responding is False
    client.on_proactive_done.assert_awaited_once()


async def test_the_completion_window_never_clobbers_a_newer_generation():
    client = _client()
    newer = []

    async def cleanup(_owner):
        await client.handle_interruption()
        newer.append(client._begin_response_generation())

    client._notify_reasoning_done = cleanup
    client.script = [[_text("说完了。"), _text("", "stop")]]
    await client.prompt_ephemeral("callback")
    assert client._active_response_generation == newer[0]
    assert client._is_responding is True


# ── A proactive reply that began later ends a cancelled turn (ninth round) ──

def _deliver_proactive_chat(client, text):
    """What finish_proactive_delivery saves: a marked reply with no
    generation of the session behind it."""
    client._conversation_history.append(AIMessage(
        content=text,
        additional_kwargs={"anti_repeat_response_id": "sid", "dialog_source": "proactive"},
    ))
    return True


@pytest.mark.parametrize("delivery", ["callback", "proactive_chat"])
async def test_a_late_cancelled_reply_goes_before_a_proactive_reply_that_began_after_it(delivery):
    """A mini-game command cancels A without adding a user message, and A
    commits late (here its stream stalls). Only a reply still in progress
    holds a proactive turn back, so one begins and commits meanwhile. A's
    shown text goes before it."""
    stalled, release = asyncio.Event(), asyncio.Event()
    client = _client()

    async def cancel_and_stall():
        await client.handle_interruption()
        stalled.set()
        await release.wait()

    client.script = [
        [_text("我先说一半"), cancel_and_stall, _text("late"), _text("", "stop")],
        [_text("主人，任务完成啦"), _text("", "stop")],
    ]
    turn_a = asyncio.create_task(client.stream_text("A"))
    await asyncio.wait_for(stalled.wait(), 1)
    assert not client.has_reply_in_progress()
    if delivery == "callback":
        assert await client.prompt_ephemeral("callback") is True
    else:
        _deliver_proactive_chat(client, "主人，任务完成啦")
    release.set()
    await turn_a
    assert _history_shape(client) == [
        ("human", "A", None),
        ("ai", "我先说一半", None),
        ("ai", "主人，任务完成啦", None),
    ]
    assert client._conversation_history[-1].additional_kwargs["dialog_source"] == "proactive"


async def test_a_late_cancelled_proactive_reply_goes_before_a_later_one():
    """The same order between two proactive replies: the first is cancelled
    and commits late, the second began after it and committed first."""
    stalled, release = asyncio.Event(), asyncio.Event()
    client = _client()
    client._conversation_history.append(HumanMessage(content="earlier"))

    async def cancel_and_stall():
        await client.handle_interruption()
        stalled.set()
        await release.wait()

    client.script = [
        [_text("刚才看到"), cancel_and_stall, _text("late"), _text("", "stop")],
        [_text("主人，任务完成啦"), _text("", "stop")],
    ]
    first = asyncio.create_task(client.prompt_ephemeral("greeting"))
    await asyncio.wait_for(stalled.wait(), 1)
    assert await client.prompt_ephemeral("callback") is True
    release.set()
    await first
    assert _history_shape(client) == [
        ("human", "earlier", None),
        ("ai", "刚才看到", None),
        ("ai", "主人，任务完成啦", None),
    ]


async def test_a_reply_cancelled_in_its_summary_call_goes_before_a_callback_reply(monkeypatch):
    """The long-reply summary is a small-model call of its own. A mini-game
    command cancelling A while it runs lets a callback reply begin and
    commit before A writes the text the UI already showed."""
    monkeypatch.setattr(_ofc_streaming, "count_tokens", lambda text: len((text or "").split()))
    monkeypatch.setattr(
        _ofc_streaming, "truncate_to_tokens",
        lambda text, budget: " ".join((text or "").split()[:budget]),
    )
    client = _client()
    client.enable_response_guard = True
    client.enable_long_response_summary = True
    client.max_response_length = 4

    async def summarize(prefix, tail):
        await client.handle_interruption()
        assert await client.prompt_ephemeral("callback") is True
        return "总之就这样啦"

    client._summarize_tail_for_tts = summarize
    long_text = (
        "one two three four. five, six seven eight nine ten. "
        + " ".join(f"w{i}" for i in range(25)) + "."
    )
    client.script = [[_text(long_text), _text("", "stop")],
                     [_text("主人，任务完成啦"), _text("", "stop")]]
    await client.stream_text("A")
    shown = "".join(
        call.args[0] for call in client.on_text_delta.await_args_list
        if call.kwargs.get("ui_enabled", True) and call.args[0] != "主人，任务完成啦"
    )
    assert shown.startswith("one two three four.")
    assert _history_shape(client) == [
        ("human", "A", None),
        ("ai", shown, None),
        ("ai", "主人，任务完成啦", None),
    ]


async def test_a_proactive_reply_this_turn_displaced_stays_before_its_cancelled_reply():
    """A callback reply that began during A's setup (after A's user message
    was saved) was displaced by A's begin, so it was shown first. It is no
    boundary for A: A's cancelled reply stays after it and after A's own
    tool round."""
    p_stalled, p_release = asyncio.Event(), asyncio.Event()
    client = _client(handler=_noop_tool)
    proactive = []

    async def transcript(_text_):
        proactive.append(asyncio.create_task(client.prompt_ephemeral("callback")))
        await asyncio.wait_for(p_stalled.wait(), 1)

    async def p_stall():
        p_stalled.set()
        await p_release.wait()

    async def finish_proactive():
        p_release.set()
        await proactive[0]

    async def cancel():
        await client.handle_interruption()

    client.on_input_transcript = transcript
    client.script = [
        [_text("刚想说"), p_stall, _text("x"), _text("", "stop")],
        [finish_proactive, _text("我查一下"), _tool_calls("c1")],
        [_text("查到了"), cancel, _text("late"), _text("", "stop")],
    ]
    await client.stream_text("A")
    assert _history_shape(client) == [
        ("human", "A", None),
        ("ai", "刚想说", None),
        ("assistant", "我查一下", ["c1"]),
        ("tool", "{}", None),
        ("ai", "查到了", None),
    ]


async def test_a_cancelled_round_whose_turn_left_history_is_not_put_back():
    """History is trimmed in place while A's handler runs (a greeting that was
    interrupted drops what was appended after it, A's turn included), then B
    replies. A's kept round is not appended after B's turn."""
    release = asyncio.Event()

    async def slow_tool(call):
        await release.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    client = _client(handler=slow_tool)
    client.script = [[_text("我查一下"), _tool_calls("a1")],
                     [_text("好的"), _text("", "stop")]]
    turn_a = asyncio.create_task(client.stream_text("A"))
    for _ in range(50):
        if any(isinstance(m, dict) and m.get("tool_calls") for m in client._conversation_history):
            break
        await asyncio.sleep(0)
    del client._conversation_history[1:]
    await client.handle_interruption()
    await client.stream_text("B")
    release.set()
    await turn_a
    assert _history_shape(client) == [("human", "B", None), ("ai", "好的", None)]


# ── A task cut after the stream loop (tenth review round) ───────────────────
#
# The independent-ASR child task is cancelled outright by the interruption
# that takes its reply over, so none of the turn's own checks run. The
# end-of-stream flush and the summary epilogue still emit text before the
# reply commits; a cut there keeps the text shown so far, once: never text a
# guard discards, nor a summary its send had not yet queued.

def _parked_when(predicate):
    """An ``on_text_delta`` that parks for good in the first send matching
    ``predicate(text, kwargs)`` (by then the text is published), and the
    event set once it has."""
    entered = asyncio.Event()

    async def send(text, is_first, **kwargs):
        if not entered.is_set() and predicate(text, kwargs):
            entered.set()
            await asyncio.Event().wait()

    return AsyncMock(side_effect=send), entered


async def _parked_after(entered):
    entered.set()
    await asyncio.Event().wait()


async def _cut_voice_turn(client, entered):
    """Run "Q1" as an independent-ASR turn; once ``entered`` is set,
    interrupt it (the take-over cancels its task) and save the next user
    message, "Q2"."""
    client._external_voice_submit_task = None
    turn = asyncio.create_task(client._run_external_voice_stream("Q1"))
    await asyncio.wait_for(entered.wait(), 2)
    assert await client.handle_interruption()
    client._conversation_history.append(HumanMessage(content="Q2"))
    (outcome,) = await asyncio.gather(turn, return_exceptions=True)
    assert isinstance(outcome, client._ExternalVoiceSubmitCancelled), outcome
    client.on_response_done.assert_not_awaited()


def _reply_chunks(provider, text):
    if provider == "gemini":
        return [_GenaiChunk([_GenaiPart(text=text)])]
    return [_text(text), _text("", "stop")]


def _count_words(monkeypatch):
    monkeypatch.setattr(_ofc_streaming, "count_tokens", lambda text: len((text or "").split()))
    monkeypatch.setattr(
        _ofc_streaming, "truncate_to_tokens",
        lambda text, budget: " ".join((text or "").split()[:budget]),
    )


_SUMMARY_PREFIX = "one two three four. five,"
_SUMMARY_SHORT_TAIL = _SUMMARY_PREFIX + " six seven eight nine ten."
_SUMMARY_LONG_TAIL = _SUMMARY_SHORT_TAIL + " " + " ".join(f"w{i}" for i in range(25)) + "."
_SUMMARY_GIBBERISH_TAIL = _SUMMARY_PREFIX + " " + " ".join(f"w{i}" for i in range(120))


def _summary_client(monkeypatch, text, summary=None):
    """A long-reply-summary client over a four-word budget: the reply cuts
    over at ``_SUMMARY_PREFIX``, and ``summary`` is what the summary call
    returns."""
    _count_words(monkeypatch)
    client = _client()
    client.enable_response_guard = True
    client.enable_long_response_summary = True
    client.max_response_length = 4

    async def summarize(prefix, tail):
        return summary

    client._summarize_tail_for_tts = summarize
    client.script = [[_text(text), _text("", "stop")]]
    return client


@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_a_task_cut_in_the_end_of_stream_flush_keeps_the_shown_reply(provider):
    """A short reply sits whole in the name-prefix buffer, so the
    end-of-stream flush is what shows it. Cut while that send is parked, the
    reply is kept, before the interrupter's message."""
    client = _client(provider)
    client._prefix_buffer_size = 100
    client.on_text_delta, entered = _parked_when(lambda text, _kw: text == "短回复")
    client.script = [_reply_chunks(provider, "短回复")]
    await _cut_voice_turn(client, entered)
    assert _history_shape(client) == [
        ("human", "Q1", None), ("ai", "短回复", None), ("human", "Q2", None),
    ]


@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_a_task_cut_in_the_flush_after_a_tool_round_keeps_only_its_segment(provider):
    """After a tool round the flush shows the post-tool segment. Cut there,
    that segment is the reply after the round; the round, whose sentinel
    this turn already handled, keeps its own text."""
    client = _client(provider, handler=_noop_tool)
    client._prefix_buffer_size = 100
    client.on_text_delta, entered = _parked_when(lambda text, _kw: text == "查到了")
    if provider == "gemini":
        first = [_gemini_calls("c1", text="我查一下")]
    else:
        first = [_text("我查一下"), _tool_calls("c1")]
    client.script = [first, _reply_chunks(provider, "查到了")]
    await _cut_voice_turn(client, entered)
    assert _history_shape(client) == [
        ("human", "Q1", None),
        ("assistant", "我查一下", ["c1"]),
        ("tool", "{}", None),
        ("ai", "查到了", None),
        ("human", "Q2", None),
    ]


def _shown(client):
    return "".join(
        call.args[0] for call in client.on_text_delta.await_args_list
        if call.kwargs.get("ui_enabled", True)
    )


@pytest.mark.parametrize("epilogue", ["short_tail", "summary", "summary_failed"])
async def test_a_task_cut_in_the_summary_epilogue_keeps_the_shown_reply(
    monkeypatch, epilogue,
):
    """The summary epilogue sends the tail, or its summary, to TTS only,
    before the reply commits. Cut in that send, the reply keeps the text
    the UI showed, as a cut in the summary call does: what the commit writes
    when the tail is read out, and never a summary that was not queued."""
    summary = "总之就这样啦" if epilogue == "summary" else None
    text = _SUMMARY_SHORT_TAIL if epilogue == "short_tail" else _SUMMARY_LONG_TAIL
    client = _summary_client(monkeypatch, text, summary)
    client.on_text_delta, entered = _parked_when(
        lambda _text_, kw: kw.get("ui_enabled") is False,
    )
    await _cut_voice_turn(client, entered)
    assert _shown(client) == text
    assert _history_shape(client) == [
        ("human", "Q1", None), ("ai", text, None), ("human", "Q2", None),
    ]


def _real_tts_side(client):
    """Send ``client``'s text through the real ``handle_text_data`` of a bare
    manager (the UI publish stays the recording mock): ``enqueued`` is what
    reached the TTS queue, in order."""
    mgr = SimpleNamespace(
        _takeover_active=False,
        use_tts=True,
        tts_cache_lock=asyncio.Lock(),
        tts_ready=True,
        tts_thread=SimpleNamespace(is_alive=lambda: True),
        tts_response_queue=queue.Queue(),
        tts_pending_chunks=[],
        current_speech_id="sid-1",
        _discard_pending_ai_voice_echo=lambda: None,
        enqueued=[],
    )
    mgr._enqueue_tts_text_chunk = lambda _sid, text: mgr.enqueued.append(text)

    async def send(text, is_first, *, ui_enabled=True, tts_enabled=True):
        await TurnMixin.handle_text_data(
            mgr, text, is_first, ui_enabled=False, tts_enabled=tts_enabled,
        )

    client.on_text_delta = AsyncMock(side_effect=send)
    return mgr


async def test_a_summary_cut_while_its_send_waits_for_the_tts_lock_is_never_written(
    monkeypatch,
):
    """The summary's TTS-only send waits for the TTS cache lock, held by
    another holder. Cut there, the summary was never queued: the reply keeps
    the shown text, not prefix + summary."""
    client = _summary_client(monkeypatch, _SUMMARY_LONG_TAIL)
    mgr = _real_tts_side(client)
    real_send = client.on_text_delta.side_effect
    entered = asyncio.Event()

    async def summarize(prefix, tail):
        await mgr.tts_cache_lock.acquire()  # taken by another holder meanwhile
        return "总之就这样啦"

    async def send(text, is_first, **kwargs):
        if text == "总之就这样啦":
            entered.set()  # it parks on the lock right after
        await real_send(text, is_first, **kwargs)

    client._summarize_tail_for_tts = summarize
    client.on_text_delta.side_effect = send
    try:
        await _cut_voice_turn(client, entered)
    finally:
        mgr.tts_cache_lock.release()
    assert "".join(mgr.enqueued) == _SUMMARY_PREFIX
    assert _shown(client) == _SUMMARY_LONG_TAIL
    assert _history_shape(client) == [
        ("human", "Q1", None), ("ai", _SUMMARY_LONG_TAIL, None), ("human", "Q2", None),
    ]


async def test_a_summary_sent_through_the_real_tts_path_is_queued_and_committed(
    monkeypatch,
):
    """Uncut, the summary goes to TTS after the prefix (never the UI-only
    tail) and the reply commits prefix + summary."""
    client = _summary_client(monkeypatch, _SUMMARY_LONG_TAIL, "总之就这样啦")
    mgr = _real_tts_side(client)
    client._external_voice_submit_task = None
    await client._run_external_voice_stream("Q1")
    assert mgr.enqueued[-1] == "总之就这样啦"
    assert "".join(mgr.enqueued) == _SUMMARY_PREFIX + "总之就这样啦"
    assert _history_shape(client) == [
        ("human", "Q1", None), ("ai", _SUMMARY_PREFIX + "总之就这样啦", None),
    ]


@pytest.mark.parametrize("rerolls", [1, 0])
async def test_a_task_cut_while_a_guard_discards_the_reply_keeps_none_of_it(
    monkeypatch, rerolls,
):
    """A length guard with nothing to recover discards what was shown: for
    a retry, or for the placeholder the caller writes once the rerolls are
    spent. Cut while that discard is sent, the turn keeps none of it."""
    _count_words(monkeypatch)
    client = _client()
    client.enable_response_guard = True
    client.max_response_length = 4
    client.max_response_rerolls = rerolls
    entered = asyncio.Event()

    async def discard(*_args):
        await _parked_after(entered)

    client.on_response_discarded = AsyncMock(side_effect=discard)
    client.script = [[
        _text("hello there friend"), _text(" aaaa bbbb cccc dddd eeee"), _text("", "stop"),
    ]]
    await _cut_voice_turn(client, entered)
    assert _emitted(client) == ["hello there friend"]
    assert client.on_response_discarded.await_args.args[3] is bool(rerolls)
    assert _history_shape(client) == [("human", "Q1", None), ("human", "Q2", None)]


@pytest.mark.parametrize(
    "commit", ["reply", "length_recovery", "summary", "gibberish_prefix"],
)
async def test_a_task_cut_after_the_commit_writes_the_reply_once(monkeypatch, commit):
    """The repetition check runs after the reply is written. Cut there, the
    reply is not written a second time (nor the UI-only tail its sent
    summary replaced, nor the gibberish tail the summary fallback leaves
    out)."""
    if commit == "reply":
        client = _client()
        client.script = [_reply_chunks("openai", "你好呀。")]
        kept = "你好呀。"
    elif commit == "length_recovery":
        client, _ = _length_guarded_client("name_prefix")
        kept = "先说一句。"
    elif commit == "summary":
        client = _summary_client(monkeypatch, _SUMMARY_LONG_TAIL, "总之就这样啦")
        kept = _SUMMARY_PREFIX + "总之就这样啦"
    else:
        client = _summary_client(monkeypatch, _SUMMARY_GIBBERISH_TAIL)
        kept = _SUMMARY_PREFIX
    entered = asyncio.Event()

    async def parked_check(_response):
        await _parked_after(entered)

    client._check_repetition = parked_check
    await _cut_voice_turn(client, entered)
    assert _history_shape(client) == [
        ("human", "Q1", None), ("ai", kept, None), ("human", "Q2", None),
    ]


async def test_a_task_cut_in_the_flush_of_a_rerolled_attempt_keeps_only_that_attempt():
    """The end-of-stream flush finds a master-name prefix: the attempt is
    discarded and rerolled. The reroll's own flush is then cut: only the
    reroll's text is kept, never the discarded attempt's."""
    client = _client()
    client._prefix_buffer_size = 100
    client.max_response_rerolls = 1
    client.on_response_discarded = AsyncMock()
    client.on_text_delta, entered = _parked_when(lambda text, _kw: text == "重来的回复")
    client.script = [
        [_text("M|我是主人"), _text("", "stop")],
        [_text("重来的回复"), _text("", "stop")],
    ]
    await _cut_voice_turn(client, entered)
    assert client.on_response_discarded.await_args.args[3] is True  # will_retry
    assert _history_shape(client) == [
        ("human", "Q1", None), ("ai", "重来的回复", None), ("human", "Q2", None),
    ]


async def _parked_voice_turn():
    client = _client()
    started = asyncio.Event()

    async def park():
        started.set()
        await asyncio.Event().wait()

    client.script = [[park]]
    client.on_status_message = AsyncMock()
    worker = asyncio.create_task(client.submit_external_voice_turn("hi", turn_id="t"))
    await asyncio.wait_for(started.wait(), 2)
    return client, worker


def _status_codes(client):
    return [json.loads(c.args[0]).get("code") for c in client.on_status_message.await_args_list]


@pytest.mark.parametrize("cut", ["worker_teardown", "close_order", "interruption"])
async def test_a_task_cut_before_the_first_token_reports_no_empty_reply(cut):
    """A reply cut off before its first token was not silent: no
    LLM_NO_RESPONSE, whether the transcript worker is torn down
    (TranscriptDispatcher.invalidate_all()), close() cancels the task before
    retiring the generation, or an interruption takes the reply over."""
    client, worker = await _parked_voice_turn()
    if cut == "worker_teardown":
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    elif cut == "close_order":
        await client._cancel_external_voice_submit_task()
        client._cancel_response_generation()
        assert await worker is False
    else:
        await client.handle_interruption()
        assert await worker is False
    assert _status_codes(client) == []


async def test_an_empty_completion_still_reports_no_response():
    client = _client()
    client.on_status_message = AsyncMock()
    client.script = [[_text("", "stop")]] * 3
    await client.stream_text("hi")
    assert _status_codes(client) == ["LLM_NO_RESPONSE"]


async def test_a_live_round_finishing_in_another_turns_setup_window_stays_paired():
    """Reply A's tool returns while typed input B is between saving its user
    message and beginning its reply (its transcript send): A is still live,
    so its round completes live, with B's message already after the call.
    The round is still put back together, so the model keeps seeing a call
    whose side effects happened, with its result."""
    release, b_done = asyncio.Event(), asyncio.Event()
    ran = []

    async def handler(call):
        await release.wait()
        ran.append(call.call_id)
        return ToolResult(call_id=call.call_id, name=call.name, output={"sent": True})

    async def transcript(_t):
        release.set()
        await _until(lambda: len(client.requests) >= 2)

    client = _client(handler=handler)
    client.script = [
        [_text("我发一下"), _tool_calls("a1")],
        [b_done.wait, _text("A late"), _text("", "stop")],
        [_text("B答"), _text("", "stop")],
        [_text("C答"), _text("", "stop")],
    ]
    turn_a = asyncio.create_task(client.stream_text("A"))
    await _until(lambda: any(
        isinstance(m, dict) and m.get("tool_calls") for m in client._conversation_history
    ))
    await client.stream_text("B", input_transcript_callback=transcript)
    b_done.set()
    await turn_a
    await client.stream_text("C")

    assert ran == ["a1"]
    shape = _history_shape(client)
    i_call = next(i for i, s in enumerate(shape) if s[2] == ["a1"])
    assert shape[i_call + 1][0] == "tool"
    assert shape.index(("human", "B", None)) > i_call + 1
    last_request = client.requests[-1]
    assert [m for m in last_request if isinstance(m, dict) and m.get("tool_calls")]
    assert [m for m in last_request if isinstance(m, dict) and m.get("role") == "tool"]


async def test_a_live_rounds_images_follow_its_last_reply_not_another_turns_message():
    """The same setup window, with a tool that returns an image: the image
    turn goes right after the round's reply, before B's user message, so B's
    request does not end on A's full-size image."""
    release = asyncio.Event()
    image = ToolImage(data_b64=_png_b64(4, 4, (7, 8, 9)), mime="image/png")

    async def handler(call):
        await release.wait()
        return ToolResult(
            call_id=call.call_id, name=call.name, output={"ok": True}, images=[image],
        )

    async def transcript(_t):
        release.set()
        await _until(lambda: len(client.requests) >= 2)

    client = _client(handler=handler)
    client.script = [
        [_text("我看一下"), _tool_calls("a1")],
        [_text("看到了"), _text("", "stop")],
        [_text("B答"), _text("", "stop")],
    ]
    turn_a = asyncio.create_task(client.stream_text("A"))
    await _until(lambda: any(
        isinstance(m, dict) and m.get("tool_calls") for m in client._conversation_history
    ))
    await client.stream_text("B", input_transcript_callback=transcript)
    await turn_a

    history = client._conversation_history
    i_call = next(
        i for i, m in enumerate(history) if isinstance(m, dict) and m.get("tool_calls")
    )
    assert history[i_call + 1].get("role") == "tool"
    assert history[i_call + 2].get("role") == "user"  # the image turn (or its placeholder)
    i_b = next(
        i for i, m in enumerate(history)
        if isinstance(m, HumanMessage) and m.content == "B"
    )
    assert i_b > i_call + 2
    b_request = client.requests[-1]
    assert not (isinstance(b_request[-1], dict) and b_request[-1].get("role") == "user")


async def test_a_cancelled_reply_is_dropped_once_history_was_reset():
    """A's reply is cut and stalls; B completes as the third repetitive reply
    and the repetition check resets history. A's late commit finds its turn
    gone with the old list and writes nothing into the reset history."""
    stalled, release = asyncio.Event(), asyncio.Event()
    client = _client()
    rep = "主人今天也要开开心心的哦"
    client._recent_responses = [rep, rep]
    resets = []

    async def on_rep():
        resets.append(True)

    client.on_repetition_detected = on_rep

    async def cancel_and_stall():
        await client.handle_interruption()
        stalled.set()
        await release.wait()

    client.script = [
        [_text("我先说一半"), cancel_and_stall, _text("late"), _text("", "stop")],
        [_text(rep), _text("", "stop")],
    ]
    turn_a = asyncio.create_task(client.stream_text("A"))
    await asyncio.wait_for(stalled.wait(), 1)
    await client.stream_text("B")
    assert resets and _history_shape(client) == []
    release.set()
    await turn_a
    assert _history_shape(client) == []


async def test_a_proactive_reply_anchored_on_a_dropped_tool_round_stays_before_the_next_user():
    """A proactive reply begins while another turn's tool round (a dict) is
    the last message, and that round later leaves history (no call ran). Its
    cut reply still goes before the user message that interrupted it."""
    hold = asyncio.Event()
    p_stalled, p_release = asyncio.Event(), asyncio.Event()

    async def handler(call):
        await hold.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    client = _client(handler=handler)
    client._conversation_history.append(HumanMessage(content="earlier"))

    async def p_stall():
        p_stalled.set()
        await p_release.wait()

    client.script = [
        [_text("A在查"), _tool_calls("a1")],
        [_text("刚才看到"), p_stall, _text("late"), _text("", "stop")],
    ]
    turn_a = asyncio.create_task(client.stream_text("A"))
    await _until(lambda: isinstance(client._conversation_history[-1], dict))
    await client.cancel_response()
    p = asyncio.create_task(client.prompt_ephemeral("callback"))
    await asyncio.wait_for(p_stalled.wait(), 1)
    await client.handle_interruption()
    turn_a.cancel()
    await asyncio.gather(turn_a, return_exceptions=True)
    client._conversation_history.append(HumanMessage(content="Q-new"))
    p_release.set()
    await asyncio.gather(p, return_exceptions=True)
    shape = _history_shape(client)
    i_reply = next(i for i, s in enumerate(shape) if s[0] == "ai" and s[1] == "刚才看到")
    assert i_reply < shape.index(("human", "Q-new", None))


_SPLIT_LONG = (
    "one two three four five six seven eight nine ten, "
    "eleven twelve thirteen fourteen fifteen sixteen seventeen."
)


def _summary_split_client():
    client = _client()
    client.enable_response_guard = True
    client.enable_long_response_summary = True
    client.max_response_length = 5
    client.max_response_rerolls = 0
    calls = []

    async def on_text_delta(text, is_first=False, *, ui_enabled=True, tts_enabled=True):
        calls.append((text, ui_enabled, tts_enabled))
        if text.endswith(",") and client._active_response_generation is not None:
            # A typed input interrupts during the first half of the split:
            # the generation retires, the stream_text task runs on.
            assert await client.handle_interruption()

    async def summarize(**_kw):
        return "SUMMARY"

    client.on_text_delta = on_text_delta
    client._summarize_tail_for_tts = summarize
    return client, calls


async def test_a_reply_cut_in_the_first_half_of_a_summary_split_sends_no_more():
    client, calls = _summary_split_client()
    client.script = [[_text("Hi! "), _text(_SPLIT_LONG), _text(" more tail text", "stop")]]
    await client.stream_text("hello")
    cut = next(i for i, c in enumerate(calls) if c[0].endswith(","))
    assert calls[cut + 1:] == []


async def test_a_reply_cut_in_the_flush_split_sends_no_tail_or_summary():
    client, calls = _summary_split_client()
    client._prefix_buffer_size = 10_000  # the whole reply goes through the flush
    client.script = [[_text(_SPLIT_LONG, "stop")]]
    await client.stream_text("hello")
    assert len(calls) == 1 and calls[0][0].endswith(",")
    shape = _history_shape(client)
    assert shape[-1][0] == "ai" and shape[-1][1] == calls[0][0]


async def test_a_live_round_whose_turn_left_history_adds_no_orphan_image():
    """The turn is trimmed out of history in place while its tool runs (a
    greeting rollback drops what was appended after it): the round keeps
    nothing, so it is not live either, and no image turn is left orphaned at
    the end of history. Its text goes with its turn."""
    image = ToolImage(data_b64=_png_b64(4, 4, (3, 4, 5)), mime="image/png")

    async def handler(call):
        del client._conversation_history[1:]
        return ToolResult(call_id=call.call_id, name=call.name, output={}, images=[image])

    client = _client(handler=handler)
    client.script = [
        [_text("我看看"), _tool_calls("a1")],
        [_text("好了"), _text("", "stop")],
    ]
    await client.stream_text("A")
    history = client._conversation_history
    assert not [m for m in history if isinstance(m, dict)]
    assert not any(isinstance(m, AIMessage) and "我看看" in m.content for m in history)
