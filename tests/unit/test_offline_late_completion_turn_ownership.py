"""A retired Offline client's late completion must not close the host's newer turn.

``close()`` retires the active generation, but the cut reply still runs its
completion when its stream unwinds, because nothing else closes that turn (the
hot-swap close has no other closer). The stream can unwind long after the
close: a reply parked in a slow tool call or a retry backoff wakes up only when
that await returns. By then ``end_session`` may have installed a new session
and the user may have started a new turn, and a completion that read the
shared per-turn state at call time closed THAT turn: it took the new request
id, sealed the new bubble, ended cross_server's assistant turn, flushed the new
turn's text and ran the wrap-up against the new session.

The end-to-end tests drive the real ``OmniOfflineClient.stream_text`` /
``close()`` under a scripted provider (the real tool loop runs; only
``llm.astream``, the tool handler and the backoff sleep are fakes) and the real
Core text path (``_process_stream_data_internal`` -> ``handle_response_complete``)
on a manager double.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import main_logic.core as core_module
import main_logic.omni_offline_client._streaming as offline_streaming
from main_logic.tool_calling import ToolDefinition, ToolResult
from tests.unit.test_avatar_interaction_payload_contract import (
    _builtin_runtime,
    _fist_payload,
)
from tests.unit.test_chat_context_reinjection import (
    _manager as _swap_ready_manager,
    _swap,
)
from tests.unit.test_core_game_route_memory_contract import (
    _FakeAliveThread,
    _FakeConnectedWebSocket,
    _FakeQueue,
    _make_callback_media_manager,
    _make_manager,
)
from tests.unit.test_hot_swap_cancellation import _drain_task
from tests.unit.test_offline_provider_frame_publish import _connection_error, _make_client
from utils.llm_client import LLMStreamChunk

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _no_side_channels(monkeypatch):
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", MagicMock())
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda *_: None)


def _text(content, finish=None):
    return LLMStreamChunk(content=content, finish_reason=finish)


def _tool_call(call_id):
    return LLMStreamChunk(content="", finish_reason="tool_calls", tool_call_deltas=[
        {"index": 0, "id": call_id, "type": "function",
         "function": {"name": "lookup", "arguments": "{}"}},
    ])


def _client(script, *, tool=None):
    """A real client over the real tool loop with a scripted provider.

    ``script`` lists one provider response per request: a list of chunks
    (a callable is awaited in place, to park the stream) or an exception
    raised before the first chunk. ``tool`` is the tool handler.
    """
    client, _ = _make_client()
    del client._astream_visible_with_tools  # the real filter + tool loop
    client._fire_bus_task = lambda coro: coro.close()
    client.master_name = "M"
    client.enable_response_guard = False
    client._recent_responses = []
    client._max_recent_responses = 5
    client._repetition_threshold = 0.8
    client._notify_reasoning_done = AsyncMock()
    client._user_language_provider = lambda: "zh"
    client.max_tool_iterations = 2
    client.on_tool_call = tool
    client.on_tool_round_start = None
    client._tool_definitions = [ToolDefinition(
        name="lookup", description="lookup",
        parameters={"type": "object", "properties": {}},
    )]
    client._openai_tools_unsupported = False
    client._openai_tools_unsupported_with_images = False
    client._genai_tools_unsupported = False
    client._use_genai_sdk = False
    client._genai_client = None
    requests = []

    async def play(step):
        if isinstance(step, BaseException):
            raise step
        for item in step:
            if callable(item):
                await item()
            else:
                yield item

    def astream(messages, **_kwargs):
        requests.append(list(messages))
        return play(script[min(len(requests), len(script)) - 1])

    client.llm = SimpleNamespace(astream=astream, max_completion_tokens=100)
    return client


class _Tracker:
    def __init__(self):
        self.ai_messages = []

    def on_user_message(self, text=None, now=None):
        pass

    def on_ai_message(self, text=None, now=None):
        self.ai_messages.append(text)


def _observe(mgr):
    """What a turn end touches: the frontend, cross_server, the tracker, the wrap-up."""
    mgr.websocket = _FakeConnectedWebSocket()
    mgr._activity_tracker = _Tracker()
    mgr._finalize_turn_after_emit = AsyncMock()
    mgr.send_user_activity = AsyncMock()
    return mgr


def _wire(mgr, client):
    """Bind a client's callbacks to the manager, as ``_create_offline_vlm_client`` does."""
    client.on_text_delta = mgr.handle_text_data
    client.on_response_done = mgr.handle_response_complete
    client.on_response_discarded = mgr.handle_response_discarded
    return client


