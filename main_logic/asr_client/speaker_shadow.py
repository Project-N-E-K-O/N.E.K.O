"""Best-effort speaker verification with no authority over the ASR route."""

from __future__ import annotations

import asyncio
import inspect
import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import asdict, dataclass
from typing import Literal, Protocol


class SpeakerShadowBackend(Protocol):
    """Injectable model adapter; model selection and assets stay out of core."""

    def load(self) -> bool: ...

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SpeakerShadowCandidateKey:
    """Identity private to observation-only speaker verification."""

    detector_epoch: int
    shadow_generation: int
    scope: Literal["provider_pause", "smart_turn_turn"]


@dataclass(frozen=True, slots=True)
class SpeakerShadowConfig:
    enabled: bool = False
    similarity_thresholds: tuple[float, ...] = (0.40, 0.44, 0.48, 0.52, 0.55)
    minimum_audio_ms: int = 1_500
    maximum_audio_ms: int = 4_000
    idle_unload_seconds: float = 60.0
    queue_capacity: int = 32
    finalized_candidate_capacity: int = 1_024
    load_retry_initial_seconds: float = 5.0
    load_retry_max_seconds: float = 60.0
    shutdown_grace_seconds: float = 0.1
    callback_timeout_seconds: float = 0.1

    def __post_init__(self) -> None:
        if (
            not self.similarity_thresholds
            or any(
                not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0
                for threshold in self.similarity_thresholds
            )
            or any(
                left >= right
                for left, right in zip(
                    self.similarity_thresholds,
                    self.similarity_thresholds[1:],
                )
            )
        ):
            raise ValueError(
                "similarity_thresholds must be finite, unique, increasing values "
                "within [0, 1]"
            )
        if self.minimum_audio_ms <= 0:
            raise ValueError("minimum_audio_ms must be positive")
        if self.maximum_audio_ms < self.minimum_audio_ms:
            raise ValueError("maximum_audio_ms must be at least minimum_audio_ms")
        if self.idle_unload_seconds <= 0:
            raise ValueError("idle_unload_seconds must be positive")
        if self.queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")
        if self.finalized_candidate_capacity < self.queue_capacity:
            raise ValueError(
                "finalized_candidate_capacity must be at least queue_capacity"
            )
        if self.load_retry_initial_seconds <= 0:
            raise ValueError("load_retry_initial_seconds must be positive")
        if self.load_retry_max_seconds < self.load_retry_initial_seconds:
            raise ValueError(
                "load_retry_max_seconds must be at least load_retry_initial_seconds"
            )
        if self.shutdown_grace_seconds <= 0:
            raise ValueError("shutdown_grace_seconds must be positive")
        if self.callback_timeout_seconds <= 0:
            raise ValueError("callback_timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class SpeakerShadowObservation:
    candidate: Hashable
    similarity: float
    would_block: tuple[tuple[float, bool], ...]
    audio_ms: int


@dataclass(slots=True)
class SpeakerShadowMetrics:
    submitted_frame_count: int = 0
    evaluated_candidate_count: int = 0
    would_block_count: int = 0
    load_count: int = 0
    unload_count: int = 0
    load_failure_count: int = 0
    inference_failure_count: int = 0
    callback_failure_count: int = 0
    dropped_frame_count: int = 0
    stale_result_count: int = 0
    submitted_audio_ms: int = 0
    load_ms: int = 0
    inference_ms: int = 0

    started_candidate_count: int = 0
    finished_candidate_count: int = 0
    insufficient_candidate_count: int = 0
    dropped_candidate_count: int = 0
    dropped_audio_ms: int = 0
    load_retry_suppressed_count: int = 0
    shutdown_timeout_count: int = 0

    def snapshot(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _AudioFrame:
    generation: int
    candidate: Hashable
    pcm16: bytes
    sample_rate_hz: int
    duration_ms: int


@dataclass(frozen=True, slots=True)
class _CandidateFinished:
    generation: int
    candidate: Hashable


@dataclass(slots=True)
class _CandidateBuffer:
    sample_rate_hz: int
    pcm16: bytearray
    audio_ms: int = 0


@dataclass(frozen=True, slots=True)
class _FinalizedCandidate:
    finish_seen: bool
    terminal_reason: Literal["sufficient", "insufficient", "dropped"]


ObservationCallback = Callable[[SpeakerShadowObservation], Awaitable[None] | None]
_STOP = object()


class SpeakerShadowRuntime:
    """Run speaker scoring asynchronously and expose observations only.

    ``submit`` never waits for model work. Queue pressure, model failures, low
    similarity, and callback failures are deliberately reduced to metrics.
    """

    def __init__(
        self,
        *,
        backend_factory: Callable[[], SpeakerShadowBackend] | None,
        config: SpeakerShadowConfig | None = None,
        on_observation: ObservationCallback | None = None,
    ) -> None:
        self._config = config or SpeakerShadowConfig()
        self._backend_factory = backend_factory
        self._on_observation = on_observation
        self._metrics = SpeakerShadowMetrics()
        self._would_block_counts = {
            threshold: 0 for threshold in self._config.similarity_thresholds
        }
        self._backend_metrics: dict[str, int] = {}
        self._queue: asyncio.Queue[_AudioFrame | _CandidateFinished | object] = (
            asyncio.Queue(maxsize=self._config.queue_capacity)
        )
        self._worker_task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._backend: SpeakerShadowBackend | None = None
        self._load_failure_streak = 0
        self._next_load_attempt_at = 0.0
        self._buffers: OrderedDict[Hashable, _CandidateBuffer] = OrderedDict()
        self._finalized: OrderedDict[Hashable, _FinalizedCandidate] = OrderedDict()
        self._generation = 0
        self._closed = False

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def snapshot(self) -> dict[str, int]:
        snapshot = self._metrics.snapshot()
        snapshot.update(
            buffered_candidate_count=len(self._buffers),
            buffered_audio_bytes=sum(
                len(buffer.pcm16) for buffer in self._buffers.values()
            ),
            finalized_tombstone_count=len(self._finalized),
        )
        snapshot.update(
            {
                f"would_block_at_{round(threshold * 100):03d}_count": count
                for threshold, count in self._would_block_counts.items()
            }
        )
        backend_metrics = dict(self._backend_metrics)
        for key, value in self._read_backend_metrics(self._backend).items():
            backend_metrics[key] = backend_metrics.get(key, 0) + value
        snapshot.update(
            {f"backend_{key}": value for key, value in backend_metrics.items()}
        )
        return snapshot

    def submit(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
        candidate: Hashable,
    ) -> bool:
        """Queue one accepted candidate frame without granting execution power."""

        if not self._config.enabled or self._closed:
            return False
        if not isinstance(pcm16, bytes) or not pcm16 or len(pcm16) % 2:
            raise ValueError("SpeakerShadowRuntime requires non-empty PCM16 bytes")
        if sample_rate_hz <= 0:
            raise ValueError("SpeakerShadowRuntime sample rate must be positive")
        duration_ms = max(1, (len(pcm16) // 2) * 1_000 // sample_rate_hz)
        frame = _AudioFrame(
            generation=self._generation,
            candidate=candidate,
            pcm16=pcm16,
            sample_rate_hz=sample_rate_hz,
            duration_ms=duration_ms,
        )
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            self._metrics.dropped_frame_count += 1
            self._metrics.dropped_audio_ms += duration_ms
            return False
        self._metrics.submitted_frame_count += 1
        self._metrics.submitted_audio_ms += duration_ms
        self._ensure_worker()
        return True

    def finish_candidate(self, candidate: Hashable) -> bool:
        """Order a candidate boundary behind its queued PCM without blocking ASR."""

        if not self._config.enabled or self._closed:
            return False
        marker = _CandidateFinished(self._generation, candidate)
        try:
            self._queue.put_nowait(marker)
        except asyncio.QueueFull:
            self._finish_candidate_now(
                marker,
                dropped=True,
            )
            return False
        self._ensure_worker()
        return True

    async def wait_idle(self) -> None:
        """Wait for already queued observations, without waiting for idle unload."""

        await self._queue.join()

    async def reset(self) -> None:
        """Invalidate queued/in-flight results while retaining a warm backend."""

        if self._closed:
            return
        self._generation += 1
        self._buffers.clear()
        self._finalized.clear()
        self._load_failure_streak = 0
        self._next_load_attempt_at = 0.0
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                self._queue.task_done()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self._buffers.clear()
        self._finalized.clear()
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                self._queue.task_done()
        worker, self._worker_task = self._worker_task, None
        if worker is not None and not worker.done():
            self._queue.put_nowait(_STOP)
        cleanup = asyncio.create_task(
            self._cleanup_after_worker(worker),
            name="speaker-shadow-cleanup",
        )
        self._cleanup_task = cleanup
        cleanup.add_done_callback(self._consume_cleanup_result)
        try:
            await asyncio.wait_for(
                asyncio.shield(cleanup),
                timeout=self._config.shutdown_grace_seconds,
            )
        except asyncio.TimeoutError:
            self._metrics.shutdown_timeout_count += 1

    def _ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(
                self._run(), name="speaker-shadow-runtime"
            )

    async def _run(self) -> None:
        while True:
            try:
                pending = self._queue  # asyncio.Queue, not stdlib queue.Queue.
                frame = await asyncio.wait_for(
                    pending.get(),
                    timeout=self._config.idle_unload_seconds,
                )
            except asyncio.TimeoutError:
                await self._unload_backend()
                if self._closed:
                    return
                continue
            try:
                if frame is _STOP:
                    return
                if isinstance(frame, _CandidateFinished):
                    self._finish_candidate_now(frame)
                else:
                    assert isinstance(frame, _AudioFrame)
                    await self._process(frame)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Shadow work must never escape into the ASR task graph.
                self._metrics.inference_failure_count += 1
            finally:
                self._queue.task_done()

    async def _process(self, frame: _AudioFrame) -> None:
        if frame.generation != self._generation or frame.candidate in self._finalized:
            return
        buffer = self._buffers.get(frame.candidate)
        if buffer is None or buffer.sample_rate_hz != frame.sample_rate_hz:
            if buffer is None and len(self._buffers) >= self._config.queue_capacity:
                dropped_candidate, dropped_buffer = self._buffers.popitem(last=False)
                self._mark_finalized(
                    dropped_candidate,
                    finish_seen=False,
                    terminal_reason="dropped",
                )
                self._metrics.dropped_candidate_count += 1
                self._metrics.dropped_audio_ms += dropped_buffer.audio_ms
            buffer = _CandidateBuffer(frame.sample_rate_hz, bytearray())
            self._buffers[frame.candidate] = buffer
            self._metrics.started_candidate_count += 1
        else:
            self._buffers.move_to_end(frame.candidate)
        remaining_ms = self._config.maximum_audio_ms - buffer.audio_ms
        if remaining_ms <= 0:
            return
        allowed_samples = frame.sample_rate_hz * remaining_ms // 1_000
        allowed_bytes = min(len(frame.pcm16), allowed_samples * 2)
        buffer.pcm16.extend(frame.pcm16[:allowed_bytes])
        buffer.audio_ms += min(frame.duration_ms, remaining_ms)
        if buffer.audio_ms < self._config.minimum_audio_ms:
            return
        pcm16 = bytes(buffer.pcm16)
        audio_ms = buffer.audio_ms
        self._buffers.pop(frame.candidate, None)
        self._mark_finalized(
            frame.candidate,
            finish_seen=False,
            terminal_reason="sufficient",
        )
        backend = await self._ensure_backend()
        if backend is None:
            return
        started = time.perf_counter()
        try:
            similarity = float(
                await asyncio.to_thread(backend.score, pcm16, buffer.sample_rate_hz)
            )
            if not math.isfinite(similarity) or not -1.0 <= similarity <= 1.0:
                raise ValueError("speaker cosine similarity must be within [-1, 1]")
        except asyncio.CancelledError:
            raise
        except Exception:
            self._metrics.inference_failure_count += 1
            return
        finally:
            self._metrics.inference_ms += int((time.perf_counter() - started) * 1_000)
        if frame.generation != self._generation or self._closed:
            self._metrics.stale_result_count += 1
            return
        would_block = tuple(
            (threshold, similarity < threshold)
            for threshold in self._config.similarity_thresholds
        )
        observation = SpeakerShadowObservation(
            candidate=frame.candidate,
            similarity=similarity,
            would_block=would_block,
            audio_ms=audio_ms,
        )
        self._metrics.evaluated_candidate_count += 1
        if any(blocked for _, blocked in would_block):
            self._metrics.would_block_count += 1
        for threshold, blocked in would_block:
            if blocked:
                self._would_block_counts[threshold] += 1
        if self._on_observation is None:
            return
        try:
            await asyncio.wait_for(
                self._invoke_observation_callback(observation),
                timeout=self._config.callback_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._metrics.callback_failure_count += 1

    async def _invoke_observation_callback(
        self,
        observation: SpeakerShadowObservation,
    ) -> None:
        callback = self._on_observation
        if callback is None:
            return
        if inspect.iscoroutinefunction(callback):
            await callback(observation)
            return
        callback_result = await asyncio.to_thread(callback, observation)
        if inspect.isawaitable(callback_result):
            await callback_result

    def _finish_candidate_now(
        self,
        marker: _CandidateFinished,
        *,
        dropped: bool = False,
    ) -> None:
        if marker.generation != self._generation:
            return
        finalized = self._finalized.get(marker.candidate)
        if finalized is not None and finalized.finish_seen:
            return
        buffer = self._buffers.pop(marker.candidate, None)
        previous_reason = (
            finalized.terminal_reason if finalized is not None else None
        )
        if previous_reason is not None:
            terminal_reason = previous_reason
        elif dropped:
            terminal_reason = "dropped"
        elif buffer is None or buffer.audio_ms < self._config.minimum_audio_ms:
            terminal_reason = "insufficient"
        else:
            terminal_reason = "sufficient"
        self._mark_finalized(
            marker.candidate,
            finish_seen=True,
            terminal_reason=terminal_reason,
        )
        self._metrics.finished_candidate_count += 1
        if terminal_reason == "dropped":
            if previous_reason != "dropped":
                self._metrics.dropped_candidate_count += 1
            if buffer is not None:
                self._metrics.dropped_audio_ms += buffer.audio_ms
            return
        if terminal_reason == "insufficient":
            self._metrics.insufficient_candidate_count += 1

    def _mark_finalized(
        self,
        candidate: Hashable,
        *,
        finish_seen: bool,
        terminal_reason: Literal["sufficient", "insufficient", "dropped"],
    ) -> None:
        previous = self._finalized.pop(candidate, None)
        self._finalized[candidate] = _FinalizedCandidate(
            finish_seen=(
                finish_seen or (previous.finish_seen if previous is not None else False)
            ),
            terminal_reason=(
                previous.terminal_reason if previous is not None else terminal_reason
            ),
        )
        while len(self._finalized) > self._config.finalized_candidate_capacity:
            self._finalized.popitem(last=False)

    async def _ensure_backend(self) -> SpeakerShadowBackend | None:
        if self._backend is not None:
            return self._backend
        if time.monotonic() < self._next_load_attempt_at:
            self._metrics.load_retry_suppressed_count += 1
            return None
        if self._backend_factory is None:
            self._record_load_failure()
            return None

        def build_and_load() -> tuple[SpeakerShadowBackend, bool]:
            backend = self._backend_factory()
            return backend, bool(backend.load())

        started = time.perf_counter()
        backend: SpeakerShadowBackend | None = None
        try:
            backend, available = await asyncio.to_thread(build_and_load)
            if not available:
                await asyncio.to_thread(backend.close)
                self._record_load_failure()
                return None
        except asyncio.CancelledError:
            raise
        except Exception:
            if backend is not None:
                await asyncio.to_thread(backend.close)
            self._record_load_failure()
            return None
        finally:
            self._metrics.load_ms += int((time.perf_counter() - started) * 1_000)
        self._backend = backend
        self._load_failure_streak = 0
        self._next_load_attempt_at = 0.0
        self._metrics.load_count += 1
        return backend

    def _record_load_failure(self) -> None:
        self._load_failure_streak += 1
        retry_seconds = min(
            self._config.load_retry_max_seconds,
            self._config.load_retry_initial_seconds
            * (2 ** (self._load_failure_streak - 1)),
        )
        self._next_load_attempt_at = time.monotonic() + retry_seconds
        self._metrics.load_failure_count += 1

    async def _unload_backend(self) -> None:
        backend, self._backend = self._backend, None
        if backend is None:
            return
        try:
            await asyncio.to_thread(backend.close)
        except Exception:
            pass
        for key, value in self._read_backend_metrics(backend).items():
            self._backend_metrics[key] = self._backend_metrics.get(key, 0) + value
        self._metrics.unload_count += 1
        self._load_failure_streak = 0
        self._next_load_attempt_at = 0.0

    @staticmethod
    def _read_backend_metrics(backend: SpeakerShadowBackend | None) -> dict[str, int]:
        if backend is None:
            return {}
        snapshot = getattr(backend, "snapshot", None)
        if not callable(snapshot):
            return {}
        try:
            values = snapshot()
        except Exception:
            return {}
        if not isinstance(values, dict):
            return {}
        return {
            str(key): value
            for key, value in values.items()
            if isinstance(key, str) and isinstance(value, int) and not isinstance(value, bool)
        }

    async def _cleanup_after_worker(
        self,
        worker: asyncio.Task[None] | None,
    ) -> None:
        if worker is not None:
            await asyncio.gather(worker, return_exceptions=True)
        await self._unload_backend()
        close_factory = getattr(self._backend_factory, "close", None)
        if callable(close_factory):
            try:
                await asyncio.to_thread(close_factory)
            except Exception:
                pass

    @staticmethod
    def _consume_cleanup_result(task: asyncio.Task[None]) -> None:
        try:
            task.exception()
        except asyncio.CancelledError:
            return
