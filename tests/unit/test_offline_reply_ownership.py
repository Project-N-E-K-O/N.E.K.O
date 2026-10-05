"""Every offline reply is closed exactly once, by whoever owns its close.

A generation's own completion callback closes it, unless someone took the
close over: an interrupter (``handle_interruption`` cancelled it mid-reply or
claimed its pending completion), or a user reply that began over it without an
interruption (``_begin_response_generation`` displaced it and reported it to
``on_response_displaced``). Core closes a taken-over reply without touching the
new turn's request id, and the wrap-up the skipped completion would have run
is owed and paid by one idle-gated settle, which the client triggers when its
last reply call returns (``on_idle``).

Real ``OmniOfflineClient`` over a scripted provider (only the transport is
faked) and the real core ``TurnMixin`` paths on the manager doubles from
test_core_game_route_memory_contract.
"""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import main_logic.core as core_module
import main_logic.omni_offline_client._streaming as offline_streaming
from main_logic.core._shared import _ReplyTurn
from main_logic.omni_offline_client._lifecycle import InterruptedReply
from main_logic.tool_calling import ToolResult
from tests.unit.test_core_game_route_memory_contract import (
    _FakeConnectedWebSocket,
    _make_callback_media_manager,
    _make_manager,
    _make_offline_session_for_callback_media,
    _make_transcript_manager,
)
from tests.unit.test_offline_late_completion_turn_ownership import _ParkedBackoff
from tests.unit.test_offline_provider_frame_publish import _connection_error
from tests.unit.test_offline_turn_cancellation_e2e import _client, _text, _tool_calls
from utils.llm_client import AIMessage, HumanMessage

pytestmark = pytest.mark.unit

M = core_module.LLMSessionManager


@pytest.fixture(autouse=True)
def _no_token_tracker(monkeypatch):
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", MagicMock())


async def _drain(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


def _bind_like_lifecycle(mgr, client):
    """What ``_create_offline_vlm_client`` binds (looked up, so the file also
    runs where a hook does not exist yet)."""
    client.on_response_done = mgr.handle_response_complete
    client.on_proactive_done = mgr.handle_proactive_complete
    displaced = getattr(mgr, "_close_displaced_offline_turn", None)
    if displaced is not None:
        client.on_response_displaced = displaced
    idle = getattr(mgr, "_on_offline_session_idle", None)
    if idle is not None:
        client.on_idle = idle


def _turn_ends(mgr):
    return [
        m for m in mgr.sync_message_queue.messages
        if isinstance(m, dict) and str(m.get("data", "")).startswith("turn end")
    ]


def _system(mgr):
    return [m for m in mgr.websocket.sent if m.get("type") == "system"]


# ── Finding 1: a displaced reply is closed by its displacer ──────────────────


def _wire_text_path(monkeypatch):
    """Real client, real core text path and completion; the text-delta sink
    appends to ``_current_ai_turn_text`` like ``handle_text_data``."""
    session = _client()
    mgr = _make_callback_media_manager(session)
    mgr.websocket = _FakeConnectedWebSocket()
    mgr._finalize_turn_after_emit = AsyncMock(
        side_effect=lambda: setattr(mgr, "_turn_wrap_up_owed", False)
    )
    notes = []
    mgr._note_ai_turn = lambda text=None, **_kw: notes.append(text)
    mgr._fire_task = asyncio.ensure_future

    async def on_text_delta(text, is_first, **_kw):
        mgr._current_ai_turn_text += text

    session.on_text_delta = on_text_delta
    _bind_like_lifecycle(mgr, session)
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda _n, _t: None)
    return session, mgr, notes


async def test_an_avatar_reply_begun_in_the_text_setup_window_is_closed_with_its_meta(
    monkeypatch,
):
    """handle_avatar_interaction passes its busy gate (greeting.py) after the
    text path's interrupt and before stream_text begins; the text reply then
    displaces it with no interrupt. The avatar turn is closed as its own turn
    with its isolation meta and no request id (that id is already the text
    turn's), and the text reply's own turn end still carries its id. The
    avatar reply was cut mid-stream, but no user input came after it, so the
    frontend's current bubble is still its own: it is sealed with its turn
    end before the text reply's first chunk."""
    session, mgr, notes = _wire_text_path(monkeypatch)
    meta = {"kind": "avatar_interaction", "interaction_id": "i-1"}
    text_began = asyncio.Event()
    avatar = []
    facts = {}

    async def park_avatar():
        await text_began.wait()

    async def text_begins():
        facts["request_id_at_displacement"] = mgr._active_text_request_id
        text_began.set()
        await _drain()

    session.script = [
        [_text("摸头好舒服，"), park_avatar, _text("还要摸摸。"), _text("", "stop")],
        [text_begins, _text("你好呀。"), _text("", "stop")],
    ]
    real_clear = mgr._clear_tts_pipeline

    async def clear_then_avatar_arrives():
        facts["gate_busy"] = bool(session._is_responding)
        mgr._pending_turn_meta = meta

        async def avatar_turn():  # greeting.py, around prompt_ephemeral
            delivered = await session.prompt_ephemeral(
                "avatar", completion_mode="response", persist_response=False,
            )
            if mgr._pending_turn_meta is meta:
                mgr._pending_turn_meta = None
            return delivered

        avatar.append(asyncio.create_task(avatar_turn()))
        await _drain()
        await real_clear()

    mgr._clear_tts_pipeline = clear_then_avatar_arrives
    await M._process_stream_data_internal(
        mgr, {"input_type": "text", "data": "在吗", "request_id": "req-text"},
    )
    await avatar[0]

    assert facts["gate_busy"] is False
    assert facts["request_id_at_displacement"] == "req-text"
    assert _turn_ends(mgr) == [
        {"type": "system", "data": "turn end", "meta": meta},
        {"type": "system", "data": "turn end", "request_id": "req-text"},
    ]
    assert notes == ["摸头好舒服，", "你好呀。"]
    assert _system(mgr) == [
        {"type": "system", "data": "turn end", "request_id": None, "meta": meta},
        {"type": "system", "data": "turn end", "request_id": "req-text"},
    ]
    assert mgr._turn_wrap_up_owed is False  # the text reply's completion paid it


async def test_a_text_reply_displaced_by_a_second_text_is_closed_on_its_own(monkeypatch):
    """Two typed messages in quick succession: B's interrupt runs before A
    begins and finds nothing, then B's stream_text displaces A."""
    session, mgr, notes = _wire_text_path(monkeypatch)
    a_streaming, b_began, b_parked = asyncio.Event(), asyncio.Event(), asyncio.Event()
    interrupts = []
    tasks = []

    async def park_a():
        a_streaming.set()
        await b_began.wait()

    async def b_begins():
        b_began.set()
        await _drain()

    session.script = [
        [_text("A说到一半，"), park_a, _text("A后半句。"), _text("", "stop")],
        [b_begins, _text("B的回复。"), _text("", "stop")],
    ]
    real_interrupt = session.handle_interruption

    async def interrupt_logged():
        kind = await real_interrupt()
        interrupts.append(kind)
        return kind

    session.handle_interruption = interrupt_logged
    real_clear = mgr._clear_tts_pipeline
    clears = []

    async def clear_hook():
        clears.append(1)
        if len(clears) == 1:  # A's setup window: B arrives
            tasks.append(asyncio.create_task(M._process_stream_data_internal(
                mgr, {"input_type": "text", "data": "B", "request_id": "req-B"},
            )))
            await b_parked.wait()
        await real_clear()

    real_inject = mgr._inject_pending_user_directives
    injects = []

    async def inject_hook():
        injects.append(1)
        if len(injects) == 1:  # B, before its request id is set
            b_parked.set()
            await a_streaming.wait()
        await real_inject()

    mgr._clear_tts_pipeline = clear_hook
    mgr._inject_pending_user_directives = inject_hook
    await M._process_stream_data_internal(
        mgr, {"input_type": "text", "data": "A", "request_id": "req-A"},
    )
    await tasks[0]

    await _drain()
    assert not any(interrupts)
    assert notes == ["A说到一半，", "B的回复。"]
    # A is closed from its own snapshot: its own id. B's input came before A
    # began, so A's bubble is still the frontend's current one: sealed (and
    # A's request released) before B's reply starts its own.
    assert _turn_ends(mgr) == [
        {"type": "system", "data": "turn end", "request_id": "req-A"},
        {"type": "system", "data": "turn end", "request_id": "req-B"},
    ]
    assert _system(mgr) == [
        {"type": "system", "data": "turn end", "request_id": "req-A"},
        {"type": "system", "data": "turn end", "request_id": "req-B"},
    ]


async def test_a_text_reply_displaced_by_a_voice_submit_is_closed_on_its_own(monkeypatch):
    """Independent ASR: the voice turn interrupted at speech onset (before A
    began); its submit at the final begins with no second interrupt."""
    session, mgr, notes = _wire_text_path(monkeypatch)
    a_streaming, voice_began = asyncio.Event(), asyncio.Event()

    async def park_a():
        a_streaming.set()
        await voice_began.wait()

    async def voice_begins():
        voice_began.set()
        await _drain()

    session.script = [
        [_text("A说到一半，"), park_a, _text("A后半句。"), _text("", "stop")],
        [voice_begins, _text("语音回复。"), _text("", "stop")],
    ]

    async def voice_final():
        await a_streaming.wait()
        await session.submit_external_voice_turn("语音", turn_id="t1")

    voice = asyncio.create_task(voice_final())
    await M._process_stream_data_internal(
        mgr, {"input_type": "text", "data": "A", "request_id": "req-A"},
    )
    await voice
    assert notes == ["A说到一半，", "语音回复。"]
    # A's id is still current at displacement (voice never sets one): A's
    # close carries and retires it, so the voice reply's turn end has none.
    assert _turn_ends(mgr) == [
        {"type": "system", "data": "turn end", "request_id": "req-A"},
        {"type": "system", "data": "turn end"},
    ]


async def test_a_displaced_text_reply_that_stored_its_id_last_leaves_no_dead_id(monkeypatch):
    """The same race, but B stored its request id before A stored A's, so the
    shared id names A when B displaces it. A's close retires A's own id: B's
    bound completion retires only req-B, and nothing else would, so later
    unbound replies would be tagged with the dead req-A."""
    session, mgr, notes = _wire_text_path(monkeypatch)
    a_streaming, b_began, b_parked = asyncio.Event(), asyncio.Event(), asyncio.Event()
    tasks = []

    async def park_a():
        a_streaming.set()
        await b_began.wait()

    async def b_begins():
        b_began.set()
        await _drain()

    session.script = [
        [_text("A说到一半，"), park_a, _text("A后半句。"), _text("", "stop")],
        [b_begins, _text("B的回复。"), _text("", "stop")],
    ]
    real_clear = mgr._clear_tts_pipeline
    clears = []

    async def clear_hook():
        clears.append(1)
        if len(clears) == 1:  # A's setup window: B arrives
            tasks.append(asyncio.create_task(M._process_stream_data_internal(
                mgr, {"input_type": "text", "data": "B", "request_id": "req-B"},
            )))
            await b_parked.wait()
        await real_clear()

    focus = []

    async def focus_hook(_text_):
        focus.append(mgr._active_text_request_id)
        if len(focus) == 1:  # B, its id already stored; A stores its own next
            b_parked.set()
            await a_streaming.wait()
        return False

    mgr._clear_tts_pipeline = clear_hook
    mgr._focus_inline_decision = focus_hook
    await M._process_stream_data_internal(
        mgr, {"input_type": "text", "data": "A", "request_id": "req-A"},
    )
    await tasks[0]
    await _drain()

    assert focus == ["req-B", "req-A"]
    assert _turn_ends(mgr) == [
        {"type": "system", "data": "turn end", "request_id": "req-A"},
        {"type": "system", "data": "turn end", "request_id": "req-B"},
    ]
    assert _system(mgr) == [
        {"type": "system", "data": "turn end", "request_id": "req-A"},
        {"type": "system", "data": "turn end", "request_id": "req-B"},
    ]
    assert mgr._active_text_request_id is None


async def test_an_interrupted_reply_is_closed_with_its_own_request_id(monkeypatch):
    """B's interrupt ran before A began (found nothing), B stored req-B and is
    still in its setup, A is live. A voice onset (or a command, or a third
    typed input) interrupts A: A is closed and abandoned as req-A, and B's
    request is left alone for B's own turn end."""
    session, mgr, notes = _wire_text_path(monkeypatch)
    a_streaming, a_release, b_parked, b_go = (asyncio.Event() for _ in range(4))
    tasks = []

    async def park_a():
        a_streaming.set()
        await a_release.wait()

    session.script = [
        [_text("A说到一半，"), park_a, _text("A后半句。"), _text("", "stop")],
        [_text("B的回复。"), _text("", "stop")],
    ]
    real_focus = mgr._focus_inline_decision

    async def focus_hook(text):
        if text == "A":  # A stored req-A; B arrives and interrupts nothing
            tasks.append(asyncio.create_task(M._process_stream_data_internal(
                mgr, {"input_type": "text", "data": "B", "request_id": "req-B"},
            )))
            await b_parked.wait()
        elif text == "B":  # B stored req-B and has not begun
            b_parked.set()
            await b_go.wait()
        return await real_focus(text)

    mgr._focus_inline_decision = focus_hook
    turn_a = asyncio.create_task(M._process_stream_data_internal(
        mgr, {"input_type": "text", "data": "A", "request_id": "req-A"},
    ))
    await asyncio.wait_for(a_streaming.wait(), 5)
    assert mgr._active_text_request_id == "req-B"

    assert await mgr._interrupt_offline_reply(session)  # e.g. a voice onset
    assert _turn_ends(mgr) == [{"type": "system", "data": "turn end", "request_id": "req-A"}]
    assert _system(mgr) == [{"type": "system", "data": "turn abandoned", "request_id": "req-A"}]
    assert mgr._active_text_request_id == "req-B"

    a_release.set()
    b_go.set()
    await asyncio.wait_for(asyncio.gather(turn_a, *tasks), 5)
    assert [m.get("request_id") for m in _turn_ends(mgr)] == ["req-A", "req-B"]
    assert notes == ["A说到一半，", "B的回复。"]


