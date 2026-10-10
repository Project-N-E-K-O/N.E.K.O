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
import hashlib
import time
from typing import Awaitable, Callable, Protocol

import numpy as np

from main_logic.voice_input.interception import (
    ActiveSessionInterceptionFactory,
    ActiveSessionInterceptionRuntime,
    InterceptionDecision,
    InterceptionResult,
)
from main_logic.voice_turn.interception_events import (
    InterceptionDeliveryReceipt,
    InterceptionDeliveryStage,
    InterceptionOutputEvent,
    InterceptionOutputIdentity,
    InterceptionOutputKind,
)

from .prewire_gate.contracts import (
    PrewireCommitStage,
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
from .prewire_calibration import PrewireCalibrationClassifier
from .candidate_identity import CandidateIdentitySelector
from .candidate_source import CandidateAudioBuffer, CandidateSource
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
    # Explicit experimental scorer capabilities; no additional length is
    # inferred from transport chunks or capture completion.
    scoring_window_samples: tuple[int, ...] | None = None
    scorer_capabilities: ScorerCapabilities | None = None
    finish_timeout_seconds: float = 1.0
    # Sampling support and processing are different clocks. None derives only
    # the necessary live capture support; prefix_deadline_seconds is the
    # additional processing/jitter budget, never a renewed inactivity timer.
    capture_support_timeout_seconds: float | None = None


def _scoring_plan_for_config(config: InterceptionRuntimeConfig) -> ScoringWindowPlan:
    """Validate preparation without allocating a scorer or starting models."""
    _validate_config(config, None)
    InterceptionOutputIdentity(
        config.session_id, config.ingress_generation, config.profile_generation,
        config.model_generation, config.config_generation,
    )
    plan = ScoringWindowPlan(
        (config.window_samples,) if config.scoring_window_samples is None else config.scoring_window_samples
    )
    PrewireWindowPlanner(
        window_samples=config.window_samples, step_samples=config.step_samples,
        guard_samples=config.guard_samples, scoring_sample_counts=plan.sample_counts,
    )
    return plan


@dataclass(slots=True)
class _PendingTargetAudio:
    pcm16: bytes | None = None
    confirmed: bool = False
    claimed: bool = False


@dataclass(slots=True)
class _PendingOutputInterval:
    identity: PrewireIntervalIdentity
    original_range: SampleRange
    asr_range: SampleRange | None
    gap_reason: str | None = None
    deadline_monotonic: float = 0.0

def _validate_config(config: InterceptionRuntimeConfig, score_backend) -> float:
    """Validate static support before allocating scheduler or model owners."""
    if type(config.required_consistent_observations) is not int or config.required_consistent_observations != 1:
        raise ValueError("runtime uses cross-window owner streak; gate required_consistent_observations must be 1")
    if type(config.owner_streak_required) is not int or config.owner_streak_required <= 0:
        raise ValueError("owner_streak_required must be positive")
    plan = ScoringWindowPlan((config.window_samples,) if config.scoring_window_samples is None else config.scoring_window_samples)
    if config.window_samples not in plan.sample_counts or any(count > config.window_samples for count in plan.sample_counts):
        raise ValueError("scoring plan must include the live window and only its shorter tails")
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
            raise ValueError(f"{name} cannot retain owner confirmation support")
    for name in ("max_outstanding_jobs", "extraction_max_pending_events"):
        value = getattr(config, name)
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if config.extraction_max_pending_events < config.owner_streak_required:
        raise ValueError("extraction_max_pending_events cannot retain owner streak")
    for capabilities in (config.scorer_capabilities, getattr(score_backend, "capabilities", None)):
        if capabilities is not None:
            if type(capabilities) is not ScorerCapabilities:
                raise ValueError("capabilities must be ScorerCapabilities")
            capabilities.require_support(plan.sample_counts, model_generation=config.model_generation)
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
        candidate_selector: CandidateIdentitySelector | None = None,
        candidate_source: CandidateSource | None = None,
    ) -> None:
        self._config = config
        window_plan = _scoring_plan_for_config(config)

        self._capture_support_seconds = _validate_config(config, score_backend)
        self._score_backend = score_backend
        self._classifier = classifier
        self._candidate_selector = candidate_selector
        if candidate_source is not None and candidate_selector is None:
            raise ValueError("candidate source requires a candidate selector")
        if candidate_selector is not None:
            binding = candidate_selector.binding
            if (binding.stream, binding.profile_generation, binding.model_generation, binding.config_generation, binding.scoring_parameters_digest) != (
                PrewireStreamKey(config.session_id, config.ingress_generation), config.profile_generation,
                config.model_generation, config.config_generation, config.scoring_parameters_digest,
            ):
                raise ValueError("candidate selector does not match this runtime")
        self._candidate_source = candidate_source or (
            CandidateAudioBuffer(candidate_selector.binding, max_buffered_pcm_bytes=config.extraction_max_buffered_pcm_bytes)
            if candidate_selector is not None else None
        )
        self._candidate_pcm: dict[PrewireIntervalIdentity, bytes] = {}
        self._candidate_close_task: asyncio.Task | None = None
        self._tse_factory = tse_factory
        self._quality_analyzer = quality_analyzer
        self._generation_token = generation_token
        self._ingress_token = ingress_token
        self._scheduler = ControlledScoringScheduler(
            score_backend,
            window_plan=window_plan,
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
        self._output_order: dict[PrewireIntervalIdentity, _PendingOutputInterval] = {}
        self._outgoing_events: list[InterceptionOutputEvent] = []
        self._delivery_events: dict[str, tuple[tuple[object, ...], PrewireIntervalIdentity]] = {}
        # Transfer releases pending debt, not the ability to record a genuine
        # ACK while the original bounded ledger record is still retained.
        self._owned_delivery_events: dict[str, tuple[tuple[object, ...], PrewireIntervalIdentity]] = {}
        self._output_sequence = 0
        # This is the PCM axis actually handed out by this runtime. Ledger
        # reservations remain immutable even when an OWNER prefix is cancelled.
        self._output_asr_cursor = 0
        self._output_identity = InterceptionOutputIdentity(
            config.session_id, config.ingress_generation, config.profile_generation,
            config.model_generation, config.config_generation,
        )
        self._unconfirmed_owner_run: list[PrewireIntervalIdentity] = []
        self._pending_audio_bytes = 0
        self._started = False
        self._closed = False
        self._retirement_confirmed = True
        self._component_retirement_task: asyncio.Task[None] | None = None
        self._gate_close_task: asyncio.Task | None = None
        self._tse_close_task: asyncio.Task | None = None
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
        self._finish_deadline: float | None = None

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
                if self._handle is not None and self._candidate_selector is None:
                    self._collect_extracted(self._extraction.accept_event(self._handle, end))
                finish_output.extend(self._drain_authorized_audio())
                # Flushing cannot manufacture missing extraction or identity
                # evidence. Preserve a visible gap for every unreturned range.
                for identity, pending in tuple(self._pending_audio.items()):
                    if pending.claimed:
                        self._gate.cancel_local_delivery(identity)
                    self._pending_audio_bytes -= len(pending.pcm16 or b"")
                    self._output_order[identity].gap_reason = "capture_finished_without_target_audio"
                    del self._pending_audio[identity]
                finish_output.extend(self._drain_authorized_audio())
                self._outgoing_events.append(self._make_output_event(
                    InterceptionOutputKind.END,
                    interval_id=f"end:{self._segment + 1}",
                    original_range=(end.original_cursor, end.original_cursor),
                    reason="capture_finished",
                ))
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
        if self._candidate_selector is not None:
            assert self._candidate_source is not None
            if planned.scoring_range.sample_count not in self._scheduler.window_plan.sample_counts:
                # Unsupported terminal tails still need the existing explicit
                # gap path; no candidate inference or zero-padding is attempted.
                if not event_ended:
                    raise InterceptionRuntimeError("candidate_scoring_window_unsupported")
            else:
                batch = self._candidate_source.batch_for(spec, self._candidate_selector.binding)
                timeout = self._config.scoring_deadline_seconds
                if self._capture_deadline is not None:
                    timeout = min(timeout, self._capture_deadline - time.monotonic())
                if self._finish_deadline is not None:
                    timeout = min(timeout, self._finish_remaining())
                if timeout <= 0:
                    raise InterceptionRuntimeError("candidate_deadline_expired")
                candidate_deadline = time.monotonic() + timeout
                if self._quality_analyzer is not None:
                    candidates = []
                    for candidate in batch.candidates:
                        remaining = candidate_deadline - time.monotonic()
                        if remaining <= 0:
                            raise InterceptionRuntimeError("candidate_deadline_expired")
                        quality = await asyncio.wait_for(self._analyze_pcm_quality(candidate.pcm16), timeout=remaining)
                        if self._closed:
                            raise InterceptionRuntimeError("runtime_closed_while_analyzing_candidate")
                        candidates.append(replace(candidate, quality=quality))
                    batch = replace(batch, candidates=tuple(candidates))
                remaining = candidate_deadline - time.monotonic()
                if remaining <= 0:
                    raise InterceptionRuntimeError("candidate_deadline_expired")
                selection = await asyncio.wait_for(self._await_operation(lambda: self._candidate_selector.select(batch)), timeout=remaining)
                selection.validate_for(spec, self._candidate_selector.binding)
                if selection.decision is PrewireDecisionState.UNAVAILABLE:
                    raise InterceptionRuntimeError(selection.reason)
                plan = self._gate.submit_candidate_interval(spec, selection, expected_binding=self._candidate_selector.binding)
                if selection.decision is PrewireDecisionState.KEEP:
                    self._candidate_pcm[identity] = selection.commit_pcm16
                if plan is None:
                    return [], False
                output, owner = self._accept_plan(plan)
                self._gate.claim(plan)
                self._settled_sample = plan.ledger_plan.original_cursor_end
                for release in plan.ledger_plan.releases:
                    self._pending_audio[release.identity].claimed = True
                self._candidate_source.discard_before(plan.ledger_plan.original_cursor_end)
                return output, owner
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
        pcm16 = self._gate_pcm(sample_range)
        return await self._analyze_pcm_quality(pcm16)

    async def _analyze_pcm_quality(self, pcm16: bytes) -> PrewireQualitySummary:
        assert self._quality_analyzer is not None
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
            self._output_order[identity].gap_reason = "owner_run_unconfirmed"
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
        while self._output_order:
            identity = next(iter(self._output_order))
            interval = self._output_order[identity]
            if interval.gap_reason is not None:
                self._outgoing_events.append(self._make_output_event(
                    InterceptionOutputKind.GAP,
                    interval_id=str(identity.segment_id),
                    original_range=interval.original_range,
                    reason=interval.gap_reason,
                ))
                del self._output_order[identity]
                continue
            pending = self._pending_audio[identity]
            if time.monotonic() >= interval.deadline_monotonic:
                # The timer may be waiting for this very process/finish lock.
                # A late extractor must never win that race and publish audio.
                raise InterceptionRuntimeError("target_audio_deadline_expired")
            if not pending.confirmed or pending.pcm16 is None:
                break
            output.append(pending.pcm16)
            self._outgoing_audio[identity] = pending.pcm16
            event = self._make_output_event(
                InterceptionOutputKind.AUDIO,
                interval_id=str(identity.segment_id),
                original_range=interval.original_range,
                asr_range=interval.asr_range,
                pcm16=pending.pcm16,
                reason="filtered_target_audio",
            )
            self._outgoing_events.append(event)
            del self._pending_audio[identity]
            del self._output_order[identity]
        return output

    def _make_output_event(
        self, kind: InterceptionOutputKind, *, interval_id: str,
        original_range: SampleRange | tuple[int, int], asr_range: SampleRange | None = None,
        pcm16: bytes = b"", reason: str,
    ) -> InterceptionOutputEvent:
        start, end = ((original_range.start, original_range.end)
                      if isinstance(original_range, SampleRange) else original_range)
        asr_start = asr_end = None
        if kind is InterceptionOutputKind.AUDIO:
            asr_start = self._output_asr_cursor
            asr_end = asr_start + end - start
            self._output_asr_cursor = asr_end
        event = InterceptionOutputEvent(
            self._output_identity, interval_id, self._output_sequence, kind,
            start, end,
            asr_start_sample=asr_start,
            asr_end_sample=asr_end,
            pcm16=pcm16, reason=reason,
        )
        self._output_sequence += 1
        return event

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
            if self._candidate_selector is not None:
                if isinstance(self._candidate_source, CandidateAudioBuffer):
                    self._candidate_source.append_pcm(start_sample=chunk.start_sample, pcm16=pcm16)
                # External full-window batches have independent channel
                # identity; single TSE chunks cannot populate those batches.
                continue
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
            if len(self._output_order) + len(self._outgoing_events) >= self._config.extraction_max_pending_events:
                raise InterceptionRuntimeError("authorized_event_capacity")
            if event.kind == "gap":
                interrupted = True
                self._output_order[event.identity] = _PendingOutputInterval(
                    event.identity, event.original_range, None, event.reason,
                    time.monotonic() + self._config.prefix_deadline_seconds,
                )
            elif event.kind == "audio":
                owner = True
                if len(self._pending_audio) + len(self._outgoing_audio) >= self._config.extraction_max_pending_events:
                    raise InterceptionRuntimeError("authorized_event_capacity")
                self._pending_audio[event.identity] = _PendingTargetAudio()
                self._output_order[event.identity] = _PendingOutputInterval(
                    event.identity, event.original_range, event.asr_range,
                    deadline_monotonic=time.monotonic() + self._config.prefix_deadline_seconds,
                )
                self._unconfirmed_owner_run.append(event.identity)
            if self._candidate_selector is not None:
                if event.kind == "audio":
                    pcm16 = self._candidate_pcm.pop(event.identity)
                    events = [ExtractedPrewireAudio(event.identity, event.original_range, event.asr_range, pcm16)]
                else:
                    self._candidate_pcm.pop(event.identity, None)
                    events = []
            else:
                events = self._extraction.accept_event(self._handle, event)
            self._collect_extracted(events)
        return [], owner and not interrupted

    def _result(self, output: list[bytes], *, finished: bool = False, release_output: bool = True) -> InterceptionResult:
        if self._output_revoked:
            return InterceptionResult(InterceptionDecision.UNAVAILABLE, reason="runtime_output_revoked")
        self._prune_owned_delivery_events()
        events = tuple(self._outgoing_events)
        if output:
            result = InterceptionResult(InterceptionDecision.KEEP, b"".join(output), "filtered_target_audio", events=events)
            # Keep only immutable, bounded evidence needed to settle actual
            # delivery. Never turn the function return into a remote receipt.
            if len(self._delivery_events) + len(self._outgoing_audio) > self._config.extraction_max_pending_events:
                raise InterceptionRuntimeError("delivery_evidence_capacity")
            if release_output:
                self._release_output()
            return result
        if release_output:
            self._release_output()
        if finished:
            return InterceptionResult(InterceptionDecision.DROP, reason="capture_finished", events=events)
        return InterceptionResult(InterceptionDecision.PENDING, reason="awaiting_identity_evidence", events=events)

    def _prune_owned_delivery_events(self) -> None:
        for interval_id, (_, identity) in tuple(self._owned_delivery_events.items()):
            if self._gate.get_interval_record(identity) is None:
                del self._owned_delivery_events[interval_id]

    def record_delivery(self, receipt: InterceptionDeliveryReceipt) -> bool:
        """Settle only an exact handed-out interval, including after close.

        This owner-local method cannot mutate a successor. Local admission is
        not transport evidence; UNKNOWN never permits replay or eviction.
        """
        if type(receipt) is not InterceptionDeliveryReceipt:
            raise TypeError("interception delivery receipt required")
        event = receipt.event
        if event.identity != self._output_identity:
            return False
        self._prune_owned_delivery_events()
        retained = self._delivery_events.get(event.interval_id) or self._owned_delivery_events.get(event.interval_id)
        if retained is None or retained[0] != self._event_fingerprint(event):
            return False
        identity = retained[1]
        record = self._gate.get_interval_record(identity)
        if record is None:
            return False
        current = record.commit_stage
        stage = receipt.stage
        if stage in {InterceptionDeliveryStage.LOCAL_ACCEPTED, InterceptionDeliveryStage.QUEUED}:
            return True
        if stage is InterceptionDeliveryStage.NOT_SENT:
            if current is not PrewireCommitStage.ENQUEUED:
                return False
            self._gate.cancel_local_delivery(identity)
            del self._delivery_events[event.interval_id]
            return True
        if stage is InterceptionDeliveryStage.TRANSPORT_WRITTEN:
            if current is PrewireCommitStage.ENQUEUED:
                self._gate.advance_delivery(identity, expected=current, next_stage=PrewireCommitStage.WRITTEN)
            return current in {PrewireCommitStage.ENQUEUED, PrewireCommitStage.WRITTEN}
        if stage is InterceptionDeliveryStage.TRANSPORT_OWNED:
            if current is PrewireCommitStage.TRANSPORT_OWNED:
                return True
            if current is not PrewireCommitStage.WRITTEN:
                return False
            self._gate.advance_delivery(
                identity, expected=current, next_stage=PrewireCommitStage.TRANSPORT_OWNED,
            )
            self._owned_delivery_events[event.interval_id] = retained
            del self._delivery_events[event.interval_id]
            return True
        if stage is InterceptionDeliveryStage.UNKNOWN:
            if current in {PrewireCommitStage.ENQUEUED, PrewireCommitStage.WRITTEN}:
                self._gate.advance_delivery(identity, expected=current, next_stage=PrewireCommitStage.UNKNOWN)
            return current in {PrewireCommitStage.ENQUEUED, PrewireCommitStage.WRITTEN, PrewireCommitStage.UNKNOWN}
        if stage is InterceptionDeliveryStage.PROVIDER_CONFIRMED:
            if current is PrewireCommitStage.ENQUEUED:
                self._gate.advance_delivery(identity, expected=current, next_stage=PrewireCommitStage.WRITTEN)
                current = PrewireCommitStage.WRITTEN
            if current in {PrewireCommitStage.WRITTEN, PrewireCommitStage.UNKNOWN, PrewireCommitStage.TRANSPORT_OWNED}:
                self._gate.advance_delivery(identity, expected=current, next_stage=PrewireCommitStage.REMOTE_CONFIRMED)
                self._delivery_events.pop(event.interval_id, None)
                self._owned_delivery_events.pop(event.interval_id, None)
                return True
        return False

    @staticmethod
    def _event_fingerprint(event: InterceptionOutputEvent) -> tuple[object, ...]:
        # Settle exact output without retaining an extra audio copy until a
        # provider-specific acknowledgement eventually arrives.
        return (
            event.identity, event.interval_id, event.sequence, event.kind,
            event.start_sample, event.end_sample, event.sample_rate_hz,
            event.asr_start_sample, event.asr_end_sample, event.reason,
            hashlib.sha256(event.pcm16).digest(),
        )

    def _release_output(self) -> None:
        # No await between relinquishing identities and the public return.
        identities = {str(identity.segment_id): identity for identity in self._outgoing_audio}
        for event in self._outgoing_events:
            if event.kind is InterceptionOutputKind.AUDIO:
                self._delivery_events[event.interval_id] = (self._event_fingerprint(event), identities[event.interval_id])
        self._pending_audio_bytes -= sum(map(len, self._outgoing_audio.values()))
        self._outgoing_audio.clear()
        self._outgoing_events.clear()

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
        self._output_order.clear()
        self._unconfirmed_owner_run.clear()
        if not preserve_output:
            self._outgoing_audio.clear()
            self._outgoing_events.clear()
        self._pending_audio_bytes = sum(map(len, self._outgoing_audio.values()))
        self._planned_ranges.clear()
        self._candidate_pcm.clear()
        if self._candidate_source is not None:
            self._candidate_source.clear()

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
        candidate_task = self._candidate_close_task
        if self._candidate_selector is not None and candidate_task is None:
            candidate_task = asyncio.create_task(self._candidate_selector.close(), name="prewire-candidate-close")
            candidate_task.add_done_callback(self._consume_finish_outcome)
            self._candidate_close_task = candidate_task
        if candidate_task is not None and not candidate_task.done():
            tasks.add(candidate_task)
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
            and (self._candidate_selector is None or (
                self._close_task_succeeded(self._candidate_close_task) and self._candidate_selector.retirement_confirmed
            ))
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
        return self._closed and self.retirement_confirmed and not self._outgoing_audio and not self._outgoing_events and (task is None or task.done())

    async def close(self, reason: str = "retired") -> None:
        # Fence output before waiting for a process that may be stalled in an
        # injected model await. The owner-local cleanup task has its own budget.
        await self._retire_components(reason)


class PrewireInterceptionFactory(ActiveSessionInterceptionFactory):
    """One physical slot for an injected backend, reusable only after retirement.

    The application owns this factory and may share it across Core managers.
    Handover is serial: creating the next runtime remains unavailable while
    physical retirement is unconfirmed; sharing does not permit concurrency.
    """

    def __init__(self, config: InterceptionRuntimeConfig, *, score_backend, classifier, tse_factory, quality_analyzer=None, candidate_factory=None, candidate_source_factory=None):
        _validate_config(config, score_backend)
        self._config = config
        self._score_backend = score_backend
        self._classifier = classifier
        self._tse_factory = tse_factory
        self._quality_analyzer = quality_analyzer
        self._candidate_factory = candidate_factory
        self._candidate_source_factory = candidate_source_factory
        self._retired = False
        self._runtimes: list[PrewireInterceptionRuntime] = []
        self._next_ingress_generation = config.ingress_generation

    @classmethod
    def for_production(
        cls, config: InterceptionRuntimeConfig, *, score_backend,
        classifier: PrewireCalibrationClassifier, tse_factory: TSEFactory,
        quality_analyzer: QualityAnalyzer,
        candidate_factory=None, candidate_source_factory=None,
    ) -> PrewireInterceptionFactory:
        """Check prerequisites while candidate release remains unavailable.

        The generic constructor remains an explicit injection seam for
        experiments. Production preparation requires an actual registered
        continuous package rather than a terminal-short calibration. That raw
        package cannot certify extracted candidates. This implementation has
        no registered candidate release; a research selector or a persisted
        protocol declaration must never make production preparation succeed.
        The explicit constructor remains the research-only injection seam.
        """
        if type(classifier) is not PrewireCalibrationClassifier:
            raise InterceptionRuntimeError("continuous_calibration_package_unavailable")
        if score_backend is None or tse_factory is None or quality_analyzer is None:
            raise InterceptionRuntimeError("interception_model_dependencies_unavailable")
        if config.owner_streak_required < 2:
            raise InterceptionRuntimeError("continuous_owner_confirmation_required")
        try:
            capabilities = score_backend.capabilities
        except AttributeError as exc:
            raise InterceptionRuntimeError("scorer_capabilities_unavailable") from exc
        if type(capabilities) is not ScorerCapabilities or config.scorer_capabilities != capabilities:
            raise InterceptionRuntimeError("scorer_capabilities_mismatch")
        counts = _scoring_plan_for_config(config).sample_counts
        classifier.require_continuous_support(
            counts, parameters_digest=config.scoring_parameters_digest,
            decision_sample_ranges=tuple(
                (count, 0, min(count, config.step_samples + config.guard_samples))
                for count in counts
            ),
            profile_generation=config.profile_generation,
            model_generation=config.model_generation,
            config_generation=config.config_generation,
        )
        if not callable(candidate_factory):
            raise InterceptionRuntimeError("candidate_identity_dependencies_unavailable")
        raise InterceptionRuntimeError("candidate_calibration_release_unavailable")

    @property
    def is_available(self) -> bool:
        """Static authority readiness; does not allocate a model or claim a slot."""
        return not self._retired

    def create(self, generation: object, *, ingress_token: object | None) -> PrewireInterceptionRuntime:
        if self._retired:
            raise InterceptionRuntimeError("factory_closed")
        # Candidate factories may own native resources. Reject unsupported
        # lengths and confirmation capacity before invoking either factory.
        _scoring_plan_for_config(self._config)
        self._runtimes = [existing for existing in self._runtimes if not existing.retired]
        if self._runtimes:
            raise InterceptionRuntimeError("previous_runtime_retirement_pending")
        # This application-owned factory survives capture restarts. Source
        # axes and event sequence numbers restart, so each capture needs a
        # distinct identity, including a failed construction attempt.
        config = replace(self._config, ingress_generation=self._next_ingress_generation)
        self._next_ingress_generation += 1
        stream = PrewireStreamKey(config.session_id, config.ingress_generation)
        selector = None if self._candidate_factory is None else self._candidate_factory(stream)
        if self._candidate_factory is not None and not isinstance(selector, CandidateIdentitySelector):
            raise InterceptionRuntimeError("candidate_selector_unavailable")
        source = None if self._candidate_source_factory is None else self._candidate_source_factory(stream)
        runtime = PrewireInterceptionRuntime(
            config,
            score_backend=self._score_backend,
            classifier=self._classifier,
            tse_factory=self._tse_factory,
            quality_analyzer=self._quality_analyzer,
            generation_token=generation,
            ingress_token=ingress_token,
            candidate_selector=selector,
            candidate_source=source,
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
