"""Core-side microphone ingress and independent-ASR bridge.

This module owns Core session, MicLease, queue, hot-swap, and transcript
delivery concerns. Provider sessions and endpointing remain encapsulated by
``main_logic.asr_client.runtime.IndependentAsrRuntime``.
"""

from __future__ import annotations

import asyncio
import json
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from main_logic.asr_client.runtime import (
    AsrRuntimeCallbacks,
    AsrStartStatus,
    IndependentAsrRuntime,
)
from main_logic.asr_client.speaker_shadow import SpeakerShadowRuntime
from main_logic.voice_turn.contracts import (
    AsrFailureEvent,
    AsrLifecycleNotification,
    AsrStatusEvent,
    AsrSubmitStatus,
    VoicePartialEvent,
    VoiceIngressToken,
    VoiceTranscriptEvent,
    VoiceTurnToken,
)
from main_logic.voice_turn.audio_input import (
    ProcessedVoiceFrame,
    VoiceInputAudioPipeline,
)
from main_logic import core as _core_facade
from main_logic.voice_input import (
    BuiltinVoiceInputConsumer,
    VoiceInputConsumerCapabilities,
    VoiceInputRegistry,
)
from main_logic.voice_input.consumers import (
    CoreChatVoiceInputConsumer,
    GameVoiceInputConsumer,
)

from ._shared import logger


@dataclass(frozen=True, slots=True)
class _QueuedMicFrame:
    message: dict
    duration_us: int
    source_rate_hz: int
    token: VoiceIngressToken
    received_at: float

    @classmethod
    def from_message(
        cls,
        message: dict,
        *,
        token: VoiceIngressToken,
        received_at: float | None = None,
    ) -> "_QueuedMicFrame":
        samples = message.get("data")
        if not isinstance(samples, list):
            raise ValueError("MIC_PCM_SAMPLES_REQUIRED")
        declared_rate_hz = message.get("sample_rate_hz")
        if declared_rate_hz is None:
            source_rate_hz = 48_000 if len(samples) == 480 else 16_000
        elif declared_rate_hz in {16_000, 48_000}:
            source_rate_hz = int(declared_rate_hz)
        else:
            raise ValueError("MIC_SAMPLE_RATE_UNSUPPORTED")
        duration_us = (len(samples) * 1_000_000 + source_rate_hz - 1) // source_rate_hz
        return cls(
            message=message,
            duration_us=duration_us,
            source_rate_hz=source_rate_hz,
            token=token,
            received_at=time.monotonic() if received_at is None else received_at,
        )


class _AudioDurationQueue:
    """Bound Core microphone ingress by duration and frame count."""

    def __init__(self, *, capacity_us: int, max_frames: int) -> None:
        if capacity_us <= 0 or max_frames <= 0:
            raise ValueError("audio queue limits must be positive")
        self.capacity_us = capacity_us
        self.maxsize = max_frames
        self._duration_us = 0
        self._queue: asyncio.Queue[_QueuedMicFrame] = asyncio.Queue(maxsize=max_frames)

    @property
    def duration_us(self) -> int:
        return self._duration_us

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()

    def put_nowait(self, frame: _QueuedMicFrame) -> None:
        if (
            self._queue.qsize() >= self.maxsize
            or self._duration_us + frame.duration_us > self.capacity_us
        ):
            raise asyncio.QueueFull
        self._queue.put_nowait(frame)
        self._duration_us += frame.duration_us

    async def get(self) -> _QueuedMicFrame:
        frame = await self._queue.get()
        self._duration_us -= frame.duration_us
        return frame

    def get_nowait(self) -> _QueuedMicFrame:
        frame = self._queue.get_nowait()
        self._duration_us -= frame.duration_us
        return frame

    def task_done(self) -> None:
        self._queue.task_done()


@dataclass(frozen=True, slots=True)
class _HotSwapAudioFrame:
    pcm16: bytes
    token: VoiceIngressToken
    speech_probability: float | None = None
    rnnoise_available: bool = False


