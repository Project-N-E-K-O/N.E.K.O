"""Per-session Smart Turn adapter for segmented ASR providers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeAlias

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
        self._fallback_task: asyncio.Task[None] | None = None
        self._callback_tasks: set[asyncio.Task[None]] = set()
        self._identity: _Identity | None = None
        self._vad_load_attempted = False
        self._vad_available = False
        self._closed = False
        self._commit_dispatched: set[_Identity] = set()

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("ASR_VOICE_TURN_CLOSED: adapter is closed")
        if self._consumer_task is None:
            self._consumer_task = asyncio.create_task(
                self._consume(), name="asr-voice-turn"
            )

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
        if self._closed:
            return
        task = self._consumer_task
        if task is None:
            self._closed = True
            await self._coordinator.close()
            await asyncio.to_thread(self._vad.close)
            return
        if task.done():
            self._closed = True
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            await self._coordinator.close()
            await asyncio.to_thread(self._vad.close)
            for callback_task in tuple(self._callback_tasks):
                callback_task.cancel()
            if self._callback_tasks:
                await asyncio.gather(*self._callback_tasks, return_exceptions=True)
            return
        completed = asyncio.get_running_loop().create_future()
        await self._queue.put(_CloseItem(completed))
        await completed
        await task

    def _ensure_running(self) -> None:
        task = self._consumer_task
        if self._closed or task is None or task.done():
            raise RuntimeError("ASR_VOICE_TURN_CLOSED: adapter is not running")

    async def _consume(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if isinstance(item, _AudioItem):
                    await self._process_audio(item)
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
            except BaseException as exc:
                completed = getattr(item, "completed", None)
                if completed is not None and not completed.done():
                    completed.set_exception(exc)
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
            self._vad_available = await asyncio.to_thread(self._vad.load)
        if not self._vad_available:
            return

        events = await asyncio.to_thread(self._gate.feed, item.pcm16)
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

        result = await self._coordinator.evaluate_buffered()
        if item.identity != self._identity:
            return
        if (
            result.status is EvaluationStatus.OK
            and result.decision is TurnDecision.COMPLETE
        ):
            self._dispatch_commit(item.identity)
            return
        if (
            result.status is EvaluationStatus.OK
            and result.decision is TurnDecision.INCOMPLETE
        ):
            self._schedule_fallback(item.identity)

    async def _process_reset(self, identity: _Identity) -> None:
        self._cancel_fallback()
        await self._coordinator.reset()
        await asyncio.to_thread(self._gate.reset)
        self._identity = identity
        self._commit_dispatched.clear()

    async def _process_close(self) -> None:
        self._closed = True
        self._cancel_fallback()
        await self._coordinator.close()
        await asyncio.to_thread(self._vad.close)
        for task in tuple(self._callback_tasks):
            task.cancel()
        if self._callback_tasks:
            await asyncio.gather(*self._callback_tasks, return_exceptions=True)

    def _schedule_fallback(self, identity: _Identity) -> None:
        self._cancel_fallback()

        async def fallback() -> None:
            await asyncio.sleep(self._continuation_timeout_seconds)
            if (
                not self._closed
                and identity == self._identity
                and self._coordinator.state is CoordinatorState.WAIT_CONTINUATION
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
