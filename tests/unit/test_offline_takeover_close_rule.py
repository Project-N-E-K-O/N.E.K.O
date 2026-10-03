"""One rule closes an Offline reply whose close was taken over.

Two takers skip a reply's completion and close it instead: an interrupter
(``_interrupt_offline_reply``) and a user reply that began over it
(``_close_displaced_offline_turn``). Both go through
``_close_taken_over_offline_reply``:

- the reply's own snapshot (a bound reply) supplies its request id; an
  unbound reply answers no request, so neither taker reads or clears
  ``_active_text_request_id``, which by then may be a typed turn's that is
  still setting up its reply;
- the frontend's current bubble is sealed with the reply's turn end while it
  is still the reply's own: a reply that streamed to the end, or any
  displaced one (the displacing reply has emitted nothing, and its user input
  came before the displaced reply began);
- otherwise a bound reply's request is released with ``turn abandoned``: an
  interrupter's user input has already split the bubbles.

The matrix drives both takers directly; the end-to-end cases run the real
client and the real typed path.
"""
import asyncio
import itertools
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.core._shared import _ReplyTurn
from main_logic.omni_offline_client._lifecycle import InterruptedReply
from tests.unit.test_core_game_route_memory_contract import (
    _FakeConnectedWebSocket,
    _make_manager,
    _make_offline_session_for_callback_media,
)
from tests.unit.test_offline_reply_ownership import (
    M,
    _system,
    _turn_ends,
    _wire_text_path,
)
from tests.unit.test_offline_turn_cancellation_e2e import _text

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_token_tracker(monkeypatch):
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", MagicMock())


_CASES = list(itertools.product(
    ("bound", "unbound"),
    ("interrupted", "displaced"),
    ("finished", "mid_stream"),
    ("response", "agent_callback"),
))


def _expected(binding, taker, progress, kind):
    """What the rule sends: (sync turn end, frontend messages)."""
    bound = binding == "bound"
    if kind == "agent_callback":
        sync = {"type": "system", "data": "turn end agent_callback"}
        sealed = dict(sync)
    else:
        sync = {"type": "system", "data": "turn end"}
        if bound:
            sync["request_id"] = "req-own"
        sealed = {"type": "system", "data": "turn end", "request_id": "req-own" if bound else None}
    if progress == "finished" or taker == "displaced":
        return sync, [sealed]
    if bound and kind == "response":
        return sync, [{"type": "system", "data": "turn abandoned", "request_id": "req-own"}]
    return sync, []


async def _take_over(mgr, taker, handed_over):
    if taker == "interrupted":
        session = _make_offline_session_for_callback_media()
        session.handle_interruption = AsyncMock(return_value=handed_over)
        assert await M._interrupt_offline_reply(mgr, session) is True
        return
    followup = M._close_displaced_offline_turn(mgr, handed_over)
    if followup is not None:
        await followup()


@pytest.mark.parametrize("binding, taker, progress, kind", _CASES)
async def test_both_takers_close_a_reply_by_the_same_rule(binding, taker, progress, kind):
    mgr = _make_manager()
    mgr.websocket = _FakeConnectedWebSocket()
    mgr._note_ai_turn = lambda text=None, **_kw: None
    mgr._current_ai_turn_text = "它已经说出口的半句"
    # A typed turn is still setting up its reply: the field is that turn's.
    mgr._active_text_request_id = "req-typed"
    owner = _ReplyTurn(speech_id=None, request_id="req-own") if binding == "bound" else None
    handed_over = InterruptedReply(kind, finished=progress == "finished", owner=owner)

    await _take_over(mgr, taker, handed_over)

    sync, frontend = _expected(binding, taker, progress, kind)
    assert _turn_ends(mgr) == [sync]
    assert _system(mgr) == frontend
    assert mgr._active_text_request_id == "req-typed"
    assert mgr._current_ai_turn_text == ""
    assert getattr(mgr, "_turn_wrap_up_owed", False) is (kind == "response")


