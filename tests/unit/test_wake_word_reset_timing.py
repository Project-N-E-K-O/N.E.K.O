"""Keyword timestamps remain valid after sherpa's automatic silence reset."""

import os
import sys
import wave
from types import SimpleNamespace

import pytest

from config.voice_wake_word import DEFAULT_WAKE_WORD_KEYWORDS
from main_logic.voice_input.activation import (
    ActivationGeneration, ActivationState, AudioFrame, OutputCommit, VoiceActivationController,
)
from main_logic.voice_input.wake_word import sherpa_backend as backend


@pytest.mark.parametrize("version", [None, "1.13.8", "1.13.9", "1.13.8+neko.kws1"])
def test_unverified_runtime_cannot_report_detector_ready(monkeypatch, version):
    monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace(
        __version__=version, version=backend.SUPPORTED_RUNTIME_VERSION))
    with pytest.raises(backend.WakeWordBackendError, match="RUNTIME_FIX_REQUIRED"):
        backend._StreamingSpotter(backend.SherpaWakeWordConfig(
            "unused", DEFAULT_WAKE_WORD_KEYWORDS,
        ))


@pytest.mark.skipif(
    not (os.getenv("NEKO_WAKE_WORD_MODEL_DIR") and os.getenv("NEKO_WAKE_WORD_TEST_WAV")),
    reason="Requires explicit local keyword model and positive WAV fixture",
)
@pytest.mark.parametrize("prefix_seconds", [12, 45])
def test_real_keyword_after_long_silence_activates_with_retained_pcm(prefix_seconds):
    with wave.open(os.environ["NEKO_WAKE_WORD_TEST_WAV"], "rb") as source:
        assert (source.getframerate(), source.getsampwidth(), source.getnchannels()) == (16000, 2, 1)
        voice = source.readframes(source.getnframes())
    pcm = b"\0" * (prefix_seconds * 32000) + voice
    spotter = backend._StreamingSpotter(backend.SherpaWakeWordConfig(
        os.environ["NEKO_WAKE_WORD_MODEL_DIR"], DEFAULT_WAKE_WORD_KEYWORDS,
    ))
    generation = ActivationGeneration("long-silence", 1, 1, 1, 1, "microphone")
    controller = VoiceActivationController()
    controller.start(generation, enabled=True)
    controller.mark_ready(generation)
    detection = None
    for offset in range(0, len(pcm), 640):
        chunk = pcm[offset:offset + 640]
        audio = AudioFrame(offset // 640, offset // 2, (offset + len(chunk)) // 2,
                           100 + offset / 32000, 16000, chunk, generation)
        controller.ingest(audio, voice_activity=False)
        if detection is None:
            result = spotter.feed(audio, controller.standby_epoch)
            if result is not None:
                detection = result
                assert detection.sample_start >= prefix_seconds * 16000
                decision = controller.apply_wake_word(result, now=audio.captured_end_at)
                assert decision.reason == "wake_word_detected"
                assert decision.state is ActivationState.REPLAYING
    assert detection is not None, "Positive fixture must produce an actual model hit"
    received = []
    while (lease := controller.claim_output()) is not None:
        received.append(lease.frame)
        controller.complete_output(lease, OutputCommit.TRANSPORT_WRITTEN,
                                   now=audio.captured_end_at)
    assert controller.state is ActivationState.ACTIVE
    assert received[0].sample_start > 0, "Evicted silence must not be replayed"
    assert received[0].sample_start <= detection.sample_start
    assert received[-1].sample_end == len(pcm) // 2
    assert [f.sequence for f in received] == list(range(
        received[0].sequence, received[-1].sequence + 1,
    ))
    assert b"".join(f.pcm for f in received) == pcm[received[0].sample_start * 2:]