def _ws(mgr, data):
    return [m for m in mgr.websocket.sent if m.get("data") == data or m.get("type") == data]


def _sync(mgr, data):
    return [m for m in mgr.sync_message_queue.messages if m.get("data") == data]


async def _text_turn(mgr, text, request_id):
    await core_module.LLMSessionManager._process_stream_data_internal(
        mgr, {"input_type": "text", "data": text, "request_id": request_id},
    )


def _parked_tool():
    entered, release = asyncio.Event(), asyncio.Event()

    async def tool(call):
        entered.set()
        await release.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    return tool, entered, release


class _ParkedBackoff:
    """Stands in for ``_streaming``'s asyncio module: only the retry sleep parks."""

    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def sleep(self, _delay):
        self.entered.set()
        await self.release.wait()


# ── End to end: a reply of a closed client unwinds after a newer turn began ──

async def test_a_late_completion_leaves_a_new_text_turn_alone():
    """The reported race: A parked in a slow tool, end_session, B mid-reply."""
    tool, tool_entered, tool_release = _parked_tool()
    old = _client([[_text("我查一下"), _tool_call("c1")]], tool=tool)
    mgr = _observe(_make_callback_media_manager(old))
    _wire(mgr, old)
    turn_a = asyncio.create_task(_text_turn(mgr, "查天气", "req-A"))
    await asyncio.wait_for(tool_entered.wait(), 5)

    # end_session closes the client (A stays parked in its tool); a new
    # session is installed and the user's turn B is mid-reply.
    await old.close()
    mgr.session = None
    b_speaking, b_release = asyncio.Event(), asyncio.Event()

    async def park_b():
        b_speaking.set()
        await b_release.wait()

    mgr.session = _wire(mgr, _client([[_text("B说到一半"), park_b, _text("，说完"), _text("", "stop")]]))
    turn_b = asyncio.create_task(_text_turn(mgr, "换个话题", "req-B"))
    await asyncio.wait_for(b_speaking.wait(), 5)
    b_partial_text = mgr._current_ai_turn_text

    # A's tool returns; its stream unwinds (the closed client can make no
    # further request) and its discard and completion run now.
    tool_release.set()
    await asyncio.wait_for(turn_a, 5)

    assert _ws(mgr, "turn end") == [], "B's bubble was sealed by A's completion"
    assert _sync(mgr, "turn end") == [], "cross_server's turn (B) was ended by A"
    assert _ws(mgr, "response_discarded") == [] and _sync(mgr, "response_discarded_clear") == []
    assert mgr._active_text_request_id == "req-B"
    assert mgr._current_ai_turn_text == b_partial_text, "B's partial text was flushed"
    assert mgr._activity_tracker.ai_messages == []
    mgr._finalize_turn_after_emit.assert_not_awaited()

    b_release.set()
    await asyncio.wait_for(turn_b, 5)
    assert [m["request_id"] for m in _ws(mgr, "turn end")] == ["req-B"]
    assert [m["request_id"] for m in _sync(mgr, "turn end")] == ["req-B"]
    assert mgr._active_text_request_id is None
    assert mgr._activity_tracker.ai_messages == [b_partial_text + "，说完"]
    mgr._finalize_turn_after_emit.assert_awaited_once()


