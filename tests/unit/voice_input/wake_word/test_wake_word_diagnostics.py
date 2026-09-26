import logging

import pytest

from main_logic.voice_input.wake_word.diagnostics import WakeWordDiagnostics
from main_logic.voice_input.activation import ActivationState, WakeWordDetection
from tests.unit.voice_identity_service.test_wake_word_runtime import Detector, frame, runtime, settle


def test_diagnostics_are_opt_in(capsys, monkeypatch):
    monkeypatch.delenv("NEKO_WAKE_WORD_DIAGNOSTICS", raising=False)
    diagnostic = WakeWordDiagnostics()
    diagnostic.emit("ready")
    diagnostic.record(None, 1, None, restarted=True, inference_ms=1)
    assert not capsys.readouterr().out


def test_progress_is_bounded_and_hits_do_not_expose_keyword_or_pcm(capsys, monkeypatch):
    monkeypatch.setenv("NEKO_WAKE_WORD_DIAGNOSTICS", "1")
    diagnostic = WakeWordDiagnostics()
    audio = frame(1)
    hit = WakeWordDetection("private-name", audio.generation, 1, audio.sample_start, audio.sample_end)
    for index in range(55):
        diagnostic.record(audio, 1, hit if index == 2 else None,
                          restarted=index == 0, inference_ms=2)
    output = capsys.readouterr().out
    assert output.count("event=progress") == 2
    assert output.count("event=hit") == 1
    assert "resets=1" in output and "hits=1" in output
    assert "private-name" not in output and "pcm=" not in output
    assert "nonzero_ratio=1.0" in output


def test_broken_diagnostic_output_is_nonfatal(monkeypatch):
    monkeypatch.setenv("NEKO_WAKE_WORD_DIAGNOSTICS", "1")
    diagnostic = WakeWordDiagnostics()

    def broken(*args, **kwargs):
        raise OSError("closed stdout")

    monkeypatch.setattr("builtins.print", broken)
    diagnostic.emit("ready")
    assert diagnostic.enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("now,reason,state", [
    (1.5, "wake_word_detected", ActivationState.ACTIVE),
    (31, "wake_word_expired", ActivationState.WAITING),
])
async def test_decision_logs_distinguish_accepted_and_rejected_hits(monkeypatch, caplog, now, reason, state):
    monkeypatch.setenv("NEKO_WAKE_WORD_DIAGNOSTICS", "1")
    caplog.set_level(logging.INFO)
    instance, _, _, _ = runtime(Detector(hit_at=0), clock=lambda: now)
    try:
        await instance.prepare()
        await instance.feed(frame(0), voice_activity=False)
        await settle()
        assert instance.state is state
        assert f"Wake word decision reason={reason}" in caplog.text
    finally:
        await instance.close()
