"""Session-owned composition of the pre-ASR interception building blocks.

This module owns lifecycle and ordering only.  ECAPA scoring, calibrated
classification, quality analysis and TSE are injected so fixtures cannot be
mistaken for production model wiring.  Every failure retires the session and
returns a non-audio result; there is no mixed-PCM fallback.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from collections import deque
import math
import time
from typing import Awaitable, Callable, Protocol

import numpy as np

from main_logic.voice_input.interception import (
    ActiveSessionInterceptionFactory,
    ActiveSessionInterceptionRuntime,
    InterceptionDecision,
    InterceptionResult,
)

from .prewire_gate.contracts import (
    PrewireIntervalIdentity,
    PrewireIntervalSpec,
    PrewireStreamKey,
    SampleRange,
)
from .prewire_gate.decision import PrewireQualitySummary
from .prewire_gate.extraction import (
    ExtractedPrewireAudio,
    PrewireExtractionAdapter,
)
from .prewire_gate.gate import PrewireGate, PrewireGatePlan, PrewireWindowPlanner
from .prewire_gate.scheduler import ControlledScoringScheduler, ScoringWindowPlan
from .tse.contracts import (
    TseAudioChunk,
    TseExtractionResult,
    TseModelError,
    TseOutputStatus,
)


class InterceptionRuntimeError(RuntimeError):
    """The session cannot safely continue releasing audio."""


class QualityAnalyzer(Protocol):
    def analyze(self, pcm16: bytes, sample_rate_hz: int) -> PrewireQualitySummary: ...


class TSEWorker(Protocol):
    async def start(self, *, timeout: float = 1.0) -> None: ...

    async def push(self, pcm: np.ndarray, *, start_sample: int) -> list[TseAudioChunk] | TseExtractionResult: ...

    async def flush(self) -> list[TseAudioChunk] | TseExtractionResult: ...

    async def close(self, *, timeout: float = 1.0) -> bool: ...


TSEFactory = Callable[[object], TSEWorker]


@dataclass(frozen=True, slots=True)
class InterceptionRuntimeConfig:
    session_id: str
    ingress_generation: int
    profile_generation: str
    model_generation: str
    config_generation: str
    scoring_parameters_digest: str
    window_samples: int = 14_400
    step_samples: int = 2_400
    guard_samples: int = 1_600
    max_held_pcm_bytes: int = 2 * 16_000 * 2
    required_consistent_observations: int = 1
    owner_streak_required: int = 2
    max_outstanding_jobs: int = 4
    max_buffered_pcm_bytes: int = 2 * 16_000 * 2
    scoring_deadline_seconds: float = 1.0
    scoring_close_timeout_seconds: float = 1.0
    prefix_deadline_seconds: float = 1.0
    extraction_max_buffered_pcm_bytes: int = 2 * 16_000 * 2
    extraction_max_pending_events: int = 128


class PrewireInterceptionRuntime(ActiveSessionInterceptionRuntime):
    """One session/generation owner for Gate, scheduler, extraction and TSE."""

    def __init__(
        self,
        config: InterceptionRuntimeConfig,
        *,
        score_backend,
        classifier,
        tse_factory: TSEFactory | None,
        quality_analyzer: QualityAnalyzer | None = None,
        generation_token: object | None = None,
        ingress_token: object | None = None,
    ) -> None:
        self._config = config
        if config.required_consistent_observations != 1:
            raise ValueError(
                "runtime uses cross-window owner streak; gate required_consistent_observations must be 1"
            )
        if type(config.owner_streak_required) is not int or config.owner_streak_required <= 0:
            raise ValueError("owner_streak_required must be positive")
        if not math.isfinite(config.prefix_deadline_seconds) or config.prefix_deadline_seconds <= 0:
            raise ValueError("prefix_deadline_seconds must be finite and positive")
        self._score_backend = score_backend
        self._classifier = classifier
        self._tse_factory = tse_factory
        self._quality_analyzer = quality_analyzer
        self._generation_token = generation_token
        self._ingress_token = ingress_token
        self._scheduler = ControlledScoringScheduler(
            score_backend,
            window_plan=ScoringWindowPlan((config.window_samples,)),
            max_outstanding_jobs=config.max_outstanding_jobs,
            max_buffered_pcm_bytes=config.max_buffered_pcm_bytes,
            deadline_seconds=config.scoring_deadline_seconds,
            close_timeout_seconds=config.scoring_close_timeout_seconds,
        )
        self._gate = PrewireGate(
            self._scheduler,
            classifier=classifier,
            window_samples=config.window_samples,
            step_samples=config.step_samples,
            guard_samples=config.guard_samples,
            max_held_pcm_bytes=config.max_held_pcm_bytes,
            scoring_parameters_digest=config.scoring_parameters_digest,
            required_consistent_observations=config.required_consistent_observations,
        )
        self._extraction = PrewireExtractionAdapter(
            max_buffered_pcm_bytes=config.extraction_max_buffered_pcm_bytes,
            max_pending_events=config.extraction_max_pending_events,
        )
        self._stream = PrewireStreamKey(config.session_id, config.ingress_generation)
        self._planner: PrewireWindowPlanner | None = None
        self._tse: TSEWorker | None = None
        self._tse_sample = 0
        self._segment = 0
        self._planned_ranges: deque = deque()
        self._owner_streak = 0
        self._held_authorized_pcm: list[bytes] = []
        self._started = False
        self._closed = False
        self._retirement_confirmed = True
        self._deadline_task: asyncio.Task[None] | None = None
        self._capture_deadline: float | None = None
        self._ready_audio: list[ExtractedPrewireAudio] = []
        self._lock = asyncio.Lock()
        self._handle = None

    async def _start(self) -> None:
        if self._started:
            return
        if self._tse_factory is None:
            raise InterceptionRuntimeError("tse_worker_unavailable")
        self._tse = self._tse_factory(self._stream)
        if self._tse is None:
            raise InterceptionRuntimeError("tse_worker_unavailable")
        try:
            await self._tse.start(timeout=self._config.scoring_deadline_seconds)
            self._retirement_confirmed = False
            self._gate.open_stream(
                self._stream,
                profile_generation=self._config.profile_generation,
                model_generation=self._config.model_generation,
                config_generation=self._config.config_generation,
            )
            self._handle = self._extraction.open_stream(
                self._stream,
                profile_generation=self._config.profile_generation,
                model_generation=self._config.model_generation,
                config_generation=self._config.config_generation,
            )
            self._planner = self._gate.new_planner()
            self._started = True
        except BaseException:
            await self._retire_components("startup_failed")
            raise

    async def process(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
        generation: object,
        ingress_token: object | None,
        captured_at: float | None,
    ) -> InterceptionResult:
        if generation != self._generation_token or ingress_token != self._ingress_token:
            return InterceptionResult(InterceptionDecision.STALE, reason="generation_mismatch")
        if sample_rate_hz != 16_000 or type(pcm16) is not bytes or not pcm16 or len(pcm16) % 2:
            return InterceptionResult(InterceptionDecision.DROP, reason="invalid_pcm")
        async with self._lock:
            if self._closed:
                return InterceptionResult(InterceptionDecision.STALE, reason="runtime_closed")
            try:
                now = time.time()
                origin = captured_at if isinstance(captured_at, (int, float)) and math.isfinite(captured_at) else now
                await self._arm_deadline(min(now, origin) + self._config.prefix_deadline_seconds)
                await self._start()
                assert self._planner is not None and self._handle is not None and self._tse is not None
                start = self._tse_sample
                sample_count = len(pcm16) // 2
                self._gate.append_pcm(self._stream, start_sample=start, pcm16=pcm16)
                samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / np.float32(32768)
                chunks = await self._tse.push(samples, start_sample=start)
                self._tse_sample += sample_count
                self._append_tse(chunks)
                output = []
                self._planned_ranges.extend(self._planner.add_samples(sample_count))
                while self._planned_ranges:
                    planned = self._planned_ranges[0]
                    self._planned_ranges.popleft()
                    output.extend(await self._submit_with_streak(planned, event_ended=False))
                    await self._arm_deadline(time.time() + self._config.prefix_deadline_seconds)
                return self._result(output)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._retire_components(f"runtime_failed:{type(exc).__name__}")
                return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason=str(exc))

    async def finish(self) -> InterceptionResult:
        """Close the capture prefix and turn unresolved tails into gaps."""

        async with self._lock:
            if self._closed or not self._started or self._planner is None:
                return InterceptionResult(InterceptionDecision.DROP, reason="runtime_finished")
            try:
                flush = getattr(self._tse, "flush", None)
                if callable(flush):
                    chunks = await flush()
                    self._append_tse(chunks)
                for planned in self._planner.finish_event():
                    self._planned_ranges.append(planned)
                finish_output: list[bytes] = []
                while self._planned_ranges:
                    planned = self._planned_ranges.popleft()
                    finish_output.extend(await self._submit_with_streak(planned, event_ended=True))
                end = await self._gate.finish_stream(self._stream)
                if self._handle is not None:
                    self._extraction.accept_event(self._handle, end)
                await self._retire_components("capture_finished")
                if finish_output:
                    return InterceptionResult(InterceptionDecision.KEEP, b"".join(finish_output), "filtered_target_audio")
                return InterceptionResult(InterceptionDecision.DROP, reason="capture_finished")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._retire_components(f"finish_failed:{type(exc).__name__}")
                return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason=str(exc))

    async def _expire_prefix(self) -> None:
        deadline = self._capture_deadline
        if deadline is None:
            return
        delay = max(0.0, deadline - time.time())
        await asyncio.sleep(delay)
        async with self._lock:
            if not self._closed and self._owner_streak < self._config.owner_streak_required:
                await self._retire_components("prefix_deadline_expired")

    async def _arm_deadline(self, deadline: float) -> None:
        self._capture_deadline = deadline
        task, self._deadline_task = self._deadline_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        self._deadline_task = asyncio.create_task(self._expire_prefix(), name="prewire-prefix-deadline")

    async def _submit_planned(self, planned, *, event_ended: bool) -> tuple[list[bytes], bool]:
        self._segment += 1
        scoring_ranges = (planned.scoring_range,)
        identity = PrewireIntervalIdentity(
            self._stream,
            self._segment,
            SampleRange(planned.commit_range.start, planned.scoring_range.end),
            self._config.profile_generation,
            self._config.model_generation,
            self._config.config_generation,
        )
        spec = PrewireIntervalSpec(
            identity,
            planned.scoring_range,
            planned.decision_range,
            planned.commit_range,
            event_ended,
            event_ended,
            event_ended,
        )
        qualities = None
        if self._quality_analyzer is not None:
            qualities = tuple(
                [await self._analyze_quality(item) for item in scoring_ranges]
            )
        submission = self._gate.submit_interval(spec, scoring_ranges=scoring_ranges, quality_summaries=qualities)
        timeout = self._config.scoring_deadline_seconds
        if self._capture_deadline is not None:
            timeout = min(timeout, self._capture_deadline - time.time())
        if timeout <= 0:
            raise InterceptionRuntimeError("prefix_deadline_expired")
        plan = await asyncio.wait_for(self._gate.resolve(submission), timeout=timeout)
        if plan is None:
            return [], False
        output, owner = self._accept_plan(plan)
        self._gate.claim(plan)
        return output, owner

    async def _analyze_quality(self, sample_range: SampleRange) -> PrewireQualitySummary:
        assert self._quality_analyzer is not None
        pcm16 = self._gate_pcm(sample_range)
        analyze_async = getattr(self._quality_analyzer, "analyze_async", None)
        if callable(analyze_async):
            return await analyze_async(pcm16, 16_000)
        return await asyncio.to_thread(
            self._quality_analyzer.analyze, pcm16, 16_000
        )

    async def _submit_with_streak(self, planned, *, event_ended: bool) -> list[bytes]:
        """Require two consecutive KEEP windows without duplicating a score."""
        output, owner = await self._submit_planned(planned, event_ended=event_ended)
        if owner:
            self._owner_streak += 1
            self._held_authorized_pcm.extend(output)
            if self._owner_streak < self._config.owner_streak_required:
                return []
            released = self._held_authorized_pcm
            self._held_authorized_pcm = []
            return released
        self._owner_streak = 0
        self._held_authorized_pcm.clear()
        return []

    def _gate_pcm(self, sample_range: SampleRange) -> bytes:
        # The gate intentionally keeps ownership of the source PCM; this helper
        # is only used by an injected quality analyzer before the range commits.
        return self._gate.copy_pcm(self._stream, sample_range)

    def _append_tse(self, chunks: list[TseAudioChunk] | TseExtractionResult) -> None:
        assert self._handle is not None
        if isinstance(chunks, TseExtractionResult):
            if chunks.status is not TseOutputStatus.TARGET_AUDIO:
                raise TseModelError(chunks.reason or chunks.status.value)
            chunks = list(chunks.chunks)
        for chunk in chunks:
            pcm = np.asarray(chunk.pcm, dtype=np.float32)
            pcm16 = np.clip(pcm, -1.0, 1.0)
            pcm16 = (pcm16 * 32767.0).astype("<i2").tobytes()
            events = self._extraction.append_extracted(
                self._handle,
                SampleRange(chunk.start_sample, chunk.end_sample),
                pcm16,
            )
            self._ready_audio.extend(
                item for item in events if isinstance(item, ExtractedPrewireAudio)
            )

    def _accept_plan(self, plan: PrewireGatePlan) -> tuple[list[bytes], bool]:
        assert self._handle is not None
        output: list[bytes] = []
        owner = False
        interrupted = False
        for event in plan.events:
            if event.kind == "gap":
                interrupted = True
                # Any delayed extraction at or before this gap belongs to an
                # authorization that was never completed.  Retaining it would
                # let a later owner window release bytes across the gap.
                self._ready_audio = [
                    item
                    for item in self._ready_audio
                    if item.identity.segment_id > event.identity.segment_id
                ]
            elif event.kind == "audio":
                owner = True
            events = self._extraction.accept_event(self._handle, event)
            output.extend(
                item.pcm16
                for item in events
                if isinstance(item, ExtractedPrewireAudio)
            )
        if interrupted:
            # A gap invalidates every delayed extraction that precedes or is
            # coalesced with this plan.  It must never be released by a later
            # owner streak.
            self._ready_audio.clear()
        elif owner:
            # With no gap, delayed output belongs to the still-contiguous
            # owner prefix.  Preserve it so the second owner window releases
            # the complete ordered prefix in one result.
            output.extend(item.pcm16 for item in self._ready_audio)
            self._ready_audio.clear()
        return output, owner and not interrupted

    @staticmethod
    def _result(output: list[bytes]) -> InterceptionResult:
        if output:
            return InterceptionResult(InterceptionDecision.KEEP, b"".join(output), "filtered_target_audio")
        return InterceptionResult(InterceptionDecision.PENDING, reason="awaiting_identity_evidence")

    async def _retire_components(self, reason: str) -> None:
        if self._closed and self._tse is None:
            return
        self._closed = True
        deadline_task, self._deadline_task = self._deadline_task, None
        if deadline_task is not None and deadline_task is not asyncio.current_task():
            deadline_task.cancel()
        try:
            await self._gate.close()
        except Exception:
            pass
        self._extraction.close()
        tse = self._tse
        if tse is not None:
            try:
                stopped = await tse.close(
                    timeout=self._config.scoring_close_timeout_seconds
                )
                self._retirement_confirmed = bool(stopped)
                if stopped:
                    self._tse = None
            except Exception:
                self._retirement_confirmed = False

    @property
    def retirement_confirmed(self) -> bool:
        return self._retirement_confirmed

    async def close(self, reason: str = "retired") -> None:
        async with self._lock:
            await self._retire_components(reason)


class PrewireInterceptionFactory(ActiveSessionInterceptionFactory):
    """Factory retaining only injected model dependencies, never model fixtures."""

    def __init__(self, config: InterceptionRuntimeConfig, *, score_backend, classifier, tse_factory, quality_analyzer=None):
        self._config = config
        self._score_backend = score_backend
        self._classifier = classifier
        self._tse_factory = tse_factory
        self._quality_analyzer = quality_analyzer
        self._retired = False
        self._runtimes: list[PrewireInterceptionRuntime] = []

    def create(self, generation: object, *, ingress_token: object | None) -> PrewireInterceptionRuntime:
        if self._retired:
            raise InterceptionRuntimeError("factory_closed")
        for existing in self._runtimes:
            if not existing.retirement_confirmed:
                raise InterceptionRuntimeError("previous_runtime_retirement_pending")
        runtime = PrewireInterceptionRuntime(
            self._config,
            score_backend=self._score_backend,
            classifier=self._classifier,
            tse_factory=self._tse_factory,
            quality_analyzer=self._quality_analyzer,
            generation_token=generation,
            ingress_token=ingress_token,
        )
        self._runtimes.append(runtime)
        return runtime

    def close(self) -> None:
        self._retired = True


__all__ = [
    "InterceptionRuntimeConfig",
    "InterceptionRuntimeError",
    "PrewireInterceptionFactory",
    "PrewireInterceptionRuntime",
    "QualityAnalyzer",
    "TSEWorker",
]