@pytest.mark.parametrize("taker", ["interrupted", "displaced"])
@pytest.mark.parametrize("binding", ["bound", "unbound"])
async def test_a_reply_that_said_nothing_has_no_bubble_to_seal(taker, binding):
    """Only the request a bound reply answered is released."""
    mgr = _make_manager()
    mgr.websocket = _FakeConnectedWebSocket()
    mgr._note_ai_turn = lambda text=None, **_kw: None
    mgr._active_text_request_id = "req-typed"
    owner = _ReplyTurn(speech_id=None, request_id="req-own") if binding == "bound" else None

    await _take_over(mgr, taker, InterruptedReply("response", finished=True, owner=owner))

    assert _turn_ends(mgr) == []
    assert _system(mgr) == (
        [{"type": "system", "data": "turn abandoned", "request_id": "req-own"}]
        if binding == "bound" else []
    )
    assert mgr._active_text_request_id == "req-typed"


async def test_a_bound_close_retires_only_its_own_request_id():
    mgr = _make_manager()
    mgr.websocket = _FakeConnectedWebSocket()
    mgr._note_ai_turn = lambda text=None, **_kw: None
    mgr._current_ai_turn_text = "说到一半"
    mgr._active_text_request_id = "req-own"
    owner = _ReplyTurn(speech_id=None, request_id="req-own")

    await _take_over(mgr, "interrupted", InterruptedReply("response", owner=owner))

    assert mgr._active_text_request_id is None
    assert owner.turn_ended is True


async def test_a_proactive_reply_displaced_mid_stream_is_sealed_before_the_typed_reply(
    monkeypatch,
):
    """An agent callback goes out while a typed input is still setting up its
    reply (no reply was in progress for its gate), and the typed reply then
    begins over it mid-stream. The callback reply's bubble is the frontend's
    current one (the typed input's user_activity came before it), so it is
    sealed with its ``turn end agent_callback`` before the typed reply's first
    chunk, which then starts a bubble of its own. Without it the callback's
    bubble is left streaming, and its unfinished sentence and turn-end
    handling are lost."""
    session, mgr, notes = _wire_text_path(monkeypatch)

    async def on_text_delta(text, is_first, **_kw):
        mgr._current_ai_turn_text += text
        mgr.websocket.sent.append({"type": "gemini_response", "text": text})

    session.on_text_delta = on_text_delta
    callback_streaming, typed_began = asyncio.Event(), asyncio.Event()
    callback = []

    async def park_callback():
        callback_streaming.set()
        await typed_began.wait()

    async def typed_begins():
        typed_began.set()
        await asyncio.sleep(0)

    session.script = [
        [_text("任务查完了，"), park_callback, _text("结果是这样的。"), _text("", "stop")],
        [typed_begins, _text("好的。"), _text("", "stop")],
    ]

    async def focus_while_the_callback_streams(_text_):
        callback.append(asyncio.create_task(session.prompt_ephemeral("callback")))
        await asyncio.wait_for(callback_streaming.wait(), 5)
        return False

    mgr._focus_inline_decision = focus_while_the_callback_streams
    await M._process_stream_data_internal(
        mgr, {"input_type": "text", "data": "在吗", "request_id": "req-B"},
    )
    await asyncio.wait_for(callback[0], 5)
    for _ in range(10):
        await asyncio.sleep(0)

    assert notes == ["任务查完了，", "好的。"]
    assert _turn_ends(mgr) == [
        {"type": "system", "data": "turn end agent_callback"},
        {"type": "system", "data": "turn end", "request_id": "req-B"},
    ]
    relevant = [m for m in mgr.websocket.sent if m["type"] in ("system", "gemini_response")]
    assert relevant == [
        {"type": "gemini_response", "text": "任务查完了，"},
        {"type": "system", "data": "turn end agent_callback"},
        {"type": "gemini_response", "text": "好的。"},
        {"type": "system", "data": "turn end", "request_id": "req-B"},
    ]


async def test_a_completed_proactive_reply_ends_its_turn_on_both_channels():
    mgr = _make_manager()
    mgr.websocket = _FakeConnectedWebSocket()
    mgr._note_ai_turn = lambda text=None, **_kw: None
    mgr.tts_thread = None
    mgr._current_ai_turn_text = "回调说完了。"

    await M.handle_proactive_complete(mgr, True)

    assert _turn_ends(mgr) == [{"type": "system", "data": "turn end agent_callback"}]
    assert _system(mgr) == [{"type": "system", "data": "turn end agent_callback"}]
    assert mgr._current_ai_turn_text == ""
