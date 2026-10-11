"""An independent-ASR voice turn holds the owed wrap-up until it ends.

The voice counterpart of the typed-input hold (``_with_owed_wrap_up_held``):
speech onset interrupts the offline reply (``_prepare_core_voice_turn``), and
the wrap-up that reply owes (renewal check, final swap, queued agent
callbacks) must not run while the user is still speaking. It is paid by the
voice turn's own reply completion, or settled once the turn ends without one
(``_abandon_core_voice_turn``), whichever way it ends.

Real ``OmniOfflineClient`` over a scripted provider, the real voice-input
registry and Core chat consumer, and the real core prepare / dispatch /
cancel paths on the manager double from test_offline_reply_ownership.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.asr_client.lifecycle import VoiceTurnToken
from main_logic.core._shared import _ReplyTurn
from main_logic.voice_turn.contracts import VoiceTranscriptEvent
from tests.unit.test_core_game_route_memory_contract import _FakeConnectedWebSocket
from tests.unit.test_offline_reply_ownership import M, _dialog, _drain, _manager
from tests.unit.test_offline_turn_cancellation_e2e import _client, _text

pytestmark = pytest.mark.unit

_WRAP_UP = ["final_swap", "agent_callbacks"]


@pytest.fixture(autouse=True)
def _no_token_tracker(monkeypatch):
    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", MagicMock())


def _voice_ready(mgr, client):
    """Core owns an open independent-ASR voice lease on ``client``."""
    mgr._init_asr_runtime_state()
    mgr._voice_lease_owner = "core"
    mgr._voice_lease_synchronized = True
    mgr._voice_input_suppressed = False
    mgr._voice_input_suppression_reasons = set()
    mgr.handle_new_message = AsyncMock()
    mgr.handle_input_transcript = AsyncMock(return_value=True)
    mgr.send_status = AsyncMock()
    log = []
    real_finalize = mgr._finalize_turn_after_emit

    async def finalize():
        log.append(len(client.requests))
        await real_finalize()

    mgr._finalize_turn_after_emit = finalize
    return log


def _token(mgr, turn_id=1):
    return VoiceTurnToken(ingress=mgr._capture_ingress_token(), turn_id=turn_id)


def _turn_id(token):
    return f"asr-{token.ingress.session_epoch}-{token.turn_id}"


async def _settle(mgr):
    await mgr._voice_input_registry.wait_idle()
    await asyncio.gather(*mgr._bg, return_exceptions=True)
    await _drain()
    await asyncio.gather(*mgr._bg, return_exceptions=True)


async def _onset_cuts_a_typed_reply(mgr, client, token, *, voice_reply=True):
    """A typed reply streams; speech onset interrupts it; its task ends."""
    park = asyncio.Event()

    async def parked():
        await park.wait()

    client.script = [[_text("说到一半"), parked, _text("late"), _text("", "stop")]]
    if voice_reply:
        client.script.append([_text("语音回复"), _text("", "stop")])
    t1 = asyncio.ensure_future(M._with_owed_wrap_up_held(mgr, client.stream_text("Q1")))
    await _drain()
    prepared = await mgr._prepare_voice_input_turn(token)
    park.set()
    await t1
    await _settle(mgr)
    return prepared


async def _final(mgr, token, text="你好"):
    await mgr._dispatch_voice_input_final(
        VoiceTranscriptEvent(turn_token=token, provider="qwen", text=text)
    )
    await _settle(mgr)


async def test_a_voice_onset_holds_the_debt_until_its_own_reply_pays_it():
    """The typed reply's task ends while the user is still speaking: nothing
    is paid then. The voice reply's own completion pays it, once, after its
    provider request (a final swap no longer starts before it)."""
    client = _client()
    mgr = _manager(client)
    log = _voice_ready(mgr, client)
    token = _token(mgr)
    assert await _onset_cuts_a_typed_reply(mgr, client, token) is True
    assert (log, mgr.wrap_ups) == ([], [])  # held while the user speaks
    assert mgr._turn_wrap_up_owed is True
    assert mgr._voice_turn_wrap_up_hold == _turn_id(token)

    await _final(mgr, token)
    assert log == [2]  # once, from the voice reply's completion
    assert mgr.wrap_ups == _WRAP_UP
    assert mgr._turn_wrap_up_owed is False
    assert mgr._voice_turn_wrap_up_hold is None


async def test_the_hold_is_taken_before_the_interruption_awaits():
    """The interrupted reply's task can end while the interruption is still
    sending its ``turn abandoned``: the hold is already in place then."""
    client = _client()
    mgr = _manager(client)
    log = _voice_ready(mgr, client)
    token = _token(mgr)
    mgr._active_text_request_id = "req-1"
    park = asyncio.Event()

    async def parked():
        await park.wait()

    client.script = [
        [_text("说到一半"), parked, _text("late"), _text("", "stop")],
        [_text("语音回复"), _text("", "stop")],
    ]
    # A typed reply, bound to its request: its close sends ``turn abandoned``.
    t1 = asyncio.ensure_future(client.stream_text(
        "Q1", reply_owner=_ReplyTurn(speech_id=None, request_id="req-1"),
    ))
    await _drain()

    class _Socket(_FakeConnectedWebSocket):
        async def send_json(self, payload):
            await super().send_json(payload)
            if payload.get("data") == "turn abandoned":
                park.set()
                await t1
                await _settle(mgr)

    mgr.websocket = _Socket()
    assert await mgr._prepare_voice_input_turn(token) is True
    assert t1.done()
    assert (log, mgr.wrap_ups) == ([], [])
    await _final(mgr, token)
    assert log == [2]
    assert mgr.wrap_ups == _WRAP_UP


async def _empty_final(mgr, token):
    await _final(mgr, token, text="   ")


async def _echo_suppressed(mgr, token):
    mgr.handle_input_transcript = AsyncMock(return_value=False)
    await _final(mgr, token)


async def _route_left_core(mgr, token):
    mgr._voice_lease_owner = "game"
    await _final(mgr, token)


async def _asr_turn_abandoned(mgr, token):
    await mgr._handle_core_asr_turn_abandoned(token)
    await _settle(mgr)


async def _asr_failure(mgr, token):
    mgr._voice_input_registry.invalidate_utterance(reason="independent_asr_failure")
    await _settle(mgr)


async def _consumer_switched(mgr, token):
    mgr._voice_input_registry.activate(mgr._game_voice_input_registration.handle)
    await _settle(mgr)


async def _submit_cancelled(mgr, token):
    mgr.session.submit_external_voice_turn = AsyncMock(return_value=False)
    await _final(mgr, token)


async def _swap_barrier_timed_out(mgr, token):
    mgr._core_voice_session_swap_barrier_timeout_s = 0.01
    await mgr._core_voice_session_swap_lock.acquire()
    try:
        await _final(mgr, token)
    finally:
        mgr._core_voice_session_swap_lock.release()


@pytest.mark.parametrize("end", [
    _empty_final, _echo_suppressed, _route_left_core, _asr_turn_abandoned,
    _asr_failure, _consumer_switched, _submit_cancelled, _swap_barrier_timed_out,
])
async def test_a_voice_turn_that_ends_without_a_reply_releases_the_hold_and_pays(end):
    client = _client()
    mgr = _manager(client)
    log = _voice_ready(mgr, client)
    token = _token(mgr)
    assert await _onset_cuts_a_typed_reply(mgr, client, token, voice_reply=False)
    assert mgr.wrap_ups == []
    await end(mgr, token)
    assert log == [1]  # exactly once, when the turn ended
    assert mgr.wrap_ups == _WRAP_UP
    assert (mgr._turn_wrap_up_owed, mgr._voice_turn_wrap_up_hold) == (False, None)


async def test_a_rejected_prepare_releases_its_hold():
    """handle_new_message fails after the interruption: the registry's
    cancellation of that attempt releases the hold, and the reply's task end
    pays the debt, once."""
    client = _client()
    mgr = _manager(client)
    log = _voice_ready(mgr, client)
    mgr.handle_new_message = AsyncMock(side_effect=RuntimeError("rejected"))
    token = _token(mgr)
    assert await _onset_cuts_a_typed_reply(mgr, client, token, voice_reply=False) is False
    assert log == [1]
    assert mgr.wrap_ups == _WRAP_UP
    assert mgr._voice_turn_wrap_up_hold is None


@pytest.mark.parametrize("torn_down", [False, True])
async def test_a_cancelled_dispatch_releases_the_hold_and_settles(torn_down):
    """A cancelled final releases the hold and still settles: the transcript
    worker is cancelled on ASR errors, detaches and transport aborts too,
    with the offline session alive and idle, so nothing else would pay. A
    torn-down session pays nothing (its debt is reset)."""
    client = _client()
    mgr = _manager(client)
    log = _voice_ready(mgr, client)
    token = _token(mgr)
    assert await _onset_cuts_a_typed_reply(mgr, client, token, voice_reply=False)
    entered = asyncio.Event()

    async def stuck(*_a, **_kw):
        entered.set()
        await asyncio.Event().wait()

    mgr.handle_input_transcript = AsyncMock(side_effect=stuck)
    # The shape of TranscriptDispatcher.invalidate_all() cancelling its worker.
    dispatch = asyncio.ensure_future(_final(mgr, token))
    await asyncio.wait_for(entered.wait(), 1)
    if torn_down:
        mgr.is_active = False
    dispatch.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dispatch
    for _ in range(3):
        await _settle(mgr)
    assert mgr._voice_turn_wrap_up_hold is None
    assert mgr.session is client and client.is_idle()
    assert log == ([] if torn_down else [1]), "finalized once, by the settle"
    if torn_down:
        assert mgr.wrap_ups == []
        assert mgr._turn_wrap_up_owed is True
    else:
        assert mgr.wrap_ups == _WRAP_UP
        assert mgr._turn_wrap_up_owed is False


async def test_a_newer_voice_turn_takes_the_hold_over():
    """Turn 2 starts before turn 1 ends: turn 1's end leaves turn 2's hold
    alone; turn 2's end pays."""
    client = _client()
    mgr = _manager(client)
    log = _voice_ready(mgr, client)
    first, second = _token(mgr, 1), _token(mgr, 2)
    assert await _onset_cuts_a_typed_reply(mgr, client, first, voice_reply=False)
    assert await mgr._prepare_voice_input_turn(second) is True
    assert mgr._voice_turn_wrap_up_hold == _turn_id(second)
    await _empty_final(mgr, first)
    assert (log, mgr._voice_turn_wrap_up_hold) == ([], _turn_id(second))
    await _empty_final(mgr, second)
    assert log == [1]
    assert mgr.wrap_ups == _WRAP_UP


