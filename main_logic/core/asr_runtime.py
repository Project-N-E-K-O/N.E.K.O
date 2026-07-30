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
from dataclasses import dataclass, replace
from typing import Callable, ClassVar, Literal

from websockets import exceptions as web_exceptions

from main_logic.asr_client.runtime import (
    AsrRuntimeCallbacks,
    AsrStartStatus,
    IndependentAsrRuntime,
)
from main_logic.voice_input import (
    BuiltinVoiceInputConsumer,
    VoiceInputConsumerCapabilities,
    VoiceInputDispatchResult,
    VoiceInputRegistry,
)
from main_logic.voice_input.consumers import (
    CoreChatTurnContext,
    CoreChatVoiceInputConsumer,
    GameVoiceInputConsumer,
)
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

from ._shared import logger

@dataclass(frozen=True, slots=True)
class _QueuedMicFrame:
    # Longest microphone PCM frame accepted at ingress. Bounded by DURATION,
    # not by a fixed sample count: the binary wire decoder derives its limit
    # per sample rate (_VOICE_BINARY_MAX_DURATION_MS in
    # main_routers/websocket_router.py), so a fixed count would reject at
    # 120 ms for 48 kHz but only at 360 ms for 16 kHz and the two ingress paths
    # would disagree (CodeRabbit). ClassVar, not a module constant: the
    # layering gate keeps this mixin module to imports and classes only.
    MAX_DURATION_MS: ClassVar[int] = 120

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
        # Per-frame bound, matching the binary wire cap in websocket_router
        # (_VOICE_BINARY_MAX_DURATION_MS). The queue's own 2 s / 256-frame
        # limits are post-decode, so without this a single JSON stream_data
        # frame could carry an arbitrarily long sample list -- the frontend
        # never sends JSON audio, so any oversized one is malformed by
        # construction.
        if len(samples) * 1_000 > source_rate_hz * cls.MAX_DURATION_MS:
            raise ValueError("MIC_PCM_FRAME_TOO_LONG")
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
        # Socket holding the voice lease; see _set_voice_input_websocket.
        self._voice_input_websocket = None
        self._voice_lease_resync_suppressed = False
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
        self._independent_asr_handshake_override: bool | None = None
        self._voice_input_resource_optimization_handshake_override: bool | None = None
        self._voice_input_resource_optimization_session_value: bool | None = None
        self._voice_input_noise_reduction_enabled = True
        self._voice_input_audio_pipeline = VoiceInputAudioPipeline(
            nr_enabled=self._voice_input_noise_reduction_enabled,
        )
        self._voice_input_pipeline_failed = False
        self._blocked_text_mode_microphone_signalled = False
        # Identity of the independent-ASR turn that owns the frontend's
        # singleton preview bubble, plus its last rendered text. Both are
        # stamped/refreshed from the ordered partial stream so a late final
        # can tell "my own bubble" from "the next turn already took it over".
        self._core_asr_preview_turn_id = ""
        self._core_asr_preview_text = ""
        self._core_asr_preview_turn_token: VoiceTurnToken | None = None
        self._init_voice_input_registry()
        callbacks = AsrRuntimeCallbacks(
            display_name=lambda: str(getattr(self, "lanlan_name", "core")),
            on_prepare_turn=self._prepare_voice_input_turn,
            on_partial=self._dispatch_voice_input_partial,
            on_final=self._dispatch_voice_input_final,
            on_turn_abandoned=self._handle_core_asr_turn_abandoned,
            on_failure=self._handle_core_asr_failure,
            on_status=self._send_core_asr_status,
            on_lifecycle=self._send_core_asr_lifecycle,
        )
        self._asr_runtime = IndependentAsrRuntime(callbacks)

    def _init_voice_input_registry(self) -> None:
        """Install the manager-lifetime built-ins exactly once."""

        if hasattr(self, "_voice_input_registry"):
            return
        registry = VoiceInputRegistry()
        core_chat = CoreChatVoiceInputConsumer(
            session_ref=lambda: getattr(self, "session", None),
            on_prepare=lambda token, context: self._prepare_core_voice_turn(
                token,
                session_ref=context.session_ref,
                abandon_on_failure=False,
            ),
            on_partial_event=self._send_core_asr_preview,
            on_final_event=lambda event, context: self._dispatch_core_asr_transcript(
                event,
                session_ref=context.session_ref,
            ),
            on_cancelled_event=self._cancel_core_chat_voice_turn,
        )
        game = GameVoiceInputConsumer(
            lanlan_name=lambda: str(getattr(self, "lanlan_name", "core")),
        )
        core_registration = registry.register_builtin(
            BuiltinVoiceInputConsumer.CORE_CHAT,
            core_chat,
            capabilities=VoiceInputConsumerCapabilities(
                accepts_partial=True,
                accepts_final=True,
            ),
        )
        game_registration = registry.register_builtin(
            BuiltinVoiceInputConsumer.GAME,
            game,
            capabilities=VoiceInputConsumerCapabilities(
                accepts_partial=False,
                accepts_final=True,
            ),
        )
        registry.activate(core_registration.handle)
        self._voice_input_registry = registry
        self._core_chat_voice_input_registration = core_registration
        self._game_voice_input_registration = game_registration

    def _ensure_asr_runtime_state(self) -> None:
        if not hasattr(self, "_asr_runtime"):
            self._init_asr_runtime_state()
        self._init_voice_input_registry()
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
        if not hasattr(self, "_independent_asr_handshake_override"):
            self._independent_asr_handshake_override = None
        if not hasattr(
            self,
            "_voice_input_resource_optimization_handshake_override",
        ):
            self._voice_input_resource_optimization_handshake_override = None
        if not hasattr(
            self,
            "_voice_input_resource_optimization_session_value",
        ):
            self._voice_input_resource_optimization_session_value = None
        if not hasattr(self, "_core_asr_preview_turn_id"):
            self._core_asr_preview_turn_id = ""
        if not hasattr(self, "_core_asr_preview_text"):
            self._core_asr_preview_text = ""
        if not hasattr(self, "_core_asr_preview_turn_token"):
            self._core_asr_preview_turn_token = None
        if not hasattr(self, "_blocked_text_mode_microphone_signalled"):
            self._blocked_text_mode_microphone_signalled = False
        if not hasattr(self, "_voice_input_websocket"):
            self._voice_input_websocket = None
        if not hasattr(self, "_voice_lease_resync_suppressed"):
            self._voice_lease_resync_suppressed = False

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
        if mode != "blocked":
            # Re-arm the one-shot text-mode notice for the next episode, and
            # the lease-resync signal now that a live route exists again.
            self._blocked_text_mode_microphone_signalled = False
            self._voice_lease_resync_suppressed = False
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
        owner = self._voice_lease_owner
        active_identity = self._voice_input_registry.active_identity
        owner_has_target = bool(
            owner in {"core", "game"}
            and active_identity is not None
            and active_identity.namespace == "builtin"
            and active_identity.name
            == (
                BuiltinVoiceInputConsumer.CORE_CHAT.value
                if owner == "core"
                else BuiltinVoiceInputConsumer.GAME.value
            )
            and self._voice_input_registry.active_accepts_input
        )
        return bool(
            self._voice_lease_synchronized
            and owner_has_target
            and not self._voice_lease_hard_muted
            and not self._voice_lease_focus_suppressed
            and not self._voice_input_suppressed
        )

    def set_independent_asr_handshake(self, value: object) -> None:
        # Record the frontend's authoritative independent-ASR toggle carried by
        # the start_session message (websocket_router). Strictly typed: only a
        # real bool is accepted; anything else (missing field from an older
        # frontend, malformed payloads) clears the override so the route
        # decision falls back to the persisted setting. The handshake is
        # deliberately NOT persisted server-side: persistence stays the
        # settings POST's job, this value only pins the route decision for the
        # session the user just started.
        self._ensure_asr_runtime_state()
        self._independent_asr_handshake_override = (
            value if isinstance(value, bool) else None
        )

    def set_voice_input_resource_optimization_handshake(
        self,
        value: object,
    ) -> None:
        """Pin one session's authoritative resource-optimization preference."""
        self._ensure_asr_runtime_state()
        self._voice_input_resource_optimization_handshake_override = (
            value if isinstance(value, bool) else None
        )

    async def _start_independent_asr_if_enabled(
        self,
        input_mode: str,
        *,
        preserve_hot_swap_audio: bool = False,
        handshake_override=...,
        resource_optimization_override=...,
    ) -> None:
        """Resolve the microphone route for one session start.

        ``handshake_override`` carries the start_session handshake belonging to
        THIS start operation, snapshotted by ``start_session`` before its first
        await. Ellipsis means "not supplied" — the internal re-entry paths
        (hot-swap, device change) have no request of their own and reuse the
        accepted live session's optimization choice.
        """
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
            # A text session blocks the route for its whole life, so revoke the
            # lease rather than let a client that missed session_started keep
            # uploading into it -- but deliver the mic-stop ack to the lease
            # holder first, and re-fence in between. The chokepoint owns that
            # order; the notice is the only thing specific to this exit.
            await self._fail_closed_voice_route(
                "text_session_active",
                operation_generation=operation_generation,
                voice_owner_notice=(
                    {"type": "session_started", "input_mode": "text"}
                    if input_mode == "text"
                    else None
                ),
            )
            return
        try:
            # strict=True or this except branch is unreachable for the failure
            # it exists to catch: the plain read swallows every IO/JSON error
            # and returns the SAME empty dict as a file that simply has no
            # settings yet, so an unreadable or malformed user_preferences.json
            # fell through to `enabled = False` below and quietly selected the
            # native Omni route -- overriding a persisted choice that required
            # independent ASR, which is the opposite of fail-closed. An absent
            # file still returns {} under strict, so a genuine first run keeps
            # defaulting normally.
            settings = await _core_facade.aload_global_conversation_settings(strict=True)
        except Exception:
            # Fail-closed is only the safe answer for a user who WANTS
            # independent ASR: for them the persisted read is the authority and
            # falling back to native would override their choice. A user whose
            # frontend handshake says the feature is OFF has no such choice to
            # protect, and this path runs for EVERY audio session -- so
            # revoking here would kill the microphone of someone who never
            # enabled the feature, over a settings file they may not even know
            # exists. Before this PR that case simply used the native route;
            # keep it that way. The handshake is read early because the
            # unreadable settings cannot answer the question at all.
            unreadable_handshake = (
                self._independent_asr_handshake_override
                if handshake_override is ...
                else handshake_override
            )
            if unreadable_handshake is False:
                if not core_start_is_current():
                    return
                # Same landing as the ordinary `not enabled` path below.
                self._set_microphone_route("native")
                await self._send_core_asr_status(
                    AsrStatusEvent(
                        code="ASR_INDEPENDENT_DISABLED",
                        provider=core_type or "unknown",
                        session_epoch=session_epoch,
                    )
                )
                return
            await self._fail_closed_voice_route(
                "asr_settings_unreadable",
                operation_generation=operation_generation,
                still_current=core_start_is_current,
                status=AsrStatusEvent(
                    code="ASR_INDEPENDENT_FAILED",
                    provider=core_type or "unknown",
                    session_epoch=session_epoch,
                ),
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
        # Prefer this operation's own snapshot; a concurrent start_session can
        # have replaced or cleared the shared field during the awaits above.
        handshake_enabled = (
            self._independent_asr_handshake_override
            if handshake_override is ...
            else handshake_override
        )
        if handshake_enabled is not None:
            # The start_session handshake carries the frontend's authoritative
            # toggle; it overrides the persisted read, which is stale when the
            # settings POST failed or was still in flight at session start.
            enabled = handshake_enabled
        else:
            enabled = bool(settings.get("independentAsrEnabled", True))
        optimization_handshake = resource_optimization_override
        if resource_optimization_override is ...:
            optimization_handshake = getattr(
                self,
                "_voice_input_resource_optimization_session_value",
                None,
            )
            if optimization_handshake is None:
                optimization_handshake = getattr(
                    self,
                    "_voice_input_resource_optimization_handshake_override",
                    None,
                )
        optimization_value = (
            optimization_handshake
            if optimization_handshake is not None
            else settings.get("voiceInputResourceOptimizationEnabled", True)
        )
        resolved_optimization_value = optimization_value is not False
        if resource_optimization_override is not ...:
            # Only an accepted start_session call supplies this argument.
            # Losing/deduplicated requests may still overwrite the manager-level
            # handshake field, so internal provider restarts must use this
            # session-owned snapshot instead.
            self._voice_input_resource_optimization_session_value = (
                resolved_optimization_value
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
            resource_optimization_enabled=resolved_optimization_value,
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
            # Independent ASR was ENABLED and failed to start (provider connect,
            # credentials, config). Unlike a runtime failure this emits no
            # BLOCKED lifecycle event -- IndependentAsrRuntime.start can never
            # reach _handle_independent_asr_error, the only emitter -- so the
            # client sees a toast and nothing else. Revoke after the route is
            # pinned: _set_microphone_route re-arms the resync suppression for
            # every non-blocked mode.
            self._set_microphone_route("blocked")
            # Generation-only fence on purpose: core_start_is_current() cannot
            # be reused here because _independent_asr_provider was just
            # assigned from the result above, so route_operation_unclaimed()'s
            # "provider is None" clause is deliberately false by now. Nothing
            # awaits between the check above and this call, so the generation
            # is exactly as strong as the original inline revoke.
            await self._fail_closed_voice_route(
                "asr_start_failed",
                operation_generation=operation_generation,
            )

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
        await self._asr_runtime.abort(reason)
        self._invalidate_voice_pcm_sync(reason)
        await self._voice_input_registry.wait_idle()

    async def _reset_native_audio_turn(
        self,
        reason: str,
        *,
        route_mode: str | None = None,
    ) -> None:
        """Discard the native provider's pending input audio after a PCM hole.

        A native turn is segmented by the provider's server VAD over one
        continuously appended input buffer, so microphone PCM dropped
        mid-utterance is invisible to it: speech from both sides of the hole
        gets concatenated into a single incorrect transcript. Clearing the
        server-side input buffer drops the pre-hole partial utterance
        instead -- the native-route equivalent of invalidating the whole
        candidate turn on the independent route. No-op for sessions without
        an input-buffer clear (text sessions, Gemini).
        """

        if (route_mode or self._asr_route_mode) != "native":
            return
        session_ref = getattr(self, "session", None)
        if getattr(session_ref, "_fatal_error_occurred", False):
            return
        clear_buffer = getattr(session_ref, "clear_audio_buffer", None)
        if not callable(clear_buffer):
            return
        try:
            await clear_buffer()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "[%s] native audio turn reset failed reason=%s",
                self.lanlan_name,
                reason,
            )

    async def _invalidate_interrupted_voice_turn(
        self,
        reason: str,
        *,
        abort: "asyncio.Future[None] | None" = None,
    ) -> None:
        """Invalidate whichever route owns a turn broken by dropped PCM.

        The independent abort runs first on purpose: its synchronous prefix
        bumps the audio generation, so a pre-hole frame still in flight in
        the worker fails its ingress-token check instead of being appended
        after the buffer clear. The clear must not simply await the abort to
        completion, though: the abort's first real suspension is a frontend
        status send, under exactly the congestion that caused the overflow.
        So the abort is scheduled, given one loop tick to run its prefix, and
        only re-joined at the end.

        ``abort`` lets a caller that dispatches this method with
        ``_fire_task`` create the abort task itself. That is load-bearing, not
        cosmetic: creating it inside this coroutine puts it one task-
        scheduling layer deeper than the caller, so the caller resumes before
        the abort's first step ever runs and the ordering above is silently
        lost (CodeRabbit). Callers that ``await`` this method directly can
        leave it None.
        """

        owns_abort = abort is None
        if abort is None:
            abort = asyncio.ensure_future(self._abort_independent_asr(reason))
        try:
            if owns_abort:
                await asyncio.sleep(0)
            await self._reset_native_audio_turn(reason)
        finally:
            await abort

    async def _suspend_independent_asr(self, reason: str) -> None:
        await self._asr_runtime.suspend(reason)
        self._invalidate_voice_pcm_sync(reason)
        await self._voice_input_registry.wait_idle()

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
        else:
            self._voice_input_registry.invalidate_utterance(
                reason="independent_asr_close",
            )
        await self._voice_input_registry.wait_idle()
        self._voice_input_audio_pipeline = VoiceInputAudioPipeline(
            nr_enabled=self._voice_input_noise_reduction_enabled,
        )
        self._voice_input_pipeline_failed = False
        self._independent_asr_provider = None
        self._independent_asr_route_key = None
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

    async def apply_voice_input_noise_reduction(self, enabled: bool) -> bool:
        """Make a mid-session noise-reduction toggle reach the live microphone.

        The settings endpoint used to update only
        ``OmniRealtimeClient._audio_processor``, but every microphone frame now
        passes through this Core-owned :class:`VoiceInputAudioPipeline` FIRST,
        and that pipeline is built once per session start. It also downsamples
        PC audio to 16 kHz, so the Omni processor downstream sees 16 kHz and
        skips RNNoise entirely -- and independent-ASR routes never reach the
        Omni processor at all. The toggle therefore did nothing at all until
        some later session rebuilt this pipeline, while the endpoint reported
        success (Codex P2).

        Replacing rather than mutating is deliberate and already the house
        pattern here: session start does exactly this when the persisted value
        differs, and the PCM ingress paths guard on
        ``self._voice_input_audio_pipeline is not pipeline_ref``, so frames
        in flight against the old processor are discarded instead of being
        mixed with the new one. A DSP stage being switched on or off mid-stream
        is precisely when dropping the in-flight frame is what you want.

        Returns True when the pipeline was actually rebuilt.
        """

        self._ensure_asr_runtime_state()
        nr_enabled = bool(enabled)
        self._voice_input_noise_reduction_enabled = nr_enabled
        if self._voice_input_audio_pipeline.nr_enabled == nr_enabled:
            return False
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
        return True

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

    async def _send_voice_control_status(self, message: str) -> None:
        """Send a mic control-plane status to the current AND voice sockets.

        ``self.websocket`` is the newest socket, which is not necessarily the
        one holding the hardware microphone: this PR deliberately supports a
        recorder superseded by a newer chat window. Lifecycle transitions,
        lease resync requests and blocked-route notices act on the microphone,
        so they must also reach its owner or the teardown never runs there.

        The extra delivery is getattr-guarded rather than folded into
        ``send_status``: that signature is doubled by a large number of focused
        tests, and narrow manager doubles do not carry the notify mixin at all.
        """

        await self.send_status(message)
        send_to_voice_owner = getattr(self, "_send_to_voice_owner", None)
        if callable(send_to_voice_owner):
            await send_to_voice_owner({"type": "status", "message": message})

    async def _maybe_signal_voice_lease_resync(self) -> None:
        """Nudge a client whose PCM is dropped only because no lease is set.

        Deliberate suppression (hard mute, focus suppression, game owner)
        must stay silent; only an unsynchronized lease or an installed
        ``none`` owner means the sender lost a lease it still believes it
        holds. One signal per connection and lease state keeps the channel
        quiet while every later lease change re-arms it.
        """

        if self._voice_lease_resync_suppressed:
            # The backend revoked this lease on purpose (fail-closed route, or
            # a text session took over). Asking the client to resync would make
            # a still-recording window re-send its snapshot and re-establish
            # exactly the lease we just dropped -- a revoke/resync ping-pong.
            # Deliberately not keyed on route == "blocked": blocked is also the
            # legitimate cold-start placeholder, where the signal IS wanted.
            return
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
        await self._send_voice_control_status(
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
            ),
        )

    async def _maybe_signal_blocked_text_mode_microphone(self) -> None:
        """Tell a client that is still recording into a text-mode session.

        The microphone lease belongs to the frontend and no session-lifecycle
        path resets it, while a text-mode session pins the route to
        ``blocked`` (``_start_independent_asr_if_enabled`` returns early for a
        non-audio ``input_mode``). A client that keeps uploading therefore has
        every frame accepted at ingress and dropped here. The current frontend
        stops the microphone itself on ``session_started(input_mode='text')``;
        this covers older and third-party clients, which would otherwise get
        no signal at all. One status per text-mode episode: the flag is
        cleared whenever the route leaves ``blocked``.
        """

        if str(getattr(self, "input_mode", "audio") or "audio") != "text":
            return
        if self._blocked_text_mode_microphone_signalled:
            return
        self._blocked_text_mode_microphone_signalled = True
        await self._send_voice_control_status(
            json.dumps(
                {
                    "code": "VOICE_INPUT_BLOCKED_TEXT_SESSION",
                    "details": {"reason": "text_session_active"},
                }
            ),
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
                    # The abort task is created HERE, not inside the
                    # wrapper: one extra scheduling layer would let this
                    # coroutine resume before the abort's synchronous prefix
                    # runs, breaking the ordering the comment above promises.
                    abort = self._fire_task(
                        self._abort_independent_asr("ingress_backpressure")
                    )
                    self._fire_task(
                        self._invalidate_interrupted_voice_turn(
                            "ingress_backpressure",
                            abort=abort,
                        )
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
        def preprocessing_failure_is_current() -> bool:
            # The route-operation generation is deliberately NOT repeated here:
            # _fail_closed_voice_route fences on it already, with the identical
            # comparison, and re-checks it after the status send too.
            return not (
                not self._voice_input_pipeline_failed
                or self._voice_lease_connection_id != source_connection_id
                or self._voice_lease_generation != source_lease_generation
                or (
                    self._voice_input_transition_generation
                    != voice_transition_generation
                )
                or self._capture_ingress_token().session_epoch
                != source_session_epoch
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
            )

        # Ingress backstop. _voice_input_pipeline_failed already drops frames,
        # but only after _enqueue_audio_stream_data has parsed and queued them:
        # _voice_input_accepts_pcm is lease-only and never consults the route.
        # The revoke lands strictly AFTER the status send -- revoking first
        # would clear the voice socket and leave the notice with no target.
        await self._fail_closed_voice_route(
            "audio_preprocessing_failed",
            operation_generation=route_operation_generation,
            still_current=preprocessing_failure_is_current,
            status=AsrStatusEvent(
                code="ASR_AUDIO_PREPROCESSING_FAILED",
                provider=source_provider,
                session_epoch=source_session_epoch,
            ),
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
                    # Weakest of the three sites: the hot-swap barrier is up, so
                    # self.session may still be the pre-swap session being torn
                    # down (clearing that buffer is a no-op) and the hole's real
                    # damage lands in the post-swap session, which the flush
                    # path below covers. Load-bearing only when the hot swap is
                    # aborted and the same session resumes.
                    await self._invalidate_interrupted_voice_turn(
                        "ingress_backpressure"
                    )
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
            await self._maybe_signal_blocked_text_mode_microphone()
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
        # Cached pre-swap frames carry a stale route generation, so replay
        # REBINDS them onto the new session -- but only the local send token is
        # rebound; the frame objects in ``damaged_frames`` keep their original
        # token. The damage check at the end therefore missed them, skipped
        # _invalidate_interrupted_voice_turn, and left a prefix that had already
        # reached the new provider in place: later speech got concatenated
        # across the missing tail instead of the damaged turn being cleared
        # (Codex P2). Track current-route damage explicitly rather than trying
        # to reconstruct it from tokens that were never updated.
        rebound_to_current_route = False
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
                nonlocal rebound_to_current_route
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
                        # From here on this frame's audio is being sent on the
                        # LIVE route, so any later damage in this flush belongs
                        # to the current turn even though the recorded frames
                        # still carry their pre-swap tokens.
                        rebound_to_current_route = True
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
            # damaged tokens remain current. rebound_to_current_route covers the
            # frames whose send token was rebound onto the live session while
            # the recorded frame kept its pre-swap token -- without it a replay
            # that failed AFTER delivering a rebound prefix looked like damage
            # to a turn nobody was listening to.
            if damaged_frames and (
                rebound_to_current_route
                or any(
                    self._ingress_token_matches(frame.token)
                    for frame in damaged_frames
                )
            ):
                await self._invalidate_interrupted_voice_turn(
                    "ingress_backpressure"
                )

    def _invalidate_voice_pcm_sync(self, reason: str) -> None:
        self._voice_input_registry.invalidate_utterance(reason=reason)
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
        previous_owner = previous[0]
        if owner != previous_owner:
            if owner == "game":
                self._voice_input_registry.activate(
                    self._game_voice_input_registration.handle,
                )
            elif owner == "core":
                self._voice_input_registry.activate(
                    self._core_chat_voice_input_registration.handle,
                )
        reasons: set[str] = set()
        if owner == "none":
            reasons.add("owner_none")
        elif (
            owner == "game"
            and not self._voice_input_registry.active_accepts_input
        ):
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
            await self._voice_input_registry.wait_idle()
        elif reason == "game_release":
            if should_abort:
                route_operation_snapshot = self._asr_route_operation_generation
                await self._asr_runtime.abort(reason)
                await self._voice_input_registry.wait_idle()
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
            await self._asr_runtime.abort(reason)
            await self._voice_input_registry.wait_idle()

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

    def _set_voice_input_websocket(self, connection_id: str, websocket) -> bool:
        """Remember which socket holds the voice lease, for mic control-plane pushes.

        ``self.websocket`` is the NEWEST socket (it is reassigned at accept
        time), which is not necessarily the one recording: this PR deliberately
        supports a recorder that has been superseded by a newer chat window.
        Microphone teardown notices must follow the lease, not the display
        plane, or the window holding the hardware never hears about them.
        Only the current lease holder may install a socket, so a stale claim
        cannot redirect the control plane.
        """

        normalized = str(connection_id or "").strip()
        if not normalized or normalized != self._voice_lease_connection_id:
            return False
        self._voice_input_websocket = websocket
        return True

    def _clear_voice_input_websocket(self) -> None:
        self._voice_input_websocket = None

    def _begin_voice_input_connection(self, connection_id: str) -> bool:
        normalized = str(connection_id or "").strip()
        if not normalized or normalized == self._voice_lease_connection_id:
            return False
        invalidate_start = getattr(self._asr_runtime, "_invalidate_asr_start", None)
        if callable(invalidate_start):
            invalidate_start()
        # A new claim must not inherit the previous holder's socket, and a
        # fresh connection re-arms the resync signal.
        self._clear_voice_input_websocket()
        self._voice_lease_resync_suppressed = False
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

    async def _fail_closed_voice_route(
        self,
        reason: str,
        *,
        operation_generation: int,
        still_current: Callable[[], bool] | None = None,
        status: AsrStatusEvent | None = None,
        voice_owner_notice: dict | None = None,
    ) -> bool:
        """The single exit by which an enabled ASR route ends fail-closed.

        Five call sites reach this state (text session, unreadable settings,
        ASR start failure, audio-preprocessing failure, runtime failure) and
        each review round found another. What every one of them has to get
        right is not a shared fence -- their staleness conditions genuinely
        differ -- but a shared ORDER:

        1. fence, so a competing NEWER route operation is not clobbered;
        2. notify the LEASE holder while the lease still has a target: the
           revoke clears ``_voice_input_websocket`` AND the lease id, and
           ``_voice_owner_socket()`` returns None on either;
        3. re-fence, because step 2 awaited and a newer start may have
           installed its own blocked placeholder during it;
        4. revoke.

        Step 3 is why a naive "revoke whenever the route ends blocked" was
        rejected: ``_revoke_voice_input_connection`` calls
        ``_invalidate_asr_start()`` first, so revoking on a stale exit cancels
        the competing newer start -- reddening
        ``test_stale_start_abort_does_not_clobber_newer_start_placeholder``.
        Holding the order here rather than at each call site is what makes a
        NEW exit correct by construction instead of by review.

        ``operation_generation`` is a required keyword so a new exit cannot
        quietly omit the one fence every exit needs; ``still_current`` carries
        the caller's own, usually stricter, predicate (route key, provider,
        session ref, ingress epoch...) and is re-evaluated in step 3 too.
        Returns True only when the lease was actually revoked.
        """

        self._ensure_asr_runtime_state()

        def operation_is_current() -> bool:
            if not self._asr_route_operation_matches(operation_generation):
                return False
            return still_current is None or bool(still_current())

        if not operation_is_current():
            return False
        notified = False
        if voice_owner_notice is not None and self._voice_lease_owner != "game":
            send_to_voice_owner = getattr(self, "_send_to_voice_owner", None)
            if callable(send_to_voice_owner):
                await send_to_voice_owner(dict(voice_owner_notice))
                notified = True
        if status is not None:
            await self._send_core_asr_status(status)
            notified = True
        if notified and not operation_is_current():
            return False
        return await self._revoke_lease_for_blocked_route(reason)

    async def _revoke_lease_for_blocked_route(self, reason: str) -> bool:
        """Fail-closed fail-safe for an enabled-but-blocked microphone route.

        Reached ONLY through :meth:`_fail_closed_voice_route`, which owns the
        notify-then-fence-then-revoke order this must not be called without;
        ``scripts/check_core_contracts.py`` enforces that.

        Clients that never receive or never honour the teardown notice (older
        builds, third-party clients, throttled background tabs) keep uploading
        PCM into a route that discards it. Stop accepting it at ingress too.
        The game owner is exempt: its built-in Registry consumer holds the
        active transcript route and must not be collaterally revoked.

        NEVER hoist this above the stale/competing-start exits.
        ``_revoke_voice_input_connection`` calls ``_invalidate_asr_start()``
        first, so revoking on a stale exit cancels a concurrent NEWER start --
        verified: an over-broad "revoke whenever the route ends blocked"
        reddens test_stale_start_abort_does_not_clobber_newer_start_placeholder.

        This is a backstop, not the primary defence. A frontend that later runs
        startMicCapture re-claims the lease from scratch via refreshMicLease,
        and ``_handle_voice_input_control`` enforces only generation
        monotonicity -- the revoke resets the generation to -1, so the next
        client snapshot wins unconditionally. The latch that gates capture
        start on the client is what actually closes the hole.
        """

        del reason
        if self._asr_route_mode != "blocked" or self._voice_lease_owner == "game":
            return False
        self._voice_lease_resync_suppressed = True
        return await self._revoke_voice_input_connection(
            self._voice_lease_connection_id
        )

    async def _revoke_voice_input_connection(self, connection_id: str) -> bool:
        """Release the voice lease held by a websocket that just disconnected.

        Only the current holder may revoke: a socket that already lost the
        identity to a newer claim must never clear the winner's lease. The
        shared session is deliberately left alone -- this aborts the voice
        turn (releasing the realtime dispatch pause and the provider
        transport) and parks the lease at owner ``none`` until the next
        socket engages voice input, exactly like a fresh claim does.
        """

        self._ensure_asr_runtime_state()
        normalized = str(connection_id or "").strip()
        if not normalized or normalized != self._voice_lease_connection_id:
            return False
        invalidate_start = getattr(self._asr_runtime, "_invalidate_asr_start", None)
        if callable(invalidate_start):
            invalidate_start()
        self._clear_voice_input_websocket()
        self._voice_lease_connection_id = ""
        self._voice_lease_generation = -1
        self._voice_lease_synchronized = False
        self._voice_lease_control_seen = False
        await self._apply_voice_lease_state(
            owner="none",
            hard_muted=False,
            focus_suppressed=False,
            reason="voice_connection_closed",
            force_abort=True,
        )
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
        self._voice_input_registry.invalidate_utterance(
            token,
            reason="asr_turn_abandoned",
        )
        await self._voice_input_registry.wait_idle()

    async def _prepare_voice_input_turn(self, token: VoiceTurnToken) -> bool:
        self._ensure_asr_runtime_state()
        # A lease transition activates its next consumer before waiting for
        # keyed cancellation callbacks and aborting the ASR transport. Reject
        # a prepare arriving in that window before it can pin an old ingress
        # token to the newly active consumer.
        if (
            not isinstance(token, VoiceTurnToken)
            or token.ingress != self._capture_ingress_token()
            or not self._voice_input_accepts_pcm()
        ):
            return False
        if not self._voice_input_registry.begin_utterance(token):
            return False
        return await self._voice_input_registry.prepare_utterance(token)

    async def _dispatch_voice_input_partial(
        self,
        event: VoicePartialEvent,
    ) -> None:
        await self._voice_input_registry.dispatch_partial(event)

    async def _dispatch_voice_input_final(
        self,
        event: VoiceTranscriptEvent,
    ) -> None:
        result = await self._voice_input_registry.dispatch_final(event)
        if result is VoiceInputDispatchResult.CALLBACK_FAILED:
            await self._send_core_asr_status(
                AsrStatusEvent(
                    code="ASR_INDEPENDENT_INJECTION_FAILED",
                    provider=event.provider,
                    session_epoch=event.turn_token.ingress.session_epoch,
                )
            )
        elif result is VoiceInputDispatchResult.REJECTED:
            logger.debug(
                "[%s] voice input final rejected turn=%s-%s",
                self.lanlan_name,
                event.turn_token.ingress.session_epoch,
                event.turn_token.turn_id,
            )

    async def _cancel_core_chat_voice_turn(
        self,
        context: CoreChatTurnContext,
        reason: str,
    ) -> None:
        del reason
        await self._send_core_asr_preview_clear(context.external_turn_id)
        self._abandon_core_voice_turn(
            context.external_turn_id,
            session_ref=context.session_ref,
        )

    async def _prepare_core_voice_turn(
        self,
        token: VoiceTurnToken,
        *,
        session_ref: object | None = None,
        abandon_on_failure: bool = True,
    ) -> bool:
        if not self._ingress_token_matches(token.ingress):
            return False
        if self._voice_lease_owner != "core":
            return False
        if session_ref is None:
            session_ref = getattr(self, "session", None)
        if session_ref is None:
            return False
        transition_generation = self._voice_input_transition_generation
        external_turn_id = f"asr-{token.ingress.session_epoch}-{token.turn_id}"
        previous_preview_turn_id = self._core_asr_preview_turn_id
        previous_preview_turn_token = self._core_asr_preview_turn_token
        previous_preview_text = self._core_asr_preview_text
        # Turn preparation is the ordered boundary between two turns' partial
        # streams, so every preview from here on belongs to this turn. Stamping
        # the owner here is what lets a previous turn's delayed clear be
        # recognized as stale by the frontend instead of erasing this bubble.
        self._core_asr_preview_turn_id = external_turn_id
        self._core_asr_preview_turn_token = token
        self._core_asr_preview_text = ""

        def operation_is_current() -> bool:
            return bool(
                transition_generation == self._voice_input_transition_generation
                and self._voice_lease_owner == "core"
                and session_ref is self.session
                and self._ingress_token_matches(token.ingress)
            )

        prepare = getattr(session_ref, "prepare_external_voice_turn", None)
        preparation_succeeded = False
        try:
            if callable(prepare):
                await prepare(turn_id=external_turn_id)
            else:
                interrupt = getattr(session_ref, "handle_interruption", None)
                if callable(interrupt):
                    await interrupt()
            if not operation_is_current():
                if abandon_on_failure:
                    self._abandon_core_voice_turn(
                        external_turn_id,
                        session_ref=session_ref,
                    )
                return False
            await self.handle_new_message()
            if operation_is_current():
                preparation_succeeded = True
                return True
            if abandon_on_failure:
                self._abandon_core_voice_turn(
                    external_turn_id,
                    session_ref=session_ref,
                )
            return False
        except asyncio.CancelledError:
            if abandon_on_failure:
                self._abandon_core_voice_turn(
                    external_turn_id,
                    session_ref=session_ref,
                )
            raise
        except Exception:
            if abandon_on_failure:
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
        finally:
            if (
                not preparation_succeeded
                and self._core_asr_preview_turn_id == external_turn_id
                and self._core_asr_preview_turn_token == token
            ):
                self._core_asr_preview_turn_id = previous_preview_turn_id
                self._core_asr_preview_turn_token = previous_preview_turn_token
                self._core_asr_preview_text = previous_preview_text

    async def _submit_core_voice_turn(
        self,
        text: str,
        *,
        turn_id: str,
        session_ref: object | None = None,
    ) -> None:
        """Submit a completed ASR turn to the session that produced it.

        ``session_ref`` is the session the caller already validated. Re-reading
        ``self.session`` here instead would discard that validation: the caller
        awaits a preview restore between its check and this call, and a hot swap
        or a concurrent start landing in that window would make this inject one
        conversation's transcript into another. Defaults to the current session
        only for callers that have no captured reference of their own.
        """

        if session_ref is None:
            session_ref = self.session
        submit = getattr(session_ref, "submit_external_voice_turn", None)
        if callable(submit):
            await submit(text, turn_id=turn_id)
        else:
            await session_ref.create_response(text)

    async def _dispatch_core_asr_transcript(
        self,
        event: VoiceTranscriptEvent,
        *,
        session_ref: object | None = None,
    ) -> None:
        token = event.turn_token.ingress
        external_turn_id = f"asr-{token.session_epoch}-{event.turn_token.turn_id}"
        if session_ref is None:
            session_ref = getattr(self, "session", None)
        transition_generation = self._voice_input_transition_generation
        try:
            if (
                not self._ingress_token_matches(token)
                or self._voice_lease_owner != "core"
                or session_ref is None
            ):
                return
            if not event.text.strip():
                # An empty final still completed the turn provider-side (e.g.
                # the OpenAI/Step stalled-item timeouts): Core deliberately
                # injects no user_transcript for empty text, yet the frontend
                # removes the streaming preview bubble only on
                # user_transcript / fatal teardown / session stop. Tell it
                # explicitly, or the bubble lingers and gets reused by the
                # next turn.
                await self._send_core_asr_preview_clear(external_turn_id)
                return
            accepted = await self.handle_input_transcript(
                event.text,
                is_voice_source=True,
                source="independent_asr",
                metadata={"provider": event.provider},
            )
            def route_still_core() -> bool:
                """The route-identity half, re-checkable across an await.

                Deliberately excludes ``self.session is session_ref``: a hot
                swap promoting a new session is NOT a reason to drop this
                transcript, it is a reason to submit it to the session it was
                validated against -- which ``_submit_core_voice_turn`` does via
                ``session_ref``. Folding the session check in here would make a
                hot swap silently discard the user's finished utterance.
                """
                return (
                    transition_generation
                    == self._voice_input_transition_generation
                    and self._voice_lease_owner == "core"
                    and self._ingress_token_matches(token)
                )

            operation_still_current = route_still_core()
            if not accepted or not operation_still_current:
                if (
                    not accepted
                    and operation_still_current
                    and self.session is session_ref
                ):
                    # Rejected text (echo suppression, takeover routing) also
                    # never produces a user_transcript; drop the preview so it
                    # cannot linger. Guarded on an unchanged runtime identity:
                    # when the world moved on, a newer turn may already own
                    # the preview bubble and the clear must not remove it.
                    await self._send_core_asr_preview_clear(external_turn_id)
                return
            await self._restore_core_asr_preview_after_final(
                external_turn_id,
                session_epoch=token.session_epoch,
            )
            # Re-fence: the restore above awaited a websocket send, and pinning
            # session_ref only protects the SESSION half of what was checked
            # before it. A game or text takeover landing inside that await moves
            # _voice_lease_owner / _voice_input_transition_generation without
            # necessarily replacing self.session, and this would still inject
            # the transcript and start an ordinary Core response after the route
            # had left Core (Codex P2). Same shape as the fence-notify-re-fence
            # order _fail_closed_voice_route owns.
            if not route_still_core():
                return
            # A same-conversation hot swap keeps the ingress and transition
            # identities live, but closes the prepared Core session before
            # promoting its replacement. Wait for that barrier so a final
            # cannot target the closed response arbiter in the close->promote
            # window. asyncio.wait observes completion without propagating a
            # handled swap-task exception into transcript dispatch.
            swap_task = getattr(self, "final_swap_task", None)
            if (
                bool(getattr(self, "is_hot_swap_imminent", False))
                and isinstance(swap_task, asyncio.Task)
                and swap_task is not asyncio.current_task()
                and not swap_task.done()
            ):
                await asyncio.wait((swap_task,))
                if not route_still_core():
                    return

            # Re-read only after the route identity has survived every await.
            # An unchanged transition/ingress means a replacement session is
            # the promoted endpoint for this same conversation, not an
            # unrelated start. Re-prepare the external turn there so its
            # response arbiter owns the pause before the priority-0 final is
            # submitted. A real conversation/lease transition bumps one of the
            # fences above and is still dropped fail-closed.
            target_session = getattr(self, "session", None)
            if target_session is None:
                return
            if target_session is not session_ref:
                session_ref = target_session
                prepare = getattr(session_ref, "prepare_external_voice_turn", None)
                if callable(prepare):
                    await prepare(turn_id=external_turn_id)
                if not route_still_core() or self.session is not session_ref:
                    return
            await self._submit_core_voice_turn(
                event.text,
                turn_id=external_turn_id,
                session_ref=session_ref,
            )
        finally:
            self._abandon_core_voice_turn(
                external_turn_id,
                session_ref=session_ref,
            )

    async def _send_core_asr_preview(
        self,
        event: VoicePartialEvent,
        *,
        remember: bool = True,
    ) -> None:
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
        payload = {
            "type": "user_transcript_preview",
            "text": event.text,
            "turn_id": turn_id,
        }
        # ``turn_id`` stays the Core speech id (unchanged legacy field). The
        # ASR turn identity travels in its own key so the frontend compares
        # like with like; it is omitted while no turn is prepared, which keeps
        # an unkeyed bubble on the pre-existing unconditional-removal path.
        preview_turn_id = self._core_asr_preview_turn_id
        if preview_turn_id:
            payload["asr_turn_id"] = preview_turn_id
        await send_json(payload)
        # ``remember=False`` is used by the repair re-send, which only mirrors
        # what the cache already holds: it must never write its own (possibly
        # older, since ``send_json`` above is awaited) text back over a partial
        # that landed in the meantime.
        if (
            remember
            and preview_turn_id
            and self._core_asr_preview_turn_id == preview_turn_id
        ):
            self._core_asr_preview_text = event.text

    async def _restore_core_asr_preview_after_final(
        self,
        finalized_turn_id: str,
        *,
        session_epoch: int,
    ) -> None:
        """Re-render a newer turn's preview erased by a late user_transcript.

        ``user_transcript`` is emitted by the injection path and carries no
        turn identity, so the frontend cannot tell whether it belongs to the
        bubble on screen; it removes the singleton preview unconditionally.
        When the accepted final arrives through the transcript dispatcher
        worker after the next turn already started streaming partials, that
        removal erases the newer turn's bubble. Re-send the newer turn's last
        preview text right behind the transcript on the ordered websocket so
        the bubble comes back immediately instead of waiting for whichever
        partial happens to arrive next.

        Owner and text are read here, after the injection await, never
        snapshotted before it: the injection yields, so the newer turn can
        stream further partials (or hand the bubble to a turn newer still)
        while it is in flight. Re-sending a pre-await snapshot would push the
        visible preview backwards -- until the next partial, or forever if the
        turn has stopped producing them.
        """
        preview_owner_turn_id = self._core_asr_preview_turn_id
        preview_owner_turn_token = getattr(
            self,
            "_core_asr_preview_turn_token",
            None,
        )
        preview_owner_text = self._core_asr_preview_text
        if (
            not preview_owner_turn_id
            or preview_owner_turn_token is None
            or not preview_owner_text
            or preview_owner_turn_id == finalized_turn_id
        ):
            return
        try:
            await self._send_core_asr_preview(
                VoicePartialEvent(
                    turn_token=preview_owner_turn_token,
                    text=preview_owner_text,
                ),
                remember=False,
            )
        except Exception:
            logger.debug(
                "[%s] independent ASR preview restore delivery failed",
                self.lanlan_name,
            )

    async def _send_core_asr_preview_clear(self, turn_id: str) -> None:
        """Ask the frontend to drop the streaming ASR preview bubble.

        Reuses the existing ``user_transcript_preview`` message with empty
        text as the clear signal: genuine partials are never empty (the ASR
        runtime strips and drops blank partials before ``on_partial``), so
        the frontend can treat an empty preview as removal without a new
        protocol message type. Identity note: finals reach Core through the
        transcript dispatcher's own worker task, so this clear can trail the
        NEXT turn's partials on the ordered websocket. It therefore carries
        ``asr_turn_id`` and the frontend drops the bubble only while that id
        still matches the displayed preview, which makes a stale clear a
        no-op instead of erasing the newer turn's bubble.
        """
        websocket_ref = getattr(self, "websocket", None)
        send_json = getattr(websocket_ref, "send_json", None)
        if not callable(send_json):
            return
        if self._core_asr_preview_turn_id == turn_id:
            self._core_asr_preview_text = ""
            self._core_asr_preview_turn_token = None
        try:
            await send_json(
                {
                    "type": "user_transcript_preview",
                    "text": "",
                    "turn_id": turn_id,
                    "asr_turn_id": turn_id,
                }
            )
        except Exception:
            logger.debug(
                "[%s] independent ASR preview clear delivery failed",
                self.lanlan_name,
            )

    async def _handle_core_asr_failure(self, event: AsrFailureEvent) -> None:
        source_identity = self._capture_core_asr_operation_identity()
        route_operation_generation = self._asr_route_operation_generation

        def failure_is_current() -> bool:
            return bool(
                self._core_asr_operation_identity_matches(source_identity)
                and event.session_epoch
                == self._core_asr_identity_ingress_token(
                    source_identity
                ).session_epoch
            )

        async with self._asr_notification_lock:
            if not failure_is_current():
                return

        # Registry cancellation may execute consumer callbacks. Those
        # callbacks are allowed to publish status/lifecycle notifications,
        # which acquire _asr_notification_lock themselves. Keep the entire
        # cancellation path outside that lock, and re-fence after releasing it
        # in case a newer route operation landed while this task was queued.
        if not failure_is_current():
            return
        self._set_microphone_route("blocked")
        self._invalidate_voice_pcm_sync("independent_asr_failure")
        post_transition_identity = self._capture_core_asr_operation_identity()
        await self._voice_input_registry.wait_idle()
        # Fail-safe for clients that never receive or never honour the
        # teardown notice (an older build, a third-party client, a
        # throttled background tab). The route is fail-closed for the rest
        # of the session, so stop accepting the PCM at ingress too. The
        # game owner is exempt: the galgame route holds the lease through
        # its built-in Registry consumer and must not be collaterally
        # revoked.
        # No notice of its own -- the BLOCKED lifecycle event that produced
        # this failure already reached the client.
        #
        # Re-captured AFTER this handler's own mutations, and that is the
        # whole point: ``source_identity`` was taken while the route was
        # still "independent", and the identity tuple carries
        # ``_asr_route_mode`` (plus ``_microphone_route_generation``, inside
        # the ingress token). Handing that pre-transition tuple to
        # ``still_current`` made the predicate false on ENTRY -- against a
        # transition this handler had itself performed two lines up -- so
        # _fail_closed_voice_route returned before the revoke and left a
        # live hardware microphone uploading into a dead route. The fence
        # exists to reject a COMPETING newer operation, never our own step.
        #
        # The sibling predicates dodge this by testing the route mode
        # ABSOLUTELY (``_asr_route_mode == "blocked"``) rather than against
        # a captured value; a full-tuple comparison cannot, so it has to be
        # re-based here instead.
        await self._fail_closed_voice_route(
            "independent_asr_failure",
            operation_generation=route_operation_generation,
            still_current=lambda: self._core_asr_operation_identity_matches(
                post_transition_identity
            ),
        )

    async def _send_core_asr_status(self, event: AsrStatusEvent) -> None:
        source_identity = self._capture_core_asr_operation_identity()
        async with self._asr_notification_lock:
            if (
                not self._core_asr_operation_identity_matches(source_identity)
                or event.session_epoch
                != self._core_asr_identity_ingress_token(source_identity).session_epoch
            ):
                return
            await self._send_voice_control_status(
                json.dumps(
                    {
                        "code": event.code,
                        "details": {
                            "provider": event.provider,
                            "session_epoch": event.session_epoch,
                        },
                    }
                ),
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
            await self._send_voice_control_status(
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
                ),
            )

    async def _wait_asr_transcript_dispatch_idle(self) -> None:
        """Test seam: await Core-side transcript dispatch quiescence.

        Production code never needs this; tests use it to order assertions
        after the serial dispatch worker drains. Delegates to the ASR
        component's allow-listed public API.
        """

        await self._asr_runtime.wait_transcript_idle()
        await self._voice_input_registry.wait_idle()