async def test_a_displaced_generation_is_handed_to_exactly_one_closer():
    client = _client()
    began = asyncio.Event()

    async def park():
        await began.wait()

    async def new_begins():
        began.set()
        await _drain()

    client.script = [
        [_text("摸头，"), park, _text("还要"), _text("", "stop")],
        [new_begins, _text("好"), _text("", "stop")],
    ]
    client.on_response_displaced = MagicMock()
    avatar = asyncio.create_task(
        client.prompt_ephemeral("avatar", completion_mode="response", persist_response=False)
    )
    await _drain()
    await client.stream_text("hi")
    await avatar
    assert client.on_response_done.await_count == 1  # the new reply's own
    client.on_response_displaced.assert_called_once_with("response")
    assert client.on_response_displaced.call_args.args[0].finished is False


@pytest.mark.parametrize("state", ["live", "guard_paused", "completion_pending"])
async def test_a_non_user_reply_never_displaces_another(state):
    """prompt_ephemeral declines to begin over a reply that is live,
    guard-paused or waiting on its completion: no provider request, nothing
    displaced, and the avatar meta is left for the caller to drop."""
    client = _client()
    client.on_response_displaced = MagicMock()
    seen = {}

    async def avatar_arrives():
        if seen:
            return  # once (a tree without the check would recurse)
        seen["arrived"] = True
        if state == "guard_paused":
            client._pause_response_generation(client._active_response_generation)
        requests_before = len(client.requests)
        seen["delivered"] = await client.prompt_ephemeral(
            "avatar", completion_mode="response", persist_response=False,
        )
        seen["requests"] = len(client.requests) - requests_before
        if state == "guard_paused":
            client._resume_response_generation(client._active_response_generation)

    if state == "completion_pending":
        async def cleanup(_seq):
            await avatar_arrives()

        client._notify_reasoning_done = cleanup
        client.script = [[_text("回调说完。"), _text("", "stop")]]
        await client.prompt_ephemeral("callback")
        client.on_proactive_done.assert_awaited_once()
    else:
        client.script = [[_text("文本前半，"), avatar_arrives, _text("后半。"), _text("", "stop")]]
        await client.stream_text("T")
        client.on_response_done.assert_awaited_once()
    assert seen == {"arrived": True, "delivered": False, "requests": 0}
    client.on_response_displaced.assert_not_called()


async def test_an_avatar_over_a_guard_paused_text_reply_does_not_tag_it(monkeypatch):
    """The avatar gate reads only _is_responding, which a guard pause drops.
    The avatar must not begin (it would cut the text reply, and whichever
    close runs would carry the avatar meta over the text)."""
    session, mgr, notes = _wire_text_path(monkeypatch)
    meta = {"kind": "avatar_interaction", "interaction_id": "i-2"}

    async def pause_and_let_avatar_in():
        session._pause_response_generation(session._active_response_generation)
        mgr._pending_turn_meta = meta
        await session.prompt_ephemeral("avatar", completion_mode="response", persist_response=False)
        if mgr._pending_turn_meta is meta:  # greeting.py drops unconsumed meta
            mgr._pending_turn_meta = None
        session._resume_response_generation(session._active_response_generation)

    session.script = [
        [_text("文本前半，"), pause_and_let_avatar_in, _text("文本后半。"), _text("", "stop")],
        [_text("摸头。"), _text("", "stop")],
    ]
    mgr._active_text_request_id = "req-T"
    await session.stream_text("T")
    assert _turn_ends(mgr) == [{"type": "system", "data": "turn end", "request_id": "req-T"}]
    assert notes == ["文本前半，文本后半。"]


async def test_taking_over_a_displaced_reply():
    client = _client()
    assert client._take_displaced_reply() == ""
    live = client._begin_response_generation("agent_callback")
    taken = client._take_displaced_reply()
    assert (taken, taken.finished) == ("agent_callback", False)
    assert client._take_interrupter_ownership(live) is True
    client._finish_response_generation(live)
    client._mark_completion_pending(7, "response")
    taken = client._take_displaced_reply()
    assert (taken, taken.finished) == ("response", True)
    assert client._completion_pending_generation is None
    assert client._take_completion(7) is False  # claimed: its completion is skipped
    assert client._take_displaced_reply() == ""


def test_a_displaced_agent_callback_leaves_the_request_id_and_meta_alone():
    mgr = _make_manager()
    mgr._note_ai_turn = lambda text=None, **_kw: None
    mgr._current_ai_turn_text = "回调说到一半"
    mgr._active_text_request_id = "req-new"
    meta = {"kind": "avatar_interaction"}
    mgr._pending_turn_meta = meta
    mgr._turn_wrap_up_owed = False
    M._close_displaced_offline_turn(mgr, "agent_callback")
    assert _turn_ends(mgr) == [{"type": "system", "data": "turn end agent_callback"}]
    assert (mgr._active_text_request_id, mgr._pending_turn_meta) == ("req-new", meta)
    assert mgr._turn_wrap_up_owed is False


async def test_the_offline_client_is_born_bound_to_its_closer_and_its_idle_settle():
    from main_logic.core import lifecycle as core_lifecycle
    from tests.unit.test_tool_image_protocol import _Manager, _result

    manager = _Manager(_result(), None)
    manager.lanlan_name, manager.master_name, manager.user_language = "l", "m", "zh"
    manager._make_thinking_active_callback = lambda session: None
    for callback in (
        "handle_text_data", "handle_text_input_transcript",
        "handle_output_transcript", "handle_connection_error",
        "handle_response_complete", "handle_repetition_detected",
        "handle_response_discarded", "send_status", "handle_proactive_complete",
        "_close_displaced_offline_turn", "_on_offline_session_idle",
    ):
        setattr(manager, callback, MagicMock(name=callback))
    endpoint = {"base_url": "https://x.test/v1", "api_key": "k", "model": "m"}
    session = core_lifecycle.LifecycleMixin._create_offline_vlm_client(
        manager,
        conversation_config=dict(endpoint),
        vision_config=dict(endpoint),
        tool_definitions=[],
        max_response_length=100,
        external_tts_enabled=False,
    )
    assert session.on_response_displaced is manager._close_displaced_offline_turn
    assert session.on_idle is manager._on_offline_session_idle


# ── Findings 2-5: the owed wrap-up and the busy flag ─────────────────────────


def _manager(client):
    """A real TurnMixin manager whose finalize can start a final swap and
    deliver a queued agent callback, bound to ``client`` like lifecycle."""
    mgr = _make_transcript_manager()
    mgr.session = client
    mgr.is_active = True
    mgr._note_ai_turn = lambda text=None, **_kw: None
    _bind_like_lifecycle(mgr, client)
    mgr._turn_wrap_up_owed = False
    mgr.is_hot_swap_imminent = False
    mgr.is_preparing_new_session = True
    mgr.summary_triggered_time = datetime.now()
    mgr.background_preparation_task = None
    mgr.pending_session_warmed_up_event = asyncio.Event()
    mgr.pending_session_warmed_up_event.set()
    mgr.final_swap_task = None
    mgr.pending_extra_replies = []
    mgr.pending_agent_callbacks = [{"summary": "queued during T1"}]
    mgr.wrap_ups = []

    async def swap():
        mgr.wrap_ups.append("final_swap")

    async def trigger():
        mgr.wrap_ups.append("agent_callbacks")

    mgr._perform_final_swap_sequence = swap
    mgr.trigger_agent_callbacks = trigger
    mgr._bg = []
    mgr._fire_task = lambda coro: mgr._bg.append(asyncio.ensure_future(coro))
    return mgr


def _parked_tool():
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(call):
        entered.set()
        await release.wait()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    return handler, entered, release


async def _mini_game_command(mgr, session):
    """turn.py mini-game path: interrupt, (awaits), then settle."""
    await mgr._interrupt_offline_reply(session)
    await asyncio.sleep(0)
    await mgr._settle_owed_turn_wrap_up()


async def test_a_voice_turn_dropped_after_interrupting_a_reply_still_wraps_that_reply_up():
    """Finding 2. T1 streams; a noise onset interrupts it (the ASR prepare
    call) and the voice turn is then dropped (empty final / echo suppression:
    no core call follows). T1's owed wrap-up is paid when T1's task ends,
    when main ran it from T1's own completion."""
    client = _client()
    mgr = _manager(client)

    async def noise_onset():
        await mgr._interrupt_offline_reply(client)

    client.script = [[_text("说到一半"), noise_onset, _text("late"), _text("", "stop")]]
    await client.stream_text("Q1")
    await _drain()
    assert mgr.wrap_ups == ["final_swap", "agent_callbacks"]
    assert mgr._turn_wrap_up_owed is False


async def test_a_command_wrap_up_waits_for_the_cancelled_task():
    """Finding 3. A mini-game command interrupts T1 inside its tool handler:
    the wrap-up runs once T1's task has finished, not while it is still in
    the handler."""
    handler, entered, release = _parked_tool()
    client = _client(handler=handler)
    mgr = _manager(client)
    client.script = [[_text("我查一下"), _tool_calls("c1")]]
    t1 = asyncio.ensure_future(client.stream_text("Q1"))
    await asyncio.wait_for(entered.wait(), 1)
    await _mini_game_command(mgr, client)
    await _drain()
    during = list(mgr.wrap_ups)
    release.set()
    await t1
    await _drain()
    assert during == []
    assert mgr.wrap_ups == ["final_swap", "agent_callbacks"]


async def test_a_mini_game_command_seals_its_own_turn_before_paying_the_owed_wrap_up():
    """The real command path (``_stream_data_now``, before any typed-input
    handling): T1 is interrupted inside its tool handler and its task ends
    while the command is still clearing the TTS pipeline. The client reports
    idle then, but the owed wrap-up (final swap, queued agent callbacks) is
    paid only once the command has sealed its own turn and sent its launch,
    so a delivered callback reply cannot take the session over before the
    command's user line and ``turn end agent_callback``."""
    handler, entered, release = _parked_tool()
    client = _client(handler=handler)
    mgr = _manager(client)
    mgr.websocket = _FakeConnectedWebSocket()
    client.script = [[_text("我查一下"), _tool_calls("c1")]]
    t1 = asyncio.ensure_future(client.stream_text("Q1"))
    await asyncio.wait_for(entered.wait(), 1)

    async def clear_tts_while_t1_ends():
        release.set()
        await t1
        await _drain()

    mgr._clear_tts_pipeline = clear_tts_while_t1_ends
    sent_at_wrap_up = []
    real_finalize = mgr._finalize_turn_after_emit

    async def finalize():
        sent_at_wrap_up.append(
            [m.get("data") if m.get("type") == "system" else m.get("type") for m in mgr.websocket.sent]
        )
        await real_finalize()

    mgr._finalize_turn_after_emit = finalize
    await M._stream_data_now(
        mgr, {"input_type": "text", "data": "/一起看", "request_id": "req-watch"},
    )
    await asyncio.gather(*mgr._bg, return_exceptions=True)
    await _drain()
    assert t1.done()
    assert sent_at_wrap_up == [["turn end agent_callback", "mini_game_invite_resolved"]]
    assert mgr.wrap_ups == ["final_swap", "agent_callbacks"]
    assert mgr._turn_wrap_up_owed is False
    assert getattr(mgr, "_reply_setup_depth", 0) == 0


@pytest.mark.parametrize(
    ("failure", "torn_down", "expected"),
    [
        (ValueError("ws"), False, ["final_swap", "agent_callbacks"]),
        (asyncio.CancelledError(), False, ["final_swap", "agent_callbacks"]),
        (asyncio.CancelledError(), True, []),
    ],
)
async def test_a_mini_game_command_that_fails_releases_the_hold(failure, torn_down, expected):
    """A command that raises still releases its hold on the owed wrap-up and
    settles it (no reply follows it). A cancelled one settles in a task of
    its own, since its session may live on and go idle no more; a torn-down
    session pays nothing (its debt is reset)."""
    client = _client()
    mgr = _manager(client)
    mgr.websocket = _FakeConnectedWebSocket()
    mgr._clear_tts_pipeline = AsyncMock()
    mgr._turn_wrap_up_owed = True

    async def fail(*_a, **_kw):
        if torn_down:
            mgr.is_active = False
        raise failure

    mgr.send_user_activity = AsyncMock(side_effect=fail)
    with pytest.raises(type(failure)):
        await M._stream_data_now(
            mgr, {"input_type": "text", "data": "/一起看", "request_id": "req-watch"},
        )
    await asyncio.gather(*mgr._bg, return_exceptions=True)
    assert getattr(mgr, "_reply_setup_depth", 0) == 0
    assert mgr.wrap_ups == expected


async def test_a_callback_reply_after_a_command_lands_after_the_interrupted_half():
    """Finding 3's consequence: a wrap-up run before the cancelled task
    committed its half let the callback reply land before it in history."""
    client = _client()
    mgr = _manager(client)
    gate = asyncio.Event()

    async def trigger():
        mgr.wrap_ups.append("agent_callbacks")
        client.script = [[_text("回调说完"), _text("", "stop")]]
        await client.prompt_ephemeral("callback")

    mgr.trigger_agent_callbacks = trigger

    async def command_then_stall():
        await _mini_game_command(mgr, client)
        await _drain()
        await gate.wait()

    async def release_later():
        await asyncio.sleep(0.05)
        gate.set()

    client.script = [[_text("A说到一半"), command_then_stall, _text("late"), _text("", "stop")]]
    rel = asyncio.ensure_future(release_later())
    await client.stream_text("Q1")
    await rel
    await asyncio.gather(*mgr._bg, return_exceptions=True)
    await _drain()
    contents = [getattr(m, "content", None) for m in client._conversation_history[1:]]
    assert contents.index("A说到一半") < contents.index("回调说完"), contents


