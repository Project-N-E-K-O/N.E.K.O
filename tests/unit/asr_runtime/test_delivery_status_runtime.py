import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, call
import pytest
from main_logic.voice_turn.contracts import AsrFailureEvent, AsrLifecycleNotification, AsrStatusEvent
import main_logic.core.asr_runtime as core_asr_runtime_module

from tests.support.core_asr_harness import (
    _install_active_smart_turn,
    _install_ready_lifecycle,
    _start_and_seal_turn,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


async def test_status_delivery_failure_never_breaks_audio_runtime() -> None:
    runtime = _Runtime()
    runtime.send_status.side_effect = RuntimeError("socket closed")
    runtime._set_microphone_route("native")
    runtime.session.stream_audio = AsyncMock()
    identity = runtime._asr_runtime._capture_runtime_identity()

    await runtime._send_asr_status(
        "ASR_INDEPENDENT_READY",
        "glm",
        session_epoch=runtime._asr_session_epoch,
        expected_identity=identity,
    )
    await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    runtime.send_status.assert_awaited_once()
    runtime.session.stream_audio.assert_awaited_once()
    assert runtime._voice_input_pipeline_failed is False


async def test_stale_core_prepare_restores_previous_preview_owner() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    prepare_started = asyncio.Event()
    release_prepare = asyncio.Event()

    async def block_prepare(*, turn_id: str) -> None:
        del turn_id
        prepare_started.set()
        await release_prepare.wait()

    runtime.session.prepare_external_voice_turn = AsyncMock(side_effect=block_prepare)
    runtime.session.abandon_external_voice_turn = MagicMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    previous_token = replace(token, turn_id=token.turn_id + 100)
    previous_turn_id = (
        f"asr-{previous_token.ingress.session_epoch}-{previous_token.turn_id}"
    )
    runtime._core_asr_preview_turn_id = previous_turn_id
    runtime._core_asr_preview_turn_token = previous_token
    runtime._core_asr_preview_text = "previous partial"

    prepare_task = asyncio.create_task(runtime._prepare_core_voice_turn(token))
    await asyncio.wait_for(prepare_started.wait(), 1)
    runtime._voice_input_transition_generation += 1
    release_prepare.set()

    assert await asyncio.wait_for(prepare_task, 1) is False
    assert runtime._core_asr_preview_turn_id == previous_turn_id
    assert runtime._core_asr_preview_turn_token == previous_token
    assert runtime._core_asr_preview_text == "previous partial"
    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    )


async def test_native_audio_failure_log_is_rate_limited(monkeypatch) -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("native")
    runtime.session_closed_by_server = False
    runtime.last_audio_send_error_time = 0.0
    runtime.audio_error_log_interval = 2.0
    runtime.session.stream_audio = AsyncMock(side_effect=RuntimeError("send failed"))
    log_error = MagicMock()
    monkeypatch.setattr(core_asr_runtime_module.logger, "error", log_error)

    await runtime._route_microphone_audio(b"\x01\x00", sample_rate_hz=16_000)
    await runtime._route_microphone_audio(b"\x01\x00", sample_rate_hz=16_000)

    assert runtime.session.stream_audio.await_count == 2
    log_error.assert_called_once()


async def test_partial_preview_keeps_prepared_token_and_rejects_after_abort() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    on_partial = AsyncMock()
    runtime._asr_runtime._callbacks = replace(
        runtime._asr_runtime._callbacks,
        on_partial=on_partial,
    )
    await _install_active_smart_turn(runtime)
    epoch = runtime._asr_session_epoch
    captured_token = runtime._asr_runtime._asr_partial_turn_token
    assert captured_token is not None
    assert runtime._activate_asr_audio_dispatcher(
        runtime._asr_lifecycle,
        captured_token,
    )

    await runtime._send_independent_asr_preview("current", epoch)

    event = on_partial.await_args.args[0]
    assert event.turn_token is captured_token
    assert event.session_epoch == epoch
    on_partial.reset_mock()

    runtime._asr_audio_dispatcher.abort(captured_token)
    await runtime._send_independent_asr_preview("late", epoch)

    on_partial.assert_not_awaited()


