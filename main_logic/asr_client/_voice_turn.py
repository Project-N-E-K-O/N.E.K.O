"""Per-session Smart Turn adapter for segmented ASR providers."""

from __future__ import annotations

import asyncio
import logging
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


_Identity: TypeAlias = tuple[int, int, int]
_FallbackReason: TypeAlias = Literal["semantic_incomplete", "semantic_degraded"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _VoiceTurnFailure:
    kind: Literal["unavailable", "runtime_error"]
    stage: Literal["vad_load", "vad_feed", "consumer"]


@dataclass(frozen=True, slots=True)
class _AudioItem:
    identity: _Identity
    pcm16: bytes


@dataclass(frozen=True, slots=True)
class _ResetItem:
    identity: _Identity
    completed: asyncio.Future[None]


@dataclass(frozen=True, slots=True)
class _CloseItem:
    completed: asyncio.Future[None]


_QueueItem: TypeAlias = _AudioItem | _ResetItem | _CloseItem


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
        queue_maxsize: int = 64,
        continuation_timeout_seconds: float = 2.0,
    ) -> None:
        if queue_maxsize <= 0:
            raise ValueError("queue_maxsize must be positive")
        if continuation_timeout_seconds <= 0:
            raise ValueError("continuation_timeout_seconds must be positive")
        self._vad = vad
        self._gate = gate
        self._coordinator = coordinator
        self._on_commit = on_commit
        self._on_activity = on_activity
        self._queue: asyncio.Queue[_QueueItem] = asyncio.Queue(maxsize=queue_maxsize)
        self._continuation_timeout_seconds = continuation_timeout_seconds
        self._consumer_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._fallback_task: asyncio.Task[None] | None = None
        self._callback_tasks: set[asyncio.Task[None]] = set()
        self._identity: _Identity | None = None
        self._vad_load_attempted = False
        self._vad_available = False
        self._semantic_degraded = False
        self._failed = False
        self._failure_future: asyncio.Future[_VoiceTurnFailure] | None = None
        self._resources_closed = False
        self._closed = False
        self._commit_dispatched: set[_Identity] = set()

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

    async def push_audio(
        self,
        *,
        generation: int,
        buffer_epoch: int,
        utterance_id: int,
        pcm16: bytes,
    ) -> None:
        if len(pcm16) % 2:
            raise ValueError("ASR_INVALID_PCM: Voice Turn requires PCM16LE")
        if not pcm16:
            return
        self._ensure_running()
        await self._queue.put(
            _AudioItem((generation, buffer_epoch, utterance_id), pcm16)
        )

    async def reset(
        self,
        *,
        generation: int,
        buffer_epoch: int,
        utterance_id: int,
    ) -> None:
        self._ensure_running()
        completed = asyncio.get_running_loop().create_future()
        await self._queue.put(
            _ResetItem((generation, buffer_epoch, utterance_id), completed)
        )
        await completed

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
            await asyncio.gather(task, return_exceptions=True)
        if task.done():
            self._closed = True
            await asyncio.gather(task, return_exceptions=True)
            await self._close_resources()
            return
        completed = asyncio.get_running_loop().create_future()
        await self._queue.put(_CloseItem(completed))
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
                    await self._process_reset(item.identity)
                    if not item.completed.done():
                        item.completed.set_result(None)
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
        self._coordinator.push_audio(item.pcm16)
        if not self._vad_load_attempted:
            self._vad_load_attempted = True
            try:
                self._vad_available = await asyncio.to_thread(self._vad.load)
            except Exception:
                self._report_failure("runtime_error", "vad_load")
                return
        if not self._vad_available:
            self._report_failure("unavailable", "vad_load")
            return

        try:
            events = await asyncio.to_thread(self._gate.feed, item.pcm16)
        except Exception:
            self._report_failure("runtime_error", "vad_feed")
            return
        for event in events:
            if self._on_activity is not None:
                await self._on_activity(event)
            await self._coordinator.on_activity_event(event)

        if any(
            event
            in (SpeechActivityEvent.SPEECH_STARTED, SpeechActivityEvent.SPEECH_RESUMED)
            for event in events
        ):
            self._cancel_fallback()

        if (
            SpeechActivityEvent.CANDIDATE_PAUSE not in events
            or self._coordinator.state is not CoordinatorState.PAUSE_CANDIDATE
        ):
            return

        if self._semantic_degraded:
            self._schedule_fallback(item.identity, "semantic_degraded")
            return

        result = await self._coordinator.evaluate_buffered()
        if item.identity != self._identity:
            return
        status = getattr(result, "status", None)
        decision = getattr(result, "decision", None)
        if (
            status is EvaluationStatus.OK
            and decision is TurnDecision.COMPLETE
        ):
            self._dispatch_commit(item.identity)
            return
        if (
            status is EvaluationStatus.OK
            and decision is TurnDecision.INCOMPLETE
        ):
            self._schedule_fallback(item.identity, "semantic_incomplete")
            return
        if status is EvaluationStatus.STALE:
            return
        self._enter_semantic_degraded()
        self._schedule_fallback(item.identity, "semantic_degraded")

    async def _process_reset(self, identity: _Identity) -> None:
        self._cancel_fallback()
        await self._coordinator.reset()
        await asyncio.to_thread(self._gate.reset)
        self._identity = identity
        self._commit_dispatched.clear()

    async def _process_close(self) -> None:
        self._closed = True
        self._cancel_fallback()
        await self._close_resources()

    async def _close_resources(self) -> None:
        if self._resources_closed:
            return
        self._resources_closed = True
        await self._coordinator.close()
        await asyncio.to_thread(self._vad.close)
        for task in tuple(self._callback_tasks):
            task.cancel()
        if self._callback_tasks:
            await asyncio.gather(*self._callback_tasks, return_exceptions=True)

    def _schedule_fallback(
        self,
        identity: _Identity,
        reason: _FallbackReason,
    ) -> None:
        self._cancel_fallback()

        async def fallback() -> None:
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
                self._dispatch_commit(identity)

        self._fallback_task = asyncio.create_task(
            fallback(), name="asr-voice-turn-fallback"
        )

    def _cancel_fallback(self) -> None:
        task = self._fallback_task
        self._fallback_task = None
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
        stage: Literal["vad_load", "vad_feed", "consumer"],
    ) -> None:
        if self._failed or self._closed:
            return
        self._failed = True
        self._cancel_fallback()
        failure_future = self._failure_future
        if failure_future is None:
            failure_future = asyncio.get_running_loop().create_future()
            self._failure_future = failure_future
        if not failure_future.done():
            failure_future.set_result(_VoiceTurnFailure(kind, stage))

    def _dispatch_commit(self, identity: _Identity) -> None:
        if self._closed or identity != self._identity:
            return
        if identity in self._commit_dispatched:
            return
        self._commit_dispatched.add(identity)
        task = asyncio.create_task(
            self._on_commit(*identity), name="asr-voice-turn-commit"
        )
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_tasks.discard)


def _create_voice_turn_adapter(
    on_commit: Callable[[int, int, int], Awaitable[None]],
    *,
    on_activity: Callable[[SpeechActivityEvent], Awaitable[None]] | None = None,
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
    )
