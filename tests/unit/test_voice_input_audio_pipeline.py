from __future__ import annotations

import asyncio
import threading

import pytest

from main_logic.voice_turn.audio_input import VoiceInputAudioPipeline


class _Processor:
    def __init__(self) -> None:
        self.inputs: list[bytes] = []
        self.closed = False
        self.speech_probability = 0.75
        self.rnnoise_available = True

    def process_chunk(self, pcm16: bytes) -> bytes:
        self.inputs.append(pcm16)
        return b"\x02\x00" * 160

    def close(self) -> None:
        self.closed = True


async def test_pipeline_passes_16k_without_creating_rnnoise_processor() -> None:
    created: list[_Processor] = []
    pipeline = VoiceInputAudioPipeline(
        processor_factory=lambda: created.append(_Processor()) or created[-1]
    )

    pcm16 = b"\x01\x00" * 160
    frame = await pipeline.process(pcm16, sample_rate_hz=16_000)

    assert frame.pcm16 == pcm16
    assert frame.sample_rate_hz == 16_000
    assert frame.speech_probability is None
    assert created == []


async def test_pipeline_owns_48k_processor_and_exposes_rnnoise_probability() -> None:
    processor = _Processor()
    pipeline = VoiceInputAudioPipeline(processor_factory=lambda: processor)
    source = b"\x01\x00" * 480

    frame = await pipeline.process(source, sample_rate_hz=48_000)

    assert processor.inputs == [source]
    assert frame.pcm16 == b"\x02\x00" * 160
    assert frame.sample_rate_hz == 16_000
    assert frame.speech_probability == 0.75
    assert frame.rnnoise_available is True
    await pipeline.close()
    assert processor.closed is True


async def test_pipeline_rejects_invalid_pcm_and_sample_rate() -> None:
    pipeline = VoiceInputAudioPipeline()

    try:
        await pipeline.process(b"\x00", sample_rate_hz=16_000)
    except ValueError as exc:
        assert "PCM16" in str(exc)
    else:
        raise AssertionError("odd PCM must be rejected")

    try:
        await pipeline.process(b"\x00\x00", sample_rate_hz=24_000)
    except ValueError as exc:
        assert "sample rate" in str(exc)
    else:
        raise AssertionError("unsupported sample rate must be rejected")


async def test_pipeline_close_waits_for_cancelled_processing_thread() -> None:
    processing_started = threading.Event()
    release_processing = threading.Event()

    class _BlockingProcessor(_Processor):
        def __init__(self) -> None:
            super().__init__()
            self.processing = False

        def process_chunk(self, pcm16: bytes) -> bytes:
            self.processing = True
            processing_started.set()
            assert release_processing.wait(5)
            self.processing = False
            return super().process_chunk(pcm16)

        def close(self) -> None:
            assert not self.processing
            super().close()

    processor = _BlockingProcessor()
    pipeline = VoiceInputAudioPipeline(processor_factory=lambda: processor)
    process_task = asyncio.create_task(
        pipeline.process(b"\x01\x00" * 480, sample_rate_hz=48_000)
    )
    assert await asyncio.to_thread(processing_started.wait, 5)

    process_task.cancel()
    close_task = asyncio.create_task(pipeline.close())
    await asyncio.sleep(0)

    assert not process_task.done()
    assert not close_task.done()
    assert processor.closed is False

    release_processing.set()
    with pytest.raises(asyncio.CancelledError):
        await process_task
    await close_task

    assert processor.closed is True
