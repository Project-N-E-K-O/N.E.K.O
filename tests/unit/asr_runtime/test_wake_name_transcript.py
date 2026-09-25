"""Wake-name correction through the real voice input registry and Core consumer."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.voice_input.activation import ActivationDecision, ActivationState
from main_logic.voice_turn.contracts import (
    AsrFailureEvent,
    VoicePartialEvent,
    VoiceTranscriptEvent,
    VoiceTurnToken,
)
from tests.support.asr_fakes import _Runtime
from tests.support.core_asr_harness import _install_ready_lifecycle

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


def _runtime() -> _Runtime:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    runtime._voice_session_activation_runtime = object()
    runtime._send_voice_session_activation_status = AsyncMock()
    runtime.websocket = SimpleNamespace(send_json=AsyncMock())
    return runtime


def _status(runtime, reason="wake_word_detected", state=ActivationState.REPLAYING):
    runtime._on_voice_session_activation_status(
        runtime._capture_voice_session_activation_generation(),
        ActivationDecision(state=state, reason=reason),
    )


def _token(runtime, turn_id):
    return VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=turn_id)


async def _final(runtime, token, text="呦呦呦。"):
    await runtime._dispatch_voice_input_final(
        VoiceTranscriptEvent(turn_token=token, provider="qwen", text=text)
    )
    await runtime._voice_input_registry.wait_idle()


def _texts(runtime):
    return [call.args[0] for call in runtime.handle_input_transcript.await_args_list]


async def test_wake_corrects_final_for_history_and_model_but_preserves_preview():
    runtime = _runtime()
    _status(runtime)
    # ASR preparation can legitimately begin after replay has completed.
    _status(runtime, "replay_handed_off", ActivationState.ACTIVE)
    token = _token(runtime, 1)
    assert await runtime._prepare_voice_input_turn(token)
    await runtime._dispatch_voice_input_partial(
        VoicePartialEvent(turn_token=token, text="呦呦呦。")
    )
    assert any(
        call.args[0].get("type") == "user_transcript_preview"
        and call.args[0]["text"] == "呦呦呦。"
        for call in runtime.websocket.send_json.await_args_list
    )
    await _final(runtime, token)
    assert _texts(runtime) == ["悠怡悠怡。"]
    runtime.session.create_response.assert_awaited_once_with("悠怡悠怡。")


@pytest.mark.parametrize("reason", [None, "owner_confirmed"])
async def test_regular_or_voiceprint_activation_does_not_correct(reason):
    runtime = _runtime()
    if reason:
        _status(runtime, reason)
    token = _token(runtime, 1)
    assert await runtime._prepare_voice_input_turn(token)
    await _final(runtime, token)
    assert _texts(runtime) == ["呦呦呦。"]


@pytest.mark.parametrize("first_text", ["", "今天怎么样？", "我刚才喊了悠宜。", "悠宜。"])
async def test_first_final_consumes_opportunity_even_when_empty_or_unmatched(first_text):
    runtime = _runtime()
    _status(runtime)
    first = _token(runtime, 1)
    assert await runtime._prepare_voice_input_turn(first)
    await _final(runtime, first, first_text)
    second = _token(runtime, 2)
    assert await runtime._prepare_voice_input_turn(second)
    await _final(runtime, second)
    expected = [] if not first_text else ["悠怡。" if first_text == "悠宜。" else first_text]
    assert _texts(runtime) == expected + ["呦呦呦。"]


async def test_out_of_order_finals_only_correct_first_prepared_turn():
    runtime = _runtime()
    _status(runtime)
    first, second = _token(runtime, 1), _token(runtime, 2)
    assert await runtime._prepare_voice_input_turn(first)
    assert await runtime._prepare_voice_input_turn(second)
    await _final(runtime, second)
    await _final(runtime, first)
    await _final(runtime, first)
    assert _texts(runtime) == ["呦呦呦。", "悠怡悠怡。"]


async def test_first_prepare_reserves_correction_before_awaiting_session():
    runtime = _runtime()
    _status(runtime)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def prepare_message(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()

    runtime.handle_new_message.side_effect = prepare_message
    first, second = _token(runtime, 1), _token(runtime, 2)
    pending = asyncio.create_task(runtime._prepare_voice_input_turn(first))
    await asyncio.wait_for(entered.wait(), 1)
    assert await runtime._prepare_voice_input_turn(second)
    await _final(runtime, second)
    release.set()
    assert await asyncio.wait_for(pending, 1)
    await _final(runtime, first)
    assert _texts(runtime) == ["呦呦呦。", "悠怡悠怡。"]


async def test_non_core_first_consumer_gets_raw_text_and_consumes_opportunity(monkeypatch):
    runtime = _runtime()
    registry = runtime._voice_input_registry
    route_transcript = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active", lambda _name: True,
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.get_active_game_route_identity",
        lambda _name: ("game", "session-a"),
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.route_external_voice_transcript",
        route_transcript,
    )
    runtime._voice_lease_owner = "game"
    registry.activate(runtime._game_voice_input_registration.handle)
    _status(runtime)
    first = _token(runtime, 1)
    assert await runtime._prepare_voice_input_turn(first)
    await _final(runtime, first)
    assert route_transcript.await_args.args[1] == "呦呦呦。"
    runtime._voice_lease_owner = "core"
    registry.activate(runtime._core_chat_voice_input_registration.handle)
    second = _token(runtime, 2)
    assert await runtime._prepare_voice_input_turn(second)
    await _final(runtime, second)
    assert _texts(runtime) == ["呦呦呦。"]


@pytest.mark.parametrize("failure", [RuntimeError("prepare failed"), asyncio.CancelledError()])
async def test_prepare_failure_or_cancellation_consumes_opportunity(failure):
    runtime = _runtime()
    _status(runtime)
    runtime.handle_new_message.side_effect = failure
    first = _token(runtime, 1)
    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await runtime._prepare_voice_input_turn(first)
    else:
        assert await runtime._prepare_voice_input_turn(first) is False
    await runtime._voice_input_registry.wait_idle()
    runtime.handle_new_message.side_effect = None
    second = _token(runtime, 2)
    assert await runtime._prepare_voice_input_turn(second)
    await _final(runtime, second)
    assert _texts(runtime) == ["呦呦呦。"]


async def test_abandoned_first_turn_does_not_transfer_opportunity():
    runtime = _runtime()
    _status(runtime)
    first = _token(runtime, 1)
    assert await runtime._prepare_voice_input_turn(first)
    await runtime._handle_core_asr_turn_abandoned(first)
    second = _token(runtime, 2)
    assert await runtime._prepare_voice_input_turn(second)
    await _final(runtime, second)
    assert _texts(runtime) == ["呦呦呦。"]


async def test_delayed_old_cancellation_preserves_new_wake_opportunity():
    runtime = _runtime()
    _status(runtime)
    first = _token(runtime, 1)
    assert await runtime._prepare_voice_input_turn(first)
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause_clear(*_args):
        entered.set()
        await release.wait()

    runtime._send_core_asr_preview_clear = AsyncMock(side_effect=pause_clear)
    runtime._voice_input_registry.invalidate_utterance(first, reason="test_cancel")
    await asyncio.wait_for(entered.wait(), 1)
    _status(runtime, "idle_timeout", ActivationState.WAITING)
    _status(runtime)
    second = _token(runtime, 2)
    assert await runtime._prepare_voice_input_turn(second)
    release.set()
    await runtime._voice_input_registry.wait_idle()
    await _final(runtime, second)
    assert _texts(runtime) == ["悠怡悠怡。"]


@pytest.mark.parametrize("change", ["standby", "session", "route", "voiceprint"])
async def test_changed_activation_identity_prevents_correction(change):
    runtime = _runtime()
    _status(runtime)
    token = _token(runtime, 1)
    assert await runtime._prepare_voice_input_turn(token)
    if change == "standby":
        _status(runtime, "idle_timeout", ActivationState.WAITING)
    elif change == "session":
        runtime.session = SimpleNamespace(create_response=AsyncMock())
    elif change == "route":
        runtime._microphone_route_generation += 1
    else:
        _status(runtime, "owner_confirmed")
    await _final(runtime, token)
    assert "悠怡悠怡。" not in _texts(runtime)


async def test_new_wake_does_not_lend_correction_to_old_final():
    runtime = _runtime()
    _status(runtime)
    first = _token(runtime, 1)
    assert await runtime._prepare_voice_input_turn(first)
    _status(runtime, "idle_timeout", ActivationState.WAITING)
    _status(runtime)
    second = _token(runtime, 2)
    assert await runtime._prepare_voice_input_turn(second)
    await _final(runtime, first)
    await _final(runtime, second)
    assert _texts(runtime) == ["呦呦呦。", "悠怡悠怡。"]


async def test_duplicate_prepare_does_not_erase_first_turn_correction():
    runtime = _runtime()
    _status(runtime)
    token = _token(runtime, 1)
    assert await runtime._prepare_voice_input_turn(token)
    assert await runtime._prepare_voice_input_turn(token) is False
    await _final(runtime, token)
    assert _texts(runtime) == ["悠怡悠怡。"]


async def test_retried_failed_prepare_with_same_token_does_not_rearm():
    runtime = _runtime()
    _status(runtime)
    token = _token(runtime, 1)
    runtime.handle_new_message.side_effect = RuntimeError("prepare failed")
    assert await runtime._prepare_voice_input_turn(token) is False
    runtime.handle_new_message.side_effect = None
    assert await runtime._prepare_voice_input_turn(token)
    await _final(runtime, token)
    assert _texts(runtime) == ["呦呦呦。"]


async def test_transport_reconnect_preserves_unbound_wake_identity():
    runtime = _runtime()
    _status(runtime)
    generation = runtime._capture_voice_session_activation_generation()
    runtime._asr_lifecycle.invalidate_transport()
    assert runtime._capture_voice_session_activation_generation() == generation
    token = _token(runtime, 1)
    assert await runtime._prepare_voice_input_turn(token)
    await _final(runtime, token)
    assert _texts(runtime) == ["悠怡悠怡。"]


async def test_delayed_final_keeps_bound_correction(monkeypatch):
    runtime = _runtime()
    _status(runtime)
    token = _token(runtime, 1)
    assert await runtime._prepare_voice_input_turn(token)
    # Simulate a final arriving long after the old proposed five-second window.
    monkeypatch.setattr(
        "main_logic.core.asr_runtime.time", SimpleNamespace(monotonic=lambda: 1e12),
    )
    await _final(runtime, token)
    assert _texts(runtime) == ["悠怡悠怡。"]


async def test_asr_failure_clears_unbound_wake_before_recovery():
    runtime = _runtime()
    runtime._voice_session_activation_runtime = SimpleNamespace(close=AsyncMock())
    _status(runtime)
    await runtime._handle_core_asr_failure(
        AsrFailureEvent(
            code="ASR_INDEPENDENT_FAILED", provider="qwen",
            session_epoch=runtime._asr_session_epoch,
        )
    )
    _install_ready_lifecycle(runtime, "qwen")
    runtime._voice_session_activation_runtime = object()
    _status(runtime, "replay_handed_off", ActivationState.ACTIVE)
    token = _token(runtime, 2)
    assert await runtime._prepare_voice_input_turn(token)
    await _final(runtime, token)
    assert _texts(runtime) == ["呦呦呦。"]