@pytest.mark.parametrize("entry", ["stream_text_status", "avatar_reasoning_done"])
async def test_a_command_claiming_the_completion_window_still_wraps_up(entry):
    """Finding 4. A finished reply is in its completion window (stream_text's
    LLM_NO_RESPONSE send, prompt_ephemeral's reasoning-done send) when a
    command claims it: the claim retires the window's busy flag, and the
    claimed task's end pays the debt."""
    client = _client()
    mgr = _manager(client)
    seen = {}

    async def window(*_a):
        await _mini_game_command(mgr, client)
        seen["is_responding_after_claim"] = client._is_responding

    if entry == "stream_text_status":
        client.on_status_message = AsyncMock(side_effect=window)
        client.script = [[_text("", "stop")]]
        await client.stream_text("Q1")
    else:
        client._notify_reasoning_done = window
        client.script = [[_text("摸摸头"), _text("", "stop")]]
        await client.prompt_ephemeral("avatar", completion_mode="response")
    await _drain()
    assert seen == {"is_responding_after_claim": False}
    assert mgr.wrap_ups == ["final_swap", "agent_callbacks"]


async def test_a_claimed_window_leaves_the_session_not_busy():
    client = _client()
    seen = []

    async def window(_owner):
        seen.append(await client.handle_interruption())
        seen.append(client._is_responding)

    client._notify_reasoning_done = window
    client.script = [[_text("摸摸头"), _text("", "stop")]]
    await client.prompt_ephemeral("avatar", completion_mode="response")
    assert seen == ["response", False]
    assert client.is_idle()
    client.on_response_done.assert_not_awaited()


async def test_a_finishing_interrupted_turn_keeps_another_turns_window_flag():
    """Finding 5. A is interrupted inside a tool; B (a proactive reply)
    streams and enters its reasoning-done window. A's tool then returns and
    A's finally runs: B's window flag is B's, and stays set."""
    handler, entered, release = _parked_tool()
    in_window, end_window = asyncio.Event(), asyncio.Event()
    client = _client(handler=handler)
    client.script = [[_text("我查一下"), _tool_calls("c1")], [_text("回调来了"), _text("", "stop")]]
    a = asyncio.ensure_future(client.stream_text("Q1"))
    await asyncio.wait_for(entered.wait(), 1)
    await client.handle_interruption()

    async def window(_owner):
        in_window.set()
        await end_window.wait()

    client._notify_reasoning_done = window
    b = asyncio.ensure_future(client.prompt_ephemeral("callback"))
    await asyncio.wait_for(in_window.wait(), 1)
    before = client._is_responding
    release.set()
    await a
    after = client._is_responding
    end_window.set()
    await b
    client.on_proactive_done.assert_awaited_once()
    assert (before, after) == (True, True)
    assert client._is_responding is False


async def test_typed_text_holds_the_debt_until_its_own_reply_even_if_the_old_task_ends_first(
    monkeypatch,
):
    """The interrupted reply's task ends while the typed input is still being
    set up: the owed wrap-up must not run then (a final swap would start
    right before the new reply). With a reply that never completes (stub),
    it is paid once the input is handled."""
    session = _make_offline_session_for_callback_media()
    mgr = _make_callback_media_manager(session)
    mgr._note_ai_turn = lambda text=None, **_kw: None
    mgr.websocket = _FakeConnectedWebSocket()
    mgr._finalize_turn_after_emit = AsyncMock(
        side_effect=lambda: setattr(mgr, "_turn_wrap_up_owed", False)
    )
    tasks = []
    mgr._fire_task = lambda coro: tasks.append(asyncio.ensure_future(coro))
    session.handle_interruption = AsyncMock(return_value="response")

    async def old_task_ends(_sid):
        M._on_offline_session_idle(mgr)
        await _drain()

    mgr.send_user_activity = old_task_ends
    finalized_before_reply = []

    async def _stream_text(_text, **_kwargs):
        finalized_before_reply.append(mgr._finalize_turn_after_emit.await_count)

    session.stream_text = AsyncMock(side_effect=_stream_text)
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda _n, _t: None)
    await M._process_stream_input(mgr, {"input_type": "text", "data": "换个话题"})
    await _drain()
    assert finalized_before_reply == [0]
    mgr._finalize_turn_after_emit.assert_awaited_once()
    assert mgr._turn_wrap_up_owed is False


async def test_a_voice_reply_in_its_pre_generation_awaits_holds_the_debt():
    """A settle fired while a reply call is still before its begin (input
    transcript callback, image preparation) waits for it: that reply's own
    completion pays, exactly once."""
    client = _client()
    mgr = _manager(client)
    order = []
    real_finalize = mgr._finalize_turn_after_emit

    async def finalize():
        order.append(list(mgr.wrap_ups))
        await real_finalize()

    mgr._finalize_turn_after_emit = finalize
    before_begin = asyncio.Event()
    t1_done = asyncio.Event()

    async def transcript(_text_):
        before_begin.set()
        await t1_done.wait()
        await mgr._settle_owed_turn_wrap_up()  # e.g. a command's settle

    async def interrupt_and_submit():
        await mgr._interrupt_offline_reply(client)
        mgr._bg.append(asyncio.ensure_future(
            client.stream_text("语音", input_transcript_callback=transcript)
        ))
        await before_begin.wait()

    client.script = [
        [_text("说到一半"), interrupt_and_submit, _text("late"), _text("", "stop")],
        [_text("语音回复"), _text("", "stop")],
    ]
    await client.stream_text("Q1")
    t1_done.set()
    await asyncio.gather(*mgr._bg)
    await _drain()
    assert order == [[]]  # one finalize, from the voice reply's completion
    assert mgr.wrap_ups == ["final_swap", "agent_callbacks"]


@pytest.mark.parametrize("where", ["response_done", "status"])
async def test_a_reply_call_that_raises_in_its_finally_still_leaves_the_session_idle(where):
    client = _client()
    idle = MagicMock()
    client.on_idle = idle
    if where == "response_done":
        client.on_response_done = AsyncMock(side_effect=RuntimeError("boom"))
        client.script = [[_text("好"), _text("", "stop")]]
    else:
        client.on_status_message = AsyncMock(side_effect=asyncio.CancelledError())
        client.script = [[_text("", "stop")]]
    with pytest.raises((RuntimeError, asyncio.CancelledError)):
        await client.stream_text("hi")
    assert client.is_idle()
    idle.assert_called_once_with()


async def test_the_idle_settle_does_not_pay_while_another_reply_is_live():
    """A's task ends while B streams: B's own completion pays, once."""
    client = _client()
    mgr = _manager(client)
    order = []
    real_finalize = mgr._finalize_turn_after_emit

    async def finalize():
        order.append(("finalize", client._active_response_generation))
        await real_finalize()

    mgr._finalize_turn_after_emit = finalize

    async def interrupt_and_start_b():
        await mgr._interrupt_offline_reply(client)
        mgr._bg.append(asyncio.ensure_future(client.stream_text("Q2")))
        await asyncio.sleep(0)

    client.script = [
        [_text("A说到一半"), interrupt_and_start_b, _text("late"), _text("", "stop")],
        [_text("B"), _text("", "stop")],
    ]
    await client.stream_text("Q1")
    await asyncio.gather(*mgr._bg)
    await _drain()
    assert order == [("finalize", None)]
    assert mgr._turn_wrap_up_owed is False


# ── Finding 6: a claimed, fully streamed reply is sealed on the frontend ─────


def _wire_ws():
    client = _client()
    mgr = _make_manager()
    mgr.session = client
    mgr.websocket = _FakeConnectedWebSocket()
    mgr._note_ai_turn = lambda text=None, **_kw: None
    mgr._finalize_turn_after_emit = AsyncMock()
    mgr._turn_wrap_up_owed = False

    async def on_text_delta(text, _is_first):
        mgr._current_ai_turn_text += text
        mgr.websocket.sent.append({"type": "gemini_response", "text": text})

    client.on_text_delta = AsyncMock(side_effect=on_text_delta)
    client.on_proactive_done = mgr.handle_proactive_complete
    client.on_response_done = mgr.handle_response_complete
    return client, mgr


async def test_a_claimed_agent_callback_reply_gets_its_frontend_turn_end():
    client, mgr = _wire_ws()

    async def cleanup(_owner):
        await M._interrupt_offline_reply(mgr, client)

    client._notify_reasoning_done = cleanup
    client.script = [[_text("我查完了，结果在这里。"), _text("", "stop")]]
    assert await client.prompt_ephemeral("callback") is True
    assert _turn_ends(mgr) == [{"type": "system", "data": "turn end agent_callback"}]
    assert _system(mgr) == [{"type": "system", "data": "turn end agent_callback"}]


async def test_a_claimed_response_reply_gets_its_frontend_turn_end():
    """An unbound reply answers no request: its turn end names none, and the
    shared id (a typed turn's) is left to that turn."""
    client, mgr = _wire_ws()
    mgr._active_text_request_id = "req-old"

    async def cleanup(_owner):
        await M._interrupt_offline_reply(mgr, client)

    client._notify_reasoning_done = cleanup
    client.script = [[_text("早上好呀。"), _text("", "stop")]]
    assert await client.prompt_ephemeral("greet", completion_mode="response") is True
    assert _system(mgr) == [{"type": "system", "data": "turn end", "request_id": None}]
    assert mgr._active_text_request_id == "req-old"
    assert mgr._turn_wrap_up_owed is True


@pytest.mark.parametrize("bound", [True, False])
async def test_a_reply_cut_mid_stream_still_gets_only_turn_abandoned(bound):
    """Never sealed: the interrupter's input split the bubbles. A bound reply
    releases its own request; an unbound one answers none, and the shared id
    (a typed turn's) is left alone."""
    client, mgr = _wire_ws()
    mgr._active_text_request_id = "req-B"

    async def interrupt():
        await M._interrupt_offline_reply(mgr, client)

    client.script = [[_text("说到一半"), interrupt, _text("后半句"), _text("", "stop")]]
    owner = _ReplyTurn(speech_id=None, request_id="req-old") if bound else None
    await client.stream_text("Q", reply_owner=owner)
    assert _system(mgr) == (
        [{"type": "system", "data": "turn abandoned", "request_id": "req-old"}] if bound else []
    )
    assert mgr._active_text_request_id == "req-B"


async def test_a_reply_cut_by_close_and_then_claimed_is_not_sealed():
    client, mgr = _wire_ws()
    mgr._active_text_request_id = "req-old"
    seen = []

    async def session_close_cut():
        client._cancel_response_generation()  # what close() does

    async def cleanup(_owner):
        seen.append(await client.handle_interruption())

    client._notify_reasoning_done = cleanup
    client.script = [[_text("说到一半"), session_close_cut, _text("x"), _text("", "stop")]]
    await client.prompt_ephemeral("greet", completion_mode="response")
    assert seen == ["response"] and seen[0].finished is False


@pytest.mark.parametrize("kind, expected", [
    ("response", {"type": "system", "data": "turn end", "request_id": "req-old"}),
    ("agent_callback", {"type": "system", "data": "turn end agent_callback"}),
])
async def test_the_claimed_turn_end_precedes_user_activity_and_the_new_reply(
    monkeypatch, kind, expected,
):
    from main_logic.omni_offline_client._lifecycle import InterruptedReply

    session = _make_offline_session_for_callback_media()
    mgr = _make_callback_media_manager(session)
    mgr._note_ai_turn = lambda text=None, **_kw: None
    mgr._current_ai_turn_text = "说完了。"
    mgr._active_text_request_id = "req-old"
    mgr.websocket = _FakeConnectedWebSocket()
    mgr._finalize_turn_after_emit = AsyncMock()

    async def send_user_activity(_sid):
        mgr.websocket.sent.append({"type": "user_activity"})

    async def stream_text(_text, **_kwargs):
        mgr.websocket.sent.append({"type": "gemini_response", "isNewMessage": True})

    mgr.send_user_activity = send_user_activity
    owner = _ReplyTurn(speech_id=None, request_id="req-old") if kind == "response" else None
    session.handle_interruption = AsyncMock(
        return_value=InterruptedReply(kind, finished=True, owner=owner))
    session.stream_text = AsyncMock(side_effect=stream_text)
    monkeypatch.setattr(core_module, "dispatch_text_user_message", lambda _n, _t: None)
    await M._process_stream_data_internal(
        mgr, {"input_type": "text", "data": "换个话题", "request_id": "req-new"},
    )
    relevant = [m for m in mgr.websocket.sent
                if m["type"] in ("system", "user_activity", "gemini_response")]
    assert relevant == [expected, {"type": "user_activity"},
                        {"type": "gemini_response", "isNewMessage": True}]


@pytest.mark.parametrize("text, finished", [("旧的说完了。新的说到一半", False), ("", True)])
async def test_only_a_finished_claim_with_text_is_sealed(text, finished):
    from main_logic.omni_offline_client._lifecycle import InterruptedReply

    session = _make_offline_session_for_callback_media()
    mgr = _make_callback_media_manager(session)
    mgr._note_ai_turn = lambda text=None, **_kw: None
    mgr._current_ai_turn_text = text
    mgr._active_text_request_id = "req-old"
    mgr.websocket = _FakeConnectedWebSocket()
    session.handle_interruption = AsyncMock(return_value=InterruptedReply(
        "response", finished=finished,
        owner=_ReplyTurn(speech_id=None, request_id="req-old"),
    ))
    assert await M._interrupt_offline_reply(mgr, session) is True
    assert mgr.websocket.sent == [
        {"type": "system", "data": "turn abandoned", "request_id": "req-old"},
    ]


async def test_an_interruption_that_cancels_a_newer_live_reply_reports_it_unfinished():
    """A finished generation in its cleanup window is displaced by a newer
    one (claimed by the begin), and the interruption then cancels the newer
    live one: that reply was cut, never sealed as finished."""
    client = _client()
    client.on_response_displaced = MagicMock()
    seen = []

    async def cleanup(_owner):
        client._begin_response_generation("response")
        seen.append(await client.handle_interruption())

    client._notify_reasoning_done = cleanup
    client.script = [[_text("说完了。"), _text("", "stop")]]
    await client.prompt_ephemeral("callback", completion_mode="response")
    assert seen == ["response"] and seen[0].finished is False
    client.on_response_displaced.assert_called_once()
    assert client.on_response_displaced.call_args.args[0].finished is True


