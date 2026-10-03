"""Provider-neutral microphone PCM validation and normalization."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .activity_evidence import RnnoiseEvidence
from utils.audio_processor import AudioProcessor


logger = logging.getLogger(__name__)


class _AudioProcessorProtocol(Protocol):
    speech_probability: float
    rnnoise_available: bool
    rnnoise_frame_count: int
    rnnoise_probability_peak: float | None
    rnnoise_probability_mean: float | None
    rnnoise_probability_last: float | None
    rnnoise_probability_ema: float | None

    def process_chunk(self, audio_bytes: bytes) -> bytes: ...

    def finalize_stream(self) -> bytes: ...

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
        nr_enabled: bool = True,
        processor_factory: Callable[[], _AudioProcessorProtocol] | None = None,
    ) -> None:
        self._nr_enabled = bool(nr_enabled)
        self._processor_factory = (
            processor_factory or self._default_processor_factory
        )
        self._processor: _AudioProcessorProtocol | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._stream_finalized = False
        self._diagnostic_id = uuid.uuid4().hex[:12]
        self._diagnostic_next_at = 0.0
        self._diagnostic_totals: Counter[str] = Counter()
        self._diagnostic_probability_peak: float | None = None
        self._diagnostic_last_input_at: float | None = None
        self._native_chunk_seconds = self._native_queue_seconds = 0.0

    @property
    def nr_enabled(self) -> bool:
        return self._nr_enabled

    def _default_processor_factory(self) -> _AudioProcessorProtocol:
        return AudioProcessor(noise_reduce_enabled=self._nr_enabled)

    async def _process_chunk_cancellation_safe(
        self,
        processor: _AudioProcessorProtocol,
        pcm16: bytes,
    ) -> bytes:
        queued_at = time.perf_counter()

        def process_timed() -> bytes:
            started_at = time.perf_counter()
            self._native_queue_seconds = started_at - queued_at
            try:
                return processor.process_chunk(pcm16)
            finally:
                # DSP includes RNNoise, resampling and AGC, not just inference.
                self._native_chunk_seconds = time.perf_counter() - started_at

        return await self._run_native_cancellation_safe(process_timed)

    async def _run_native_cancellation_safe(
        self,
        operation: Callable[[], bytes | None],
    ) -> bytes | None:
        processing_task = asyncio.create_task(
            asyncio.to_thread(operation)
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
        requested_at = time.perf_counter()
        async with self._lock:
            acquired_at = time.perf_counter()
            if self._closed:
                raise RuntimeError("VOICE_AUDIO_PIPELINE_CLOSED")
            if self._stream_finalized:
                raise RuntimeError("VOICE_AUDIO_PIPELINE_FINALIZED")
            if not pcm16:
                return ProcessedVoiceFrame(
                    b"", 16_000, None, False, RnnoiseEvidence.unavailable()
                )
            if sample_rate_hz == 16_000:
                self._record_audio_diagnostics(
                    RnnoiseEvidence.unavailable(), pcm16, pcm16,
                    sample_rate_hz, requested_at, acquired_at, 0.0, 0.0,
                )
                return ProcessedVoiceFrame(
                    pcm16, 16_000, None, False, RnnoiseEvidence.unavailable()
                )
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
            if getattr(self._processor, "rnnoise_processing_failed", False):
                rnnoise_available = False
            raw_frame_count = getattr(
                self._processor,
                "rnnoise_frame_count",
                None,
            )
            if not rnnoise_available:
                evidence = RnnoiseEvidence.unavailable()
            elif raw_frame_count is None:
                # Compatibility for injected legacy processors only. The production
                # AudioProcessor always exposes real per-chunk frame statistics.
                evidence = RnnoiseEvidence.from_legacy_probability(
                    probability,
                    available=True,
                )
            else:
                frame_count = int(raw_frame_count)
                if frame_count > 0:
                    evidence = RnnoiseEvidence(
                        True,
                        frame_count,
                        float(self._processor.rnnoise_probability_peak),
                        float(self._processor.rnnoise_probability_mean),
                        float(self._processor.rnnoise_probability_last),
                        float(self._processor.rnnoise_probability_ema),
                    )
                else:
                    evidence = RnnoiseEvidence(
                        True, 0, None, None, None, None
                    )
            self._record_audio_diagnostics(
                evidence, pcm16, processed, sample_rate_hz,
                requested_at, acquired_at,
                self._native_chunk_seconds, self._native_queue_seconds,
            )
        return ProcessedVoiceFrame(
            processed,
            16_000,
            evidence.peak,
            rnnoise_available,
            evidence,
        )

    def _record_audio_diagnostics(
        self, evidence: RnnoiseEvidence, source: bytes, output: bytes,
        sample_rate: int, requested_at: float, acquired_at: float,
        native_seconds: float, queue_seconds: float,
    ) -> None:
        """Bounded numeric summaries; unavailable evidence is never a zero score."""
        now = time.perf_counter()
        totals = self._diagnostic_totals
        totals["chunks"] += 1
        totals["input_ms"] += len(source) * 500 / sample_rate
        totals["output_ms"] += len(output) / 32
        totals["rnnoise_frames"] += evidence.frame_count
        totals["unavailable_chunks"] += int(not evidence.available)
        if evidence.mean is not None:
            totals["probability_sum"] += evidence.mean * evidence.frame_count
            totals["scored_frames"] += evidence.frame_count
        if evidence.peak is not None:
            peak = self._diagnostic_probability_peak
            self._diagnostic_probability_peak = max(peak or 0.0, evidence.peak)
            totals["peak_ge_0_5_chunks"] += int(evidence.peak >= 0.5)
        for key, seconds in (
            ("dsp", native_seconds),
            ("thread_queue", queue_seconds),
            ("lock_wait", acquired_at - requested_at),
            ("pipeline", now - requested_at),
        ):
            totals[key + "_ms"] += seconds * 1000
            totals[key + "_max_ms"] = max(totals[key + "_max_ms"], seconds * 1000)
        if self._diagnostic_last_input_at is not None:
            totals["input_interval_max_ms"] = max(
                totals["input_interval_max_ms"],
                (requested_at - self._diagnostic_last_input_at) * 1000,
            )
        self._diagnostic_last_input_at = requested_at
        if now < self._diagnostic_next_at:
            return
        mean = (totals["probability_sum"] / totals["scored_frames"]
                if totals["scored_frames"] else None)
        summary = {key: round(value, 3) for key, value in totals.items()
                   if key != "probability_sum"}
        summary.update(probability_mean=mean,
                       probability_peak=self._diagnostic_probability_peak,
                       latest_probability_last=evidence.last,
                       latest_probability_ema=evidence.ema)
        try:
            logger.info("[voice-rnnoise] pipeline=%s stats=%s", self._diagnostic_id, summary)
        except Exception:
            # Diagnostics cannot reject PCM or break cancellation/close ownership.
            pass
        self._diagnostic_next_at = now + 2.0
        totals.clear()
        self._diagnostic_probability_peak = None

    async def finalize_stream(self) -> bytes:
        """Flush the processor EOF tail once without closing native state."""

        async with self._lock:
            if self._closed:
                raise RuntimeError("VOICE_AUDIO_PIPELINE_CLOSED")
            if self._stream_finalized:
                raise RuntimeError("VOICE_AUDIO_PIPELINE_FINALIZED")
            processor = self._processor
            if processor is None:
                raise RuntimeError("VOICE_AUDIO_PIPELINE_EMPTY")
            self._stream_finalized = True
            result = await self._run_native_cancellation_safe(
                processor.finalize_stream
            )
            if type(result) is not bytes:
                raise TypeError("audio processor EOF tail must be bytes")
            return result

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            processor, self._processor = self._processor, None
            if processor is not None:
                await self._run_native_cancellation_safe(processor.close)