async def test_a_hold_whose_turn_never_ends_is_replaced_by_the_next_turn():
    """Turn 1 never gets a final nor a cancellation. The next onset takes
    the hold over, and its end pays."""
    client = _client()
    mgr = _manager(client)
    log = _voice_ready(mgr, client)
    leaked, second = _token(mgr, 1), _token(mgr, 2)
    assert await _onset_cuts_a_typed_reply(mgr, client, leaked, voice_reply=False)
    assert await mgr._prepare_voice_input_turn(second) is True
    await _empty_final(mgr, second)
    assert log == [1]
    assert mgr._voice_turn_wrap_up_hold is None


async def test_a_hold_whose_turn_record_is_gone_does_not_block_the_debt():
    client = _client()
    mgr = _manager(client)
    _voice_ready(mgr, client)
    mgr._turn_wrap_up_owed = True
    mgr._voice_turn_wrap_up_hold = "asr-1-9"  # released by no path
    await mgr._settle_owed_turn_wrap_up()
    await _settle(mgr)
    assert mgr.wrap_ups == _WRAP_UP
    assert mgr._voice_turn_wrap_up_hold is None


async def test_abandoning_every_voice_turn_releases_any_hold_and_pays():
    """``_abandon_core_voice_turn()`` without an id ends every turn (it drops
    every record), so it releases whichever turn holds the debt."""
    client = _client()
    mgr = _manager(client)
    log = _voice_ready(mgr, client)
    token = _token(mgr)
    assert await _onset_cuts_a_typed_reply(mgr, client, token, voice_reply=False)
    assert (log, mgr._voice_turn_wrap_up_hold) == ([], _turn_id(token))
    mgr._abandon_core_voice_turn()
    await _settle(mgr)
    assert log == [1]
    assert mgr.wrap_ups == _WRAP_UP
    assert (mgr._turn_wrap_up_owed, mgr._voice_turn_wrap_up_hold) == (False, None)