async def test_a_late_completion_leaves_a_new_voice_turn_alone(monkeypatch):
    """A voice turn takes a fresh speech id but leaves the text request id as it
    was, so the request id alone cannot see it. A, parked in a retry backoff,
    wakes to a closed client and completes without a discard: it must release
    its own request id, or the voice turn's end would carry it."""
    backoff = _ParkedBackoff()
    monkeypatch.setattr(offline_streaming, "asyncio", backoff)
    old = _client([_connection_error()])
    mgr = _observe(_make_callback_media_manager(old))
    _wire(mgr, old)
    turn_a = asyncio.create_task(_text_turn(mgr, "查天气", "req-A"))
    await asyncio.wait_for(backoff.entered.wait(), 5)

    await old.close()
    mgr.session = object()  # the voice session end_session/start_session installed
    await mgr.handle_new_message()  # the user's utterance starts turn V
    await mgr.handle_text_data("V说到一半", True)
    v_partial_text = mgr._current_ai_turn_text

    backoff.release.set()
    await asyncio.wait_for(turn_a, 5)

    assert _ws(mgr, "turn end") == [] and _sync(mgr, "turn end") == []
    assert mgr._active_text_request_id is None, "A's request id would ride on V's turn end"
    assert mgr._current_ai_turn_text == v_partial_text
    assert mgr._activity_tracker.ai_messages == []
    mgr._finalize_turn_after_emit.assert_not_awaited()


async def test_a_retired_clients_discard_leaves_a_new_voice_turn_alone():
    """The same reply's discard (the closed client fails its post-tool request)
    is request-bound, and a voice turn leaves A's request id current, so only
    the reply snapshot keeps it off the voice turn's output."""
    tool, tool_entered, tool_release = _parked_tool()
    old = _client([[_text("我查一下"), _tool_call("c1")]], tool=tool)
    mgr = _observe(_make_callback_media_manager(old))
    mgr._clear_tts_pipeline = AsyncMock()
    _wire(mgr, old)
    turn_a = asyncio.create_task(_text_turn(mgr, "查天气", "req-A"))
    await asyncio.wait_for(tool_entered.wait(), 5)

    await old.close()
    mgr.session = object()
    await mgr.handle_new_message()
    mgr._clear_tts_pipeline.reset_mock()

    tool_release.set()
    await asyncio.wait_for(turn_a, 5)

    assert _ws(mgr, "response_discarded") == [], "the frontend would clear V's bubble"
    assert _sync(mgr, "response_discarded_clear") == [], "cross_server would drop V's text"
    mgr._clear_tts_pipeline.assert_not_awaited()
    assert _ws(mgr, "turn end") == [] and _sync(mgr, "turn end") == []


async def test_a_late_completion_with_no_newer_turn_still_closes_its_turn():
    """end_session with nothing after it: the cut reply's completion is the only
    thing that closes its turn, so it still does -- which is why a guard on the
    client's identity alone is not the fix."""
    tool, tool_entered, tool_release = _parked_tool()
    old = _client([[_text("我查一下"), _tool_call("c1")]], tool=tool)
    mgr = _observe(_make_callback_media_manager(old))
    _wire(mgr, old)
    turn_a = asyncio.create_task(_text_turn(mgr, "查天气", "req-A"))
    await asyncio.wait_for(tool_entered.wait(), 5)

    await old.close()
    mgr.session = object()  # a new session, but no new turn

    tool_release.set()
    await asyncio.wait_for(turn_a, 5)

    assert [m["request_id"] for m in _ws(mgr, "turn end")] == ["req-A"]
    assert [m["request_id"] for m in _sync(mgr, "turn end")] == ["req-A"]
    assert mgr._active_text_request_id is None
    mgr._finalize_turn_after_emit.assert_awaited_once()
    assert mgr._open_reply_turn is None, "a returned reply must not pin its closed client"


