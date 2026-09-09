"""One bounded, generation-fenced execution channel for speaker scoring."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import StrEnum
import math
import time
from typing import Protocol
import uuid


class SpeakerScoringMode(StrEnum):
    STANDARD = "standard"
    SHORT_PROBE = "short_probe"


class SpeakerScoringLane(StrEnum):
    PREWIRE = "prewire"
    SHADOW = "shadow"


class SharedHostResultStatus(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class SharedHostError(RuntimeError):
    pass


class SharedHostClosedError(SharedHostError):
    pass


class SharedHostCapacityError(SharedHostError):
    pass


class SharedHostIdentityError(SharedHostError):
    pass


class SharedHostScoreError(SharedHostError):
    def __init__(self, status: SharedHostResultStatus, error_code: str) -> None:
        super().__init__(error_code)
        self.status = status
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class SpeakerHostIdentity:
    profile_generation: str
    model_generation: str
    config_generation: str

    def __post_init__(self) -> None:
        for name in (
            "profile_generation",
            "model_generation",
            "config_generation",
        ):
            value = getattr(self, name)
            if type(value) is not str or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class HostGenerationReceipt:
    manager_id: str
    host_generation: int
    identity: SpeakerHostIdentity


@dataclass(frozen=True, slots=True)
class PhysicalScoreResponse:
    host_generation: int
    request_id: int
    score: float


class PhysicalSpeakerScoringHost(Protocol):
    """Killable physical process adapter; only the manager may call it."""

    @property
    def process_count(self) -> int: ...

    async def load(self, *, timeout_seconds: float) -> bool: ...

    async def score(
        self,
        pcm16: bytearray,
        *,
        sample_rate_hz: int,
        mode: SpeakerScoringMode,
        host_generation: int,
        request_id: int,
        timeout_seconds: float,
    ) -> PhysicalScoreResponse: ...

    async def close(self, *, timeout_seconds: float) -> bool: ...

    async def terminate(self, *, timeout_seconds: float) -> None: ...


class PhysicalSpeakerScoringHostFactory(Protocol):
    def __call__(
        self,
        identity: SpeakerHostIdentity,
        host_generation: int,
    ) -> Awaitable[PhysicalSpeakerScoringHost]: ...


@dataclass(frozen=True, slots=True)
class SharedScoreReceipt:
    manager_id: str
    host_generation: int
    request_id: int
    identity: SpeakerHostIdentity
    lane: SpeakerScoringLane
    mode: SpeakerScoringMode
    enqueued_at: float
    absolute_deadline: float

    def matches(self, generation: HostGenerationReceipt) -> bool:
        return bool(
            self.manager_id == generation.manager_id
            and self.host_generation == generation.host_generation
            and self.identity == generation.identity
        )


@dataclass(frozen=True, slots=True)
class SharedScoreResult:
    receipt: SharedScoreReceipt
    status: SharedHostResultStatus
    score: float | None = None
    error_code: str | None = None

    def validate_generation(
        self, generation: HostGenerationReceipt
    ) -> SharedScoreResult:
        if self.receipt.matches(generation):
            return self
        return replace(
            self,
            status=SharedHostResultStatus.STALE,
            score=None,
            error_code="host_generation_changed",
        )


@dataclass(slots=True)
class _ScoreJob:
    receipt: SharedScoreReceipt
    pcm16: bytearray | None
    sample_rate_hz: int
    future: asyncio.Future[SharedScoreResult]
    physical_task: asyncio.Task[PhysicalScoreResponse] | None = None
    cancelled: bool = False
    abandoned: bool = False
    pcm_released: bool = False


class SharedSpeakerScoringLease:
    """Generation-bound async scorer for one lane and one input mode."""

    def __init__(
        self,
        manager: SharedSpeakerScoringHostManager,
        generation: HostGenerationReceipt,
        *,
        lane: SpeakerScoringLane,
        mode: SpeakerScoringMode,
        timeout_seconds: float,
    ) -> None:
        self._manager = manager
        self.generation = generation
        self.lane = lane
        self.mode = mode
        self._timeout_seconds = timeout_seconds
        self._closed = False
        self._receipts: set[SharedScoreReceipt] = set()

    @property
    def alive(self) -> bool:
        return bool(
            not self._closed and self._manager.is_generation_current(self.generation)
        )

    @property
    def loaded(self) -> bool:
        return self.alive

    @property
    def process_count(self) -> int:
        if self._closed:
            return 0
        return self._manager.process_count_for(self.generation)

    @property
    def pcm_bytes_in_use(self) -> int:
        if self._closed:
            return 0
        return self._manager.pcm_bytes_in_use_for(self.generation)

    async def score_async(self, pcm16: bytes, sample_rate_hz: int) -> float:
        return await self._score(
            pcm16,
            sample_rate_hz=sample_rate_hz,
            timeout_seconds=self._timeout_seconds,
        )

    async def score(self, pcm16: bytes, *, timeout_seconds: float) -> float:
        return await self._score(
            pcm16,
            sample_rate_hz=16_000,
            timeout_seconds=timeout_seconds,
        )

    async def _score(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
        timeout_seconds: float,
    ) -> float:
        if self._closed:
            raise SharedHostClosedError("lease_closed")
        self._manager._validate_timeout("timeout_seconds", timeout_seconds)
        receipt = self._manager.submit(
            pcm16,
            sample_rate_hz=sample_rate_hz,
            generation=self.generation,
            lane=self.lane,
            mode=self.mode,
            absolute_deadline=self._manager.now() + float(timeout_seconds),
        )
        self._receipts.add(receipt)
        try:
            result = await self._manager.await_result(receipt)
        except asyncio.CancelledError:
            self._manager.cancel(receipt)
            self._manager.abandon(receipt)
            raise
        finally:
            self._receipts.discard(receipt)
        result = result.validate_generation(self.generation)
        if result.status is not SharedHostResultStatus.COMPLETED:
            raise SharedHostScoreError(
                result.status,
                result.error_code or "speaker_score_unavailable",
            )
        assert result.score is not None
        return result.score

    async def close(self, *, timeout_seconds: float = 1.0) -> bool:
        self._manager._validate_timeout("timeout_seconds", timeout_seconds)
        self._closed = True
        receipts = tuple(self._receipts)
        for receipt in receipts:
            self._manager.cancel(receipt)
            self._manager.abandon(receipt)
        self._receipts.clear()
        return True


class SharedSpeakerScoringHostManager:
    """Own exactly one physical host and serialize every scoring lane."""

    def __init__(
        self,
        physical_host_factory: PhysicalSpeakerScoringHostFactory,
        *,
        max_outstanding_jobs: int,
        max_buffered_pcm_bytes: int,
        close_timeout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(physical_host_factory):
            raise TypeError("physical_host_factory must be callable")
        if type(max_outstanding_jobs) is not int or max_outstanding_jobs <= 0:
            raise ValueError("max_outstanding_jobs must be positive")
        if type(max_buffered_pcm_bytes) is not int or max_buffered_pcm_bytes <= 0:
            raise ValueError("max_buffered_pcm_bytes must be positive")
        self._validate_timeout("close_timeout_seconds", close_timeout_seconds)
        self._physical_host_factory = physical_host_factory
        self._max_outstanding_jobs = max_outstanding_jobs
        self._max_buffered_pcm_bytes = max_buffered_pcm_bytes
        self._close_timeout_seconds = float(close_timeout_seconds)
        self._clock = clock
        self._manager_id = uuid.uuid4().hex
        self._host_generation = 0
        self._identity: SpeakerHostIdentity | None = None
        self._generation_receipt: HostGenerationReceipt | None = None
        self._host: PhysicalSpeakerScoringHost | None = None
        self._starting_host: PhysicalSpeakerScoringHost | None = None
        self._queue: deque[int] = deque()
        self._jobs: dict[int, _ScoreJob] = {}
        self._next_request_id = 1
        self._buffered_pcm_bytes = 0
        self._worker: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._active_job: _ScoreJob | None = None
        self._active_physical_task: asyncio.Task[PhysicalScoreResponse] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._accepting = False
        self._terminal = False
        self._closed = False
        self._metrics = {
            "completed_count": 0,
            "cancelled_count": 0,
            "timed_out_count": 0,
            "failed_count": 0,
            "stale_count": 0,
            "unavailable_count": 0,
            "host_termination_count": 0,
            "reload_count": 0,
        }

    @staticmethod
    def _validate_timeout(name: str, value: object) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0
        ):
            raise ValueError(f"{name} must be finite and positive")

    def now(self) -> float:
        value = float(self._clock())
        if not math.isfinite(value):
            raise SharedHostError("clock_non_finite")
        return value

    @property
    def current_generation(self) -> HostGenerationReceipt | None:
        return self._generation_receipt

    def is_generation_current(self, receipt: HostGenerationReceipt) -> bool:
        return bool(
            type(receipt) is HostGenerationReceipt
            and receipt == self._generation_receipt
            and self._accepting
            and not self._closed
            and not self._terminal
            and self._host is not None
            and self._host.process_count == 1
        )

    def process_count_for(self, receipt: HostGenerationReceipt) -> int:
        if not self.is_generation_current(receipt):
            return 0
        assert self._host is not None
        return self._host.process_count

    def pcm_bytes_in_use_for(self, receipt: HostGenerationReceipt) -> int:
        if not self.is_generation_current(receipt):
            return 0
        active = self._active_job
        if active is None or active.receipt.host_generation != receipt.host_generation:
            return 0
        pcm16 = active.pcm16
        return len(pcm16) if pcm16 is not None else 0

    async def install(
        self,
        identity: SpeakerHostIdentity,
        *,
        absolute_deadline: float,
    ) -> HostGenerationReceipt:
        if type(identity) is not SpeakerHostIdentity:
            raise TypeError("identity must be SpeakerHostIdentity")
        self._validate_deadline(absolute_deadline)
        self._bind_loop()
        async with self._lifecycle_lock:
            if self._closed or self._terminal:
                raise SharedHostClosedError("manager_terminal")
            current = self._generation_receipt
            if (
                current is not None
                and current.identity == identity
                and self._host is not None
                and self._host.process_count == 1
            ):
                return current

            self._accepting = False
            self._generation_receipt = None
            self._identity = None
            previous_host = self._host
            had_previous = previous_host is not None
            self._host_generation += 1
            self._invalidate_jobs_for_reload()
            if previous_host is not None:
                retired = await self._retire_host(
                    previous_host,
                    absolute_deadline=absolute_deadline,
                )
                if not retired:
                    self._terminal_fence("reload_retirement_failed")
                    raise SharedHostClosedError("reload_retirement_failed")
                if self._host is previous_host:
                    self._host = None
                if not await self._join_retired_worker(absolute_deadline):
                    self._terminal_fence("reload_worker_retirement_failed")
                    raise SharedHostClosedError("reload_worker_retirement_failed")

            host = await self._create_host(identity, absolute_deadline)
            self._starting_host = host
            remaining = self._remaining(absolute_deadline)
            if remaining <= 0:
                await self._retire_host(
                    host,
                    absolute_deadline=self.now() + self._close_timeout_seconds,
                )
                raise SharedHostScoreError(
                    SharedHostResultStatus.TIMED_OUT,
                    "host_load_deadline_expired",
                )
            load_task = asyncio.create_task(
                host.load(timeout_seconds=remaining),
                name="speaker-shared-host-load",
            )
            loaded = False
            try:
                done, _pending = await asyncio.wait({load_task}, timeout=remaining)
                if done:
                    loaded = bool(load_task.result())
            except Exception:
                loaded = False
            if not loaded or host.process_count != 1:
                load_task.cancel()
                retired = await self._retire_host(
                    host,
                    absolute_deadline=self.now() + self._close_timeout_seconds,
                )
                if retired and self._starting_host is host:
                    self._starting_host = None
                if not retired:
                    self._terminal_fence("host_load_cleanup_failed")
                raise SharedHostScoreError(
                    (
                        SharedHostResultStatus.UNAVAILABLE
                        if done
                        else SharedHostResultStatus.TIMED_OUT
                    ),
                    "host_load_failed" if done else "host_load_deadline_expired",
                )

            self._starting_host = None
            self._host = host
            self._identity = identity
            receipt = HostGenerationReceipt(
                self._manager_id,
                self._host_generation,
                identity,
            )
            self._generation_receipt = receipt
            self._accepting = True
            if had_previous:
                self._metrics["reload_count"] += 1
            return receipt

    async def deactivate(self, *, absolute_deadline: float) -> bool:
        """Retire the current generation while keeping the manager reusable."""

        self._validate_deadline(absolute_deadline)
        self._bind_loop()
        task = asyncio.create_task(
            self._deactivate_owned(absolute_deadline),
            name="speaker-shared-host-deactivate",
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # The state transition has its own absolute bound. Let it finish
            # so cancellation cannot strand a live host behind a stale lease.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            try:
                task.result()
            except Exception:
                pass
            raise

    async def _deactivate_owned(self, absolute_deadline: float) -> bool:
        async with self._lifecycle_lock:
            if self._closed or self._terminal:
                raise SharedHostClosedError("manager_terminal")
            host = self._host
            if host is None and self._generation_receipt is None:
                return True

            self._accepting = False
            self._generation_receipt = None
            self._identity = None
            self._host_generation += 1
            self._invalidate_jobs_for_reload()
            if host is not None:
                retired = await self._retire_host(
                    host,
                    absolute_deadline=absolute_deadline,
                )
                if not retired:
                    self._terminal_fence("deactivation_retirement_failed")
                    return False
                if self._host is host:
                    self._host = None
            if not await self._join_retired_worker(absolute_deadline):
                self._terminal_fence("deactivation_worker_retirement_failed")
                return False
            return True

    def lease(
        self,
        generation: HostGenerationReceipt,
        *,
        lane: SpeakerScoringLane,
        mode: SpeakerScoringMode,
        timeout_seconds: float,
    ) -> SharedSpeakerScoringLease:
        self._require_generation(generation)
        if type(lane) is not SpeakerScoringLane:
            raise TypeError("lane must be SpeakerScoringLane")
        if type(mode) is not SpeakerScoringMode:
            raise TypeError("mode must be SpeakerScoringMode")
        self._validate_timeout("timeout_seconds", timeout_seconds)
        return SharedSpeakerScoringLease(
            self,
            generation,
            lane=lane,
            mode=mode,
            timeout_seconds=float(timeout_seconds),
        )

    def submit(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
        generation: HostGenerationReceipt,
        lane: SpeakerScoringLane,
        mode: SpeakerScoringMode,
        absolute_deadline: float,
    ) -> SharedScoreReceipt:
        self._bind_loop()
        self._require_generation(generation)
        self._validate_deadline(absolute_deadline)
        if type(lane) is not SpeakerScoringLane:
            raise TypeError("lane must be SpeakerScoringLane")
        if type(mode) is not SpeakerScoringMode:
            raise TypeError("mode must be SpeakerScoringMode")
        if type(pcm16) is not bytes or not pcm16 or len(pcm16) % 2:
            raise ValueError("pcm16 must contain complete samples")
        if type(sample_rate_hz) is not int or sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if len(self._jobs) >= self._max_outstanding_jobs:
            raise SharedHostCapacityError("outstanding_job_capacity")
        if self._buffered_pcm_bytes + len(pcm16) > self._max_buffered_pcm_bytes:
            raise SharedHostCapacityError("pcm_buffer_capacity")

        now = self.now()
        request_id = self._next_request_id
        self._next_request_id += 1
        receipt = SharedScoreReceipt(
            manager_id=self._manager_id,
            host_generation=generation.host_generation,
            request_id=request_id,
            identity=generation.identity,
            lane=lane,
            mode=mode,
            enqueued_at=now,
            absolute_deadline=float(absolute_deadline),
        )
        loop = asyncio.get_running_loop()
        job = _ScoreJob(receipt, bytearray(pcm16), sample_rate_hz, loop.create_future())
        self._jobs[request_id] = job
        self._queue.append(request_id)
        self._buffered_pcm_bytes += len(pcm16)
        self._ensure_worker(loop)
        self._wake.set()
        return receipt

    def cancel(self, receipt: SharedScoreReceipt) -> bool:
        job = self._owned_job(receipt)
        if job is None or job.future.done():
            return False
        job.cancelled = True
        if self._active_job is not job:
            try:
                self._queue.remove(receipt.request_id)
            except ValueError:
                pass
        self._resolve(
            job,
            SharedHostResultStatus.CANCELLED,
            error_code="cancelled_by_waiter",
        )
        return True

    def abandon(self, receipt: SharedScoreReceipt) -> None:
        """Drop a logically cancelled waiter without touching physical work."""
        job = self._owned_job(receipt)
        if job is None:
            return
        job.abandoned = True
        physical_task = job.physical_task
        if self._active_job is not job or physical_task is None:
            self._jobs.pop(receipt.request_id, None)

    async def await_result(self, receipt: SharedScoreReceipt) -> SharedScoreResult:
        job = self._owned_job(receipt)
        if job is None:
            raise SharedHostIdentityError("receipt_not_owned")
        result = await asyncio.shield(job.future)
        if self._jobs.get(receipt.request_id) is job:
            self._jobs.pop(receipt.request_id, None)
        return result

    async def close(self) -> None:
        self._bind_loop()
        async with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            self._accepting = False
            self._host_generation += 1
            self._settle_queued(
                SharedHostResultStatus.CANCELLED,
                "manager_closed",
                include_active=True,
            )
            host = self._host
            if host is not None:
                retired = await self._retire_host(
                    host,
                    absolute_deadline=self.now() + self._close_timeout_seconds,
                )
                if retired and self._host is host:
                    self._host = None
            starting_host = self._starting_host
            if starting_host is not None and starting_host is not host:
                retired = await self._retire_host(
                    starting_host,
                    absolute_deadline=self.now() + self._close_timeout_seconds,
                )
                if retired and self._starting_host is starting_host:
                    self._starting_host = None
            worker = self._worker
            if worker is not None and not worker.done():
                worker.cancel()
                await asyncio.wait({worker}, timeout=self._close_timeout_seconds)
            self._generation_receipt = None
            self._identity = None

    def snapshot(self) -> dict[str, int]:
        host = self._host
        starting_host = self._starting_host
        physical_host_count = host.process_count if host is not None else 0
        if starting_host is not None and starting_host is not host:
            physical_host_count += starting_host.process_count
        return {
            **self._metrics,
            "physical_host_count": physical_host_count,
            "queued_job_count": len(self._queue),
            "outstanding_job_count": len(self._jobs),
            "buffered_pcm_bytes": self._buffered_pcm_bytes,
            "active_job_count": int(self._active_job is not None),
            "host_generation": self._host_generation,
            "terminal_count": int(self._terminal),
        }

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._owner_loop is None:
            self._owner_loop = loop
        elif self._owner_loop is not loop:
            raise SharedHostError("manager_event_loop_changed")

    def _validate_deadline(self, value: object) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ValueError("absolute_deadline must be finite")

    def _remaining(self, deadline: float) -> float:
        return max(0.0, float(deadline) - self.now())

    def _require_generation(self, receipt: HostGenerationReceipt) -> None:
        if self._closed or self._terminal or not self._accepting:
            raise SharedHostClosedError("manager_not_accepting")
        current = self._generation_receipt
        if type(receipt) is not HostGenerationReceipt or receipt != current:
            raise SharedHostIdentityError("host_generation_stale")

    async def _create_host(
        self,
        identity: SpeakerHostIdentity,
        absolute_deadline: float,
    ) -> PhysicalSpeakerScoringHost:
        remaining = self._remaining(absolute_deadline)
        if remaining <= 0:
            raise SharedHostScoreError(
                SharedHostResultStatus.TIMED_OUT,
                "host_create_deadline_expired",
            )
        try:
            created = self._physical_host_factory(identity, self._host_generation)
            task = asyncio.ensure_future(created)
        except Exception as exc:
            raise SharedHostScoreError(
                SharedHostResultStatus.UNAVAILABLE,
                "host_create_failed",
            ) from exc
        done, _pending = await asyncio.wait({task}, timeout=remaining)
        if not done:
            self._terminal_fence("host_create_timeout")
            task.add_done_callback(self._consume_late_created_host)
            raise SharedHostScoreError(
                SharedHostResultStatus.TIMED_OUT,
                "host_create_deadline_expired",
            )
        try:
            return task.result()
        except Exception as exc:
            raise SharedHostScoreError(
                SharedHostResultStatus.UNAVAILABLE,
                "host_create_failed",
            ) from exc

    def _consume_late_created_host(
        self, task: asyncio.Future[PhysicalSpeakerScoringHost]
    ) -> None:
        if task.cancelled():
            return
        try:
            host = task.result()
        except Exception:
            return
        cleanup = asyncio.create_task(
            host.terminate(timeout_seconds=self._close_timeout_seconds),
            name="speaker-shared-host-late-create-cleanup",
        )
        cleanup.add_done_callback(self._consume_cleanup_result)

    @staticmethod
    def _consume_cleanup_result(task: asyncio.Future[None]) -> None:
        if not task.cancelled():
            task.exception()

    def _ensure_worker(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._worker is None or self._worker.done():
            self._worker = loop.create_task(
                self._run(), name="speaker-shared-host-worker"
            )

    async def _run(self) -> None:
        generation = self._host_generation
        while (
            not self._closed
            and not self._terminal
            and generation == self._host_generation
        ):
            while self._queue:
                request_id = self._queue.popleft()
                job = self._jobs.get(request_id)
                if job is None or job.future.done() or job.cancelled:
                    continue
                self._active_job = job
                try:
                    await self._execute(job, generation)
                finally:
                    physical_task = job.physical_task
                    if physical_task is None or physical_task.done():
                        self._release_pcm(job)
                    if self._active_job is job:
                        self._active_job = None
                if self._terminal or generation != self._host_generation:
                    return
            self._wake.clear()
            if not self._queue and self._accepting:
                await self._wake.wait()
            else:
                return

    async def _execute(self, job: _ScoreJob, generation: int) -> None:
        remaining = self._remaining(job.receipt.absolute_deadline)
        if remaining <= 0:
            self._resolve(
                job,
                SharedHostResultStatus.TIMED_OUT,
                error_code="deadline_expired_in_queue",
            )
            return
        host = self._host
        pcm16 = job.pcm16
        if (
            host is None
            or pcm16 is None
            or host.process_count != 1
            or generation != self._host_generation
        ):
            self._resolve(
                job,
                SharedHostResultStatus.UNAVAILABLE,
                error_code="physical_host_unavailable",
            )
            if generation == self._host_generation and host is not None:
                await self._terminate_and_fence(host, "physical_host_unavailable")
            return
        physical_task = asyncio.create_task(
            host.score(
                pcm16,
                sample_rate_hz=job.sample_rate_hz,
                mode=job.receipt.mode,
                host_generation=generation,
                request_id=job.receipt.request_id,
                timeout_seconds=remaining,
            ),
            name=f"speaker-shared-host-score-{job.receipt.request_id}",
        )
        job.physical_task = physical_task
        self._active_physical_task = physical_task
        physical_task.add_done_callback(
            lambda task: self._physical_task_done(job, task)
        )
        try:
            done, _pending = await asyncio.wait({physical_task}, timeout=remaining)
            if not done:
                if self._request_is_current(host, generation):
                    await self._terminate_and_fence(host, "physical_score_hung")
                self._resolve(
                    job,
                    SharedHostResultStatus.TIMED_OUT,
                    error_code="physical_score_deadline_expired",
                )
                return
            try:
                response = physical_task.result()
            except TimeoutError:
                if self._request_is_current(host, generation):
                    await self._terminate_and_fence(host, "physical_score_timed_out")
                self._resolve(
                    job,
                    SharedHostResultStatus.TIMED_OUT,
                    error_code="physical_score_timed_out",
                )
                return
            except Exception:
                if self._request_is_current(host, generation):
                    await self._terminate_and_fence(host, "physical_score_failed")
                self._resolve(
                    job,
                    SharedHostResultStatus.FAILED,
                    error_code="physical_score_failed",
                )
                return
            if (
                type(response) is not PhysicalScoreResponse
                or response.host_generation != generation
                or response.request_id != job.receipt.request_id
            ):
                if self._request_is_current(host, generation):
                    await self._terminate_and_fence(host, "physical_identity_mismatch")
                self._resolve(
                    job,
                    SharedHostResultStatus.STALE,
                    error_code="physical_response_identity_mismatch",
                )
                return
            score = response.score
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
                or not -1.0 <= float(score) <= 1.0
            ):
                if self._request_is_current(host, generation):
                    await self._terminate_and_fence(host, "physical_score_invalid")
                self._resolve(
                    job,
                    SharedHostResultStatus.FAILED,
                    error_code="physical_score_invalid",
                )
                return
            if (
                job.cancelled
                or generation != self._host_generation
                or job.receipt.identity != self._identity
            ):
                self._resolve(
                    job,
                    SharedHostResultStatus.STALE,
                    error_code="score_result_stale",
                )
                return
            self._resolve(
                job,
                SharedHostResultStatus.COMPLETED,
                score=float(score),
            )
        except asyncio.CancelledError:
            # Lifecycle teardown owns the physical task and host. Never let a
            # waiter cancellation propagate through to the shared process.
            raise

    def _physical_task_done(
        self,
        job: _ScoreJob,
        task: asyncio.Future[PhysicalScoreResponse],
    ) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass
        if self._active_physical_task is task:
            self._active_physical_task = None
        self._release_pcm(job)
        if job.abandoned:
            self._jobs.pop(job.receipt.request_id, None)

    def _request_is_current(
        self,
        host: PhysicalSpeakerScoringHost,
        generation: int,
    ) -> bool:
        return bool(
            self._host is host
            and generation == self._host_generation
            and not self._closed
            and not self._terminal
        )

    async def _terminate_and_fence(
        self,
        host: PhysicalSpeakerScoringHost,
        reason: str,
    ) -> None:
        self._terminal_fence(reason)
        terminated = await self._terminate_host_bounded(host)
        if terminated and self._host is host:
            self._host = None

    def _terminal_fence(self, reason: str) -> None:
        if self._terminal:
            return
        self._terminal = True
        self._accepting = False
        self._host_generation += 1
        self._settle_queued(
            SharedHostResultStatus.UNAVAILABLE,
            reason,
            include_active=False,
        )
        self._wake.set()

    def _invalidate_jobs_for_reload(self) -> None:
        self._settle_queued(
            SharedHostResultStatus.STALE,
            "host_generation_replaced",
            include_active=True,
        )
        self._queue.clear()
        self._wake.set()

    def _settle_queued(
        self,
        status: SharedHostResultStatus,
        error_code: str,
        *,
        include_active: bool,
    ) -> None:
        active = self._active_job
        for job in tuple(self._jobs.values()):
            if job.future.done() or (not include_active and job is active):
                continue
            job.cancelled = True
            self._resolve(job, status, error_code=error_code)
        self._queue.clear()

    async def _retire_host(
        self,
        host: PhysicalSpeakerScoringHost,
        *,
        absolute_deadline: float,
    ) -> bool:
        active_task = self._active_physical_task
        if active_task is not None and not active_task.done():
            wait_seconds = min(
                self._remaining(absolute_deadline),
                self._close_timeout_seconds,
            )
            if wait_seconds > 0:
                await asyncio.wait({active_task}, timeout=wait_seconds)
        if active_task is not None and not active_task.done():
            terminated = await self._terminate_host_bounded(host)
            if not terminated:
                return False
            done, _pending = await asyncio.wait(
                {active_task}, timeout=self._close_timeout_seconds
            )
            return bool(done and host.process_count == 0)
        remaining = self._remaining(absolute_deadline)
        if remaining <= 0:
            return await self._terminate_host_bounded(host)
        close_task = asyncio.create_task(
            host.close(timeout_seconds=remaining),
            name="speaker-shared-host-close",
        )
        done, _pending = await asyncio.wait({close_task}, timeout=remaining)
        if done:
            try:
                closed = bool(close_task.result())
            except Exception:
                closed = False
            if closed and host.process_count == 0:
                return True
        close_task.cancel()
        return await self._terminate_host_bounded(host)

    async def _join_retired_worker(self, absolute_deadline: float) -> bool:
        worker = self._worker
        if worker is None:
            return True
        if not worker.done():
            self._wake.set()
            remaining = self._remaining(absolute_deadline)
            if remaining > 0:
                await asyncio.wait({worker}, timeout=remaining)
        if not worker.done():
            worker.cancel()
            done, _pending = await asyncio.wait(
                {worker}, timeout=self._close_timeout_seconds
            )
            if not done:
                return False
        self._worker = None
        return True

    async def _terminate_host_bounded(self, host: PhysicalSpeakerScoringHost) -> bool:
        task = asyncio.create_task(
            host.terminate(timeout_seconds=self._close_timeout_seconds),
            name="speaker-shared-host-terminate",
        )
        done, _pending = await asyncio.wait({task}, timeout=self._close_timeout_seconds)
        if not done:
            task.cancel()
            return False
        try:
            task.result()
        except Exception:
            return False
        self._metrics["host_termination_count"] += 1
        return host.process_count == 0

    def _owned_job(self, receipt: SharedScoreReceipt) -> _ScoreJob | None:
        if type(receipt) is not SharedScoreReceipt:
            return None
        if receipt.manager_id != self._manager_id:
            return None
        job = self._jobs.get(receipt.request_id)
        if job is None or job.receipt != receipt:
            return None
        return job

    def _resolve(
        self,
        job: _ScoreJob,
        status: SharedHostResultStatus,
        *,
        score: float | None = None,
        error_code: str | None = None,
    ) -> None:
        if not job.future.done():
            job.future.set_result(
                SharedScoreResult(job.receipt, status, score, error_code)
            )
            self._metrics[f"{status.value}_count"] += 1
        physical_task = job.physical_task
        if physical_task is None or physical_task.done():
            self._release_pcm(job)

    def _release_pcm(self, job: _ScoreJob) -> None:
        if job.pcm_released:
            return
        job.pcm_released = True
        pcm16, job.pcm16 = job.pcm16, None
        if pcm16 is not None:
            self._buffered_pcm_bytes -= len(pcm16)
            pcm16[:] = b"\x00" * len(pcm16)


__all__ = [
    "HostGenerationReceipt",
    "PhysicalScoreResponse",
    "PhysicalSpeakerScoringHost",
    "PhysicalSpeakerScoringHostFactory",
    "SharedHostCapacityError",
    "SharedHostClosedError",
    "SharedHostError",
    "SharedHostIdentityError",
    "SharedHostResultStatus",
    "SharedHostScoreError",
    "SharedScoreReceipt",
    "SharedScoreResult",
    "SharedSpeakerScoringHostManager",
    "SharedSpeakerScoringLease",
    "SpeakerHostIdentity",
    "SpeakerScoringLane",
    "SpeakerScoringMode",
]