def test_releasing_outside_a_running_loop_never_blocks_the_turn_cleanup():
    """``_abandon_core_voice_turn`` is sync and releases the hold first: with
    no running loop to settle in, the release stands down instead of raising,
    and the turn's record and response pause are still released."""
    client = _client()
    mgr = _manager(client)
    mgr._init_asr_runtime_state()
    record = SimpleNamespace(invalidated=asyncio.Event())
    mgr._core_multimodal_turns["asr-1-1"] = record
    mgr._voice_turn_wrap_up_hold = "asr-1-1"
    mgr._turn_wrap_up_owed = True
    session = SimpleNamespace(abandon_external_voice_turn=MagicMock())
    mgr._abandon_core_voice_turn("asr-1-1", session_ref=session)
    assert mgr._voice_turn_wrap_up_hold is None
    assert "asr-1-1" not in mgr._core_multimodal_turns
    assert record.invalidated.is_set()
    session.abandon_external_voice_turn.assert_called_once_with("asr-1-1")
    assert mgr._turn_wrap_up_owed is True  # paid by the next finalize


async def test_a_session_reset_clears_the_hold():
    client = _client()
    mgr = _manager(client)
    _voice_ready(mgr, client)
    mgr._voice_turn_wrap_up_hold = "asr-1-1"
    mgr._reset_preparation_state = AsyncMock()
    mgr._cleanup_pending_session_resources = AsyncMock()
    mgr.state = SimpleNamespace(reset=AsyncMock())
    mgr._focus_scorer = MagicMock()
    mgr._master_emotion = MagicMock()
    await M._init_renew_status(mgr)
    assert mgr._voice_turn_wrap_up_hold is None