async def test_a_reply_cut_by_the_hot_swap_still_closes_its_turn(monkeypatch):
    """The real final swap closes the old client under A and rotates the speech
    id after promoting, without starting a turn. A's late completion is still
    the only thing that closes A's turn."""
    tool, tool_entered, tool_release = _parked_tool()
    old = _client([[_text("我查一下"), _tool_call("c1")]], tool=tool)
    mgr, pending = _swap_ready_manager(monkeypatch)
    for name, value in vars(_make_callback_media_manager(old)).items():
        if name not in vars(mgr):
            setattr(mgr, name, value)
    mgr.session = old
    mgr.is_active = True
    _observe(mgr)
    _wire(mgr, old)

    async def memory_get(*_args, **_kwargs):
        return SimpleNamespace(is_success=True, text="MEMORY\n")

    monkeypatch.setattr(
        "utils.internal_http_client.get_internal_http_client",
        lambda: SimpleNamespace(get=memory_get),
    )
    turn_a = asyncio.create_task(_text_turn(mgr, "查天气", "req-A"))
    try:
        await asyncio.wait_for(tool_entered.wait(), 5)
        await mgr._background_prepare_pending_session()
        a_speech_id = mgr.current_speech_id
        await _swap(mgr, pending)
        assert mgr.current_speech_id != a_speech_id, "fixture must reach the post-promote rotation"

        tool_release.set()
        await asyncio.wait_for(turn_a, 5)
    finally:
        tool_release.set()
        await _drain_task(turn_a)
        await _drain_task(mgr.message_handler_task)

    assert len(_ws(mgr, "turn end")) == 1
    assert len(_sync(mgr, "turn end")) == 1
    assert mgr._active_text_request_id is None
    mgr._finalize_turn_after_emit.assert_awaited_once()


# ── The pieces on their own ─────────────────────────────────────────────────

def _core_manager():
    mgr = _observe(_make_manager())
    mgr.session = object()
    mgr._open_reply_turn = None
    return mgr


def _retired_reply(mgr, *, request_id="req-A", meta=None):
    reply_turn = mgr._begin_reply_turn(
        speech_id=mgr.current_speech_id, request_id=request_id, meta=meta,
    )
    reply_turn.session = object()  # a client that is no longer mgr.session
    mgr._active_text_request_id = request_id
    return reply_turn


async def test_a_turn_starting_while_the_tts_done_waits_is_left_alone():
    """The ownership check runs again after the one await before the turn end,
    and the TTS done is bound to the reply's own speech id."""
    mgr = _core_manager()
    mgr.use_tts = True
    mgr.tts_thread = _FakeAliveThread()
    mgr.tts_cache_lock = asyncio.Lock()
    mgr._request_tts_done_locked = MagicMock(return_value="queued")
    reply_turn = _retired_reply(mgr)

    async with mgr.tts_cache_lock:
        completion = asyncio.create_task(mgr.handle_response_complete(reply_turn=reply_turn))
        await asyncio.sleep(0)
        mgr.current_speech_id = "speech-B"  # turn B starts meanwhile
    await asyncio.wait_for(completion, 5)

    mgr._request_tts_done_locked.assert_not_called()
    assert _ws(mgr, "turn end") == [] and _sync(mgr, "turn end") == []
    mgr._finalize_turn_after_emit.assert_not_awaited()


