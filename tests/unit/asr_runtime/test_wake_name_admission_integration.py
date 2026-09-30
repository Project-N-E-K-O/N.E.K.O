"""The integrated admission policy observes raw ASR text before correction."""

from unittest.mock import Mock

import pytest

from main_logic.core import asr_runtime
from main_logic.voice_turn.admission import AdmissionDecision, SpeechEvidence
from main_logic.voice_turn.contracts import VoiceTranscriptEvent
from tests.unit.asr_runtime.test_wake_name_transcript import (
    _final, _runtime, _status, _texts, _token,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


async def test_admission_sees_original_text_and_final_keeps_delivery_result(monkeypatch):
    runtime = _runtime()
    _status(runtime)
    token = _token(runtime, 1)
    assert await runtime._prepare_voice_input_turn(token)
    assess = Mock(wraps=asr_runtime.assess_transcript)
    monkeypatch.setattr(asr_runtime, "assess_transcript", assess)
    event = VoiceTranscriptEvent(token, "qwen", "友谊，打开灯。")
    assert await runtime._dispatch_voice_input_final(event) is True
    assess.assert_called_once_with(
        "友谊，打开灯。", None, is_voice_source=True, final=True,
    )
    assert _texts(runtime) == ["悠怡，打开灯。"]
    runtime.session.create_response.assert_awaited_once_with("悠怡，打开灯。")
    assert await runtime._dispatch_voice_input_final(event) is False


async def test_rejected_final_consumes_wake_correction_without_model_submission():
    runtime = _runtime()
    _status(runtime)
    token = _token(runtime, 1)
    assert await runtime._prepare_voice_input_turn(token)
    evidence = SpeechEvidence(
        scope_id="wake-integration", candidate_id=1,
        audio_start_sample=0, audio_end_sample=512,
        observed_samples=512, voiced_samples=0, longest_speech_run_samples=0,
        longest_gap_samples=512, probability_mean=0.1, probability_peak=0.1,
        decision=AdmissionDecision.REJECT, reason="insufficient_speech",
    )
    await runtime._dispatch_voice_input_final(
        VoiceTranscriptEvent(token, "qwen", "嗯。", evidence=evidence)
    )
    assert _texts(runtime) == []
    runtime.session.create_response.assert_not_awaited()
    following = _token(runtime, 2)
    assert await runtime._prepare_voice_input_turn(following)
    await _final(runtime, following)
    assert _texts(runtime) == ["呦呦呦。"]
