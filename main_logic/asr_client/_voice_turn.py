"""Per-session Smart Turn adapter for segmented ASR providers."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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

from .detector_queue import DetectorDurationQueue
from .detector_contracts import DetectorIngressIdentity


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
        self._on_activity = on_activity
        self._on_scoped_commit = on_scoped_commit
        self._on_scoped_activity = on_scoped_activity
        self._queue: DetectorDurationQueue[_AudioItem, _ControlItem] = (
            DetectorDurationQueue(
                capacity_us=queue_capacity_ms * 1_000,
                max_frames=queue_maxsize,
            )
        )
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
            return
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
            self._dispatch_commit(item.identity, item.detector_identity)
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
    ) -> None:
        if self._closed or identity != self._identity:
            return
        if identity in self._commit_dispatched:
            return
        self._commit_dispatched.add(identity)

        async def commit() -> None:
            try:
                if self._closed or self._failed or identity != self._identity:
                    return
                if self._on_scoped_commit is not None and detector_identity is not None:
                    await self._on_scoped_commit(*identity, detector_identity)
                    if self._closed or self._failed or identity != self._identity:
                        return
                if self._closed or self._failed or identity != self._identity:
                    return
                await self._on_commit(*identity)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._report_failure("runtime_error", "consumer")

        task = asyncio.create_task(
            commit(), name="asr-voice-turn-commit"
        )
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_tasks.discard)


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