async def test_a_truncation_recovery_keeps_its_reply_the_owner():
    """A recovery re-speaks the reply under a fresh speech id. That rotation
    starts no turn, so a reply whose client was retired meanwhile (a hot-swap
    promotion can land in the recovery's awaits) still finishes its recovery."""
    mgr = _core_manager()
    mgr.use_tts = True
    mgr._clear_tts_pipeline = AsyncMock()
    mgr.feed_tts_chunk = AsyncMock()
    mgr._request_tts_done_for_turn = AsyncMock(return_value="queued")
    reply_turn = _retired_reply(mgr)

    await mgr.handle_response_discarded(
        "length_truncated", 1, 1, False,
        '{"code": "RESPONSE_LENGTH_TRUNCATED", "text": "截断到这里。"}',
        request_id="req-A",
        reply_turn=reply_turn,
    )

    assert [m["request_id"] for m in _ws(mgr, "turn end")] == ["req-A"]
    assert [m["request_id"] for m in _sync(mgr, "turn end")] == ["req-A"]
    assert mgr._reply_turn_is_current(reply_turn)


@pytest.mark.parametrize("ending", ["completion", "truncation_recovery"])
async def test_a_reply_ends_its_turn_without_a_meta_it_did_not_stage(ending):
    """``_pending_turn_meta`` belongs to the reply that staged it (an avatar
    interaction, parked in a tool). A text reply ending its turn meanwhile, by
    completing or by a truncation recovery, must not carry it away, nor let it
    make its own recovered text ephemeral."""
    mgr = _core_manager()
    mgr.session = SimpleNamespace(_conversation_history=[])
    avatar_meta = {"kind": "avatar_interaction", "interaction_id": "i-1"}
    mgr._pending_turn_meta = avatar_meta
    reply_turn = mgr._begin_reply_turn(speech_id=mgr.current_speech_id, request_id="req-B")
    reply_turn.session = mgr.session
    mgr._active_text_request_id = "req-B"

    if ending == "completion":
        await mgr.handle_response_complete(reply_turn=reply_turn)
    else:
        await mgr.handle_response_discarded(
            "length_truncated", 1, 1, False,
            '{"code": "RESPONSE_LENGTH_TRUNCATED", "text": "截断到这里。"}',
            request_id="req-B",
            reply_turn=reply_turn,
        )
        assert [m.content for m in mgr.session._conversation_history] == ["截断到这里。"]

    assert [("meta" in m, m["request_id"]) for m in _ws(mgr, "turn end")] == [(False, "req-B")]
    assert mgr._pending_turn_meta is avatar_meta


@pytest.mark.parametrize("retired", [True, False])
async def test_an_avatar_reply_binds_its_completion_to_its_own_turn(monkeypatch, retired):
    """The avatar path hands prompt_ephemeral a completion bound to its reply.
    Retired mid-reply while a text turn started, it ends nothing. On its live
    client it still ends its own turn, with its own meta and no request id,
    and leaves the text turn's request id alone."""
    runtime = _builtin_runtime(monkeypatch)
    runtime._takeover_active = False
    runtime.use_tts = False
    runtime.tts_thread = None
    runtime.sync_message_queue = _FakeQueue()
    runtime._current_ai_turn_text = ""
    runtime._active_text_request_id = None
    runtime._open_reply_turn = None
    _observe(runtime)

    async def prompt_ephemeral(_instruction, *, response_done_callback=None, **_kwargs):
        if retired:
            runtime.session = object()  # end_session retired this client
        runtime.current_speech_id = "speech-B"  # and a text turn started
        runtime._active_text_request_id = "req-B"
        # The real client falls back to the session callback the same way.
        await (response_done_callback or runtime.handle_response_complete)()
        return True

    runtime.session.prompt_ephemeral = prompt_ephemeral
    result = await runtime.handle_avatar_interaction(_fist_payload("fist-1"))

    expected = [] if retired else [(None, "avatar_interaction")]
    assert [(m["request_id"], m["meta"]["kind"]) for m in _ws(runtime, "turn end")] == expected
    assert [(m.get("request_id"), m["meta"]["kind"]) for m in _sync(runtime, "turn end")] == expected
    assert runtime._active_text_request_id == "req-B"
    assert result["accepted"] is False and runtime._pending_turn_meta is None
    assert runtime._open_reply_turn is None, "a returned reply must not pin its client"