@pytest.mark.parametrize("slow_send", [False, True])
async def test_a_finished_avatar_reply_displaced_by_typed_text_is_sealed_before_it(
    monkeypatch, slow_send,
):
    """Typed input still in its pre-generation awaits (no reply live, so the
    avatar busy gate lets a poke in); the avatar reply streams to the end and
    waits on its completion; the typed stream_text then begins and claims it.
    The claimed avatar turn is sealed on the frontend like one that ended just
    before the typed reply: its turn end (its meta, no request id: that id is
    the typed turn's) reaches the WebSocket before the typed reply's first
    chunk, even when that send stalls (``slow_send``): the typed reply awaits
    it before sending its provider request."""
    session, mgr, notes = _wire_text_path(monkeypatch)

    async def on_text_delta(text, is_first, **_kw):
        mgr._current_ai_turn_text += text
        mgr.websocket.sent.append({"type": "gemini_response", "text": text})

    session.on_text_delta = on_text_delta
    meta = {"kind": "avatar_interaction", "interaction_id": "i-9"}
    parked, release = asyncio.Event(), asyncio.Event()
    cleanups = []
    avatar = []
    displaced = []

    async def reasoning_done(_owner=None):
        cleanups.append(1)
        if len(cleanups) == 1:  # the avatar's completion window
            parked.set()
            await release.wait()

    async def typed_reply_streams():
        release.set()
        # A real provider's first chunk is at least one loop turn away (the
        # request goes out over the network); one turn is all the seal gets.
        await asyncio.sleep(0)

    session._notify_reasoning_done = reasoning_done
    real_displaced = session.on_response_displaced

    def on_displaced(kind):
        displaced.append(kind)
        return real_displaced(kind)

    session.on_response_displaced = on_displaced
    if slow_send:
        real_send = mgr.websocket.send_json

        async def stalling_send(message):
            if message.get("meta") is meta:
                await asyncio.sleep(0.05)  # a slow socket
            await real_send(message)

        mgr.websocket.send_json = stalling_send
    session.script = [
        [_text("摸摸头好舒服。"), _text("", "stop")],
        [typed_reply_streams, _text("用户回复。"), _text("", "stop")],
    ]

    async def focus_while_avatar_finishes(_text_):
        mgr._pending_turn_meta = meta

        async def avatar_turn():  # greeting.py, around prompt_ephemeral
            delivered = await session.prompt_ephemeral(
                "avatar", completion_mode="response", persist_response=False,
            )
            if mgr._pending_turn_meta is meta:
                mgr._pending_turn_meta = None
            return delivered

        avatar.append(asyncio.create_task(avatar_turn()))
        await parked.wait()
        return False

    mgr._focus_inline_decision = focus_while_avatar_finishes
    await M._process_stream_data_internal(
        mgr, {"input_type": "text", "data": "在吗", "request_id": "req-text"},
    )
    assert await avatar[0] is True
    await _drain()

    assert displaced == ["response"] and displaced[0].finished is True
    assert notes == ["摸摸头好舒服。", "用户回复。"]
    assert _turn_ends(mgr) == [
        {"type": "system", "data": "turn end", "meta": meta},
        {"type": "system", "data": "turn end", "request_id": "req-text"},
    ]
    relevant = [m for m in mgr.websocket.sent if m["type"] in ("system", "gemini_response")]
    assert relevant == [
        {"type": "gemini_response", "text": "摸摸头好舒服。"},
        {"type": "system", "data": "turn end", "request_id": None, "meta": meta},
        {"type": "gemini_response", "text": "用户回复。"},
        {"type": "system", "data": "turn end", "request_id": "req-text"},
    ]


@pytest.mark.parametrize("kind, finished, text, expected", [
    ("response", True, "说完了。", [{"type": "system", "data": "turn end", "request_id": None}]),
    ("agent_callback", True, "回调说完。", [{"type": "system", "data": "turn end agent_callback"}]),
    ("response", False, "说到一半", [{"type": "system", "data": "turn end", "request_id": None}]),
    ("agent_callback", False, "回调说到一半", [{"type": "system", "data": "turn end agent_callback"}]),
    ("response", True, "", []),
    ("response", False, "", []),
])
async def test_only_a_displaced_reply_with_text_is_sealed(kind, finished, text, expected):
    """A displaced reply, finished or cut mid-stream, still owns the
    frontend's current bubble (the displacing reply has emitted nothing, and
    its input came first), so it is sealed; one that said nothing has no
    bubble to seal. The new turn's request id is left alone either way."""
    from main_logic.omni_offline_client._lifecycle import InterruptedReply

    mgr = _make_manager()
    mgr.websocket = _FakeConnectedWebSocket()
    mgr._note_ai_turn = lambda text=None, **_kw: None
    mgr._current_ai_turn_text = text
    mgr._active_text_request_id = "req-new"
    followup = M._close_displaced_offline_turn(mgr, InterruptedReply(kind, finished=finished))
    if followup is not None:
        await followup()
    assert _system(mgr) == expected
    assert mgr._active_text_request_id == "req-new"


# ── Finding 11: one turn-end builder ─────────────────────────────────────────


async def test_a_completed_reply_turn_end_carries_its_meta_on_both_channels():
    mgr = _make_manager()
    mgr.websocket = _FakeConnectedWebSocket()
    mgr._note_ai_turn = lambda text=None, **_kw: None
    mgr._finalize_turn_after_emit = AsyncMock()
    mgr._current_ai_turn_text = "摸头。"
    meta = {"kind": "avatar_interaction", "interaction_id": "i-3"}
    mgr._pending_turn_meta = meta
    mgr._active_text_request_id = "req-1"
    await M.handle_response_complete(mgr)
    expected = {"type": "system", "data": "turn end", "request_id": "req-1", "meta": meta}
    assert _turn_ends(mgr) == [expected]
    assert _system(mgr) == [expected]
    assert mgr._pending_turn_meta is None


# ── A discard empties the AI-turn buffer, not cross_server's turn ───────────


async def test_a_reply_interrupted_in_its_retry_backoff_still_ends_its_turn(monkeypatch):
    """A said something, lost its connection and waits in the retry backoff.
    Its discard emptied the AI-turn buffer, but the text had reached
    cross_server, which stays in A's assistant turn until a turn end. B
    interrupts there: A's close still ends that turn, or B's reply is filed
    in A's turn and B's own input after it."""
    backoff = _ParkedBackoff()
    monkeypatch.setattr(offline_streaming, "asyncio", backoff)
    session, mgr, notes = _wire_text_path(monkeypatch)

    async def drop():
        raise _connection_error()

    session.script = [
        [_text("A说到一半"), drop],
        [_text("B的回答。"), _text("", "stop")],
    ]
    turn_a = asyncio.create_task(M._process_stream_data_internal(
        mgr, {"input_type": "text", "data": "A", "request_id": "req-A"},
    ))
    await asyncio.wait_for(backoff.entered.wait(), 5)
    await M._process_stream_data_internal(
        mgr, {"input_type": "text", "data": "B", "request_id": "req-B"},
    )
    backoff.release.set()
    await asyncio.wait_for(turn_a, 5)
    await _drain()

    assert [m.get("request_id") for m in _turn_ends(mgr)] == ["req-A", "req-B"]
    assert _system(mgr) == [
        {"type": "system", "data": "turn abandoned", "request_id": "req-A"},
        {"type": "system", "data": "turn end", "request_id": "req-B"},
    ]
    assert notes == [None, "B的回答。"]  # the discarded text is not A's turn
    assert mgr._discarded_turn_open is False


@pytest.mark.parametrize("cut_at", ["send", "tts_feed"])
async def test_a_truncation_recovery_cut_midway_still_ends_its_turn(cut_at):
    """The recovered body reaches cross_server untracked (the discard emptied
    the buffer). An input that interrupts the recovery's later steps takes
    its close over, and that close still ends the turn there."""
    mgr = _make_manager()
    mgr.is_active = True
    mgr.websocket = _FakeConnectedWebSocket()
    mgr._finalize_turn_after_emit = AsyncMock()
    mgr._note_ai_turn = lambda text=None, **_kw: None
    mgr._open_reply_turn = None
    mgr.use_tts = True
    mgr._clear_tts_pipeline = AsyncMock()
    mgr._request_tts_done_for_turn = AsyncMock(return_value="queued")
    mgr._push_focus_thinking = AsyncMock()
    session = MagicMock(_conversation_history=[])
    mgr.session = session
    reply_turn = mgr._begin_reply_turn(speech_id=mgr.current_speech_id, request_id="req-A")
    reply_turn.session = session
    # The cut reply hands back the snapshot it was started with.
    session.handle_interruption = AsyncMock(
        return_value=InterruptedReply("response", owner=reply_turn))
    mgr._active_text_request_id = "req-A"
    real_send = M.send_lanlan_response.__get__(mgr)

    async def send(*args, **kwargs):
        published = await real_send(*args, **kwargs)
        if cut_at == "send":
            await mgr._interrupt_offline_reply(session)
        return published

    async def feed(*_args, **_kwargs):
        if cut_at == "tts_feed":
            await mgr._interrupt_offline_reply(session)

    mgr.send_lanlan_response = send
    mgr.feed_tts_chunk = feed
    await mgr.handle_response_discarded(
        "length_truncated", 1, 1, False,
        '{"code": "RESPONSE_LENGTH_TRUNCATED", "text": "截断到这里。"}',
        request_id="req-A",
        reply_turn=reply_turn,
    )

    sync = mgr.sync_message_queue.messages
    body = [i for i, m in enumerate(sync) if m.get("type") == "json"]
    assert body
    assert [m.get("request_id") for m in sync[body[-1]:] if m.get("data") == "turn end"] == ["req-A"]


@pytest.mark.parametrize("cut_at", ["turn_end_ws_send", "during_finalize"])
async def test_an_interruption_after_a_final_discard_ended_the_turn_closes_nothing(cut_at):
    """A final discard (no rerolls left) ends the reply's turn while its
    generation is still live: the discard callback runs inside the stream
    loop. An input interrupting after that turn end (while its frontend copy
    is sent, or while the discard's own wrap-up runs) takes over a reply whose
    turn is already over. Closing it again sent ``turn abandoned`` for the
    ended request and owed a second wrap-up (a second finalize). Only the
    request id is released, right away."""
    client = _client()
    client.enable_response_guard = True
    client.max_response_length = 8
    client.max_response_rerolls = 0
    mgr = _manager(client)
    mgr.use_tts = False
    mgr.user_language = "zh-CN"
    ws = _FakeConnectedWebSocket()
    mgr.websocket = ws

    owner = _ReplyTurn(speech_id=mgr.current_speech_id, request_id="req-A")
    owner.session = client
    mgr._open_reply_turn = owner
    mgr._active_text_request_id = "req-A"

    seen = {}
    finalize_calls = []
    real_finalize = mgr._finalize_turn_after_emit

    async def interrupt_once(where):
        if seen.get("cut"):
            return
        seen["cut"] = where
        seen["turn_ended_at_cut"] = owner.turn_ended
        seen["gen_live_at_cut"] = client._active_response_generation is not None
        seen["interrupted"] = await M._interrupt_offline_reply(mgr, client)
        seen["request_id_after_cut"] = mgr._active_text_request_id
        seen["owed_after_cut"] = mgr._turn_wrap_up_owed

    real_send = ws.send_json

    async def send_json(payload):
        await real_send(payload)
        if (
            cut_at == "turn_end_ws_send"
            and payload.get("data") == "turn end"
            and payload.get("request_id") == "req-A"
        ):
            await interrupt_once(cut_at)

    ws.send_json = send_json

    async def finalize():
        finalize_calls.append(owner.turn_ended)
        await real_finalize()
        if cut_at == "during_finalize":
            await interrupt_once(cut_at)

    mgr._finalize_turn_after_emit = finalize

    async def discarded(reason, attempt, max_attempts, will_retry, message=None):
        await mgr.handle_response_discarded(
            reason, attempt, max_attempts, will_retry, message,
            request_id="req-A", reply_turn=owner,
        )

    async def done():
        await mgr.handle_response_complete(reply_turn=owner)

    client.script = [
        [_text("我在说，一直说，不停地说，还在说，继续说，说个没完，还要说，"), _text("", "stop")],
    ]
    await client.stream_text(
        "hi",
        response_discarded_callback=discarded,
        response_done_callback=done,
        reply_owner=owner,
    )
    await _settle_bg(mgr)
    await mgr._settle_owed_turn_wrap_up()
    await _settle_bg(mgr)

    system = [m for m in ws.sent if m.get("type") == "system"]
    turn_ends_sync = [
        m for m in mgr.sync_message_queue.messages
        if isinstance(m, dict) and m.get("data") == "turn end"
    ]
    assert seen["turn_ended_at_cut"] is True and seen["gen_live_at_cut"] is True
    assert seen["interrupted"] is True
    assert seen["request_id_after_cut"] is None
    assert seen["owed_after_cut"] is False
    assert [m.get("request_id") for m in turn_ends_sync] == ["req-A"]
    assert system == [{"type": "system", "data": "turn end", "request_id": "req-A"}]
    assert finalize_calls == [True]


@pytest.mark.parametrize("displaced", [False, True])
@pytest.mark.parametrize("active_request", ["req-A", "req-B"])
async def test_a_reply_whose_turn_ended_is_taken_over_without_a_close(
    displaced, active_request,
):
    """Either taker of a bound reply whose final discard already sent its
    turn end (an interrupter, or a user reply that began over it) closes
    nothing: no second turn end, the AI-turn buffer and the staged turn meta
    (by now another turn's) stay, nothing is owed and nothing goes to the
    frontend. The reply's request id is released only while the shared field
    still holds it; a newer request's id is left alone."""
    mgr = _manager(_client())
    owner = _ReplyTurn(speech_id=mgr.current_speech_id, request_id="req-A")
    owner.turn_ended = True
    mgr._active_text_request_id = active_request
    mgr._current_ai_turn_text = "下一轮的话"
    meta = {"kind": "avatar_interaction"}
    mgr._pending_turn_meta = meta

    send = mgr._close_taken_over_offline_reply(
        InterruptedReply("response", finished=True, owner=owner), displaced=displaced,
    )

    assert send is None
    assert _turn_ends(mgr) == []
    assert mgr._current_ai_turn_text == "下一轮的话"
    assert mgr._pending_turn_meta is meta
    assert mgr._turn_wrap_up_owed is False
    assert mgr._active_text_request_id == (None if active_request == "req-A" else "req-B")


