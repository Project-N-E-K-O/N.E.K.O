"""Async Smart Turn coordination with stale-result and coalescing guards."""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Protocol

import numpy as np

from .audio_buffer import Pcm16RingBuffer
from .contracts import (
    EvaluationStatus,
    SmartTurnConfig,
    SpeechActivityEvent,
    TurnDecision,
    TurnEvaluation,
)
from .onnx_runtime import RuntimeInferenceError, RuntimeUnavailableError


class ProbabilityPredictor(Protocol):
    def load(self) -> bool: ...

    def predict_probability(self, audio: np.ndarray) -> float: ...

    def unload(self) -> bool: ...

    def close(self) -> None: ...


class CoordinatorState(Enum):
    IDLE = "idle"
    SPEECH_ACTIVE = "speech_active"
    PAUSE_CANDIDATE = "pause_candidate"
    EVALUATING = "evaluating"
    WAIT_CONTINUATION = "wait_continuation"
    CLOSED = "closed"


class TurnCoordinator:
    """Own model evaluation state, but never commit a provider buffer."""

    def __init__(self, predictor: ProbabilityPredictor, config: SmartTurnConfig) -> None:
        self._predictor = predictor
        self._config = config
        self._buffer = Pcm16RingBuffer(config.max_audio_seconds)
        self._generation = 0
        self._activity_seq = 0
        self._request_seq = 0
        self._latest_request = 0
        self._state = CoordinatorState.IDLE
        self._closed = False
        self._state_lock = asyncio.Lock()
        self._evaluation_lock = asyncio.Lock()

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def activity_seq(self) -> int:
        return self._activity_seq

    @property
    def state(self) -> CoordinatorState:
        return self._state

    def push_audio(self, pcm16_le: bytes) -> None:
        if self._closed:
            return
        self._buffer.append(pcm16_le)

    async def on_activity_event(self, event: SpeechActivityEvent) -> None:
        if event is SpeechActivityEvent.NONE or self._closed:
            return
        async with self._state_lock:
            if self._closed:
                return
            if event in (SpeechActivityEvent.SPEECH_STARTED, SpeechActivityEvent.SPEECH_RESUMED):
                self._activity_seq += 1
                self._state = CoordinatorState.SPEECH_ACTIVE
            elif event is SpeechActivityEvent.CANDIDATE_PAUSE:
                self._state = CoordinatorState.PAUSE_CANDIDATE

    async def on_speech_started(self) -> None:
        await self.on_activity_event(SpeechActivityEvent.SPEECH_STARTED)

    async def evaluate_buffered(self) -> TurnEvaluation:
        return await self.evaluate(self._buffer.snapshot_bytes())

    async def prepare_predictor(self) -> bool:
        """Load SmartTurn without performing endpoint inference."""

        async with self._evaluation_lock:
            if self._closed:
                return False
            try:
                return bool(await self._run_predictor_call(self._predictor.load))
            except asyncio.CancelledError:
                raise
            except Exception:
                return False

    async def evaluate(self, audio_tail: bytes) -> TurnEvaluation:
        if len(audio_tail) % 2:
            raise ValueError("Smart Turn input must contain complete PCM16 samples")
        async with self._state_lock:
            self._request_seq += 1
            request = self._request_seq
            self._latest_request = request
            generation = self._generation
            activity_seq = self._activity_seq
            if self._closed:
                return self._non_ok(
                    EvaluationStatus.STALE, generation, activity_seq, "coordinator_closed"
                )

        async with self._evaluation_lock:
            async with self._state_lock:
                if request != self._latest_request:
                    return self._non_ok(
                        EvaluationStatus.STALE, generation, activity_seq, "candidate_superseded"
                    )
                if generation != self._generation or activity_seq != self._activity_seq:
                    return self._non_ok(
                        EvaluationStatus.STALE, generation, activity_seq, "activity_changed"
                    )
                self._state = CoordinatorState.EVALUATING

            if not audio_tail:
                result = self._non_ok(
                    EvaluationStatus.UNAVAILABLE, generation, activity_seq, "empty_audio"
                )
            else:
                try:
                    loaded = await self._run_predictor_call(self._predictor.load)
                    if not loaded:
                        result = self._non_ok(
                            EvaluationStatus.UNAVAILABLE,
                            generation,
                            activity_seq,
                            "model_unavailable",
                        )
                    else:
                        audio = np.frombuffer(audio_tail, dtype="<i2").astype(np.float32) / 32768.0
                        probability = await self._run_predictor_call(
                            self._predictor.predict_probability, audio
                        )
                        decision = (
                            TurnDecision.COMPLETE
                            if probability >= self._config.evaluation_threshold
                            else TurnDecision.INCOMPLETE
                        )
                        result = TurnEvaluation(
                            EvaluationStatus.OK,
                            decision,
                            probability,
                            generation,
                            activity_seq,
                        )
                except asyncio.CancelledError:
                    async with self._state_lock:
                        if (
                            not self._closed
                            and request == self._latest_request
                            and generation == self._generation
                            and activity_seq == self._activity_seq
                            and self._state is CoordinatorState.EVALUATING
                        ):
                            self._state = CoordinatorState.PAUSE_CANDIDATE
                    raise
                except RuntimeUnavailableError as exc:
                    result = self._non_ok(
                        EvaluationStatus.UNAVAILABLE, generation, activity_seq, str(exc)
                    )
                except (RuntimeInferenceError, Exception) as exc:
                    result = self._non_ok(
                        EvaluationStatus.ERROR,
                        generation,
                        activity_seq,
                        f"{type(exc).__name__}:{exc}",
                    )

            async with self._state_lock:
                if (
                    self._closed
                    or request != self._latest_request
                    or generation != self._generation
                    or activity_seq != self._activity_seq
                ):
                    return self._non_ok(
                        EvaluationStatus.STALE, generation, activity_seq, "result_became_stale"
                    )
                self._state = (
                    CoordinatorState.WAIT_CONTINUATION
                    if result.status is EvaluationStatus.OK
                    and result.decision is TurnDecision.INCOMPLETE
                    else CoordinatorState.PAUSE_CANDIDATE
                )
                return result

    @staticmethod
    async def _run_predictor_call(call, *args):
        """Keep the inference lane owned until a cancelled thread call exits."""

        task = asyncio.create_task(asyncio.to_thread(call, *args))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            try:
                task.result()
            except BaseException:
                pass
            raise asyncio.CancelledError
        return task.result()

    async def reset(self) -> None:
        async with self._state_lock:
            if self._closed:
                return
            self._generation += 1
            self._latest_request = self._request_seq + 1
            self._state = CoordinatorState.IDLE
            self._buffer.reset()

    async def unload_predictor(self) -> None:
        """Release SmartTurn after idle without closing the coordinator."""

        async with self._evaluation_lock:
            if self._closed:
                return
            unload = getattr(self._predictor, "unload", None)
            if callable(unload):
                await asyncio.to_thread(unload)

    async def close(self) -> None:
        async with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            self._latest_request = self._request_seq + 1
            self._state = CoordinatorState.CLOSED
            self._buffer.reset()
        # Drain the single inference lane before releasing the session. The
        # generation change above already guarantees any late result is stale.
        async with self._evaluation_lock:
            await asyncio.to_thread(self._predictor.close)

    @staticmethod
    def _non_ok(
        status: EvaluationStatus, generation: int, activity_seq: int, reason: str
    ) -> TurnEvaluation:
        return TurnEvaluation(status, None, None, generation, activity_seq, reason)