async def test_injection_failure_is_reported_once_without_provider_body() -> None:
    runtime = _Runtime()
    runtime.session.create_response.side_effect = RuntimeError("sensitive response")
    await _start_and_seal_turn(runtime, "gemini")

    await runtime._handle_independent_asr_final(
        "hello",
        runtime._asr_session_epoch,
        "gemini",
    )
    await runtime._wait_asr_transcript_dispatch_idle()

    status_payloads = [call.args[0] for call in runtime.send_status.await_args_list]
    assert any("ASR_INDEPENDENT_INJECTION_FAILED" in item for item in status_payloads)
    assert "sensitive response" not in str(status_payloads)
    runtime.session.create_response.assert_awaited_once_with("hello")


@pytest.mark.parametrize("notification", ["status", "lifecycle", "failure"])
async def test_notification_waiting_on_lock_drops_same_epoch_stale_identity(
    notification: str,
) -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    current_epoch = runtime._asr_session_epoch
    await runtime._asr_notification_lock.acquire()
    if notification == "status":
        event = AsrStatusEvent(
            code="ASR_OLD_READY",
            provider="old-provider",
            session_epoch=current_epoch,
        )
        delivery = asyncio.create_task(runtime._send_core_asr_status(event))
    elif notification == "lifecycle":
        event = AsrLifecycleNotification(
            state="local_listen",
            provider="old-provider",
            session_epoch=current_epoch,
        )
        delivery = asyncio.create_task(runtime._send_core_asr_lifecycle(event))
    else:
        event = AsrFailureEvent(
            code="ASR_INDEPENDENT_FAILED",
            provider="old-provider",
            session_epoch=current_epoch,
        )
        delivery = asyncio.create_task(runtime._handle_core_asr_failure(event))
    await asyncio.sleep(0)

    runtime._asr_audio_generation += 1
    runtime._asr_notification_lock.release()
    await asyncio.wait_for(delivery, 1)

    runtime.send_status.assert_not_awaited()
    assert runtime._asr_route_mode == "independent"


async def test_cancelled_lease_resync_send_retries_same_episode() -> None:
    runtime = _Runtime()
    assert runtime._begin_voice_input_connection("chat-window") is True
    send_started = asyncio.Event()
    release_send = asyncio.Event()

    async def block_send(_message: str) -> None:
        send_started.set()
        await release_send.wait()

    runtime.send_status = AsyncMock(side_effect=block_send)
    first = asyncio.create_task(runtime._maybe_signal_voice_lease_resync())
    await asyncio.wait_for(send_started.wait(), 1)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    assert runtime._voice_lease_resync_signal_state is None
    runtime.send_status = AsyncMock()
    await runtime._maybe_signal_voice_lease_resync()

    runtime.send_status.assert_awaited_once()
    assert runtime._voice_lease_resync_signal_state is not None


async def test_cancelled_blocked_text_notice_retries_same_episode() -> None:
    runtime = _Runtime()
    runtime.input_mode = "text"
    assert runtime._begin_voice_input_connection("chat-window") is True
    runtime._set_microphone_route("blocked")
    send_started = asyncio.Event()
    release_send = asyncio.Event()

    async def block_send(_message: str) -> None:
        send_started.set()
        await release_send.wait()

    runtime.send_status = AsyncMock(side_effect=block_send)
    first = asyncio.create_task(
        runtime._maybe_signal_blocked_text_mode_microphone()
    )
    await asyncio.wait_for(send_started.wait(), 1)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    assert runtime._blocked_text_mode_microphone_signal_state is None
    runtime.send_status = AsyncMock()
    await runtime._maybe_signal_blocked_text_mode_microphone()

    runtime.send_status.assert_awaited_once()
    assert runtime._blocked_text_mode_microphone_signal_state is not None
