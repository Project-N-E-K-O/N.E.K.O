"""Opt-in real model/real runtime PCM delivery acceptance, using local WAVs."""

import asyncio
import os
import wave

import pytest

from config.voice_wake_word import DEFAULT_WAKE_WORD_KEYWORDS
from main_logic.voice_identity_service.activation_runtime import VoiceSessionActivationRuntime
from main_logic.voice_identity_service.activation_scoring import ActivationScoreStatus
from main_logic.voice_input.activation import (
    ActivationGeneration, ActivationState, AudioFrame, OutputCommit, VoiceActivationController,
)
from main_logic.voice_input.wake_word.sherpa_backend import SherpaWakeWordConfig, SherpaWakeWordDetector


class ReadyScorer:
    calls = 0
    profile_generation = "profile"
    scorer_generation = 1

    async def prepare(self):
        return ActivationScoreStatus.READY

    async def score(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("Short wake word must not require speaker scoring")

    async def close(self):
        pass


@pytest.mark.asyncio
@pytest.mark.skipif(not (os.getenv("NEKO_WAKE_WORD_MODEL_DIR") and os.getenv("NEKO_WAKE_WORD_TEST_WAV")),
                    reason="Set explicit local model and positive WAV paths for acoustic smoke")
async def test_real_model_activates_without_vad_or_speaker_and_delivers_original_pcm_once():
    path = os.environ["NEKO_WAKE_WORD_TEST_WAV"]
    with wave.open(path, "rb") as source:
        assert (source.getframerate(), source.getsampwidth(), source.getnchannels()) == (16000, 2, 1)
        pcm = source.readframes(source.getnframes())
    assert len(pcm) < 8 * 32000, "Smoke fixture must fit original replay buffer"
    generation = ActivationGeneration("actual-model-smoke", 1, 1, 1, 1, "microphone")
    detector = SherpaWakeWordDetector(SherpaWakeWordConfig(
        os.environ["NEKO_WAKE_WORD_MODEL_DIR"], DEFAULT_WAKE_WORD_KEYWORDS))
    scorer = ReadyScorer()
    received, decisions = [], []

    async def output(frame):
        received.append(frame)
        return OutputCommit.TRANSPORT_WRITTEN

    runtime = VoiceSessionActivationRuntime(generation, scorer, output,
        controller=VoiceActivationController(clock=lambda: 10 + len(pcm) / 32000),
        status_callback=decisions.append, wake_detector=detector)
    try:
        assert (await runtime.prepare()).state is ActivationState.WAITING
        assert detector.runtime_info is not None
        assert detector.runtime_info["runtime_version"] == "1.13.8+neko.kws2"
        assert detector.runtime_info["native_version"] == "1.13.8+neko.kws2"
        assert detector.runtime_info["keyword_threshold"] == 0.25
        assert detector.runtime_info["max_active_paths"] == 8
        assert detector.runtime_info["keyword_score"] == 1.0
        for offset in range(0, len(pcm), 640):
            chunk = pcm[offset:offset + 640]
            frame = AudioFrame(offset // 640, offset // 2, (offset + len(chunk)) // 2,
                               10 + offset / 32000, 16000, chunk, generation)
            await runtime.feed(frame, voice_activity=False)
        async with asyncio.timeout(5):
            while runtime.state is not ActivationState.ACTIVE or runtime.pending_output_bytes:
                assert runtime.state is not ActivationState.UNAVAILABLE
                await asyncio.sleep(0.01)
        assert scorer.calls == 0
        assert sum(d.reason == "wake_word_detected" for d in decisions) == 1
        assert received
        assert [f.sequence for f in received] == list(range(received[0].sequence, received[-1].sequence + 1))
        assert received[-1].sample_end == len(pcm) // 2
        assert b"".join(f.pcm for f in received) == pcm[received[0].sample_start * 2:]
        # The provided synthetic fixture begins the name within 300ms, so all
        # source PCM (including its contiguous command) should be replayed.
        assert received[0].sample_start == 0
    finally:
        await runtime.close()