class _HotSwapAudioBuffer:
    """Bound hot-swap PCM without silently dropping the middle of a turn."""

    def __init__(self, *, capacity_ms: int = 8_000) -> None:
        if capacity_ms <= 0:
            raise ValueError("capacity_ms must be positive")
        self._capacity_bytes = 16_000 * 2 * capacity_ms // 1_000
        self._size_bytes = 0
        self._frames: list[_HotSwapAudioFrame] = []

    @property
    def duration_ms(self) -> int:
        return self._size_bytes * 1_000 // (16_000 * 2)

    def append(self, frame: _HotSwapAudioFrame) -> bool:
        if self._size_bytes + len(frame.pcm16) > self._capacity_bytes:
            self.clear()
            return False
        self._frames.append(frame)
        self._size_bytes += len(frame.pcm16)
        return True

    def drain(self) -> tuple[_HotSwapAudioFrame, ...]:
        frames = tuple(self._frames)
        self.clear()
        return frames

    def clear(self) -> None:
        self._frames.clear()
        self._size_bytes = 0

    def __bool__(self) -> bool:
        return bool(self._frames)

    def __len__(self) -> int:
        return len(self._frames)


class AsrRuntimeMixin:
    """Core manager facade for microphone input and independent ASR."""

    def _init_asr_runtime_state(self) -> None:
        self._voice_lease_generation = -1
        self._voice_lease_connection_id = ""
        self._voice_lease_synchronized = False
        self._voice_lease_owner = "none"
        self._voice_lease_hard_muted = False
        self._voice_lease_focus_suppressed = False
        self._voice_lease_requires_abort = False
        self._voice_input_suppressed = True
        self._voice_input_suppression_reasons: set[str] = {"owner_none"}
        self._audio_stream_queue = _AudioDurationQueue(
            capacity_us=2_000_000,
            max_frames=256,
        )
        self._audio_stream_worker_task: asyncio.Task | None = None
        self._audio_stream_dropped_total = 0
        self._audio_stream_epoch = 0
        self._last_audio_stream_backlog_log_time = 0.0
        self.hot_swap_audio_cache = _HotSwapAudioBuffer(capacity_ms=8_000)
        self.hot_swap_cache_lock = asyncio.Lock()
        self.is_flushing_hot_swap_cache = False
        self._hot_swap_live_audio_inflight = 0
        self._hot_swap_live_audio_idle = asyncio.Event()
        self._hot_swap_live_audio_idle.set()
        self._omni_mic_audio_bytes = 0
        self._asr_route_mode = "blocked"
        self._microphone_route_generation = 0
        self._independent_asr_provider: str | None = None
        self._independent_asr_route_key: str | None = None
        self._speaker_shadow_factory: (
            Callable[[], SpeakerShadowRuntime | None] | None
        ) = None
        self._voice_input_audio_pipeline = VoiceInputAudioPipeline()
        self._voice_input_registry = VoiceInputRegistry()
        self._core_chat_voice_input_registration = (
            self._voice_input_registry.register_builtin(
                BuiltinVoiceInputConsumer.CORE_CHAT,
                CoreChatVoiceInputConsumer(
                    on_prepare=self._prepare_core_chat_voice_turn,
                    on_partial_event=self._send_core_chat_asr_preview,
                    on_final_event=self._dispatch_core_chat_asr_transcript,
                    on_cancelled_event=self._cancel_core_chat_voice_turn,
                ),
                capabilities=VoiceInputConsumerCapabilities(
                    accepts_partial=True,
                    accepts_final=True,
                ),
            )
        )
        self._game_voice_input_registration = (
            self._voice_input_registry.register_builtin(
                BuiltinVoiceInputConsumer.GAME,
                GameVoiceInputConsumer(
                    lanlan_name=lambda: str(getattr(self, "lanlan_name", "core")),
                ),
                capabilities=VoiceInputConsumerCapabilities(
                    accepts_partial=False,
                    accepts_final=True,
                ),
            )
        )
        self._voice_input_registry.activate(
            self._core_chat_voice_input_registration.handle
        )
        callbacks = AsrRuntimeCallbacks(
            display_name=lambda: str(getattr(self, "lanlan_name", "core")),
            on_prepare_turn=self._prepare_core_voice_turn,
            on_partial=self._send_core_asr_preview,
            on_final=self._dispatch_core_asr_transcript,
            on_failure=self._handle_core_asr_failure,
            on_status=self._send_core_asr_status,
            on_lifecycle=self._send_core_asr_lifecycle,
        )
        self._asr_runtime = IndependentAsrRuntime(callbacks)

    def _ensure_asr_runtime_state(self) -> None:
        if not hasattr(self, "_asr_runtime"):
            self._init_asr_runtime_state()

    def _set_microphone_route(
        self,
        mode: Literal["native", "independent", "blocked"],
    ) -> None:
        if mode not in {"native", "independent", "blocked"}:
            raise ValueError("MICROPHONE_ROUTE_INVALID")
        if mode != self._asr_route_mode:
            self._microphone_route_generation += 1
        self._asr_route_mode = mode

    def _capture_ingress_token(self, _lifecycle=None) -> VoiceIngressToken:
        return self._asr_runtime.capture_ingress_token(
            connection_id=self._voice_lease_connection_id,
            lease_generation=self._voice_lease_generation,
            route_generation=self._microphone_route_generation,
        )

    def _capture_native_ingress_token(self) -> VoiceIngressToken:
        return self._capture_ingress_token()

    def _ingress_token_matches(self, token: VoiceIngressToken) -> bool:
        return bool(
            token.connection_id == self._voice_lease_connection_id
            and token.lease_generation == self._voice_lease_generation
            and token.route_generation == self._microphone_route_generation
        )

    def _voice_input_accepts_pcm(self) -> bool:
        return bool(
            self._voice_lease_synchronized
            and self._voice_lease_owner in {"core", "game"}
            and self._voice_input_registry.active_accepts_input
            and not self._voice_lease_hard_muted
            and not self._voice_lease_focus_suppressed
            and not self._voice_input_suppressed
        )

    async def _start_independent_asr_if_enabled(self, input_mode: str) -> None:
        self._ensure_asr_runtime_state()
        await self._close_independent_asr(next_route_mode="blocked")
        self._omni_mic_audio_bytes = 0
        if input_mode != "audio":
            self._set_microphone_route("blocked")
            return
        core_type = str(getattr(self, "core_api_type", "") or "").strip().lower()
        try:
            settings = await _core_facade.aload_global_conversation_settings()
        except Exception:
            await self._send_core_asr_status(
                AsrStatusEvent(
                    code="ASR_INDEPENDENT_FAILED",
                    provider=core_type or "unknown",
                )
            )
            return
        enabled = bool(settings.get("independentAsrEnabled", True))
        optimization_value = settings.get(
            "voice_input_resource_optimization_enabled",
            settings.get("voiceInputResourceOptimizationEnabled", True),
        )
        if not enabled:
            self._set_microphone_route("native")
            await self._send_core_asr_status(
                AsrStatusEvent(
                    code="ASR_INDEPENDENT_DISABLED",
                    provider=core_type or "unknown",
                )
            )
            return
        self._independent_asr_route_key = core_type
        result = await self._asr_runtime.start(
            route_key=core_type,
            resource_optimization_enabled=optimization_value is not False,
            speaker_shadow_factory=self._speaker_shadow_factory,
        )
        self._independent_asr_provider = result.provider
        if result.status is AsrStartStatus.READY:
            self._set_microphone_route("independent")
        else:
            self._set_microphone_route("blocked")

    async def _close_independent_asr(
        self,
        *,
        next_route_mode: Literal["blocked"],
    ) -> None:
        del next_route_mode
        provider = self._independent_asr_provider
        omni_audio_bytes = self._omni_mic_audio_bytes
        self._set_microphone_route("blocked")
        self._invalidate_voice_pcm_sync("independent_asr_close")
        await self._asr_runtime.close()
        pipeline = self._voice_input_audio_pipeline
        try:
            await pipeline.close()
        except Exception:
            logger.warning(
                "[%s] voice input audio pipeline close failed",
                self.lanlan_name,
            )
        self._voice_input_audio_pipeline = VoiceInputAudioPipeline()
        self._independent_asr_provider = None
        self._independent_asr_route_key = None
        if omni_audio_bytes:
            logger.info(
                "[%s] microphone route metrics provider=%s omni_mic_audio_bytes=%d",
                self.lanlan_name,
                provider or "blocked",
                omni_audio_bytes,
            )

    async def _reconcile_independent_asr_after_core_change(self) -> None:
        self._ensure_asr_runtime_state()
        core_type = str(getattr(self, "core_api_type", "") or "").strip().lower()
        if core_type == self._independent_asr_route_key:
            return
        await self._start_independent_asr_if_enabled(
            str(getattr(self, "input_mode", "audio") or "audio")
        )

    def _ensure_audio_stream_worker(self) -> None:
        task = self._audio_stream_worker_task
        if task is not None and not task.done():
            return
        self._audio_stream_worker_task = self._fire_task(
            self._audio_stream_worker_loop()
        )

    def _clear_audio_stream_queue(self, reason: str) -> None:
        dropped = 0
        while True:
            try:
                self._audio_stream_queue.get_nowait()
                self._audio_stream_queue.task_done()
                dropped += 1
            except asyncio.QueueEmpty:
                break
        if dropped:
            self._audio_stream_dropped_total += dropped
            logger.info(
                "[%s] audio stream queue cleared reason=%s dropped=%d total_dropped=%d",
                self.lanlan_name,
                reason,
                dropped,
                self._audio_stream_dropped_total,
            )

    def _cancel_audio_stream_worker(self, reason: str) -> None:
        task = self._audio_stream_worker_task
        if task is None:
            return
        if task.done():
            self._audio_stream_worker_task = None
            return
        if task is asyncio.current_task():
            return
        task.cancel()
        self._audio_stream_worker_task = None
        logger.debug(
            "[%s] audio stream worker cancelled reason=%s",
            self.lanlan_name,
            reason,
        )

    async def _enqueue_audio_stream_data(self, message: dict) -> None:
        self._ensure_asr_runtime_state()
        if not self._voice_input_accepts_pcm():
            return
        token = self._capture_ingress_token()
        try:
            frame = _QueuedMicFrame.from_message(message, token=token)
        except ValueError:
            logger.warning("[%s] invalid microphone ingress frame", self.lanlan_name)
            return
        self._ensure_audio_stream_worker()
        try:
            self._audio_stream_queue.put_nowait(frame)
        except asyncio.QueueFull:
            await asyncio.sleep(0)
            if not self._ingress_token_matches(frame.token):
                return
            try:
                self._audio_stream_queue.put_nowait(frame)
            except asyncio.QueueFull:
                self._clear_audio_stream_queue("ingress_backpressure")
                self._audio_stream_dropped_total += 1
                await self._asr_runtime.abort("ingress_backpressure")
                return
        now = time.time()
        queued_duration_us = self._audio_stream_queue.duration_us
        if (
            queued_duration_us >= 1_500_000
            and now - self._last_audio_stream_backlog_log_time >= 2.0
        ):
            self._last_audio_stream_backlog_log_time = now
            logger.warning(
                "[%s] audio stream queue backlog qsize=%d duration_ms=%d "
                "max_duration_ms=%d total_dropped=%d",
                self.lanlan_name,
                self._audio_stream_queue.qsize(),
                queued_duration_us // 1_000,
                self._audio_stream_queue.capacity_us // 1_000,
                self._audio_stream_dropped_total,
            )

    async def _audio_stream_worker_loop(self) -> None:
        while True:
            frame = await self._audio_stream_queue.get()
            try:
                if not self._ingress_token_matches(frame.token):
                    self._audio_stream_dropped_total += 1
                    continue
                await self._process_microphone_stream_data(
                    frame.message,
                    ingress_token=frame.token,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.error(
                    "[%s] audio stream worker error: %s",
                    self.lanlan_name,
                    error,
                )
            finally:
                self._audio_stream_queue.task_done()

    async def _process_microphone_stream_data(
        self,
        message: dict,
        *,
        ingress_token: VoiceIngressToken,
    ) -> None:
        if not self._ingress_token_matches(ingress_token):
            return
        data = message.get("data")
        session_ref = self.session
        audio_epoch = self._audio_stream_epoch
        try:
            if not isinstance(data, list):
                logger.error("Microphone input rejected: expected a PCM sample list")
                return
            audio_bytes = struct.pack(f"<{len(data)}h", *data)
            declared_rate_hz = message.get("sample_rate_hz")
            if declared_rate_hz is None:
                source_rate_hz = 48_000 if len(data) == 480 else 16_000
            elif declared_rate_hz in {16_000, 48_000}:
                source_rate_hz = int(declared_rate_hz)
            else:
                logger.error(
                    "Microphone input rejected: unsupported sample rate %r",
                    declared_rate_hz,
                )
                return
            processed_frame = await self._voice_input_audio_pipeline.process(
                audio_bytes,
                sample_rate_hz=source_rate_hz,
            )
            if not processed_frame.pcm16:
                return
            if (
                not self._ingress_token_matches(ingress_token)
                or self.session is not session_ref
                or not self.is_active
                or self._audio_stream_epoch != audio_epoch
            ):
                return
            cache_for_hot_swap = False
            live_route_reserved = False
            async with self.hot_swap_cache_lock:
                if self.is_hot_swap_imminent or self.is_flushing_hot_swap_cache:
                    cache_for_hot_swap = True
                    accepted = self.hot_swap_audio_cache.append(
                        _HotSwapAudioFrame(
                            pcm16=processed_frame.pcm16,
                            token=ingress_token,
                            speech_probability=processed_frame.speech_probability,
                            rnnoise_available=processed_frame.rnnoise_available,
                        )
                    )
                else:
                    self._hot_swap_live_audio_inflight += 1
                    self._hot_swap_live_audio_idle.clear()
                    live_route_reserved = True
            if cache_for_hot_swap:
                if not accepted:
                    await self._asr_runtime.abort("ingress_backpressure")
                return
            try:
                if not self._ingress_token_matches(ingress_token):
                    return
                await self._route_microphone_audio(
                    processed_frame.pcm16,
                    sample_rate_hz=processed_frame.sample_rate_hz,
                    speech_probability=processed_frame.speech_probability,
                    rnnoise_available=processed_frame.rnnoise_available,
                    ingress_token=ingress_token,
                )
            finally:
                if live_route_reserved:
                    async with self.hot_swap_cache_lock:
                        self._hot_swap_live_audio_inflight = max(
                            0,
                            self._hot_swap_live_audio_inflight - 1,
                        )
                        if self._hot_swap_live_audio_inflight == 0:
                            self._hot_swap_live_audio_idle.set()
        except struct.error:
            logger.error("Microphone input rejected: invalid PCM samples")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Microphone preprocessing or ASR routing failed")

    async def _route_microphone_audio(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
        speech_probability: float | None = None,
        rnnoise_available: bool | None = None,
        ingress_token: VoiceIngressToken | None = None,
    ) -> bool:
        route_mode = self._asr_route_mode
        if not self._voice_input_accepts_pcm():
            return True
        if route_mode == "native":
            stream_audio = getattr(self.session, "stream_audio", None)
            if not callable(stream_audio):
                return True
            try:
                await stream_audio(pcm16)
                self._record_omni_microphone_audio(len(pcm16))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error(
                    "[%s] Omni native microphone routing failed",
                    self.lanlan_name,
                )
            return True
        if route_mode != "independent":
            self._set_microphone_route("blocked")
            return True
        token = ingress_token or self._capture_ingress_token()
        if not self._ingress_token_matches(token):
            return True
        result = await self._asr_runtime.submit(
            ProcessedVoiceFrame(
                pcm16=pcm16,
                sample_rate_hz=sample_rate_hz,
                speech_probability=speech_probability,
                rnnoise_available=bool(rnnoise_available),
            ),
            ingress_token=token,
        )
        if result.status is AsrSubmitStatus.UNAVAILABLE:
            self._set_microphone_route("blocked")
            self._clear_audio_stream_queue("independent_asr_unavailable")
            self.hot_swap_audio_cache.clear()
        return True

    def _record_omni_microphone_audio(self, byte_count: int) -> None:
        byte_count = int(byte_count)
        if byte_count <= 0:
            return
        if self._asr_route_mode != "native":
            raise RuntimeError("OMNI_MICROPHONE_ROUTE_FORBIDDEN")
        self._omni_mic_audio_bytes += byte_count

    async def _flush_hot_swap_audio_cache(self) -> None:
        damaged_frames: list[_HotSwapAudioFrame] = []
        flush_complete = False
        async with self.hot_swap_cache_lock:
            self.is_flushing_hot_swap_cache = True
            if self._hot_swap_live_audio_inflight == 0:
                self._hot_swap_live_audio_idle.set()
            else:
                self._hot_swap_live_audio_idle.clear()
        try:
            await self._hot_swap_live_audio_idle.wait()
            if not self.session or not self.is_active:
                async with self.hot_swap_cache_lock:
                    damaged_frames.extend(self.hot_swap_audio_cache.drain())
                return
            iteration = 0
            while iteration < 20:
                async with self.hot_swap_cache_lock:
                    audio_frames = self.hot_swap_audio_cache.drain()
                if not audio_frames:
                    break
                for index, frame in enumerate(audio_frames):
                    if not self._ingress_token_matches(frame.token):
                        continue
                    try:
                        await self._route_microphone_audio(
                            frame.pcm16,
                            sample_rate_hz=16_000,
                            speech_probability=frame.speech_probability,
                            rnnoise_available=frame.rnnoise_available,
                            ingress_token=frame.token,
                        )
                    except asyncio.CancelledError:
                        damaged_frames.extend(audio_frames[index:])
                        raise
                    except Exception:
                        damaged_frames.extend(audio_frames[index:])
                        return
                iteration += 1
            async with self.hot_swap_cache_lock:
                remaining = self.hot_swap_audio_cache.drain()
                if remaining:
                    damaged_frames.extend(remaining)
                else:
                    self.is_flushing_hot_swap_cache = False
                    self.is_hot_swap_imminent = False
                    flush_complete = True
        finally:
            async with self.hot_swap_cache_lock:
                if not flush_complete:
                    damaged_frames.extend(self.hot_swap_audio_cache.drain())
                    self.is_flushing_hot_swap_cache = False
                    self.is_hot_swap_imminent = False
            damaged_tokens: list[VoiceIngressToken] = []
            for frame in damaged_frames:
                if (
                    self._ingress_token_matches(frame.token)
                    and frame.token not in damaged_tokens
                ):
                    damaged_tokens.append(frame.token)
            for token in damaged_tokens:
                await self._asr_runtime.abort("ingress_backpressure")

    def _invalidate_voice_pcm_sync(self, reason: str) -> None:
        self._voice_input_registry.invalidate_utterance(reason)
        self._clear_audio_stream_queue(reason)
        self.hot_swap_audio_cache.clear()

    async def _apply_voice_lease_state(
        self,
        *,
        owner: str,
        hard_muted: bool,
        focus_suppressed: bool,
        reason: str,
        force_abort: bool,
    ) -> None:
        previous = (
            self._voice_lease_owner,
            self._voice_lease_hard_muted,
            self._voice_lease_focus_suppressed,
        )
        self._voice_lease_owner = owner
        self._voice_lease_hard_muted = hard_muted
        self._voice_lease_focus_suppressed = focus_suppressed
        previous_owner = previous[0]
        if owner != previous_owner:
            if owner == "game":
                self._voice_input_registry.activate(
                    self._game_voice_input_registration.handle
                )
            elif owner == "core":
                self._voice_input_registry.activate(
                    self._core_chat_voice_input_registration.handle
                )
        reasons: set[str] = set()
        if owner == "none":
            reasons.add("owner_none")
        elif owner == "game" and not self._voice_input_registry.active_accepts_input:
            reasons.add("game")
        if hard_muted:
            reasons.add("hard_mute")
        if focus_suppressed:
            reasons.add("focus")
        self._voice_input_suppression_reasons = reasons
        self._voice_input_suppressed = bool(reasons)
        self._invalidate_voice_pcm_sync(reason)
        current = (owner, hard_muted, focus_suppressed)
        should_abort = (
            force_abort or self._voice_lease_requires_abort or previous != current
        )
        self._voice_lease_requires_abort = False
        if owner == "game" and not self._voice_input_registry.active_accepts_input:
            await self._asr_runtime.suspend(reason)
        elif reason == "game_release":
            if should_abort:
                await self._asr_runtime.abort(reason)
            await self._asr_runtime.resume(reason)
        elif should_abort:
            await self._asr_runtime.abort(reason)

    async def _suspend_independent_voice_input_for_game(self) -> None:
        await self._apply_voice_lease_state(
            owner="game",
            hard_muted=self._voice_lease_hard_muted,
            focus_suppressed=self._voice_lease_focus_suppressed,
            reason="game_takeover",
            force_abort=True,
        )

    async def _resume_independent_voice_input_after_game(self) -> None:
        await self._apply_voice_lease_state(
            owner="core",
            hard_muted=self._voice_lease_hard_muted,
            focus_suppressed=self._voice_lease_focus_suppressed,
            reason="game_release",
            force_abort=False,
        )

    def _begin_voice_input_connection(self, connection_id: str) -> bool:
        normalized = str(connection_id or "").strip()
        if not normalized or normalized == self._voice_lease_connection_id:
            return False
        self._voice_lease_connection_id = normalized
        self._voice_lease_generation = -1
        self._voice_lease_synchronized = False
        self._voice_lease_owner = "none"
        self._voice_lease_hard_muted = False
        self._voice_lease_focus_suppressed = False
        self._voice_input_suppression_reasons = {"owner_none"}
        self._voice_input_suppressed = True
        self._voice_lease_requires_abort = True
        self._invalidate_voice_pcm_sync("websocket_reconnect")
        return True

    async def _handle_voice_input_control(
        self,
        event: str,
        lease_generation: int,
        *,
        owner: str | None = None,
        hard_muted: bool | None = None,
        focus_suppressed: bool | None = None,
    ) -> bool:
        self._ensure_asr_runtime_state()
        try:
            generation = int(lease_generation)
        except (TypeError, ValueError):
            return False
        if generation <= self._voice_lease_generation:
            return False
        normalized_event = str(event or "").strip().lower()
        if normalized_event not in {
            "lease_sync",
            "hard_mute",
            "hard_unmute",
            "focus_suppress",
            "focus_resume",
            "game_takeover",
            "game_release",
        }:
            return False
        if normalized_event == "lease_sync":
            normalized_owner = str(owner or "").strip().lower()
            if normalized_owner not in {"none", "core", "game"}:
                return False
            if not isinstance(hard_muted, bool) or not isinstance(
                focus_suppressed,
                bool,
            ):
                return False
            next_owner = normalized_owner
            next_hard_muted = hard_muted
            next_focus_suppressed = focus_suppressed
        else:
            next_owner = self._voice_lease_owner
            next_hard_muted = self._voice_lease_hard_muted
            next_focus_suppressed = self._voice_lease_focus_suppressed
            if normalized_event == "hard_mute":
                next_hard_muted = True
            elif normalized_event == "hard_unmute":
                next_hard_muted = False
            elif normalized_event == "focus_suppress":
                next_focus_suppressed = True
            elif normalized_event == "focus_resume":
                next_focus_suppressed = False
            elif normalized_event == "game_takeover":
                next_owner = "game"
            elif normalized_event == "game_release":
                next_owner = "core"
        self._voice_lease_generation = generation
        self._voice_lease_synchronized = True
        await self._apply_voice_lease_state(
            owner=next_owner,
            hard_muted=next_hard_muted,
            focus_suppressed=next_focus_suppressed,
            reason=normalized_event,
            force_abort=True,
        )
        return True

    async def _prepare_core_voice_turn(self, token: VoiceTurnToken) -> bool:
        if not self._ingress_token_matches(token.ingress):
            return False
        if not self._voice_input_registry.begin_utterance(token):
            return False
        return await self._voice_input_registry.prepare_utterance()

    async def _prepare_core_chat_voice_turn(self, token: VoiceTurnToken) -> bool:
        if not self._ingress_token_matches(token.ingress):
            return False
        session_ref = self.session
        prepare = getattr(session_ref, "prepare_external_voice_turn", None)
        try:
            if callable(prepare):
                await prepare()
            else:
                interrupt = getattr(session_ref, "handle_interruption", None)
                if callable(interrupt):
                    await interrupt()
            if session_ref is not self.session:
                return False
            await self.handle_new_message()
            return self._ingress_token_matches(token.ingress)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "[%s] independent ASR turn preparation failed",
                self.lanlan_name,
            )
            return False

    async def _submit_core_voice_turn(
        self,
        text: str,
        *,
        turn_id: str,
    ) -> None:
        session_ref = self.session
        submit = getattr(session_ref, "submit_external_voice_turn", None)
        if callable(submit):
            await submit(text, turn_id=turn_id)
        else:
            await session_ref.create_response(text)

    async def _dispatch_core_asr_transcript(
        self,
        event: VoiceTranscriptEvent,
    ) -> None:
        token = event.turn_token.ingress
        if not self._ingress_token_matches(token):
            return
        await self._voice_input_registry.dispatch_final(event)

    async def _dispatch_core_chat_asr_transcript(
        self,
        event: VoiceTranscriptEvent,
    ) -> None:
        token = event.turn_token.ingress
        if not self._ingress_token_matches(token):
            return
        session_ref = self.session
        accepted = await self.handle_input_transcript(
            event.text,
            is_voice_source=True,
            source="independent_asr",
            metadata={"provider": event.provider},
        )
        if (
            not accepted
            or self.session is not session_ref
            or not self._ingress_token_matches(token)
        ):
            return
        await self._submit_core_voice_turn(
            event.text,
            turn_id=(f"asr-{token.session_epoch}-{event.turn_token.turn_id}"),
        )

    async def _send_core_asr_preview(self, event: VoicePartialEvent) -> None:
        await self._voice_input_registry.dispatch_partial(event)

    async def _send_core_chat_asr_preview(self, event: VoicePartialEvent) -> None:
        websocket = getattr(self, "websocket", None)
        send_json = getattr(websocket, "send_json", None)
        if not callable(send_json):
            return
        turn_id = str(
            getattr(self, "current_speech_id", None)
            or f"asr-preview-{event.session_epoch}"
        )
        await send_json(
            {
                "type": "user_transcript_preview",
                "text": event.text,
                "turn_id": turn_id,
            }
        )

    async def _cancel_core_chat_voice_turn(
        self,
        token: VoiceTurnToken,
        reason: str,
    ) -> None:
        del token, reason

    async def _handle_core_asr_failure(self, event: AsrFailureEvent) -> None:
        del event
        self._set_microphone_route("blocked")
        self._clear_audio_stream_queue("independent_asr_failure")
        self.hot_swap_audio_cache.clear()

    async def _send_core_asr_status(self, event: AsrStatusEvent) -> None:
        await self.send_status(
            json.dumps({"code": event.code, "details": {"provider": event.provider}})
        )

    async def _send_core_asr_lifecycle(
        self,
        event: AsrLifecycleNotification,
    ) -> None:
        await self.send_status(
            json.dumps(
                {
                    "code": "ASR_LIFECYCLE_STATE",
                    "details": {
                        "provider": event.provider,
                        "state": event.state,
                        "route_mode": self._asr_route_mode,
                    },
                }
            )
        )

    async def _wait_asr_transcript_dispatch_idle(self) -> None:
        await self._asr_runtime.wait_transcript_idle()

    async def close_voice_input_session(self) -> None:
        await self._voice_input_registry.close()
        await self._asr_runtime.close()