# ── An independent-ASR voice reply is taken over before its task is cancelled ──


def _dialog(client):
    return [
        (message["role"], message.get("content"))
        if isinstance(message, dict) else (message.type, message.content)
        for message in client._conversation_history[1:]
    ]


def _voice_reply(client, *steps):
    """Start an independent-ASR voice reply (its child task streams it) and
    return it with an event set once it reaches the first parked step."""
    streaming, release = asyncio.Event(), asyncio.Event()

    async def parked():
        streaming.set()
        await release.wait()

    client.script = [[parked if step == "park" else step for step in steps]]
    voice = asyncio.ensure_future(client.submit_external_voice_turn("语音", turn_id="t1"))
    return voice, streaming


async def test_a_typed_input_cutting_a_voice_reply_takes_its_close_over(monkeypatch):
    """The voice reply streams in the child task the typed input's
    interruption cancels. It is taken over first, so its completion does not
    run inside that interruption: the interrupter closes it, the half it
    showed lands before the typed message, and the typed reply's completion
    pays the wrap-up once, after its own provider request."""
    session, mgr, notes = _wire_text_path(monkeypatch)
    finalized = []

    async def finalize():
        finalized.append(len(session.requests))
        mgr._turn_wrap_up_owed = False

    mgr._finalize_turn_after_emit = finalize
    voice, streaming = _voice_reply(
        session, _text("语音说到一半，"), "park", _text("后半句。"), _text("", "stop"),
    )
    session.script.append([_text("B的回复。"), _text("", "stop")])
    await asyncio.wait_for(streaming.wait(), 5)
    await M._process_stream_data_internal(
        mgr, {"input_type": "text", "data": "B", "request_id": "req-B"},
    )
    assert await voice is False
    await _drain()

    assert finalized == [2]
    assert notes == ["语音说到一半，", "B的回复。"]
    assert _turn_ends(mgr) == [
        {"type": "system", "data": "turn end"},
        {"type": "system", "data": "turn end", "request_id": "req-B"},
    ]
    assert _system(mgr) == [{"type": "system", "data": "turn end", "request_id": "req-B"}]
    dialog = _dialog(session)
    assert dialog[:2] == [("human", "语音"), ("ai", "语音说到一半，")]
    assert dialog[-1] == ("ai", "B的回复。")
    assert [kind for kind, _ in dialog] == ["human", "ai", "human", "ai"]


async def test_a_voice_reply_cut_before_its_first_chunk_reports_no_failure():
    """Cut while waiting for its first chunk: an interruption, not an empty
    reply. No LLM_NO_RESPONSE, no completion, handed to the interrupter."""
    client = _client()
    statuses = []
    client.on_status_message = AsyncMock(side_effect=statuses.append)
    voice, streaming = _voice_reply(client, "park", _text("晚到的"), _text("", "stop"))
    await asyncio.wait_for(streaming.wait(), 5)
    kind = await client.handle_interruption()
    assert await voice is False
    assert kind == "response" and kind.finished is False
    assert statuses == []
    client.on_response_done.assert_not_awaited()
    assert _dialog(client) == [("human", "语音")]
    assert client.is_idle()


async def test_a_voice_reply_whose_turn_is_cancelled_keeps_what_it_showed():
    """Its voice turn is torn down mid-reply (the submit is cancelled, and
    its child task with it): nothing took the reply over, so its own
    completion still closes it, and the text it showed stays in history."""
    client = _client()
    voice, streaming = _voice_reply(client, _text("说到一半，"), "park", _text("x"), _text("", "stop"))
    await asyncio.wait_for(streaming.wait(), 5)
    voice.cancel()
    with pytest.raises(asyncio.CancelledError):
        await voice
    assert _dialog(client) == [("human", "语音"), ("ai", "说到一半，")]
    client.on_response_done.assert_awaited_once()


@pytest.mark.parametrize("cut_in, expected", [
    # No call finished: the round is dropped, the shown text is the reply.
    ("first_call", [("human", "语音"), ("ai", "我查一下，")]),
    # One finished: the kept round already holds the shown text (its
    # sentinel never comes), so it is not committed a second time.
    ("second_call", [("human", "语音"), ("assistant", "我查一下，"), ("tool", '{"ok": true}')]),
    # Same, but the text was still held back (name-prefix buffer): the kept
    # round holds only what was shown, as its sentinel would have trimmed it.
    ("second_call_unshown", [("human", "语音"), ("assistant", ""), ("tool", '{"ok": true}')]),
    # Cut in the text after a finished round: that round is not this text's.
    ("after_the_round", [
        ("human", "语音"), ("assistant", "我查一下，"), ("tool", '{"ok": true}'),
        ("ai", "查到了，"),
    ]),
])
async def test_a_voice_reply_cut_around_its_tool_round_keeps_its_shown_text_once(
    cut_in, expected,
):
    client = _client()
    entered, release = asyncio.Event(), asyncio.Event()

    async def parked():
        entered.set()
        await release.wait()

    async def handler(call):
        cut_at = {"first_call": "c1", "second_call": "c2", "second_call_unshown": "c2"}
        if call.call_id == cut_at.get(cut_in):
            await parked()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client.on_tool_call = handler
    if cut_in == "second_call_unshown":
        client._prefix_buffer_size = 50
    calls = ("c1",) if cut_in == "after_the_round" else ("c1", "c2")
    voice, _streaming = _voice_reply(client, _text("我查一下，"), _tool_calls(*calls))
    client.script.append([_text("查到了，"), parked, _text("late"), _text("", "stop")])
    await asyncio.wait_for(entered.wait(), 5)
    kind = await client.handle_interruption()
    assert await voice is False
    assert kind == "response"
    client.on_response_done.assert_not_awaited()
    assert _dialog(client) == expected


@pytest.mark.parametrize("discard", ["guard_reroll", "stream_retried", "stream_failed"])
async def test_a_voice_reply_cut_in_a_discard_keeps_no_discarded_text(discard):
    """The cut lands while the shown text is being discarded (a guard
    reroll, a stream retried or given up after a failure): none of it is
    kept."""
    client = _client()
    entered, release = asyncio.Event(), asyncio.Event()

    async def discarded(*_args):
        entered.set()
        await release.wait()

    async def fail():
        raise _connection_error() if discard == "stream_retried" else ValueError("boom")

    client.on_response_discarded = discarded
    if discard == "guard_reroll":
        client.enable_response_guard = True
        client.max_response_length = 20
        client.max_response_rerolls = 1
        client.script = [[_text("哈哈哈哈哈哈"), _text("哈" * 300), _text("", "stop")]]
    else:
        client.script = [[_text("说到一半"), fail]]
    voice = asyncio.ensure_future(client.submit_external_voice_turn("语音", turn_id="t1"))
    await asyncio.wait_for(entered.wait(), 5)
    assert await client.handle_interruption() == "response"
    assert await voice is False
    assert _dialog(client) == [("human", "语音")]
    client.on_response_done.assert_not_awaited()


async def test_a_voice_reply_cut_in_its_summary_call_keeps_what_was_shown(monkeypatch):
    """Cut while the long-reply summary is being written: the reply keeps
    the text the UI already showed, as the check after that call would."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from tests.unit.test_tool_calling import _build_summary_client
    from utils.llm_client import LLMStreamChunk

    entered = asyncio.Event()

    async def parked_summary(self, prefix, tail):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(OmniOfflineClient, "_summarize_tail_for_tts", parked_summary)
    long_text = (
        "one two three four. five, six seven eight nine ten. "
        + " ".join(f"w{i}" for i in range(25)) + "."
    )

    async def _astream(self, messages, **overrides):
        yield LLMStreamChunk(content=long_text)

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)
    shown = []

    async def on_text_delta(text, _is_first, **kwargs):
        if kwargs.get("ui_enabled", True):
            shown.append(text)

    client = _build_summary_client(monkeypatch, max_response_length=4)
    client.on_text_delta = on_text_delta
    client.on_input_transcript = AsyncMock()
    client.on_response_done = AsyncMock()
    client.on_response_discarded = None
    client.on_status_message = AsyncMock()
    client.on_repetition_detected = None
    voice = asyncio.ensure_future(client.submit_external_voice_turn("trigger long", turn_id="t1"))
    await asyncio.wait_for(entered.wait(), 5)
    assert await client.handle_interruption() == "response"
    assert await voice is False
    assert client._conversation_history[-1].content == "".join(shown)
    client.on_response_done.assert_not_awaited()


async def test_a_voice_reply_cut_in_a_typed_setup_window_leaves_that_request_alone(
    monkeypatch,
):
    """Typed input B has stored its request id and is still setting up its
    reply when a voice reply that began meanwhile is cut (a speech onset or a
    mini-game command interrupts). Taken over by the interrupter, the voice
    reply is closed by the shared rule: it is unbound and answers no request,
    so B's id is neither put on its turn end nor released on the frontend;
    B's own turn end carries it."""
    session, mgr, notes = _wire_text_path(monkeypatch)
    facts = {}

    async def focus_hook(_text_):
        # B's id is stored; its stream_text has not begun.
        voice, streaming = _voice_reply(
            session, _text("语音说到一半，"), "park", _text("后半句。"), _text("", "stop"),
        )
        session.script.append([_text("B的回复。"), _text("", "stop")])
        await asyncio.wait_for(streaming.wait(), 5)
        facts["interrupted"] = await M._interrupt_offline_reply(mgr, session)
        facts["voice_delivered"] = await voice
        facts["id_after_cut"] = mgr._active_text_request_id
        return False

    mgr._focus_inline_decision = focus_hook
    await M._process_stream_data_internal(
        mgr, {"input_type": "text", "data": "B", "request_id": "req-B"},
    )
    await _drain()

    assert facts == {"interrupted": True, "voice_delivered": False, "id_after_cut": "req-B"}
    assert notes == ["语音说到一半，", "B的回复。"]
    assert _turn_ends(mgr) == [
        {"type": "system", "data": "turn end"},
        {"type": "system", "data": "turn end", "request_id": "req-B"},
    ]
    assert _system(mgr) == [{"type": "system", "data": "turn end", "request_id": "req-B"}]
    assert mgr._active_text_request_id is None


@pytest.mark.parametrize("cut_in", ["stream", "second_call", "after_the_round"])
async def test_a_cut_stream_keeps_its_text_after_a_reply_it_displaced(cut_in):
    """What a reply whose task is cancelled mid-stream keeps goes where its
    turn ends (``_cancelled_turn_end``), judged by its own generation. A
    callback reply that began in this turn's setup (after its user message
    was saved) and was displaced by its begin was shown first, so it is no
    boundary: the kept half stays after it, and a tool round kept after it is
    trimmed rather than committed a second time."""
    p_stalled, p_release = asyncio.Event(), asyncio.Event()
    cut_point, never = asyncio.Event(), asyncio.Event()

    async def parked(*_args):
        cut_point.set()
        await never.wait()

    async def handler(call):
        if cut_in == "second_call" and call.call_id == "c2":
            await parked()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = _client(handler=handler)
    proactive = []

    async def transcript(_text_):
        proactive.append(asyncio.create_task(client.prompt_ephemeral("callback")))
        await asyncio.wait_for(p_stalled.wait(), 5)

    async def p_stall():
        p_stalled.set()
        await p_release.wait()

    async def finish_proactive():
        p_release.set()
        await proactive[0]

    client.on_input_transcript = transcript
    client.script = [[_text("刚想说"), p_stall, _text("x"), _text("", "stop")]]
    if cut_in == "stream":
        client.script.append(
            [finish_proactive, _text("A说到一半，"), parked, _text("late"), _text("", "stop")])
        kept = [("ai", "A说到一半，")]
    elif cut_in == "second_call":
        client.script.append([finish_proactive, _text("我查一下，"), _tool_calls("c1", "c2")])
        kept = [("assistant", "我查一下，"), ("tool", '{"ok": true}')]
    else:
        client.script.append([finish_proactive, _text("我查一下，"), _tool_calls("c1")])
        client.script.append([_text("查到了，"), parked, _text("late"), _text("", "stop")])
        kept = [("assistant", "我查一下，"), ("tool", '{"ok": true}'), ("ai", "查到了，")]
    turn = asyncio.ensure_future(client.stream_text("A"))
    await asyncio.wait_for(cut_point.wait(), 5)
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn
    assert _dialog(client) == [("human", "A"), ("ai", "刚想说"), *kept]


# ── Invariants: exactly one close per generation; busy flag left clean ───────


