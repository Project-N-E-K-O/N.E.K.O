"""Session-level endpoint detector and Smart Turn adapter."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Literal, TypeAlias

from main_logic.voice_turn.activity_evidence import RnnoiseEvidence
from main_logic.voice_turn.contracts import (
    EvaluationStatus,
    SpeechActivityEvent,
    TurnDecision,
)

from .config import SmartTurnConfig
from .coordinator import CoordinatorState, TurnCoordinator
from .detector import (
    BoundDetectorTurn,
    DetectorActivityEvent,
    DetectorCandidateKey,
    DetectorDurationQueue,
    DetectorEvent,
    DetectorIngressIdentity,
    DetectorPrewarmEvent,
    DetectorSubmitResult,
    DetectorSubmitStatus,
    DetectorTransportPrewarmEvent,
    DetectorTurnEvent,
    ProviderCandidateFence,
    SmartTurnCompletionFence,
)
from .silero_vad import SileroActivityGate, SileroVad
from .smart_turn_audio_evidence import create_smart_turn_audio_evidence_recorder
from .smart_turn_diagnostics import create_smart_turn_runtime_diagnostics
from .smart_turn_v3 import SmartTurnV3
from .throttle_policy import (
    ThrottleAction,
    ThrottleShadowMetrics,
    VoiceThrottlePolicy,
)
from ..lifecycle import VoiceIngressToken, VoiceTurnToken
from ..provider_policy import AsrProviderPolicy
from ..speaker_shadow.contracts import (
    SpeakerShadowCandidateKey,
    SpeakerShadowObserver,
)


logger = logging.getLogger(__name__)


_Identity: TypeAlias = tuple[int, int, int]
_FallbackReason: TypeAlias = Literal["semantic_incomplete", "semantic_degraded"]
_COMMIT_DRAIN_ON_CLOSE_SECONDS = 0.5


@dataclass(frozen=True, slots=True)
class _VoiceTurnFailure:
    kind: Literal["unavailable", "runtime_error"]
    stage: Literal["vad_load", "vad_feed", "smart_turn", "consumer"]


@dataclass(frozen=True, slots=True)
class _AudioItem:
    identity: _Identity
    pcm16: bytes
    duration_us: int
    detector_identity: DetectorIngressIdentity | None = None


@dataclass(frozen=True, slots=True)
class _ResetItem:
    identity: _Identity
    completed: asyncio.Future[None]
    requester: asyncio.Task[object] | None = None


@dataclass(frozen=True, slots=True)
class _CloseItem:
    completed: asyncio.Future[None]


@dataclass(frozen=True, slots=True)
class _EvaluationResultItem:
    identity: _Identity
    coordinator_generation: int
    activity_seq: int
    reason: Literal["candidate_pause", "periodic_no_vad", "strict_retry"]
    detector_identity: DetectorIngressIdentity | None = None
    evaluation_ms: int = 0
    result: object | None = None
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class _PendingCompleteConfirmation:
    identity: _Identity
    detector_identity: DetectorIngressIdentity | None
    reason: Literal["candidate_pause", "periodic_no_vad", "strict_retry"]
    probability: float | None


@dataclass(frozen=True, slots=True)
class _CompleteConfirmationItem:
    pending: _PendingCompleteConfirmation


_ControlItem: TypeAlias = (
    _ResetItem | _CloseItem | _EvaluationResultItem | _CompleteConfirmationItem
)
_QueueItem: TypeAlias = _AudioItem | _ControlItem


class _VoiceTurnAdapter:
    """Serialize Silero and Smart Turn work outside the ASR audio producer."""

    def __init__(
        self,
        *,
        vad: SileroVad,
        gate: SileroActivityGate,
        coordinator: TurnCoordinator,
        on_commit: Callable[[int, int, int], Awaitable[None]],
        on_completion_fence: Callable[
            [int, int, int, DetectorIngressIdentity], _Identity
        ]
        | None = None,
        on_activity: Callable[[SpeechActivityEvent], Awaitable[None]] | None = None,
        on_scoped_commit: Callable[
            [int, int, int, DetectorIngressIdentity], Awaitable[None]
        ]
        | None = None,
        on_scoped_activity: Callable[
            [SpeechActivityEvent, DetectorIngressIdentity], Awaitable[None]
        ]
        | None = None,
        on_accepted_audio: Callable[[bytes, int, DetectorIngressIdentity | None], None]
        | None = None,
        on_candidate_complete: Callable[[DetectorIngressIdentity | None], None]
        | None = None,
        queue_maxsize: int = 128,
        queue_capacity_ms: int = 1_000,
        continuation_timeout_seconds: float = 2.0,
        max_endpoint_wait_seconds: float = 15.0,
        candidate_complete_confirmation_seconds: float = 0.0,
        strict_complete_confirmation_seconds: float = 0.6,
        smart_turn_required: bool = False,
        smart_turn_warm_seconds: float = 60.0,
        fallback_evaluation_interval_ms: int = 500,
    ) -> None:
        if queue_maxsize <= 0:
            raise ValueError("queue_maxsize must be positive")
        if queue_capacity_ms <= 0:
            raise ValueError("queue_capacity_ms must be positive")
        if continuation_timeout_seconds <= 0:
            raise ValueError("continuation_timeout_seconds must be positive")
        if max_endpoint_wait_seconds <= 0:
            raise ValueError("max_endpoint_wait_seconds must be positive")
        if max_endpoint_wait_seconds < continuation_timeout_seconds:
            raise ValueError(
                "max_endpoint_wait_seconds must not be shorter than continuation timeout"
            )
        if (
            not math.isfinite(candidate_complete_confirmation_seconds)
            or candidate_complete_confirmation_seconds < 0
        ):
            raise ValueError(
                "candidate complete confirmation delay must be finite and non-negative"
            )
        if (
            not math.isfinite(strict_complete_confirmation_seconds)
            or strict_complete_confirmation_seconds < 0
        ):
            raise ValueError(
                "strict complete confirmation delay must be finite and non-negative"
            )
        if smart_turn_warm_seconds <= 0:
            raise ValueError("smart_turn_warm_seconds must be positive")
        if fallback_evaluation_interval_ms <= 0:
            raise ValueError("fallback_evaluation_interval_ms must be positive")
        self._vad = vad
        self._gate = gate
        self._coordinator = coordinator
        self._smart_turn_diagnostics = create_smart_turn_runtime_diagnostics()
        self._smart_turn_audio_evidence = create_smart_turn_audio_evidence_recorder()
        self._on_commit = on_commit
        self._on_completion_fence = on_completion_fence
        self._on_activity = on_activity
        self._on_scoped_commit = on_scoped_commit
        self._on_scoped_activity = on_scoped_activity
        self._on_accepted_audio = on_accepted_audio
        self._on_candidate_complete = on_candidate_complete
        self._queue: DetectorDurationQueue[_AudioItem, _ControlItem] = (
            DetectorDurationQueue(
                capacity_us=queue_capacity_ms * 1_000,
                max_frames=queue_maxsize,
            )
        )
        queue_capacity_us = queue_capacity_ms * 1_000
        confirmation_capacity_us = 0
        if smart_turn_required:
            confirmation_capacity_us = math.ceil(
                max(
                    candidate_complete_confirmation_seconds,
                    strict_complete_confirmation_seconds,
                )
                * 1_000_000
            )
        # Retained audio can already occupy one queue-capacity window while an
        # evaluation is in flight. A configured confirmation then needs its own
        # full window plus one bounded queue window for frame granularity and
        # event-loop scheduling before the confirmation control item is handled.
        self._evaluation_tail_capacity_us = (
            queue_capacity_us
            + confirmation_capacity_us
            + (queue_capacity_us if confirmation_capacity_us else 0)
        )
        self._evaluation_tail: list[_AudioItem] = []
        self._evaluation_tail_duration_us = 0
        self._confirmation_tail: list[_AudioItem] = []
        self._confirmation_tail_duration_us = 0
        self._continuation_timeout_seconds = continuation_timeout_seconds
        self._max_endpoint_wait_seconds = max_endpoint_wait_seconds
        self._candidate_complete_confirmation_seconds = (
            candidate_complete_confirmation_seconds
        )
        self._strict_complete_confirmation_seconds = (
            strict_complete_confirmation_seconds
        )
        self._smart_turn_required = smart_turn_required
        self._smart_turn_warm_seconds = smart_turn_warm_seconds
        self._fallback_evaluation_interval_ms = fallback_evaluation_interval_ms
        self._consumer_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._fallback_task: asyncio.Task[None] | None = None
        self._smart_turn_unload_task: asyncio.Task[None] | None = None
        self._evaluation_task: asyncio.Task[None] | None = None
        self._reevaluation_requested = False
        self._reevaluation_reason: (
            Literal["candidate_pause", "periodic_no_vad", "strict_retry"] | None
        ) = None
        self._pending_complete_confirmation: _PendingCompleteConfirmation | None = None
        self._strict_endpoint_deadline: float | None = None
        self._latest_detector_identity: DetectorIngressIdentity | None = None
        self._smart_turn_evaluation_ms = 0
        self._smart_turn_stale_result_count = 0
        self._smart_turn_coalesced_evaluation_count = 0
        self._callback_tasks: set[asyncio.Task[None]] = set()
        self._identity: _Identity | None = None
        self._vad_load_attempted = False
        self._vad_available = False
        self._vad_degraded = False
        self._fallback_speech_started = False
        self._fallback_audio_bytes = 0
        self._semantic_degraded = False
        self._failed = False
        self._failure_future: asyncio.Future[_VoiceTurnFailure] | None = None
        self._failure: _VoiceTurnFailure | None = None
        self._resources_closed = False
        self._closed = False
        self._commit_dispatched: set[_Identity] = set()
        self._successor_audio_fence: tuple[_Identity, int, _Identity] | None = None
        self._smart_turn_pin_count = 0

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("ASR_VOICE_TURN_CLOSED: adapter is closed")
        if self._failed:
            raise RuntimeError("ASR_VOICE_TURN_FAILED: adapter has failed")
        if self._failure_future is None:
            self._failure_future = asyncio.get_running_loop().create_future()
        if self._consumer_task is None:
            self._consumer_task = asyncio.create_task(
                self._consume(), name="asr-voice-turn"
            )

    async def wait_failure(self) -> _VoiceTurnFailure:
        failure_future = self._failure_future
        if failure_future is None:
            failure_future = asyncio.get_running_loop().create_future()
            self._failure_future = failure_future
        return await failure_future

    @property
    def failed(self) -> bool:
        return self._failed

    @property
    def failure(self) -> _VoiceTurnFailure | None:
        return self._failure

    @property
    def throttle_available(self) -> bool:
        return not self._vad_degraded

    @property
    def queued_audio_ms(self) -> int:
        return self._queue.audio_duration_us // 1_000

    @property
    def smart_turn_evaluation_ms(self) -> int:
        return self._smart_turn_evaluation_ms

    @property
    def smart_turn_stale_result_count(self) -> int:
        return self._smart_turn_stale_result_count

    @property
    def smart_turn_coalesced_evaluation_count(self) -> int:
        return self._smart_turn_coalesced_evaluation_count

    async def wait_idle(self) -> None:
        """Drain detector work for tests and shutdown; never use per PCM frame."""

        while True:
            await self._queue.join()
            evaluation_task = self._evaluation_task
            if evaluation_task is None:
                break
            await asyncio.gather(evaluation_task, return_exceptions=True)
        callbacks = tuple(self._callback_tasks)
        if callbacks:
            await asyncio.gather(*callbacks)

    def pin_smart_turn(self) -> None:
        self._ensure_running()
        self._smart_turn_pin_count += 1
        self._cancel_smart_turn_unload()

    def unpin_smart_turn(self) -> None:
        if self._smart_turn_pin_count <= 0:
            return
        self._smart_turn_pin_count -= 1
        if self._smart_turn_pin_count == 0 and self._identity is not None:
            self._schedule_smart_turn_unload(self._identity)

    async def push_audio(
        self,
        *,
        generation: int,
        buffer_epoch: int,
        utterance_id: int,
        pcm16: bytes,
        sample_rate_hz: int = 16_000,
        detector_identity: DetectorIngressIdentity | None = None,
    ) -> None:
        if len(pcm16) % 2:
            raise ValueError("ASR_INVALID_PCM: Voice Turn requires PCM16LE")
        if not pcm16:
            return
        if sample_rate_hz != 16_000:
            raise ValueError("ASR_INVALID_SAMPLE_RATE: Voice Turn requires 16 kHz")
        self._ensure_running()
        samples = len(pcm16) // 2
        duration_us = (samples * 1_000_000 + sample_rate_hz - 1) // sample_rate_hz
        pending = self._pending_complete_confirmation
        retains_for_confirmation = (
            pending is not None
            and pending.identity == (generation, buffer_epoch, utterance_id)
            and pending.detector_identity is not None
            and detector_identity is not None
            and detector_identity.detector_epoch
            == pending.detector_identity.detector_epoch
            and detector_identity.sequence_no > pending.detector_identity.sequence_no
        )
        if (
            (self._evaluation_task is not None or retains_for_confirmation)
            and self._evaluation_tail_duration_us
            + self._confirmation_tail_duration_us
            + self._queue.audio_duration_us
            + duration_us
            > self._evaluation_tail_capacity_us
        ):
            # Surface the shared duration budget at ingress so DetectorRuntime
            # can use its existing whole-candidate backpressure recovery. Once
            # an item reaches the consumer it must remain replayable if the
            # in-flight evaluation completes the predecessor turn.
            raise asyncio.QueueFull
        self._queue.put_audio_nowait(
            _AudioItem(
                (generation, buffer_epoch, utterance_id),
                pcm16,
                duration_us,
                detector_identity,
            ),
            duration_us=duration_us,
        )

    async def reset(
        self,
        *,
        generation: int,
        buffer_epoch: int,
        utterance_id: int,
    ) -> None:
        self._ensure_running()
        self._queue.discard_audio()
        completed = asyncio.get_running_loop().create_future()
        self._queue.put_control_nowait(
            _ResetItem(
                (generation, buffer_epoch, utterance_id),
                completed,
                asyncio.current_task(),
            ),
            priority=True,
        )
        consumer = self._consumer_task
        if consumer is None:
            raise RuntimeError("ASR_VOICE_TURN_CLOSED: adapter is not running")
        done, _pending = await asyncio.wait(
            {completed, consumer},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if completed in done:
            await completed
            return
        if not completed.done():
            completed.cancel()
        raise RuntimeError("ASR_VOICE_TURN_FAILED: adapter stopped during reset")

    async def close(self) -> None:
        close_task = self._close_task
        if close_task is None:
            close_task = asyncio.create_task(
                self._close_impl(),
                name="asr-voice-turn-close",
            )
            self._close_task = close_task
        await asyncio.shield(close_task)

    async def _close_impl(self) -> None:
        if self._closed:
            return
        task = self._consumer_task
        if task is None:
            self._closed = True
            await self._close_resources()
            return
        if self._failed and not task.done():
            # SmartTurn 的端点等待任务也可能在 consumer 队列外宣告失败。
            # 此时 consumer 仍在等下一项，必须显式取消，避免关闭流程永久等待。
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if task.done():
            self._closed = True
            await asyncio.gather(task, return_exceptions=True)
            await self._close_resources()
            return
        completed = asyncio.get_running_loop().create_future()
        # Preserve FIFO for PCM that push_audio() already admitted. In
        # particular, continuation audio must be allowed to cancel a pending
        # provisional COMPLETE before shutdown resolves it.
        self._queue.put_control_nowait(_CloseItem(completed))
        await completed
        await task

    def _ensure_running(self) -> None:
        if self._failed:
            raise RuntimeError("ASR_VOICE_TURN_FAILED: adapter has failed")
        task = self._consumer_task
        if self._closed or task is None or task.done():
            raise RuntimeError("ASR_VOICE_TURN_CLOSED: adapter is not running")

    async def _consume(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if isinstance(item, _AudioItem):
                    await self._process_audio(item)
                    if self._failed:
                        await self._drain_queue_on_failure_exit()
                        return
                    continue
                if isinstance(item, _ResetItem):
                    await self._process_reset(item.identity, requester=item.requester)
                    if not item.completed.done():
                        item.completed.set_result(None)
                    continue
                if isinstance(item, _EvaluationResultItem):
                    await self._process_evaluation_result(item)
                    if self._failed:
                        await self._drain_queue_on_failure_exit()
                        return
                    continue
                if isinstance(item, _CompleteConfirmationItem):
                    if self._fallback_task is not None and self._fallback_task.done():
                        self._fallback_task = None
                    await self._publish_pending_complete_confirmation(item.pending)
                    continue
                await self._process_close()
                if not item.completed.done():
                    item.completed.set_result(None)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                completed = getattr(item, "completed", None)
                if completed is not None and not completed.done():
                    completed.set_exception(exc)
                if not isinstance(item, _CloseItem):
                    self._report_failure("runtime_error", "consumer")
                    await self._drain_queue_on_failure_exit()
                    return
                raise
            finally:
                self._queue.task_done()

    async def _drain_queue_on_failure_exit(self) -> None:
        """Unblock join()/reset() waiters when a failure stops the consumer."""

        evaluation_task, self._evaluation_task = self._evaluation_task, None
        if evaluation_task is not None:
            evaluation_task.cancel()
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                if isinstance(item, _CloseItem):
                    try:
                        await self._process_close()
                    except Exception:
                        logger.exception(
                            "ASR voice turn close failed after consumer failure"
                        )
                    if not item.completed.done():
                        item.completed.set_result(None)
                    continue
                completed = getattr(item, "completed", None)
                if completed is not None and not completed.done():
                    completed.set_exception(
                        RuntimeError("ASR_VOICE_TURN_FAILED: adapter has failed")
                    )
            finally:
                self._queue.task_done()

    async def _process_audio(self, item: _AudioItem) -> None:
        if self._identity is None:
            self._identity = item.identity
        if item.identity != self._identity:
            fence = self._successor_audio_fence
            if (
                fence is None
                or item.detector_identity is None
                or item.identity != fence[0]
                or self._identity != fence[2]
                or item.detector_identity.sequence_no <= fence[1]
            ):
                return
            item = _AudioItem(
                identity=self._identity,
                pcm16=item.pcm16,
                duration_us=item.duration_us,
                detector_identity=item.detector_identity,
            )
        defers_for_evaluation = self._evaluation_task is not None
        if defers_for_evaluation:
            self._evaluation_tail.append(item)
            self._evaluation_tail_duration_us += item.duration_us
        retained_for_confirmation = self._retain_confirmation_audio(item)
        if not defers_for_evaluation and not retained_for_confirmation:
            self._attribute_accepted_audio(item)
        self._latest_detector_identity = item.detector_identity
        self._coordinator.push_audio(item.pcm16)
        if self._vad_degraded:
            await self._process_without_vad(item)
            return
        if not self._vad_load_attempted:
            self._vad_load_attempted = True
            try:
                self._vad_available = await asyncio.to_thread(self._vad.load)
            except Exception:
                if self._smart_turn_required:
                    self._vad_degraded = True
                    await self._process_without_vad(item)
                else:
                    self._report_failure("runtime_error", "vad_load")
                return
        if not self._vad_available:
            if self._smart_turn_required:
                self._vad_degraded = True
                await self._process_without_vad(item)
            else:
                self._report_failure("unavailable", "vad_load")
            return

        try:
            events = await asyncio.to_thread(self._gate.feed, item.pcm16)
        except Exception:
            if self._smart_turn_required:
                self._vad_degraded = True
                await self._process_without_vad(item)
            else:
                self._report_failure("runtime_error", "vad_feed")
            return
        for event in events:
            if self._on_activity is not None:
                await self._on_activity(event)
            if (
                self._on_scoped_activity is not None
                and item.detector_identity is not None
            ):
                await self._on_scoped_activity(event, item.detector_identity)
            await self._coordinator.on_activity_event(event)

        if any(
            event
            in (SpeechActivityEvent.SPEECH_STARTED, SpeechActivityEvent.SPEECH_RESUMED)
            for event in events
        ):
            self._cancel_smart_turn_unload()
            self._cancel_fallback()
            self._strict_endpoint_deadline = None

        if (
            SpeechActivityEvent.CANDIDATE_PAUSE not in events
            or self._coordinator.state is not CoordinatorState.PAUSE_CANDIDATE
        ):
            return

        if self._semantic_degraded:
            if self._smart_turn_required:
                self._report_failure("unavailable", "smart_turn")
                return
            self._schedule_fallback(item.identity, "semantic_degraded")
            return

        self._request_evaluation(
            item.identity,
            "candidate_pause",
            item.detector_identity,
        )

    async def _process_without_vad(self, item: _AudioItem) -> None:
        """Keep SmartTurn authoritative when Silero cannot provide candidates."""

        started_now = False
        if not self._fallback_speech_started:
            self._fallback_speech_started = True
            started_now = True
            event = SpeechActivityEvent.SPEECH_STARTED
            if self._on_activity is not None:
                await self._on_activity(event)
            if (
                self._on_scoped_activity is not None
                and item.detector_identity is not None
            ):
                await self._on_scoped_activity(event, item.detector_identity)
            await self._coordinator.on_activity_event(event)
        self._fallback_audio_bytes += len(item.pcm16)
        if started_now:
            return
        # 16 kHz PCM16 mono is 32 bytes per millisecond; accumulate bytes so
        # sub-millisecond frames still advance the periodic-evaluation clock.
        if self._fallback_audio_bytes < self._fallback_evaluation_interval_ms * 32:
            return
        self._fallback_audio_bytes = 0
        self._request_evaluation(
            item.identity,
            "periodic_no_vad",
            item.detector_identity,
        )

    def _request_evaluation(
        self,
        identity: _Identity,
        reason: Literal["candidate_pause", "periodic_no_vad", "strict_retry"],
        detector_identity: DetectorIngressIdentity | None = None,
    ) -> None:
        if self._closed or self._failed or identity != self._identity:
            return
        if self._evaluation_task is not None:
            self._smart_turn_coalesced_evaluation_count += 1
            self._reevaluation_requested = True
            self._reevaluation_reason = reason
            return
        coordinator_generation = int(getattr(self._coordinator, "generation", 0))
        activity_seq = int(getattr(self._coordinator, "activity_seq", 0))
        self._smart_turn_diagnostics.candidate(reason=reason)

        async def evaluate() -> None:
            started_at = time.perf_counter()
            result: object | None = None
            error: BaseException | None = None
            try:
                result = await self._coordinator.evaluate_buffered()
            except asyncio.CancelledError:
                return
            except BaseException as exc:
                error = exc
            self._queue.put_control_nowait(
                _EvaluationResultItem(
                    identity=identity,
                    coordinator_generation=coordinator_generation,
                    activity_seq=activity_seq,
                    reason=reason,
                    detector_identity=detector_identity,
                    evaluation_ms=int((time.perf_counter() - started_at) * 1_000),
                    result=result,
                    error=error,
                )
            )

        self._evaluation_task = asyncio.create_task(
            evaluate(), name="asr-smart-turn-evaluation"
        )

    async def _process_evaluation_result(self, item: _EvaluationResultItem) -> None:
        self._smart_turn_evaluation_ms = item.evaluation_ms
        self._evaluation_task = None
        evaluation_tail = tuple(self._evaluation_tail)
        self._evaluation_tail.clear()
        self._evaluation_tail_duration_us = 0
        reevaluate = self._reevaluation_requested
        reevaluation_reason = self._reevaluation_reason or item.reason
        self._reevaluation_requested = False
        self._reevaluation_reason = None
        identity_matches = item.identity == self._identity
        generation_matches = item.coordinator_generation == int(
            getattr(self._coordinator, "generation", item.coordinator_generation)
        )
        activity_matches = item.activity_seq == int(
            getattr(self._coordinator, "activity_seq", item.activity_seq)
        )
        result = item.result
        status = getattr(result, "status", None)
        decision = getattr(result, "decision", None)
        probability = getattr(result, "probability", None)
        if (
            self._closed
            or self._failed
            or not identity_matches
            or not generation_matches
        ):
            diagnostic_outcome = "discarded"
        elif not activity_matches:
            diagnostic_outcome = "superseded"
        elif item.error is not None:
            diagnostic_outcome = "error"
        elif status is EvaluationStatus.OK and decision is TurnDecision.COMPLETE:
            diagnostic_outcome = "complete"
        elif status is EvaluationStatus.OK and decision is TurnDecision.INCOMPLETE:
            diagnostic_outcome = "incomplete"
        elif status is EvaluationStatus.STALE:
            diagnostic_outcome = "stale"
        elif status is EvaluationStatus.UNAVAILABLE:
            diagnostic_outcome = "unavailable"
        elif status is EvaluationStatus.ERROR:
            diagnostic_outcome = "error"
        else:
            diagnostic_outcome = "unknown"
        self._smart_turn_diagnostics.evaluation(
            reason=item.reason,
            outcome=diagnostic_outcome,
            evaluation_ms=item.evaluation_ms,
            probability=probability,
            threshold=getattr(self._coordinator, "evaluation_threshold", None),
        )
        if (
            self._closed
            or self._failed
            or not identity_matches
            or not generation_matches
        ):
            if (
                reevaluate
                and identity_matches
                and not self._closed
                and not self._failed
            ):
                self._request_evaluation(
                    item.identity,
                    reevaluation_reason,
                    self._latest_detector_identity,
                )
            return
        if not activity_matches:
            self._observe_evaluation_tail(evaluation_tail)
            if reevaluate:
                self._request_evaluation(
                    item.identity,
                    reevaluation_reason,
                    self._latest_detector_identity,
                )
            return
        if item.error is not None:
            self._report_failure("runtime_error", "smart_turn")
            return
        if status is EvaluationStatus.STALE:
            self._observe_evaluation_tail(evaluation_tail)
            self._smart_turn_stale_result_count += 1
            if reevaluate:
                self._request_evaluation(
                    item.identity,
                    reevaluation_reason,
                    self._latest_detector_identity,
                )
            return
        if status is EvaluationStatus.OK and decision is TurnDecision.COMPLETE:
            confirmation_seconds = 0.0
            if self._smart_turn_required:
                if item.reason == "candidate_pause":
                    confirmation_seconds = self._candidate_complete_confirmation_seconds
                elif item.reason == "strict_retry":
                    confirmation_seconds = self._strict_complete_confirmation_seconds
            if confirmation_seconds > 0:
                if (
                    self._closed
                    or self._failed
                    or item.identity != self._identity
                    or self._evaluation_task is not None
                    or self._coordinator.state is not CoordinatorState.PAUSE_CANDIDATE
                ):
                    return
                self._schedule_complete_confirmation(
                    item.identity,
                    item.detector_identity,
                    item.reason,
                    probability=probability,
                    delay_seconds=confirmation_seconds,
                    evaluation_tail=evaluation_tail,
                )
                return
            await self._publish_complete_result(
                item.identity,
                item.detector_identity,
                item.reason,
                probability=probability,
                evaluation_tail=evaluation_tail,
            )
            return
        if status is EvaluationStatus.OK and decision is TurnDecision.INCOMPLETE:
            self._observe_evaluation_tail(evaluation_tail)
            if reevaluate:
                self._request_evaluation(
                    item.identity,
                    reevaluation_reason,
                    self._latest_detector_identity,
                )
                return
            if item.reason != "periodic_no_vad":
                if self._smart_turn_required and self._strict_endpoint_deadline is None:
                    self._strict_endpoint_deadline = (
                        asyncio.get_running_loop().time()
                        + self._max_endpoint_wait_seconds
                    )
                self._schedule_fallback(item.identity, "semantic_incomplete")
            return
        if self._smart_turn_required:
            failure_kind = (
                "unavailable"
                if status is EvaluationStatus.UNAVAILABLE
                else "runtime_error"
            )
            self._report_failure(failure_kind, "smart_turn")
            return
        self._enter_semantic_degraded()
        self._schedule_fallback(item.identity, "semantic_degraded")

    def _observe_accepted_audio(self, item: _AudioItem) -> None:
        callback = self._on_accepted_audio
        if callback is None:
            return
        try:
            callback(item.pcm16, 16_000, item.detector_identity)
        except Exception:
            return

    def _attribute_accepted_audio(self, item: _AudioItem) -> None:
        self._observe_accepted_audio(item)
        self._smart_turn_audio_evidence.accepted_audio(
            identity=item.identity,
            pcm16=item.pcm16,
        )

    def _observe_evaluation_tail(self, items: tuple[_AudioItem, ...]) -> None:
        for item in items:
            self._attribute_accepted_audio(item)

    def _retain_confirmation_audio(self, item: _AudioItem) -> bool:
        pending = self._pending_complete_confirmation
        candidate = pending.detector_identity if pending is not None else None
        incoming = item.detector_identity
        if (
            pending is None
            or candidate is None
            or incoming is None
            or item.identity != pending.identity
            or incoming.detector_epoch != candidate.detector_epoch
            or incoming.sequence_no <= candidate.sequence_no
        ):
            return False
        self._confirmation_tail.append(item)
        self._confirmation_tail_duration_us += item.duration_us
        return True

    def _clear_confirmation_audio(self) -> tuple[_AudioItem, ...]:
        items = tuple(self._confirmation_tail)
        self._confirmation_tail.clear()
        self._confirmation_tail_duration_us = 0
        return items

    def _complete_observed_candidate(
        self,
        detector_identity: DetectorIngressIdentity | None,
    ) -> None:
        callback = self._on_candidate_complete
        if callback is None:
            return
        try:
            callback(detector_identity)
        except Exception:
            return

    async def _process_reset(
        self,
        identity: _Identity,
        *,
        requester: asyncio.Task[object] | None = None,
    ) -> None:
        if self._identity is not None and identity < self._identity:
            # A newer reset already jumped the control queue and owns the
            # current turn; a stale one must not roll the identity back or
            # cancel the newer turn's work.
            return
        self._cancel_fallback(attribute_confirmation_tail=False)
        self._cancel_smart_turn_unload()
        # Invalidate callbacks before awaiting their cancellation. A callback
        # may suppress CancelledError, but it must still observe the new turn.
        self._identity = identity
        evaluation_task, self._evaluation_task = self._evaluation_task, None
        if evaluation_task is not None:
            evaluation_task.cancel()
        callback_tasks = tuple(
            task for task in self._callback_tasks if task is not requester
        )
        for task in callback_tasks:
            task.cancel()
        if callback_tasks:
            await asyncio.gather(*callback_tasks, return_exceptions=True)
        self._reevaluation_requested = False
        self._reevaluation_reason = None
        self._strict_endpoint_deadline = None
        self._latest_detector_identity = None
        self._evaluation_tail.clear()
        self._evaluation_tail_duration_us = 0
        self._clear_confirmation_audio()
        self._successor_audio_fence = None
        self._smart_turn_audio_evidence.discard()
        await self._coordinator.reset()
        await asyncio.to_thread(self._gate.reset)
        self._commit_dispatched.clear()
        self._fallback_speech_started = False
        self._fallback_audio_bytes = 0
        if self._smart_turn_pin_count == 0:
            self._schedule_smart_turn_unload(identity)

    async def _process_close(self) -> None:
        await self._publish_pending_complete_before_close()
        self._closed = True
        self._cancel_fallback()
        await self._close_resources()

    async def _close_resources(self) -> None:
        if self._resources_closed:
            return
        self._resources_closed = True
        self._cancel_smart_turn_unload()
        evaluation_task, self._evaluation_task = self._evaluation_task, None
        if evaluation_task is not None:
            evaluation_task.cancel()
        await self._coordinator.close()
        await asyncio.to_thread(self._vad.close)
        for task in tuple(self._callback_tasks):
            task.cancel()
        if self._callback_tasks:
            await asyncio.gather(*self._callback_tasks, return_exceptions=True)
        if evaluation_task is not None:
            await asyncio.gather(evaluation_task, return_exceptions=True)
        await self._smart_turn_diagnostics.close()
        await self._smart_turn_audio_evidence.close()

    def _schedule_fallback(
        self,
        identity: _Identity,
        reason: _FallbackReason,
    ) -> None:
        self._cancel_fallback()

        async def fallback() -> None:
            if reason == "semantic_incomplete" and self._smart_turn_required:
                await self._strict_incomplete_wait(identity)
                return
            await asyncio.sleep(self._continuation_timeout_seconds)
            state_matches = (
                self._coordinator.state is CoordinatorState.WAIT_CONTINUATION
                if reason == "semantic_incomplete"
                else self._semantic_degraded
                and self._coordinator.state is CoordinatorState.PAUSE_CANDIDATE
            )
            if (
                not self._closed
                and not self._failed
                and identity == self._identity
                and state_matches
            ):
                self._dispatch_commit(identity, self._latest_detector_identity)

        self._fallback_task = asyncio.create_task(
            fallback(), name="asr-voice-turn-fallback"
        )

    def _schedule_complete_confirmation(
        self,
        identity: _Identity,
        detector_identity: DetectorIngressIdentity | None,
        reason: Literal["candidate_pause", "periodic_no_vad", "strict_retry"],
        *,
        probability: float | None,
        delay_seconds: float,
        evaluation_tail: tuple[_AudioItem, ...] = (),
    ) -> None:
        self._cancel_fallback()
        pending = _PendingCompleteConfirmation(
            identity=identity,
            detector_identity=detector_identity,
            reason=reason,
            probability=probability,
        )
        self._pending_complete_confirmation = pending
        for item in evaluation_tail:
            if pending.detector_identity is None and item.detector_identity is None:
                # The internal ASR path has no completion fence that can move
                # these samples to a successor identity. Attribute them now so
                # the deferred evaluation interval cannot leave an evidence gap.
                self._attribute_accepted_audio(item)
            else:
                self._retain_confirmation_audio(item)

        async def confirm_complete() -> None:
            await asyncio.sleep(delay_seconds)
            self._queue.put_control_nowait(_CompleteConfirmationItem(pending))

        self._fallback_task = asyncio.create_task(
            confirm_complete(), name=f"asr-voice-turn-{reason}-complete-confirm"
        )

    async def _publish_pending_complete_before_close(self) -> None:
        pending = self._pending_complete_confirmation
        if pending is None:
            return
        task = self._fallback_task
        self._fallback_task = None
        if task is not None:
            task.cancel()
        await self._publish_pending_complete_confirmation(
            pending,
            require_pause_candidate=False,
            wait_for_commit=True,
        )

    async def _publish_pending_complete_confirmation(
        self,
        pending: _PendingCompleteConfirmation,
        *,
        require_pause_candidate: bool = True,
        wait_for_commit: bool = False,
    ) -> bool:
        if self._pending_complete_confirmation is not pending:
            return False
        current_task = asyncio.current_task()
        if current_task is self._fallback_task:
            self._fallback_task = None
        self._pending_complete_confirmation = None
        confirmation_tail = self._clear_confirmation_audio()
        if (
            self._closed
            or self._failed
            or pending.identity != self._identity
            or self._evaluation_task is not None
            or (
                require_pause_candidate
                and self._coordinator.state is not CoordinatorState.PAUSE_CANDIDATE
            )
        ):
            if (
                not self._closed
                and not self._failed
                and pending.identity == self._identity
                and self._evaluation_task is None
            ):
                self._observe_evaluation_tail(confirmation_tail)
            return False
        await self._publish_complete_result(
            pending.identity,
            pending.detector_identity,
            pending.reason,
            probability=pending.probability,
            evaluation_tail=confirmation_tail,
            wait_for_commit=wait_for_commit,
        )
        return True

    async def _strict_incomplete_wait(self, identity: _Identity) -> None:
        """Schedule one strict retry through the single SmartTurn lane."""

        await asyncio.sleep(self._continuation_timeout_seconds)
        if (
            self._closed
            or self._failed
            or identity != self._identity
            or self._coordinator.state is not CoordinatorState.WAIT_CONTINUATION
        ):
            return
        deadline = self._strict_endpoint_deadline
        if deadline is None or asyncio.get_running_loop().time() >= deadline:
            self._report_failure("unavailable", "smart_turn")
            return
        self._request_evaluation(
            identity,
            "strict_retry",
            self._latest_detector_identity,
        )

    async def _publish_complete_result(
        self,
        identity: _Identity,
        detector_identity: DetectorIngressIdentity | None,
        reason: Literal["candidate_pause", "periodic_no_vad", "strict_retry"],
        *,
        probability: float | None,
        evaluation_tail: tuple[_AudioItem, ...],
        wait_for_commit: bool = False,
    ) -> None:
        self._strict_endpoint_deadline = None
        self._smart_turn_diagnostics.complete(reason=reason)
        self._complete_observed_candidate(detector_identity)
        active_identity = identity
        if self._on_completion_fence is not None and detector_identity is not None:
            active_identity = self._on_completion_fence(
                *identity,
                detector_identity,
            )
        if active_identity == identity:
            self._observe_evaluation_tail(evaluation_tail)
        self._smart_turn_audio_evidence.complete(
            identity=identity,
            reason=reason,
            probability=probability,
            threshold=getattr(self._coordinator, "evaluation_threshold", None),
        )
        if active_identity != identity:
            await self._process_reset(
                active_identity,
                requester=asyncio.current_task(),
            )
            self._successor_audio_fence = (
                identity,
                detector_identity.sequence_no,
                active_identity,
            )
        callback_tasks_before = tuple(self._callback_tasks)
        completion_published = self._dispatch_commit(
            identity,
            detector_identity,
            active_identity=active_identity,
        )
        if wait_for_commit and completion_published is not None:
            commit_tasks = tuple(
                task
                for task in self._callback_tasks
                if task not in callback_tasks_before
            )
            if commit_tasks:
                await asyncio.wait(
                    commit_tasks,
                    timeout=_COMMIT_DRAIN_ON_CLOSE_SECONDS,
                )
        elif completion_published is not None:
            _ = await completion_published
        if active_identity == identity:
            return
        for tail_item in evaluation_tail:
            await self._process_audio(
                _AudioItem(
                    identity=active_identity,
                    pcm16=tail_item.pcm16,
                    duration_us=tail_item.duration_us,
                    detector_identity=tail_item.detector_identity,
                )
            )

    def _cancel_fallback(self, *, attribute_confirmation_tail: bool = True) -> None:
        self._pending_complete_confirmation = None
        confirmation_tail = self._clear_confirmation_audio()
        if attribute_confirmation_tail:
            self._observe_evaluation_tail(confirmation_tail)
        task = self._fallback_task
        self._fallback_task = None
        if task is not None:
            task.cancel()

    def _schedule_smart_turn_unload(self, identity: _Identity) -> None:
        if self._smart_turn_pin_count > 0:
            return

        async def unload_after_warm_ttl() -> None:
            try:
                await asyncio.sleep(self._smart_turn_warm_seconds)
                if self._closed or self._failed or identity != self._identity:
                    return
                unload = getattr(self._coordinator, "unload_predictor", None)
                if callable(unload):
                    await unload()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("ASR SmartTurn idle unload failed")

        self._smart_turn_unload_task = asyncio.create_task(
            unload_after_warm_ttl(),
            name="asr-smart-turn-idle-unload",
        )

    def _cancel_smart_turn_unload(self) -> None:
        task = self._smart_turn_unload_task
        self._smart_turn_unload_task = None
        if task is not None:
            task.cancel()

    def _enter_semantic_degraded(self) -> None:
        if self._semantic_degraded:
            return
        self._semantic_degraded = True
        logger.warning(
            "ASR Smart Turn unavailable; using Silero-only endpointing for this session"
        )

    def _report_failure(
        self,
        kind: Literal["unavailable", "runtime_error"],
        stage: Literal["vad_load", "vad_feed", "smart_turn", "consumer"],
    ) -> None:
        if self._failed or self._closed:
            return
        self._failed = True
        self._failure = _VoiceTurnFailure(kind, stage)
        self._smart_turn_diagnostics.failure(kind=kind, stage=stage)
        self._cancel_fallback(attribute_confirmation_tail=False)
        self._cancel_smart_turn_unload()
        current_task = asyncio.current_task()
        for task in tuple(self._callback_tasks):
            if task is not current_task:
                task.cancel()
        failure_future = self._failure_future
        if failure_future is None:
            failure_future = asyncio.get_running_loop().create_future()
            self._failure_future = failure_future
        if not failure_future.done():
            failure_future.set_result(self._failure)

    def _dispatch_commit(
        self,
        identity: _Identity,
        detector_identity: DetectorIngressIdentity | None = None,
        *,
        active_identity: _Identity | None = None,
    ) -> asyncio.Future[None] | None:
        expected_identity = active_identity or identity
        if self._closed or expected_identity != self._identity:
            return None
        if identity in self._commit_dispatched:
            return None
        self._commit_dispatched.add(identity)
        completion_published = asyncio.get_running_loop().create_future()

        async def commit() -> None:
            try:
                if self._closed or self._failed or expected_identity != self._identity:
                    return
                if self._on_scoped_commit is not None and detector_identity is not None:
                    await self._on_scoped_commit(*identity, detector_identity)
                    if (
                        self._closed
                        or self._failed
                        or expected_identity != self._identity
                    ):
                        return
                if not completion_published.done():
                    completion_published.set_result(None)
                if self._closed or self._failed or expected_identity != self._identity:
                    return
                await self._on_commit(*identity)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._report_failure("runtime_error", "consumer")
            finally:
                if not completion_published.done():
                    completion_published.set_result(None)

        task = asyncio.create_task(commit(), name="asr-voice-turn-commit")
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_tasks.discard)
        return completion_published


def _create_voice_turn_adapter(
    on_commit: Callable[[int, int, int], Awaitable[None]],
    *,
    on_activity: Callable[[SpeechActivityEvent], Awaitable[None]] | None = None,
    smart_turn_required: bool = False,
) -> _VoiceTurnAdapter:
    config = SmartTurnConfig(enabled=True)
    vad = SileroVad(
        enabled=True,
        inference_error_limit=config.inference_error_limit,
    )
    gate = SileroActivityGate(vad, config)
    predictor = SmartTurnV3(
        enabled=True,
        inference_error_limit=config.inference_error_limit,
    )
    coordinator = TurnCoordinator(predictor, config)
    return _VoiceTurnAdapter(
        vad=vad,
        gate=gate,
        coordinator=coordinator,
        on_commit=on_commit,
        on_activity=on_activity,
        candidate_complete_confirmation_seconds=(
            config.candidate_complete_confirmation_seconds
        ),
        smart_turn_required=smart_turn_required,
    )


@dataclass(frozen=True, slots=True)
class DetectorFeedResult:
    events: tuple[SpeechActivityEvent, ...]
    throttle_available: bool
    endpointing_available: bool = True
    throttle_action: ThrottleAction | None = None


class SmartTurnReadiness(Enum):
    UNLOADED = "unloaded"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"
    UNLOADING = "unloading"


@dataclass(slots=True)
class SmartTurnLease:
    token: VoiceTurnToken
    _runtime: "DetectorRuntime"
    _lease_id: int
    _released: bool = False

    async def release(self) -> None:
        if self._released:
            return
        await self._runtime.release_endpointing(self.token, self._lease_id)
        self._released = True


class DetectorRuntime:
    """Serialize Silero loading and inference without owning an ASR session."""

    def __init__(
        self,
        *,
        vad: SileroVad | None = None,
        gate: SileroActivityGate | None = None,
        rnnoise_onset_probability: float = 0.35,
        resource_optimization_enabled: bool = True,
        provider_policy: AsrProviderPolicy | None = None,
        coordinator: TurnCoordinator | None = None,
        throttle_policy: VoiceThrottlePolicy | None = None,
        speaker_shadow: SpeakerShadowObserver | None = None,
        on_turn_complete: Callable[[], Awaitable[None]] | None = None,
        on_endpointing_failure: Callable[[], Awaitable[None]] | None = None,
        on_event: Callable[[DetectorEvent], Awaitable[None]] | None = None,
    ) -> None:
        if not 0.0 <= rnnoise_onset_probability <= 1.0:
            raise ValueError("RNNoise onset probability must be within [0, 1]")
        if vad is None:
            config = SmartTurnConfig(enabled=True)
            vad = SileroVad(
                enabled=True,
                inference_error_limit=config.inference_error_limit,
            )
            gate = SileroActivityGate(vad, config)
        if gate is None:
            raise ValueError("DetectorRuntime gate is required with a custom VAD")
        self._vad = vad
        self._gate = gate
        self._lock = asyncio.Lock()
        self._load_attempted = False
        self._available = True
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._rnnoise_onset_probability = rnnoise_onset_probability
        self._resource_optimization_enabled = bool(resource_optimization_enabled)
        self._throttle_policy = throttle_policy or VoiceThrottlePolicy(
            resource_optimization_enabled=self._resource_optimization_enabled,
            bootstrap_onset=rnnoise_onset_probability,
        )
        self._policy_event_candidate: DetectorCandidateKey | None = None
        self._speech_active = False
        self._events: list[SpeechActivityEvent] = []
        self._semantic_adapter: _VoiceTurnAdapter | None = None
        self._semantic_coordinator: TurnCoordinator | None = None
        self._semantic_started = False
        self._semantic_generation = 0
        self._deferred_completion_identity_advanced = False
        self._semantic_turn_id = 1
        self._on_endpointing_failure = on_endpointing_failure
        self._on_turn_complete = on_turn_complete
        self._on_event = on_event
        self._defer_turn_complete = False
        self._deferred_turn_complete = False
        self._failure_watch_task: asyncio.Task[None] | None = None
        self._smart_turn_readiness = SmartTurnReadiness.UNLOADED
        self._smart_turn_token: VoiceTurnToken | None = None
        self._smart_turn_generation_sequence = 0
        self._smart_turn_generation: int | None = None
        self._smart_turn_lease_sequence = 0
        self._smart_turn_lease_ids: set[int] = set()
        self._prepare_task: asyncio.Task[bool] | None = None
        self._prepare_token: VoiceTurnToken | None = None
        self._prepare_epoch: int | None = None
        self._prepare_waiters: dict[asyncio.Task[bool], int] = {}
        self._prepare_generations: dict[asyncio.Task[bool], int] = {}
        self._overflow_reset_task: asyncio.Task[None] | None = None
        self._detector_epoch = 0
        self._sequence_no = 0
        self._ingress_token: VoiceIngressToken | None = None
        self._candidate_open = False
        self._candidate_generation = 0
        self._bound_turns: dict[DetectorCandidateKey, BoundDetectorTurn] = {}
        self._deferred_completions: dict[
            DetectorCandidateKey, DetectorIngressIdentity
        ] = {}
        self._completion_fences: dict[
            tuple[int, int, int], SmartTurnCompletionFence
        ] = {}
        self._provider_candidate_fence: ProviderCandidateFence | None = None
        self._provider_discarded_through_sequence_no: int | None = None
        self._speaker_shadow = speaker_shadow
        self._speaker_shadow_generation = 0
        self._speaker_shadow_candidate: SpeakerShadowCandidateKey | None = None
        if (
            provider_policy is not None
            and provider_policy.endpoint_authority == "smart_turn"
        ):
            if on_turn_complete is None and on_event is None:
                raise ValueError(
                    "SmartTurn DetectorRuntime requires a completion consumer"
                )
            config = SmartTurnConfig(enabled=True)
            semantic_coordinator = coordinator or TurnCoordinator(
                SmartTurnV3(
                    enabled=True,
                    inference_error_limit=config.inference_error_limit,
                ),
                config,
            )
            candidate_complete_confirmation_seconds = (
                config.candidate_complete_confirmation_seconds
                if isinstance(semantic_coordinator, TurnCoordinator)
                else 0.0
            )
            self._semantic_coordinator = semantic_coordinator

            def completion_fence(
                generation: int,
                buffer_epoch: int,
                turn_id: int,
                identity: DetectorIngressIdentity,
            ) -> _Identity:
                successor_present = self._sequence_no > identity.sequence_no
                fence = SmartTurnCompletionFence(
                    detector_epoch=identity.detector_epoch,
                    candidate_generation=self._candidate_generation,
                    through_sequence_no=identity.sequence_no,
                    semantic_generation=generation,
                    semantic_turn_id=turn_id,
                    successor_candidate_generation=self._candidate_generation + 1,
                    successor_present=successor_present,
                )
                self._completion_fences[(generation, buffer_epoch, turn_id)] = fence
                self._semantic_generation += 1
                self._semantic_turn_id += 1
                self._candidate_generation = fence.successor_candidate_generation
                self._policy_event_candidate = None
                if not successor_present:
                    self._candidate_open = False
                    self._throttle_policy.reset_candidate_activity()
                return (
                    self._semantic_generation,
                    buffer_epoch,
                    self._semantic_turn_id,
                )

            async def commit(generation: int, buffer_epoch: int, turn_id: int) -> None:
                fence = self._completion_fences.pop(
                    (generation, buffer_epoch, turn_id),
                    None,
                )
                if fence is None:
                    self._candidate_open = False
                    self._policy_event_candidate = None
                    self._throttle_policy.reset_candidate_activity()
                if self._defer_turn_complete:
                    self._deferred_turn_complete = True
                    self._deferred_completion_identity_advanced = fence is not None
                    return
                # 当前轮 seal 后立即把检测身份推进到下一轮。旧 provider final
                # 到达前，新语音只做本地语义判断，完成信号延迟发布。
                self._defer_turn_complete = True
                if fence is None:
                    self._semantic_generation += 1
                    self._semantic_turn_id += 1
                    self._candidate_generation += 1
                    adapter = self._semantic_adapter
                    if adapter is not None:
                        await adapter.reset(
                            generation=self._semantic_generation,
                            buffer_epoch=0,
                            utterance_id=self._semantic_turn_id,
                        )
                if on_turn_complete is not None:
                    await on_turn_complete()

            async def activity(event: SpeechActivityEvent) -> None:
                self._throttle_policy.observe_silero(event)
                self._events.append(event)

            async def scoped_activity(
                event: SpeechActivityEvent,
                identity: DetectorIngressIdentity,
            ) -> None:
                if (
                    self._on_event is None
                    or identity.detector_epoch != self._detector_epoch
                ):
                    return
                await self._on_event(
                    DetectorActivityEvent(
                        ingress=identity,
                        candidate=DetectorCandidateKey(
                            identity.detector_epoch,
                            self._candidate_generation,
                        ),
                        activity=event,
                    )
                )

            async def scoped_commit(
                generation: int,
                buffer_epoch: int,
                turn_id: int,
                identity: DetectorIngressIdentity,
            ) -> None:
                if (
                    self._on_event is None
                    or identity.detector_epoch != self._detector_epoch
                ):
                    return
                fence = self._completion_fences.get((generation, buffer_epoch, turn_id))
                candidate = (
                    fence.candidate
                    if fence is not None
                    else DetectorCandidateKey(
                        identity.detector_epoch,
                        self._candidate_generation,
                    )
                )
                if not await self._publish_bound_completion(candidate, identity):
                    self._deferred_completions[candidate] = identity

            self._semantic_adapter = _VoiceTurnAdapter(
                vad=self._vad,
                gate=self._gate,
                coordinator=semantic_coordinator,
                on_commit=commit,
                on_completion_fence=completion_fence,
                on_activity=activity,
                on_scoped_commit=scoped_commit,
                on_scoped_activity=scoped_activity,
                on_accepted_audio=(
                    self._observe_smart_turn_speaker_shadow
                    if self._speaker_shadow is not None
                    else None
                ),
                on_candidate_complete=(
                    self._finish_smart_turn_speaker_shadow
                    if self._speaker_shadow is not None
                    else None
                ),
                candidate_complete_confirmation_seconds=(
                    candidate_complete_confirmation_seconds
                ),
                smart_turn_required=True,
            )

    @property
    def smart_turn_readiness(self) -> SmartTurnReadiness:
        return self._smart_turn_readiness

    @property
    def detector_epoch(self) -> int:
        return self._detector_epoch

    @property
    def candidate_open(self) -> bool:
        return self._candidate_open

    @property
    def throttle_shadow_metrics(self) -> ThrottleShadowMetrics:
        return self._throttle_policy.shadow_metrics

    @property
    def queued_audio_ms(self) -> int:
        adapter = self._semantic_adapter
        return adapter.queued_audio_ms if adapter is not None else 0

    @property
    def smart_turn_evaluation_ms(self) -> int:
        adapter = self._semantic_adapter
        return adapter.smart_turn_evaluation_ms if adapter is not None else 0

    @property
    def smart_turn_stale_result_count(self) -> int:
        adapter = self._semantic_adapter
        return adapter.smart_turn_stale_result_count if adapter is not None else 0

    @property
    def smart_turn_coalesced_evaluation_count(self) -> int:
        adapter = self._semantic_adapter
        return (
            adapter.smart_turn_coalesced_evaluation_count if adapter is not None else 0
        )

    async def bind_candidate(
        self,
        candidate: DetectorCandidateKey,
        turn_token: VoiceTurnToken,
    ) -> BoundDetectorTurn | None:
        if (
            self._closed
            or candidate.detector_epoch != self._detector_epoch
            or (
                candidate.candidate_generation != self._candidate_generation
                and candidate not in self._deferred_completions
            )
        ):
            return None
        existing = self._bound_turns.get(candidate)
        if existing is not None:
            return existing if existing.turn_token == turn_token else None
        bound = BoundDetectorTurn(candidate, turn_token)
        self._bound_turns[candidate] = bound
        deferred = self._deferred_completions.pop(candidate, None)
        if deferred is not None:
            await self._publish_bound_completion(candidate, deferred)
        return bound

    async def _publish_bound_completion(
        self,
        candidate: DetectorCandidateKey,
        identity: DetectorIngressIdentity,
    ) -> bool:
        bound_turn = self._bound_turns.pop(candidate, None)
        if bound_turn is None:
            return False
        if self._on_event is not None:
            await self._on_event(
                DetectorTurnEvent(
                    ingress=identity,
                    bound_turn=bound_turn,
                    kind="complete",
                )
            )
        return True

    async def force_speech_started(
        self,
        identity: DetectorIngressIdentity,
    ) -> bool:
        """Open continuous-upload mode without changing SmartTurn authority."""

        if (
            self._on_event is None
            or self._closed
            or identity.detector_epoch != self._detector_epoch
        ):
            return False
        await self._on_event(
            DetectorActivityEvent(
                ingress=identity,
                candidate=DetectorCandidateKey(
                    identity.detector_epoch,
                    self._candidate_generation,
                ),
                activity=SpeechActivityEvent.SPEECH_STARTED,
            )
        )
        return True

    async def prepare_endpointing(
        self,
        token: VoiceTurnToken,
    ) -> SmartTurnLease | None:
        """Load and pin SmartTurn before any provider wire audio is allowed."""

        adapter = self._semantic_adapter
        coordinator = self._semantic_coordinator
        if self._closed or adapter is None or coordinator is None:
            self._smart_turn_readiness = SmartTurnReadiness.FAILED
            return None
        await self._ensure_semantic_started(adapter)
        prepare_task: asyncio.Task[bool] | None = None
        while True:
            stale_task: asyncio.Task[bool] | None = None
            async with self._lock:
                if self._closed or adapter.failed:
                    self._smart_turn_readiness = SmartTurnReadiness.FAILED
                    return None
                if (
                    self._smart_turn_token == token
                    and self._smart_turn_readiness is SmartTurnReadiness.READY
                ):
                    return self._acquire_endpointing_lease_locked(token)
                if self._smart_turn_token is not None:
                    return None
                if (
                    self._smart_turn_readiness is SmartTurnReadiness.READY
                    and self._prepare_task is None
                ):
                    adapter.pin_smart_turn()
                    self._smart_turn_generation_sequence += 1
                    self._smart_turn_generation = (
                        self._smart_turn_generation_sequence
                    )
                    self._smart_turn_token = token
                    return self._acquire_endpointing_lease_locked(token)
                if self._prepare_task is not None:
                    if self._prepare_token == token:
                        prepare_task = self._prepare_task
                        self._prepare_waiters[prepare_task] = (
                            self._prepare_waiters.get(prepare_task, 0) + 1
                        )
                    elif self._prepare_token is not None:
                        return None
                    else:
                        # _prepare_task alive with _prepare_token cleared means
                        # a reset/release/invalidate orphaned an in-flight
                        # model load.  Wait for its cleanup outside the lock
                        # and retry instead of failing the new turn; the task
                        # stays registered so close() can still cancel it.
                        stale_task = self._prepare_task
                else:
                    self._smart_turn_readiness = SmartTurnReadiness.LOADING
                    self._prepare_token = token
                    self._prepare_epoch = self._detector_epoch
                    adapter.pin_smart_turn()
                    self._smart_turn_generation_sequence += 1
                    prepare_generation = self._smart_turn_generation_sequence
                    prepare_task = asyncio.create_task(
                        self._prepare_endpointing_task(
                            adapter,
                            coordinator,
                            token,
                            self._detector_epoch,
                            prepare_generation,
                        ),
                        name="detector-runtime-smart-turn-prepare",
                    )
                    self._prepare_task = prepare_task
                    self._prepare_waiters[prepare_task] = 1
                    self._prepare_generations[prepare_task] = prepare_generation
            if stale_task is None:
                break
            await asyncio.gather(stale_task, return_exceptions=True)
        if prepare_task is None:
            return None
        prepared = False
        try:
            prepared = await asyncio.shield(prepare_task)
        finally:
            lease = await self._finish_prepare_waiter(
                prepare_task,
                token,
                acquire=prepared,
            )
        return lease

    def _acquire_endpointing_lease_locked(
        self,
        token: VoiceTurnToken,
    ) -> SmartTurnLease:
        self._smart_turn_lease_sequence += 1
        lease_id = self._smart_turn_lease_sequence
        self._smart_turn_lease_ids.add(lease_id)
        return SmartTurnLease(token, self, lease_id)

    async def _finish_prepare_waiter(
        self,
        prepare_task: asyncio.Task[bool],
        token: VoiceTurnToken,
        *,
        acquire: bool,
    ) -> SmartTurnLease | None:
        async with self._lock:
            waiter_count = self._prepare_waiters.get(prepare_task, 0)
            prepare_generation = self._prepare_generations.get(prepare_task)
            if waiter_count <= 1:
                self._prepare_waiters.pop(prepare_task, None)
                self._prepare_generations.pop(prepare_task, None)
            else:
                self._prepare_waiters[prepare_task] = waiter_count - 1
            if acquire and self.endpointing_ready(token):
                return self._acquire_endpointing_lease_locked(token)
            if (
                self._prepare_waiters.get(prepare_task, 0) == 0
                and not self._smart_turn_lease_ids
                and self._smart_turn_token == token
                and self._smart_turn_generation == prepare_generation
            ):
                self._smart_turn_token = None
                self._smart_turn_generation = None
                self._smart_turn_readiness = SmartTurnReadiness.UNLOADED
                adapter = self._semantic_adapter
                if adapter is not None:
                    adapter.unpin_smart_turn()
            return None

    async def _prepare_endpointing_task(
        self,
        adapter: _VoiceTurnAdapter,
        coordinator: TurnCoordinator,
        token: VoiceTurnToken,
        detector_epoch: int,
        prepare_generation: int,
    ) -> bool:
        loaded = False
        prepare_error: BaseException | None = None
        try:
            loaded = await coordinator.prepare_predictor()
        except asyncio.CancelledError as exc:
            prepare_error = exc
        except Exception as exc:
            prepare_error = exc
        prepared = False
        async with self._lock:
            current_task = asyncio.current_task()
            owns_prepare = self._prepare_task is current_task
            if owns_prepare:
                self._prepare_task = None
            valid_state = bool(
                owns_prepare
                and not self._closed
                and not adapter.failed
                and self._prepare_token == token
                and self._prepare_epoch == detector_epoch
                and self._detector_epoch == detector_epoch
            )
            has_waiters = bool(
                current_task is not None
                and self._prepare_waiters.get(current_task, 0) > 0
            )
            if owns_prepare:
                # Only the registered single-flight owner may clear these; a
                # detached task must not clobber a successor's fields.
                self._prepare_token = None
                self._prepare_epoch = None
            if valid_state and has_waiters and loaded and prepare_error is None:
                self._smart_turn_token = token
                self._smart_turn_generation = prepare_generation
                self._smart_turn_readiness = SmartTurnReadiness.READY
                prepared = True
            else:
                adapter.unpin_smart_turn()
            if valid_state and not prepared:
                self._smart_turn_readiness = (
                    SmartTurnReadiness.FAILED
                    if has_waiters
                    else SmartTurnReadiness.UNLOADED
                )
        if isinstance(prepare_error, asyncio.CancelledError):
            raise prepare_error
        return prepared

    async def _ensure_semantic_started(self, adapter: _VoiceTurnAdapter) -> None:
        if self._semantic_started:
            return
        await adapter.start()
        self._semantic_started = True
        self._failure_watch_task = asyncio.create_task(
            self._watch_semantic_failure(adapter),
            name="detector-runtime-smart-turn-watch",
        )

    def endpointing_ready(self, token: VoiceTurnToken) -> bool:
        adapter = self._semantic_adapter
        return bool(
            not self._closed
            and adapter is not None
            and not adapter.failed
            and self._smart_turn_readiness is SmartTurnReadiness.READY
            and self._smart_turn_token == token
        )

    async def release_endpointing(
        self,
        token: VoiceTurnToken,
        lease_id: int,
    ) -> None:
        async with self._lock:
            if lease_id not in self._smart_turn_lease_ids:
                return
            self._smart_turn_lease_ids.remove(lease_id)
            if self._smart_turn_lease_ids:
                return
            if self._smart_turn_token != token:
                return
            self._smart_turn_token = None
            self._smart_turn_generation = None
            self._smart_turn_readiness = SmartTurnReadiness.UNLOADED
            adapter = self._semantic_adapter
            if adapter is not None:
                adapter.unpin_smart_turn()

    async def invalidate(self, token: VoiceTurnToken) -> None:
        speaker_shadow: SpeakerShadowObserver | None = None
        async with self._lock:
            if self._smart_turn_token != token and self._prepare_token != token:
                return
            self._smart_turn_token = None
            self._smart_turn_generation = None
            self._smart_turn_lease_ids.clear()
            self._prepare_token = None
            self._prepare_epoch = None
            self._detector_epoch += 1
            self._candidate_generation = 0
            self._candidate_open = False
            self._policy_event_candidate = None
            self._throttle_policy.reset_candidate_activity()
            self._ingress_token = None
            self._bound_turns.clear()
            self._deferred_completions.clear()
            self._completion_fences.clear()
            self._provider_candidate_fence = None
            self._deferred_completion_identity_advanced = False
            self._provider_discarded_through_sequence_no = None
            # A deferred completion belongs to the invalidated epoch; keeping
            # the flags would let release_deferred_turn spuriously advance the
            # fresh epoch's first candidate.
            self._defer_turn_complete = False
            self._deferred_turn_complete = False
            self._reset_speaker_shadow_identity()
            speaker_shadow = self._speaker_shadow
            adapter = self._semantic_adapter
            if adapter is not None:
                adapter.unpin_smart_turn()
            self._smart_turn_readiness = SmartTurnReadiness.UNLOADED
        await self._reset_speaker_shadow(speaker_shadow)

    async def feed(
        self,
        pcm16: bytes,
        *,
        speech_probability: float | None = None,
        rnnoise_available: bool | None = None,
        rnnoise_evidence: RnnoiseEvidence | None = None,
        ingress_token: VoiceIngressToken | None = None,
        allow_baseline_update: bool = False,
    ) -> DetectorFeedResult:
        if not isinstance(pcm16, bytes) or len(pcm16) % 2:
            raise ValueError("DetectorRuntime requires complete PCM16 bytes")
        if not pcm16:
            return DetectorFeedResult((), self._available)
        if speech_probability is not None and not 0.0 <= speech_probability <= 1.0:
            raise ValueError("speech_probability must be within [0, 1]")
        if rnnoise_available is None:
            rnnoise_available = speech_probability is not None
        adapter = self._semantic_adapter
        if adapter is not None:
            self._events.clear()
            effective_ingress = (
                ingress_token
                or self._ingress_token
                or VoiceIngressToken(
                    session_epoch=0,
                    connection_id="detector-feed-compat",
                    lease_generation=0,
                    route_generation=0,
                    audio_generation=0,
                )
            )
            submitted = await self.submit_audio(
                pcm16,
                ingress_token=effective_ingress,
                sample_rate_hz=16_000,
                speech_probability=speech_probability,
                rnnoise_available=bool(rnnoise_available),
                rnnoise_evidence=rnnoise_evidence,
                allow_baseline_update=allow_baseline_update,
            )
            if submitted.status is DetectorSubmitStatus.SKIPPED_QUIET:
                return DetectorFeedResult(
                    (),
                    submitted.throttle_available,
                    throttle_action=submitted.throttle_action,
                )
            if submitted.status is not DetectorSubmitStatus.ACCEPTED:
                return DetectorFeedResult(
                    (),
                    submitted.throttle_available,
                    endpointing_available=submitted.endpointing_available,
                    throttle_action=submitted.throttle_action,
                )
            await adapter.wait_idle()
            if adapter.failed:
                failure = adapter.failure
                endpointing_available = getattr(failure, "stage", None) not in {
                    "smart_turn",
                    "consumer",
                }
                return DetectorFeedResult(
                    (),
                    False,
                    endpointing_available=endpointing_available,
                    throttle_action=submitted.throttle_action,
                )
            events = tuple(self._events)
            if any(
                event
                in {
                    SpeechActivityEvent.SPEECH_STARTED,
                    SpeechActivityEvent.SPEECH_RESUMED,
                }
                for event in events
            ):
                self._speech_active = True
            return DetectorFeedResult(
                events,
                adapter.throttle_available,
                throttle_action=submitted.throttle_action,
            )
        async with self._lock:
            if self._closed or not self._available:
                return DetectorFeedResult((), False)
            effective_ingress = ingress_token or VoiceIngressToken(
                session_epoch=0,
                connection_id="detector-feed-compat",
                lease_generation=0,
                route_generation=0,
                audio_generation=0,
            )
            if self._ingress_token is None:
                self._ingress_token = effective_ingress
            elif self._ingress_token != effective_ingress:
                return DetectorFeedResult((), False, endpointing_available=False)
            evidence = rnnoise_evidence or RnnoiseEvidence.from_legacy_probability(
                speech_probability,
                available=bool(rnnoise_available),
            )
            candidate_admission_open = bool(
                self._candidate_open or self._provider_candidate_fence is not None
            )
            throttle = self._throttle_policy.decide(
                evidence,
                candidate_open=candidate_admission_open,
                allow_baseline_update=allow_baseline_update,
            )
            if throttle.action is ThrottleAction.SKIP_IDLE_PCM:
                return DetectorFeedResult(
                    (),
                    True,
                    throttle_action=throttle.action,
                )
            if not self._load_attempted:
                self._load_attempted = True
                try:
                    self._available = bool(await asyncio.to_thread(self._vad.load))
                except Exception:
                    self._available = False
                if not self._available:
                    return DetectorFeedResult(
                        (),
                        False,
                        throttle_action=throttle.action,
                    )
            try:
                events = tuple(await asyncio.to_thread(self._gate.feed, pcm16))
            except Exception:
                self._available = False
                return DetectorFeedResult(
                    (),
                    False,
                    throttle_action=throttle.action,
                )
            self._candidate_open = True
            self._sequence_no += 1
            identity = DetectorIngressIdentity(
                ingress_token=effective_ingress,
                detector_epoch=self._detector_epoch,
                sequence_no=self._sequence_no,
            )
            candidate = DetectorCandidateKey(
                self._detector_epoch,
                self._candidate_generation,
            )
            if (
                throttle.action is ThrottleAction.PREWARM
                and self._on_event is not None
                and self._policy_event_candidate != candidate
            ):
                self._policy_event_candidate = candidate
                await self._on_event(DetectorTransportPrewarmEvent(identity))
            if any(
                event
                in {
                    SpeechActivityEvent.SPEECH_STARTED,
                    SpeechActivityEvent.SPEECH_RESUMED,
                }
                for event in events
            ):
                self._speech_active = True
            for event in events:
                self._throttle_policy.observe_silero(event)
        return DetectorFeedResult(
            events,
            True,
            throttle_action=throttle.action,
        )

    def observe_provider_audio(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
    ) -> None:
        """Observe Provider PCM only after the ASR dispatcher admits it."""

        if (
            self._closed
            or self._semantic_adapter is not None
            or not isinstance(pcm16, bytes)
            or not pcm16
            or len(pcm16) % 2
            or sample_rate_hz <= 0
        ):
            return
        candidate = self._speaker_shadow_candidate
        if candidate is None:
            candidate = self._open_speaker_shadow_candidate("provider_candidate")
        if candidate is None or candidate.scope != "provider_candidate":
            return
        self._submit_speaker_shadow(
            pcm16,
            sample_rate_hz=sample_rate_hz,
            candidate=candidate,
        )

    async def seal_provider_candidate(self) -> ProviderCandidateFence | None:
        """Seal local detector activity after a streaming Provider endpoint."""

        async with self._lock:
            if self._closed or self._semantic_adapter is not None:
                return None
            existing = self._provider_candidate_fence
            if existing is not None:
                return existing
            fence = ProviderCandidateFence(
                detector_epoch=self._detector_epoch,
                candidate_generation=self._candidate_generation,
                through_sequence_no=self._sequence_no,
            )
            self._provider_candidate_fence = fence
            self._provider_discarded_through_sequence_no = None
            self._candidate_generation += 1
            self._candidate_open = False
            self._speech_active = False
            self._policy_event_candidate = None
            self._throttle_policy.reset_candidate_activity()
            self._finish_speaker_shadow_candidate(expected_scope="provider_candidate")
            return fence

    async def discard_provider_successor(
        self,
        fence: ProviderCandidateFence,
    ) -> bool:
        """Discard only successor activity while preserving the sealed fence."""

        speaker_shadow: SpeakerShadowObserver | None = None
        async with self._lock:
            if (
                self._closed
                or self._semantic_adapter is not None
                or fence != self._provider_candidate_fence
                or fence.detector_epoch != self._detector_epoch
            ):
                return False
            await asyncio.to_thread(self._gate.reset)
            self._provider_discarded_through_sequence_no = self._sequence_no
            self._candidate_generation += 1
            self._candidate_open = False
            self._speech_active = False
            self._policy_event_candidate = None
            self._throttle_policy.reset_candidate_activity()
            self._reset_speaker_shadow_identity()
            speaker_shadow = self._speaker_shadow
        await self._reset_speaker_shadow(speaker_shadow)
        return True

    async def complete_provider_candidate(
        self,
        fence: ProviderCandidateFence,
    ) -> bool | None:
        """Consume one Provider fence and report whether successor PCM exists."""

        async with self._lock:
            if (
                self._closed
                or fence != self._provider_candidate_fence
                or fence.detector_epoch != self._detector_epoch
            ):
                return None
            self._provider_candidate_fence = None
            successor_floor = max(
                fence.through_sequence_no,
                self._provider_discarded_through_sequence_no
                or fence.through_sequence_no,
            )
            self._provider_discarded_through_sequence_no = None
            successor_present = self._sequence_no > successor_floor
            successor_confirmed = successor_present and self._speech_active
            if not successor_confirmed:
                self._candidate_open = False
                self._speech_active = False
                self._policy_event_candidate = None
                self._throttle_policy.reset_candidate_activity()
            return successor_present

    async def submit_audio(
        self,
        pcm16: bytes,
        *,
        ingress_token: VoiceIngressToken,
        sample_rate_hz: int,
        speech_probability: float | None,
        rnnoise_available: bool,
        rnnoise_evidence: RnnoiseEvidence | None = None,
        allow_baseline_update: bool = False,
    ) -> DetectorSubmitResult:
        """Validate and enqueue one frame without waiting for detector inference."""

        if not isinstance(pcm16, bytes) or len(pcm16) % 2:
            raise ValueError("DetectorRuntime requires complete PCM16 bytes")
        if sample_rate_hz <= 0:
            raise ValueError("DetectorRuntime sample rate must be positive")
        if speech_probability is not None and not 0.0 <= speech_probability <= 1.0:
            raise ValueError("speech_probability must be within [0, 1]")
        adapter = self._semantic_adapter
        if self._closed:
            return DetectorSubmitResult(
                DetectorSubmitStatus.CLOSED,
                False,
                False,
                None,
            )
        overflow_reset_task = self._overflow_reset_task
        if overflow_reset_task is not None and not overflow_reset_task.done():
            return DetectorSubmitResult(
                DetectorSubmitStatus.BACKPRESSURE,
                adapter.throttle_available if adapter is not None else False,
                True,
                None,
            )
        if self._smart_turn_readiness is SmartTurnReadiness.FAILED:
            return DetectorSubmitResult(
                DetectorSubmitStatus.FAILED,
                adapter.throttle_available if adapter is not None else False,
                False,
                None,
            )
        if adapter is None or adapter.failed:
            return DetectorSubmitResult(
                DetectorSubmitStatus.FAILED,
                False,
                False,
                None,
            )
        if not pcm16:
            return DetectorSubmitResult(
                DetectorSubmitStatus.SKIPPED_QUIET,
                adapter.throttle_available,
                True,
                None,
            )
        if self._ingress_token is None:
            self._ingress_token = ingress_token
        elif self._ingress_token != ingress_token:
            return DetectorSubmitResult(
                DetectorSubmitStatus.FAILED,
                adapter.throttle_available,
                True,
                None,
            )
        evidence = rnnoise_evidence or RnnoiseEvidence.from_legacy_probability(
            speech_probability,
            available=rnnoise_available,
        )
        throttle = self._throttle_policy.decide(
            evidence,
            candidate_open=self._candidate_open,
            allow_baseline_update=allow_baseline_update,
        )
        if throttle.action is ThrottleAction.SKIP_IDLE_PCM:
            return DetectorSubmitResult(
                DetectorSubmitStatus.SKIPPED_QUIET,
                adapter.throttle_available,
                True,
                None,
                throttle.action,
            )
        self._candidate_open = True
        await self._ensure_semantic_started(adapter)
        next_sequence = self._sequence_no + 1
        identity = DetectorIngressIdentity(
            ingress_token=ingress_token,
            detector_epoch=self._detector_epoch,
            sequence_no=next_sequence,
        )
        try:
            await adapter.push_audio(
                generation=self._semantic_generation,
                buffer_epoch=0,
                utterance_id=self._semantic_turn_id,
                pcm16=pcm16,
                sample_rate_hz=sample_rate_hz,
                detector_identity=identity,
            )
        except asyncio.QueueFull:
            self._detector_epoch += 1
            self._reset_speaker_shadow_identity()
            speaker_shadow = self._speaker_shadow
            self._candidate_generation = 0
            self._candidate_open = False
            self._policy_event_candidate = None
            self._throttle_policy.reset_candidate_activity()
            self._ingress_token = None
            self._bound_turns.clear()
            self._deferred_completion_identity_advanced = False
            self._deferred_completions.clear()
            self._completion_fences.clear()
            self._provider_candidate_fence = None
            self._provider_discarded_through_sequence_no = None
            self._defer_turn_complete = False
            self._deferred_turn_complete = False
            self._semantic_generation += 1
            self._semantic_turn_id += 1
            overflow_reset_task = asyncio.create_task(
                self._reset_after_overflow(
                    adapter,
                    self._semantic_generation,
                    self._semantic_turn_id,
                    speaker_shadow,
                ),
                name="detector-runtime-overflow-reset",
            )
            self._overflow_reset_task = overflow_reset_task
            return DetectorSubmitResult(
                DetectorSubmitStatus.BACKPRESSURE,
                adapter.throttle_available,
                True,
                None,
            )
        self._sequence_no = next_sequence
        candidate = DetectorCandidateKey(
            identity.detector_epoch,
            self._candidate_generation,
        )
        control_event_emitted = False
        if (
            self._on_event is not None
            and self._policy_event_candidate != candidate
            and throttle.action in {ThrottleAction.PREWARM, ThrottleAction.PROCESS_PCM}
        ):
            self._policy_event_candidate = candidate
            await self._on_event(
                DetectorPrewarmEvent(
                    ingress=identity,
                    candidate=candidate,
                    kind=(
                        "continuous"
                        if throttle.action is ThrottleAction.PROCESS_PCM
                        else "prewarm"
                    ),
                )
            )
            control_event_emitted = True
        return DetectorSubmitResult(
            DetectorSubmitStatus.ACCEPTED,
            adapter.throttle_available,
            True,
            identity,
            throttle.action,
            candidate,
            control_event_emitted,
        )

    async def _reset_after_overflow(
        self,
        adapter: _VoiceTurnAdapter,
        generation: int,
        utterance_id: int,
        speaker_shadow: SpeakerShadowObserver | None,
    ) -> None:
        failed = False
        try:
            await adapter.reset(
                generation=generation,
                buffer_epoch=0,
                utterance_id=utterance_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            failed = True
        finally:
            try:
                await self._reset_speaker_shadow(speaker_shadow)
            finally:
                async with self._lock:
                    if self._overflow_reset_task is asyncio.current_task():
                        self._overflow_reset_task = None
                    if failed and not self._closed:
                        self._smart_turn_readiness = SmartTurnReadiness.FAILED

    async def reset(self) -> None:
        overflow_reset_task = self._overflow_reset_task
        if (
            overflow_reset_task is not None
            and overflow_reset_task is not asyncio.current_task()
        ):
            await asyncio.gather(overflow_reset_task, return_exceptions=True)
        adapter: _VoiceTurnAdapter | None = None
        semantic_identity: tuple[int, int, int] | None = None
        speaker_shadow: SpeakerShadowObserver | None = None
        try:
            async with self._lock:
                if self._closed:
                    return
                self._detector_epoch += 1
                self._reset_speaker_shadow_identity()
                speaker_shadow = self._speaker_shadow
                self._candidate_generation = 0
                self._sequence_no = 0
                self._ingress_token = None
                self._candidate_open = False
                self._policy_event_candidate = None
                self._throttle_policy.reset_candidate_activity()
                self._bound_turns.clear()
                self._deferred_completions.clear()
                self._completion_fences.clear()
                self._provider_candidate_fence = None
                self._provider_discarded_through_sequence_no = None
                self._speech_active = False
                self._prepare_token = None
                self._prepare_epoch = None
                self._smart_turn_generation = None
                self._smart_turn_lease_ids.clear()
                self._smart_turn_readiness = SmartTurnReadiness.UNLOADED
                if self._semantic_adapter is not None and self._semantic_started:
                    if self._smart_turn_token is not None:
                        self._smart_turn_token = None
                        self._semantic_adapter.unpin_smart_turn()
                    self._defer_turn_complete = False
                    self._deferred_turn_complete = False
                    self._deferred_completion_identity_advanced = False
                    self._semantic_generation += 1
                    self._semantic_turn_id += 1
                    adapter = self._semantic_adapter
                    semantic_identity = (
                        self._semantic_generation,
                        0,
                        self._semantic_turn_id,
                    )
                else:
                    # feed() runs gate.feed in a thread while holding self._lock;
                    # the gate counters are unlocked, so the reset must run under
                    # the same lock to avoid interleaving with an in-flight feed.
                    await asyncio.to_thread(self._gate.reset)
            if adapter is not None and semantic_identity is not None:
                await adapter.reset(
                    generation=semantic_identity[0],
                    buffer_epoch=semantic_identity[1],
                    utterance_id=semantic_identity[2],
                )
        finally:
            await self._reset_speaker_shadow(speaker_shadow)

    async def release_deferred_turn(self) -> None:
        """Release a deferred SmartTurn completion after the prior final."""

        callback: Callable[[], Awaitable[None]] | None = None
        async with self._lock:
            if self._closed or self._semantic_adapter is None:
                return
            self._defer_turn_complete = False
            if self._deferred_turn_complete:
                self._deferred_turn_complete = False
                self._defer_turn_complete = True
                identity_advanced = self._deferred_completion_identity_advanced
                self._deferred_completion_identity_advanced = False
                if not identity_advanced:
                    self._semantic_generation += 1
                    self._semantic_turn_id += 1
                    await self._semantic_adapter.reset(
                        generation=self._semantic_generation,
                        buffer_epoch=0,
                        utterance_id=self._semantic_turn_id,
                    )
                callback = self._on_turn_complete
        if callback is not None:
            # 不持有 detector lock 调用 Core，避免 Core 清理时反向 reset 死锁。
            await callback()

    async def close(self) -> None:
        close_task = self._close_task
        if close_task is None:
            close_task = asyncio.create_task(
                self._close_impl(),
                name="detector-runtime-close",
            )
            self._close_task = close_task
        await asyncio.shield(close_task)

    async def _close_impl(self) -> None:
        adapter: _VoiceTurnAdapter | None = None
        vad = None
        prepare_task: asyncio.Task[bool] | None = None
        overflow_reset_task: asyncio.Task[None] | None = None
        speaker_shadow: SpeakerShadowObserver | None = None
        try:
            async with self._lock:
                if self._closed:
                    return
                self._closed = True
                self._detector_epoch += 1
                self._reset_speaker_shadow_identity()
                speaker_shadow = self._speaker_shadow
                self._candidate_generation = 0
                self._candidate_open = False
                self._policy_event_candidate = None
                self._throttle_policy.reset_candidate_activity()
                self._ingress_token = None
                self._bound_turns.clear()
                self._deferred_completions.clear()
                self._completion_fences.clear()
                self._provider_candidate_fence = None
                self._provider_discarded_through_sequence_no = None
                watch_task, self._failure_watch_task = self._failure_watch_task, None
                if watch_task is not None:
                    watch_task.cancel()
                if self._semantic_adapter is not None:
                    overflow_reset_task = self._overflow_reset_task
                    self._smart_turn_token = None
                    self._smart_turn_generation = None
                    self._smart_turn_lease_ids.clear()
                    self._prepare_token = None
                    self._prepare_epoch = None
                    prepare_task, self._prepare_task = self._prepare_task, None
                    if prepare_task is not None:
                        prepare_task.cancel()
                    self._smart_turn_readiness = SmartTurnReadiness.UNLOADING
                    adapter = self._semantic_adapter
                else:
                    vad = self._vad
            if adapter is not None:
                if overflow_reset_task is not None:
                    await asyncio.gather(overflow_reset_task, return_exceptions=True)
                await adapter.close()
                if prepare_task is not None:
                    await asyncio.gather(prepare_task, return_exceptions=True)
                self._smart_turn_readiness = SmartTurnReadiness.UNLOADED
            else:
                await asyncio.to_thread(vad.close)
        finally:
            await self._close_speaker_shadow(speaker_shadow)

    def _observe_smart_turn_speaker_shadow(
        self,
        pcm16: bytes,
        sample_rate_hz: int,
        detector_identity: DetectorIngressIdentity | None,
    ) -> None:
        if (
            detector_identity is None
            or detector_identity.detector_epoch != self._detector_epoch
            or detector_identity.ingress_token != self._ingress_token
            or detector_identity.sequence_no > self._sequence_no
        ):
            return
        candidate = self._speaker_shadow_candidate
        if candidate is None:
            candidate = self._open_speaker_shadow_candidate("smart_turn_turn")
        if candidate is None or candidate.scope != "smart_turn_turn":
            return
        self._submit_speaker_shadow(
            pcm16,
            sample_rate_hz=sample_rate_hz,
            candidate=candidate,
        )

    def _finish_smart_turn_speaker_shadow(
        self,
        detector_identity: DetectorIngressIdentity | None,
    ) -> None:
        if (
            detector_identity is None
            or detector_identity.detector_epoch != self._detector_epoch
        ):
            return
        self._finish_speaker_shadow_candidate(expected_scope="smart_turn_turn")

    def _open_speaker_shadow_candidate(
        self,
        scope: Literal["provider_candidate", "smart_turn_turn"],
    ) -> SpeakerShadowCandidateKey | None:
        shadow = self._speaker_shadow
        if shadow is None:
            return None
        try:
            if not shadow.enabled:
                return None
        except Exception:
            return None
        candidate = SpeakerShadowCandidateKey(
            detector_epoch=self._detector_epoch,
            shadow_generation=self._speaker_shadow_generation,
            scope=scope,
        )
        self._speaker_shadow_candidate = candidate
        return candidate

    def _submit_speaker_shadow(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
        candidate: SpeakerShadowCandidateKey,
    ) -> None:
        shadow = self._speaker_shadow
        if shadow is None:
            return
        try:
            shadow.submit(
                pcm16,
                sample_rate_hz=sample_rate_hz,
                candidate=candidate,
            )
        except Exception:
            return

    def _finish_speaker_shadow_candidate(
        self,
        *,
        expected_scope: Literal["provider_candidate", "smart_turn_turn"],
    ) -> None:
        candidate = self._speaker_shadow_candidate
        self._speaker_shadow_candidate = None
        self._speaker_shadow_generation += 1
        if candidate is None or candidate.scope != expected_scope:
            return
        shadow = self._speaker_shadow
        if shadow is None:
            return
        try:
            shadow.finish_candidate(candidate)
        except Exception:
            return

    def _reset_speaker_shadow_identity(self) -> None:
        self._speaker_shadow_generation += 1
        self._speaker_shadow_candidate = None

    @staticmethod
    async def _reset_speaker_shadow(
        shadow: SpeakerShadowObserver | None,
    ) -> None:
        if shadow is None:
            return
        try:
            await shadow.reset()
        except Exception:
            return

    @staticmethod
    async def _close_speaker_shadow(
        shadow: SpeakerShadowObserver | None,
    ) -> None:
        if shadow is None:
            return
        try:
            await shadow.close()
        except Exception:
            return

    async def _watch_semantic_failure(self, adapter: _VoiceTurnAdapter) -> None:
        try:
            failure = await adapter.wait_failure()
            if getattr(failure, "stage", None) in {"vad_load", "vad_feed"}:
                self._available = False
                return
            self._detector_epoch += 1
            self._reset_speaker_shadow_identity()
            self._candidate_generation = 0
            self._candidate_open = False
            self._policy_event_candidate = None
            self._throttle_policy.reset_candidate_activity()
            self._ingress_token = None
            self._bound_turns.clear()
            self._deferred_completions.clear()
            self._completion_fences.clear()
            self._provider_candidate_fence = None
            self._provider_discarded_through_sequence_no = None
            self._smart_turn_readiness = SmartTurnReadiness.FAILED
            await self._reset_speaker_shadow(self._speaker_shadow)
            callback = self._on_endpointing_failure
            if callback is not None and not self._closed:
                await callback()
        except asyncio.CancelledError:
            return
