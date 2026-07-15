"""Independent-ASR + Smart Turn orchestration for one voice session."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from main_logic.asr_client import (
    AsrSessionConfig,
    AsrTranscriptEvent,
    RealtimeAsrSession,
    create_asr_session,
)
from main_logic.voice_turn.contracts import (
    EvaluationStatus,
    SmartTurnConfig,
    SpeechActivityEvent,
    TurnDecision,
)
from main_logic.voice_turn.coordinator import TurnCoordinator
from main_logic.voice_turn.silero_vad import SileroActivityGate, SileroVad
from main_logic.voice_turn.smart_turn_v3 import SmartTurnV3


logger = logging.getLogger(__name__)

CaptionCallback = Callable[[str, bool, str | None], Awaitable[None]]
TurnCallback = Callable[["ExternalTextTurn"], Awaitable[None]]
SpeechStartedCallback = Callable[[], Awaitable[None]]
StatusCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ExternalTextTurn:
    turn_id: str
    generation: int
    buffer_epoch: int
    utterance_ids: tuple[int, ...]
    text: str
    created_at: float


class IndependentAsrRuntime:
    """Own ASR metadata, semantic endpointing, aggregation, and deduplication.

    This runtime never sends microphone audio to the realtime LLM. Its only
    remote-LLM output is a completed ``ExternalTextTurn`` delivered through
    ``on_turn_complete``.
    """

    _FINAL_SETTLE_SECONDS = 0.15
    _FINAL_TIMEOUT_SECONDS = 5.0
    _COMPLETED_TURN_LIMIT = 256

    def __init__(
        self,
        *,
        core_type: str,
        language: str = "zh",
        on_caption: CaptionCallback,
        on_turn_complete: TurnCallback,
        on_speech_started: SpeechStartedCallback,
        on_connection_error: StatusCallback,
        on_status_message: StatusCallback | None = None,
        smart_turn_config: SmartTurnConfig | None = None,
        asr_session: RealtimeAsrSession | None = None,
        predictor: SmartTurnV3 | None = None,
        vad: SileroVad | None = None,
    ) -> None:
        self._config = smart_turn_config or SmartTurnConfig(enabled=True)
        if not self._config.enabled:
            raise ValueError("independent ASR runtime requires Smart Turn to be enabled")
        self._on_caption = on_caption
        self._on_turn_complete = on_turn_complete
        self._on_speech_started = on_speech_started
        self._on_connection_error = on_connection_error
        self._on_status_message = on_status_message
        self._predictor = predictor or SmartTurnV3(enabled=True)
        self._vad = vad or SileroVad(enabled=True)
        self._coordinator = TurnCoordinator(self._predictor, self._config)
        self._activity_gate = SileroActivityGate(self._vad, self._config)
        self._asr = asr_session or create_asr_session(
            core_type,
            config=AsrSessionConfig(
                language=language,
                endpointing_mode="manual",
            ),
            on_input_transcript=self._legacy_final_noop,
            on_transcript_event=self._on_asr_event,
            on_connection_error=self._on_connection_error,
            on_status_message=self._on_status_message,
        )
        self._started = False
        self._closed = False
        self._turn_complete_requested = False
        self._deferred_completion = False
        self._active_turn_id: str | None = None
        self._active_generation = 0
        self._asr_generation: int | None = None
        self._asr_buffer_epoch: int | None = None
        self._segments: OrderedDict[tuple[int, int, int], str] = OrderedDict()
        self._finalize_task: asyncio.Task[None] | None = None
        self._final_timeout_task: asyncio.Task[None] | None = None
        self._completed_turn_ids: OrderedDict[str, None] = OrderedDict()
        self._inference_errors = 0

    @property
    def is_ready(self) -> bool:
        return self._started and not self._closed and self._asr.is_ready

    async def start(self) -> None:
        if self._started:
            return
        if self._closed:
            raise RuntimeError("independent ASR runtime is closed")
        vad_ready, smart_turn_ready = await asyncio.gather(
            asyncio.to_thread(self._vad.load),
            asyncio.to_thread(self._predictor.load),
        )
        if not vad_ready or not smart_turn_ready:
            raise RuntimeError("independent ASR models are unavailable or unverified")
        await self._asr.connect()
        self._started = True

    async def feed_audio(self, pcm16_le: bytes) -> None:
        if not self.is_ready:
            raise RuntimeError("independent ASR runtime is not ready")
        if not pcm16_le:
            return
        if len(pcm16_le) % 2:
            raise ValueError("independent ASR requires complete PCM16 samples")

        await self._asr.stream_audio(pcm16_le, sample_rate_hz=16_000)
        self._coordinator.push_audio(pcm16_le)
        event = await asyncio.to_thread(self._activity_gate.feed, pcm16_le)
        if event is SpeechActivityEvent.NONE:
            return
        await self._coordinator.on_activity_event(event)
        if event is SpeechActivityEvent.SPEECH_STARTED:
            await self._on_speech_started()
            return
        if event is SpeechActivityEvent.SPEECH_RESUMED:
            return
        if event is SpeechActivityEvent.CANDIDATE_PAUSE:
            await self._evaluate_candidate_pause()

    async def _evaluate_candidate_pause(self) -> None:
        result = await self._coordinator.evaluate_buffered()
        if result.status is EvaluationStatus.STALE:
            return
        if result.status is EvaluationStatus.OK:
            self._inference_errors = 0
            if result.decision is TurnDecision.INCOMPLETE:
                return
            await self._request_turn_completion(result.generation)
            return

        self._inference_errors += 1
        logger.warning(
            "smart_turn evaluation failed status=%s reason=%s count=%d",
            result.status.value,
            result.reason,
            self._inference_errors,
        )
        if self._inference_errors >= self._config.inference_error_limit:
            # Bounded availability fallback: after repeated local inference
            # failures, close the audible utterance instead of deadlocking the
            # conversation forever. ASR remains the sole transcript source.
            await self._request_turn_completion(result.generation)

    async def _request_turn_completion(self, generation: int) -> None:
        if self._turn_complete_requested:
            # A fast follow-up utterance can finish while the prior manual ASR
            # commit is still waiting for its final. Preserve one deferred
            # boundary; provider audio continues in the next utterance buffer.
            self._deferred_completion = True
            return
        self._turn_complete_requested = True
        self._active_turn_id = uuid4().hex
        self._active_generation = generation
        await self._asr.signal_user_activity_end()
        if self._segments:
            self._schedule_finalize()
        self._final_timeout_task = asyncio.create_task(
            self._wait_for_final_timeout(self._active_turn_id),
            name="independent-asr-final-timeout",
        )
        self._activity_gate.reset()
        await self._coordinator.reset()

    async def _on_asr_event(self, event: AsrTranscriptEvent) -> None:
        if self._closed:
            return
        if self._asr_generation is not None and event.generation < self._asr_generation:
            return
        if self._asr_buffer_epoch is not None and event.buffer_epoch < self._asr_buffer_epoch:
            return
        if (
            self._asr_generation is None
            or event.generation > self._asr_generation
            or self._asr_buffer_epoch is None
            or event.buffer_epoch > self._asr_buffer_epoch
        ):
            self._segments.clear()
            self._asr_generation = event.generation
            self._asr_buffer_epoch = event.buffer_epoch
        if event.kind == "partial":
            await self._on_caption(event.text, False, self._active_turn_id)
            return

        key = (event.generation, event.buffer_epoch, event.utterance_id)
        if key in self._segments:
            return
        self._segments[key] = event.text
        await self._on_caption(event.text, True, self._active_turn_id)
        if self._turn_complete_requested:
            self._schedule_finalize()

    def _schedule_finalize(self) -> None:
        if self._finalize_task is not None and not self._finalize_task.done():
            self._finalize_task.cancel()
        self._finalize_task = asyncio.create_task(
            self._finalize_after_settle(), name="independent-asr-finalize"
        )

    async def _finalize_after_settle(self) -> None:
        try:
            await asyncio.sleep(self._FINAL_SETTLE_SECONDS)
        except asyncio.CancelledError:
            return
        if not self._turn_complete_requested or not self._segments:
            return

        turn_id = self._active_turn_id
        if turn_id is None or turn_id in self._completed_turn_ids:
            return
        keys = tuple(self._segments)
        text = self._join_segments(tuple(self._segments.values())).strip()
        if not text:
            await self._reset_turn_state()
            return
        turn = ExternalTextTurn(
            turn_id=turn_id,
            generation=keys[-1][0],
            buffer_epoch=keys[-1][1],
            utterance_ids=tuple(key[2] for key in keys),
            text=text,
            created_at=time.time(),
        )
        self._remember_completed(turn_id)
        logger.info(
            "external_turn committed turn=%s chars=%d hash=%s segments=%d",
            turn_id,
            len(text),
            hashlib.sha256(text.encode("utf-8")).hexdigest()[:8],
            len(keys),
        )
        await self._reset_turn_state(resume_deferred=True)
        await self._on_turn_complete(turn)

    async def _wait_for_final_timeout(self, turn_id: str | None) -> None:
        try:
            await asyncio.sleep(self._FINAL_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return
        if turn_id and self._active_turn_id == turn_id and not self._segments:
            await self._on_connection_error(
                "ASR_FINAL_TIMEOUT: committed utterance produced no final transcript"
            )
            await self._reset_turn_state()

    async def _reset_turn_state(self, *, resume_deferred: bool = False) -> None:
        deferred = self._deferred_completion and resume_deferred and not self._closed
        current = asyncio.current_task()
        for task in (self._finalize_task, self._final_timeout_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
        self._finalize_task = None
        self._final_timeout_task = None
        self._segments.clear()
        self._turn_complete_requested = False
        self._deferred_completion = False
        self._active_turn_id = None
        self._inference_errors = 0
        if deferred:
            await self._request_turn_completion(self._coordinator.generation)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._reset_turn_state()
        await self._asr.close()
        await self._coordinator.close()
        self._vad.close()

    def _remember_completed(self, turn_id: str) -> None:
        self._completed_turn_ids[turn_id] = None
        self._completed_turn_ids.move_to_end(turn_id)
        while len(self._completed_turn_ids) > self._COMPLETED_TURN_LIMIT:
            self._completed_turn_ids.popitem(last=False)

    @staticmethod
    async def _legacy_final_noop(_text: str) -> None:
        return

    @staticmethod
    def _join_segments(segments: tuple[str, ...]) -> str:
        combined = ""
        for segment in segments:
            clean = segment.strip()
            if not clean:
                continue
            if (
                combined
                and combined[-1].isascii()
                and combined[-1].isalnum()
                and clean[0].isascii()
                and clean[0].isalnum()
            ):
                combined += " "
            combined += clean
        return combined
