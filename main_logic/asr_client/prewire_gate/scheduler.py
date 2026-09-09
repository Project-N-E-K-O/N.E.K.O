"""Bounded single-channel scheduling for pre-wire speaker scoring."""

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


class ScoreBackend(Protocol):
    """Synchronous scorer executed outside the receiving event loop."""

    def score(self, pcm16: bytes, sample_rate_hz: int) -> float: ...


class AsyncScoreBackend(Protocol):
    """Async scorer whose implementation owns physical cancellation."""

    def score_async(self, pcm16: bytes, sample_rate_hz: int) -> Awaitable[float]: ...


class SchedulerError(RuntimeError):
    pass


class SchedulerClosedError(SchedulerError):
    pass


class SchedulerCapacityError(SchedulerError):
    pass


class UnknownReceiptError(SchedulerError):
    pass


@dataclass(frozen=True, slots=True)
class ScoringIdentity:
    session_id: str
    ingress_generation: int
    profile_generation: str
    model_generation: str
    config_generation: str

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id must be non-empty")
        if type(self.ingress_generation) is not int or self.ingress_generation <= 0:
            raise ValueError("ingress_generation must be a positive integer")
        for name in (
            "profile_generation",
            "model_generation",
            "config_generation",
        ):
            if type(getattr(self, name)) is not str or not getattr(self, name):
                raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class RawSampleRange:
    start_sample: int
    end_sample: int

    def __post_init__(self) -> None:
        if (
            type(self.start_sample) is not int
            or type(self.end_sample) is not int
            or self.start_sample < 0
            or self.end_sample <= self.start_sample
        ):
            raise ValueError("raw sample range must be a non-empty forward interval")

    @property
    def sample_count(self) -> int:
        return self.end_sample - self.start_sample


@dataclass(frozen=True, slots=True)
class ScoringWindowPlan:
    """Injected scheduling values; this contract makes no accuracy claim."""

    sample_counts: tuple[int, ...]

    def __post_init__(self) -> None:
        if (
            not self.sample_counts
            or any(type(value) is not int or value <= 0 for value in self.sample_counts)
            or tuple(sorted(set(self.sample_counts))) != self.sample_counts
        ):
            raise ValueError(
                "window sample counts must be positive, unique, and increasing"
            )


@dataclass(frozen=True, slots=True)
class ScoreRequest:
    request_id: str
    identity: ScoringIdentity
    sample_range: RawSampleRange
    pcm16: bytes
    sample_rate_hz: int

    def __post_init__(self) -> None:
        if not self.request_id:
            raise ValueError("request_id must be non-empty")
        if not isinstance(self.identity, ScoringIdentity):
            raise ValueError("identity must be ScoringIdentity")
        if not isinstance(self.sample_range, RawSampleRange):
            raise ValueError("sample_range must be RawSampleRange")
        if type(self.pcm16) is not bytes or not self.pcm16 or len(self.pcm16) % 2:
            raise ValueError("pcm16 must contain complete samples")
        if len(self.pcm16) // 2 != self.sample_range.sample_count:
            raise ValueError("pcm16 length must exactly match the raw sample range")
        if type(self.sample_rate_hz) is not int or self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")


@dataclass(frozen=True, slots=True)
class IdentityReceipt:
    scheduler_id: str
    scheduler_generation: int
    job_id: int
    request_id: str
    identity: ScoringIdentity
    sample_range: RawSampleRange
    enqueued_at: float
    absolute_deadline: float

    def matches(self, identity: ScoringIdentity, sample_range: RawSampleRange) -> bool:
        return self.identity == identity and self.sample_range == sample_range