async def _scenario(client, name):
    """Drive ``client`` through one ownership path. Interruptions go through
    ``client.handle_interruption`` (counted by the caller)."""
    if name == "completes":
        client.script = [[_text("好的。"), _text("", "stop")]]
        await client.stream_text("Q")
    elif name == "interrupted_mid_reply":
        async def cut():
            await client.handle_interruption()
        client.script = [[_text("说到"), cut, _text("一半"), _text("", "stop")]]
        await client.stream_text("Q")
    elif name == "interrupted_while_guard_paused":
        async def paused_cut():
            client._pause_response_generation(client._active_response_generation)
            await client.handle_interruption()
            client._resume_response_generation(client._response_generation)
        client.script = [[_text("说到"), paused_cut, _text("一半"), _text("", "stop")]]
        await client.stream_text("Q")
    elif name == "interrupted_in_a_tool":
        async def handler(call):
            await client.handle_interruption()
            return ToolResult(call_id=call.call_id, name=call.name, output={})
        client.on_tool_call = handler
        client.script = [[_text("查一下"), _tool_calls("c1")], [_text("x"), _text("", "stop")]]
        await client.stream_text("Q")
    elif name == "claimed_in_its_cleanup":
        async def cleanup(_seq):
            await client.handle_interruption()
        client._notify_reasoning_done = cleanup
        client.script = [[_text("回调。"), _text("", "stop")]]
        await client.prompt_ephemeral("callback")
    elif name == "displaced_live":
        began = asyncio.Event()

        async def park():
            await began.wait()

        async def displacer_begins():
            began.set()
            await _drain()
        client.script = [[_text("摸头，"), park, _text("还要"), _text("", "stop")],
                         [displacer_begins, _text("好"), _text("", "stop")]]
        avatar = asyncio.ensure_future(client.prompt_ephemeral("avatar", completion_mode="response"))
        await _drain()
        await client.stream_text("hi")
        await avatar
    elif name == "displaced_in_its_cleanup":
        voice = []

        async def cleanup(_seq):
            voice.append(asyncio.ensure_future(client.stream_text("语音")))
            await _drain()
        client._notify_reasoning_done = cleanup
        client.script = [[_text("回调。"), _text("", "stop")], [_text("语音回复"), _text("", "stop")]]
        await client.prompt_ephemeral("callback")
        await voice[0]
    elif name == "cut_by_close":
        async def close_cut():
            client._cancel_response_generation()
        client.script = [[_text("说到"), close_cut, _text("一半"), _text("", "stop")]]
        await client.stream_text("Q")
    elif name == "external_voice_cut":
        streaming, park = asyncio.Event(), asyncio.Event()

        async def parked():
            streaming.set()
            await park.wait()
        client.script = [[_text("语音说到"), parked, _text("一半"), _text("", "stop")]]
        voice = asyncio.ensure_future(client.submit_external_voice_turn("语音", turn_id="t1"))
        await streaming.wait()
        await client.handle_interruption()
        assert await voice is False
    elif name == "pending_beside_a_live_reply":
        # A reply cut by close() (which takes nothing over) reaches its
        # completion window after a newer reply began on the client; an
        # interruption then cancels the newer one.
        newer, newer_live, release = [], asyncio.Event(), asyncio.Event()

        async def close_cut():
            client._cancel_response_generation()
            newer.append(asyncio.ensure_future(client.stream_text("新")))
            await newer_live.wait()

        async def newer_begins():
            newer_live.set()
            await release.wait()

        async def cleanup(_seq):
            await client.handle_interruption()
            release.set()
        client._notify_reasoning_done = cleanup
        client.script = [[_text("说到"), close_cut, _text("一半"), _text("", "stop")],
                         [newer_begins, _text("x"), _text("", "stop")]]
        await client.prompt_ephemeral("callback", completion_mode="response")
        await newer[0]
    elif name == "declined_over_a_live_reply":
        declined = []

        async def proactive_arrives():
            if not declined:  # once (a tree without the check would recurse)
                declined.append(None)
                declined[0] = await client.prompt_ephemeral("callback")
        client.script = [[_text("文本，"), proactive_arrives, _text("说完"), _text("", "stop")]]
        await client.stream_text("Q")
        assert declined == [False]
    else:  # pragma: no cover
        raise AssertionError(name)


@pytest.mark.parametrize("name", [
    "completes", "interrupted_mid_reply", "interrupted_while_guard_paused",
    "interrupted_in_a_tool", "claimed_in_its_cleanup", "displaced_live",
    "displaced_in_its_cleanup", "cut_by_close", "declined_over_a_live_reply",
    "external_voice_cut", "pending_beside_a_live_reply",
])
async def test_every_begun_generation_is_closed_exactly_once(name):
    client = _client()
    begun, handed_over = [], []
    real_begin = client._begin_response_generation

    def begin(kind="response", owner=None):
        generation = real_begin(kind, owner)
        begun.append(generation)
        return generation

    client._begin_response_generation = begin
    real_interrupt = client.handle_interruption

    async def interrupt():
        kind = await real_interrupt()
        if kind:
            handed_over.append(kind)
        return kind

    client.handle_interruption = interrupt
    client.on_response_displaced = MagicMock(side_effect=handed_over.append)
    idle = MagicMock()
    client.on_idle = idle

    await _scenario(client, name)
    await _drain()

    completions = client.on_response_done.await_count + client.on_proactive_done.await_count
    assert completions + len(handed_over) == len(begun), (completions, handed_over, begun)
    # Nothing is left owned, pending or busy once every call has returned.
    assert getattr(client, "_interrupter_owned_generations", set()) == set()
    assert getattr(client, "_completion_pending_generation", None) is None
    assert client._active_response_generation is None
    assert client._is_responding is False
    assert client.is_idle()
    idle.assert_called()


async def test_an_avatar_poke_over_a_guard_paused_reply_reads_busy(monkeypatch):
    """A guard pause drops _is_responding while its reply is still live. The
    avatar gate must still read busy: prompt_ephemeral would decline anyway,
    after the speech id had been rotated under the paused reply."""
    from tests.unit.test_avatar_interaction_payload_contract import (
        _builtin_runtime, _fist_payload,
    )

    runtime = _builtin_runtime(monkeypatch, cooldown_ms=0)
    runtime.session._active_response_generation = 3  # live, guard-paused
    runtime.session.prompt_ephemeral = AsyncMock(return_value=True)
    result = await runtime.handle_avatar_interaction(_fist_payload("fist-paused"))
    assert result.get("reason") == "busy"
    assert runtime.current_speech_id == ""
    runtime.session.prompt_ephemeral.assert_not_awaited()
    assert runtime._pending_turn_meta is None


async def test_a_failing_settle_never_replaces_the_typed_inputs_own_error():
    mgr = _make_manager()
    mgr._reply_setup_depth = 0
    mgr._process_stream_data_internal = AsyncMock(side_effect=ValueError("input"))
    mgr._settle_owed_turn_wrap_up = AsyncMock(side_effect=RuntimeError("settle"))
    with pytest.raises(ValueError):
        await M._process_stream_input(mgr, {"input_type": "text", "data": "hi"})
    mgr._settle_owed_turn_wrap_up.assert_awaited_once()
    assert mgr._reply_setup_depth == 0


@pytest.mark.parametrize("kind,owed", [("response", True), ("agent_callback", False)])
def test_a_displaced_response_owes_its_wrap_up(kind, owed):
    """The displacing reply's own completion usually pays it, but that reply
    can fail or be interrupted in turn; the displaced one's skipped wrap-up
    must still be on the books then. A displaced agent-callback reply owes
    none (``handle_proactive_complete`` runs no wrap-up)."""
    mgr = _make_transcript_manager()
    mgr._note_ai_turn = lambda text=None, **_kw: None
    mgr._current_ai_turn_text = "摸头，"
    mgr._active_text_request_id = "req-new"
    M._close_displaced_offline_turn(mgr, kind)
    assert getattr(mgr, "_turn_wrap_up_owed", False) is owed
    assert mgr._active_text_request_id == "req-new"


@pytest.mark.parametrize("entry", ["live", "flushed"])
async def test_every_ready_typed_input_goes_through_the_setup_tracking(entry):
    """Both ways a typed input reaches processing (a ready session, or the
    replay of inputs queued while it started) hold an owed wrap-up for the
    input's own reply (``_process_stream_input``)."""
    mgr = _make_manager()
    mgr.session = MagicMock()
    mgr.is_active = True
    mgr.input_cache_lock = asyncio.Lock()
    mgr._pending_input_flush_active = False
    mgr._maybe_handle_mini_game_magic_command = AsyncMock(return_value=False)
    mgr._process_stream_input = AsyncMock()
    mgr._process_stream_data_internal = AsyncMock()
    message = {"input_type": "text", "data": "你好"}
    if entry == "live":
        mgr.session_ready = True
        mgr._starting_session_count = 0
        mgr.pending_input_data = []
        await M._stream_data_now(mgr, message)
    else:
        mgr.session_ready = True
        mgr._starting_session_count = 0
        mgr.pending_input_data = [message]
        await M._flush_pending_input_data(mgr)
    mgr._process_stream_input.assert_awaited_once()
    assert mgr._process_stream_input.await_args.args[0]["data"] == "你好"
    mgr._process_stream_data_internal.assert_not_awaited()


@pytest.mark.parametrize("state", ["live", "completion_pending"])
async def test_a_declined_ephemeral_does_nothing_before_declining(monkeypatch, state):
    """With another reply in progress at entry, prompt_ephemeral declines
    before any work: no anti-repeat preload, no image fitting, and above all
    no vision switch, which would replace and close the client that reply
    is streaming on."""
    import memory.anti_repeat as anti_repeat
    import main_logic.omni_offline_client._lifecycle as lifecycle_module

    client = _client()
    client.vision_model = "vision-x"
    client.switch_model = AsyncMock()
    fit = AsyncMock(return_value=(["img"], None))
    monkeypatch.setattr(lifecycle_module, "fit_images_to_turn_budget", fit)
    corpus = MagicMock()
    corpus.apreload = AsyncMock()
    monkeypatch.setattr(anti_repeat, "get_anti_repeat_corpus", lambda: corpus)
    seen = {}

    async def callback_arrives():
        if seen:
            return
        seen["delivered"] = await client.prompt_ephemeral(
            "callback with media", images=["b64"], completion_mode="response",
        )

    if state == "completion_pending":
        async def cleanup(_seq):
            await callback_arrives()

        client._notify_reasoning_done = cleanup
        client.script = [[_text("回调说完。"), _text("", "stop")]]
        await client.prompt_ephemeral("callback")
    else:
        client.script = [[_text("文本前半，"), callback_arrives, _text("后半。"), _text("", "stop")]]
        await client.stream_text("T")
    assert seen == {"delivered": False}
    client.switch_model.assert_not_awaited()
    fit.assert_not_awaited()
    corpus.apreload.assert_not_awaited()


async def test_a_reply_begun_during_the_ephemeral_setup_is_not_displaced(monkeypatch):
    """Another reply can begin during prompt_ephemeral's own setup awaits
    (the anti-repeat preload here); the check right before the begin still
    declines, so that reply is neither cut nor handed over."""
    import memory.anti_repeat as anti_repeat

    client = _client()
    client.on_response_displaced = MagicMock()
    other = {}

    async def preload(_name):
        other["generation"] = client._begin_response_generation()

    corpus = MagicMock()
    corpus.apreload = AsyncMock(side_effect=preload)
    monkeypatch.setattr(anti_repeat, "get_anti_repeat_corpus", lambda: corpus)
    client.script = [[_text("不该发出"), _text("", "stop")]]
    delivered = await client.prompt_ephemeral("avatar", completion_mode="response")
    assert delivered is False
    assert client.requests == []
    assert client._active_response_generation == other["generation"]
    client.on_response_displaced.assert_not_called()
    client._finish_response_generation(other["generation"])


# ── Every proactive gate reads a reply in progress the same way ──────────────


def _callback_mgr(client):
    """``_manager`` with the real agent-callback delivery: the real
    ``trigger_agent_callbacks`` claiming through a real SessionStateMachine
    (test_proactive_sm_integration's proactive doubles), one callback queued."""
    from tests.unit.test_proactive_sm_integration import _make_mgr

    mgr = _manager(client)
    del mgr.trigger_agent_callbacks
    proactive = _make_mgr(session=client)
    for name in (
        "state", "_proactive_write_lock", "_voice_proactive_inject_lock",
        "_voice_playback_active", "is_active", "input_mode",
        "_starting_session_count", "_starting_input_mode", "goodbye_silent",
        "goodbye_silent_reason", "goodbye_silent_updated_at",
        "proactive_manager", "_get_text_guard_max_length",
    ):
        setattr(mgr, name, getattr(proactive, name))
    mgr.user_language = "zh-CN"
    mgr.is_preparing_new_session = False
    mgr.pending_session_warmed_up_event = None
    mgr.current_speech_id = "user-sid"
    mgr.pending_agent_callbacks = [{"status": "completed", "summary": "任务完成"}]
    client.on_proactive_done = AsyncMock()
    return mgr


async def _settle_bg(mgr):
    for _ in range(3):
        await asyncio.gather(*mgr._bg)
        await _drain()


async def test_a_callback_during_a_guard_retry_leaves_the_reply_its_speech_id():
    """A guard pauses the typed reply (role-hallucination prefix) and the
    core's discard handling runs while it is paused; an agent callback fired
    then must not claim the turn. Claiming rotated the speech id under the
    paused reply before prompt_ephemeral declined, so its retry (or a
    recovered sentence, fed to TTS under the speech id it froze) landed under
    a proactive id. The callback stays queued and goes out after the reply."""
    from main_logic.session_state import ProactivePhase

    client = _client()
    client.enable_response_guard = True
    client._prefix_buffer_size = 3
    client.max_response_rerolls = 1
    mgr = _callback_mgr(client)
    seen = {}

    async def discarded(reason, attempt, max_attempts, will_retry, message):
        seen["paused"] = (
            client._active_response_generation is not None,
            client._is_responding,
        )
        seen["claimed"] = await M.trigger_agent_callbacks(mgr)
        seen["phase"] = mgr.state.phase

    speech_ids = []

    async def on_text_delta(text, is_first, **_kw):
        speech_ids.append((text, mgr.current_speech_id))

    client.on_response_discarded = discarded
    client.on_text_delta = on_text_delta
    client.script = [
        [_text("M | 我替主人说"), _text("", "stop")],
        [_text("这才是回复。"), _text("", "stop")],
        [_text("任务做完啦。"), _text("", "stop")],
    ]
    await client.stream_text("hi")
    reply = speech_ids[:]
    await _settle_bg(mgr)

    assert seen == {
        "paused": (True, False), "claimed": False, "phase": ProactivePhase.IDLE,
    }
    assert reply == [("这才是回复。", "user-sid")]
    assert len(client.requests) == 3  # the callback went out after the reply
    assert speech_ids[-1][0] == "任务做完啦。"
    assert speech_ids[-1][1] != "user-sid"
    assert mgr.pending_agent_callbacks == []


async def test_a_callback_during_the_too_long_recovery_leaves_it_spoken():
    """A typed reply runs out of length rerolls; the core's discard recovery
    shows and speaks its fallback under a speech id of its own while the
    generation is still guard-paused. An agent callback fired then claimed the
    turn and rotated the speech id, so the recovery's TTS feed
    (expected_speech_id=recovery_turn_id) was dropped: shown, never spoken.
    The claim is now refused, and the callback goes out after the reply."""
    client = _client()
    client.enable_response_guard = True
    client.max_response_length = 8
    client.max_response_rerolls = 0
    mgr = _callback_mgr(client)
    mgr.use_tts = True
    client.on_response_discarded = mgr.handle_response_discarded
    seen = {}
    real_send = mgr.send_lanlan_response

    async def send(text, *args, **kwargs):  # the recovery's first await
        if not seen:
            seen["paused"] = (
                client._active_response_generation is not None,
                client._is_responding,
            )
            seen["claimed"] = await M.trigger_agent_callbacks(mgr)
        return await real_send(text, *args, **kwargs)

    mgr.send_lanlan_response = send
    client.script = [
        [_text("我在说，一直说，不停地说，还在说，继续说，说个没完，还要说，"), _text("", "stop")],
        [_text("任务做完啦。"), _text("", "stop")],
    ]
    await client.stream_text("hi")
    await _settle_bg(mgr)

    assert seen == {"paused": (True, False), "claimed": False}
    [recovery] = mgr.sent_responses
    assert mgr.tts_pending_chunks == [(recovery["turn_id"], recovery["text"])]
    assert len(client.requests) == 2  # the callback went out after the reply
    assert mgr.pending_agent_callbacks == []


