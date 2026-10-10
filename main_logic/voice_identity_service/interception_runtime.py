"""Session-owned composition of the pre-ASR interception building blocks.

This module owns lifecycle and ordering only.  ECAPA scoring, calibrated
classification, quality analysis and TSE are injected so fixtures cannot be
mistaken for production model wiring.  Every failure retires the session and
returns a non-audio result; there is no mixed-PCM fallback.
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, replace
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
    PrewireDecisionState,
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
from .prewire_gate.gate import PrewireEndEvent, PrewireGate, PrewireGatePlan, PrewireWindowPlanner
from .prewire_gate.scheduler import ControlledScoringScheduler, ScorerCapabilities, ScoringWindowPlan
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
_NO_RETIREMENT_EVIDENCE = object()


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
    finish_timeout_seconds: float = 1.0
    # Sampling support and processing are different clocks. None derives only
    # the necessary live capture support; prefix_deadline_seconds is the
    # additional processing/jitter budget, never a renewed inactivity timer.
    capture_support_timeout_seconds: float | None = None


@dataclass(slots=True)
class _PendingTargetAudio:
    pcm16: bytes | None = None
    confirmed: bool = False
    claimed: bool = False


def _validate_config(config: InterceptionRuntimeConfig, score_backend) -> float:
    """Validate static support before allocating scheduler or model owners."""
    if type(config.required_consistent_observations) is not int or config.required_consistent_observations != 1:
        raise ValueError("runtime uses cross-window owner streak; gate required_consistent_observations must be 1")
    if type(config.owner_streak_required) is not int or config.owner_streak_required <= 0:
        raise ValueError("owner_streak_required must be positive")
    ScoringWindowPlan((config.window_samples,))
    PrewireWindowPlanner(window_samples=config.window_samples, step_samples=config.step_samples, guard_samples=config.guard_samples)
    stream = PrewireStreamKey(config.session_id, config.ingress_generation)
    PrewireIntervalIdentity(stream, 1, SampleRange(0, config.window_samples), config.profile_generation,
                           config.model_generation, config.config_generation)
    if (type(config.scoring_parameters_digest) is not str or len(config.scoring_parameters_digest) != 64
            or any(character not in "0123456789abcdef" for character in config.scoring_parameters_digest)):
        raise ValueError("scoring_parameters_digest must be a lowercase SHA-256 digest")
    for name in ("prefix_deadline_seconds", "scoring_deadline_seconds", "scoring_close_timeout_seconds", "finish_timeout_seconds"):
        value = getattr(config, name)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    support_samples = config.window_samples + (config.owner_streak_required - 1) * config.step_samples
    support_seconds = support_samples / 16_000
    if config.capture_support_timeout_seconds is not None:
        value = config.capture_support_timeout_seconds
        if type(value) not in (int, float) or not math.isfinite(value) or value < support_seconds:
            raise ValueError("capture_support_timeout_seconds cannot be shorter than confirmation support")
        support_seconds = value
    for name in ("max_held_pcm_bytes", "max_buffered_pcm_bytes", "extraction_max_buffered_pcm_bytes"):
        value = getattr(config, name)
        if type(value) is not int or value < support_samples * 2:
            raise ValueError(f"{name} cannot retain confirmation support")
    for name in ("max_outstanding_jobs", "extraction_max_pending_events"):
        value = getattr(config, name)
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if config.extraction_max_pending_events < config.owner_streak_required:
        raise ValueError("extraction_max_pending_events cannot retain owner streak")
    capabilities = getattr(score_backend, "capabilities", None)
    if capabilities is not None:
        if type(capabilities) is not ScorerCapabilities:
            raise ValueError("capabilities must be ScorerCapabilities")
        capabilities.require_support((config.window_samples,), model_generation=config.model_generation)
    return support_seconds


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
        self._capture_support_seconds = _validate_config(config, score_backend)
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
        self._pending_audio: dict[PrewireIntervalIdentity, _PendingTargetAudio] = {}
        # Drained PCM remains locally owned until process/finish returns. It
        # shares the same byte/event budgets with pending extraction.
        self._outgoing_audio: dict[PrewireIntervalIdentity, bytes] = {}
        self._unconfirmed_owner_run: list[PrewireIntervalIdentity] = []
        self._pending_audio_bytes = 0
        self._started = False
        self._closed = False
        self._retirement_confirmed = True
        self._component_retirement_task: asyncio.Task[None] | None = None
        self._gate_close_task: asyncio.Task | None = None
        self._tse_close_task: asyncio.Task | None = None
        self._finish_deadline: float | None = None
        self._output_revoked = False
        self._deadline_task: asyncio.Task[None] | None = None
        self._capture_deadline: float | None = None
        self._ingress_anchors: deque[tuple[int, float]] = deque()
        self._settled_sample = 0
        self._operations: set[asyncio.Task] = set()
        self._finish_task: asyncio.Task | None = None
        self._finish_result_delivered = False
        self._lock = asyncio.Lock()
        self._handle = None

    async def _start(self) -> None:
        self._check_output_authority()
        if self._capture_deadline is not None and self._capture_deadline <= time.monotonic():
            raise InterceptionRuntimeError("prefix_deadline_expired")
        if self._started:
            return
        if self._tse_factory is None:
            raise InterceptionRuntimeError("tse_worker_unavailable")
        self._tse = self._tse_factory(self._stream)
        if self._tse is None:
            raise InterceptionRuntimeError("tse_worker_unavailable")
        # Reserve physical ownership before start can fail or be cancelled.
        self._retirement_confirmed = False
        try:
            await self._await_operation(lambda: self._tse.start(timeout=self._config.scoring_deadline_seconds))
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
                now = time.monotonic()
                # Captured wall timestamps never become monotonic deadlines.
                # They are useful only as a bounded age observation at ingress.
                age = 0.0
                if type(captured_at) in (int, float) and math.isfinite(captured_at):
                    age = max(0.0, time.time() - captured_at)
                sample_count = len(pcm16) // 2
                self._ingress_anchors.append((self._tse_sample + sample_count, now - age))
                self._refresh_deadline()
                await self._start()
                self._check_output_authority()
                assert self._planner is not None and self._handle is not None and self._tse is not None
                start = self._tse_sample
                self._gate.append_pcm(self._stream, start_sample=start, pcm16=pcm16)
                samples = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / np.float32(32768)
                chunks = await self._await_operation(lambda: self._tse.push(samples, start_sample=start))
                self._tse_sample += sample_count
                self._append_tse(chunks)
                output = []
                self._planned_ranges.extend(self._planner.add_samples(sample_count))
                while self._planned_ranges:
                    planned = self._planned_ranges[0]
                    self._planned_ranges.popleft()
                    output.extend(await self._submit_with_streak(planned, event_ended=False))
                output.extend(self._drain_authorized_audio())
                result = self._result(output)
                self._refresh_deadline()
                return result
            except asyncio.CancelledError:
                self._begin_component_retirement()
                raise
            except Exception as exc:
                await self._retire_components(f"runtime_failed:{type(exc).__name__}")
                return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason=str(exc))

    async def finish(self) -> InterceptionResult:
        """Close the capture prefix and turn unresolved tails into gaps."""

        deadline = time.monotonic() + self._config.finish_timeout_seconds
        task = self._finish_task
        if task is None:
            task = asyncio.create_task(self._finish_locked(deadline), name="prewire-capture-finish")
            task.add_done_callback(self._consume_finish_outcome)
            self._finish_task = task
        elif task.done():
            return InterceptionResult(InterceptionDecision.DROP, reason="runtime_finished")
        try:
            done, _ = await asyncio.wait({task}, timeout=max(0.0, deadline - time.monotonic()))
            if task in done:
                result = task.result()
                if self._output_revoked:
                    return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason="runtime_output_revoked")
                if self._finish_result_delivered:
                    return InterceptionResult(InterceptionDecision.DROP, reason="runtime_finished")
                self._finish_result_delivered = True
                self._release_output()
                return result
            task.cancel()
            self._begin_component_retirement()
            return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason="finish_deadline_expired")
        except asyncio.CancelledError:
            task.cancel()
            self._begin_component_retirement()
            raise


    async def _finish_locked(self, deadline: float) -> InterceptionResult:

        async with self._lock:
            if self._closed or not self._started or self._planner is None:
                return InterceptionResult(InterceptionDecision.DROP, reason="runtime_finished")
            try:
                self._finish_deadline = deadline
                flush = getattr(self._tse, "flush", None)
                if callable(flush):
                    chunks = await self._await_operation(flush)
                    self._append_tse(chunks)
                for planned in self._planner.finish_event():
                    self._planned_ranges.append(planned)
                finish_output = self._drain_authorized_audio()
                while self._planned_ranges:
                    planned = self._planned_ranges.popleft()
                    finish_output.extend(await self._submit_with_streak(planned, event_ended=True))
                self._cancel_unconfirmed_audio()
                previous_settled_end = -1
                for _ in range(self._config.extraction_max_pending_events + 1):
                    end = await asyncio.wait_for(self._gate.finish_stream(self._stream), timeout=self._finish_remaining())
                    if self._closed:
                        raise InterceptionRuntimeError("runtime_closed_while_finishing")
                    if isinstance(end, PrewireEndEvent):
                        break
                    if not isinstance(end, PrewireGatePlan) or not end.events:
                        raise InterceptionRuntimeError("invalid_finish_settlement")
                    settled_end = end.ledger_plan.original_cursor_end
                    if settled_end <= previous_settled_end:
                        raise InterceptionRuntimeError("finish_settlement_no_progress")
                    previous_settled_end = settled_end
                    self._accept_plan(end)
                    self._gate.claim(end)
                    self._settled_sample = end.ledger_plan.original_cursor_end
                    for release in end.ledger_plan.releases:
                        self._pending_audio[release.identity].claimed = True
                    self._cancel_unconfirmed_audio()
                    self._owner_streak = 0
                    finish_output.extend(self._drain_authorized_audio())
                else:
                    raise InterceptionRuntimeError("finish_settlement_limit")
                if self._handle is not None:
                    self._collect_extracted(self._extraction.accept_event(self._handle, end))
                finish_output.extend(self._drain_authorized_audio())
                await self._retire_components("capture_finished", preserve_output=True)
                if self._output_revoked:
                    return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason="runtime_output_revoked")
                return self._result(finish_output, finished=True, release_output=False)
            except asyncio.CancelledError:
                self._begin_component_retirement()
                raise
            except Exception as exc:
                await self._retire_components(f"finish_failed:{type(exc).__name__}")
                return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason=str(exc))


    def _finish_remaining(self) -> float:
        assert self._finish_deadline is not None
        remaining = self._finish_deadline - time.monotonic()
        if remaining <= 0:
            raise InterceptionRuntimeError("finish_deadline_expired")
        return remaining


    @staticmethod
    def _consume_finish_outcome(task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()

    async def _expire_prefix(self) -> None:
        deadline = self._capture_deadline
        if deadline is None:
            return
        delay = max(0.0, deadline - time.monotonic())
        await asyncio.sleep(delay)
        if not self._closed and self._capture_deadline == deadline:
            await self._retire_components("prefix_deadline_expired")

    def _arm_deadline(self, deadline: float) -> None:
        if self._capture_deadline == deadline:
            return
        self._capture_deadline = deadline
        task, self._deadline_task = self._deadline_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        self._deadline_task = asyncio.create_task(self._expire_prefix(), name="prewire-prefix-deadline")

    def _refresh_deadline(self) -> None:
        oldest_sample = min((identity.original_range.start for identity in self._pending_audio), default=self._settled_sample)
        while self._ingress_anchors and self._ingress_anchors[0][0] <= oldest_sample:
            self._ingress_anchors.popleft()
        if self._ingress_anchors:
            self._arm_deadline(self._ingress_anchors[0][1] + self._capture_support_seconds + self._config.prefix_deadline_seconds)

    def _check_output_authority(self) -> None:
        if self._closed or self._output_revoked:
            raise InterceptionRuntimeError("runtime_output_revoked")

    async def _await_operation(self, operation: Callable[[], Awaitable]):
        """Bound caller waiting while retaining the actual execution owner.

        Shield prevents cancellation of a to_thread wrapper from being mistaken
        for native exit. Component close must stop delegated work, and the
        tracked operation must itself return before factory handover.
        """
        self._check_output_authority()
        timeout = self._config.scoring_deadline_seconds
        if self._capture_deadline is not None:
            timeout = min(timeout, self._capture_deadline - time.monotonic())
        if self._finish_deadline is not None:
            timeout = min(timeout, self._finish_remaining())
        if timeout <= 0:
            raise InterceptionRuntimeError("prefix_deadline_expired")
        # Admission happens before constructing or scheduling model work.
        task = asyncio.create_task(operation())
        self._operations.add(task)
        task.add_done_callback(self._operations.discard)
        task.add_done_callback(self._consume_finish_outcome)
        result = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        self._check_output_authority()
        return result

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
            False,  # Capture finish is not a validated endpoint boundary.
            False,  # Planner tails are not independent utterances.
        )
        qualities = None
        if self._quality_analyzer is not None:
            qualities = tuple(
                [await self._analyze_quality(item) for item in scoring_ranges]
            )
        submission = self._gate.submit_interval(spec, scoring_ranges=scoring_ranges, quality_summaries=qualities)
        timeout = self._config.scoring_deadline_seconds
        if self._capture_deadline is not None:
            timeout = min(timeout, self._capture_deadline - time.monotonic())
        if self._finish_deadline is not None:
            timeout = min(timeout, self._finish_remaining())
        if timeout <= 0:
            raise InterceptionRuntimeError("prefix_deadline_expired")
        plan = await asyncio.wait_for(self._gate.resolve(submission), timeout=timeout)
        if self._closed:
            raise InterceptionRuntimeError("runtime_closed_while_scoring")
        record = self._gate.get_interval_record(identity)
        if record is not None and record.decision is PrewireDecisionState.UNAVAILABLE:
            if not event_ended or record.decision_reason != "scoring_window_unsupported":
                raise InterceptionRuntimeError(record.decision_reason or "scoring_unavailable")
        if record is not None and record.decision is PrewireDecisionState.UNCERTAIN:
            plan = self._gate.finalize_uncertain(submission)
        if plan is None:
            return [], False
        output, owner = self._accept_plan(plan)
        self._gate.claim(plan)
        self._settled_sample = plan.ledger_plan.original_cursor_end
        for release in plan.ledger_plan.releases:
            self._pending_audio[release.identity].claimed = True
        return output, owner

    async def _analyze_quality(self, sample_range: SampleRange) -> PrewireQualitySummary:
        assert self._quality_analyzer is not None
        pcm16 = self._gate_pcm(sample_range)
        analyze_async = getattr(self._quality_analyzer, "analyze_async", None)
        if callable(analyze_async):
            return await self._await_operation(lambda: analyze_async(pcm16, 16_000))
        return await self._await_operation(lambda: asyncio.to_thread(self._quality_analyzer.analyze, pcm16, 16_000))

    async def _submit_with_streak(self, planned, *, event_ended: bool) -> list[bytes]:
        """Require two consecutive KEEP windows without duplicating a score."""
        _, owner = await self._submit_planned(planned, event_ended=event_ended)
        if owner:
            self._owner_streak += 1
            if self._owner_streak >= self._config.owner_streak_required:
                for identity in self._unconfirmed_owner_run:
                    self._pending_audio[identity].confirmed = True
                self._unconfirmed_owner_run.clear()
        else:
            self._owner_streak = 0
            self._cancel_unconfirmed_audio()
        return self._drain_authorized_audio()

    def _cancel_unconfirmed_audio(self) -> None:
        for identity in self._unconfirmed_owner_run:
            self._gate.cancel_local_delivery(identity)
            pending = self._pending_audio.pop(identity)
            self._pending_audio_bytes -= len(pending.pcm16 or b"")
        self._unconfirmed_owner_run.clear()

    def _collect_extracted(self, events) -> None:
        for event in events:
            if not isinstance(event, ExtractedPrewireAudio):
                continue
            pending = self._pending_audio.get(event.identity)
            if pending is None:
                # A gap revoked this unconfirmed interval while TSE was late.
                continue
            if pending.pcm16 is not None:
                raise InterceptionRuntimeError("duplicate_target_audio")
            if self._pending_audio_bytes + len(event.pcm16) > self._config.max_held_pcm_bytes:
                raise InterceptionRuntimeError("authorized_audio_capacity")
            pending.pcm16 = event.pcm16
            self._pending_audio_bytes += len(event.pcm16)

    def _drain_authorized_audio(self) -> list[bytes]:
        output = []
        # Dict insertion order is the original gate interval order, independent
        # of whether PCM arrived in append_extracted or accept_event.
        while self._pending_audio:
            identity = next(iter(self._pending_audio))
            pending = self._pending_audio[identity]
            if not pending.confirmed or pending.pcm16 is None:
                break
            output.append(pending.pcm16)
            self._outgoing_audio[identity] = pending.pcm16
            del self._pending_audio[identity]
        return output

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
            self._collect_extracted(events)

    def _accept_plan(self, plan: PrewireGatePlan) -> tuple[list[bytes], bool]:
        assert self._handle is not None
        owner = False
        interrupted = False
        for event in plan.events:
            if event.kind == "gap":
                interrupted = True
            elif event.kind == "audio":
                owner = True
                if len(self._pending_audio) + len(self._outgoing_audio) >= self._config.extraction_max_pending_events:
                    raise InterceptionRuntimeError("authorized_event_capacity")
                self._pending_audio[event.identity] = _PendingTargetAudio()
                self._unconfirmed_owner_run.append(event.identity)
            events = self._extraction.accept_event(self._handle, event)
            self._collect_extracted(events)
        return [], owner and not interrupted

    def _result(self, output: list[bytes], *, finished: bool = False, release_output: bool = True) -> InterceptionResult:
        if self._output_revoked:
            return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason="runtime_output_revoked")
        if output:
            result = InterceptionResult(InterceptionDecision.KEEP, b"".join(output), "filtered_target_audio")
            if release_output:
                self._release_output()
            return result
        if finished:
            return InterceptionResult(InterceptionDecision.DROP, reason="capture_finished")
        return InterceptionResult(InterceptionDecision.PENDING, reason="awaiting_identity_evidence")

    def _release_output(self) -> None:
        # No await between relinquishing identities and the public return.
        self._pending_audio_bytes -= sum(map(len, self._outgoing_audio.values()))
        self._outgoing_audio.clear()

    async def _retire_components(self, reason: str, *, preserve_output: bool = False) -> None:
        task = self._begin_component_retirement(preserve_output=preserve_output)
        # The runtime owns cleanup even if its process/finish/close waiter dies.
        await asyncio.shield(task)

    def _begin_component_retirement(self, *, preserve_output: bool = False) -> asyncio.Task[None]:
        self._closed = True
        if not preserve_output:
            self._output_revoked = True
        deadline_task, self._deadline_task = self._deadline_task, None
        if deadline_task is not None and deadline_task is not asyncio.current_task():
            deadline_task.cancel()
        self._extraction.close()
        identities = tuple(identity for identity, pending in self._pending_audio.items() if pending.claimed)
        if not preserve_output:
            identities += tuple(self._outgoing_audio)
        for identity in identities:
            self._gate.cancel_local_delivery(identity)
        self._pending_audio.clear()
        self._unconfirmed_owner_run.clear()
        if not preserve_output:
            self._outgoing_audio.clear()
        self._pending_audio_bytes = sum(map(len, self._outgoing_audio.values()))
        self._planned_ranges.clear()
        self._ingress_anchors.clear()
        task = self._component_retirement_task
        if task is None or (task.done() and not self._retirement_confirmed):
            self._retirement_confirmed = False
            task = asyncio.create_task(self._close_components(), name="prewire-components-retire")
            self._component_retirement_task = task
        return task

    async def _close_components(self) -> None:
        deadline = time.monotonic() + self._config.scoring_close_timeout_seconds
        gate_task = self._gate_close_task
        if gate_task is None:
            gate_task = asyncio.create_task(self._gate.close(), name="prewire-gate-close")
            gate_task.add_done_callback(self._consume_finish_outcome)
            self._gate_close_task = gate_task
        tse = self._tse
        tse_task = self._tse_close_task
        if tse is not None and (tse_task is None or (tse_task.done() and not self._close_task_succeeded(tse_task, require_true=True))):
            tse_task = asyncio.create_task(tse.close(timeout=max(0.0, deadline - time.monotonic())), name="prewire-extractor-close")
            tse_task.add_done_callback(self._consume_finish_outcome)
            self._tse_close_task = tse_task
        tasks = {task for task in (gate_task, tse_task) if task is not None and not task.done()}
        if tasks:
            await asyncio.wait(tasks, timeout=max(0.0, deadline - time.monotonic()))
        self._retirement_confirmed = self._components_stopped()
        if self._retirement_confirmed:
            self._tse = None


    @staticmethod
    def _close_task_succeeded(task: asyncio.Task | None, *, require_true: bool = False) -> bool:
        if task is None or not task.done() or task.cancelled():
            return False
        try:
            result = task.result()
            return result is True if require_true else True
        except Exception:
            return False


    def _components_stopped(self) -> bool:
        return bool(
            self._close_task_succeeded(self._gate_close_task)
            and self._scheduler.retirement_confirmed
            and (self._tse is None or self._close_task_succeeded(self._tse_close_task, require_true=True))
            and self._explicit_owner_stopped(self._tse)
            and self._explicit_owner_stopped(self._quality_analyzer)
            and not any(not task.done() for task in self._operations)
        )

    @staticmethod
    def _explicit_owner_stopped(owner) -> bool:
        # Legacy operations remain bound by awaited completion/close. A model
        # that additionally declares delegated physical ownership must provide
        # its affirmative evidence, even after its coroutine returned normally.
        try:
            evidence = getattr(owner, "retirement_confirmed")
        except AttributeError:
            try:
                declaration = inspect.getattr_static(owner, "retirement_confirmed", _NO_RETIREMENT_EVIDENCE)
                return declaration is _NO_RETIREMENT_EVIDENCE
            except Exception:
                return False
        except Exception:
            return False
        return evidence is True

    @property
    def is_closed(self) -> bool:
        """Terminal capture fence; physical retirement remains independent."""
        return self._closed

    @property
    def retirement_confirmed(self) -> bool:
        if self._closed:
            self._retirement_confirmed = self._components_stopped()
        return self._retirement_confirmed

    @property
    def retired(self) -> bool:
        task = self._component_retirement_task
        return self._closed and self.retirement_confirmed and not self._outgoing_audio and (task is None or task.done())

    async def close(self, reason: str = "retired") -> None:
        # Fence output before waiting for a process that may be stalled in an
        # injected model await. The owner-local cleanup task has its own budget.
        await self._retire_components(reason)


class PrewireInterceptionFactory(ActiveSessionInterceptionFactory):
    """App-owned injected dependencies with one physical runtime slot.

    Multiple Core managers may retain this factory for serial handover only.
    Creation is unavailable while another runtime owns the slot or its
    physical retirement is unconfirmed; sharing does not permit concurrency.
    """

    def __init__(self, config: InterceptionRuntimeConfig, *, score_backend, classifier, tse_factory, quality_analyzer=None):
        _validate_config(config, score_backend)
        self._config = config
        self._score_backend = score_backend
        self._classifier = classifier
        self._tse_factory = tse_factory
        self._quality_analyzer = quality_analyzer
        self._retired = False
        self._runtimes: list[PrewireInterceptionRuntime] = []
        self._next_ingress_generation = config.ingress_generation

    @property
    def is_available(self) -> bool:
        """Static authority readiness; does not allocate a model or claim a slot."""
        return not self._retired

    def create(self, generation: object, *, ingress_token: object | None) -> PrewireInterceptionRuntime:
        if self._retired:
            raise InterceptionRuntimeError("factory_closed")
        self._runtimes = [existing for existing in self._runtimes if not existing.retired]
        if self._runtimes:
            raise InterceptionRuntimeError("previous_runtime_retirement_pending")
        # Captures can restart inside one authorized route. A fresh sample axis
        # must never reuse the previous ledger/delivery identity.
        config = replace(self._config, ingress_generation=self._next_ingress_generation)
        self._next_ingress_generation += 1
        runtime = PrewireInterceptionRuntime(
            config,
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
