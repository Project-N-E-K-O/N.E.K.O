"""Session-level endpoint detector and Smart Turn adapter."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Literal, TypeAlias

from main_logic.voice_turn.contracts import (
    EvaluationStatus,
    SmartTurnConfig,
    SpeechActivityEvent,
    TurnDecision,
)
from main_logic.voice_turn.coordinator import CoordinatorState, TurnCoordinator
from main_logic.voice_turn.silero_vad import SileroActivityGate, SileroVad
from main_logic.voice_turn.smart_turn_v3 import SmartTurnV3

from .detector import (
    BoundDetectorTurn,
    DetectorActivityEvent,
    DetectorCandidateKey,
    DetectorDurationQueue,
    DetectorEvent,
    DetectorIngressIdentity,
    DetectorPrewarmEvent,
    DetectorTransportPrewarmEvent,
    DetectorSubmitResult,
    DetectorSubmitStatus,
    DetectorTurnEvent,
    ProviderCandidateFence,
    SmartTurnCompletionFence,
)
from .activity_evidence import RnnoiseEvidence
from .lifecycle import VoiceIngressToken, VoiceTurnToken
from .provider_policy import AsrProviderPolicy
from .speaker_shadow import SpeakerShadowRuntime
from .throttle_policy import (
    ThrottleAction,
    ThrottleShadowMetrics,
    VoiceThrottlePolicy,
)


logger = logging.getLogger(__name__)


_Identity: TypeAlias = tuple[int, int, int]
_FallbackReason: TypeAlias = Literal["semantic_incomplete", "semantic_degraded"]

logger = logging.getLogger(__name__)


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


_ControlItem: TypeAlias = _ResetItem | _CloseItem | _EvaluationResultItem
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
        queue_maxsize: int = 128,
        queue_capacity_ms: int = 1_000,
        continuation_timeout_seconds: float = 2.0,
        max_endpoint_wait_seconds: float = 15.0,
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
        if smart_turn_warm_seconds <= 0:
            raise ValueError("smart_turn_warm_seconds must be positive")
        if fallback_evaluation_interval_ms <= 0:
            raise ValueError("fallback_evaluation_interval_ms must be positive")
        self._vad = vad
        self._gate = gate
        self._coordinator = coordinator
        self._on_commit = on_commit
        self._on_completion_fence = on_completion_fence
        self._on_activity = on_activity
        self._on_scoped_commit = on_scoped_commit
        self._on_scoped_activity = on_scoped_activity
        self._queue: DetectorDurationQueue[_AudioItem, _ControlItem] = (
            DetectorDurationQueue(
                capacity_us=queue_capacity_ms * 1_000,
                max_frames=queue_maxsize,
            )
        )
        self._evaluation_tail_capacity_us = queue_capacity_ms * 1_000
        self._evaluation_tail: list[_AudioItem] = []
        self._evaluation_tail_duration_us = 0
        self._continuation_timeout_seconds = continuation_timeout_seconds
        self._max_endpoint_wait_seconds = max_endpoint_wait_seconds
        self._smart_turn_required = smart_turn_required
        self._smart_turn_warm_seconds = smart_turn_warm_seconds
        self._fallback_evaluation_interval_ms = fallback_evaluation_interval_ms
        self._consumer_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._fallback_task: asyncio.Task[None] | None = None
        self._smart_turn_unload_task: asyncio.Task[None] | None = None
        self._evaluation_task: asyncio.Task[None] | None = None
        self._reevaluation_requested = False
        self._reevaluation_reason: Literal[
            "candidate_pause", "periodic_no_vad", "strict_retry"
        ] | None = None
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
        self._fallback_audio_ms = 0
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
            raise ValueError(
                "ASR_INVALID_SAMPLE_RATE: Voice Turn requires 16 kHz"
            )
        self._ensure_running()
        samples = len(pcm16) // 2
        duration_us = (
            samples * 1_000_000 + sample_rate_hz - 1
        ) // sample_rate_hz
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
        self._queue.put_control_nowait(_CloseItem(completed), priority=True)
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
                        return
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
                    return
                raise
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
        if self._evaluation_task is not None:
            next_duration = self._evaluation_tail_duration_us + item.duration_us
            if next_duration > self._evaluation_tail_capacity_us:
                self._report_failure("runtime_error", "consumer")
                return
            self._evaluation_tail.append(item)
            self._evaluation_tail_duration_us = next_duration
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
            if self._on_scoped_activity is not None and item.detector_identity is not None:
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
            if self._on_scoped_activity is not None and item.detector_identity is not None:
                await self._on_scoped_activity(event, item.detector_identity)
            await self._coordinator.on_activity_event(event)
        self._fallback_audio_ms += len(item.pcm16) * 1_000 // (16_000 * 2)
        if started_now:
            return
        if self._fallback_audio_ms < self._fallback_evaluation_interval_ms:
            return
        self._fallback_audio_ms = 0
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
        if (
            self._closed
            or self._failed
            or not identity_matches
            or not generation_matches
            or not activity_matches
        ):
            if reevaluate and identity_matches and not self._closed and not self._failed:
                self._request_evaluation(
                    item.identity,
                    reevaluation_reason,
                    self._latest_detector_identity,
                )
            return
        if item.error is not None:
            self._report_failure("runtime_error", "smart_turn")
            return
        result = item.result
        status = getattr(result, "status", None)
        decision = getattr(result, "decision", None)
        if status is EvaluationStatus.STALE:
            self._smart_turn_stale_result_count += 1
            if reevaluate:
                self._request_evaluation(
                    item.identity,
                    reevaluation_reason,
                    self._latest_detector_identity,
                )
            return
        if status is EvaluationStatus.OK and decision is TurnDecision.COMPLETE:
            self._strict_endpoint_deadline = None
            active_identity = item.identity
            if (
                self._on_completion_fence is not None
                and item.detector_identity is not None
            ):
                active_identity = self._on_completion_fence(
                    *item.identity,
                    item.detector_identity,
                )
                if active_identity != item.identity:
                    await self._process_reset(
                        active_identity,
                        requester=asyncio.current_task(),
                    )
                    self._successor_audio_fence = (
                        item.identity,
                        item.detector_identity.sequence_no,
                        active_identity,
                    )
            completion_published = self._dispatch_commit(
                item.identity,
                item.detector_identity,
                active_identity=active_identity,
            )
            if completion_published is not None:
                await completion_published
            for tail_item in evaluation_tail:
                await self._process_audio(
                    _AudioItem(
                        identity=active_identity,
                        pcm16=tail_item.pcm16,
                        duration_us=tail_item.duration_us,
                        detector_identity=tail_item.detector_identity,
                    )
                )
            return
        if status is EvaluationStatus.OK and decision is TurnDecision.INCOMPLETE:
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

    async def _process_reset(
        self,
        identity: _Identity,
        *,
        requester: asyncio.Task[object] | None = None,
    ) -> None:
        self._cancel_fallback()
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
        self._successor_audio_fence = None
        await self._coordinator.reset()
        await asyncio.to_thread(self._gate.reset)
        self._commit_dispatched.clear()
        self._fallback_speech_started = False
        self._fallback_audio_ms = 0
        if self._smart_turn_pin_count == 0:
            self._schedule_smart_turn_unload(identity)

    async def _process_close(self) -> None:
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

    def _cancel_fallback(self) -> None:
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
        self._cancel_fallback()
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
                if (
                    self._closed
                    or self._failed
                    or expected_identity != self._identity
                ):
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
                if (
                    self._closed
                    or self._failed
                    or expected_identity != self._identity
                ):
                    return
                await self._on_commit(*identity)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._report_failure("runtime_error", "consumer")
            finally:
                if not completion_published.done():
                    completion_published.set_result(None)

        task = asyncio.create_task(
            commit(), name="asr-voice-turn-commit"
        )
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
    _released: bool = False

    async def release(self) -> None:
        if self._released:
            return
        await self._runtime.release_endpointing(self.token)
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
        speaker_shadow: SpeakerShadowRuntime | None = None,
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
        self._rnnoise_onset_probability = rnnoise_onset_probability
        self._resource_optimization_enabled = bool(resource_optimization_enabled)
        self._throttle_policy = throttle_policy or VoiceThrottlePolicy(
            resource_optimization_enabled=self._resource_optimization_enabled,
            bootstrap_onset=rnnoise_onset_probability,
        )
        self._speaker_shadow = speaker_shadow
        self._speech_active = False
        self._events: list[SpeechActivityEvent] = []
        self._semantic_adapter: _VoiceTurnAdapter | None = None
        self._semantic_coordinator: TurnCoordinator | None = None
        self._semantic_started = False
        self._semantic_generation = 0
        self._semantic_turn_id = 1
        self._on_endpointing_failure = on_endpointing_failure
        self._on_turn_complete = on_turn_complete
        self._on_event = on_event
        self._defer_turn_complete = False
        self._deferred_turn_complete = False
        self._failure_watch_task: asyncio.Task[None] | None = None
        self._smart_turn_readiness = SmartTurnReadiness.UNLOADED
        self._smart_turn_token: VoiceTurnToken | None = None
        self._prepare_task: asyncio.Task[bool] | None = None
        self._prepare_token: VoiceTurnToken | None = None
        self._prepare_epoch: int | None = None
        self._session_smart_turn_pinned = False
        self._overflow_reset_task: asyncio.Task[None] | None = None
        self._detector_epoch = 0
        self._sequence_no = 0
        self._ingress_token: VoiceIngressToken | None = None
        self._candidate_open = False
        self._candidate_generation = 0
        self._policy_event_candidate: DetectorCandidateKey | None = None
        self._bound_turns: dict[DetectorCandidateKey, BoundDetectorTurn] = {}
        self._deferred_completions: dict[
            DetectorCandidateKey, DetectorIngressIdentity
        ] = {}
        self._completion_fences: dict[
            tuple[int, int, int], SmartTurnCompletionFence
        ] = {}
        self._provider_candidate_fence: ProviderCandidateFence | None = None
        self._provider_discarded_through_sequence_no: int | None = None
        if provider_policy is not None and provider_policy.endpoint_authority == "smart_turn":
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
            self._semantic_coordinator = semantic_coordinator

            def completion_fence(
                generation: int,
                buffer_epoch: int,
                turn_id: int,
                identity: DetectorIngressIdentity,
            ) -> tuple[int, int, int]:
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
                self._events.append(event)
                self._throttle_policy.observe_silero(event)

            async def scoped_activity(
                event: SpeechActivityEvent,
                identity: DetectorIngressIdentity,
            ) -> None:
                self._throttle_policy.observe_silero(event)
                if self._on_event is None or identity.detector_epoch != self._detector_epoch:
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
                if self._on_event is None or identity.detector_epoch != self._detector_epoch:
                    return
                fence = self._completion_fences.get(
                    (generation, buffer_epoch, turn_id)
                )
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
            adapter.smart_turn_coalesced_evaluation_count
            if adapter is not None
            else 0
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
        async with self._lock:
            if self._closed or adapter.failed:
                self._smart_turn_readiness = SmartTurnReadiness.FAILED
                return None
            if (
                self._smart_turn_token == token
                and self._smart_turn_readiness is SmartTurnReadiness.READY
            ):
                return SmartTurnLease(token, self)
            if self._smart_turn_token is not None:
                return None
            if (
                self._smart_turn_readiness is SmartTurnReadiness.READY
                and self._prepare_task is None
            ):
                adapter.pin_smart_turn()
                self._smart_turn_token = token
                return SmartTurnLease(token, self)
            if self._prepare_task is not None:
                if self._prepare_token != token:
                    return None
                prepare_task = self._prepare_task
            else:
                self._smart_turn_readiness = SmartTurnReadiness.LOADING
                self._prepare_token = token
                self._prepare_epoch = self._detector_epoch
                adapter.pin_smart_turn()
                prepare_task = asyncio.create_task(
                    self._prepare_endpointing_task(
                        adapter,
                        coordinator,
                        token,
                        self._detector_epoch,
                    ),
                    name="detector-runtime-smart-turn-prepare",
                )
                self._prepare_task = prepare_task
        if prepare_task is None or not await asyncio.shield(prepare_task):
            return None
        if self.endpointing_ready(token):
            return SmartTurnLease(token, self)
        return None

    async def pin_endpointing_session(self) -> bool:
        """Load and retain SmartTurn for a continuous-upload voice session."""

        adapter = self._semantic_adapter
        coordinator = self._semantic_coordinator
        if self._closed or adapter is None or coordinator is None:
            self._smart_turn_readiness = SmartTurnReadiness.FAILED
            return False
        await self._ensure_semantic_started(adapter)
        async with self._lock:
            if self._closed or adapter.failed:
                self._smart_turn_readiness = SmartTurnReadiness.FAILED
                return False
            if self._session_smart_turn_pinned:
                return self._smart_turn_readiness is not SmartTurnReadiness.FAILED
            self._session_smart_turn_pinned = True
            self._smart_turn_readiness = SmartTurnReadiness.LOADING
            adapter.pin_smart_turn()
            detector_epoch = self._detector_epoch
        loaded = False
        cancelled = False
        try:
            loaded = await coordinator.prepare_predictor()
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            loaded = False
        async with self._lock:
            valid = bool(
                self._session_smart_turn_pinned
                and not self._closed
                and not adapter.failed
                and detector_epoch == self._detector_epoch
            )
            if valid and loaded:
                self._smart_turn_readiness = SmartTurnReadiness.READY
                return True
            if self._session_smart_turn_pinned:
                self._session_smart_turn_pinned = False
                adapter.unpin_smart_turn()
            if valid:
                self._smart_turn_readiness = SmartTurnReadiness.FAILED
        if cancelled:
            raise asyncio.CancelledError
        return False

    async def release_endpointing_session(self) -> None:
        async with self._lock:
            if not self._session_smart_turn_pinned:
                return
            self._session_smart_turn_pinned = False
            adapter = self._semantic_adapter
            if adapter is not None:
                adapter.unpin_smart_turn()
            if self._smart_turn_token is None and self._prepare_token is None:
                self._smart_turn_readiness = SmartTurnReadiness.UNLOADED

    async def _prepare_endpointing_task(
        self,
        adapter: _VoiceTurnAdapter,
        coordinator: TurnCoordinator,
        token: VoiceTurnToken,
        detector_epoch: int,
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
            owns_prepare = self._prepare_task is asyncio.current_task()
            if owns_prepare:
                self._prepare_task = None
            valid = bool(
                owns_prepare
                and not self._closed
                and not adapter.failed
                and self._prepare_token == token
                and self._prepare_epoch == detector_epoch
                and self._detector_epoch == detector_epoch
            )
            self._prepare_token = None
            self._prepare_epoch = None
            if valid and loaded and prepare_error is None:
                self._smart_turn_token = token
                self._smart_turn_readiness = SmartTurnReadiness.READY
                prepared = True
            else:
                adapter.unpin_smart_turn()
            if valid and not prepared:
                self._smart_turn_readiness = SmartTurnReadiness.FAILED
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

    async def release_endpointing(self, token: VoiceTurnToken) -> None:
        async with self._lock:
            if self._smart_turn_token != token and self._prepare_token != token:
                return
            self._smart_turn_token = None
            self._prepare_token = None
            self._prepare_epoch = None
            self._smart_turn_readiness = SmartTurnReadiness.UNLOADED
            adapter = self._semantic_adapter
            if adapter is not None:
                adapter.unpin_smart_turn()

    async def invalidate(self, token: VoiceTurnToken) -> None:
        async with self._lock:
            if self._smart_turn_token != token and self._prepare_token != token:
                return
            self._smart_turn_token = None
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
            adapter = self._semantic_adapter
            if adapter is not None:
                adapter.unpin_smart_turn()
            self._smart_turn_readiness = SmartTurnReadiness.UNLOADED

    async def feed(
        self,
        pcm16: bytes,
        *,
        speech_probability: float | None = None,
        rnnoise_available: bool | None = None,
        rnnoise_evidence: RnnoiseEvidence | None = None,
        ingress_token: VoiceIngressToken | None = None,
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
            ingress_token = self._ingress_token or VoiceIngressToken(
                session_epoch=0,
                connection_id="detector-feed-compat",
                lease_generation=0,
                route_generation=0,
                audio_generation=0,
            )
            submitted = await self.submit_audio(
                pcm16,
                ingress_token=ingress_token,
                sample_rate_hz=16_000,
                speech_probability=speech_probability,
                rnnoise_available=rnnoise_available,
                rnnoise_evidence=rnnoise_evidence,
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
            evidence = rnnoise_evidence or RnnoiseEvidence.from_legacy_probability(
                speech_probability,
                available=bool(rnnoise_available),
            )
            throttle = self._throttle_policy.decide(
                evidence,
                candidate_open=self._speech_active,
                allow_baseline_update=not self._speech_active,
            )
            if throttle.action is ThrottleAction.SKIP_IDLE_PCM:
                return DetectorFeedResult(
                    (),
                    True,
                    throttle_action=throttle.action,
                )
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
            # PC 48k 已经过 RNNoise：低概率环境音在尚未进入说话态时
            # 不唤醒 Silero；移动端 16k 没有该概率，仍完整运行 Silero。
            try:
                events = tuple(await asyncio.to_thread(self._gate.feed, pcm16))
            except Exception:
                self._available = False
                return DetectorFeedResult(
                    (),
                    False,
                    throttle_action=throttle.action,
                )
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
            self._submit_speaker_shadow(
                pcm16,
                sample_rate_hz=16_000,
                candidate=DetectorCandidateKey(
                    self._detector_epoch,
                    self._candidate_generation,
                ),
            )
        return DetectorFeedResult(
            events,
            True,
            throttle_action=throttle.action,
        )

    async def seal_provider_candidate(self) -> ProviderCandidateFence | None:
        """Seal local detector activity after the Provider declares an endpoint."""

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
            self._speech_active = False
            self._policy_event_candidate = None
            self._throttle_policy.reset_candidate_activity()
            return fence

    async def discard_provider_successor(
        self,
        fence: ProviderCandidateFence,
    ) -> bool:
        """Discard only post-endpoint detector activity for a Provider turn."""

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
            self._speech_active = False
            self._policy_event_candidate = None
            self._throttle_policy.reset_candidate_activity()
            return True

    async def complete_provider_candidate(
        self,
        fence: ProviderCandidateFence,
    ) -> bool | None:
        """Consume one Provider fence and report whether successor audio exists."""

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
            if not successor_present:
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
            self._candidate_generation = 0
            self._candidate_open = False
            self._policy_event_candidate = None
            self._throttle_policy.reset_candidate_activity()
            self._ingress_token = None
            self._bound_turns.clear()
            self._deferred_completions.clear()
            self._completion_fences.clear()
            self._semantic_generation += 1
            self._semantic_turn_id += 1
            overflow_reset_task = asyncio.create_task(
                self._reset_after_overflow(
                    adapter,
                    self._semantic_generation,
                    self._semantic_turn_id,
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
        self._submit_speaker_shadow(
            pcm16,
            sample_rate_hz=sample_rate_hz,
            candidate=candidate,
        )
        if (
            self._on_event is not None
            and self._policy_event_candidate != candidate
            and throttle.action
            in {ThrottleAction.PREWARM, ThrottleAction.ALLOW_PROVIDER_AUDIO}
        ):
            self._policy_event_candidate = candidate
            await self._on_event(
                DetectorPrewarmEvent(
                    ingress=identity,
                    candidate=candidate,
                    kind=(
                        "continuous"
                        if throttle.action is ThrottleAction.ALLOW_PROVIDER_AUDIO
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
            async with self._lock:
                if self._overflow_reset_task is asyncio.current_task():
                    self._overflow_reset_task = None
                if failed and not self._closed:
                    self._smart_turn_readiness = SmartTurnReadiness.FAILED

    async def reset(self) -> None:
        if self._speaker_shadow is not None:
            await self._speaker_shadow.reset()
        overflow_reset_task = self._overflow_reset_task
        if (
            overflow_reset_task is not None
            and overflow_reset_task is not asyncio.current_task()
        ):
            await asyncio.gather(overflow_reset_task, return_exceptions=True)
        adapter: _VoiceTurnAdapter | None = None
        semantic_identity: tuple[int, int, int] | None = None
        prepare_task: asyncio.Task[bool] | None = None
        async with self._lock:
            if self._closed:
                return
            self._detector_epoch += 1
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
            if (
                self._prepare_task is not None
                and self._prepare_task is not asyncio.current_task()
            ):
                prepare_task = self._prepare_task
                prepare_task.cancel()
            self._smart_turn_readiness = SmartTurnReadiness.UNLOADED
            if self._semantic_adapter is not None and self._semantic_started:
                if self._smart_turn_token is not None:
                    self._smart_turn_token = None
                    self._semantic_adapter.unpin_smart_turn()
                if self._session_smart_turn_pinned:
                    self._session_smart_turn_pinned = False
                    self._semantic_adapter.unpin_smart_turn()
                self._defer_turn_complete = False
                self._deferred_turn_complete = False
                self._semantic_generation += 1
                self._semantic_turn_id += 1
                adapter = self._semantic_adapter
                semantic_identity = (
                    self._semantic_generation,
                    0,
                    self._semantic_turn_id,
                )
        if prepare_task is not None:
            await asyncio.gather(prepare_task, return_exceptions=True)
        if adapter is not None and semantic_identity is not None:
            await adapter.reset(
                generation=semantic_identity[0],
                buffer_epoch=semantic_identity[1],
                utterance_id=semantic_identity[2],
            )
            return
        await asyncio.to_thread(self._gate.reset)

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
        if self._speaker_shadow is not None:
            await self._speaker_shadow.close()
        adapter: _VoiceTurnAdapter | None = None
        vad = None
        prepare_task: asyncio.Task[bool] | None = None
        overflow_reset_task: asyncio.Task[None] | None = None
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._detector_epoch += 1
            self._candidate_generation = 0
            self._candidate_open = False
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
                release_turn_pin = self._smart_turn_token is not None
                self._smart_turn_token = None
                if release_turn_pin:
                    self._semantic_adapter.unpin_smart_turn()
                if self._session_smart_turn_pinned:
                    self._session_smart_turn_pinned = False
                    self._semantic_adapter.unpin_smart_turn()
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
            return
        await asyncio.to_thread(vad.close)

    @property
    def speaker_shadow_metrics(self) -> dict[str, int]:
        """Return observation-only metrics without exposing routing controls."""

        if self._speaker_shadow is None:
            return {}
        return self._speaker_shadow.snapshot()

    def _submit_speaker_shadow(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
        candidate: DetectorCandidateKey,
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
            # Speaker verification is a shadow observer and cannot fail ASR.
            return

    async def _watch_semantic_failure(self, adapter: _VoiceTurnAdapter) -> None:
        try:
            failure = await adapter.wait_failure()
            if getattr(failure, "stage", None) in {"vad_load", "vad_feed"}:
                self._available = False
                return
            self._detector_epoch += 1
            self._candidate_generation = 0
            self._candidate_open = False
            self._throttle_policy.reset_candidate_activity()
            self._ingress_token = None
            self._bound_turns.clear()
            self._deferred_completions.clear()
            self._completion_fences.clear()
            self._smart_turn_readiness = SmartTurnReadiness.FAILED
            callback = self._on_endpointing_failure
            if callback is not None and not self._closed:
                await callback()
        except asyncio.CancelledError:
            return
