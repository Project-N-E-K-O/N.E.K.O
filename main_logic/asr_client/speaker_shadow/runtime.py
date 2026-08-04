"""Bounded, fail-open runtime for observation-only speaker scoring."""

from __future__ import annotations

import asyncio
import inspect
import math
import multiprocessing
import time
from collections import OrderedDict
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any, Literal

from .contracts import (
    MAX_SPEAKER_SHADOW_CANDIDATE_PCM_BYTES,
    MAX_SPEAKER_SHADOW_FRAME_PCM_BYTES,
    MAX_SPEAKER_SHADOW_RETAINED_PCM_BYTES,
    ObservationCallback,
    SpeakerShadowBackend,
    SpeakerShadowBackendFactory,
    SpeakerShadowCandidateKey,
    SpeakerShadowConfig,
    SpeakerShadowMetrics,
    SpeakerShadowObservation,
    SpeakerShadowScope,
    SPEAKER_SHADOW_SAMPLE_RATE_HZ,
    SpeakerShadowTerminalReason,
)

_HOST_POLL_INTERVAL_SECONDS = 0.005
_HostOperation = Literal["load", "score", "close"]


class _BackendHostError(RuntimeError):
    pass


class _BackendHostTimeout(_BackendHostError):
    pass


def _backend_host_error_name(exc: BaseException) -> str:
    """Return a non-sensitive error identity safe to cross the process pipe."""

    return type(exc).__name__


def _backend_host_main(
    factory: SpeakerShadowBackendFactory,
    connection: Connection,
    pcm_buffer: Any,
) -> None:
    """Own one blocking backend session inside a killable spawn process."""

    backend: SpeakerShadowBackend | None = None
    factory_closed = False

    def close_owned_resources() -> str | None:
        nonlocal backend, factory_closed
        error_name: str | None = None
        if backend is not None:
            owned_backend, backend = backend, None
            try:
                owned_backend.close()
            except BaseException as exc:  # process boundary must contain backend faults
                error_name = _backend_host_error_name(exc)
        close_factory = getattr(factory, "close", None)
        if not factory_closed and callable(close_factory):
            factory_closed = True
            try:
                close_factory()
            except BaseException as exc:  # process boundary must contain factory faults
                error_name = error_name or _backend_host_error_name(exc)
        return error_name

    try:
        while True:
            try:
                message = connection.recv()
            except EOFError:
                return
            operation = message[0]
            try:
                if operation == "load":
                    if backend is None:
                        backend = factory()
                    connection.send((True, bool(backend.load())))
                    continue
                if operation == "score":
                    if backend is None:
                        raise RuntimeError("backend is not loaded")
                    pcm_length = int(message[1])
                    sample_rate_hz = int(message[2])
                    pcm16 = bytearray(
                        memoryview(pcm_buffer).cast("B")[:pcm_length]
                    )
                    try:
                        similarity = float(backend.score(bytes(pcm16), sample_rate_hz))
                    finally:
                        pcm16[:] = b"\x00" * len(pcm16)
                        del pcm16
                    connection.send((True, similarity))
                    continue
                if operation == "close":
                    error_name = close_owned_resources()
                    connection.send((error_name is None, error_name))
                    return
                raise RuntimeError("unsupported backend-host operation")
            except BaseException as exc:  # backend faults stay inside this process
                try:
                    connection.send((False, _backend_host_error_name(exc)))
                except (BrokenPipeError, EOFError, OSError):
                    return
    finally:
        close_owned_resources()
        connection.close()


