from unittest.mock import AsyncMock, MagicMock
import pytest
from main_logic.voice_input import VoiceInputDispatchResult
from main_logic.voice_turn.contracts import VoiceTranscriptEvent
import main_logic.core.asr_runtime as core_asr_runtime_module

from tests.support.core_asr_harness import (
    _install_ready_lifecycle,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


async def test_game_consumer_ignores_empty_final(monkeypatch) -> None:
    runtime = _Runtime()
    route_transcript = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.get_active_game_route_identity",
        lambda _name: ("game", "session-a"),
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.route_external_voice_transcript",
        route_transcript,
    )
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    _install_ready_lifecycle(runtime, "qwen")
    lifecycle = runtime._asr_lifecycle
    assert lifecycle is not None
    event = VoiceTranscriptEvent(
        turn_token=runtime._asr_runtime._capture_turn_token(lifecycle),
        provider="qwen",
        text="",
    )

    assert await runtime._prepare_voice_input_turn(event.turn_token) is True
    await runtime._dispatch_voice_input_final(event)
    await runtime._voice_input_registry.wait_idle()

    route_transcript.assert_not_awaited()
    runtime.handle_new_message.assert_not_awaited()
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()


async def test_rejected_voice_input_final_is_observable(monkeypatch) -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    event = VoiceTranscriptEvent(
        turn_token=runtime._asr_runtime._capture_turn_token(
            runtime._asr_lifecycle
        ),
        provider="qwen",
        text="hello",
    )
    runtime._voice_input_registry.dispatch_final = AsyncMock(
        return_value=VoiceInputDispatchResult.REJECTED
    )
    debug = MagicMock()
    monkeypatch.setattr(core_asr_runtime_module.logger, "debug", debug)

    await runtime._dispatch_voice_input_final(event)

    debug.assert_called_once()
    assert "voice input final rejected" in debug.call_args.args[0]
