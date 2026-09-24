import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from main_logic.voice_input.consumers import CoreChatTurnContext
from main_logic.asr_client.lifecycle import VoiceTurnToken
from main_logic.voice_turn.contracts import AsrFailureEvent, VoiceTranscriptEvent

from tests.support.core_asr_harness import (
    _install_ready_lifecycle,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


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