class _BackendProcessHost:
    """One serial spawn-process host for one backend session."""

    def __init__(
        self,
        *,
        factory: SpeakerShadowBackendFactory,
        terminate_timeout_seconds: float,
    ) -> None:
        context = multiprocessing.get_context("spawn")
        parent_connection, child_connection = context.Pipe(duplex=True)
        pcm_buffer = context.RawArray("B", MAX_SPEAKER_SHADOW_CANDIDATE_PCM_BYTES)
        process = context.Process(
            target=_backend_host_main,
            args=(factory, child_connection, pcm_buffer),
            name="speaker-shadow-backend",
            daemon=True,
        )
        self._connection: Connection | None = parent_connection
        self._child_connection: Connection | None = child_connection
        self._pcm_buffer = pcm_buffer
        self._process: BaseProcess | None = process
        self._terminate_timeout_seconds = terminate_timeout_seconds
        self.loaded = False
        self.was_terminated = False
        self.timed_out = False
        self.pcm_bytes_in_use = 0

    @classmethod
    def create_started(
        cls,
        *,
        factory: SpeakerShadowBackendFactory,
        terminate_timeout_seconds: float,
    ) -> _BackendProcessHost:
        """Construct IPC resources and spawn outside the asyncio event loop."""

        host = cls(
            factory=factory,
            terminate_timeout_seconds=terminate_timeout_seconds,
        )
        host.start()
        return host

    @property
    def alive(self) -> bool:
        process = self._process
        return process is not None and process.is_alive()

    @property
    def process_count(self) -> int:
        return int(self.alive)

    def start(self) -> None:
        process = self._process
        child_connection = self._child_connection
        if process is None or child_connection is None:
            raise _BackendHostError("backend host is already closed")
        try:
            process.start()
        except BaseException:
            self._dispose_handles()
            raise
        finally:
            child_connection.close()
            self._child_connection = None

    async def load(self, *, timeout_seconds: float) -> bool:
        available = bool(
            await self._request("load", timeout_seconds=timeout_seconds)
        )
        self.loaded = available
        return available

    async def score(
        self,
        pcm16: bytes | bytearray,
        *,
        timeout_seconds: float,
    ) -> float:
        if len(pcm16) > MAX_SPEAKER_SHADOW_CANDIDATE_PCM_BYTES:
            raise _BackendHostError("candidate PCM exceeds host buffer")
        if self._pcm_buffer is None:
            raise _BackendHostError("backend host PCM buffer is closed")
        pcm_view = memoryview(self._pcm_buffer).cast("B")
        pcm_view[: len(pcm16)] = pcm16
        self.pcm_bytes_in_use = len(pcm16)
        try:
            return float(
                await self._request(
                    "score",
                    len(pcm16),
                    SPEAKER_SHADOW_SAMPLE_RATE_HZ,
                    timeout_seconds=timeout_seconds,
                )
            )
        finally:
            pcm_view[: len(pcm16)] = b"\x00" * len(pcm16)
            self.pcm_bytes_in_use = 0

    async def close(self, *, timeout_seconds: float) -> bool:
        success = True
        if self.alive:
            try:
                await self._request("close", timeout_seconds=timeout_seconds)
            except _BackendHostError:
                success = False
        self.loaded = False
        if self.alive and not await self._wait_for_exit(timeout_seconds):
            success = False
            await self.terminate()
        await asyncio.to_thread(self._dispose_handles)
        return success

    async def terminate(self) -> None:
        process = self._process
        self.loaded = False
        if process is None:
            await asyncio.to_thread(self._dispose_handles)
            return
        if process.is_alive():
            self.was_terminated = True
            process.terminate()
            if not await self._wait_for_exit(self._terminate_timeout_seconds):
                process.kill()
                if not await self._wait_for_exit(self._terminate_timeout_seconds):
                    raise _BackendHostError("backend host could not be terminated")
        await asyncio.to_thread(self._dispose_handles)

    async def _request(
        self,
        operation: _HostOperation,
        *payload: object,
        timeout_seconds: float,
    ) -> object:
        connection = self._connection
        process = self._process
        if connection is None or process is None or not process.is_alive():
            await asyncio.to_thread(self._dispose_handles)
            raise _BackendHostError("backend host is not alive")
        try:
            connection.send((operation, *payload))
        except (BrokenPipeError, EOFError, OSError) as exc:
            await self.terminate()
            raise _BackendHostError("backend host command failed") from exc

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        try:
            while True:
                if connection.poll(0.0):
                    try:
                        succeeded, value = connection.recv()
                    except (BrokenPipeError, EOFError, OSError) as exc:
                        await self.terminate()
                        raise _BackendHostError(
                            "backend host response failed"
                        ) from exc
                    if succeeded:
                        return value
                    raise _BackendHostError(f"backend operation failed: {value}")
                if not process.is_alive():
                    await asyncio.to_thread(self._dispose_handles)
                    raise _BackendHostError("backend host exited without a response")
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    self.timed_out = True
                    await self.terminate()
                    raise _BackendHostTimeout(f"backend {operation} timed out")
                await asyncio.sleep(min(_HOST_POLL_INTERVAL_SECONDS, remaining))
        except asyncio.CancelledError:
            await self.terminate()
            raise

    async def _wait_for_exit(self, timeout_seconds: float) -> bool:
        process = self._process
        if process is None:
            return True
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while process.is_alive():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(_HOST_POLL_INTERVAL_SECONDS, remaining))
        await asyncio.to_thread(process.join, 0)
        return True

    def _dispose_handles(self) -> None:
        for connection_name in ("_connection", "_child_connection"):
            connection = getattr(self, connection_name)
            if connection is not None:
                try:
                    connection.close()
                except OSError:
                    pass
                setattr(self, connection_name, None)
        process = self._process
        if process is not None and not process.is_alive():
            # ``Process.join()`` raises when ``start()`` failed before a PID was
            # assigned, which would otherwise mask the original spawn error.
            if process.pid is not None:
                process.join(timeout=0)
            try:
                process.close()
            except ValueError:
                pass
            self._process = None
        pcm_buffer = self._pcm_buffer
        if pcm_buffer is not None:
            pcm_view = memoryview(pcm_buffer).cast("B")
            pcm_view[:] = b"\x00" * len(pcm_view)
            self._pcm_buffer = None
            self.pcm_bytes_in_use = 0


@dataclass(slots=True)
class _AudioFrame:
    generation: int
    candidate: SpeakerShadowCandidateKey
    token: _CandidateToken
    pcm16: bytearray
    sample_rate_hz: int
    sample_count: int


@dataclass(frozen=True, slots=True)
class _CandidateFinished:
    generation: int
    candidate: SpeakerShadowCandidateKey
    token: _CandidateToken


@dataclass(slots=True)
class _CandidateToken:
    candidate: SpeakerShadowCandidateKey
    sample_rate_hz: int
    accepted_sample_count: int = 0
    terminal_reason: SpeakerShadowTerminalReason | None = None
    finish_seen: bool = False


@dataclass(slots=True)
class _CandidateBuffer:
    token: _CandidateToken
    sample_rate_hz: int
    pcm16: bytearray
    sample_count: int = 0
    next_checkpoint_index: int = 0

    @property
    def audio_ms(self) -> int:
        return self.sample_count * 1_000 // self.sample_rate_hz


@dataclass(frozen=True, slots=True)
class _FinalizedCandidate:
    finish_seen: bool
    terminal_reason: SpeakerShadowTerminalReason
    token: _CandidateToken | None = None


_STOP = object()
_QueueItem = _AudioFrame | _CandidateFinished | object