async def test_a_live_clients_interrupted_reply_still_ends_its_own_turn():
    """On a live client, turn succession is the interruption path's business and
    the interrupted reply keeps ending its turn (cross_server needs it before the
    interrupter's reply) -- but with its own request id, not the interrupter's."""
    a_speaking, a_release = asyncio.Event(), asyncio.Event()
    b_waiting, b_release = asyncio.Event(), asyncio.Event()

    async def park_a():
        a_speaking.set()
        await a_release.wait()

    async def park_b():
        b_waiting.set()
        await b_release.wait()

    client = _client([
        [_text("A说到一半"), park_a, _text("A后半"), _text("", "stop")],
        [park_b, _text("B的回答"), _text("", "stop")],
    ])
    mgr = _observe(_make_callback_media_manager(client))
    _wire(mgr, client)
    turn_a = asyncio.create_task(_text_turn(mgr, "第一句", "req-A"))
    await asyncio.wait_for(a_speaking.wait(), 5)
    turn_b = asyncio.create_task(_text_turn(mgr, "第二句", "req-B"))  # interrupts A
    await asyncio.wait_for(b_waiting.wait(), 5)

    a_release.set()
    await asyncio.wait_for(turn_a, 5)
    assert [m["request_id"] for m in _sync(mgr, "turn end")] == ["req-A"]
    assert mgr._active_text_request_id == "req-B"

    b_release.set()
    await asyncio.wait_for(turn_b, 5)
    assert [m["request_id"] for m in _sync(mgr, "turn end")] == ["req-A", "req-B"]


async def test_prompt_ephemeral_runs_the_bound_completion_in_place_of_the_session_one():
    client = _client([[_text("好的。"), _text("", "stop")]])
    client.on_response_done = AsyncMock()
    bound = AsyncMock()

    assert await client.prompt_ephemeral(
        "avatar", completion_mode="response", persist_response=False,
        response_done_callback=bound,
    ) is True
    bound.assert_awaited_once()
    client.on_response_done.assert_not_awaited()


async def test_the_carry_never_adopts_a_reply_a_newer_turn_superseded():
    """Only the reply still on the replaced speech id is carried; one that a
    turn without a snapshot (voice, proactive) already superseded stays behind."""
    mgr = _core_manager()
    reply_turn = mgr._begin_reply_turn(speech_id="speech-A", request_id="req-A")
    mgr.current_speech_id = "speech-V"  # a voice turn started after A
    replaced, mgr.current_speech_id = mgr.current_speech_id, "speech-promoted"
    mgr._carry_reply_turn(replaced)
    assert reply_turn.speech_id == "speech-A"


class _BlockingWebSocket(_FakeConnectedWebSocket):
    """A connected socket whose sends wait until released (backpressure)."""

    def __init__(self):
        super().__init__()
        self.sending, self.release = asyncio.Event(), asyncio.Event()

    async def send_json(self, payload):
        self.sending.set()
        await self.release.wait()
        await super().send_json(payload)


async def test_a_turn_starting_while_the_turn_end_is_sent_keeps_its_text_and_wrap_up():
    """The WS send of the turn end is an await too. A turn that starts during it
    writes into the shared AI text buffer: the ending reply must have flushed its
    own text before that send, and must leave the wrap-up to the newer turn."""
    mgr = _core_manager()
    mgr.websocket = _BlockingWebSocket()
    reply_turn = _retired_reply(mgr)
    mgr._current_ai_turn_text = "A的回答"

    completion = asyncio.create_task(mgr.handle_response_complete(reply_turn=reply_turn))
    await asyncio.wait_for(mgr.websocket.sending.wait(), 5)
    mgr.current_speech_id = "speech-B"  # turn B starts and speaks meanwhile
    mgr._current_ai_turn_text += "B说到一半"
    mgr.websocket.release.set()
    await asyncio.wait_for(completion, 5)

    assert mgr._activity_tracker.ai_messages == ["A的回答"]
    assert mgr._current_ai_turn_text == "B说到一半"
    mgr._finalize_turn_after_emit.assert_not_awaited()


