import asyncio
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace
import pytest
from main_logic.voice_input.consumers import CoreChatTurnContext
from main_logic.asr_client.lifecycle import VoiceTurnToken
from main_logic.voice_turn.contracts import AsrFailureEvent, VoiceTranscriptEvent
from main_logic.omni_realtime_client._response_arbiter import RealtimeResponseArbiter

from tests.support.core_asr_harness import (
    _install_ready_lifecycle,
    _install_active_smart_turn,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


@pytest.mark.parametrize("failure", ["error", "cancel"])
@pytest.mark.parametrize("replace_probe", [False, True])
async def test_failed_preparation_releases_only_its_own_pause_probe(failure, replace_probe):
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    arbiter = SimpleNamespace(pause_owner_alive=None)
    runtime.session._response_arbiter = arbiter
    newer_probe = lambda owner: True

    async def prepare(**kwargs):
        assert callable(arbiter.pause_owner_alive)
        if replace_probe:
            arbiter.pause_owner_alive = newer_probe
        if failure == "cancel":
            raise asyncio.CancelledError
        raise RuntimeError("prepare failed")

    runtime.session.prepare_external_voice_turn = prepare
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await runtime._prepare_core_voice_turn(token)
    else:
        assert await runtime._prepare_core_voice_turn(token) is False
    assert arbiter.pause_owner_alive is (newer_probe if replace_probe else None)


async def test_pause_owner_probe_is_fenced_to_active_asr_runtime_and_turn():
    runtime = _Runtime()
    arbiter = SimpleNamespace()
    runtime.session._response_arbiter = arbiter
    runtime.session.prepare_external_voice_turn = AsyncMock(return_value=False)
    await _install_active_smart_turn(runtime)
    owner = runtime.session.prepare_external_voice_turn.await_args.kwargs["turn_id"]
    assert arbiter.pause_owner_alive(owner)
    assert not arbiter.pause_owner_alive("another turn")
    component = runtime._asr_runtime
    component._asr_audio_generation += 1
    assert not arbiter.pause_owner_alive(owner)
    component._asr_audio_generation -= 1
    assert arbiter.pause_owner_alive(owner)
    component._asr_turn_prepared = False
    assert not arbiter.pause_owner_alive(owner)


@pytest.mark.parametrize("probe_kind", ["owned", "newer", "connection"])
async def test_successful_turn_releases_only_matching_pause_probe(probe_kind):
    runtime = _Runtime()
    arbiter = RealtimeResponseArbiter(AsyncMock())
    runtime.session._response_arbiter = arbiter
    runtime.session.prepare_external_voice_turn = AsyncMock(return_value=False)
    await _install_active_smart_turn(runtime)
    owner = runtime.session.prepare_external_voice_turn.await_args.kwargs["turn_id"]
    probe = arbiter.pause_owner_alive
    if probe_kind != "owned":
        probe = lambda owner: True
        if probe_kind == "newer":
            probe.pause_owner = "newer turn"
        arbiter.pause_owner_alive = probe
    arbiter._ensure_worker = lambda: None
    arbiter.pause_dispatch(owner)
    arbiter.resume_dispatch()
    assert arbiter.pause_owner_alive is (None if probe_kind == "owned" else probe)


async def test_prepare_failure_releases_keyed_external_turn_pause() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.prepare_external_voice_turn = AsyncMock(
        side_effect=RuntimeError("prepare failed")
    )
    runtime.session.abandon_external_voice_turn = MagicMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)

    assert await runtime._prepare_core_voice_turn(token) is False

    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    )


async def test_registry_prepare_rejection_releases_keyed_external_turn_pause() -> (
    None
):
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.abandon_external_voice_turn = MagicMock()
    runtime.handle_new_message = AsyncMock(side_effect=RuntimeError("history failed"))
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)

    assert await runtime._prepare_voice_input_turn(token) is False

    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    )


async def test_registry_cancelled_prepare_releases_keyed_external_turn_pause() -> (
    None
):
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.abandon_external_voice_turn = MagicMock()
    runtime.handle_new_message = AsyncMock(side_effect=asyncio.CancelledError)
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)

    with pytest.raises(asyncio.CancelledError):
        await runtime._prepare_voice_input_turn(token)
    await runtime._voice_input_registry.wait_idle()

    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    )


async def test_transcript_dispatch_failure_releases_keyed_external_turn_pause() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.abandon_external_voice_turn = MagicMock()
    runtime.handle_input_transcript.side_effect = RuntimeError("history failed")
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    event = VoiceTranscriptEvent(
        turn_token=token,
        provider="qwen",
        text="hello",
    )

    with pytest.raises(RuntimeError, match="history failed"):
        await runtime._dispatch_core_asr_transcript(event)

    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    )


async def test_cancelled_preview_clear_still_releases_keyed_external_turn_pause() -> None:
    runtime = _Runtime()
    session = runtime.session
    session.abandon_external_voice_turn = MagicMock()
    runtime._send_core_asr_preview_clear = AsyncMock(
        side_effect=asyncio.CancelledError
    )
    token = VoiceTurnToken(ingress=runtime._capture_ingress_token(), turn_id=7)
    context = CoreChatTurnContext(
        token=token,
        external_turn_id="asr-cancelled-preview",
        session_ref=session,
    )

    with pytest.raises(asyncio.CancelledError):
        await runtime._cancel_core_chat_voice_turn(context, "takeover")

    session.abandon_external_voice_turn.assert_called_once_with(
        "asr-cancelled-preview"
    )


@pytest.mark.parametrize("stale_guard", ["ingress", "owner"])
async def test_stale_final_guard_releases_keyed_external_turn_pause(
    stale_guard: str,
) -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime)
    runtime.session.abandon_external_voice_turn = MagicMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    event = VoiceTranscriptEvent(
        turn_token=token,
        provider="qwen",
        text="hello",
    )
    if stale_guard == "ingress":
        runtime._asr_audio_generation += 1
    else:
        runtime._voice_lease_owner = "game"

    await runtime._dispatch_core_asr_transcript(event)

    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}"
    )


@pytest.mark.parametrize("operation", ["abort", "close"])
async def test_core_asr_teardown_force_releases_external_turn_pause(
    operation: str,
) -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    runtime.session.abandon_external_voice_turn = MagicMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    assert await runtime._prepare_voice_input_turn(token) is True

    if operation == "abort":
        runtime._asr_runtime.abort = AsyncMock()
        await runtime._abort_independent_asr("test_abort")
        runtime._asr_runtime.abort.assert_awaited_once_with("test_abort")
    else:
        runtime._asr_runtime.close = AsyncMock()
        await runtime._close_independent_asr(next_route_mode="blocked")
        runtime._asr_runtime.close.assert_awaited_once_with()

    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}",
    )


async def test_current_asr_failure_force_releases_external_turn_pause() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    runtime.session.abandon_external_voice_turn = MagicMock()
    token = runtime._asr_runtime._capture_turn_token(runtime._asr_lifecycle)
    assert await runtime._prepare_voice_input_turn(token) is True

    await runtime._handle_core_asr_failure(
        AsrFailureEvent(
            code="ASR_INDEPENDENT_FAILED",
            provider="qwen",
            session_epoch=runtime._asr_session_epoch,
        )
    )

    runtime.session.abandon_external_voice_turn.assert_called_once_with(
        f"asr-{token.ingress.session_epoch}-{token.turn_id}",
    )
