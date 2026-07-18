"""Provider-neutral microphone PCM validation and normalization."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from main_logic.asr_client.activity_evidence import RnnoiseEvidence
from utils.audio_processor import AudioProcessor


class _AudioProcessorProtocol(Protocol):
    speech_probability: float

    def process_chunk(self, audio_bytes: bytes) -> bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ProcessedVoiceFrame:
    """One validated mono PCM16 frame normalized for voice input consumers."""

    pcm16: bytes
    sample_rate_hz: int
    speech_probability: float | None
    rnnoise_available: bool = False
    rnnoise_evidence: RnnoiseEvidence | None = None


class VoiceInputAudioPipeline:
    """Validate PCM and normalize PC 48 kHz or mobile 16 kHz to 16 kHz."""

    def __init__(
        self,
        *,
        processor_factory: Callable[[], _AudioProcessorProtocol] | None = None,
    ) -> None:
        self._processor_factory = processor_factory or AudioProcessor
        self._processor: _AudioProcessorProtocol | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def _process_chunk_cancellation_safe(
        self,
        processor: _AudioProcessorProtocol,
        pcm16: bytes,
    ) -> bytes:
        processing_task = asyncio.create_task(
            asyncio.to_thread(processor.process_chunk, pcm16)
        )
        cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                processed = await asyncio.shield(processing_task)
            except asyncio.CancelledError as exc:
                if processing_task.cancelled():
                    raise
                if cancellation is None:
                    cancellation = exc
                continue
            except Exception:
                if cancellation is not None:
                    raise cancellation
                raise
            if cancellation is not None:
                raise cancellation
            return processed

    async def process(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
    ) -> ProcessedVoiceFrame:
        if not isinstance(pcm16, bytes):
            raise TypeError("microphone PCM must be bytes")
        if len(pcm16) % 2:
            raise ValueError("microphone PCM16 contains an incomplete sample")
        if sample_rate_hz not in (16_000, 48_000):
            raise ValueError("microphone sample rate must be 16000 or 48000")
        if self._closed:
            raise RuntimeError("VOICE_AUDIO_PIPELINE_CLOSED")
        if not pcm16:
            return ProcessedVoiceFrame(
                b"", 16_000, None, False, RnnoiseEvidence.unavailable()
            )
        if sample_rate_hz == 16_000:
            return ProcessedVoiceFrame(
                pcm16, 16_000, None, False, RnnoiseEvidence.unavailable()
            )

        async with self._lock:
            if self._closed:
                raise RuntimeError("VOICE_AUDIO_PIPELINE_CLOSED")
            if self._processor is None:
                self._processor = self._processor_factory()
            processed = await self._process_chunk_cancellation_safe(
                self._processor,
                pcm16,
            )
            probability = float(self._processor.speech_probability)
            rnnoise_available = bool(
                getattr(
                    self._processor,
                    "rnnoise_available",
                    getattr(self._processor, "_denoiser", None) is not None,
                )
            )
            if rnnoise_available:
                frame_count = int(
                    getattr(
                        self._processor,
                        "rnnoise_frame_count",
                        1 if processed else 0,
                    )
                )
                if frame_count > 0:
                    peak = float(
                        getattr(
                            self._processor,
                            "rnnoise_probability_peak",
                            probability,
                        )
                    )
                    mean = float(
                        getattr(
                            self._processor,
                            "rnnoise_probability_mean",
                            probability,
                        )
                    )
                    last = float(
                        getattr(
                            self._processor,
                            "rnnoise_probability_last",
                            probability,
                        )
                    )
                    ema = float(
                        getattr(
                            self._processor,
                            "rnnoise_probability_ema",
                            probability,
                        )
                    )
                    evidence = RnnoiseEvidence(
                        True, frame_count, peak, mean, last, ema
                    )
                else:
                    evidence = RnnoiseEvidence(True, 0, None, None, None, None)
            else:
                evidence = RnnoiseEvidence.unavailable()
        return ProcessedVoiceFrame(
            processed,
            16_000,
            evidence.peak,
            rnnoise_available,
            evidence,
        )

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            processor, self._processor = self._processor, None
            if processor is not None:
                await asyncio.to_thread(processor.close)