class ScoreResultStatus(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class ScoreResult:
    receipt: IdentityReceipt
    status: ScoreResultStatus
    score: float | None = None
    error_code: str | None = None

    def validate_identity(
        self, identity: ScoringIdentity, sample_range: RawSampleRange
    ) -> ScoreResult:
        """Fence a result after any caller await and before state mutation."""
        if self.receipt.matches(identity, sample_range):
            return self
        return replace(
            self,
            status=ScoreResultStatus.STALE,
            score=None,
            error_code="identity_or_sample_range_changed",
        )


@dataclass(slots=True)
class _Job:
    request: ScoreRequest | None
    receipt: IdentityReceipt
    future: asyncio.Future[ScoreResult]
    cancelled: bool = False
    released: bool = False


class ControlledScoringScheduler:
    """One worker, one execution channel, bounded outstanding work and PCM."""

    def __init__(
        self,
        backend: ScoreBackend | AsyncScoreBackend,
        *,
        window_plan: ScoringWindowPlan,
        max_outstanding_jobs: int,
        max_buffered_pcm_bytes: int,
        deadline_seconds: float,
        close_timeout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(max_outstanding_jobs) is not int or max_outstanding_jobs <= 0:
            raise ValueError("max_outstanding_jobs must be positive")
        if type(max_buffered_pcm_bytes) is not int or max_buffered_pcm_bytes <= 0:
            raise ValueError("max_buffered_pcm_bytes must be positive")
        for name, value in (
            ("deadline_seconds", deadline_seconds),
            ("close_timeout_seconds", close_timeout_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be finite and positive")
            if not math.isfinite(float(value)) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        self._backend = backend
        self._window_plan = window_plan
        self._max_outstanding_jobs = max_outstanding_jobs
        self._max_buffered_pcm_bytes = max_buffered_pcm_bytes
        self._deadline_seconds = float(deadline_seconds)
        self._close_timeout_seconds = float(close_timeout_seconds)
        self._clock = clock
        self._scheduler_id = uuid.uuid4().hex
        self._generation = 1
        self._next_job_id = 1
        self._buffered_pcm_bytes = 0
        self._queue: deque[int] = deque()
        self._jobs: dict[int, _Job] = {}
        self._worker: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._closed = False
        self._active_job_id: int | None = None
        self._active_score_task: asyncio.Task[float] | None = None
        self._active_score_is_async = False

    @property
    def window_plan(self) -> ScoringWindowPlan:
        return self._window_plan

    @property
    def buffered_pcm_bytes(self) -> int:
        return self._buffered_pcm_bytes

    @property
    def outstanding_jobs(self) -> int:
        return len(self._jobs)

    @property
    def queued_jobs(self) -> int:
        return len(self._queue)

    def submit(self, request: ScoreRequest) -> IdentityReceipt:
        """Queue without awaiting scoring, keeping the receive path non-blocking."""
        if self._closed:
            raise SchedulerClosedError("scheduler_closed")
        if request.sample_range.sample_count not in self._window_plan.sample_counts:
            raise SchedulerError("sample_range_not_in_window_plan")
        if len(self._jobs) >= self._max_outstanding_jobs:
            raise SchedulerCapacityError("outstanding_job_capacity")
        size = len(request.pcm16)
        if self._buffered_pcm_bytes + size > self._max_buffered_pcm_bytes:
            raise SchedulerCapacityError("pcm_buffer_capacity")
        loop = asyncio.get_running_loop()
        now = float(self._clock())
        if not math.isfinite(now):
            raise SchedulerError("clock_non_finite")
        job_id = self._next_job_id
        self._next_job_id += 1
        receipt = IdentityReceipt(
            scheduler_id=self._scheduler_id,
            scheduler_generation=self._generation,
            job_id=job_id,
            request_id=request.request_id,
            identity=request.identity,
            sample_range=request.sample_range,
            enqueued_at=now,
            absolute_deadline=now + self._deadline_seconds,
        )
        if not math.isfinite(receipt.absolute_deadline):
            raise SchedulerError("deadline_non_finite")
        self._jobs[job_id] = _Job(request, receipt, loop.create_future())
        self._queue.append(job_id)
        self._buffered_pcm_bytes += size
        self._ensure_worker(loop)
        self._wake.set()
        return receipt

    def reprioritize(self, receipt: IdentityReceipt) -> bool:
        """Move queued work forward without changing its first-queue deadline."""
        job = self._owned_job(receipt)
        if job is None or receipt.job_id == self._active_job_id:
            return False
        try:
            self._queue.remove(receipt.job_id)
        except ValueError:
            return False
        self._queue.appendleft(receipt.job_id)
        self._wake.set()
        return True

    def cancel(self, receipt: IdentityReceipt) -> bool:
        job = self._owned_job(receipt)
        if job is None or job.future.done():
            return False
        job.cancelled = True
        if receipt.job_id == self._active_job_id:
            score_task = self._active_score_task
            if (
                self._active_score_is_async
                and score_task is not None
                and not score_task.done()
            ):
                score_task.cancel()
        else:
            try:
                self._queue.remove(receipt.job_id)
            except ValueError:
                pass
        self._resolve(
            job, ScoreResultStatus.CANCELLED, error_code="cancelled_by_caller"
        )
        return True

    def _fence_after_unsettled_async_cancellation(self) -> None:
        """Stop this channel before an uncooperative call can overlap another."""

        if self._closed:
            return
        self._closed = True
        self._generation += 1
        active_job_id = self._active_job_id
        for job_id, job in tuple(self._jobs.items()):
            if job_id == active_job_id or job.future.done():
                continue
            job.cancelled = True
            self._resolve(
                job,
                ScoreResultStatus.CANCELLED,
                error_code="scheduler_fenced",
            )
        self._queue.clear()
        self._wake.set()

    async def await_result(self, receipt: IdentityReceipt) -> ScoreResult:
        job = self._owned_job(receipt)
        if job is None:
            raise UnknownReceiptError("receipt_not_owned")
        result = await asyncio.shield(job.future)
        # The caller receives the exact identity/range receipt after this await.
        if self._jobs.get(receipt.job_id) is job:
            self._jobs.pop(receipt.job_id, None)
        return result

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        for job in tuple(self._jobs.values()):
            if not job.future.done():
                job.cancelled = True
                self._resolve(
                    job, ScoreResultStatus.CANCELLED, error_code="scheduler_closed"
                )
        self._queue.clear()
        self._wake.set()
        worker, self._worker = self._worker, None
        if worker is not None and not worker.done():
            score_task = self._active_score_task
            if (
                self._active_score_is_async
                and score_task is not None
                and not score_task.done()
            ):
                score_task.cancel()
            worker.cancel()
            done, _pending = await asyncio.wait(
                {worker}, timeout=self._close_timeout_seconds
            )
            if done:
                try:
                    worker.result()
                except asyncio.CancelledError:
                    pass

    def _ensure_worker(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._worker is None or self._worker.done():
            self._worker = loop.create_task(
                self._run(), name="prewire-controlled-scorer"
            )

    def _owned_job(self, receipt: IdentityReceipt) -> _Job | None:
        if receipt.scheduler_id != self._scheduler_id:
            return None
        job = self._jobs.get(receipt.job_id)
        if job is None or job.receipt != receipt:
            return None
        return job

    def _release_pcm(self, job: _Job) -> None:
        if job.released:
            return
        job.released = True
        request, job.request = job.request, None
        if request is not None:
            self._buffered_pcm_bytes -= len(request.pcm16)

    def _resolve(
        self,
        job: _Job,
        status: ScoreResultStatus,
        *,
        score: float | None = None,
        error_code: str | None = None,
    ) -> None:
        self._release_pcm(job)
        if not job.future.done():
            job.future.set_result(ScoreResult(job.receipt, status, score, error_code))

    async def _run(self) -> None:
        generation = self._generation
        while not self._closed and generation == self._generation:
            while self._queue:
                job_id = self._queue.popleft()
                job = self._jobs.get(job_id)
                if job is None or job.future.done() or job.cancelled:
                    continue
                self._active_job_id = job_id
                try:
                    await self._execute(job, generation)
                finally:
                    if self._active_job_id == job_id:
                        self._active_job_id = None
            self._wake.clear()
            if not self._queue and not self._closed:
                await self._wake.wait()

    async def _execute(self, job: _Job, generation: int) -> None:
        remaining = job.receipt.absolute_deadline - float(self._clock())
        if remaining <= 0:
            self._resolve(
                job, ScoreResultStatus.TIMED_OUT, error_code="deadline_expired_in_queue"
            )
            return
        request = job.request
        if request is None:
            return
        pcm16 = request.pcm16
        sample_rate_hz = request.sample_rate_hz
        score_async = getattr(self._backend, "score_async", None)
        use_async = callable(score_async)
        if use_async:
            score_task = asyncio.create_task(
                self._call_async_backend(score_async, pcm16, sample_rate_hz),
                name=f"prewire-score-async-{job.receipt.job_id}",
            )
        else:
            score = getattr(self._backend, "score", None)
            if not callable(score):
                self._resolve(
                    job,
                    ScoreResultStatus.FAILED,
                    error_code="backend_contract_invalid",
                )
                return
            score_task = asyncio.create_task(
                asyncio.to_thread(score, pcm16, sample_rate_hz),
                name=f"prewire-score-sync-{job.receipt.job_id}",
            )
        self._active_score_task = score_task
        self._active_score_is_async = use_async

        def consume_late(task: asyncio.Task[float]) -> None:
            if not task.cancelled():
                try:
                    task.exception()
                except (asyncio.CancelledError, Exception):
                    pass

        score_task.add_done_callback(consume_late)
        try:
            done, _pending = await asyncio.wait(
                {score_task, job.future},
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if score_task not in done:
                cancelled_or_closed = bool(
                    job.cancelled or self._closed or generation != self._generation
                )
                if not cancelled_or_closed:
                    self._resolve(
                        job,
                        ScoreResultStatus.TIMED_OUT,
                        error_code="execution_deadline_expired",
                    )
                if use_async:
                    settled = await self._cancel_async_score_bounded(score_task)
                    if not settled:
                        self._fence_after_unsettled_async_cancellation()
                else:
                    # A Python thread cannot be stopped safely. Keep this worker
                    # serial until it returns; close may still detach the worker.
                    try:
                        await asyncio.shield(score_task)
                    except Exception:
                        pass
                return
            if job.cancelled or self._closed or generation != self._generation:
                self._resolve(
                    job,
                    ScoreResultStatus.CANCELLED,
                    error_code="result_isolated",
                )
                return
            try:
                score = score_task.result()
            except asyncio.CancelledError:
                self._resolve(
                    job,
                    ScoreResultStatus.FAILED,
                    error_code="backend_cancelled",
                )
                return
            except Exception:
                self._resolve(
                    job, ScoreResultStatus.FAILED, error_code="backend_exception"
                )
                return
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                self._resolve(job, ScoreResultStatus.FAILED, error_code="score_invalid")
                return
            try:
                score_value = float(score)
            except (OverflowError, ValueError):
                self._resolve(job, ScoreResultStatus.FAILED, error_code="score_invalid")
                return
            if not math.isfinite(score_value):
                self._resolve(
                    job, ScoreResultStatus.FAILED, error_code="score_non_finite"
                )
                return
            self._resolve(job, ScoreResultStatus.COMPLETED, score=score_value)
        except asyncio.CancelledError:
            if use_async:
                await self._cancel_async_score_bounded(score_task)
            raise
        finally:
            if self._active_score_task is score_task:
                self._active_score_task = None
                self._active_score_is_async = False

    @staticmethod
    async def _call_async_backend(
        score_async: Callable[[bytes, int], Awaitable[float]],
        pcm16: bytes,
        sample_rate_hz: int,
    ) -> float:
        return await score_async(pcm16, sample_rate_hz)

    async def _cancel_async_score_bounded(
        self,
        score_task: asyncio.Task[float],
    ) -> bool:
        """Request cancellation, then detach an uncooperative host await."""

        if not score_task.done() and score_task.cancelling() == 0:
            score_task.cancel()
        try:
            done, _pending = await asyncio.wait(
                {score_task}, timeout=self._close_timeout_seconds
            )
        except asyncio.CancelledError:
            score_task.cancel()
            raise
        if not done:
            # The shared host manager owns physical termination and serialization.
            # This scheduler only isolates the late logical result.
            return False
        try:
            score_task.result()
        except (asyncio.CancelledError, Exception):
            pass
        return True