@pytest.mark.parametrize("state", ["live", "guard_paused", "completion_pending"])
async def test_every_proactive_gate_reads_a_reply_in_progress(state):
    """The SM claim (try_start_proactive: greetings, /api/proactive_chat,
    agent callbacks), its 409 pre-check and the manager's release gate all
    refuse while a reply is live, guard-paused or waiting on its completion,
    the same check prompt_ephemeral declines on."""
    from main_logic.session_state import ProactivePhase
    from tests.unit.test_proactive_sm_integration import _make_mgr

    client = _client()
    mgr = _make_mgr(session=client)
    seen = {}

    async def gates():
        if seen:
            return
        if state == "guard_paused":
            client._pause_response_generation(client._active_response_generation)
        seen["declines"] = client._declines_over_another_reply("proactive")
        seen["release"] = M._can_release_proactive(mgr)
        seen["can_start"] = mgr.state.can_start_proactive(session=client)
        seen["claimed"] = await mgr.state.try_start_proactive(session=client)
        if state == "guard_paused":
            client._resume_response_generation(client._active_response_generation)

    if state == "completion_pending":
        async def cleanup(_seq):
            await gates()

        client._notify_reasoning_done = cleanup
        client.script = [[_text("回调说完。"), _text("", "stop")]]
        await client.prompt_ephemeral("callback")
    else:
        client.script = [[_text("文本前半，"), gates, _text("后半。"), _text("", "stop")]]
        await client.stream_text("T")
    assert seen == {
        "declines": True, "release": False, "can_start": False, "claimed": False,
    }
    assert mgr.state.phase is ProactivePhase.IDLE
    assert client.is_idle()
    assert mgr.state.can_start_proactive(session=client) is True


@pytest.mark.parametrize("other", ["none", "cancelled_reply_in_its_tool"])
async def test_a_callback_queued_during_a_reply_goes_out_right_after_it(other):
    """The gates read a reply in progress, not ``is_idle``: a reply call that
    is only still returning holds no reply. With a cancelled reply still in
    its tool handler (is_idle False), the callback queued during the next
    reply is still delivered by that reply's wrap-up; an is_idle gate left it
    queued, and nothing owed would re-trigger it when that task ended."""
    handler, entered, release = _parked_tool()
    client = _client(handler=handler)
    mgr = _callback_mgr(client)
    at_claim = []
    real_claim = mgr.state.try_start_proactive

    async def claim(session=None):
        at_claim.append(client.is_idle())
        return await real_claim(session=session)

    mgr.state.try_start_proactive = claim
    script = [[_text("用户回复。"), _text("", "stop")], [_text("任务做完啦。"), _text("", "stop")]]
    cancelled = None
    if other == "cancelled_reply_in_its_tool":
        client.script = [[_text("我查一下"), _tool_calls("c1")], *script]
        cancelled = asyncio.ensure_future(client.stream_text("Q0"))
        await asyncio.wait_for(entered.wait(), 1)
        await mgr._interrupt_offline_reply(client)
    else:
        client.script = script
    await client.stream_text("Q1")
    await _settle_bg(mgr)

    # One claim, made by that reply's wrap-up; with the cancelled reply still
    # in its handler the session is not idle there, which is the window.
    assert at_claim == ([False] if cancelled is not None else [at_claim[0]])
    assert mgr.pending_agent_callbacks == []
    client.on_proactive_done.assert_awaited_once()
    release.set()
    if cancelled is not None:
        await cancelled
    await _settle_bg(mgr)


def test_the_reply_check_reads_only_what_a_session_has():
    """Realtime sessions (and doubles) answer with ``_is_responding`` alone;
    generation fields count only when they are ints."""
    from types import SimpleNamespace

    from main_logic.session_state import session_reply_in_progress

    assert session_reply_in_progress(None) is False
    assert session_reply_in_progress(SimpleNamespace(_is_responding=True)) is True
    assert session_reply_in_progress(SimpleNamespace(_is_responding=False)) is False
    assert session_reply_in_progress(MagicMock(_is_responding=False)) is False
    assert session_reply_in_progress(
        SimpleNamespace(_is_responding=False, _active_response_generation=4)
    ) is True
    assert session_reply_in_progress(
        SimpleNamespace(_is_responding=False, _completion_pending_generation=4)
    ) is True


async def test_a_claimed_reply_is_closed_with_its_own_request_id(monkeypatch):
    """A finished, silent reply A waits in its completion window (its status
    send) while typed B has stored req-B and is still in setup. An
    interruption that claims A closes and abandons it as req-A; B keeps its
    request for its own turn end."""
    session, mgr, notes = _wire_text_path(monkeypatch)
    a_in_window, a_release, b_parked, b_go = (asyncio.Event() for _ in range(4))
    tasks = []

    async def status(_payload):
        a_in_window.set()
        await a_release.wait()

    session.on_status_message = status
    # A's empty completion is retried: three requests, then its status send.
    session.script = [
        [_text("", "stop")],
        [_text("", "stop")],
        [_text("", "stop")],
        [_text("B的回复。"), _text("", "stop")],
    ]
    real_focus = mgr._focus_inline_decision

    async def focus_hook(text):
        if text == "A":  # A stored req-A; B arrives and interrupts nothing
            tasks.append(asyncio.create_task(M._process_stream_data_internal(
                mgr, {"input_type": "text", "data": "B", "request_id": "req-B"},
            )))
            await b_parked.wait()
        elif text == "B":  # B stored req-B and has not begun
            b_parked.set()
            await b_go.wait()
        return await real_focus(text)

    mgr._focus_inline_decision = focus_hook
    turn_a = asyncio.create_task(M._process_stream_data_internal(
        mgr, {"input_type": "text", "data": "A", "request_id": "req-A"},
    ))
    await asyncio.wait_for(a_in_window.wait(), 5)
    assert mgr._active_text_request_id == "req-B"

    assert await mgr._interrupt_offline_reply(session)  # claims A's completion
    assert _system(mgr) == [{"type": "system", "data": "turn abandoned", "request_id": "req-A"}]
    assert mgr._active_text_request_id == "req-B"

    a_release.set()
    b_go.set()
    await asyncio.wait_for(asyncio.gather(turn_a, *tasks), 5)
    assert [m.get("request_id") for m in _turn_ends(mgr)] == ["req-B"]
    assert notes == ["B的回复。"]


async def test_a_deferred_typed_input_leaves_the_debt_to_its_replay():
    """A typed input deferred back to the pending queue (its session is still
    starting) has not been handled: the owed wrap-up stays owed until the
    replay of that input settles it."""
    from main_logic.core.session_records import INPUT_DISPATCH_DEFERRED

    mgr = _make_manager()
    mgr._turn_wrap_up_owed = True
    mgr._settle_owed_turn_wrap_up = AsyncMock()
    mgr._process_stream_data_internal = AsyncMock(return_value=INPUT_DISPATCH_DEFERRED)
    message = {"input_type": "text", "data": "你好"}

    result = await M._process_stream_input(mgr, message, on_dispatch_attempted=lambda: None)
    assert result is INPUT_DISPATCH_DEFERRED
    mgr._settle_owed_turn_wrap_up.assert_not_awaited()
    assert getattr(mgr, "_reply_setup_depth", 0) == 0

    mgr._process_stream_data_internal = AsyncMock(return_value=None)  # the replay
    await M._process_stream_input(mgr, message, on_dispatch_attempted=lambda: None)
    mgr._settle_owed_turn_wrap_up.assert_awaited_once()


@pytest.mark.parametrize("cancel_interrupter", [False, True])
async def test_an_interrupter_cancelled_in_its_own_wait_still_closes_the_reply(
    monkeypatch, cancel_interrupter,
):
    """The interrupter (e.g. the ASR detector worker preparing a voice turn)
    is cancelled while it waits for the reply's task: the reply was taken
    over already and skipped its own completion, so the interrupter still
    closes it: one turn end, the half it said noted as its own AI turn, the
    wrap-up owed."""
    session, mgr, notes = _wire_text_path(monkeypatch)
    voice, streaming = _voice_reply(
        session, _text("语音说到一半，"), "park", _text("后半句。"), _text("", "stop"),
    )
    await asyncio.wait_for(streaming.wait(), 5)
    interrupter = asyncio.ensure_future(mgr._interrupt_offline_reply(session))
    await asyncio.sleep(0)
    if cancel_interrupter:
        interrupter.cancel()
    result = (await asyncio.gather(interrupter, return_exceptions=True))[0]
    await asyncio.gather(voice, return_exceptions=True)
    await _drain()
    if cancel_interrupter:
        assert isinstance(result, asyncio.CancelledError)
    else:
        assert result is True
    assert _turn_ends(mgr) == [{"type": "system", "data": "turn end"}]
    assert mgr._current_ai_turn_text == ""
    assert mgr._turn_wrap_up_owed is True
    assert notes == ["语音说到一半，"]  # the half it said, as its own AI turn


async def test_a_cancelled_interrupter_sends_the_notice_before_its_cancellation_goes_on(
    monkeypatch,
):
    """The reply a cancelled interrupter took over is bound: its frontend
    notice goes out before the interrupter's cancellation propagates, as on
    the normal path, so it cannot land after a newer reply's text."""
    from main_logic.core._shared import _ReplyTurn

    session, mgr, _notes = _wire_text_path(monkeypatch)
    voice, streaming = _voice_reply(
        session, _text("说到一半，"), "park", _text("后半句。"), _text("", "stop"),
    )
    await asyncio.wait_for(streaming.wait(), 5)
    session._active_reply_owner = _ReplyTurn(speech_id="sid-A", request_id="req-A")
    mgr._active_text_request_id = "req-A"
    gate = asyncio.Event()
    real_send = mgr.websocket.send_json

    async def send_json(payload):
        if payload.get("data") == "turn abandoned":
            await gate.wait()
        await real_send(payload)

    mgr.websocket.send_json = send_json
    interrupter = asyncio.ensure_future(mgr._interrupt_offline_reply(session))
    await asyncio.sleep(0)
    interrupter.cancel()
    await _drain()
    assert not interrupter.done(), "the cancellation waits for the notice"
    gate.set()
    result = (await asyncio.gather(interrupter, return_exceptions=True))[0]
    assert isinstance(result, asyncio.CancelledError)
    assert _system(mgr) == [{"type": "system", "data": "turn abandoned", "request_id": "req-A"}]
    await asyncio.gather(voice, return_exceptions=True)


async def _recovery_cut_by_a_typed_input(monkeypatch, request_id):
    """A final length discard's recovery (RESPONSE_TOO_LONG) is interrupted
    by a typed input in its first await; the typed input then holds the
    owed wrap-up while it sets its reply up."""
    from main_logic.core._shared import _ReplyTurn

    session, mgr, _notes = _wire_text_path(monkeypatch)
    session.enable_response_guard = True
    session.max_response_length = 8
    session.max_response_rerolls = 0
    mgr.use_tts = False
    mgr.user_language = "zh-CN"
    mgr._active_text_request_id = request_id
    reply_turn = _ReplyTurn(speech_id=mgr.current_speech_id, request_id=request_id)
    reply_turn.session = session
    facts = {"finalize_depths": []}
    interrupted, release_setup = asyncio.Event(), asyncio.Event()
    real_send = mgr.send_lanlan_response
    real_finalize = mgr._finalize_turn_after_emit

    async def finalize():
        facts["finalize_depths"].append(getattr(mgr, "_reply_setup_depth", 0))
        await real_finalize()

    mgr._finalize_turn_after_emit = finalize

    async def typed_input_setup():
        facts["interrupted"] = await mgr._interrupt_offline_reply(session)
        interrupted.set()
        await release_setup.wait()

    interrupter = None

    async def send(text, *args, **kwargs):
        nonlocal interrupter
        if interrupter is None:
            interrupter = asyncio.ensure_future(
                mgr._with_owed_wrap_up_held(typed_input_setup())
            )
            await interrupted.wait()
        return await real_send(text, *args, **kwargs)

    mgr.send_lanlan_response = send

    async def discarded(reason, attempt, max_attempts, will_retry, message=None):
        await mgr.handle_response_discarded(
            reason, attempt, max_attempts, will_retry, message,
            request_id=request_id, reply_turn=reply_turn,
        )

    async def done():
        await mgr.handle_response_complete(reply_turn=reply_turn)

    session.script = [[_text("我在说，一直说，不停地说，还在说，继续说，说个没完，还要说，"), _text("", "stop")]]
    await session.stream_text(
        "hi", response_discarded_callback=discarded,
        response_done_callback=done, reply_owner=reply_turn,
    )
    facts["finalized_inside_the_hold"] = list(facts["finalize_depths"])
    release_setup.set()
    await asyncio.gather(interrupter)
    await _drain()
    facts["turn_ends"] = _turn_ends(mgr)
    facts["owed"] = mgr._turn_wrap_up_owed
    return facts


@pytest.mark.parametrize("request_id", [None, "req-A"])
async def test_a_recovery_taken_over_neither_ends_its_turn_again_nor_finalizes_in_the_hold(
    monkeypatch, request_id,
):
    """The interrupter closed the reply (one turn end) and recorded the
    wrap-up as owed. The recovery stops sending (even with no request id to
    tell it so) and pays the debt through the settle, which waits for the
    typed input's hold: finalized once, after the hold."""
    facts = await _recovery_cut_by_a_typed_input(monkeypatch, request_id)
    assert facts["interrupted"] is True
    # Cut before its body was published: the recovery knows it was taken
    # over (``taken_over``), with or without a request id, and stops there;
    # nothing reached cross_server, so there is nothing to end.
    assert facts["turn_ends"] == []
    assert facts["finalized_inside_the_hold"] == []
    assert facts["finalize_depths"] == [0]
    assert facts["owed"] is False


