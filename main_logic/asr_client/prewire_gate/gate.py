"""Provider-neutral coordination for locally gated PCM before ASR wiring."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TypeAlias

from .contracts import (
    SAMPLE_RATE_HZ,
    PrewireContiguousPlan,
    PrewireDecisionState,
    PrewireIntervalIdentity,
    PrewireIntervalSpec,
    PrewireStreamKey,
    SampleRange,
)
from .decision import (
    PrewireEvidenceClassifier,
    PrewireQualitySummary,
    PrewireScoreObservation,
    StrictPrewireEvidencePolicy,
)
from .ledger import PrewireIntervalLedger, PrewireTransitionError
from .scheduler import (
    ControlledScoringScheduler,
    IdentityReceipt,
    RawSampleRange,
    SchedulerError,
    ScoreRequest,
    ScoreResultStatus,
    ScoringIdentity,
)


class PrewireGateError(RuntimeError):
    """Base error for invalid gate operations."""


class PrewireGateIdentityError(PrewireGateError):
    pass


class PrewireGateRangeError(PrewireGateError):
    pass


class PrewireGateCapacityError(PrewireGateError):
    pass


@dataclass(frozen=True, slots=True)
class PrewirePlannedRanges:
    """A deterministic range plan with no VAD or speaker semantics."""

    scoring_range: SampleRange
    decision_range: SampleRange
    commit_range: SampleRange


class PrewireWindowPlanner:
    """Plan fixed windows from one continuous raw-sample cursor.

    Transport chunk boundaries never become decision boundaries.  The caller
    separately supplies endpoint trust and independent-event facts when it
    creates ``PrewireIntervalSpec`` instances.
    """

    def __init__(
        self,
        *,
        window_samples: int,
        step_samples: int,
        guard_samples: int,
        start_sample: int = 0,
    ) -> None:
        for name, value in (
            ("window_samples", window_samples),
            ("step_samples", step_samples),
            ("guard_samples", guard_samples),
            ("start_sample", start_sample),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if window_samples == 0 or step_samples == 0:
            raise ValueError("window_samples and step_samples must be positive")
        if window_samples < step_samples + guard_samples:
            raise ValueError("window must contain the commit step and guard")
        self.window_samples = window_samples
        self.step_samples = step_samples
        self.guard_samples = guard_samples
        self._captured_end = start_sample
        self._next_commit_start = start_sample

    @property
    def captured_end(self) -> int:
        return self._captured_end

    def add_samples(self, sample_count: int) -> tuple[PrewirePlannedRanges, ...]:
        if type(sample_count) is not int or sample_count < 0:
            raise ValueError("sample_count must be a non-negative integer")
        self._captured_end += sample_count
        planned: list[PrewirePlannedRanges] = []
        while self._next_commit_start + self.window_samples <= self._captured_end:
            start = self._next_commit_start
            commit = SampleRange(start, start + self.step_samples)
            decision = SampleRange(start, commit.end + self.guard_samples)
            scoring = SampleRange(start, start + self.window_samples)
            planned.append(PrewirePlannedRanges(scoring, decision, commit))
            self._next_commit_start = commit.end
        return tuple(planned)

    def finish_event(self) -> tuple[PrewirePlannedRanges, ...]:
        """Return the remaining tail without assigning endpoint semantics."""
        if self._next_commit_start >= self._captured_end:
            return ()
        tail = SampleRange(self._next_commit_start, self._captured_end)
        self._next_commit_start = self._captured_end
        return (PrewirePlannedRanges(tail, tail, tail),)


@dataclass(frozen=True, slots=True)
class PrewireSubmission:
    identity: PrewireIntervalIdentity


@dataclass(frozen=True, slots=True)
class PrewireAudioEvent:
    identity: PrewireIntervalIdentity
    original_range: SampleRange
    asr_range: SampleRange
    pcm16: bytes
    kind: str = field(default="audio", init=False)


@dataclass(frozen=True, slots=True)
class PrewireGapEvent:
    identity: PrewireIntervalIdentity
    original_range: SampleRange
    decision: PrewireDecisionState
    reason: str
    kind: str = field(default="gap", init=False)


@dataclass(frozen=True, slots=True)
class PrewireEndEvent:
    stream: PrewireStreamKey
    original_cursor: int
    asr_cursor: int
    kind: str = field(default="end", init=False)


PrewireOutputEvent: TypeAlias = PrewireAudioEvent | PrewireGapEvent | PrewireEndEvent


@dataclass(frozen=True, slots=True)
class PrewireGatePlan:
    """A non-mutating delivery proposal which must be claimed after enqueue."""

    ledger_plan: PrewireContiguousPlan
    events: tuple[PrewireAudioEvent | PrewireGapEvent, ...]


@dataclass(slots=True)
class _PendingInterval:
    spec: PrewireIntervalSpec
    receipts: tuple[IdentityReceipt, ...]
    scoring_ranges: tuple[SampleRange, ...]
    observations: list[PrewireScoreObservation] = field(default_factory=list)
    emitted: bool = False
    resolver_task: asyncio.Task[None] | None = None


@dataclass(slots=True)
class _StreamState:
    profile_generation: str
    model_generation: str
    config_generation: str
    buffer_start: int
    pcm16: bytearray = field(default_factory=bytearray)
    ended: bool = False

    @property
    def buffer_end(self) -> int:
        return self.buffer_start + len(self.pcm16) // 2


class PrewireGate:
    """Hold, score, decide, and release PCM without provider assumptions."""

    def __init__(
        self,
        scheduler: ControlledScoringScheduler,
        *,
        classifier: PrewireEvidenceClassifier | None = None,
        ledger: PrewireIntervalLedger | None = None,
        window_samples: int,
        step_samples: int,
        guard_samples: int,
        max_held_pcm_bytes: int,
        scoring_parameters_digest: str,
        required_consistent_observations: int,
    ) -> None:
        if type(max_held_pcm_bytes) is not int or max_held_pcm_bytes <= 0:
            raise ValueError("max_held_pcm_bytes must be positive")
        if (
            type(scoring_parameters_digest) is not str
            or len(scoring_parameters_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in scoring_parameters_digest
            )
        ):
            raise ValueError(
                "scoring_parameters_digest must be a lowercase SHA-256 digest"
            )
        self.planner_parameters = (window_samples, step_samples, guard_samples)
        # Validate the injected range contract once; planners may then be made
        # per stream without sharing a cursor.
        PrewireWindowPlanner(
            window_samples=window_samples,
            step_samples=step_samples,
            guard_samples=guard_samples,
        )
        if window_samples not in scheduler.window_plan.sample_counts:
            raise ValueError(
                "scheduler window plan does not contain gate window_samples"
            )
        self._scheduler = scheduler
        self._ledger = ledger or PrewireIntervalLedger()
        self._policy = StrictPrewireEvidencePolicy(
            classifier,
            required_consistent_observations=required_consistent_observations,
        )
        self._classifier_available = classifier is not None
        self._window_samples = window_samples
        self._step_samples = step_samples
        self._guard_samples = guard_samples
        self._max_held_pcm_bytes = max_held_pcm_bytes
        self._parameters_digest = scoring_parameters_digest
        self._held_pcm_bytes = 0
        self._streams: dict[PrewireStreamKey, _StreamState] = {}
        self._pending: dict[PrewireIntervalIdentity, _PendingInterval] = {}
        self._active_plans: dict[PrewireStreamKey, PrewireGatePlan] = {}
        self._closed = False

    @property
    def held_pcm_bytes(self) -> int:
        return self._held_pcm_bytes

    @property
    def pending_interval_count(self) -> int:
        return len(self._pending)

    def new_planner(self, *, start_sample: int = 0) -> PrewireWindowPlanner:
        window, step, guard = self.planner_parameters
        return PrewireWindowPlanner(
            window_samples=window,
            step_samples=step,
            guard_samples=guard,
            start_sample=start_sample,
        )

    def open_stream(
        self,
        stream: PrewireStreamKey,
        *,
        profile_generation: str,
        model_generation: str,
        config_generation: str,
        original_cursor: int = 0,
        asr_cursor: int = 0,
    ) -> None:
        if self._closed:
            raise PrewireGateError("gate_closed")
        state = _StreamState(
            profile_generation,
            model_generation,
            config_generation,
            original_cursor,
        )
        existing = self._streams.get(stream)
        if existing is not None:
            if (
                existing.profile_generation != profile_generation
                or existing.model_generation != model_generation
                or existing.config_generation != config_generation
            ):
                raise PrewireGateIdentityError(
                    "stream_already_open_with_different_versions"
                )
            return
        self._ledger.open_stream(
            stream,
            original_cursor=original_cursor,
            asr_cursor=asr_cursor,
        )
        self._streams.setdefault(stream, state)

    def append_pcm(
        self,
        stream: PrewireStreamKey,
        *,
        start_sample: int,
        pcm16: bytes,
    ) -> SampleRange:
        """Synchronously retain one transport chunk without awaiting scoring."""
        if self._closed:
            raise PrewireGateError("gate_closed")
        state = self._streams.get(stream)
        if state is None or state.ended:
            raise PrewireGateIdentityError("stream_is_not_current")
        if type(start_sample) is not int or start_sample < 0:
            raise PrewireGateRangeError("start_sample must be non-negative")
        if type(pcm16) is not bytes or not pcm16 or len(pcm16) % 2:
            raise PrewireGateRangeError("pcm16 must contain complete samples")
        if start_sample != state.buffer_end:
            raise PrewireGateRangeError(
                "PCM chunks must be contiguous and exactly ordered"
            )
        if self._held_pcm_bytes + len(pcm16) > self._max_held_pcm_bytes:
            raise PrewireGateCapacityError("local_pcm_capacity")
        retained = bytes(pcm16)
        state.pcm16.extend(retained)
        self._held_pcm_bytes += len(retained)
        return SampleRange(start_sample, start_sample + len(retained) // 2)

    def submit_interval(
        self,
        spec: PrewireIntervalSpec,
        *,
        scoring_ranges: tuple[SampleRange, ...] | None = None,
    ) -> PrewireSubmission:
        """Synchronously reference retained PCM and queue work; never await."""
        if self._closed:
            raise PrewireGateError("gate_closed")
        identity = spec.identity
        if not self._identity_is_current(identity):
            raise PrewireGateIdentityError("interval_identity_is_stale")
        if identity in self._pending:
            raise PrewireGateIdentityError("interval_already_submitted")
        state = self._streams[identity.stream]
        available = SampleRange(state.buffer_start, state.buffer_end)
        if not available.contains(identity.original_range):
            raise PrewireGateRangeError(
                "original_range is not retained in the stream PCM store"
            )
        ranges = scoring_ranges or (spec.scoring_range,)
        self._validate_ranges(spec, ranges)
        self._ledger.add(spec)

        pending = _PendingInterval(spec, (), ranges)
        self._pending[identity] = pending

        immediate = self._policy.decide(
            spec.decision_range,
            (),
            event_ended=spec.event_ended,
            boundary_trusted=spec.boundary_trusted,
            independent_event=spec.independent_event,
        )
        if immediate.used_ended_micro_event_rule:
            self._ledger.decide(
                identity,
                immediate.state,
                reason=immediate.reason,
                used_ended_micro_event_rule=True,
            )
            return PrewireSubmission(identity)
        if not self._classifier_available:
            self._decide_unavailable(pending, "calibration_package_unavailable")
            return PrewireSubmission(identity)

        receipts: list[IdentityReceipt] = []
        try:
            for index, sample_range in enumerate(ranges, start=1):
                raw_range = self._raw_range(sample_range)
                receipts.append(
                    self._scheduler.submit(
                        ScoreRequest(
                            request_id=(
                                f"{identity.stream.session_id}:"
                                f"{identity.stream.ingress_generation}:"
                                f"{identity.segment_id}:{index}"
                            ),
                            identity=self._scoring_identity(identity),
                            sample_range=raw_range,
                            pcm16=self._slice_pcm(identity.stream, sample_range),
                            sample_rate_hz=SAMPLE_RATE_HZ,
                        )
                    )
                )
        except SchedulerError as exc:
            for receipt in receipts:
                self._scheduler.cancel(receipt)
            self._decide_unavailable(
                pending, f"scoring_not_queued:{type(exc).__name__}"
            )
        else:
            pending.receipts = tuple(receipts)
        return PrewireSubmission(identity)

    async def resolve(self, submission: PrewireSubmission) -> PrewireGatePlan | None:
        pending = self._pending.get(submission.identity)
        if pending is None or pending.emitted:
            return self._active_plans.get(submission.identity.stream)
        await self._join_resolution(pending)
        if not self._identity_is_current(submission.identity):
            return None
        return self._plan_stream(submission.identity.stream)

    def claim(self, plan: PrewireGatePlan) -> None:
        """Commit a plan only after its audio events were actually enqueued."""
        active = self._active_plans.get(plan.ledger_plan.stream)
        if active is not plan:
            raise PrewireGateIdentityError("delivery_plan_is_stale")
        stream = plan.ledger_plan.stream
        state = self._streams.get(stream)
        if state is None or state.ended:
            raise PrewireGateIdentityError("stream_changed_before_delivery_claim")
        try:
            if plan.ledger_plan.releases:
                self._ledger.claim_enqueued(plan.ledger_plan)
            else:
                self._ledger.claim_gaps(plan.ledger_plan)
        except Exception:
            if self._active_plans.get(stream) is plan:
                self._active_plans.pop(stream, None)
            raise
        for identity in plan.ledger_plan.record_identities:
            pending = self._pending.pop(identity, None)
            if pending is not None:
                pending.emitted = True
        self._active_plans.pop(stream, None)
        self._discard_consumed_prefix(stream)

    def invalidate_stream(
        self, stream: PrewireStreamKey, *, reason: str = "stream_invalidated"
    ) -> tuple[PrewireOutputEvent, ...]:
        state = self._streams.get(stream)
        if state is not None:
            state.ended = True
        for pending in tuple(self._pending.values()):
            if pending.spec.identity.stream != stream or pending.emitted:
                continue
            for receipt in pending.receipts:
                self._scheduler.cancel(receipt)
            self._decide_stale(pending, reason)
        identities: dict[PrewireIntervalIdentity, SampleRange] = {}
        active = self._active_plans.pop(stream, None)
        if active is not None:
            for identity in active.ledger_plan.record_identities:
                pending = self._pending.get(identity)
                if pending is not None:
                    identities[identity] = pending.spec.commit_range
        for pending in self._pending.values():
            if pending.spec.identity.stream == stream and not pending.emitted:
                identities[pending.spec.identity] = pending.spec.commit_range
                pending.emitted = True
        for identity in identities:
            self._pending.pop(identity, None)
        events: tuple[PrewireOutputEvent, ...] = tuple(
            PrewireGapEvent(identity, sample_range, PrewireDecisionState.STALE, reason)
            for identity, sample_range in sorted(
                identities.items(), key=lambda item: item[1].start
            )
        )
        if state is not None:
            self._held_pcm_bytes -= len(state.pcm16)
            state.pcm16[:] = b"\x00" * len(state.pcm16)
            state.pcm16.clear()
            state.buffer_start = self._ledger.release_cursor(stream)
            self._streams.pop(stream, None)
        return events

    async def finish_stream(
        self, stream: PrewireStreamKey
    ) -> PrewireGatePlan | PrewireEndEvent:
        state = self._streams.get(stream)
        if state is None:
            raise PrewireGateIdentityError("stream_is_not_current")
        for pending in tuple(self._pending.values()):
            if pending.spec.identity.stream == stream and not pending.emitted:
                await self._join_resolution(pending)
                if self._streams.get(stream) is not state:
                    raise PrewireGateIdentityError("stream_changed_while_finishing")
        # Any non-terminal uncertain/unavailable range still blocking the
        # ledger becomes stale at stream close rather than being released.
        for pending in tuple(self._pending.values()):
            if pending.spec.identity.stream == stream and not pending.emitted:
                self._decide_stale(pending, "stream_finished_before_commit")
        plan = self._plan_stream(stream)
        if plan is not None:
            return plan
        if self._ledger.release_cursor(stream) != state.buffer_end:
            raise PrewireGateRangeError("captured_pcm_not_fully_planned")
        state.ended = True
        self._streams.pop(stream, None)
        return PrewireEndEvent(
            stream,
            self._ledger.release_cursor(stream),
            self._ledger.asr_cursor(stream),
        )

    async def close(self) -> tuple[PrewireOutputEvent, ...]:
        if self._closed:
            return ()
        self._closed = True
        events: list[PrewireOutputEvent] = []
        for stream in tuple(self._streams):
            events.extend(self.invalidate_stream(stream, reason="gate_closed"))
        await self._scheduler.close()
        return tuple(events)

    async def _resolve_pending(self, pending: _PendingInterval) -> None:
        if pending.emitted or not pending.receipts:
            return
        identity = pending.spec.identity
        scoring_identity = self._scoring_identity(identity)
        for index, (receipt, sample_range) in enumerate(
            zip(pending.receipts, pending.scoring_ranges, strict=True)
        ):
            result = await self._scheduler.await_result(receipt)
            if not self._pending_is_current(pending):
                self._decide_stale(pending, "identity_changed_while_scoring")
                return
            raw_range = self._raw_range(sample_range)
            result = result.validate_identity(scoring_identity, raw_range)
            if result.status is not ScoreResultStatus.COMPLETED or result.score is None:
                for remaining in pending.receipts[index + 1 :]:
                    self._scheduler.cancel(remaining)
                self._decide_unavailable(
                    pending,
                    f"scoring_{result.status.value}:{result.error_code or 'unknown'}",
                )
                return
            if not -1.0 <= result.score <= 1.0:
                self._decide_unavailable(pending, "score_out_of_contract")
                return
            self._ledger.record_score(
                identity,
                score=result.score,
                scoring_parameters_digest=self._parameters_digest,
            )
            pending.observations.append(
                PrewireScoreObservation(
                    scoring_range=sample_range,
                    decision_range=pending.spec.decision_range,
                    raw_similarity=result.score,
                    quality=PrewireQualitySummary(
                        speech_samples=None,
                        continuous=True,
                    ),
                    profile_generation=identity.profile_generation,
                    model_generation=identity.model_generation,
                    config_generation=identity.config_generation,
                    parameters_digest=self._parameters_digest,
                )
            )
        if not self._pending_is_current(pending):
            self._decide_stale(pending, "identity_changed_before_decision")
            return
        decision = self._policy.decide(
            pending.spec.decision_range,
            tuple(pending.observations),
            event_ended=pending.spec.event_ended,
            boundary_trusted=pending.spec.boundary_trusted,
            independent_event=pending.spec.independent_event,
            deadline_expired=True,
        )
        self._ledger.decide(
            identity,
            decision.state,
            reason=decision.reason,
            used_ended_micro_event_rule=decision.used_ended_micro_event_rule,
        )

    async def _join_resolution(self, pending: _PendingInterval) -> None:
        task = pending.resolver_task
        if task is None:
            task = asyncio.create_task(
                self._resolve_pending(pending),
                name=(
                    "prewire-resolve-"
                    f"{pending.spec.identity.stream.session_id}-"
                    f"{pending.spec.identity.segment_id}"
                ),
            )
            pending.resolver_task = task
            task.add_done_callback(self._consume_task_outcome)
        await asyncio.shield(task)

    @staticmethod
    def _consume_task_outcome(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    def _pending_is_current(self, pending: _PendingInterval) -> bool:
        identity = pending.spec.identity
        return bool(
            not pending.emitted
            and self._pending.get(identity) is pending
            and self._identity_is_current(identity)
            and self._ledger.get(identity) is not None
        )

    def _identity_is_current(self, identity: PrewireIntervalIdentity) -> bool:
        state = self._streams.get(identity.stream)
        return bool(
            state is not None
            and not state.ended
            and state.profile_generation == identity.profile_generation
            and state.model_generation == identity.model_generation
            and state.config_generation == identity.config_generation
        )

    def _decide_unavailable(self, pending: _PendingInterval, reason: str) -> None:
        record = self._ledger.get(pending.spec.identity)
        if record is None or record.decision is PrewireDecisionState.STALE:
            return
        try:
            self._ledger.decide(
                pending.spec.identity,
                PrewireDecisionState.UNAVAILABLE,
                reason=reason,
            )
        except PrewireTransitionError:
            self._decide_stale(pending, reason)

    def _decide_stale(self, pending: _PendingInterval, reason: str) -> None:
        record = self._ledger.get(pending.spec.identity)
        if record is None or record.decision in {
            PrewireDecisionState.KEEP,
            PrewireDecisionState.DROP,
            PrewireDecisionState.STALE,
        }:
            return
        try:
            self._ledger.decide(
                pending.spec.identity,
                PrewireDecisionState.STALE,
                reason=reason,
            )
        except PrewireTransitionError:
            pass

    def _plan_stream(self, stream: PrewireStreamKey) -> PrewireGatePlan | None:
        active = self._active_plans.get(stream)
        ledger_plan = self._ledger.plan_contiguous(stream)
        if active is not None and active.ledger_plan == ledger_plan:
            return active
        if not ledger_plan.record_identities:
            self._active_plans.pop(stream, None)
            return None
        releases = {release.identity: release for release in ledger_plan.releases}
        gaps = {gap.identity: gap for gap in ledger_plan.gaps}
        events: list[PrewireAudioEvent | PrewireGapEvent] = []
        for identity in ledger_plan.record_identities:
            release = releases.get(identity)
            if release is not None:
                events.append(
                    PrewireAudioEvent(
                        identity,
                        release.original_range,
                        release.asr_range,
                        self._slice_pcm(stream, release.original_range),
                    )
                )
                continue
            gap = gaps[identity]
            record = self._ledger.get(identity)
            events.append(
                PrewireGapEvent(
                    identity,
                    gap.original_range,
                    gap.decision,
                    record.decision_reason
                    if record is not None
                    else "decision_reason_missing",
                )
            )
        plan = PrewireGatePlan(ledger_plan, tuple(events))
        self._active_plans[stream] = plan
        return plan

    def _validate_ranges(
        self,
        spec: PrewireIntervalSpec,
        scoring_ranges: tuple[SampleRange, ...],
    ) -> None:
        if not scoring_ranges:
            raise PrewireGateRangeError("at least one scoring range is required")
        if scoring_ranges[0] != spec.scoring_range:
            raise PrewireGateRangeError(
                "first scoring range must match spec.scoring_range"
            )
        if spec.commit_range.start != spec.decision_range.start:
            raise PrewireGateRangeError(
                "decision and commit ranges must share their start"
            )
        if (
            not spec.event_ended
            and spec.commit_range.sample_count != self._step_samples
        ):
            raise PrewireGateRangeError("live commit range must equal step_samples")
        if spec.event_ended and spec.commit_range.sample_count > self._step_samples:
            raise PrewireGateRangeError(
                "terminal commit range cannot exceed step_samples"
            )
        if spec.permits_ended_micro_event_rule:
            return
        for sample_range in scoring_ranges:
            if not spec.identity.original_range.contains(sample_range):
                raise PrewireGateRangeError(
                    "scoring range is outside original ownership"
                )
            if not sample_range.contains(spec.decision_range):
                raise PrewireGateRangeError("scoring range must contain decision range")
            if sample_range.sample_count != self._window_samples:
                raise PrewireGateRangeError("scoring range must equal window_samples")
            if (
                not spec.event_ended
                and sample_range.end - spec.decision_range.end < self._guard_samples
            ):
                raise PrewireGateRangeError(
                    "live scoring range lacks the configured guard"
                )

    @staticmethod
    def _raw_range(sample_range: SampleRange) -> RawSampleRange:
        return RawSampleRange(sample_range.start, sample_range.end)

    @staticmethod
    def _scoring_identity(identity: PrewireIntervalIdentity) -> ScoringIdentity:
        return ScoringIdentity(
            session_id=identity.stream.session_id,
            ingress_generation=identity.stream.ingress_generation,
            profile_generation=identity.profile_generation,
            model_generation=identity.model_generation,
            config_generation=identity.config_generation,
        )

    def _slice_pcm(self, stream: PrewireStreamKey, sample_range: SampleRange) -> bytes:
        state = self._streams.get(stream)
        if state is None:
            raise PrewireGateError("pcm_not_available")
        if (
            sample_range.start < state.buffer_start
            or sample_range.end > state.buffer_end
        ):
            raise PrewireGateError("pcm_range_not_retained")
        start = (sample_range.start - state.buffer_start) * 2
        end = (sample_range.end - state.buffer_start) * 2
        return bytes(state.pcm16[start:end])

    def _discard_consumed_prefix(self, stream: PrewireStreamKey) -> None:
        state = self._streams.get(stream)
        if state is None:
            return
        discard_end = min(self._ledger.release_cursor(stream), state.buffer_end)
        if discard_end <= state.buffer_start:
            return
        byte_count = (discard_end - state.buffer_start) * 2
        state.pcm16[:byte_count] = b"\x00" * byte_count
        del state.pcm16[:byte_count]
        state.buffer_start = discard_end
        self._held_pcm_bytes -= byte_count


__all__ = [
    "PrewireAudioEvent",
    "PrewireEndEvent",
    "PrewireGapEvent",
    "PrewireGate",
    "PrewireGateCapacityError",
    "PrewireGateError",
    "PrewireGateIdentityError",
    "PrewireGatePlan",
    "PrewireGateRangeError",
    "PrewireOutputEvent",
    "PrewirePlannedRanges",
    "PrewireSubmission",
    "PrewireWindowPlanner",
]