@pytest.mark.parametrize("retired", [True, False])
async def test_a_truncation_discard_wraps_up_only_while_its_reply_owns_the_turn(retired):
    """The discard path's wrap-up deliberately survives losing the request to a
    newer one on a live client (skipping it chains into the truncation death
    loop). A retired client's reply whose host moved on has no session left to
    settle; finalizing would act on the newer turn's session mid-reply."""
    mgr = _core_manager()
    mgr._clear_tts_pipeline = AsyncMock()
    reply_turn = mgr._begin_reply_turn(speech_id=mgr.current_speech_id, request_id="req-A")
    reply_turn.session = object() if retired else mgr.session
    mgr.current_speech_id = "speech-B"  # turn B owns the host now
    mgr._active_text_request_id = "req-B"

    await mgr.handle_response_discarded(
        "length_truncated", 1, 1, False,
        '{"code": "RESPONSE_LENGTH_TRUNCATED", "text": "截断到这里。"}',
        request_id="req-A",
        reply_turn=reply_turn,
    )

    assert _ws(mgr, "turn end") == [] and _sync(mgr, "turn end") == []
    assert mgr._finalize_turn_after_emit.await_count == (0 if retired else 1)


@pytest.mark.parametrize("current", [True, False])
async def test_a_stale_reply_skips_the_takeover_cleanup(current):
    """During a game takeover an ordinary completion clears the shared output.
    A retired reply that no longer owns the turn must not: that would cut the
    takeover's own speech and wipe its bookkeeping. A current one still does."""
    mgr = _core_manager()
    mgr._takeover_active = True
    mgr._clear_tts_pipeline = AsyncMock()
    reply_turn = _retired_reply(mgr)
    if not current:
        mgr.current_speech_id = "speech-mirror"  # the takeover speaks meanwhile
    mgr._current_ai_turn_text = "镜像台词"
    mgr._active_text_request_id = "req-mirror"

    await mgr.handle_response_complete(reply_turn=reply_turn)

    assert mgr._clear_tts_pipeline.await_count == (1 if current else 0)
    assert mgr._current_ai_turn_text == ("" if current else "镜像台词")
    assert mgr._active_text_request_id == (None if current else "req-mirror")


# ── A final discard that already ended the turn ─────────────────────────────

async def test_a_too_long_final_discard_ends_its_turn_once():
    """The length guard discards a runaway reply for good (repeated text past
    the user's cap, no rerolls left), and the discard's recovery ends the turn:
    the placeholder, the turn end, the wrap-up. The stream still runs the
    reply's completion when it unwinds, and that completion used to end the
    same turn again: a second turn end on both channels and a second wrap-up."""
    client = _client([[_text("ahah" * 200), _text("", "stop")]])
    client.enable_response_guard = True
    client.max_response_rerolls = 0
    mgr = _observe(_make_callback_media_manager(client))
    mgr._get_text_guard_max_length = lambda: 30  # the user's reply cap
    _wire(mgr, client)

    await asyncio.wait_for(_text_turn(mgr, "说点什么", "req-A"), 5)

    discards = [json.loads(m["message"])["code"] for m in _ws(mgr, "response_discarded")]
    assert discards == ["RESPONSE_TOO_LONG"], "fixture must reach the too-long final discard"
    assert [m["request_id"] for m in _ws(mgr, "turn end")] == ["req-A"]
    assert [m["request_id"] for m in _sync(mgr, "turn end")] == ["req-A"]
    mgr._finalize_turn_after_emit.assert_awaited_once()
    placeholder = client._conversation_history[-1].content
    assert mgr._activity_tracker.ai_messages == [placeholder]
    assert mgr._active_text_request_id is None