async def test_a_recovery_cut_before_its_body_is_published_sends_no_empty_turn_end():
    """Nothing was published before the final discard, and the recovery is
    interrupted while ``send_lanlan_response`` still awaits its Focus
    cleanup: no body has reached cross_server yet, so the takeover queues no
    empty turn end there (it would re-trigger analysis and eat the turn's
    meta), and the recovery, no longer owning the output, publishes none."""
    mgr = _make_manager()
    mgr.is_active = True
    mgr.websocket = _FakeConnectedWebSocket()
    mgr._finalize_turn_after_emit = AsyncMock()
    mgr._note_ai_turn = lambda text=None, **_kw: None
    mgr._open_reply_turn = None
    mgr.use_tts = False
    mgr._clear_tts_pipeline = AsyncMock()
    session = MagicMock(_conversation_history=[])
    mgr.session = session
    reply_turn = mgr._begin_reply_turn(speech_id=mgr.current_speech_id, request_id="req-A")
    reply_turn.session = session
    session.handle_interruption = AsyncMock(
        return_value=InterruptedReply("response", owner=reply_turn))
    mgr._active_text_request_id = "req-A"
    mgr._discarded_turn_open = False
    mgr.send_lanlan_response = M.send_lanlan_response.__get__(mgr)
    cuts = []

    async def focus_cleanup(active):
        # The first chunk's Focus cleanup, right before the publish.
        if active is False and not cuts:
            cuts.append(await mgr._interrupt_offline_reply(session))

    mgr._push_focus_thinking = focus_cleanup
    await mgr.handle_response_discarded(
        "length_truncated", 1, 1, False,
        '{"code": "RESPONSE_LENGTH_TRUNCATED", "text": "截断到这里。"}',
        request_id="req-A",
        reply_turn=reply_turn,
    )
    assert cuts == [True]
    turn_ends = [i for i, m in enumerate(mgr.sync_message_queue.messages)
                 if m.get("data") == "turn end"]
    bodies = [i for i, m in enumerate(mgr.sync_message_queue.messages)
              if m.get("type") == "json"]
    # Taken over in that await, the recovery publishes no body either.
    assert bodies == [] and turn_ends == []
    assert mgr._discarded_turn_open is False


_LONG_BODY = "我在说，一直说，不停地说，还在说，继续说，说个没完，还要说，"


def _recovery_setup(monkeypatch, request_id):
    from main_logic.core._shared import _ReplyTurn

    session, mgr, _notes = _wire_text_path(monkeypatch)
    session.enable_response_guard = True
    session.max_response_length = 8
    session.max_response_rerolls = 0
    mgr.use_tts = False
    mgr.user_language = "zh-CN"
    mgr._active_text_request_id = request_id
    reply_turn = _ReplyTurn(speech_id=mgr.current_speech_id, request_id=request_id)
    reply_turn.session = session
    depths = []
    real_finalize = mgr._finalize_turn_after_emit

    async def finalize():
        depths.append(getattr(mgr, "_reply_setup_depth", 0))
        await real_finalize()

    mgr._finalize_turn_after_emit = finalize
    return session, mgr, reply_turn, depths


async def _drive_final_discard(session, mgr, reply_turn, request_id):
    async def discarded(reason, attempt, max_attempts, will_retry, message=None):
        await mgr.handle_response_discarded(
            reason, attempt, max_attempts, will_retry, message,
            request_id=request_id, reply_turn=reply_turn,
        )

    async def done():
        await mgr.handle_response_complete(reply_turn=reply_turn)

    session.script = [[_text(_LONG_BODY), _text("", "stop")]]
    await session.stream_text(
        "hi", response_discarded_callback=discarded,
        response_done_callback=done, reply_owner=reply_turn,
    )


def _held_typed_input(mgr, session, facts, release):
    interrupted = asyncio.Event()

    async def setup():
        facts["interrupted"] = await mgr._interrupt_offline_reply(session)
        facts["ws_mark"] = len(mgr.websocket.sent)
        interrupted.set()
        await release.wait()

    return setup, interrupted


async def test_a_takeover_in_the_recoverys_own_turn_end_send_still_defers_its_wrap_up(
    monkeypatch,
):
    """The typed input takes the reply over while the recovery sends its own
    turn end (its turn already ended, so the takeover records no debt): the
    recovery still owes its wrap-up and settles it after the typed input's
    hold instead of finalizing inside it."""
    session, mgr, reply_turn, depths = _recovery_setup(monkeypatch, "req-A")
    facts, release, holder = {}, asyncio.Event(), {}
    setup, interrupted = _held_typed_input(mgr, session, facts, release)
    real_send_turn_end = mgr._send_turn_end_to_frontend

    async def send_turn_end(msg):
        if "task" not in holder and reply_turn.turn_ended:
            holder["task"] = asyncio.ensure_future(mgr._with_owed_wrap_up_held(setup()))
            await interrupted.wait()
        await real_send_turn_end(msg)

    mgr._send_turn_end_to_frontend = send_turn_end
    await _drive_final_discard(session, mgr, reply_turn, "req-A")
    inside_the_hold = list(depths)
    release.set()
    await holder["task"]
    await _drain()
    assert facts["interrupted"] is True
    assert inside_the_hold == []
    assert depths == [0]
    assert mgr._turn_wrap_up_owed is False


async def test_a_recovery_without_request_id_taken_over_before_its_loop_sends_nothing(
    monkeypatch,
):
    """No request id, nothing shown before the guard caught it, and the
    takeover lands in the recovery's TTS cleanup await: the recovery knows it
    was taken over and publishes no body and no turn end into the new turn."""
    session, mgr, reply_turn, _depths = _recovery_setup(monkeypatch, None)
    facts, release, holder = {}, asyncio.Event(), {}
    setup, interrupted = _held_typed_input(mgr, session, facts, release)
    real_land = mgr._let_tts_interrupt_land

    async def land(interrupt, still_owned=None):
        if "task" not in holder:
            holder["task"] = asyncio.ensure_future(mgr._with_owed_wrap_up_held(setup()))
            await interrupted.wait()
        await real_land(interrupt, still_owned)

    mgr._let_tts_interrupt_land = land
    await _drive_final_discard(session, mgr, reply_turn, None)
    after = [(m.get("type"), m.get("data")) for m in mgr.websocket.sent[facts["ws_mark"]:]]
    release.set()
    await holder["task"]
    await _drain()
    assert facts["interrupted"] is True
    assert ("system", "turn end") not in after
    assert _turn_ends(mgr) == []
    assert not [m for m in mgr.sync_message_queue.messages if m.get("type") == "json"]


def _history_messages(session):
    return [
        (type(m).__name__, m.content) for m in session._conversation_history
        if isinstance(m, (AIMessage, HumanMessage))
    ]


@pytest.mark.parametrize("request_id", [None, "req-A"])
async def test_a_recovery_taken_over_in_its_body_send_still_keeps_the_body_in_history(
    monkeypatch, request_id,
):
    """The takeover lands in the WebSocket send of the recovery body, after
    the body was published to cross_server: the steps after it stop, but the
    body the user saw and memory recorded is in history, before anything the
    taker adds."""
    session, mgr, reply_turn, _depths = _recovery_setup(monkeypatch, request_id)
    facts, release, holder = {}, asyncio.Event(), {}
    setup, interrupted = _held_typed_input(mgr, session, facts, release)

    async def send(text, is_first_chunk=False, turn_id=None, *, request_id=None,
                   on_published=None, publish_if=None, **_kw):
        # send_lanlan_response's order: guard, sync publish, on_published,
        # then the WebSocket send, where the takeover lands.
        if publish_if is not None and not publish_if():
            return None
        mgr.sync_message_queue.put({"type": "json", "data": {"type": "gemini_response", "text": text}})
        if on_published is not None:
            on_published(0.0)
        if "task" not in holder:
            holder["task"] = asyncio.ensure_future(mgr._with_owed_wrap_up_held(setup()))
            await interrupted.wait()
        await mgr.websocket.send_json({"type": "gemini_response", "text": text})
        return True

    mgr.send_lanlan_response = send
    await _drive_final_discard(session, mgr, reply_turn, request_id)
    session._conversation_history.append(HumanMessage(content="B"))
    release.set()
    await holder["task"]
    await _drain()
    assert facts["interrupted"] is True
    history = _history_messages(session)
    assert history[0] == ("HumanMessage", "hi")
    assert history[1][0] == "AIMessage" and history[1][1]
    assert history[2] == ("HumanMessage", "B")


async def test_a_takeover_in_the_discards_tts_wait_sends_no_stale_notice(monkeypatch):
    """The takeover lands while the discard waits for its TTS interrupt to
    land (a live worker): the taker may already be sending to the frontend,
    so the discard no longer sends its ``response_discarded``, which would
    clear the taker's bubble and audio. cross_server's clear went out before
    the wait."""
    session, mgr, reply_turn, _depths = _recovery_setup(monkeypatch, "req-A")
    facts, release, holder = {}, asyncio.Event(), {}
    setup, interrupted = _held_typed_input(mgr, session, facts, release)
    real_land = mgr._let_tts_interrupt_land

    async def land(interrupt, still_owned=None):
        if "task" not in holder:
            holder["task"] = asyncio.ensure_future(mgr._with_owed_wrap_up_held(setup()))
            await interrupted.wait()
        await real_land(interrupt, still_owned)

    mgr._let_tts_interrupt_land = land
    await _drive_final_discard(session, mgr, reply_turn, "req-A")
    release.set()
    await holder["task"]
    await _drain()
    sync = [m.get("data") for m in mgr.sync_message_queue.messages if isinstance(m, dict)]
    ws_types = [m.get("type") for m in mgr.websocket.sent]
    assert facts["interrupted"] is True
    assert "response_discarded_clear" in sync
    assert "response_discarded" not in ws_types


async def test_a_discard_taken_over_in_its_frontend_notice_leaves_the_tts_pipeline_alone(
    monkeypatch,
):
    """The takeover lands in the WebSocket send of ``response_discarded``:
    the taker may already be feeding the TTS pipeline, so the discard no
    longer clears it (the taker clears what was left there itself)."""
    session, mgr, reply_turn, _depths = _recovery_setup(monkeypatch, "req-A")
    facts, release, holder = {}, asyncio.Event(), {}
    setup, interrupted = _held_typed_input(mgr, session, facts, release)
    clears = []
    real_finish = mgr._finish_tts_clear

    async def finish(interrupt):
        clears.append("taken_over" in holder)
        await real_finish(interrupt)

    mgr._finish_tts_clear = finish
    real_send_json = mgr.websocket.send_json

    async def send_json(payload):
        if payload.get("type") == "response_discarded" and "task" not in holder:
            holder["task"] = asyncio.ensure_future(mgr._with_owed_wrap_up_held(setup()))
            await interrupted.wait()
            holder["taken_over"] = True
        await real_send_json(payload)

    mgr.websocket.send_json = send_json
    await _drive_final_discard(session, mgr, reply_turn, "req-A")
    release.set()
    await holder["task"]
    await _drain()
    assert facts["interrupted"] is True
    assert True not in clears, "no TTS clear after the takeover"


async def test_a_final_discard_interrupts_tts_before_telling_the_frontend(monkeypatch):
    """With a live TTS worker and the discarded reply's audio still queued,
    the worker is interrupted and that audio dropped before the frontend's
    ``response_discarded`` goes out: the frontend clears its audio on the
    notice, and nothing of the discarded reply may arrive after it."""
    import queue

    class _AliveThread:
        def is_alive(self):
            return True

    session, mgr, reply_turn, _depths = _recovery_setup(monkeypatch, "req-A")
    mgr.tts_thread = _AliveThread()
    mgr.tts_request_queue = queue.Queue()
    mgr.tts_response_queue = queue.Queue()
    mgr.tts_pending_chunks = []
    mgr.tts_cache_lock = asyncio.Lock()
    old_sid = mgr.current_speech_id
    mgr.tts_response_queue.put(("__audio__", old_sid, b"old-audio"))
    seen = {}
    real_send_json = mgr.websocket.send_json

    async def send_json(payload):
        if payload.get("type") == "response_discarded":
            seen["requests"] = list(mgr.tts_request_queue.queue)
            seen["responses"] = list(mgr.tts_response_queue.queue)
        await real_send_json(payload)

    mgr.websocket.send_json = send_json
    await _drive_final_discard(session, mgr, reply_turn, "req-A")
    await _drain()
    assert ("__interrupt__", None) in seen["requests"]
    assert ("__audio__", old_sid, b"old-audio") not in seen["responses"]


async def test_audio_a_handler_already_held_goes_out_before_the_discard_notice(monkeypatch):
    """The TTS response handler had already taken an audio item off the
    queue when the discard interrupted the worker, so draining cannot take
    it back: the discard lets it go out before telling the frontend, or it
    would play after the frontend cleared its audio."""
    import queue

    class _AliveThread:
        def is_alive(self):
            return True

    session, mgr, reply_turn, _depths = _recovery_setup(monkeypatch, "req-A")
    mgr.tts_thread = _AliveThread()
    mgr.tts_request_queue = queue.Queue()
    mgr.tts_response_queue = queue.Queue()
    mgr.tts_pending_chunks = []
    mgr.tts_cache_lock = asyncio.Lock()
    real_interrupt = mgr._interrupt_tts_now

    def interrupt_with_audio_in_hand():
        result = real_interrupt()
        # The handler resumes with its item on the next loop turns.
        loop = asyncio.get_running_loop()
        loop.call_soon(lambda: asyncio.ensure_future(
            mgr.websocket.send_json({"type": "audio_in_hand"})
        ))
        return result

    mgr._interrupt_tts_now = interrupt_with_audio_in_hand
    await _drive_final_discard(session, mgr, reply_turn, "req-A")
    await _drain()
    types = [m.get("type") for m in mgr.websocket.sent]
    assert types.index("audio_in_hand") < types.index("response_discarded")