async def test_a_voice_onset_cutting_a_voice_reply_owes_its_wrap_up():
    """Speech onset cuts the previous voice turn's reply, which streams in
    the independent-ASR child task the interruption cancels. The reply is
    taken over before that cancel: its completion (turn end, wrap-up) does
    not run inside the interruption while the user is speaking, the
    interrupter closes it, the shown half lands in its own turn, and the
    new turn's reply pays the wrap-up once."""
    client = _client()
    mgr = _manager(client)
    log = _voice_ready(mgr, client)
    statuses = []
    client.on_status_message = AsyncMock(side_effect=statuses.append)

    async def on_text_delta(text, _is_first, **_kw):
        mgr._current_ai_turn_text += text

    client.on_text_delta = on_text_delta
    streaming, park = asyncio.Event(), asyncio.Event()

    async def parked():
        streaming.set()
        await park.wait()

    client.script = [
        [_text("说到一半，"), parked, _text("后半句。"), _text("", "stop")],
        [_text("第二句回复。"), _text("", "stop")],
    ]
    first, second = _token(mgr, 1), _token(mgr, 2)
    assert await mgr._prepare_voice_input_turn(first) is True
    reply = asyncio.ensure_future(_final(mgr, first, text="问题一"))
    await asyncio.wait_for(streaming.wait(), 2)

    assert await mgr._prepare_voice_input_turn(second) is True
    assert (log, mgr.wrap_ups) == ([], [])  # held while the user speaks
    assert mgr._turn_wrap_up_owed is True
    assert mgr.sync_message_queue.messages[-1:] == [{"type": "system", "data": "turn end"}]
    await reply
    assert _dialog(client) == [("human", "问题一"), ("ai", "说到一半，")]
    assert (log, mgr._voice_turn_wrap_up_hold) == ([], _turn_id(second))

    await _final(mgr, second, text="问题二")
    assert log == [2]
    assert mgr.wrap_ups == _WRAP_UP
    assert [m for m in mgr.sync_message_queue.messages if m.get("data") == "turn end"] == [
        {"type": "system", "data": "turn end"},
        {"type": "system", "data": "turn end"},
    ]
    assert _dialog(client) == [
        ("human", "问题一"), ("ai", "说到一半，"), ("human", "问题二"), ("ai", "第二句回复。"),
    ]
    assert statuses == []
