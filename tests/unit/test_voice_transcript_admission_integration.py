from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.voice_turn.admission import CandidateAdmission


@pytest.mark.asyncio
async def test_final_noise_rejection_cleans_preview_without_injection_or_reply():
    from tests.unit.test_audio_stream_queue import (
        _make_transcript_dispatch_manager,
        _transcript_event,
    )

    manager = _make_transcript_dispatch_manager()
    weak = CandidateAdmission("test").observe(0, 512, 0.9)
    event = replace(_transcript_event(manager, "嗯。"), evidence=weak)
    await manager._dispatch_core_asr_transcript(event)
    manager.handle_input_transcript.assert_not_awaited()
    manager.session.submit_external_voice_turn.assert_not_awaited()
    manager.session.create_response.assert_not_awaited()
    manager.websocket.send_json.assert_awaited_once()
    assert (
        manager.websocket.send_json.await_args.args[0]["type"]
        == "user_transcript_preview"
    )


@pytest.mark.asyncio
async def test_rejected_final_does_not_clear_successor_preview():
    from tests.unit.test_audio_stream_queue import (
        _make_transcript_dispatch_manager,
        _transcript_event,
    )

    manager = _make_transcript_dispatch_manager()
    event = replace(
        _transcript_event(manager, "嗯。", turn_id=7),
        evidence=CandidateAdmission("old").observe(0, 512, 0.9),
    )
    manager._core_asr_preview_turn_id = (
        f"asr-{event.turn_token.ingress.session_epoch}-8"
    )
    manager._core_asr_preview_text = "next sentence"
    await manager._dispatch_core_asr_transcript(event)
    assert manager._core_asr_preview_text == "next sentence"
    assert (
        manager.websocket.send_json.await_args.args[0]["asr_turn_id"]
        == f"asr-{event.turn_token.ingress.session_epoch}-7"
    )
    manager.handle_input_transcript.assert_not_awaited()
    manager.session.submit_external_voice_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_final_guard_precedes_user_activity_and_takeover():
    from main_logic.core.turn import TurnMixin

    manager = SimpleNamespace(
        last_user_activity_time=123, _takeover_input_dispatcher=AsyncMock()
    )
    weak = CandidateAdmission("test").observe(0, 512, 0.9)
    result = await TurnMixin.handle_input_transcript(
        manager, "嗯。", metadata={"speech_evidence": weak}
    )
    assert result is False
    assert manager.last_user_activity_time == 123
    manager._takeover_input_dispatcher.assert_not_awaited()