@pytest.mark.parametrize("message", [
    '{"code": "RESPONSE_TOO_LONG"}',
    '{"code": "RESPONSE_LENGTH_TRUNCATED", "text": "截断到这里。"}',
], ids=["too_long", "length_truncated"])
async def test_a_final_discards_recovery_leaves_its_completion_nothing_to_end(message):
    """Both recoveries end the reply's turn themselves, and the completion that
    follows them must not end it again."""
    mgr = _core_manager()
    mgr.user_language = "zh-CN"  # the too-long placeholder is localized
    mgr.session = SimpleNamespace(_conversation_history=[])
    mgr._clear_tts_pipeline = AsyncMock()
    reply_turn = mgr._begin_reply_turn(speech_id=mgr.current_speech_id, request_id="req-A")
    reply_turn.session = mgr.session
    mgr._active_text_request_id = "req-A"

    await mgr.handle_response_discarded(
        "length>30", 1, 1, False, message, request_id="req-A", reply_turn=reply_turn,
    )
    assert len(_ws(mgr, "turn end")) == 1, "fixture must let the recovery end the turn"
    await mgr.handle_response_complete(reply_turn=reply_turn)

    assert [m["request_id"] for m in _ws(mgr, "turn end")] == ["req-A"]
    assert [m["request_id"] for m in _sync(mgr, "turn end")] == ["req-A"]
    mgr._finalize_turn_after_emit.assert_awaited_once()


async def test_a_reply_whose_turn_already_ended_leaves_a_later_takeover_alone():
    """A takeover that starts between the discard and the completion speaks
    under its own turn; the completion of a reply already ended must not run
    the takeover cleanup over it."""
    mgr = _core_manager()
    mgr.user_language = "zh-CN"
    mgr.session = SimpleNamespace(_conversation_history=[])
    mgr._clear_tts_pipeline = AsyncMock()
    reply_turn = mgr._begin_reply_turn(speech_id=mgr.current_speech_id, request_id="req-A")
    reply_turn.session = mgr.session
    mgr._active_text_request_id = "req-A"
    await mgr.handle_response_discarded(
        "length>30", 1, 1, False, '{"code": "RESPONSE_TOO_LONG"}',
        request_id="req-A", reply_turn=reply_turn,
    )
    mgr._clear_tts_pipeline.reset_mock()
    mgr._takeover_active = True  # the takeover starts and speaks meanwhile
    mgr._current_ai_turn_text = "镜像台词"

    await mgr.handle_response_complete(reply_turn=reply_turn)

    mgr._clear_tts_pipeline.assert_not_awaited()
    assert mgr._current_ai_turn_text == "镜像台词"


async def test_a_final_discard_that_ended_nothing_leaves_its_completion_to_end_the_turn():
    """Only a turn end the recovery actually sent closes the reply. On a live
    client a newer request already holds the shared output, so the discard
    sends nothing, and the reply's completion still ends its own turn, as an
    interrupted reply does."""
    mgr = _core_manager()
    mgr.session = SimpleNamespace(_conversation_history=[])
    mgr._clear_tts_pipeline = AsyncMock()
    reply_turn = mgr._begin_reply_turn(speech_id=mgr.current_speech_id, request_id="req-A")
    reply_turn.session = mgr.session
    mgr._active_text_request_id = "req-B"  # B was submitted meanwhile

    await mgr.handle_response_discarded(
        "length>30", 1, 1, False, '{"code": "RESPONSE_TOO_LONG"}',
        request_id="req-A", reply_turn=reply_turn,
    )
    assert _ws(mgr, "turn end") == [] and _sync(mgr, "turn end") == []

    await mgr.handle_response_complete(reply_turn=reply_turn)

    assert [m["request_id"] for m in _ws(mgr, "turn end")] == ["req-A"]
    assert [m["request_id"] for m in _sync(mgr, "turn end")] == ["req-A"]
    assert mgr._active_text_request_id == "req-B"
