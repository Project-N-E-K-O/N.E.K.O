"""Pure state machine for owner-activated voice sessions."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from .buffer import BoundedAudioFrameBuffer, FrameRangeUnavailable
from .contracts import (
    ActivationDecision,
    ActivationGeneration,
    ActivationState,
    AudioFrame,
    CandidateWindow,
    OutputCommit,
    OutputLease,
    OutputOrigin,
    VerificationRequest,
    VerificationInput,
    VerificationResultKind,
    WakeWordDetection,
)


@dataclass(frozen=True, slots=True)
class VoiceActivationConfig:
    """Hard capacity and timing limits for one activation controller."""

    sample_rate: int = 16_000
    idle_timeout_seconds: float = 30.0
    buffer_seconds: float = 8.0
    buffer_bytes: int = 256_000
    pre_roll_seconds: float = 0.3
    output_queue_bytes: int = 256_000

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("VOICE_ACTIVATION_SAMPLE_RATE_INVALID")
        if self.idle_timeout_seconds <= 0:
            raise ValueError("VOICE_ACTIVATION_IDLE_TIMEOUT_INVALID")
        if self.buffer_seconds <= 0 or self.buffer_bytes <= 0:
            raise ValueError("VOICE_ACTIVATION_BUFFER_LIMIT_INVALID")
        if self.pre_roll_seconds < 0:
            raise ValueError("VOICE_ACTIVATION_PRE_ROLL_INVALID")
        if self.output_queue_bytes <= 0:
            raise ValueError("VOICE_ACTIVATION_OUTPUT_LIMIT_INVALID")


@dataclass(frozen=True, slots=True)
class _QueuedOutput:
    frame: AudioFrame
    origin: OutputOrigin


@dataclass(slots=True)
class _VerificationWork:
    request: VerificationRequest
    frames: tuple[AudioFrame, ...]
    claimed: bool = False


class VoiceActivationController:
    """Coordinate activation without importing Core or provider code."""

    def __init__(
        self,
        config: VoiceActivationConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config or VoiceActivationConfig()
        self._clock = clock
        self._buffer = BoundedAudioFrameBuffer(
            max_seconds=self._config.buffer_seconds,
            max_bytes=self._config.buffer_bytes,
        )
        self._state = ActivationState.DISABLED
        self._generation: ActivationGeneration | None = None
        self._last_sequence: int | None = None
        self._last_sample_end: int | None = None
        self._latest_voice_at: float | None = None
        self._request_counter = 0
        self._inflight: _VerificationWork | None = None
        self._queued_candidate: CandidateWindow | None = None
        self._replay_cutoff_sequence: int | None = None
        self._output: deque[_QueuedOutput] = deque()
        self._output_bytes = 0
        self._lease_counter = 0
        self._claimed: OutputLease | None = None
        self._unavailable_reason: str | None = None
        self._standby_epoch = 0

    @property
    def standby_epoch(self) -> int:
        """A new microphone/standby round cannot reuse prior keyword work."""
        return self._standby_epoch

    def verification_is_current(self, request: VerificationRequest) -> bool:
        return bool(
            self._state is ActivationState.VERIFYING
            and self._inflight is not None
            and self._inflight.request is request
            and self._accept_generation(request.generation)
        )

    @property
    def state(self) -> ActivationState:
        return self._state

    @property
    def generation(self) -> ActivationGeneration | None:
        return self._generation

    @property
    def buffered_bytes(self) -> int:
        return self._buffer.total_bytes

    @property
    def buffered_seconds(self) -> float:
        return self._buffer.total_seconds

    @property
    def pending_output_bytes(self) -> int:
        return self._output_bytes

    @property
    def verification_inflight(self) -> bool:
        return self._inflight is not None

    @property
    def verification_queued(self) -> bool:
        return self._queued_candidate is not None

    @property
    def last_voice_at(self) -> float | None:
        return self._latest_voice_at

    @property
    def idle_deadline(self) -> float | None:
        if self._latest_voice_at is None:
            return None
        return self._latest_voice_at + self._config.idle_timeout_seconds

    def monotonic_now(self) -> float:
        """Use the same clock for all local qualification decisions."""
        return self._clock()

    def start(
        self,
        generation: ActivationGeneration,
        *,
        enabled: bool,
    ) -> ActivationDecision:
        """Bind a fresh microphone authority and invalidate all old work."""

        if self._state is ActivationState.CLOSED:
            return self._decision("controller_closed")
        self._reset_runtime()
        self._standby_epoch += 1
        self._generation = generation
        self._state = ActivationState.PREPARING if enabled else ActivationState.DISABLED
        return self._decision("preparing" if enabled else "disabled")

    def mark_ready(
        self,
        generation: ActivationGeneration,
    ) -> ActivationDecision:
        if not self._accept_generation(generation):
            return self._decision("stale_ready")
        if self._state not in {
            ActivationState.PREPARING,
            ActivationState.UNAVAILABLE,
        }:
            return self._decision("ready_ignored")
        if self._state is ActivationState.UNAVAILABLE:
            # Audio captured while protection was broken must not become a
            # replay candidate merely because the backend recovered.
            self._buffer.clear()
            self._latest_voice_at = None
        self._state = ActivationState.WAITING
        self._standby_epoch += 1
        self._unavailable_reason = None
        return self._decision("waiting_for_owner")

    def mark_unavailable(
        self,
        generation: ActivationGeneration,
        reason: str,
    ) -> ActivationDecision:
        if not self._accept_generation(generation):
            return self._decision("stale_unavailable")
        if self._state in {ActivationState.DISABLED, ActivationState.CLOSED}:
            return self._decision("unavailable_ignored")
        self._fail_closed(reason or "activation_unavailable")
        return self._decision(self._unavailable_reason or "activation_unavailable")

    def disable(self) -> ActivationDecision:
        if self._state is ActivationState.CLOSED:
            return self._decision("controller_closed")
        self._reset_runtime()
        self._state = ActivationState.DISABLED
        return self._decision("disabled")

    def ingest(
        self,
        frame: AudioFrame,
        *,
        voice_activity: bool,
    ) -> ActivationDecision:
        """Accept one capture-ordered frame and decide whether it may leave."""

        if self._state is ActivationState.CLOSED:
            return self._decision("controller_closed")
        if not self._accept_generation(frame.generation):
            return self._decision("stale_frame")
        if frame.sample_rate != self._config.sample_rate:
            self._fail_closed("sample_rate_mismatch")
            return self._decision("sample_rate_mismatch")
        if self._last_sequence is not None:
            if frame.sequence <= self._last_sequence:
                return self._decision("duplicate_or_stale_frame")
            if (
                frame.sequence != self._last_sequence + 1
                or frame.sample_start != self._last_sample_end
            ):
                self._fail_closed("capture_timeline_gap")
                return self._decision("capture_timeline_gap")
        self._last_sequence = frame.sequence
        self._last_sample_end = frame.sample_end

        if self._state is ActivationState.DISABLED:
            if not self._enqueue_output(frame, OutputOrigin.BYPASS):
                return self._decision("output_backlog_overflow")
            return self._decision("bypass_frame")

        if self._state in {
            ActivationState.ACTIVE,
            ActivationState.REPLAYING,
        } and self._idle_expired(frame.captured_at):
            self._state = ActivationState.WAITING
            self._standby_epoch += 1
            self._latest_voice_at = None
            self._replay_cutoff_sequence = None
            # Existing output leases remain authorized. Their source frames
            # must not become pre-roll for a later, separate activation.
            self._buffer.clear()

        if self._state is ActivationState.ACTIVE:
            if voice_activity:
                self._latest_voice_at = frame.captured_end_at
            if not self._enqueue_output(frame, OutputOrigin.LIVE):
                return self._decision("output_backlog_overflow")
            return self._decision("active_frame")

        try:
            self._buffer.append(frame)
        except ValueError:
            self._fail_closed("capture_timeline_gap")
            return self._decision("capture_timeline_gap")

        if voice_activity:
            self._latest_voice_at = frame.captured_end_at

        if self._state is ActivationState.REPLAYING:
            cutoff = self._replay_cutoff_sequence
            if cutoff is None or frame.sequence <= cutoff:
                self._fail_closed("replay_cutoff_invalid")
                return self._decision("replay_cutoff_invalid")
            if not self._enqueue_output(frame, OutputOrigin.LIVE):
                return self._decision("output_backlog_overflow")
            return self._decision("replay_live_frame")

        return self._decision("frame_buffered")

    def request_verification(
        self,
        *,
        candidate_start_sequence: int,
        candidate_end_sequence: int,
    ) -> ActivationDecision:
        candidate = CandidateWindow(
            candidate_start_sequence,
            candidate_end_sequence,
        )
        if self._state is ActivationState.VERIFYING:
            if self._queued_candidate is not None:
                return self._decision("verification_queue_full")
            self._queued_candidate = candidate
            return self._decision("verification_queued")
        if self._state is not ActivationState.WAITING:
            return self._decision("verification_not_allowed")
        return self._start_verification(candidate)

    def apply_verification_result(
        self,
        request: VerificationRequest,
        result: VerificationResultKind,
        *,
        now: float | None = None,
    ) -> ActivationDecision:
        """Commit a verifier result only if its complete authority is current."""

        if not self.verification_is_current(request):
            return self._decision("stale_verification_result")

        if result is not VerificationResultKind.FAILED and not self._inflight.claimed:
            return self._decision("verification_input_not_claimed")

        self._inflight = None
        if result is VerificationResultKind.FAILED:
            self._fail_closed("verification_failed")
            return self._decision("verification_failed")
        if result is not VerificationResultKind.OWNER:
            self._state = ActivationState.WAITING
            return self._start_queued_verification(
                "owner_not_confirmed"
                if result is VerificationResultKind.NOT_OWNER
                else "verification_insufficient"
            )

        now = self._clock() if now is None else now
        if self._latest_voice_at is None or self._idle_expired(now):
            self._state = ActivationState.WAITING
            self._queued_candidate = None
            return self._decision("verified_candidate_stale")

        return self._commit_replay(request.replay_start_sequence, "owner_confirmed")

    def apply_wake_word(
        self,
        detection: WakeWordDetection,
        *,
        now: float | None = None,
    ) -> ActivationDecision:
        """Commit keyword evidence without inventing an OWNER score.

        Only a complete live PCM range may authorize replay. Capture time is
        reconstructed from its owning frame, never from decoder wall time.
        """
        if (
            self._state not in {ActivationState.WAITING, ActivationState.VERIFYING}
            or not self._accept_generation(detection.generation)
            or detection.epoch != self._standby_epoch
        ):
            return self._decision("stale_wake_word")
        oldest = self._buffer.oldest_sequence
        latest = self._buffer.latest_sequence
        if oldest is None or latest is None:
            return self._decision("wake_word_source_unavailable")
        frames = self._buffer.get_range(oldest, latest)
        start = next(
            (
                frame
                for frame in frames
                if frame.sample_start <= detection.sample_start < frame.sample_end
            ),
            None,
        )
        end = next(
            (
                frame
                for frame in frames
                if frame.sample_start < detection.sample_end <= frame.sample_end
            ),
            None,
        )
        if start is None or end is None:
            return self._decision("wake_word_source_unavailable")
        activity_at = (
            end.captured_at
            + (detection.sample_end - end.sample_start) / end.sample_rate
        )
        current = self._clock() if now is None else now
        if current - activity_at >= self._config.idle_timeout_seconds:
            return self._decision("wake_word_expired")
        replay_start = self._buffer.replay_start_sequence(
            start.sequence,
            pre_roll_seconds=self._config.pre_roll_seconds,
        )
        self._latest_voice_at = max(self._latest_voice_at or activity_at, activity_at)
        # Fence the scorer, but do not cancel its backend operation. A late
        # FAILED result has no authority over this activation or the next one.
        self._inflight = None
        return self._commit_replay(replay_start, "wake_word_detected")

    def _commit_replay(
        self, replay_start_sequence: int, reason: str
    ) -> ActivationDecision:
        """Both independent evidence sources use one ordered output path."""
        cutoff = self._buffer.latest_sequence
        if cutoff is None:
            self._state = ActivationState.WAITING
            return self._decision("replay_source_empty")
        try:
            replay_frames = self._buffer.get_range(
                replay_start_sequence,
                cutoff,
            )
        except FrameRangeUnavailable:
            self._state = ActivationState.WAITING
            self._queued_candidate = None
            return self._decision("replay_source_evicted")

        self._queued_candidate = None
        self._replay_cutoff_sequence = cutoff
        for frame in replay_frames:
            if not self._enqueue_output(frame, OutputOrigin.REPLAY):
                return self._decision("output_backlog_overflow")
        self._state = ActivationState.REPLAYING
        return self._decision(reason, replay_cutoff=cutoff)

    def claim_verification_input(
        self,
        request: VerificationRequest,
    ) -> VerificationInput | None:
        """Return PCM only while the exact request and source range are live."""

        work = self._inflight
        if (
            self._state is not ActivationState.VERIFYING
            or work is None
            or work.request is not request
            or work.claimed
            or not self._accept_generation(request.generation)
        ):
            return None
        try:
            live_frames = self._buffer.get_range(
                request.candidate.start_sequence,
                request.candidate.end_sequence,
            )
        except FrameRangeUnavailable:
            self._fail_closed("verification_source_evicted")
            return None
        if live_frames != work.frames:
            self._fail_closed("verification_source_changed")
            return None
        work.claimed = True
        return VerificationInput(
            request=request,
            sample_rate=self._config.sample_rate,
            sample_start=work.frames[0].sample_start,
            sample_end=work.frames[-1].sample_end,
            pcm=b"".join(frame.pcm for frame in work.frames),
        )

    def tick(self, now: float | None = None) -> ActivationDecision:
        """Apply the 30-second inactivity rule using a monotonic timestamp."""

        if self._state not in {ActivationState.ACTIVE, ActivationState.REPLAYING}:
            return self._decision("tick_ignored")
        current = self._clock() if now is None else float(now)
        if not self._idle_expired(current):
            return self._decision("still_active")
        self._state = ActivationState.WAITING
        self._standby_epoch += 1
        self._latest_voice_at = None
        self._replay_cutoff_sequence = None
        self._buffer.clear()
        return self._decision("idle_timeout")

    def claim_output(self) -> OutputLease | None:
        """Grant the sole writer one frame; a second claimant gets no work."""

        if self._claimed is not None or not self._output:
            return None
        queued = self._output[0]
        generation = self._generation
        if generation is None:
            self._fail_closed("output_generation_missing")
            return None
        self._lease_counter += 1
        self._claimed = OutputLease(
            lease_id=self._lease_counter,
            generation=generation,
            frame=queued.frame,
            origin=queued.origin,
        )
        return self._claimed

    def complete_output(
        self,
        lease: OutputLease,
        commit: OutputCommit,
        *,
        now: float | None = None,
    ) -> ActivationDecision:
        """Advance only the exact claimed frame with a known send outcome."""

        if self._claimed is not lease:
            return self._decision("stale_output_lease")
        self._claimed = None
        if commit is OutputCommit.NOT_SENT:
            return self._decision("output_not_sent")
        if commit is OutputCommit.UNKNOWN:
            self._fail_closed("output_delivery_unknown")
            return self._decision("output_delivery_unknown")
        if commit not in {
            OutputCommit.LOCAL_ACCEPTED,
            OutputCommit.TRANSPORT_WRITTEN,
            OutputCommit.PROVIDER_CONFIRMED,
        }:
            self._fail_closed("output_delivery_invalid")
            return self._decision("output_delivery_invalid")

        queued = self._output.popleft()
        self._output_bytes -= len(queued.frame.pcm)
        if self._state is ActivationState.REPLAYING and not self._output:
            self._state = ActivationState.ACTIVE
            self._buffer.clear()
            self._replay_cutoff_sequence = None
            if self._idle_expired(self._clock() if now is None else now):
                self._state = ActivationState.WAITING
                self._standby_epoch += 1
                self._latest_voice_at = None
                return self._decision("idle_timeout_after_replay")
            return self._decision("replay_handed_off")
        return self._decision("output_committed")

    def close(self) -> ActivationDecision:
        if self._state is ActivationState.CLOSED:
            return self._decision("already_closed")
        self._reset_runtime()
        self._generation = None
        self._state = ActivationState.CLOSED
        return self._decision("closed")

    def _start_verification(
        self,
        candidate: CandidateWindow,
    ) -> ActivationDecision:
        generation = self._generation
        if generation is None:
            self._fail_closed("verification_generation_missing")
            return self._decision("verification_generation_missing")
        try:
            frames = self._buffer.get_range(
                candidate.start_sequence,
                candidate.end_sequence,
            )
            replay_start = self._buffer.replay_start_sequence(
                candidate.start_sequence,
                pre_roll_seconds=self._config.pre_roll_seconds,
            )
        except FrameRangeUnavailable:
            self._state = ActivationState.WAITING
            return self._decision("candidate_unavailable")
        self._request_counter += 1
        request = VerificationRequest(
            request_id=self._request_counter,
            generation=generation,
            candidate=candidate,
            replay_start_sequence=replay_start,
        )
        self._inflight = _VerificationWork(request=request, frames=frames)
        self._state = ActivationState.VERIFYING
        return self._decision(
            "verification_started",
            verification_request=request,
        )

    def _start_queued_verification(self, fallback_reason: str) -> ActivationDecision:
        candidate = self._queued_candidate
        self._queued_candidate = None
        if candidate is None:
            return self._decision(fallback_reason)
        decision = self._start_verification(candidate)
        if decision.verification_request is None:
            return self._decision("queued_candidate_unavailable")
        return decision

    def _enqueue_output(self, frame: AudioFrame, origin: OutputOrigin) -> bool:
        if self._output_bytes + len(frame.pcm) > self._config.output_queue_bytes:
            self._fail_closed("output_backlog_overflow")
            return False
        self._output.append(_QueuedOutput(frame, origin))
        self._output_bytes += len(frame.pcm)
        return True

    def _idle_expired(self, now: float) -> bool:
        return bool(
            self._latest_voice_at is not None
            and now - self._latest_voice_at >= self._config.idle_timeout_seconds
        )

    def _accept_generation(self, generation: ActivationGeneration) -> bool:
        return self._generation is not None and generation == self._generation

    def _fail_closed(self, reason: str) -> None:
        self._buffer.clear()
        self._output.clear()
        self._output_bytes = 0
        self._claimed = None
        self._inflight = None
        self._queued_candidate = None
        self._replay_cutoff_sequence = None
        self._latest_voice_at = None
        self._unavailable_reason = reason
        self._state = ActivationState.UNAVAILABLE

    def _reset_runtime(self) -> None:
        self._buffer.clear()
        self._output.clear()
        self._output_bytes = 0
        self._claimed = None
        self._inflight = None
        self._queued_candidate = None
        self._replay_cutoff_sequence = None
        self._latest_voice_at = None
        self._last_sequence = None
        self._last_sample_end = None
        self._unavailable_reason = None

    def _decision(
        self,
        reason: str,
        *,
        verification_request: VerificationRequest | None = None,
        replay_cutoff: int | None = None,
    ) -> ActivationDecision:
        return ActivationDecision(
            state=self._state,
            reason=reason,
            verification_request=verification_request,
            output_ready=bool(self._output) and self._claimed is None,
            replay_cutoff_sequence=(
                self._replay_cutoff_sequence if replay_cutoff is None else replay_cutoff
            ),
        )