class SpeakerShadowRuntime:
    """Score accepted candidate PCM without controlling the ASR path.

    ``submit`` and ``finish_candidate`` are non-blocking. Queue pressure and all
    backend/callback failures terminate shadow work locally and never escape to
    the ASR task graph. Observation callbacks are cancellation-cooperative;
    shutdown uses bounded repeated cancellation so a callback can finish cleanup
    after consuming its first cancellation request.
    """

    def __init__(
        self,
        *,
        backend_factory: SpeakerShadowBackendFactory | None,
        config: SpeakerShadowConfig | None = None,
        on_observation: ObservationCallback | None = None,
    ) -> None:
        self._config = config or SpeakerShadowConfig()
        self._backend_factory = backend_factory
        if on_observation is not None and not (
            inspect.iscoroutinefunction(on_observation)
            or inspect.iscoroutinefunction(getattr(on_observation, "__call__", None))
        ):
            raise TypeError("SpeakerShadowRuntime observation callback must be async")
        self._on_observation = on_observation
        self._metrics = SpeakerShadowMetrics()
        self._would_block_counts = {
            threshold: 0 for threshold in self._config.similarity_thresholds
        }
        self._queue: asyncio.Queue[_QueueItem] = asyncio.Queue(
            maxsize=self._config.queue_capacity
        )
        self._queued_pcm_bytes = 0
        self._queued_audio_tail: _AudioFrame | None = None
        self._active_pcm_bytes = 0
        self._buffers: OrderedDict[SpeakerShadowCandidateKey, _CandidateBuffer] = (
            OrderedDict()
        )
        self._finalized: OrderedDict[
            SpeakerShadowCandidateKey, _FinalizedCandidate
        ] = OrderedDict()
        self._finalized_through: dict[SpeakerShadowScope, tuple[int, int]] = {}
        self._candidate_tokens: OrderedDict[
            SpeakerShadowCandidateKey, _CandidateToken
        ] = OrderedDict()
        self._worker_task: asyncio.Task[None] | None = None
        self._callback_task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._host_start_task: asyncio.Task[_BackendProcessHost] | None = None
        self._active_evaluation: tuple[int, SpeakerShadowCandidateKey] | None = None
        self._active_evaluation_terminal = False
        self._backend_host: _BackendProcessHost | None = None
        self._load_failure_streak = 0
        self._next_load_attempt_at = 0.0
        self._generation = 0
        self._closed = False
        self._factory_closed = False

    @property
    def enabled(self) -> bool:
        """Whether submissions can do work.

        A missing factory is treated exactly like disabled configuration: no
        PCM is queued and no task or model-loading attempt is created.
        """

        return (
            self._config.enabled
            and self._backend_factory is not None
            and not self._closed
        )

    @property
    def generation(self) -> int:
        return self._generation

    def snapshot(self) -> dict[str, int]:
        buffered_audio_bytes = sum(
            len(buffer.pcm16) for buffer in self._buffers.values()
        )
        host_pcm_bytes = (
            self._backend_host.pcm_bytes_in_use
            if self._backend_host is not None
            else 0
        )
        snapshot = self._metrics.snapshot()
        snapshot.update(
            buffered_candidate_count=len(self._buffers),
            buffered_audio_bytes=buffered_audio_bytes,
            queued_audio_bytes=self._queued_pcm_bytes,
            active_audio_bytes=self._active_pcm_bytes,
            retained_pcm_bytes=(
                buffered_audio_bytes
                + self._queued_pcm_bytes
                + self._active_pcm_bytes
                + host_pcm_bytes
            ),
            finalized_tombstone_count=len(self._finalized),
            queued_item_count=self._queue.qsize(),
            in_flight_candidate_count=int(self._active_evaluation is not None),
            worker_task_count=int(
                self._worker_task is not None and not self._worker_task.done()
            ),
            callback_task_count=int(
                self._callback_task is not None and not self._callback_task.done()
            ),
            cleanup_task_count=int(
                self._cleanup_task is not None and not self._cleanup_task.done()
            ),
            host_start_task_count=int(
                self._host_start_task is not None
                and not self._host_start_task.done()
            ),
            backend_loaded_count=int(
                self._backend_host is not None
                and self._backend_host.alive
                and self._backend_host.loaded
            ),
            backend_process_count=(
                self._backend_host.process_count
                if self._backend_host is not None
                else 0
            ),
            backend_close_failed_count=0,
        )
        snapshot.update(
            {
                self._threshold_metric_key(threshold): count
                for threshold, count in self._would_block_counts.items()
            }
        )
        return snapshot

    def submit(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
        candidate: SpeakerShadowCandidateKey,
    ) -> bool:
        """Queue immutable PCM accepted by the current candidate fence."""

        if not self.enabled:
            return False
        if not isinstance(candidate, SpeakerShadowCandidateKey):
            return False
        if not isinstance(pcm16, bytes) or not pcm16 or len(pcm16) % 2:
            return False
        if sample_rate_hz != SPEAKER_SHADOW_SAMPLE_RATE_HZ:
            self._metrics.dropped_frame_count += 1
            return False
        if len(pcm16) > MAX_SPEAKER_SHADOW_FRAME_PCM_BYTES:
            self._metrics.dropped_frame_count += 1
            self._metrics.dropped_audio_ms += self._audio_ms(
                len(pcm16) // 2,
                SPEAKER_SHADOW_SAMPLE_RATE_HZ,
            )
            self._drop_candidate(candidate)
            return False

        identity = (self._generation, candidate)
        if (
            candidate in self._finalized
            or self._candidate_was_evicted(candidate)
            or (
                identity == self._active_evaluation
                and self._active_evaluation_terminal
            )
        ):
            return False

        token = self._candidate_tokens.get(candidate)
        if token is not None and token.sample_rate_hz != sample_rate_hz:
            self._metrics.dropped_frame_count += 1
            self._metrics.dropped_audio_ms += self._audio_ms(
                len(pcm16) // 2,
                sample_rate_hz,
            )
            self._drop_candidate(candidate, token=token)
            return False
        if token is None:
            token = _CandidateToken(candidate, sample_rate_hz)
        accepted_sample_count = token.accepted_sample_count
        maximum_samples = (
            sample_rate_hz * self._config.maximum_audio_ms // 1_000
        )
        remaining_samples = maximum_samples - accepted_sample_count
        if remaining_samples <= 0:
            self._metrics.dropped_frame_count += 1
            self._metrics.dropped_audio_ms += self._audio_ms(
                len(pcm16) // 2,
                sample_rate_hz,
            )
            return False
        input_sample_count = len(pcm16) // 2
        sample_count = min(input_sample_count, remaining_samples)
        if sample_count <= 0:
            return False
        if sample_count < input_sample_count:
            self._metrics.dropped_audio_ms += self._audio_ms(
                input_sample_count - sample_count,
                sample_rate_hz,
            )
        bounded_pcm16 = bytearray(memoryview(pcm16)[: sample_count * 2])
        frame = _AudioFrame(
            generation=self._generation,
            candidate=candidate,
            token=token,
            pcm16=bounded_pcm16,
            sample_rate_hz=sample_rate_hz,
            sample_count=sample_count,
        )
        if self._retained_pcm_bytes() + len(bounded_pcm16) > (
            MAX_SPEAKER_SHADOW_RETAINED_PCM_BYTES
        ):
            self._metrics.dropped_frame_count += 1
            self._metrics.dropped_audio_ms += self._audio_ms(
                sample_count,
                sample_rate_hz,
            )
            self._wipe_bytearray(bounded_pcm16)
            self._drop_candidate(candidate, token=token)
            return False
        queued_tail = self._queued_audio_tail
        coalesce_tail = bool(
            self._active_evaluation == identity
            and not self._active_evaluation_terminal
            and queued_tail is not None
            and queued_tail.generation == frame.generation
            and queued_tail.candidate == frame.candidate
            and queued_tail.token is frame.token
            and queued_tail.sample_rate_hz == frame.sample_rate_hz
        )
        if coalesce_tail:
            assert queued_tail is not None
            queued_tail.pcm16.extend(bounded_pcm16)
            queued_tail.sample_count += sample_count
            self._wipe_bytearray(bounded_pcm16)
        else:
            try:
                self._queue.put_nowait(frame)
            except asyncio.QueueFull:
                self._metrics.dropped_frame_count += 1
                self._metrics.dropped_audio_ms += self._audio_ms(
                    sample_count, sample_rate_hz
                )
                self._wipe_bytearray(bounded_pcm16)
                self._drop_candidate(candidate, token=token)
                return False
            self._queued_audio_tail = (
                frame
                if self._active_evaluation == identity
                and not self._active_evaluation_terminal
                else None
            )
        self._queued_pcm_bytes += len(bounded_pcm16)
        if not self._ensure_worker():
            self._drain_queue()
            self._metrics.worker_start_failure_count += 1
            self._metrics.dropped_frame_count += 1
            self._drop_candidate(candidate, token=token)
            return False
        token.accepted_sample_count = accepted_sample_count + sample_count
        self._candidate_tokens[candidate] = token
        self._candidate_tokens.move_to_end(candidate)
        self._metrics.submitted_frame_count += 1
        self._metrics.submitted_audio_ms += self._audio_ms(
            sample_count, sample_rate_hz
        )
        return True

    def finish_candidate(self, candidate: SpeakerShadowCandidateKey) -> bool:
        """Order the terminal boundary behind all previously accepted PCM."""

        if not self.enabled:
            return False
        if not isinstance(candidate, SpeakerShadowCandidateKey):
            return False
        finalized = self._finalized.get(candidate)
        if finalized is not None:
            self._record_finish(candidate, finalized)
            return True
        if self._candidate_was_evicted(candidate):
            return True
        token = self._candidate_tokens.get(candidate)
        if token is None:
            token = _CandidateToken(candidate, 0)
        marker = _CandidateFinished(self._generation, candidate, token)
        self._queued_audio_tail = None
        try:
            self._queue.put_nowait(marker)
        except asyncio.QueueFull:
            self._drop_candidate(
                candidate,
                finish_seen=True,
                token=token,
            )
            return False
        if not self._ensure_worker():
            self._drain_queue()
            self._metrics.worker_start_failure_count += 1
            self._drop_candidate(
                candidate,
                finish_seen=True,
                token=token,
            )
            return False
        self._candidate_tokens[candidate] = token
        self._candidate_tokens.move_to_end(candidate)
        return True

    async def wait_idle(self) -> None:
        """Wait for accepted work, excluding the warm-backend idle timer."""

        await self._queue.join()

    async def reset(self) -> None:
        """Invalidate queued/in-flight results while retaining a warm backend."""

        if self._closed:
            return
        self._generation += 1
        self._cancel_observation_callback()
        self._clear_buffers()
        self._retire_finalized_candidates()
        self._candidate_tokens.clear()
        self._load_failure_streak = 0
        self._next_load_attempt_at = 0.0
        self._drain_queue()

    async def close(self) -> None:
        """Stop accepting work and release every tracked resource exactly once.

        Blocking backend calls live only in the dedicated spawn process. If the
        serial worker misses its grace period, cleanup terminates that process
        before joining the worker, so close has a hard resource boundary.
        """

        if not self._closed:
            self._closed = True
            self._generation += 1
            self._cancel_observation_callback()
            self._clear_buffers()
            self._finalized.clear()
            self._candidate_tokens.clear()
            self._drain_queue()
            worker = self._worker_task
            if worker is not None and not worker.done():
                self._queue.put_nowait(_STOP)
            needs_cleanup = (
                worker is not None
                or self._backend_host is not None
                or self._host_start_task is not None
            )
            if needs_cleanup:
                cleanup = asyncio.create_task(
                    self._cleanup_after_worker(worker),
                    name="speaker-shadow-cleanup",
                )
                self._cleanup_task = cleanup
                cleanup.add_done_callback(self._consume_cleanup_result)
        cleanup = self._cleanup_task
        if cleanup is None:
            self._close_parent_factory()
            return
        await asyncio.shield(cleanup)

    def _ensure_worker(self) -> bool:
        if self._worker_task is not None and not self._worker_task.done():
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        worker = loop.create_task(
            self._run(), name="speaker-shadow-runtime"
        )
        worker.add_done_callback(self._consume_worker_result)
        self._worker_task = worker
        return True

    async def _run(self) -> None:
        while True:
            try:
                work_items = self._queue
                item = await asyncio.wait_for(
                    work_items.get(),
                    timeout=self._config.idle_unload_seconds,
                )
                if item is self._queued_audio_tail:
                    self._queued_audio_tail = None
            except asyncio.TimeoutError:
                await self._unload_backend()
                if self._queue.empty():
                    return
                continue
            try:
                if item is _STOP:
                    return
                if isinstance(item, _CandidateFinished):
                    self._process_finish(item)
                else:
                    assert isinstance(item, _AudioFrame)
                    await self._process_frame(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                # A defensive final fence: shadow errors never reach ASR.
                self._metrics.inference_failure_count += 1
                if isinstance(item, (_AudioFrame, _CandidateFinished)):
                    self._finalize_candidate(
                        item.candidate,
                        "failed",
                        token=item.token,
                    )
            finally:
                if isinstance(item, _AudioFrame):
                    self._queued_pcm_bytes = max(
                        0,
                        self._queued_pcm_bytes - len(item.pcm16),
                    )
                    self._wipe_bytearray(item.pcm16)
                self._queue.task_done()
                item = None
            if self._queue.empty() and self._backend_host is None:
                return

    async def _process_frame(self, frame: _AudioFrame) -> None:
        if frame.generation != self._generation:
            return
        if (
            frame.token.terminal_reason is not None
            or frame.candidate in self._finalized
            or self._candidate_was_evicted(
                frame.candidate,
                token=frame.token,
            )
        ):
            return
        buffer = self._buffers.get(frame.candidate)
        if buffer is None:
            if len(self._buffers) >= self._config.buffered_candidate_capacity:
                dropped_candidate, dropped_buffer = self._buffers.popitem(last=False)
                self._metrics.dropped_audio_ms += dropped_buffer.audio_ms
                self._wipe_bytearray(dropped_buffer.pcm16)
                self._finalize_candidate(
                    dropped_candidate,
                    "dropped",
                    token=dropped_buffer.token,
                )
            buffer = _CandidateBuffer(
                token=frame.token,
                sample_rate_hz=frame.sample_rate_hz,
                pcm16=bytearray(),
            )
            self._buffers[frame.candidate] = buffer
            self._metrics.started_candidate_count += 1
        elif buffer.token is not frame.token:
            return
        elif buffer.sample_rate_hz != frame.sample_rate_hz:
            self._buffers.pop(frame.candidate, None)
            self._wipe_bytearray(buffer.pcm16)
            self._finalize_candidate(
                frame.candidate,
                "failed",
                token=frame.token,
            )
            return
        else:
            self._buffers.move_to_end(frame.candidate)

        maximum_samples = (
            buffer.sample_rate_hz * self._config.maximum_audio_ms // 1_000
        )
        allowed_samples = min(
            frame.sample_count,
            maximum_samples - buffer.sample_count,
        )
        if allowed_samples > 0:
            buffer.pcm16.extend(frame.pcm16[: allowed_samples * 2])
            buffer.sample_count += allowed_samples
        explicit_checkpoints = self._config.observation_checkpoints_ms
        checkpoints = explicit_checkpoints or (self._config.minimum_audio_ms,)
        while buffer.next_checkpoint_index < len(checkpoints):
            checkpoint_index = buffer.next_checkpoint_index
            checkpoint_ms = checkpoints[checkpoint_index]
            checkpoint_samples = math.ceil(
                buffer.sample_rate_hz * checkpoint_ms / 1_000
            )
            if buffer.sample_count < checkpoint_samples:
                return

            terminal = checkpoint_index == len(checkpoints) - 1
            buffer.next_checkpoint_index += 1
            score_sample_count = (
                buffer.sample_count
                if explicit_checkpoints is None
                else checkpoint_samples
            )
            candidate_pcm = bytearray(
                buffer.pcm16[: score_sample_count * 2]
            )
            if terminal:
                self._buffers.pop(frame.candidate, None)
                self._wipe_bytearray(buffer.pcm16)
            try:
                await self._evaluate_candidate(
                    generation=frame.generation,
                    candidate=frame.candidate,
                    token=frame.token,
                    pcm16=candidate_pcm,
                    sample_rate_hz=buffer.sample_rate_hz,
                    audio_ms=(
                        buffer.audio_ms
                        if explicit_checkpoints is None
                        else checkpoint_ms
                    ),
                    checkpoint_ms=(
                        checkpoint_ms
                        if explicit_checkpoints is not None
                        else None
                    ),
                    terminal=terminal,
                )
            finally:
                self._wipe_bytearray(candidate_pcm)
            if (
                not terminal
                and frame.token.terminal_reason is not None
                and self._buffers.get(frame.candidate) is buffer
            ):
                self._buffers.pop(frame.candidate, None)
                self._wipe_bytearray(buffer.pcm16)
            if not self._identity_is_current(
                frame.generation,
                frame.candidate,
                frame.token,
            ):
                return

    async def _evaluate_candidate(
        self,
        *,
        generation: int,
        candidate: SpeakerShadowCandidateKey,
        token: _CandidateToken,
        pcm16: bytearray,
        sample_rate_hz: int,
        audio_ms: int,
        checkpoint_ms: int | None,
        terminal: bool,
    ) -> None:
        self._active_evaluation = (generation, candidate)
        self._active_evaluation_terminal = terminal
        self._active_pcm_bytes = len(pcm16)
        try:
            backend_host = await self._ensure_backend()
            if not self._identity_is_current(generation, candidate, token):
                self._metrics.stale_result_count += 1
                return
            if backend_host is None:
                self._finalize_candidate(candidate, "failed", token=token)
                return
            started = time.perf_counter()
            try:
                similarity = float(
                    await backend_host.score(
                        pcm16,
                        timeout_seconds=self._config.backend_score_timeout_seconds,
                    )
                )
                if not math.isfinite(similarity) or not -1.0 <= similarity <= 1.0:
                    raise ValueError(
                        "speaker cosine similarity must be within [-1, 1]"
                    )
            except asyncio.CancelledError:
                raise
            except _BackendHostTimeout:
                self._metrics.backend_timeout_count += 1
                self._discard_backend_host(backend_host)
                self._metrics.inference_failure_count += 1
                if self._identity_is_current(generation, candidate, token):
                    self._finalize_candidate(candidate, "failed", token=token)
                return
            except Exception:
                if not backend_host.alive:
                    self._discard_backend_host(backend_host)
                self._metrics.inference_failure_count += 1
                if self._identity_is_current(generation, candidate, token):
                    self._finalize_candidate(candidate, "failed", token=token)
                return
            finally:
                self._metrics.inference_ms += int(
                    (time.perf_counter() - started) * 1_000
                )
            if not self._identity_is_current(generation, candidate, token):
                self._metrics.stale_result_count += 1
                return

            would_block = tuple(
                (threshold, similarity < threshold)
                for threshold in self._config.similarity_thresholds
            )
            if terminal:
                self._finalize_candidate(candidate, "scored", token=token)
                self._metrics.evaluated_candidate_count += 1
            if any(blocked for _, blocked in would_block):
                self._metrics.would_block_count += 1
            for threshold, blocked in would_block:
                if blocked:
                    self._would_block_counts[threshold] += 1
            callback = self._on_observation
            if callback is None:
                return
            existing_callback_task = self._callback_task
            if existing_callback_task is not None:
                if not existing_callback_task.done():
                    self._metrics.callback_failure_count += 1
                    return
                self._consume_callback_result(existing_callback_task)
            observation = SpeakerShadowObservation(
                candidate=candidate,
                similarity=similarity,
                would_block=would_block,
                audio_ms=audio_ms,
                checkpoint_ms=checkpoint_ms,
            )
            callback_task = asyncio.create_task(
                callback(observation),
                name="speaker-shadow-observation",
            )
            self._callback_task = callback_task
            callback_task.add_done_callback(self._consume_callback_result)
            try:
                done, _ = await asyncio.wait(
                    {callback_task},
                    timeout=self._config.callback_timeout_seconds,
                )
            except asyncio.CancelledError:
                await self._cancel_callback_bounded(callback_task)
                raise
            if not done:
                self._metrics.callback_failure_count += 1
                await self._cancel_callback_bounded(callback_task)
                return
            try:
                callback_task.result()
            except asyncio.CancelledError:
                self._metrics.stale_result_count += 1
            except Exception:
                self._metrics.callback_failure_count += 1
        finally:
            if self._active_evaluation == (generation, candidate):
                self._active_evaluation = None
                self._active_evaluation_terminal = False
                self._active_pcm_bytes = 0

    async def _ensure_backend(self) -> _BackendProcessHost | None:
        existing_host = self._backend_host
        if (
            existing_host is not None
            and existing_host.alive
            and existing_host.loaded
        ):
            return existing_host
        if existing_host is not None:
            self._discard_backend_host(existing_host)
        if time.monotonic() < self._next_load_attempt_at:
            self._metrics.load_retry_suppressed_count += 1
            return None
        factory = self._backend_factory
        if factory is None:
            return None

        started = time.perf_counter()
        start_task = asyncio.create_task(
            asyncio.to_thread(
                _BackendProcessHost.create_started,
                factory=factory,
                terminate_timeout_seconds=(
                    self._config.process_terminate_timeout_seconds
                ),
            ),
            name="speaker-shadow-host-start",
        )
        self._host_start_task = start_task
        host: _BackendProcessHost | None = None
        try:
            try:
                host = await asyncio.shield(start_task)
            except asyncio.CancelledError:
                # ``to_thread`` cannot stop an in-progress ``Process.start``.
                # Keep ownership across repeated worker cancellations so a host
                # that finishes starting after shutdown is always retrieved and
                # terminated by the outer cancellation handler.
                while not start_task.done():
                    try:
                        await asyncio.shield(start_task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if start_task.done() and not start_task.cancelled():
                    try:
                        host = start_task.result()
                    except Exception:
                        host = None
                raise
            available = await host.load(
                timeout_seconds=self._config.backend_load_timeout_seconds
            )
            if not available:
                await self._close_host(host)
                self._record_load_failure()
                return None
        except asyncio.CancelledError:
            if host is not None:
                await self._terminate_host(host)
            raise
        except _BackendHostTimeout:
            self._metrics.backend_timeout_count += 1
            if host is not None:
                self._record_host_termination(host)
            self._record_load_failure()
            return None
        except Exception:
            if host is not None:
                await self._close_host(host)
            self._record_load_failure()
            return None
        finally:
            if self._host_start_task is start_task:
                self._host_start_task = None
            self._metrics.load_ms += int(
                (time.perf_counter() - started) * 1_000
            )
        assert host is not None
        self._backend_host = host
        self._load_failure_streak = 0
        self._next_load_attempt_at = 0.0
        self._metrics.load_count += 1
        return host

    def _record_load_failure(self) -> None:
        self._load_failure_streak += 1
        retry_seconds = self._config.load_retry_initial_seconds
        for _ in range(self._load_failure_streak - 1):
            if retry_seconds >= self._config.load_retry_max_seconds:
                break
            retry_seconds = min(
                self._config.load_retry_max_seconds,
                retry_seconds * 2,
            )
        self._next_load_attempt_at = time.monotonic() + retry_seconds
        self._metrics.load_failure_count += 1

    async def _unload_backend(self) -> bool:
        host = self._backend_host
        if host is None:
            return True
        closed = await self._close_host(host)
        if self._backend_host is host:
            self._backend_host = None
        if closed:
            self._metrics.unload_count += 1
        else:
            self._metrics.unload_failure_count += 1
        self._load_failure_streak = 0
        self._next_load_attempt_at = 0.0
        return closed

    async def _close_host(self, host: _BackendProcessHost) -> bool:
        try:
            closed = await host.close(
                timeout_seconds=self._config.backend_close_timeout_seconds
            )
        except Exception:
            await self._terminate_host(host)
            return False
        if host.timed_out:
            self._metrics.backend_timeout_count += 1
        self._record_host_termination(host)
        return closed

    async def _terminate_host(self, host: _BackendProcessHost) -> None:
        try:
            await host.terminate()
        except Exception:
            self._metrics.unload_failure_count += 1
        self._record_host_termination(host)

    def _discard_backend_host(self, host: _BackendProcessHost) -> None:
        if self._backend_host is host:
            self._backend_host = None
        self._record_host_termination(host)

    def _record_host_termination(self, host: _BackendProcessHost) -> None:
        if host.was_terminated:
            self._metrics.backend_process_termination_count += 1
            host.was_terminated = False

    async def _cleanup_after_worker(
        self,
        worker: asyncio.Task[None] | None,
    ) -> None:
        try:
            if worker is not None and not worker.done():
                done, _ = await asyncio.wait(
                    {worker},
                    timeout=self._config.shutdown_grace_seconds,
                )
                if not done:
                    self._metrics.shutdown_timeout_count += 1
                    cancellation_timeout = (
                        self._config.process_terminate_timeout_seconds * 2
                        + _HOST_POLL_INTERVAL_SECONDS
                    )
                    for attempt in range(2):
                        worker.cancel()
                        done, _ = await asyncio.wait(
                            {worker},
                            timeout=cancellation_timeout,
                        )
                        if done:
                            break
                        if attempt == 0:
                            host, self._backend_host = self._backend_host, None
                            if host is not None:
                                await self._terminate_host(host)
                    if not worker.done():
                        # A thread already inside ``Process.start`` cannot be
                        # cancelled. Keep cleanup attached until the worker
                        # retrieves and terminates any host it eventually
                        # returns; close must not leave that ownership orphaned.
                        await asyncio.wait({worker})
            if worker is not None and worker.done():
                self._consume_worker_result(worker)
            await self._cancel_callback_bounded()
        finally:
            try:
                await self._unload_backend()
            finally:
                self._close_parent_factory()

    def _close_parent_factory(self) -> None:
        """Release the parent-owned profile exactly once without exposing it."""

        if self._factory_closed:
            return
        self._factory_closed = True
        close_factory = getattr(self._backend_factory, "close", None)
        if not callable(close_factory):
            return
        try:
            # Factory.close is a parent-memory wipe contract. It must be
            # idempotent and non-blocking; running a copy elsewhere would not
            # clear the parent-owned profile or embedding.
            close_factory()
        except Exception:
            self._metrics.unload_failure_count += 1

    def _process_finish(self, marker: _CandidateFinished) -> None:
        if marker.generation != self._generation:
            return
        if self._candidate_was_evicted(
            marker.candidate,
            token=marker.token,
        ):
            return
        if marker.token.terminal_reason is not None:
            self._record_token_finish(marker.token)
            return
        finalized = self._finalized.get(marker.candidate)
        if finalized is not None:
            self._record_finish(marker.candidate, finalized)
            return
        buffer = self._buffers.pop(marker.candidate, None)
        terminal_reason: SpeakerShadowTerminalReason = "insufficient"
        if buffer is not None:
            if buffer.next_checkpoint_index > 0:
                terminal_reason = "scored"
                self._metrics.evaluated_candidate_count += 1
            self._wipe_bytearray(buffer.pcm16)
        self._finalize_candidate(
            marker.candidate,
            terminal_reason,
            finish_seen=True,
            token=marker.token,
        )

    def _drop_candidate(
        self,
        candidate: SpeakerShadowCandidateKey,
        *,
        finish_seen: bool = False,
        token: _CandidateToken | None = None,
    ) -> None:
        buffer = self._buffers.pop(candidate, None)
        if buffer is not None:
            self._metrics.dropped_audio_ms += buffer.audio_ms
            self._wipe_bytearray(buffer.pcm16)
            if token is None:
                token = buffer.token
        self._finalize_candidate(
            candidate,
            "dropped",
            finish_seen=finish_seen,
            token=token,
        )

    def _finalize_candidate(
        self,
        candidate: SpeakerShadowCandidateKey,
        terminal_reason: SpeakerShadowTerminalReason,
        *,
        finish_seen: bool = False,
        token: _CandidateToken | None = None,
    ) -> None:
        if token is None:
            token = self._candidate_tokens.get(candidate)
        if token is not None and token.terminal_reason is not None:
            if finish_seen:
                self._record_token_finish(token)
            return
        if token is not None:
            token.terminal_reason = terminal_reason
            token.finish_seen = finish_seen
            if self._candidate_tokens.get(candidate) is token:
                self._candidate_tokens.pop(candidate, None)
        previous = self._finalized.pop(candidate, None)
        if previous is not None:
            self._finalized[candidate] = _FinalizedCandidate(
                finish_seen=previous.finish_seen or finish_seen,
                terminal_reason=previous.terminal_reason,
                token=previous.token or token,
            )
            if finish_seen and not previous.finish_seen:
                self._metrics.finished_candidate_count += 1
            return
        self._finalized[candidate] = _FinalizedCandidate(
            finish_seen=finish_seen,
            terminal_reason=terminal_reason,
            token=token,
        )
        if finish_seen:
            self._metrics.finished_candidate_count += 1
        counter_name = f"{terminal_reason}_candidate_count"
        setattr(
            self._metrics,
            counter_name,
            getattr(self._metrics, counter_name) + 1,
        )
        while len(self._finalized) > self._config.finalized_candidate_capacity:
            evicted_candidate, _ = self._finalized.popitem(last=False)
            self._record_evicted_candidate(evicted_candidate)

    def _candidate_was_evicted(
        self,
        candidate: SpeakerShadowCandidateKey,
        *,
        token: _CandidateToken | None = None,
    ) -> bool:
        current_token = self._candidate_tokens.get(candidate)
        buffer = self._buffers.get(candidate)
        if token is None and (
            current_token is not None
            or buffer is not None
            or self._active_evaluation == (self._generation, candidate)
        ):
            return False
        if token is not None and (
            current_token is token
            or (buffer is not None and buffer.token is token)
        ):
            return False
        finalized_through = self._finalized_through.get(candidate.scope)
        if finalized_through is None:
            return False
        return (
            candidate.detector_epoch,
            candidate.shadow_generation,
        ) <= finalized_through

    def _record_evicted_candidate(
        self,
        candidate: SpeakerShadowCandidateKey,
    ) -> None:
        position = (candidate.detector_epoch, candidate.shadow_generation)
        previous = self._finalized_through.get(candidate.scope)
        if previous is None or position > previous:
            self._finalized_through[candidate.scope] = position

    def _retire_finalized_candidates(self) -> None:
        for candidate in self._finalized:
            self._record_evicted_candidate(candidate)
        self._finalized.clear()

    @staticmethod
    def _threshold_metric_key(threshold: float) -> str:
        # ``repr`` is the shortest round-trippable float representation, so
        # distinct configured thresholds cannot collapse into one metric key.
        suffix = (
            repr(threshold)
            .replace("-", "m")
            .replace("+", "p")
            .replace(".", "_")
        )
        return f"would_block_at_{suffix}_count"

    def _record_finish(
        self,
        candidate: SpeakerShadowCandidateKey,
        finalized: _FinalizedCandidate,
    ) -> None:
        if finalized.finish_seen:
            return
        if finalized.token is not None:
            finalized.token.finish_seen = True
        self._finalized.pop(candidate, None)
        self._finalized[candidate] = _FinalizedCandidate(
            finish_seen=True,
            terminal_reason=finalized.terminal_reason,
            token=finalized.token,
        )
        self._metrics.finished_candidate_count += 1

    def _record_token_finish(self, token: _CandidateToken) -> None:
        if token.finish_seen:
            return
        token.finish_seen = True
        finalized = self._finalized.get(token.candidate)
        if finalized is not None:
            self._finalized.pop(token.candidate, None)
            self._finalized[token.candidate] = _FinalizedCandidate(
                finish_seen=True,
                terminal_reason=finalized.terminal_reason,
                token=finalized.token or token,
            )
        self._metrics.finished_candidate_count += 1

    def _identity_is_current(
        self,
        generation: int,
        candidate: SpeakerShadowCandidateKey,
        token: _CandidateToken,
    ) -> bool:
        return (
            generation == self._generation
            and not self._closed
            and candidate not in self._finalized
            and token.terminal_reason is None
            and self._candidate_tokens.get(candidate) is token
        )

    def _drain_queue(self) -> None:
        self._queued_audio_tail = None
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            else:
                if isinstance(item, _AudioFrame):
                    self._queued_pcm_bytes = max(
                        0,
                        self._queued_pcm_bytes - len(item.pcm16),
                    )
                    self._wipe_bytearray(item.pcm16)
                self._queue.task_done()

    def _retained_pcm_bytes(self) -> int:
        host_pcm_bytes = (
            self._backend_host.pcm_bytes_in_use
            if self._backend_host is not None
            else 0
        )
        return (
            self._queued_pcm_bytes
            + sum(len(buffer.pcm16) for buffer in self._buffers.values())
            + self._active_pcm_bytes
            + host_pcm_bytes
        )

    def _clear_buffers(self) -> None:
        for buffer in self._buffers.values():
            self._wipe_bytearray(buffer.pcm16)
        self._buffers.clear()

    @staticmethod
    def _wipe_bytearray(value: bytearray) -> None:
        value[:] = b"\x00" * len(value)

    def _cancel_observation_callback(self) -> None:
        callback_task = self._callback_task
        if callback_task is not None and not callback_task.done():
            callback_task.cancel()

    async def _cancel_callback_bounded(
        self,
        task: asyncio.Task[None] | None = None,
    ) -> bool:
        callback_task = task if task is not None else self._callback_task
        if callback_task is None:
            return True
        for _ in range(2):
            if callback_task.done():
                self._consume_callback_result(callback_task)
                return True
            callback_task.cancel()
            done, _ = await asyncio.wait(
                {callback_task},
                timeout=self._config.callback_timeout_seconds,
            )
            if done:
                self._consume_callback_result(callback_task)
                return True
        return False

    def _consume_callback_result(self, task: asyncio.Task[None]) -> None:
        try:
            task.exception()
        except asyncio.CancelledError:
            pass
        finally:
            if self._callback_task is task and task.done():
                self._callback_task = None

    @staticmethod
    def _consume_worker_result(task: asyncio.Task[None]) -> None:
        try:
            task.exception()
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _audio_ms(sample_count: int, sample_rate_hz: int) -> int:
        return max(1, sample_count * 1_000 // sample_rate_hz)

    @staticmethod
    def _consume_cleanup_result(task: asyncio.Task[None]) -> None:
        try:
            task.exception()
        except asyncio.CancelledError:
            return
