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
from dataclasses import dataclass, field, replace
from typing import Literal

from websockets import exceptions as web_exceptions

from main_logic.asr_client.runtime import (
    AsrRuntimeCallbacks,
    AsrStartStatus,
    IndependentAsrRuntime,
)
from main_logic.voice_turn.contracts import (
    AsrFailureEvent,
    AsrLifecycleNotification,
    AsrStatusEvent,
    AsrSubmitStatus,
    VoicePartialEvent,
    VoiceIngressToken,
    VoiceTranscriptCallback,
    VoiceTranscriptEvent,
    VoiceTurnToken,
)
from main_logic.voice_turn.audio_input import (
    ProcessedVoiceFrame,
    VoiceInputAudioPipeline,
)
from main_logic import core as _core_facade

from ._shared import logger


@dataclass(frozen=True, slots=True)
class VoiceInputConsumerBinding:
    owner: Literal["game"]
    on_final: VoiceTranscriptCallback
    identity: object = field(default_factory=object, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class _QueuedMicFrame:
    message: dict
    duration_us: int
    source_rate_hz: int
    token: VoiceIngressToken
    received_at: float
    audio_stream_epoch: int = 0
    ingress_sequence: int = 0

    @classmethod
    def from_message(
        cls,
        message: dict,
        *,
        token: VoiceIngressToken,
        received_at: float | None = None,
        audio_stream_epoch: int = 0,
        ingress_sequence: int = 0,
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
            audio_stream_epoch=audio_stream_epoch,
            ingress_sequence=ingress_sequence,
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
    audio_stream_epoch: int = 0
    ingress_sequence: int = 0


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
        self._voice_lease_control_seen = False
        self._voice_input_transition_generation = 0
        self._voice_lease_owner = "none"
        self._voice_lease_hard_muted = False
        self._voice_lease_focus_suppressed = False
        self._voice_lease_requires_abort = False
        self._voice_input_suppressed = True
        self._voice_input_suppression_reasons: set[str] = {"owner_none"}
        self._voice_lease_resync_signal_state: tuple[str, int, bool, str] | None = (
            None
        )
        self._voice_input_consumer_bindings: dict[
            str,
            VoiceInputConsumerBinding,
        ] = {}
        self._audio_stream_queue = _AudioDurationQueue(
            capacity_us=2_000_000,
            max_frames=256,
        )
        self._audio_stream_worker_task: asyncio.Task | None = None
        self._audio_stream_dropped_total = 0
        self._audio_stream_epoch = 0
        self._last_audio_stream_backlog_log_time = 0.0
        self._last_hot_swap_rebind_drop_log_time = 0.0
        self.hot_swap_audio_cache = _HotSwapAudioBuffer(capacity_ms=8_000)
        self.hot_swap_cache_lock = asyncio.Lock()
        self.is_flushing_hot_swap_cache = False
        self._hot_swap_ingress_sequence = 0
        self._hot_swap_pending_sequences: set[int] = set()
        self._hot_swap_sequence_progress = asyncio.Event()
        self._hot_swap_sequence_progress.set()
        self._omni_mic_audio_bytes = 0
        self._asr_route_mode = "blocked"
        self._microphone_route_generation = 0
        self._asr_route_operation_generation = 0
        self._asr_notification_lock = asyncio.Lock()
        self._independent_asr_provider: str | None = None
        self._independent_asr_route_key: str | None = None
        self._voice_input_noise_reduction_enabled = True
        self._voice_input_audio_pipeline = VoiceInputAudioPipeline(
            nr_enabled=self._voice_input_noise_reduction_enabled,
        )
        self._voice_input_pipeline_failed = False
        callbacks = AsrRuntimeCallbacks(
            display_name=lambda: str(getattr(self, "lanlan_name", "core")),
            on_prepare_turn=self._prepare_core_voice_turn,
            on_partial=self._send_core_asr_preview,
            on_final=self._dispatch_core_asr_transcript,
            on_turn_abandoned=self._handle_core_asr_turn_abandoned,
            on_failure=self._handle_core_asr_failure,
            on_status=self._send_core_asr_status,
            on_lifecycle=self._send_core_asr_lifecycle,
        )
        self._asr_runtime = IndependentAsrRuntime(callbacks)

    def _ensure_asr_runtime_state(self) -> None:
        if not hasattr(self, "_asr_runtime"):
            self._init_asr_runtime_state()
        if not hasattr(self, "_asr_route_operation_generation"):
            self._asr_route_operation_generation = 0
        if not hasattr(self, "_asr_notification_lock"):
            self._asr_notification_lock = asyncio.Lock()
        if not hasattr(self, "_voice_input_transition_generation"):
            self._voice_input_transition_generation = 0
        if not hasattr(self, "_voice_lease_resync_signal_state"):
            self._voice_lease_resync_signal_state = None
        if not hasattr(self, "_voice_input_noise_reduction_enabled"):
            self._voice_input_noise_reduction_enabled = True
        if not hasattr(self, "_last_hot_swap_rebind_drop_log_time"):
            self._last_hot_swap_rebind_drop_log_time = 0.0

    def _begin_asr_route_operation(self) -> int:
        self._asr_route_operation_generation += 1
        return self._asr_route_operation_generation

    def _asr_route_operation_matches(self, operation_generation: int) -> bool:
        return operation_generation == self._asr_route_operation_generation

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

    def _capture_core_asr_operation_identity(self) -> tuple[object, ...]:
        return (
            self._asr_route_operation_generation,
            self._voice_input_transition_generation,
            self._voice_lease_connection_id,
            self._voice_lease_generation,
            self._voice_lease_owner,
            self._voice_lease_hard_muted,
            self._voice_lease_focus_suppressed,
            getattr(self, "session", None),
            self._capture_ingress_token(),
            self._asr_route_mode,
            str(getattr(self, "core_api_type", "") or "").strip().lower(),
            self._independent_asr_route_key,
            self._independent_asr_provider,
        )

    @staticmethod
    def _core_asr_identity_ingress_token(
        identity: tuple[object, ...],
    ) -> VoiceIngressToken:
        # The operation identity is a positional tuple; keep this a real
        # runtime check rather than an assert (asserts vanish under
        # ``python -O``).
        token = identity[8]
        if not isinstance(token, VoiceIngressToken):
            raise TypeError("CORE_ASR_IDENTITY_INGRESS_TOKEN_INVALID")
        return token

    def _core_asr_operation_identity_matches(
        self,
        identity: tuple[object, ...],
        *,
        include_runtime_identity: bool = True,
    ) -> bool:
        (
            route_operation_generation,
            voice_transition_generation,
            connection_id,
            lease_generation,
            owner,
            hard_muted,
            focus_suppressed,
            session_ref,
            ingress_token,
            route_mode,
            core_type,
            route_key,
            provider,
        ) = identity
        if (
            route_operation_generation != self._asr_route_operation_generation
            or voice_transition_generation != self._voice_input_transition_generation
            or connection_id != self._voice_lease_connection_id
            or lease_generation != self._voice_lease_generation
            or owner != self._voice_lease_owner
            or hard_muted != self._voice_lease_hard_muted
            or focus_suppressed != self._voice_lease_focus_suppressed
            or session_ref is not getattr(self, "session", None)
            or route_mode != self._asr_route_mode
            or core_type
            != str(getattr(self, "core_api_type", "") or "").strip().lower()
            or route_key != self._independent_asr_route_key
            or provider != self._independent_asr_provider
        ):
            return False
        return bool(
            not include_runtime_identity
            or ingress_token == self._capture_ingress_token()
        )

    def _ingress_token_matches(self, token: VoiceIngressToken) -> bool:
        return bool(
            token.connection_id == self._voice_lease_connection_id
            and token.lease_generation == self._voice_lease_generation
            and token.route_generation == self._microphone_route_generation
        )

    def _voice_input_accepts_pcm(self) -> bool:
        owner_has_target = self._voice_lease_owner == "core" or (
            self._voice_lease_owner == "game"
            and self._voice_input_consumer_bindings.get("game") is not None
        )
        return bool(
            self._voice_lease_synchronized
            and owner_has_target
            and not self._voice_lease_hard_muted
            and not self._voice_lease_focus_suppressed
            and not self._voice_input_suppressed
        )

    def bind_voice_input_consumer(
        self,
        owner: str,
        on_final: VoiceTranscriptCallback,
    ) -> VoiceInputConsumerBinding:
        self._ensure_asr_runtime_state()
        normalized_owner = str(owner or "").strip().lower()
        if normalized_owner != "game":
            raise ValueError("VOICE_INPUT_CONSUMER_OWNER_UNSUPPORTED")
        if not callable(on_final):
            raise TypeError("VOICE_INPUT_CONSUMER_CALLBACK_REQUIRED")
        if self._voice_lease_owner == normalized_owner:
            raise RuntimeError("VOICE_INPUT_CONSUMER_BIND_BEFORE_TAKEOVER")
        if normalized_owner in self._voice_input_consumer_bindings:
            raise RuntimeError("VOICE_INPUT_CONSUMER_ALREADY_BOUND")
        binding = VoiceInputConsumerBinding(owner="game", on_final=on_final)
        self._voice_input_consumer_bindings[normalized_owner] = binding
        return binding

    def unbind_voice_input_consumer(
        self,
        binding: VoiceInputConsumerBinding,
    ) -> bool:
        self._ensure_asr_runtime_state()
        if not isinstance(binding, VoiceInputConsumerBinding):
            return False
        if self._voice_lease_owner == binding.owner:
            raise RuntimeError("VOICE_INPUT_CONSUMER_RELEASE_LEASE_FIRST")
        if self._voice_input_consumer_bindings.get(binding.owner) is not binding:
            return False
        del self._voice_input_consumer_bindings[binding.owner]
        return True

    def _current_voice_input_consumer(self) -> VoiceInputConsumerBinding | None:
        if self._voice_lease_owner != "game":
            return None
        return self._voice_input_consumer_bindings.get("game")

    async def _start_independent_asr_if_enabled(
        self,
        input_mode: str,
        *,
        preserve_hot_swap_audio: bool = False,
    ) -> None:
        self._ensure_asr_runtime_state()
        operation_generation = self._begin_asr_route_operation()
        await self._close_independent_asr(
            next_route_mode="blocked",
            preserve_hot_swap_audio=preserve_hot_swap_audio,
            operation_generation=operation_generation,
        )
        if not self._asr_route_operation_matches(operation_generation):
            return
        self._omni_mic_audio_bytes = 0
        core_type = str(getattr(self, "core_api_type", "") or "").strip().lower()
        self._independent_asr_route_key = core_type
        session_epoch = self._capture_ingress_token().session_epoch
        start_connection_id = self._voice_lease_connection_id
        start_session_ref = getattr(self, "session", None)

        def route_operation_unclaimed() -> bool:
            # No competing route operation has run or completed: the route is
            # still the blocked placeholder this start installed.
            return bool(
                self._asr_route_operation_matches(operation_generation)
                and self._asr_route_mode == "blocked"
                and self._independent_asr_route_key == core_type
                and self._independent_asr_provider is None
                and str(getattr(self, "core_api_type", "") or "").strip().lower()
                == core_type
                and getattr(self, "session", None) is start_session_ref
            )

        def core_start_is_current() -> bool:
            # Route setup is fenced on competing route operations and on
            # websocket replacement. Lease state (owner/mute/focus) gates PCM
            # at ingress, not routing: the frontend flips owner to "core"
            # only after session_started, so a lease-state gate here would
            # permanently block every cold start.
            return bool(
                route_operation_unclaimed()
                and self._voice_lease_connection_id == start_connection_id
            )

        if input_mode != "audio":
            self._set_microphone_route("blocked")
            return
        try:
            settings = await _core_facade.aload_global_conversation_settings()
        except Exception:
            if not core_start_is_current():
                return
            await self._send_core_asr_status(
                AsrStatusEvent(
                    code="ASR_INDEPENDENT_FAILED",
                    provider=core_type or "unknown",
                    session_epoch=session_epoch,
                )
            )
            return
        if not core_start_is_current():
            return
        nr_enabled = settings.get("noiseReductionEnabled", True) is not False
        self._voice_input_noise_reduction_enabled = nr_enabled
        if self._voice_input_audio_pipeline.nr_enabled != nr_enabled:
            stale_pipeline = self._voice_input_audio_pipeline
            self._voice_input_audio_pipeline = VoiceInputAudioPipeline(
                nr_enabled=nr_enabled,
            )
            self._voice_input_pipeline_failed = False
            try:
                await stale_pipeline.close()
            except Exception:
                logger.warning(
                    "[%s] voice input audio pipeline close failed",
                    self.lanlan_name,
                )
            if not core_start_is_current():
                return
        enabled = bool(settings.get("independentAsrEnabled", False))
        optimization_value = settings.get(
            "voiceInputResourceOptimizationEnabled",
            True,
        )
        if not enabled:
            self._set_microphone_route("native")
            await self._send_core_asr_status(
                AsrStatusEvent(
                    code="ASR_INDEPENDENT_DISABLED",
                    provider=core_type or "unknown",
                    session_epoch=session_epoch,
                )
            )
            return
        result = await self._asr_runtime.start(
            route_key=core_type,
            resource_optimization_enabled=optimization_value is not False,
            # Session language follows the Core-tracked user language; the
            # asr_client factory maps it per provider and falls back to
            # automatic detection when it is unset or unsupported.
            user_language=getattr(self, "user_language", None),
        )
        current_epoch = self._capture_ingress_token().session_epoch
        if not core_start_is_current():
            route_fields_still_ours = bool(
                self._asr_route_operation_matches(operation_generation)
                and self._asr_route_mode == "blocked"
                and self._independent_asr_route_key == core_type
                and self._independent_asr_provider is None
            )
            if route_fields_still_ours:
                # Websocket replacement or session swap without a competing
                # route operation: nobody else owns the runtime, so close the
                # candidate and clear the route key so a later reconcile
                # retries.
                await self._abort_independent_asr("stale_core_start")
                # Re-check after the abort await: a competing start may have
                # installed its own blocked placeholder meanwhile, and
                # clearing that key would silently kill it before it even
                # reaches the native fallback.
                if (
                    self._asr_route_operation_matches(operation_generation)
                    and self._independent_asr_route_key == core_type
                ):
                    self._independent_asr_route_key = None
            return
        if result.failure_code == "ASR_START_STALE":
            # Runtime-level invalidation (e.g. a new websocket connection)
            # without a competing route operation: clear the route key so a
            # later reconcile retries instead of treating the route as done.
            self._independent_asr_route_key = None
            return
        if result.session_epoch != current_epoch:
            self._independent_asr_route_key = None
            return
        self._independent_asr_provider = result.provider
        if result.status is AsrStartStatus.READY:
            self._set_microphone_route("independent")
        else:
            self._set_microphone_route("blocked")

    def _abandon_core_voice_turn(
        self,
        turn_id: str | None = None,
        *,
        session_ref: object | None = None,
    ) -> None:
        target_session = (
            session_ref if session_ref is not None else getattr(self, "session", None)
        )
        abandon = getattr(target_session, "abandon_external_voice_turn", None)
        if not callable(abandon):
            return
        try:
            abandon(turn_id)
        except Exception:
            logger.warning(
                "[%s] external ASR dispatch pause release failed",
                self.lanlan_name,
            )

    async def _abort_independent_asr(self, reason: str) -> None:
        self._abandon_core_voice_turn()
        await self._asr_runtime.abort(reason)

    async def _suspend_independent_asr(self, reason: str) -> None:
        self._abandon_core_voice_turn()
        await self._asr_runtime.suspend(reason)

    async def _close_independent_asr(
        self,
        *,
        next_route_mode: Literal["blocked"],
        preserve_hot_swap_audio: bool = False,
        operation_generation: int | None = None,
    ) -> None:
        self._ensure_asr_runtime_state()
        if operation_generation is None:
            operation_generation = self._begin_asr_route_operation()
        elif not self._asr_route_operation_matches(operation_generation):
            return
        del next_route_mode
        provider = self._independent_asr_provider
        omni_audio_bytes = self._omni_mic_audio_bytes
        pipeline = self._voice_input_audio_pipeline
        self._set_microphone_route("blocked")
        if not preserve_hot_swap_audio:
            self._invalidate_voice_pcm_sync("independent_asr_close")
        self._voice_input_audio_pipeline = VoiceInputAudioPipeline(
            nr_enabled=self._voice_input_noise_reduction_enabled,
        )
        self._voice_input_pipeline_failed = False
        self._independent_asr_provider = None
        self._independent_asr_route_key = None
        self._abandon_core_voice_turn()
        await self._asr_runtime.close()
        try:
            await pipeline.close()
        except Exception:
            logger.warning(
                "[%s] voice input audio pipeline close failed",
                self.lanlan_name,
            )
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
            str(getattr(self, "input_mode", "audio") or "audio"),
            preserve_hot_swap_audio=True,
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
                frame = self._audio_stream_queue.get_nowait()
                self._audio_stream_queue.task_done()
                self._complete_hot_swap_ingress_sequence(frame.ingress_sequence)
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

    async def _maybe_signal_voice_lease_resync(self) -> None:
        """Nudge a client whose PCM is dropped only because no lease is set.

        Deliberate suppression (hard mute, focus suppression, game owner)
        must stay silent; only an unsynchronized lease or an installed
        ``none`` owner means the sender lost a lease it still believes it
        holds. One signal per connection and lease state keeps the channel
        quiet while every later lease change re-arms it.
        """

        if (
            self._voice_lease_hard_muted
            or self._voice_lease_focus_suppressed
            or self._voice_lease_owner == "game"
        ):
            return
        if self._voice_lease_synchronized and self._voice_lease_owner != "none":
            return
        signal_state = (
            self._voice_lease_connection_id,
            self._voice_lease_generation,
            self._voice_lease_synchronized,
            self._voice_lease_owner,
        )
        if signal_state == self._voice_lease_resync_signal_state:
            return
        self._voice_lease_resync_signal_state = signal_state
        await self.send_status(
            json.dumps(
                {
                    "code": "VOICE_INPUT_LEASE_RESYNC_REQUIRED",
                    "details": {
                        "reason": (
                            "lease_unsynchronized"
                            if not self._voice_lease_synchronized
                            else "owner_none"
                        ),
                    },
                }
            )
        )

    async def _enqueue_audio_stream_data(self, message: dict) -> None:
        self._ensure_asr_runtime_state()
        if not self._voice_input_accepts_pcm():
            await self._maybe_signal_voice_lease_resync()
            return
        token = self._capture_ingress_token()
        ingress_sequence = self._reserve_hot_swap_ingress_sequence()
        sequence_owned = True
        try:
            frame = _QueuedMicFrame.from_message(
                message,
                token=token,
                audio_stream_epoch=self._audio_stream_epoch,
                ingress_sequence=ingress_sequence,
            )
        except ValueError:
            self._complete_hot_swap_ingress_sequence(ingress_sequence)
            logger.warning("[%s] invalid microphone ingress frame", self.lanlan_name)
            return
        self._ensure_audio_stream_worker()
        try:
            self._audio_stream_queue.put_nowait(frame)
            sequence_owned = False
        except asyncio.QueueFull:
            try:
                await asyncio.sleep(0)
                if not self._ingress_token_matches(frame.token):
                    rebound = self._rebind_hot_swap_ingress_token(
                        frame.token,
                        audio_stream_epoch=frame.audio_stream_epoch,
                    )
                    if rebound is None:
                        return
                    frame = replace(frame, token=rebound)
                try:
                    self._audio_stream_queue.put_nowait(frame)
                    sequence_owned = False
                except asyncio.QueueFull:
                    self._clear_audio_stream_queue("ingress_backpressure")
                    self._audio_stream_dropped_total += 1
                    # Keep the slow provider teardown off the websocket
                    # receive path: run the abort as a tracked task and yield
                    # once so its synchronous prefix (the generation bumps and
                    # lifecycle invalidation ``IndependentAsrRuntime.abort``
                    # performs before its first await) still executes before
                    # this coroutine resumes and any later frame is accepted.
                    self._fire_task(
                        self._abort_independent_asr("ingress_backpressure")
                    )
                    await asyncio.sleep(0)
                    return
            finally:
                if sequence_owned:
                    self._complete_hot_swap_ingress_sequence(ingress_sequence)
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
                token = frame.token
                if not self._ingress_token_matches(token):
                    rebound = self._rebind_hot_swap_ingress_token(
                        token,
                        audio_stream_epoch=frame.audio_stream_epoch,
                    )
                    if rebound is None:
                        self._audio_stream_dropped_total += 1
                        continue
                    token = rebound
                await self._process_microphone_stream_data(
                    frame.message,
                    ingress_token=token,
                    audio_stream_epoch=frame.audio_stream_epoch,
                    ingress_sequence=frame.ingress_sequence,
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
                self._complete_hot_swap_ingress_sequence(frame.ingress_sequence)

    def _reserve_hot_swap_ingress_sequence(self) -> int:
        self._hot_swap_ingress_sequence += 1
        sequence = self._hot_swap_ingress_sequence
        self._hot_swap_pending_sequences.add(sequence)
        self._hot_swap_sequence_progress.clear()
        return sequence

    def _complete_hot_swap_ingress_sequence(self, sequence: int) -> None:
        if sequence <= 0:
            return
        self._hot_swap_pending_sequences.discard(sequence)
        self._hot_swap_sequence_progress.set()

    def _hot_swap_cutoff_complete(self, cutoff: int) -> bool:
        return not any(
            sequence <= cutoff for sequence in self._hot_swap_pending_sequences
        )

    def _rebind_hot_swap_ingress_token(
        self,
        token: VoiceIngressToken,
        *,
        audio_stream_epoch: int,
    ) -> VoiceIngressToken | None:
        if not (self.is_hot_swap_imminent or self.is_flushing_hot_swap_cache):
            return None
        current = self._capture_ingress_token()
        if (
            token.session_epoch != current.session_epoch
            or token.audio_generation != current.audio_generation
            or audio_stream_epoch != self._audio_stream_epoch
            or token.connection_id != current.connection_id
            or token.lease_generation != current.lease_generation
            or token.route_generation == current.route_generation
            or self._voice_lease_owner != "core"
            or not self._voice_input_accepts_pcm()
        ):
            return None
        return current

    async def _fail_voice_input_pipeline(
        self,
        *,
        ingress_token: VoiceIngressToken,
        session_ref: object,
        audio_epoch: int,
        pipeline_ref: VoiceInputAudioPipeline,
    ) -> None:
        if (
            self._voice_input_pipeline_failed
            or ingress_token != self._capture_ingress_token()
            or self.session is not session_ref
            or self._audio_stream_epoch != audio_epoch
            or self._voice_input_audio_pipeline is not pipeline_ref
            or not self.is_active
        ):
            return
        source_session_epoch = ingress_token.session_epoch
        source_connection_id = ingress_token.connection_id
        source_lease_generation = ingress_token.lease_generation
        voice_transition_generation = self._voice_input_transition_generation
        route_operation_generation = self._asr_route_operation_generation
        source_session_ref = session_ref
        source_audio_epoch = audio_epoch
        source_pipeline_ref = pipeline_ref
        source_route_mode = self._asr_route_mode
        self._voice_input_pipeline_failed = True
        independent_route = source_route_mode == "independent"
        source_provider = (
            self._independent_asr_provider
            or self._independent_asr_route_key
            or "unknown"
        )
        self._set_microphone_route("blocked")
        self._clear_audio_stream_queue("audio_preprocessing_failed")
        self.hot_swap_audio_cache.clear()
        if independent_route:
            await self._abort_independent_asr("audio_preprocessing_failed")
        if (
            not self._voice_input_pipeline_failed
            or self._voice_lease_connection_id != source_connection_id
            or self._voice_lease_generation != source_lease_generation
            or (self._voice_input_transition_generation != voice_transition_generation)
            or self._asr_route_operation_generation != route_operation_generation
            or self._capture_ingress_token().session_epoch != source_session_epoch
            or self.session is not source_session_ref
            or self._audio_stream_epoch != source_audio_epoch
            or self._voice_input_audio_pipeline is not source_pipeline_ref
            or not self.is_active
            or self._asr_route_mode != "blocked"
            or (
                self._independent_asr_provider
                or self._independent_asr_route_key
                or "unknown"
            )
            != source_provider
        ):
            return
        await self._send_core_asr_status(
            AsrStatusEvent(
                code="ASR_AUDIO_PREPROCESSING_FAILED",
                provider=source_provider,
                session_epoch=source_session_epoch,
            )
        )

    async def _process_microphone_stream_data(
        self,
        message: dict,
        *,
        ingress_token: VoiceIngressToken,
        audio_stream_epoch: int | None = None,
        ingress_sequence: int | None = None,
    ) -> None:
        sequence_owned = ingress_sequence is None
        if ingress_sequence is None:
            ingress_sequence = self._reserve_hot_swap_ingress_sequence()
        if audio_stream_epoch is None:
            audio_stream_epoch = self._audio_stream_epoch
        if self._voice_input_pipeline_failed:
            if sequence_owned:
                self._complete_hot_swap_ingress_sequence(ingress_sequence)
            return
        if not self._ingress_token_matches(ingress_token):
            rebound = self._rebind_hot_swap_ingress_token(
                ingress_token,
                audio_stream_epoch=audio_stream_epoch,
            )
            if rebound is None:
                if sequence_owned:
                    self._complete_hot_swap_ingress_sequence(ingress_sequence)
                return
            ingress_token = rebound
        data = message.get("data")
        session_ref = self.session
        audio_epoch = audio_stream_epoch
        pipeline_ref = self._voice_input_audio_pipeline
        voice_owner = self._voice_lease_owner
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
            try:
                processed_frame = await pipeline_ref.process(
                    audio_bytes,
                    sample_rate_hz=source_rate_hz,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._fail_voice_input_pipeline(
                    ingress_token=ingress_token,
                    session_ref=session_ref,
                    audio_epoch=audio_epoch,
                    pipeline_ref=pipeline_ref,
                )
                return
            if not processed_frame.pcm16:
                return
            if (
                not self.is_active
                or self._audio_stream_epoch != audio_epoch
                or self._voice_lease_owner != voice_owner
                or not self._voice_input_accepts_pcm()
            ):
                return
            if not self._ingress_token_matches(ingress_token):
                rebound = self._rebind_hot_swap_ingress_token(
                    ingress_token,
                    audio_stream_epoch=audio_epoch,
                )
                if rebound is None:
                    return
                ingress_token = rebound
            refs_changed = (
                self.session is not session_ref
                or self._voice_input_audio_pipeline is not pipeline_ref
            )
            cache_for_hot_swap = False
            async with self.hot_swap_cache_lock:
                hot_swap_barrier = (
                    self.is_hot_swap_imminent or self.is_flushing_hot_swap_cache
                )
                if refs_changed and not hot_swap_barrier:
                    return
                if hot_swap_barrier:
                    cache_for_hot_swap = True
                    accepted = self.hot_swap_audio_cache.append(
                        _HotSwapAudioFrame(
                            pcm16=processed_frame.pcm16,
                            token=ingress_token,
                            speech_probability=processed_frame.speech_probability,
                            rnnoise_available=processed_frame.rnnoise_available,
                            audio_stream_epoch=audio_epoch,
                            ingress_sequence=ingress_sequence,
                        )
                    )
            if cache_for_hot_swap:
                if not accepted:
                    await self._abort_independent_asr("ingress_backpressure")
                return
            if not self._ingress_token_matches(ingress_token):
                return
            await self._route_microphone_audio(
                processed_frame.pcm16,
                sample_rate_hz=processed_frame.sample_rate_hz,
                speech_probability=processed_frame.speech_probability,
                rnnoise_available=processed_frame.rnnoise_available,
                ingress_token=ingress_token,
            )
        except struct.error:
            logger.error("Microphone input rejected: invalid PCM samples")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Microphone preprocessing or ASR routing failed")
        finally:
            if sequence_owned:
                self._complete_hot_swap_ingress_sequence(ingress_sequence)

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
            if getattr(self, "session_closed_by_server", False):
                return True
            token = ingress_token or self._capture_native_ingress_token()
            session_ref = self.session

            def native_send_is_current() -> bool:
                return bool(
                    self.session is session_ref
                    and self._asr_route_mode == "native"
                    and token == self._capture_native_ingress_token()
                    and self._voice_lease_owner == "core"
                    and self._voice_input_accepts_pcm()
                )

            if not native_send_is_current():
                return True
            stream_audio = getattr(session_ref, "stream_audio", None)
            if not callable(stream_audio):
                return True
            if getattr(session_ref, "_fatal_error_occurred", False):
                # After an Omni fatal error (1011 / response timeout) the
                # session is doomed; stop feeding it microphone PCM, with
                # rate-limited logging (parity with the legacy streaming.py
                # audio-branch guard).
                now = time.monotonic()
                if now - getattr(
                    self, "last_audio_send_error_time", 0.0
                ) > getattr(self, "audio_error_log_interval", 2.0):
                    logger.warning(
                        "[%s] Omni session fatal error, skipping microphone audio",
                        self.lanlan_name,
                    )
                    self.last_audio_send_error_time = now
                return True
            try:
                await stream_audio(pcm16)
                if not native_send_is_current():
                    return True
                self._record_omni_microphone_audio(len(pcm16))
            except asyncio.CancelledError:
                raise
            except web_exceptions.ConnectionClosedOK:
                if not native_send_is_current():
                    return True
                self.session_closed_by_server = True
            except (web_exceptions.ConnectionClosed, AttributeError) as exc:
                if not native_send_is_current():
                    return True
                self.session_closed_by_server = True
                now = time.monotonic()
                if now - getattr(self, "last_audio_send_error_time", 0.0) > getattr(
                    self, "audio_error_log_interval", 2.0
                ):
                    logger.warning(
                        "[%s] Omni native microphone connection closed: %s",
                        self.lanlan_name,
                        exc,
                    )
                    self.last_audio_send_error_time = now
            except Exception as exc:
                if not native_send_is_current():
                    return True
                message = str(exc).lower()
                if "no close frame" in message or "connection closed" in message:
                    self.session_closed_by_server = True
                now = time.monotonic()
                if now - getattr(self, "last_audio_send_error_time", 0.0) > getattr(
                    self, "audio_error_log_interval", 2.0
                ):
                    logger.error(
                        "[%s] Omni native microphone routing failed: %s",
                        self.lanlan_name,
                        exc,
                    )
                    self.last_audio_send_error_time = now
            return True
        if route_mode != "independent":
            self._set_microphone_route("blocked")
            return True
        token = ingress_token or self._capture_ingress_token()
        if not self._ingress_token_matches(token):
            return True
        route_mode = self._asr_route_mode
        voice_transition_generation = self._voice_input_transition_generation
        route_operation_generation = self._asr_route_operation_generation
        provider = self._independent_asr_provider
        owner = self._voice_lease_owner
        result = await self._asr_runtime.submit(
            ProcessedVoiceFrame(
                pcm16=pcm16,
                sample_rate_hz=sample_rate_hz,
                speech_probability=speech_probability,
                rnnoise_available=bool(rnnoise_available),
            ),
            ingress_token=token,
        )
        submit_is_current = bool(
            token == self._capture_ingress_token()
            and route_mode == "independent"
            and self._asr_route_mode == "independent"
            and (route_operation_generation == self._asr_route_operation_generation)
            and (voice_transition_generation == self._voice_input_transition_generation)
            and owner == self._voice_lease_owner
            and self._voice_input_accepts_pcm()
            and self._independent_asr_provider == provider
        )
        if not submit_is_current:
            return True
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
            cutoff = self._hot_swap_ingress_sequence
        try:
            while True:
                if self._hot_swap_cutoff_complete(cutoff):
                    break
                self._hot_swap_sequence_progress.clear()
                if self._hot_swap_cutoff_complete(cutoff):
                    continue
                await self._hot_swap_sequence_progress.wait()
            if not self.session or not self.is_active:
                async with self.hot_swap_cache_lock:
                    damaged_frames.extend(self.hot_swap_audio_cache.drain())
                return
            # Native replay throttle: coalesce up to five 10 ms frames per
            # send and sleep 25 ms between sends (~2x real time), matching
            # the pre-independent-route flush pacing (legacy streaming.py:
            # 320-byte chunks x5 at 0.025 s). The independent route keeps
            # per-frame submits so detector metadata stays frame-accurate.
            native_batch_frames = 5
            send_interval_s = 0.025

            async def replay_frames(
                audio_frames: tuple[_HotSwapAudioFrame, ...],
                *,
                paced: bool,
            ) -> bool:
                """Replay drained frames; ``False`` means a send failed."""
                index = 0
                while index < len(audio_frames):
                    frame = audio_frames[index]
                    token = frame.token
                    if not self._ingress_token_matches(token):
                        rebound = self._rebind_hot_swap_ingress_token(
                            token,
                            audio_stream_epoch=frame.audio_stream_epoch,
                        )
                        if rebound is None:
                            self._audio_stream_dropped_total += 1
                            now = time.time()
                            if (
                                now - self._last_hot_swap_rebind_drop_log_time
                                >= 2.0
                            ):
                                self._last_hot_swap_rebind_drop_log_time = now
                                logger.warning(
                                    "[%s] hot swap replay dropped stale "
                                    "frame total_dropped=%d",
                                    self.lanlan_name,
                                    self._audio_stream_dropped_total,
                                )
                            index += 1
                            continue
                        token = rebound
                    batch_end = index + 1
                    if self._asr_route_mode == "native":
                        while (
                            batch_end < len(audio_frames)
                            and batch_end - index < native_batch_frames
                            and audio_frames[batch_end].token == frame.token
                            and audio_frames[batch_end].audio_stream_epoch
                            == frame.audio_stream_epoch
                        ):
                            batch_end += 1
                    try:
                        await self._route_microphone_audio(
                            b"".join(
                                item.pcm16
                                for item in audio_frames[index:batch_end]
                            ),
                            sample_rate_hz=16_000,
                            speech_probability=frame.speech_probability,
                            rnnoise_available=frame.rnnoise_available,
                            ingress_token=token,
                        )
                    except asyncio.CancelledError:
                        damaged_frames.extend(audio_frames[index:])
                        raise
                    except Exception:
                        damaged_frames.extend(audio_frames[index:])
                        return False
                    index = batch_end
                    if paced and self._asr_route_mode == "native":
                        try:
                            await asyncio.sleep(send_interval_s)
                        except asyncio.CancelledError:
                            damaged_frames.extend(audio_frames[index:])
                            raise
                return True

            # Termination contract: live frames keep landing in the cache
            # while the flush runs, so a paced native pass can never drain
            # to empty on its own -- at ~2x real time each pass roughly
            # halves the backlog until per-batch pacing overhead dominates
            # (one <=5-frame batch costs 25 ms while ~2.5 frames arrive)
            # and the drain settles at a few-frame steady state. That
            # healthy tail is replayed unpaced while holding the cache
            # lock, so the flush barrier drops atomically and the next
            # live frame routes directly instead of being damaged. The
            # wall-clock deadline (2x the initial-backlog replay estimate
            # plus fixed slack) therefore only trips when replay cannot
            # outpace ingress -- genuine backpressure -- and the residue
            # then invalidates the candidate turn below.
            tail_handoff_frames = 25  # <=250 ms residue: burst and hand off
            flush_deadline = (
                time.monotonic()
                + 2.0 * self.hot_swap_audio_cache.duration_ms / 1_000.0
                + 3.0
            )
            while True:
                async with self.hot_swap_cache_lock:
                    audio_frames = self.hot_swap_audio_cache.drain()
                    if len(audio_frames) <= tail_handoff_frames:
                        if audio_frames and not await replay_frames(
                            audio_frames,
                            paced=False,
                        ):
                            return
                        self.is_flushing_hot_swap_cache = False
                        self.is_hot_swap_imminent = False
                        flush_complete = True
                        return
                if time.monotonic() >= flush_deadline:
                    damaged_frames.extend(audio_frames)
                    return
                if not await replay_frames(audio_frames, paced=True):
                    return
        finally:
            async with self.hot_swap_cache_lock:
                if not flush_complete:
                    damaged_frames.extend(self.hot_swap_audio_cache.drain())
                    self.is_flushing_hot_swap_cache = False
                    self.is_hot_swap_imminent = False
            # One abort invalidates the whole candidate turn, however many
            # damaged tokens remain current.
            if any(
                self._ingress_token_matches(frame.token)
                for frame in damaged_frames
            ):
                await self._abort_independent_asr("ingress_backpressure")

    def _invalidate_voice_pcm_sync(self, reason: str) -> None:
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
        self._ensure_asr_runtime_state()
        self._voice_input_transition_generation += 1
        previous = (
            self._voice_lease_owner,
            self._voice_lease_hard_muted,
            self._voice_lease_focus_suppressed,
        )
        self._voice_lease_owner = owner
        self._voice_lease_hard_muted = hard_muted
        self._voice_lease_focus_suppressed = focus_suppressed
        reasons: set[str] = set()
        if owner == "none":
            reasons.add("owner_none")
        elif owner == "game" and self._current_voice_input_consumer() is None:
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
        if reason == "game_takeover" or (
            owner == "game" and self._current_voice_input_consumer() is None
        ):
            await self._suspend_independent_asr(reason)
        elif reason == "game_release":
            if should_abort:
                route_operation_snapshot = self._asr_route_operation_generation
                await self._abort_independent_asr(reason)
                if (
                    self._asr_route_operation_generation != route_operation_snapshot
                    or self._voice_lease_owner != "core"
                ):
                    return
            if self._voice_lease_owner != "core":
                return
            # Resume the lifecycle even while hard-muted or focus-suppressed:
            # those states gate PCM at ingress, and no later unmute path calls
            # resume, so skipping here would leave the runtime SUSPENDED for
            # the rest of the session.
            await self._asr_runtime.resume(reason)
        elif should_abort:
            await self._abort_independent_asr(reason)

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
        invalidate_start = getattr(self._asr_runtime, "_invalidate_asr_start", None)
        if callable(invalidate_start):
            invalidate_start()
        self._voice_lease_connection_id = normalized
        self._voice_lease_generation = -1
        self._voice_lease_synchronized = False
        self._voice_lease_control_seen = False
        self._voice_lease_owner = "none"
        self._voice_lease_hard_muted = False
        self._voice_lease_focus_suppressed = False
        self._voice_input_suppression_reasons = {"owner_none"}
        self._voice_input_suppressed = True
        self._voice_lease_requires_abort = True
        self._invalidate_voice_pcm_sync("websocket_reconnect")
        return True

    async def _ensure_voice_input_session_authorized(
        self,
        connection_id: str,
    ) -> bool:
        """Authorize one legacy ordinary-audio session without weakening MicLease."""

        self._ensure_asr_runtime_state()
        normalized = str(connection_id or "").strip()
        if not normalized or normalized != self._voice_lease_connection_id:
            return False
        if self._voice_lease_synchronized:
            return True
        if self._voice_lease_control_seen:
            return False

        self._voice_lease_generation = 0
        self._voice_lease_synchronized = True
        await self._apply_voice_lease_state(
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
            reason="legacy_session_start",
            force_abort=False,
        )
        return bool(
            self._voice_lease_connection_id == normalized
            and self._voice_lease_generation == 0
            and self._voice_lease_synchronized
            and not self._voice_lease_control_seen
            and self._voice_lease_owner == "core"
            and not self._voice_lease_hard_muted
            and not self._voice_lease_focus_suppressed
        )

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
        self._voice_lease_control_seen = True
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

    async def _handle_core_asr_turn_abandoned(self, token: VoiceTurnToken) -> None:
        external_turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
        self._abandon_core_voice_turn(external_turn_id)

    async def _prepare_core_voice_turn(self, token: VoiceTurnToken) -> bool:
        if not self._ingress_token_matches(token.ingress):
            return False
        if self._voice_lease_owner == "game":
            return self._current_voice_input_consumer() is not None
        if self._voice_lease_owner != "core":
            return False
        session_ref = self.session
        transition_generation = self._voice_input_transition_generation
        external_turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"

        def operation_is_current() -> bool:
            return bool(
                transition_generation == self._voice_input_transition_generation
                and self._voice_lease_owner == "core"
                and session_ref is self.session
                and self._ingress_token_matches(token.ingress)
            )

        prepare = getattr(session_ref, "prepare_external_voice_turn", None)
        try:
            if callable(prepare):
                await prepare(turn_id=external_turn_id)
            else:
                interrupt = getattr(session_ref, "handle_interruption", None)
                if callable(interrupt):
                    await interrupt()
            if not operation_is_current():
                self._abandon_core_voice_turn(
                    external_turn_id,
                    session_ref=session_ref,
                )
                return False
            await self.handle_new_message()
            if operation_is_current():
                return True
            self._abandon_core_voice_turn(
                external_turn_id,
                session_ref=session_ref,
            )
            return False
        except asyncio.CancelledError:
            self._abandon_core_voice_turn(
                external_turn_id,
                session_ref=session_ref,
            )
            raise
        except Exception:
            self._abandon_core_voice_turn(
                external_turn_id,
                session_ref=session_ref,
            )
            if not operation_is_current():
                return False
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
        binding = self._current_voice_input_consumer()
        if binding is not None:
            if self._voice_input_consumer_bindings.get(binding.owner) is not binding:
                return
            if not event.text.strip():
                return
            await binding.on_final(event)
            return
        if self._voice_lease_owner != "core":
            return
        session_ref = self.session
        transition_generation = self._voice_input_transition_generation
        external_turn_id = f"asr-{token.session_epoch}-{event.turn_token.turn_id}"
        try:
            if not event.text.strip():
                return
            accepted = await self.handle_input_transcript(
                event.text,
                is_voice_source=True,
                source="independent_asr",
                metadata={"provider": event.provider},
            )
            if (
                not accepted
                or self.session is not session_ref
                or transition_generation != self._voice_input_transition_generation
                or self._voice_lease_owner != "core"
                or not self._ingress_token_matches(token)
            ):
                return
            await self._submit_core_voice_turn(
                event.text,
                turn_id=external_turn_id,
            )
        finally:
            self._abandon_core_voice_turn(
                external_turn_id,
                session_ref=session_ref,
            )

    async def _send_core_asr_preview(self, event: VoicePartialEvent) -> None:
        if (
            event.session_epoch != self._capture_ingress_token().session_epoch
            or self._voice_lease_owner != "core"
            or self._asr_route_mode != "independent"
            or not self._voice_input_accepts_pcm()
        ):
            return
        websocket_ref = getattr(self, "websocket", None)
        send_json = getattr(websocket_ref, "send_json", None)
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

    async def _handle_core_asr_failure(self, event: AsrFailureEvent) -> None:
        source_identity = self._capture_core_asr_operation_identity()
        async with self._asr_notification_lock:
            if (
                not self._core_asr_operation_identity_matches(source_identity)
                or event.session_epoch
                != self._core_asr_identity_ingress_token(source_identity).session_epoch
            ):
                return
            self._abandon_core_voice_turn()
            self._set_microphone_route("blocked")
            self._clear_audio_stream_queue("independent_asr_failure")
            self.hot_swap_audio_cache.clear()

    async def _send_core_asr_status(self, event: AsrStatusEvent) -> None:
        source_identity = self._capture_core_asr_operation_identity()
        async with self._asr_notification_lock:
            if (
                not self._core_asr_operation_identity_matches(source_identity)
                or event.session_epoch
                != self._core_asr_identity_ingress_token(source_identity).session_epoch
            ):
                return
            await self.send_status(
                json.dumps(
                    {
                        "code": event.code,
                        "details": {
                            "provider": event.provider,
                            "session_epoch": event.session_epoch,
                        },
                    }
                )
            )

    async def _send_core_asr_lifecycle(
        self,
        event: AsrLifecycleNotification,
    ) -> None:
        source_identity = self._capture_core_asr_operation_identity()
        async with self._asr_notification_lock:
            if (
                not self._core_asr_operation_identity_matches(source_identity)
                or event.session_epoch
                != self._core_asr_identity_ingress_token(source_identity).session_epoch
            ):
                return
            await self.send_status(
                json.dumps(
                    {
                        "code": "ASR_LIFECYCLE_STATE",
                        "details": {
                            "provider": event.provider,
                            "state": event.state,
                            "route_mode": self._asr_route_mode,
                            "session_epoch": event.session_epoch,
                        },
                    }
                )
            )

    async def _wait_asr_transcript_dispatch_idle(self) -> None:
        await self._asr_runtime.wait_transcript_idle()
