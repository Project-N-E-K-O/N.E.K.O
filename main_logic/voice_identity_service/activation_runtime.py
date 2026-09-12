"""Runtime coordinator for Owner-activated voice sessions."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
import math
import os

from main_logic.voice_input.activation import (
    ActivationDecision,
    ActivationGeneration,
    ActivationState,
    AudioFrame,
    OutputCommit,
    VerificationRequest,
    VerificationResultKind,
    VoiceActivationController,
    WakeWordDetector,
)

from .activation_scoring import (
    ActivationScoreIdentity,
    ActivationScoreStatus,
    CampPlusActivationScorer,
)


ActivationOutput = Callable[[AudioFrame], Awaitable[OutputCommit]]
ActivationStatusCallback = Callable[[ActivationDecision], None]


logger = logging.getLogger(__name__)

_OUTPUT_RETRY_DELAY_SECONDS = 0.25


@dataclass(frozen=True, slots=True)
class VoiceSessionActivationRuntimeConfig:
    owner_similarity_threshold: float = 0.40
    first_checkpoint_seconds: float = 1.5
    second_checkpoint_seconds: float = 3.0
    candidate_silence_seconds: float = 0.5
    shutdown_timeout_seconds: float = 1.0
    wake_queue_bytes: int = 256_000

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.owner_similarity_threshold)
            or not -1.0 <= self.owner_similarity_threshold <= 1.0
        ):
            raise ValueError("owner_similarity_threshold must be within [-1, 1]")
        if self.first_checkpoint_seconds <= 0:
            raise ValueError("first_checkpoint_seconds must be positive")
        if self.second_checkpoint_seconds <= self.first_checkpoint_seconds:
            raise ValueError("second checkpoint must follow first checkpoint")
        if self.candidate_silence_seconds <= 0:
            raise ValueError("candidate_silence_seconds must be positive")
        if self.shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be positive")
        if type(self.wake_queue_bytes) is not int or self.wake_queue_bytes <= 0:
            raise ValueError("wake_queue_bytes must be a positive integer")


class VoiceSessionActivationRuntime:
    """Drive scoring and the single output writer around the pure controller."""

    def __init__(
        self,
        generation: ActivationGeneration,
        scorer: CampPlusActivationScorer,
        output: ActivationOutput,
        *,
        controller: VoiceActivationController | None = None,
        config: VoiceSessionActivationRuntimeConfig | None = None,
        status_callback: ActivationStatusCallback | None = None,
        enabled: bool = True,
        wake_detector: WakeWordDetector | None = None,
    ) -> None:
        if not callable(output):
            raise TypeError("output must be callable")
        if status_callback is not None and not callable(status_callback):
            raise TypeError("status_callback must be callable or None")
        if type(enabled) is not bool:
            raise TypeError("enabled must be bool")
        self._generation = generation
        self._scorer = scorer
        self._output = output
        self._controller = controller or VoiceActivationController()
        self._config = config or VoiceSessionActivationRuntimeConfig()
        self._status_callback = status_callback
        self._status_callback_failure_logged = False
        self._enabled = enabled
        self._lock = asyncio.Lock()
        self._verification_task: asyncio.Task[None] | None = None
        self._pending_verification_request: VerificationRequest | None = None
        self._wake_detector = wake_detector
        self._wake_ready = False
        self._wake_close_complete = False
        self._wake_task: asyncio.Task[None] | None = None
        self._wake_queue: deque[tuple[AudioFrame, int]] = deque()
        self._wake_queue_bytes = 0
        self._wake_inflight_bytes = 0
        self._wake_epoch: int | None = None
        self._output_task: asyncio.Task[None] | None = None
        self._output_retry_task: asyncio.Task[None] | None = None
        self._output_retry_requested = False
        self._output_retry_attempted = False
        self._output_pause_owner: object | None = None
        self._output_resumed_owner: object | None = None
        self._output_failure_reason: str | None = None
        self._capture_progress_provider: Callable[[], float | None] | None = None
        self._capture_blocked_since: float | None = None
        self._capture_progress_failure: ActivationDecision | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._close_completion: asyncio.Future[None] | None = None
        self._close_recovery_task: asyncio.Task[None] | None = None
        self._shutdown_tasks: tuple[asyncio.Task[None], ...] = ()
        self._scorer_close_complete = False
        self._closed = False
        self._candidate_start_sequence: int | None = None
        self._candidate_start_sample: int | None = None
        self._candidate_voice_samples = 0
        self._last_voice_end_at: float | None = None
        self._attempted_checkpoints: set[float] = set()
        self._controller.start(generation, enabled=enabled)

    @property
    def state(self) -> ActivationState:
        return self._controller.state

    @property
    def generation(self) -> ActivationGeneration:
        return self._generation

    @property
    def pending_output_bytes(self) -> int:
        return self._controller.pending_output_bytes

    @property
    def verification_inflight(self) -> bool:
        return self._controller.verification_inflight

    @property
    def last_voice_at(self) -> float | None:
        return self._controller.last_voice_at

    @property
    def idle_deadline(self) -> float | None:
        return self._controller.idle_deadline

    @property
    def output_inflight(self) -> bool:
        return self._output_task is not None and not self._output_task.done()

    @property
    def output_paused(self) -> bool:
        return self._output_pause_owner is not None

    def set_capture_progress_provider(
        self, callback: Callable[[], float | None]
    ) -> None:
        """Read the oldest input still awaiting local capture-ordered processing."""
        if not callable(callback):
            raise TypeError("capture progress provider must be callable")
        self._capture_progress_provider = callback

    async def pause_output(self, owner: object, *, deadline: float) -> bool:
        """Stop new claims, then settle the existing writer outside the lock.

        Cancellation/timeout leaves the barrier owned by the caller. It never
        cancels an uncertain network write or implicitly rolls it back.
        """
        if owner is None or not math.isfinite(deadline):
            raise ValueError("valid output owner and deadline required")
        async with self._lock:
            if self._closed or self.state in {
                ActivationState.DISABLED,
                ActivationState.CLOSED,
                ActivationState.UNAVAILABLE,
            }:
                return False
            if (
                self._output_pause_owner is not None
                and self._output_pause_owner is not owner
            ):
                return False
            self._output_pause_owner = owner
            self._output_resumed_owner = None
            task = self._output_task
        if task is not None and not task.done():
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            done, _ = await asyncio.wait({task}, timeout=remaining)
            if not done:
                return False
            self._consume_task_result(task)
        async with self._lock:
            return (
                not self._closed
                and self._output_pause_owner is owner
                and self.state
                not in {ActivationState.UNAVAILABLE, ActivationState.CLOSED}
                and not self.output_inflight
                and asyncio.get_running_loop().time() <= deadline
            )

    async def resume_output(self, owner: object) -> bool:
        async with self._lock:
            if (
                owner is None
                or self._output_pause_owner is not owner
                or self._closed
                or self.output_inflight
                or self.state in {ActivationState.UNAVAILABLE, ActivationState.CLOSED}
            ):
                return False
            self._output_pause_owner = None
            self._output_resumed_owner = owner
            self._ensure_output_task_locked()
            return True

    def complete_output_handoff(self, owner: object) -> bool:
        """Release a successful Core ticket in the same event-loop commit step."""
        if (
            owner is None
            or self._closed
            or self._output_pause_owner is not None
            or self._output_resumed_owner is not owner
        ):
            return False
        self._output_resumed_owner = None
        return True

    async def fail_output(self, owner: object, reason: str) -> None:
        async with self._lock:
            if (
                owner is None
                or self._closed
                or not (
                    self._output_pause_owner is owner
                    or (
                        self._output_pause_owner is None
                        and self._output_resumed_owner is owner
                    )
                )
            ):
                return
            # Core may discover an expired/revoked ticket immediately after
            # resume. Keep that exact owner revocable until its final commit,
            # without granting older tickets authority over the next pause.
            self._output_pause_owner = owner
            self._output_resumed_owner = None
            self._output_failure_reason = reason or "output_handoff_failed"
            self._publish(
                self._controller.mark_unavailable(
                    self._generation, self._output_failure_reason
                )
            )

    def _qualification_now_locked(self, now: float | None = None) -> float:
        self._capture_progress_failure = None
        current = self._controller.monotonic_now() if now is None else float(now)
        provider = self._capture_progress_provider
        deadline = self._controller.idle_deadline
        if provider is None or deadline is None:
            self._capture_blocked_since = None
            return current
        try:
            pending = provider()
            if pending is not None and not math.isfinite(pending):
                raise ValueError("invalid capture watermark")
        except Exception:
            self._capture_progress_failure = self._controller.mark_unavailable(
                self._generation, "capture_progress_invalid"
            )
            return current
        if pending is None or pending >= deadline or current < deadline:
            self._capture_blocked_since = None
            return current
        if self._capture_blocked_since is None:
            self._capture_blocked_since = current
        if current - self._capture_blocked_since >= 5.0:
            self._capture_progress_failure = self._controller.mark_unavailable(
                self._generation, "capture_progress_timeout"
            )
            return current
        return min(current, pending)

    async def prepare(self) -> ActivationDecision:
        if not self._enabled:
            return self._publish(self._controller.disable())
        status = await self._scorer.prepare()
        wake_error = False
        if status is ActivationScoreStatus.READY and self._wake_detector is not None:
            async with self._lock:
                if self._closed:
                    return self._publish(self._controller.close())
            try:
                await self._wake_detector.prepare()
            except asyncio.CancelledError:
                raise
            except Exception:
                wake_error = True
        async with self._lock:
            if self._closed:
                return self._publish(self._controller.close())
            if self._output_failure_reason is not None:
                return self._publish(
                    self._controller.mark_unavailable(
                        self._generation, self._output_failure_reason
                    )
                )
            if status is ActivationScoreStatus.READY:
                if wake_error:
                    self._wake_ready = False
                    return self._publish(
                        self._controller.mark_unavailable(
                            self._generation,
                            "wake_word_prepare_failed",
                        )
                    )
                self._wake_ready = self._wake_detector is not None
                return self._publish(self._controller.mark_ready(self._generation))
            return self._publish(
                self._controller.mark_unavailable(
                    self._generation,
                    status.value,
                )
            )

    async def feed(
        self,
        frame: AudioFrame,
        *,
        voice_activity: bool,
    ) -> ActivationDecision:
        request: VerificationRequest | None = None
        async with self._lock:
            if self._closed:
                return self._publish(self._controller.close())
            decision = self._controller.ingest(frame, voice_activity=voice_activity)
            if (
                decision.reason == "frame_buffered"
                and decision.state is ActivationState.PREPARING
            ):
                self._advance_candidate(
                    frame,
                    voice_activity=voice_activity,
                    allow_verification=False,
                )
            elif decision.reason == "frame_buffered" and decision.state in {
                ActivationState.WAITING,
                ActivationState.VERIFYING,
            }:
                if not self._enqueue_wake_locked(frame):
                    return self._publish(
                        self._controller.mark_unavailable(
                            self._generation,
                            "wake_word_queue_overflow",
                        )
                    )
                request = self._advance_candidate(frame, voice_activity=voice_activity)
            elif decision.state in {ActivationState.ACTIVE, ActivationState.REPLAYING}:
                self._clear_candidate()
            decision = self._publish(decision)
            self._ensure_output_task_locked()
            self._ensure_idle_task_locked()
            if request is not None:
                self._ensure_verification_task_locked(request)
            return decision

    async def tick(self, *, now: float | None = None) -> ActivationDecision:
        async with self._lock:
            current = self._qualification_now_locked(now)
            decision = self._capture_progress_failure or self._controller.tick(current)
            if decision.state is ActivationState.WAITING:
                self._clear_candidate()
            return self._publish(decision)

    async def close(self) -> None:
        completion = self._close_completion
        if completion is not None:
            await asyncio.shield(completion)
            return

        completion = asyncio.get_running_loop().create_future()
        self._close_completion = completion
        try:
            await self._close()
        except asyncio.CancelledError:
            self._close_recovery_task = asyncio.create_task(
                self._recover_close(completion),
                name="voice-session-activation-close-recovery",
            )
            raise
        except BaseException as error:
            completion.set_exception(error)
            completion.exception()
            raise
        else:
            completion.set_result(None)

    async def _recover_close(self, completion: asyncio.Future[None]) -> None:
        try:
            await self._close()
        except BaseException as error:
            completion.set_exception(error)
            completion.exception()
            try:
                logger.warning("Voice activation cleanup recovery failed")
            except Exception:
                pass
        else:
            completion.set_result(None)

    async def _close(self) -> None:
        async with self._lock:
            if not self._closed:
                self._closed = True
                self._output_pause_owner = None
                self._output_resumed_owner = None
                self._publish(self._controller.close())
                self._shutdown_tasks = tuple(
                    task
                    for task in (
                        self._verification_task,
                        self._output_task,
                        self._output_retry_task,
                        self._idle_task,
                        self._wake_task,
                    )
                    if task is not None
                )
            shutdown_tasks = self._shutdown_tasks
            self._clear_wake_queue_locked()
            self._pending_verification_request = None
        for task in shutdown_tasks:
            if not task.done():
                task.cancel()
        close_error: BaseException | None = None
        if self._wake_detector is not None and not self._wake_close_complete:
            try:
                await self._wake_detector.close()
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                close_error = error
            else:
                self._wake_close_complete = True
        if not self._scorer_close_complete:
            try:
                await self._scorer.close()
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                close_error = error
            else:
                self._scorer_close_complete = True
        await self._join_tasks(
            shutdown_tasks,
            timeout_seconds=self._config.shutdown_timeout_seconds,
        )
        if close_error is not None:
            raise close_error

    def _advance_candidate(
        self,
        frame: AudioFrame,
        *,
        voice_activity: bool,
        allow_verification: bool = True,
    ) -> VerificationRequest | None:
        if voice_activity:
            if (
                self._last_voice_end_at is not None
                and frame.captured_at - self._last_voice_end_at
                >= self._config.candidate_silence_seconds
            ):
                self._clear_candidate()
            if self._candidate_start_sequence is None:
                self._candidate_start_sequence = frame.sequence
                self._candidate_start_sample = frame.sample_start
                self._attempted_checkpoints.clear()
            self._candidate_voice_samples += frame.sample_end - frame.sample_start
            self._last_voice_end_at = frame.captured_end_at
        elif (
            self._last_voice_end_at is not None
            and frame.captured_end_at - self._last_voice_end_at
            >= self._config.candidate_silence_seconds
        ):
            self._clear_candidate()
            return None

        start_sequence = self._candidate_start_sequence
        start_sample = self._candidate_start_sample
        if start_sequence is None or start_sample is None:
            return None
        if not allow_verification:
            return None
        duration = self._candidate_voice_samples / frame.sample_rate
        checkpoint = next(
            (
                value
                for value in (
                    self._config.first_checkpoint_seconds,
                    self._config.second_checkpoint_seconds,
                )
                if duration >= value and value not in self._attempted_checkpoints
            ),
            None,
        )
        if checkpoint is None:
            return None
        self._attempted_checkpoints.add(checkpoint)
        decision = self._controller.request_verification(
            candidate_start_sequence=start_sequence,
            candidate_end_sequence=frame.sequence,
        )
        self._publish(decision)
        return decision.verification_request

    def _ensure_verification_task_locked(self, request: VerificationRequest) -> None:
        if not self._controller.verification_is_current(request):
            return
        task = self._verification_task
        if task is not None and not task.done():
            # The old backend call may still hold the scorer's lock. Keep just
            # one current request, not an unbounded collection of waiters.
            self._pending_verification_request = request
            return
        self._pending_verification_request = None
        self._verification_task = asyncio.create_task(
            self._verify(request),
            name="voice-session-activation-verify",
        )

    async def _verify(self, request: VerificationRequest) -> None:
        current_task = asyncio.current_task()
        try:
            await self._score_and_apply(request)
        finally:
            async with self._lock:
                if self._verification_task is current_task:
                    self._verification_task = None
                    pending = self._pending_verification_request
                    self._pending_verification_request = None
                    if pending is not None and not self._closed:
                        self._ensure_verification_task_locked(pending)

    async def _score_and_apply(self, request: VerificationRequest) -> None:
        async with self._lock:
            verification_input = self._controller.claim_verification_input(request)
        if verification_input is None:
            return
        score_identity = ActivationScoreIdentity(
            self._scorer.profile_generation,
            self._scorer.scorer_generation,
            request.request_id,
        )
        try:
            score = await self._scorer.score(
                score_identity,
                verification_input.pcm,
                sample_rate_hz=verification_input.sample_rate,
            )
            score_status = score.status
        except asyncio.CancelledError:
            raise
        except Exception:
            score_status = ActivationScoreStatus.FAILED
        if score_status is ActivationScoreStatus.READY:
            kind = (
                VerificationResultKind.OWNER
                if float(score.similarity) >= self._config.owner_similarity_threshold
                else VerificationResultKind.NOT_OWNER
            )
        elif score_status is ActivationScoreStatus.INVALID_AUDIO:
            kind = VerificationResultKind.INSUFFICIENT
        else:
            kind = VerificationResultKind.FAILED

        next_request: VerificationRequest | None = None
        async with self._lock:
            if self._closed or not self._controller.verification_is_current(request):
                return
            current = self._qualification_now_locked()
            decision = (
                self._capture_progress_failure
                or self._controller.apply_verification_result(
                    request, kind, now=current
                )
            )
            if decision.reason == "verification_failed":
                logger.warning(
                    "Voice activation verification failed: status=%s request=%s "
                    "pcm_bytes=%s sample_rate=%s audio_seconds=%.3f",
                    score_status.value,
                    request.request_id,
                    len(verification_input.pcm),
                    verification_input.sample_rate,
                    len(verification_input.pcm) / (2 * verification_input.sample_rate),
                )
            self._publish(decision)
            next_request = decision.verification_request
            self._ensure_output_task_locked()
            self._ensure_idle_task_locked()
            if next_request is not None:
                self._ensure_verification_task_locked(next_request)

    def _clear_wake_queue_locked(self) -> None:
        self._wake_queue.clear()
        self._wake_queue_bytes = 0

    def _enqueue_wake_locked(self, frame: AudioFrame) -> bool:
        if self._wake_detector is None or not self._wake_ready:
            return True
        epoch = self._controller.standby_epoch
        if self._wake_epoch != epoch:
            self._clear_wake_queue_locked()
            self._wake_epoch = epoch
        if (
            self._wake_queue_bytes + self._wake_inflight_bytes + len(frame.pcm)
            > self._config.wake_queue_bytes
        ):
            self._clear_wake_queue_locked()
            return False
        self._wake_queue.append((frame, epoch))
        self._wake_queue_bytes += len(frame.pcm)
        self._ensure_wake_task_locked()
        return True

    def _ensure_wake_task_locked(self) -> None:
        if self._closed or not self._wake_queue:
            return
        if self._wake_task is None or self._wake_task.done():
            self._wake_task = asyncio.create_task(
                self._run_wake_detector(), name="voice-wake-word"
            )

    async def _run_wake_detector(self) -> None:
        current_task = asyncio.current_task()
        diagnostics = os.getenv("NEKO_WAKE_WORD_DIAGNOSTICS") == "1"
        try:
            while True:
                async with self._lock:
                    if self._closed or self.state not in {
                        ActivationState.WAITING,
                        ActivationState.VERIFYING,
                    }:
                        self._clear_wake_queue_locked()
                        return
                    if not self._wake_queue:
                        return
                    frame, epoch = self._wake_queue.popleft()
                    self._wake_queue_bytes -= len(frame.pcm)
                    if epoch != self._controller.standby_epoch:
                        continue
                    self._wake_inflight_bytes = len(frame.pcm)
                failed = False
                try:
                    detection = await self._wake_detector.feed(frame, epoch)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failed = True
                    detection = None
                async with self._lock:
                    self._wake_inflight_bytes = 0
                    if (
                        self._closed
                        or epoch != self._controller.standby_epoch
                        or self.state
                        not in {ActivationState.WAITING, ActivationState.VERIFYING}
                    ):
                        if diagnostics and detection is not None:
                            self._log_wake_diagnostic("stale_request", epoch)
                        continue
                    if failed:
                        self._wake_ready = False
                        self._clear_wake_queue_locked()
                        self._publish(
                            self._controller.mark_unavailable(
                                self._generation,
                                "wake_word_runtime_failed",
                            )
                        )
                        return
                    if detection is not None:
                        # Check evidence identity before capture-progress checks,
                        # which can themselves fail the current input closed.
                        if (
                            detection.generation != self._generation
                            or detection.epoch != epoch
                        ):
                            if diagnostics:
                                self._log_wake_diagnostic("stale_evidence", epoch)
                            continue
                        self._qualification_now_locked()
                        decision = (
                            self._capture_progress_failure
                            or self._controller.apply_wake_word(
                                detection,
                                now=self._controller.monotonic_now(),
                            )
                        )
                        self._publish(decision)
                        if diagnostics:
                            self._log_wake_diagnostic(decision.reason, epoch)
                        if decision.reason == "wake_word_detected":
                            self._pending_verification_request = None
                            self._clear_candidate()
                            self._clear_wake_queue_locked()
                            self._ensure_output_task_locked()
                            self._ensure_idle_task_locked()
        finally:
            async with self._lock:
                if self._wake_task is current_task:
                    self._wake_inflight_bytes = 0
                    self._wake_task = None
                    self._ensure_wake_task_locked()

    def _log_wake_diagnostic(self, reason: str, epoch: int) -> None:
        try:
            logger.info("Wake word decision reason=%s state=%s epoch=%s queued_bytes=%s",
                        reason, self.state.value, epoch, self._wake_queue_bytes)
        except Exception:
            pass

    def _ensure_output_task_locked(self) -> None:
        if self._closed or self._output_pause_owner is not None:
            return
        task = self._output_task
        if task is not None and not task.done():
            # Preserve the existing edge-triggered retry contract: input that
            # arrives during an uncertain attempt may resume the retained
            # lease immediately once that attempt reports NOT_SENT.
            self._output_retry_requested = True
            return
        retry_task = self._output_retry_task
        if retry_task is not None:
            self._output_retry_task = None
            if retry_task is not asyncio.current_task() and not retry_task.done():
                retry_task.cancel()
        lease = self._controller.claim_output()
        if lease is None:
            return
        # claim_output is exclusive. Release this speculative lease as NOT_SENT
        # so the actual writer task can claim it after this synchronous check.
        self._controller.complete_output(lease, OutputCommit.NOT_SENT)
        self._output_task = asyncio.create_task(
            self._drain_output(),
            name="voice-session-activation-output",
        )

    async def _drain_output(self) -> None:
        while True:
            async with self._lock:
                if self._closed or self._output_pause_owner is not None:
                    self._output_task = None
                    return
                lease = self._controller.claim_output()
                if lease is None:
                    self._output_task = None
                    return
            try:
                commit = await self._output(lease.frame)
            except asyncio.CancelledError:
                async with self._lock:
                    if not self._closed:
                        self._publish(
                            self._controller.complete_output(
                                lease,
                                OutputCommit.UNKNOWN,
                            )
                        )
                raise
            except Exception:
                commit = OutputCommit.UNKNOWN
            async with self._lock:
                if self._closed:
                    return
                current = self._qualification_now_locked()
                decision = (
                    self._capture_progress_failure
                    or self._controller.complete_output(lease, commit, now=current)
                )
                self._publish(decision)
                self._ensure_idle_task_locked()
                if commit is OutputCommit.NOT_SENT:
                    self._output_task = None
                    if self._output_retry_attempted:
                        self._output_retry_requested = False
                        self._publish(
                            self._controller.mark_unavailable(
                                self._generation,
                                "output_not_sent",
                            )
                        )
                    else:
                        self._output_retry_attempted = True
                    if self.state is ActivationState.UNAVAILABLE:
                        return
                    if self._output_pause_owner is not None:
                        self._output_retry_requested = True
                    elif self._output_retry_requested:
                        self._output_retry_requested = False
                        self._ensure_output_task_locked()
                    else:
                        self._schedule_output_retry_locked()
                    return
                elif commit is OutputCommit.UNKNOWN:
                    self._output_task = None
                    return
                else:
                    self._output_retry_requested = False
                    self._output_retry_attempted = False

    def _schedule_output_retry_locked(self) -> None:
        task = self._output_retry_task
        if task is None or task.done():
            self._output_retry_task = asyncio.create_task(
                self._retry_output_once(),
                name="voice-session-activation-output-retry",
            )

    async def _retry_output_once(self) -> None:
        current = asyncio.current_task()
        try:
            await asyncio.sleep(_OUTPUT_RETRY_DELAY_SECONDS)
            async with self._lock:
                if self._output_retry_task is not current:
                    return
                self._output_retry_task = None
                if self._closed or self._output_pause_owner is not None:
                    return
                self._ensure_output_task_locked()
        finally:
            if self._output_retry_task is current:
                self._output_retry_task = None

    def _ensure_idle_task_locked(self) -> None:
        if self._closed or self._controller.state not in {
            ActivationState.REPLAYING,
            ActivationState.ACTIVE,
        }:
            return
        task = self._idle_task
        if task is None or task.done():
            self._idle_task = asyncio.create_task(
                self._run_idle_timer(),
                name="voice-session-activation-idle",
            )

    async def _run_idle_timer(self) -> None:
        current = asyncio.current_task()
        try:
            while True:
                await asyncio.sleep(1.0)
                async with self._lock:
                    if self._closed:
                        return
                    current_time = self._qualification_now_locked()
                    decision = self._capture_progress_failure or self._controller.tick(
                        current_time
                    )
                    self._publish(decision)
                    if decision.state not in {
                        ActivationState.REPLAYING,
                        ActivationState.ACTIVE,
                    }:
                        if decision.state is ActivationState.WAITING:
                            self._clear_candidate()
                        return
        finally:
            if self._idle_task is current:
                self._idle_task = None

    def _clear_candidate(self) -> None:
        self._candidate_start_sequence = None
        self._candidate_start_sample = None
        self._candidate_voice_samples = 0
        self._last_voice_end_at = None
        self._attempted_checkpoints.clear()

    def _publish(self, decision: ActivationDecision) -> ActivationDecision:
        if self._status_callback is not None:
            try:
                self._status_callback(decision)
            except Exception:
                if not self._status_callback_failure_logged:
                    self._status_callback_failure_logged = True
                    try:
                        logger.warning("Voice activation status callback failed")
                    except Exception:
                        pass
        return decision

    @classmethod
    async def _join_tasks(
        cls,
        tasks: tuple[asyncio.Task[None], ...],
        *,
        timeout_seconds: float,
    ) -> None:
        if not tasks:
            return
        done, pending = await asyncio.wait(set(tasks), timeout=timeout_seconds)
        for task in done:
            cls._consume_task_result(task)
        for task in pending:
            task.add_done_callback(cls._consume_task_result)

    @staticmethod
    def _consume_task_result(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            task.exception()
        except BaseException:
            pass


__all__ = [
    "ActivationOutput",
    "ActivationStatusCallback",
    "VoiceSessionActivationRuntime",
    "VoiceSessionActivationRuntimeConfig",
]
