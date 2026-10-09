"""Diagnostics must distinguish DSP latency from waiting without changing PCM."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from main_logic.voice_turn import audio_input


class Processor:
    speech_probability = 0.2
    rnnoise_available = True
    rnnoise_frame_count = 3
    rnnoise_probability_peak = 0.9
    rnnoise_probability_mean = 0.6
    rnnoise_probability_last = 0.2
    rnnoise_probability_ema = 0.55

    def __init__(self, clock):
        self.clock = clock

    def process_chunk(self, pcm):
        self.clock.value += 0.01
        return pcm[:320]

    def close(self):
        pass


@pytest.mark.asyncio
async def test_logs_separate_lock_wait_dsp_and_missing_evidence(monkeypatch, caplog):
    clock = SimpleNamespace(value=10.0)
    monkeypatch.setattr(audio_input, "time", SimpleNamespace(perf_counter=lambda: clock.value))
    processor = Processor(clock)
    pipeline = audio_input.VoiceInputAudioPipeline(processor_factory=lambda: processor)
    caplog.set_level(logging.INFO, logger=audio_input.__name__)
    source = b"private-audio-marker!" * 144
    await pipeline._lock.acquire()
    pending = asyncio.create_task(pipeline.process(source, sample_rate_hz=48_000))
    await asyncio.sleep(0)
    clock.value += 0.3
    pipeline._lock.release()
    try:
        frame = await pending
        assert frame.pcm16 == source[:320]
        assert frame.speech_probability == 0.9
        stats = caplog.records[-1].args[-1]
        assert stats["lock_wait_ms"] == pytest.approx(300)
        assert stats["dsp_ms"] == pytest.approx(10)
        assert stats["pipeline_ms"] == pytest.approx(310)
        assert stats["probability_mean"] == pytest.approx(0.6)
        assert stats["peak_ge_0_5_chunks"] == 1
        processor.rnnoise_available = False
        clock.value += 2.1
        await pipeline.process(source, sample_rate_hz=48_000)
        missing = caplog.records[-1].args[-1]
        assert missing["unavailable_chunks"] == 1
        assert missing["probability_mean"] is None
        assert missing["probability_peak"] is None
        assert "private-audio-marker" not in caplog.text
    finally:
        await pipeline.close()


@pytest.mark.asyncio
async def test_16k_diagnostics_are_rate_limited_and_cannot_fail_audio(monkeypatch, caplog):
    clock = SimpleNamespace(value=10.0)
    monkeypatch.setattr(audio_input, "time", SimpleNamespace(perf_counter=lambda: clock.value))
    pipeline = audio_input.VoiceInputAudioPipeline()
    caplog.set_level(logging.INFO, logger=audio_input.__name__)
    pcm = b"\x11\x22" * 160
    try:
        for _ in range(201):
            assert (await pipeline.process(pcm, sample_rate_hz=16_000)).pcm16 == pcm
        assert len(caplog.records) == 1
        clock.value += 2.0
        await pipeline.process(pcm, sample_rate_hz=16_000)
        stats = caplog.records[-1].args[-1]
        assert stats["chunks"] == 201
        assert stats["input_ms"] == 2010
        assert stats["rnnoise_frames"] == 0
        assert stats["probability_mean"] is None

        def broken_logger(*args):
            raise OSError("log sink unavailable")

        monkeypatch.setattr(audio_input.logger, "info", broken_logger)
        clock.value += 2.0
        assert (await pipeline.process(pcm, sample_rate_hz=16_000)).pcm16 == pcm
    finally:
        await pipeline.close()
