"""Provider-neutral independent-ASR runtime with explicit Core callbacks."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from main_logic.asr_client import (
    _attach_partial_callback,
    _create_asr_session_from_selection,
    _resolve_asr_selection,
)
from main_logic.voice_turn.contracts import (
    AsrFailureEvent,
    SpeechActivityEvent,
    VoiceTranscriptEvent,
    VoiceTurnToken,
)

from ._infra import logger
from .audio import AsrAudioDispatcher, ProcessedVoiceFrame, VoiceInputAudioPipeline
from .detector import (
    AsrDetectorDispatcher,
    CoreDetectorEventEnvelope,
    DetectorActivityEvent,
    DetectorRuntimeEvent,
    DetectorSubmitStatus,
    DetectorTurnEvent,
)
from .detector_runtime import DetectorRuntime, SmartTurnLease
from .lifecycle import (
    AudioDisposition,
    FinalKey,
    VoiceIngressToken,
    VoiceInputLifecycleController,
    VoiceLifecycleEvent,
    VoiceLifecycleState,
    VoiceRouteMode,
    VoiceTransportToken,
)
from .provider_policy import resolve_provider_policy
from .transcript import (
    TranscriptDispatcher,
    TranscriptEnvelope,
)


class AsrStartStatus(Enum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AsrStartResult:
    status: AsrStartStatus
    provider: str | None = None
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class AsrRuntimeCallbacks:
    display_name: Callable[[], str]
    on_prepare_turn: Callable[[VoiceTurnToken], Awaitable[bool]]
    on_partial: Callable[[str, int], Awaitable[None]]
    on_final: Callable[[VoiceTranscriptEvent], Awaitable[None]]
    on_failure: Callable[[AsrFailureEvent], Awaitable[None]]
    on_status: Callable[[str, str], Awaitable[None]]
    on_lifecycle: Callable[[VoiceLifecycleState, str, str], Awaitable[None]]


class IndependentAsrRuntime:
    """Own one independent ASR session without reading Core manager state."""

    def __init__(self, callbacks: AsrRuntimeCallbacks) -> None:
        self._callbacks = callbacks
        self._init_asr_runtime_state()

    @property
    def display_name(self) -> str:
        return self._callbacks.display_name()

    @property
    def route_mode(self) -> str:
        return self._asr_route_mode

    @property
    def required(self) -> bool:
        return self._asr_required

    @property
    def provider(self) -> str | None:
        return self._asr_provider

    @property
    def lifecycle(self) -> VoiceInputLifecycleController | None:
        return self._asr_lifecycle

    @property
    def session_epoch(self) -> int:
        return self._asr_session_epoch

    @property
    def audio_generation(self) -> int:
        return self._asr_audio_generation

    @property
    def audio_bytes(self) -> int:
        return self._asr_audio_bytes

    @property
    def core_route_key(self) -> str | None:
        return self._asr_core_type

    def sync_voice_lease(
        self,
        *,
        connection_id: str,
        generation: int,
        synchronized: bool,
        owner: str,
        hard_muted: bool,
        focus_suppressed: bool,
        suppressed: bool,
    ) -> None:
        """Receive the Core-owned MicLease snapshot used for token checks."""

        self._voice_lease_connection_id = connection_id
        self._voice_lease_generation = generation
        self._voice_lease_synchronized = synchronized
        self._voice_lease_owner = owner
        self._voice_lease_hard_muted = hard_muted
        self._voice_lease_focus_suppressed = focus_suppressed
        self._voice_input_suppressed = suppressed

    def activate_native_route(self) -> None:
        self._asr_required = False
        self._asr_route_mode = "native"

    def deactivate_audio_route(self) -> None:
        self._asr_required = False
        self._asr_route_mode = "blocked"

    def block_audio_route(self) -> None:
        self._asr_required = True
        self._asr_route_mode = "blocked"

    async def close(self) -> None:
        await self._close_independent_asr(next_route_mode="blocked")

    async def process_audio(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
    ) -> ProcessedVoiceFrame:
        return await self._process_microphone_audio(
            pcm16,
            sample_rate_hz=sample_rate_hz,
        )

    def capture_ingress_token(self) -> VoiceIngressToken:
        lifecycle = self._asr_lifecycle
        if lifecycle is None:
            return self._capture_native_ingress_token()
        return self._capture_ingress_token(lifecycle)

    def ingress_token_matches(self, token: VoiceIngressToken) -> bool:
        return self._ingress_token_matches(token)

    def invalidate_voice_pcm(self, reason: str) -> None:
        self._invalidate_voice_pcm_sync(reason)

    async def apply_voice_lease_state(
        self,
        *,
        owner: str,
        hard_muted: bool,
        focus_suppressed: bool,
        suppressed: bool,
        reason: str,
        force_abort: bool,
    ) -> None:
        await self._apply_voice_lease_state(
            owner=owner,
            hard_muted=hard_muted,
            focus_suppressed=focus_suppressed,
            suppressed=suppressed,
            reason=reason,
            force_abort=force_abort,
        )

    async def handle_ingress_backpressure(
        self,
        token: VoiceIngressToken,
    ) -> None:
        await self._handle_audio_ingress_backpressure(token)

    async def wait_transcript_idle(self) -> None:
        await self._asr_transcript_dispatcher.wait_idle()

    def _init_asr_runtime_state(self) -> None:
        self._asr_session = None
        # Microphone audio is fail-closed until an independent ASR session is
        # fully connected. Text sessions never need a permissive audio route.
        self._asr_route_mode = "blocked"
        self._asr_required = False
        self._asr_session_epoch = 0
        self._asr_provider = None
        self._asr_core_type = None
        self._asr_turn_prepared = False
        self._asr_final_lock = asyncio.Lock()
        self._asr_audio_bytes = 0
        self._asr_received_audio = False
        self._asr_close_tasks: set[asyncio.Task[None]] = set()
        self._asr_lifecycle: VoiceInputLifecycleController | None = None
        self._asr_detector: DetectorRuntime | None = None
        self._asr_smart_turn_lease: SmartTurnLease | None = None
        self._voice_input_audio_pipeline = VoiceInputAudioPipeline()
        self._asr_session_factory = None
        self._asr_transport_selection = None
        self._asr_transport_task: asyncio.Task[None] | None = None
        self._asr_transport_lock = asyncio.Lock()
        self._asr_warm_expiry_task: asyncio.Task[None] | None = None
        self._asr_final_watchdog_task: asyncio.Task[None] | None = None
        self._asr_pending_speech_confirmed = False
        self._asr_pending_detector_candidate = None
        self._asr_sealed_turn_token: VoiceTransportToken | None = None
        self._asr_audio_sequence = 0
        self._asr_audio_generation = 0
        self._asr_accepted_final_keys: OrderedDict[FinalKey, None] = OrderedDict()
        self._asr_reserved_final_key: FinalKey | None = None
        self._asr_transcript_dispatcher = TranscriptDispatcher(
            self._dispatch_asr_transcript_envelope,
        )
        self._asr_detector_dispatcher = AsrDetectorDispatcher(
            self._dispatch_asr_detector_event,
            on_failure=self._handle_asr_detector_dispatcher_failure,
        )
        self._asr_audio_dispatcher = AsrAudioDispatcher(
            validator=self._asr_audio_command_is_valid,
            on_wire_audio=self._record_asr_dispatcher_wire_audio,
            on_failure=self._handle_asr_audio_dispatcher_failure,
        )
        self._asr_last_provider_wire_audio_ms = 0
        self._asr_turn_audio_started_at: float | None = None
        self._asr_turn_endpointed_at: float | None = None
        self._asr_first_partial_recorded = False
        self._voice_lease_generation = -1
        self._voice_lease_connection_id = ""
        self._voice_lease_synchronized = False
        self._voice_lease_owner = "none"
        self._voice_lease_hard_muted = False
        self._voice_lease_focus_suppressed = False
        self._voice_lease_requires_abort = False
        self._voice_input_suppressed = True
        self._voice_input_suppression_reasons: set[str] = set()
        self._voice_input_resource_optimization_enabled = True

    def _ensure_asr_runtime_state(self) -> None:
        # A number of focused unit tests intentionally construct the manager via
        # __new__. Keep those narrow lifecycle doubles compatible.
        if not hasattr(self, "_asr_session_epoch"):
            self._init_asr_runtime_state()
        elif not hasattr(self, "_asr_transcript_dispatcher"):
            self._asr_transcript_dispatcher = TranscriptDispatcher(
                self._dispatch_asr_transcript_envelope,
            )
        if not hasattr(self, "_asr_detector_dispatcher"):
            self._asr_detector_dispatcher = AsrDetectorDispatcher(
                self._dispatch_asr_detector_event,
                on_failure=self._handle_asr_detector_dispatcher_failure,
            )
        if not hasattr(self, "_asr_audio_dispatcher"):
            self._asr_audio_dispatcher = AsrAudioDispatcher(
                validator=self._asr_audio_command_is_valid,
                on_wire_audio=self._record_asr_dispatcher_wire_audio,
                on_failure=self._handle_asr_audio_dispatcher_failure,
            )
            self._asr_audio_sequence = 0
            self._asr_pending_detector_candidate = None

    def _capture_ingress_token(
        self,
        lifecycle: VoiceInputLifecycleController,
    ) -> VoiceIngressToken:
        snapshot = lifecycle.snapshot
        return VoiceIngressToken(
            session_epoch=self._asr_session_epoch,
            connection_id=self._voice_lease_connection_id,
            lease_generation=self._voice_lease_generation,
            route_generation=snapshot.route_generation,
            audio_generation=self._asr_audio_generation,
        )

    def _capture_native_ingress_token(self) -> VoiceIngressToken:
        return VoiceIngressToken(
            session_epoch=self._asr_session_epoch,
            connection_id=self._voice_lease_connection_id,
            lease_generation=self._voice_lease_generation,
            route_generation=0,
            audio_generation=self._asr_audio_generation,
        )

    def _capture_turn_token(
        self,
        lifecycle: VoiceInputLifecycleController,
    ) -> VoiceTurnToken:
        return VoiceTurnToken(
            ingress=self._capture_ingress_token(lifecycle),
            turn_id=lifecycle.snapshot.turn_id,
        )

    def _capture_transport_token(
        self,
        lifecycle: VoiceInputLifecycleController,
    ) -> VoiceTransportToken:
        return VoiceTransportToken(
            turn=self._capture_turn_token(lifecycle),
            transport_generation=lifecycle.snapshot.transport_generation,
        )

    def _ingress_token_matches(self, token: VoiceIngressToken) -> bool:
        lifecycle = self._asr_lifecycle
        route_matches = (
            lifecycle is not None
            and token.route_generation == lifecycle.snapshot.route_generation
        ) or (
            lifecycle is None
            and token.route_generation == 0
            and self._asr_route_mode == "native"
            and not self._asr_required
        )
        return bool(
            token.session_epoch == self._asr_session_epoch
            and token.connection_id == self._voice_lease_connection_id
            and token.lease_generation == self._voice_lease_generation
            and token.audio_generation == self._asr_audio_generation
            and route_matches
        )

    def _voice_input_accepts_pcm(self) -> bool:
        owner = self._voice_lease_owner
        owner_has_target = owner in {"core", "game"}
        return bool(
            self._voice_lease_synchronized
            and owner_has_target
            and not self._voice_lease_hard_muted
            and not self._voice_lease_focus_suppressed
            and not self._voice_input_suppressed
        )

    def _transport_token_matches(
        self,
        token: VoiceTransportToken,
        lifecycle: VoiceInputLifecycleController,
    ) -> bool:
        snapshot = lifecycle.snapshot
        return bool(
            self._asr_lifecycle is lifecycle
            and self._ingress_token_matches(token.turn.ingress)
            and token.turn.turn_id == snapshot.turn_id
            and token.transport_generation == snapshot.transport_generation
        )

    def _accept_final_key(self, key: FinalKey) -> bool:
        if key in self._asr_accepted_final_keys:
            return False
        self._asr_accepted_final_keys[key] = None
        while len(self._asr_accepted_final_keys) > 256:
            self._asr_accepted_final_keys.popitem(last=False)
        return True

    def _asr_audio_command_is_valid(
        self,
        turn_token: VoiceTurnToken,
        session_ref: Any,
    ) -> bool:
        lifecycle = self._asr_lifecycle
        detector = self._asr_detector
        return bool(
            self._asr_route_mode == "independent"
            and lifecycle is not None
            and detector is not None
            and self._asr_session is session_ref
            and self._ingress_token_matches(turn_token.ingress)
            and lifecycle.snapshot.turn_id == turn_token.turn_id
            and self._asr_endpointing_ready(lifecycle, detector, turn_token)
            and self._voice_input_accepts_pcm()
        )

    def _asr_endpointing_ready(
        self,
        lifecycle: VoiceInputLifecycleController,
        detector: DetectorRuntime | None,
        turn_token: VoiceTurnToken,
    ) -> bool:
        """Accept provider authority without manufacturing a SmartTurn lease."""

        if detector is None:
            return False
        if lifecycle.provider_policy.endpoint_authority == "provider":
            return True
        return detector.endpointing_ready(turn_token)

    async def _record_asr_dispatcher_wire_audio(
        self,
        turn_token: VoiceTurnToken,
        session_ref: Any,
        byte_count: int,
    ) -> None:
        if byte_count <= 0:
            return
        self._sync_provider_wire_metrics(
            session_ref,
            fallback_audio_bytes=byte_count,
        )
        if self._asr_session is session_ref:
            self._asr_received_audio = True
            self._asr_audio_bytes += byte_count
            lifecycle = self._asr_lifecycle
            if lifecycle is not None:
                lifecycle.metrics.provider_wire_sequence = (
                    self._asr_audio_dispatcher.provider_wire_sequence
                )
                lifecycle.metrics.asr_audio_command_queue_ms = (
                    self._asr_audio_dispatcher.asr_audio_command_queue_ms
                )

    async def _handle_asr_audio_dispatcher_failure(
        self,
        turn_token: VoiceTurnToken,
        error: BaseException,
    ) -> None:
        if turn_token.ingress.session_epoch != self._asr_session_epoch:
            return
        status_code = (
            "ASR_STREAM_BACKPRESSURE"
            if "BACKPRESSURE" in str(error)
            else "ASR_INDEPENDENT_STREAM_FAILED"
        )
        await self._handle_independent_asr_error(
            self._asr_session_epoch,
            self._asr_provider or "unknown",
            status_code=status_code,
        )

    async def _handle_asr_detector_dispatcher_failure(
        self,
        envelope: CoreDetectorEventEnvelope,
        error: BaseException,
    ) -> None:
        logger.error(
            "[%s] detector event dispatcher failed epoch=%s",
            self.display_name,
            envelope.session_epoch,
            exc_info=(type(error), error, error.__traceback__),
        )
        await self._handle_independent_asr_error(
            self._asr_session_epoch,
            self._asr_provider or "unknown",
            status_code="ASR_ENDPOINTING_FAILED",
        )

    async def _dispatch_asr_detector_event(
        self,
        envelope: CoreDetectorEventEnvelope,
    ) -> None:
        event = envelope.event
        detector = self._asr_detector
        lifecycle = self._asr_lifecycle
        stale = bool(
            envelope.session_epoch != self._asr_session_epoch
            or detector is not envelope.detector_ref
            or lifecycle is not envelope.lifecycle_ref
            or detector is None
            or lifecycle is None
            or event.ingress.detector_epoch != detector.detector_epoch
            or not self._ingress_token_matches(event.ingress.ingress_token)
        )
        if stale:
            stale_metrics = getattr(envelope.lifecycle_ref, "metrics", None)
            if stale_metrics is not None:
                stale_metrics.detector_stale_event_count += 1
            return
        lifecycle.metrics.smart_turn_inference_ms = (
            detector.smart_turn_evaluation_ms
        )
        lifecycle.metrics.smart_turn_stale_result_count = (
            detector.smart_turn_stale_result_count
        )
        lifecycle.metrics.smart_turn_coalesced_evaluation_count = (
            detector.smart_turn_coalesced_evaluation_count
        )
        if isinstance(event, DetectorRuntimeEvent):
            await self._handle_independent_asr_error(
                self._asr_session_epoch,
                self._asr_provider or "unknown",
                status_code=(
                    "ASR_INGRESS_BACKPRESSURE"
                    if event.kind == "audio_backpressure"
                    else "ASR_ENDPOINTING_FAILED"
                ),
            )
            return
        if isinstance(event, DetectorActivityEvent):
            await self._handle_independent_asr_activity(
                event.activity,
                self._asr_session_epoch,
            )
            lifecycle = self._asr_lifecycle
            if detector is not self._asr_detector or lifecycle is not envelope.lifecycle_ref:
                return
            if event.activity not in {
                SpeechActivityEvent.SPEECH_STARTED,
                SpeechActivityEvent.SPEECH_RESUMED,
            }:
                return
            if lifecycle.snapshot.state is VoiceLifecycleState.DRAINING:
                self._asr_pending_detector_candidate = event.candidate
                return
            if lifecycle.snapshot.state not in {
                VoiceLifecycleState.PREWARMING,
                VoiceLifecycleState.ACTIVE,
            }:
                return
            turn_token = self._capture_turn_token(lifecycle)
            bound = await detector.bind_candidate(event.candidate, turn_token)
            if bound is None:
                return
            if lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE:
                self._activate_asr_audio_dispatcher(lifecycle, turn_token)
            return
        if not isinstance(event, DetectorTurnEvent):
            return
        turn_token = event.bound_turn.turn_token
        if (
            not self._ingress_token_matches(turn_token.ingress)
            or lifecycle.snapshot.turn_id != turn_token.turn_id
            or not detector.endpointing_ready(turn_token)
        ):
            return
        await self._handle_independent_asr_endpoint(self._asr_session_epoch)
        session_ref = self._asr_session
        if session_ref is None:
            return
        if not self._asr_audio_dispatcher.seal(
            turn_token,
            session_ref,
            after_sequence=self._asr_audio_sequence,
        ):
            await self._handle_independent_asr_error(
                self._asr_session_epoch,
                self._asr_provider or "unknown",
                status_code="ASR_AUDIO_ORDERING_FAILED",
            )

    def _activate_asr_audio_dispatcher(
        self,
        lifecycle: VoiceInputLifecycleController,
        turn_token: VoiceTurnToken,
        *,
        buffered_pcm16: bytes | None = None,
    ) -> bool:
        detector = self._asr_detector
        session_ref = self._asr_session
        if (
            session_ref is None
            or detector is None
            or not getattr(session_ref, "is_ready", True)
            or not self._asr_endpointing_ready(lifecycle, detector, turn_token)
        ):
            return False
        if self._asr_audio_dispatcher.active_turn == turn_token:
            return True
        self._asr_audio_sequence = 0
        return self._asr_audio_dispatcher.activate(
            turn_token,
            session_ref,
            (
                lifecycle.drain_active_start_audio()
                if buffered_pcm16 is None
                else buffered_pcm16
            ),
            sample_rate_hz=16_000,
        )

    async def _ensure_smart_turn_ready(
        self,
        lifecycle: VoiceInputLifecycleController,
        epoch: int,
    ) -> bool:
        if epoch != self._asr_session_epoch or self._asr_lifecycle is not lifecycle:
            return False
        if lifecycle.provider_policy.endpoint_authority == "provider":
            return True
        turn_token = self._capture_turn_token(lifecycle)
        detector = self._asr_detector
        if detector is None:
            await self._handle_independent_asr_error(
                epoch,
                self._asr_provider or "unknown",
                status_code="ASR_BLOCKED_ENDPOINTING",
            )
            return False
        lease = self._asr_smart_turn_lease
        if (
            lease is not None
            and lease.token == turn_token
            and detector.endpointing_ready(turn_token)
        ):
            return True
        if lease is not None:
            await lease.release()
            self._asr_smart_turn_lease = None
        lease = await detector.prepare_endpointing(turn_token)
        if (
            lease is None
            or epoch != self._asr_session_epoch
            or self._asr_lifecycle is not lifecycle
            or not detector.endpointing_ready(turn_token)
        ):
            if lease is not None:
                await lease.release()
            if epoch == self._asr_session_epoch:
                await self._handle_independent_asr_error(
                    epoch,
                    self._asr_provider or "unknown",
                    status_code="ASR_BLOCKED_ENDPOINTING",
                )
            return False
        self._asr_smart_turn_lease = lease
        return True

    async def _handle_audio_ingress_backpressure(
        self,
        token: VoiceIngressToken,
    ) -> None:
        """Invalidate a whole candidate/turn instead of dropping middle PCM."""

        lifecycle = self._asr_lifecycle
        if lifecycle is None or not self._ingress_token_matches(token):
            return
        state = lifecycle.snapshot.state
        if state is VoiceLifecycleState.DRAINING:
            lifecycle.discard_pending_turn()
            self._asr_pending_speech_confirmed = False
            self._asr_pending_detector_candidate = None
            detector = self._asr_detector
            if detector is not None:
                await detector.reset()
            await self._send_asr_status(
                "ASR_INGRESS_BACKPRESSURE",
                self._asr_provider or "unknown",
            )
            return
        if state in {
            VoiceLifecycleState.LOCAL_LISTEN,
            VoiceLifecycleState.WARM_IDLE,
            VoiceLifecycleState.DEEP_SLEEP,
        }:
            self._asr_audio_generation += 1
            lifecycle.invalidate_audio()
            detector = self._asr_detector
            if detector is not None:
                try:
                    await detector.reset()
                except Exception:
                    logger.warning(
                        "[%s] detector reset failed after ingress backpressure",
                        self.display_name,
                    )
            await self._send_asr_status(
                "ASR_INGRESS_BACKPRESSURE",
                self._asr_provider or "unknown",
            )
            return
        if state in {
            VoiceLifecycleState.PREWARMING,
            VoiceLifecycleState.BACKOFF,
            VoiceLifecycleState.ACTIVE,
        }:
            detector = self._asr_detector
            lifecycle.invalidate_audio()
            await self.abort_transport("detector_audio_backpressure")
            if detector is not None and detector is self._asr_detector:
                await detector.reset()
            await self._send_asr_status(
                "ASR_INGRESS_BACKPRESSURE",
                self._asr_provider or "unknown",
            )
            await self._send_asr_lifecycle_state(VoiceLifecycleState.LOCAL_LISTEN)
            return
        await self._send_asr_status(
            "ASR_INGRESS_BACKPRESSURE",
            self._asr_provider or "unknown",
        )

    async def start(
        self,
        *,
        route_key: str,
        resource_optimization_enabled: bool,
    ) -> AsrStartResult:
        """Resolve and start one independent-ASR route."""

        self._ensure_asr_runtime_state()
        await self._close_independent_asr(next_route_mode="blocked")
        self._asr_required = True
        self._asr_route_mode = "blocked"
        self._asr_audio_bytes = 0
        self._voice_input_resource_optimization_enabled = bool(
            resource_optimization_enabled
        )
        core_type = str(route_key or "").strip().lower()
        # Remember attempted disabled/failed routes too. Hot-swap
        # reconciliation should retry only when the Core route truly changes.
        self._asr_core_type = core_type

        try:
            selection = _resolve_asr_selection(core_type)
            selected_provider = getattr(selection, "provider_key", None)
            if not isinstance(selected_provider, str) or not selected_provider.strip():
                raise ValueError("invalid ASR provider selection")
            provider = selected_provider.strip().lower()
            endpointing_mode = getattr(selection, "endpointing_mode", None)
            if endpointing_mode not in {"manual", "provider"}:
                raise ValueError("invalid ASR endpointing selection")
            policy = resolve_provider_policy(provider, endpointing_mode)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Configuration errors must not abort the already-started Core
            # session. Keep the microphone fail-closed and report only the
            # fixed status code/provider category.
            self._asr_session = None
            self._asr_provider = None
            self._asr_route_mode = "blocked"
            unavailable = "BLOCKED" in str(error) or "UNKNOWN_CORE" in str(error)
            failure_code = (
                "ASR_INDEPENDENT_UNAVAILABLE"
                if unavailable
                else "ASR_INDEPENDENT_FAILED"
            )
            await self._send_asr_status(failure_code, core_type or "unknown")
            return AsrStartResult(
                AsrStartStatus.UNAVAILABLE if unavailable else AsrStartStatus.FAILED,
                failure_code=failure_code,
            )

        # Provider selection is immutable for this session epoch. Expose the
        # selected provider during connect retries, then clear it only if the
        # startup attempt ultimately fails.
        self._asr_provider = provider
        epoch = self._asr_session_epoch

        def create_candidate(candidate_selection: Any) -> Any:
            """Create one startup candidate with callbacks bound to its identity."""

            candidate_provider = candidate_selection.provider_key
            candidate_endpointing = candidate_selection.endpointing_mode
            candidate_policy = resolve_provider_policy(
                candidate_provider,
                candidate_endpointing,
            )
            candidate_session = None

            def is_adopted_candidate() -> bool:
                return (
                    candidate_session is not None
                    and self._asr_session is candidate_session
                    and epoch == self._asr_session_epoch
                )

            async def on_final(text: str) -> None:
                if not is_adopted_candidate():
                    return
                await self._handle_independent_asr_final(
                    text, epoch, candidate_provider
                )

            async def on_error(_message: str) -> None:
                if not is_adopted_candidate():
                    return
                await self._handle_independent_asr_error(epoch, candidate_provider)

            async def on_status(_message: str) -> None:
                # Provider status strings are intentionally not forwarded verbatim.
                return None

            async def on_activity(event: SpeechActivityEvent) -> None:
                if not is_adopted_candidate():
                    return
                await self._handle_independent_asr_activity(event, epoch)

            async def on_endpoint() -> None:
                if not is_adopted_candidate():
                    return
                await self._handle_independent_asr_endpoint(epoch)

            async def on_partial(text: str) -> None:
                if not is_adopted_candidate():
                    return
                await self._send_independent_asr_preview(text, epoch)

            candidate_session = _create_asr_session_from_selection(
                core_type,
                selection=candidate_selection,
                on_input_transcript=on_final,
                on_connection_error=on_error,
                on_status_message=on_status,
                on_speech_activity=on_activity,
                on_turn_endpointed=on_endpoint,
                external_endpointing_runtime=(
                    candidate_policy.endpoint_authority == "smart_turn"
                ),
            )
            _attach_partial_callback(candidate_session, on_partial)
            return candidate_session

        asr_session = None
        connect_started_at = time.monotonic()
        try:
            max_attempts = policy.connect_max_attempts
            for attempt in range(max_attempts):
                if epoch != self._asr_session_epoch:
                    return AsrStartResult(
                        AsrStartStatus.FAILED,
                        provider=provider,
                        failure_code="ASR_START_STALE",
                    )
                asr_session = create_candidate(selection)
                try:
                    await asr_session.connect()
                    break
                except asyncio.CancelledError:
                    try:
                        await asr_session.close()
                    except Exception:
                        pass
                    asr_session = None
                    raise
                except Exception:
                    try:
                        await asr_session.close()
                    except Exception:
                        pass
                    asr_session = None
                    if attempt + 1 >= max_attempts:
                        raise
                    await asyncio.sleep(
                        min(
                            policy.connect_retry_cap_seconds,
                            policy.connect_retry_base_seconds * (2**attempt),
                        )
                    )
            if asr_session is None:
                raise RuntimeError("ASR_CONNECT_FAILED")
            if epoch != self._asr_session_epoch:
                await asr_session.close()
                return AsrStartResult(
                    AsrStartStatus.FAILED,
                    provider=provider,
                    failure_code="ASR_START_STALE",
                )
            self._asr_session = asr_session
            self._asr_last_provider_wire_audio_ms = 0
            self._asr_provider = provider
            self._asr_route_mode = "independent"
            self._asr_lifecycle = VoiceInputLifecycleController(
                provider_policy=policy,
                shadow_mode=False,
                resource_optimization_enabled=(
                    self._voice_input_resource_optimization_enabled
                ),
            )
            self._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
            self._asr_lifecycle.metrics.connect_latency_ms = int(
                (time.monotonic() - connect_started_at) * 1_000
            )
            async def on_detector_endpointing_failure() -> None:
                await self._handle_independent_asr_error(
                    epoch,
                    provider,
                    status_code="ASR_ENDPOINTING_FAILED",
                )

            detector_ref: DetectorRuntime | None = None

            async def on_detector_event(event) -> None:
                lifecycle_ref = self._asr_lifecycle
                if (
                    detector_ref is None
                    or lifecycle_ref is None
                    or epoch != self._asr_session_epoch
                ):
                    return
                accepted = self._asr_detector_dispatcher.submit_nowait(
                    CoreDetectorEventEnvelope(
                        event=event,
                        detector_ref=detector_ref,
                        lifecycle_ref=lifecycle_ref,
                        session_epoch=epoch,
                    )
                )
                if not accepted:
                    raise RuntimeError("ASR_DETECTOR_CONTROL_BACKPRESSURE")

            detector_ref = DetectorRuntime(
                provider_policy=policy,
                on_endpointing_failure=(
                    on_detector_endpointing_failure
                    if policy.endpoint_authority == "smart_turn"
                    else None
                ),
                on_event=(
                    on_detector_event
                    if policy.endpoint_authority == "smart_turn"
                    else None
                ),
            )
            self._asr_detector = detector_ref
            self._asr_session_factory = create_candidate
            self._asr_transport_selection = selection
            self._schedule_transport_warm_expiry(epoch)
            await self._send_asr_lifecycle_state(VoiceLifecycleState.LOCAL_LISTEN)
            await self._send_asr_status("ASR_INDEPENDENT_READY", provider)
            return AsrStartResult(AsrStartStatus.READY, provider=provider)
        except asyncio.CancelledError:
            if asr_session is not None:
                await asr_session.close()
            raise
        except Exception:
            if asr_session is not None:
                try:
                    await asr_session.close()
                except Exception:
                    pass
            if epoch == self._asr_session_epoch:
                self._asr_session = None
                self._asr_provider = None
                self._asr_route_mode = "blocked"
                failure_code = (
                    "ASR_INDEPENDENT_PROVIDER_UNAVAILABLE"
                    if policy.connect_max_attempts > 1
                    else "ASR_INDEPENDENT_FAILED"
                )
                await self._send_asr_status(failure_code, provider)
                return AsrStartResult(
                    AsrStartStatus.UNAVAILABLE
                    if policy.connect_max_attempts > 1
                    else AsrStartStatus.FAILED,
                    provider=provider,
                    failure_code=failure_code,
                )
        return AsrStartResult(
            AsrStartStatus.FAILED,
            provider=provider,
            failure_code="ASR_INDEPENDENT_FAILED",
        )

    async def _close_independent_asr(
        self,
        *,
        next_route_mode: Literal["blocked"],
    ) -> None:
        """Invalidate callbacks first, then release the detached provider session."""

        self._ensure_asr_runtime_state()
        self._asr_session_epoch += 1
        self._asr_audio_generation += 1
        self._asr_transcript_dispatcher.invalidate_all()
        self._asr_detector_dispatcher.invalidate_all()
        self._asr_audio_dispatcher.abort()
        asr_session = self._asr_session
        self._asr_session = None
        self._asr_provider = None
        self._asr_core_type = None
        lifecycle = self._asr_lifecycle
        self._asr_lifecycle = None
        if lifecycle is not None:
            lifecycle.stop()
        detector, self._asr_detector = self._asr_detector, None
        self._asr_smart_turn_lease = None
        if detector is not None:
            await detector.close()
        audio_pipeline = self._voice_input_audio_pipeline
        try:
            await audio_pipeline.close()
        except Exception:
            logger.warning("[%s] voice input audio pipeline close failed", self.display_name)
        self._voice_input_audio_pipeline = VoiceInputAudioPipeline()
        self._asr_route_mode = next_route_mode
        self._asr_required = True
        self._asr_received_audio = False
        self._asr_turn_prepared = False
        self._asr_accepted_final_keys.clear()
        self._asr_reserved_final_key = None
        for task_name in (
            "_asr_transport_task",
            "_asr_warm_expiry_task",
            "_asr_final_watchdog_task",
        ):
            task = getattr(self, task_name, None)
            setattr(self, task_name, None)
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        self._asr_session_factory = None
        self._asr_transport_selection = None
        self._asr_pending_speech_confirmed = False
        self._asr_pending_detector_candidate = None
        self._asr_audio_sequence = 0
        self._asr_sealed_turn_token = None
        if asr_session is not None:
            try:
                await asr_session.close()
            except Exception:
                logger.warning("[%s] independent ASR close failed", self.display_name)
        close_tasks = tuple(self._asr_close_tasks)
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
    async def submit(
        self,
        frame: ProcessedVoiceFrame,
        *,
        ingress_token: VoiceIngressToken,
    ) -> bool:
        """Submit one normalized frame to the independent-ASR hard route."""

        self._ensure_asr_runtime_state()
        if self._asr_route_mode != "independent":
            self._asr_route_mode = "blocked"
            return False
        if not self._ingress_token_matches(ingress_token):
            return False

        pcm16 = frame.pcm16
        sample_rate_hz = frame.sample_rate_hz
        speech_probability = frame.speech_probability
        rnnoise_available = frame.rnnoise_available

        try:
            lifecycle = self._asr_lifecycle
            detector = self._asr_detector

            def ingress_is_current() -> bool:
                return bool(
                    lifecycle is not None
                    and self._asr_lifecycle is lifecycle
                    and self._ingress_token_matches(ingress_token)
                )

            if lifecycle is not None and detector is not None:
                submit_audio = getattr(detector, "submit_audio", None)
                uses_smart_turn = (
                    lifecycle.provider_policy.endpoint_authority == "smart_turn"
                )
                if (
                    uses_smart_turn
                    and callable(submit_audio)
                ):
                    detector_submit_started_at = time.perf_counter()
                    submitted = await submit_audio(
                        pcm16,
                        ingress_token=ingress_token,
                        sample_rate_hz=sample_rate_hz,
                        speech_probability=speech_probability,
                        rnnoise_available=bool(rnnoise_available),
                    )
                    lifecycle.metrics.detector_submit_latency_ms = int(
                        (time.perf_counter() - detector_submit_started_at) * 1_000
                    )
                    lifecycle.metrics.detector_queue_audio_ms = (
                        detector.queued_audio_ms
                    )
                    lifecycle.metrics.detector_queue_high_water_ms = max(
                        lifecycle.metrics.detector_queue_high_water_ms,
                        detector.queued_audio_ms,
                    )
                    lifecycle.metrics.smart_turn_inference_ms = (
                        detector.smart_turn_evaluation_ms
                    )
                    lifecycle.metrics.smart_turn_stale_result_count = (
                        detector.smart_turn_stale_result_count
                    )
                    lifecycle.metrics.smart_turn_coalesced_evaluation_count = (
                        detector.smart_turn_coalesced_evaluation_count
                    )
                    if not ingress_is_current():
                        return True
                    if submitted.status is DetectorSubmitStatus.BACKPRESSURE:
                        lifecycle.metrics.detector_overflow_count += 1
                        await self._handle_audio_ingress_backpressure(ingress_token)
                        return True
                    if (
                        submitted.status
                        in {DetectorSubmitStatus.CLOSED, DetectorSubmitStatus.FAILED}
                        or not submitted.endpointing_available
                    ):
                        await self._handle_independent_asr_error(
                            self._asr_session_epoch,
                            self._asr_provider or "unknown",
                            status_code="ASR_ENDPOINTING_FAILED",
                        )
                        return True
                    if not submitted.throttle_available:
                        lifecycle.enable_independent_asr_fail_open()
                    if (
                        submitted.identity is not None
                        and (
                            not submitted.throttle_available
                            or not self._voice_input_resource_optimization_enabled
                        )
                        and lifecycle.snapshot.state
                        in {
                            VoiceLifecycleState.LOCAL_LISTEN,
                            VoiceLifecycleState.WARM_IDLE,
                            VoiceLifecycleState.DEEP_SLEEP,
                        }
                    ):
                        await detector.force_speech_started(submitted.identity)
                else:
                    detector_result = await detector.feed(
                        pcm16,
                        speech_probability=speech_probability,
                        rnnoise_available=rnnoise_available,
                    )
                    if not ingress_is_current():
                        return True
                    if not detector_result.endpointing_available:
                        await self._handle_independent_asr_error(
                            self._asr_session_epoch,
                            self._asr_provider or "unknown",
                            status_code="ASR_ENDPOINTING_FAILED",
                        )
                        return True
                    if not detector_result.throttle_available:
                        lifecycle.enable_independent_asr_fail_open()
                    else:
                        for event in detector_result.events:
                            await self._handle_independent_asr_activity(
                                event,
                                self._asr_session_epoch,
                            )
                            if not ingress_is_current():
                                return True
                    if (
                        not detector_result.throttle_available
                        or not self._voice_input_resource_optimization_enabled
                    ) and lifecycle.snapshot.state in {
                        VoiceLifecycleState.LOCAL_LISTEN,
                        VoiceLifecycleState.WARM_IDLE,
                        VoiceLifecycleState.DEEP_SLEEP,
                    }:
                        await self._handle_independent_asr_activity(
                            SpeechActivityEvent.SPEECH_STARTED,
                            self._asr_session_epoch,
                        )
                        if not ingress_is_current():
                            return True
            if lifecycle is not None and not ingress_is_current():
                return True
            decision = (
                lifecycle.accept_audio(pcm16, sample_rate_hz=sample_rate_hz)
                if lifecycle is not None
                else None
            )
            if decision is not None and decision.disposition is AudioDisposition.BLOCK:
                if decision.backpressure:
                    await self._handle_audio_ingress_backpressure(ingress_token)
                return True
            if decision is not None and decision.disposition in {
                AudioDisposition.BUFFER,
                AudioDisposition.SUPPRESS,
            }:
                if (
                    lifecycle is not None
                    and lifecycle.snapshot.state
                    in {
                        VoiceLifecycleState.PREWARMING,
                        VoiceLifecycleState.BACKOFF,
                    }
                    and (
                        self._asr_session is None
                        or not getattr(self._asr_session, "is_ready", True)
                    )
                ):
                    self._ensure_transport_restart_task()
                return True
            if lifecycle is None or detector is None:
                await self._handle_independent_asr_error(
                    self._asr_session_epoch,
                    self._asr_provider or "unknown",
                    status_code="ASR_BLOCKED_ENDPOINTING",
                )
                return True
            turn_token = self._capture_turn_token(lifecycle)
            if (
                lifecycle.snapshot.state is not VoiceLifecycleState.ACTIVE
                or not self._asr_endpointing_ready(lifecycle, detector, turn_token)
            ):
                await self._handle_independent_asr_error(
                    self._asr_session_epoch,
                    self._asr_provider or "unknown",
                    status_code="ASR_BLOCKED_ENDPOINTING",
                )
                return True
            asr_session = self._asr_session
            if asr_session is None or not getattr(asr_session, "is_ready", True):
                if lifecycle is None:
                    await self._handle_independent_asr_error(
                        self._asr_session_epoch,
                        self._asr_provider or "unknown",
                    )
                    return True
                self._ensure_transport_restart_task()
                return True
            payload = (
                decision.pre_roll
                if decision is not None
                and decision.disposition is AudioDisposition.FORWARD_WITH_PRE_ROLL
                else pcm16
            )
            if not payload:
                return True
            if not ingress_is_current():
                return True
            if self._asr_audio_dispatcher.active_turn != turn_token:
                if not self._activate_asr_audio_dispatcher(lifecycle, turn_token):
                    await self._handle_independent_asr_error(
                        self._asr_session_epoch,
                        self._asr_provider or "unknown",
                        status_code="ASR_AUDIO_ORDERING_FAILED",
                    )
                    return True
            self._asr_audio_sequence += 1
            if not self._asr_audio_dispatcher.enqueue_audio(
                turn_token,
                asr_session,
                payload,
                sample_rate_hz=sample_rate_hz,
                sequence_no=self._asr_audio_sequence,
            ):
                await self._handle_independent_asr_error(
                    self._asr_session_epoch,
                    self._asr_provider or "unknown",
                    status_code="ASR_AUDIO_ORDERING_FAILED",
                )
                return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._asr_received_audio = True
            status_code = (
                "ASR_STREAM_BACKPRESSURE"
                if str(exc).startswith("ASR_STREAM_BACKPRESSURE:")
                else "ASR_INDEPENDENT_STREAM_FAILED"
            )
            if (
                status_code == "ASR_STREAM_BACKPRESSURE"
                and self._asr_lifecycle is not None
            ):
                self._asr_lifecycle.metrics.queue_backpressure_count += 1
            await self._handle_independent_asr_error(
                self._asr_session_epoch,
                self._asr_provider or "unknown",
                status_code=status_code,
            )
            return True

        return True

    def _ensure_transport_restart_task(self) -> None:
        task = self._asr_transport_task
        if task is not None and not task.done():
            return
        task = asyncio.create_task(
            self.restart_transport(),
            name="independent-asr-transport-restart",
        )
        self._asr_transport_task = task

    async def connect_transport(self) -> None:
        """Connect only the independent ASR transport."""

        await self.restart_transport(max_attempts=1)

    async def restart_transport(self, *, max_attempts: int = 3) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        async with self._asr_transport_lock:
            if self._asr_route_mode != "independent":
                return
            existing = self._asr_session
            if existing is not None and getattr(existing, "is_ready", True):
                return
            factory = self._asr_session_factory
            selection = self._asr_transport_selection
            lifecycle = self._asr_lifecycle
            if factory is None or selection is None or lifecycle is None:
                await self._handle_independent_asr_error(
                    self._asr_session_epoch,
                    self._asr_provider or "unknown",
                )
                return

            for attempt in range(max_attempts):
                if lifecycle.snapshot.state is VoiceLifecycleState.BACKOFF:
                    lifecycle.transition(VoiceLifecycleEvent.RETRY)
                    lifecycle.metrics.reconnect_count += 1
                    await self._send_asr_lifecycle_state(VoiceLifecycleState.PREWARMING)
                candidate = None
                try:
                    connect_started_at = time.monotonic()
                    candidate = factory(selection)
                    await candidate.connect()
                    if self._asr_route_mode != "independent":
                        await candidate.close()
                        return
                    self._asr_session = candidate
                    self._asr_last_provider_wire_audio_ms = 0
                    lifecycle.invalidate_transport()
                    lifecycle.metrics.connect_latency_ms = int(
                        (time.monotonic() - connect_started_at) * 1_000
                    )
                    if (
                        self._asr_pending_speech_confirmed
                        and lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING
                    ):
                        detector = self._asr_detector
                        turn_token = self._capture_turn_token(lifecycle)
                        if (
                            detector is None
                            or not self._asr_endpointing_ready(
                                lifecycle,
                                detector,
                                turn_token,
                            )
                        ):
                            await self._handle_independent_asr_error(
                                self._asr_session_epoch,
                                self._asr_provider or "unknown",
                                status_code="ASR_BLOCKED_ENDPOINTING",
                            )
                            return
                        lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
                        self._asr_pending_speech_confirmed = False
                        self._asr_turn_audio_started_at = time.monotonic()
                        self._asr_first_partial_recorded = False
                        await self._send_asr_lifecycle_state(VoiceLifecycleState.ACTIVE)
                        payload = lifecycle.drain_active_start_audio()
                        await self._prepare_independent_asr_turn(
                            self._asr_session_epoch
                        )
                        if not self._activate_asr_audio_dispatcher(
                            lifecycle,
                            turn_token,
                            buffered_pcm16=payload,
                        ):
                            await self._handle_independent_asr_error(
                                self._asr_session_epoch,
                                self._asr_provider or "unknown",
                                status_code="ASR_AUDIO_ORDERING_FAILED",
                            )
                            return
                    return
                except asyncio.CancelledError:
                    if candidate is not None:
                        await candidate.close()
                    raise
                except Exception:
                    if candidate is not None:
                        try:
                            await candidate.close()
                        except Exception:
                            pass
                    if lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING:
                        lifecycle.transition(VoiceLifecycleEvent.CONNECT_FAILED)
                        await self._send_asr_lifecycle_state(VoiceLifecycleState.BACKOFF)
                    if attempt + 1 < max_attempts:
                        await asyncio.sleep(min(1.0, 0.25 * (2**attempt)))
                        continue
            if lifecycle.snapshot.state is VoiceLifecycleState.BACKOFF:
                lifecycle.transition(VoiceLifecycleEvent.RETRIES_EXHAUSTED)
            self._asr_route_mode = "blocked"
            await self._send_asr_lifecycle_state(VoiceLifecycleState.BLOCKED)
            await self._send_asr_status(
                "ASR_INDEPENDENT_FAILED",
                self._asr_provider or "unknown",
            )

    async def abort_transport(self, reason: str) -> None:
        """Invalidate provider I/O before closing a live transport."""

        self._asr_audio_generation += 1
        self._asr_transcript_dispatcher.invalidate_all()
        self._asr_detector_dispatcher.invalidate_all()
        self._asr_audio_dispatcher.abort()
        self._asr_reserved_final_key = None
        self._asr_sealed_turn_token = None
        self._asr_turn_prepared = False
        self._asr_received_audio = False
        self._asr_pending_speech_confirmed = False
        self._asr_pending_detector_candidate = None
        self._asr_audio_sequence = 0
        self._asr_turn_endpointed_at = None
        self._asr_accepted_final_keys.clear()
        lease, self._asr_smart_turn_lease = self._asr_smart_turn_lease, None
        if lease is not None:
            await lease.release()
        for task_name in (
            "_asr_transport_task",
            "_asr_warm_expiry_task",
            "_asr_final_watchdog_task",
        ):
            task = getattr(self, task_name, None)
            setattr(self, task_name, None)
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        asr_session, self._asr_session = self._asr_session, None
        lifecycle = self._asr_lifecycle
        if lifecycle is not None:
            lifecycle.metrics.asr_abort_discarded_command_count = (
                self._asr_audio_dispatcher.asr_abort_discarded_command_count
            )
            lifecycle.invalidate_transport()
        if asr_session is not None:
            try:
                await asr_session.close()
            except Exception:
                logger.warning(
                    "[%s] independent ASR abort failed reason=%s",
                    self.display_name,
                    reason,
                )

    async def close_transport_only(self) -> None:
        """Enter deep sleep while preserving microphone detection."""

        warm_task = self._asr_warm_expiry_task
        if warm_task is not None and warm_task is not asyncio.current_task():
            warm_task.cancel()
        self._asr_warm_expiry_task = None
        asr_session, self._asr_session = self._asr_session, None
        lifecycle = self._asr_lifecycle
        if lifecycle is not None:
            lifecycle.invalidate_transport()
            if lifecycle.snapshot.state in {
                VoiceLifecycleState.LOCAL_LISTEN,
                VoiceLifecycleState.WARM_IDLE,
            }:
                lifecycle.transition(VoiceLifecycleEvent.WARM_EXPIRED)
                await self._send_asr_lifecycle_state(VoiceLifecycleState.DEEP_SLEEP)
        if asr_session is not None:
            try:
                await asr_session.close()
            except Exception:
                logger.warning(
                    "[%s] independent ASR transport-only close failed",
                    self.display_name,
                )

    async def close_voice_input_session(self) -> None:
        """Release the complete voice-input session after user stop."""

        await self._close_independent_asr(next_route_mode="blocked")

    def _schedule_transport_warm_expiry(self, epoch: int) -> None:
        task = self._asr_warm_expiry_task
        if task is not None:
            task.cancel()
        lifecycle = self._asr_lifecycle
        if lifecycle is None or not self._voice_input_resource_optimization_enabled:
            return
        ttl_ms = lifecycle.provider_policy.warm_transport_ms

        async def expire() -> None:
            try:
                await asyncio.sleep(ttl_ms / 1_000)
                if epoch != self._asr_session_epoch:
                    return
                current = self._asr_lifecycle
                if (
                    current is not None
                    and current.snapshot.state
                    in {
                        VoiceLifecycleState.LOCAL_LISTEN,
                        VoiceLifecycleState.WARM_IDLE,
                    }
                ):
                    await self.close_transport_only()
            except asyncio.CancelledError:
                return

        self._asr_warm_expiry_task = asyncio.create_task(
            expire(),
            name="independent-asr-warm-expiry",
        )

    def _schedule_provider_final_watchdog(
        self,
        epoch: int,
        lifecycle: VoiceInputLifecycleController,
        sealed_token: VoiceTransportToken,
    ) -> None:
        task = self._asr_final_watchdog_task
        if task is not None:
            task.cancel()
        timeout_ms = lifecycle.provider_policy.provider_final_timeout_ms

        async def expire() -> None:
            try:
                await asyncio.sleep(timeout_ms / 1_000)
                if (
                    epoch != self._asr_session_epoch
                    or self._asr_lifecycle is not lifecycle
                    or self._asr_sealed_turn_token != sealed_token
                    or lifecycle.snapshot.state is not VoiceLifecycleState.DRAINING
                ):
                    return
                await self._handle_independent_asr_error(
                    epoch,
                    self._asr_provider or "unknown",
                    status_code="ASR_PROVIDER_FINAL_TIMEOUT",
                )
            except asyncio.CancelledError:
                return

        self._asr_final_watchdog_task = asyncio.create_task(
            expire(),
            name="independent-asr-provider-final-watchdog",
        )

    def _sync_provider_wire_metrics(
        self,
        asr_session: Any,
        *,
        fallback_audio_bytes: int = 0,
    ) -> None:
        lifecycle = self._asr_lifecycle
        if lifecycle is None:
            return
        cumulative_ms = getattr(asr_session, "provider_wire_audio_ms", None)
        if isinstance(cumulative_ms, int) and not isinstance(cumulative_ms, bool):
            delta_ms = max(0, cumulative_ms - self._asr_last_provider_wire_audio_ms)
            self._asr_last_provider_wire_audio_ms = max(
                self._asr_last_provider_wire_audio_ms,
                cumulative_ms,
            )
            if delta_ms:
                lifecycle.record_provider_wire_audio(delta_ms)
            return
        if (
            lifecycle.provider_policy.transport == "streaming"
            and fallback_audio_bytes > 0
        ):
            lifecycle.record_provider_wire_audio(
                fallback_audio_bytes * 1_000 // (16_000 * 2)
            )

    async def _process_microphone_audio(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
    ) -> ProcessedVoiceFrame:
        """Normalize microphone PCM without consulting an Omni session."""

        self._ensure_asr_runtime_state()
        return await self._voice_input_audio_pipeline.process(
            pcm16,
            sample_rate_hz=sample_rate_hz,
        )

    def _invalidate_voice_pcm_sync(self, reason: str) -> None:
        """Apply the synchronous half of every authoritative PCM barrier."""

        self._asr_audio_generation += 1
        self._asr_transcript_dispatcher.invalidate_all()
        self._asr_reserved_final_key = None
        self._asr_sealed_turn_token = None
        self._asr_turn_prepared = False
        self._asr_received_audio = False
        self._asr_pending_speech_confirmed = False
        lifecycle = self._asr_lifecycle
        if lifecycle is not None:
            lifecycle.invalidate_audio()

    async def _apply_voice_lease_state(
        self,
        *,
        owner: str,
        hard_muted: bool,
        focus_suppressed: bool,
        suppressed: bool,
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
        self._voice_input_suppressed = suppressed
        self._invalidate_voice_pcm_sync(reason)

        lifecycle = self._asr_lifecycle
        if lifecycle is not None:
            if (
                owner == "game"
                and suppressed
                and lifecycle.snapshot.state not in {
                    VoiceLifecycleState.OFF,
                    VoiceLifecycleState.BLOCKED,
                    VoiceLifecycleState.SUSPENDED,
                }
            ):
                lifecycle.transition(VoiceLifecycleEvent.GAME_TAKEOVER)
            elif (
                owner == "core"
                and lifecycle.snapshot.state is VoiceLifecycleState.SUSPENDED
            ):
                lifecycle.transition(VoiceLifecycleEvent.GAME_RELEASED)

        detector = self._asr_detector
        if detector is not None:
            try:
                await detector.reset()
            except Exception:
                logger.warning(
                    "[%s] detector reset failed during lease sync",
                    self.display_name,
                )

        current = (owner, hard_muted, focus_suppressed)
        should_abort = force_abort or self._voice_lease_requires_abort or previous != current
        self._voice_lease_requires_abort = False
        if should_abort:
            await self.abort_transport(reason)
        lifecycle = self._asr_lifecycle
        if lifecycle is not None:
            await self._send_asr_lifecycle_state(lifecycle.snapshot.state)

    async def _handle_independent_asr_activity(
        self,
        event: SpeechActivityEvent,
        epoch: int,
    ) -> None:
        if epoch != self._asr_session_epoch:
            return
        lifecycle = self._asr_lifecycle
        if (
            lifecycle is not None
            and lifecycle.snapshot.state is VoiceLifecycleState.DRAINING
            and event
            in {
                SpeechActivityEvent.SPEECH_STARTED,
                SpeechActivityEvent.SPEECH_RESUMED,
            }
        ):
            lifecycle.mark_pending_turn_speech()
            return
        if lifecycle is not None and event in {
            SpeechActivityEvent.SPEECH_STARTED,
            SpeechActivityEvent.SPEECH_RESUMED,
        }:
            previous_state = lifecycle.snapshot.state
            state = lifecycle.snapshot.state
            if state in {
                VoiceLifecycleState.LOCAL_LISTEN,
                VoiceLifecycleState.DEEP_SLEEP,
                VoiceLifecycleState.WARM_IDLE,
            }:
                warm_task = self._asr_warm_expiry_task
                if warm_task is not None:
                    warm_task.cancel()
                    self._asr_warm_expiry_task = None
                if state is VoiceLifecycleState.WARM_IDLE:
                    lifecycle.metrics.warm_hit_count += 1
                lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
                state = lifecycle.snapshot.state
            if state is VoiceLifecycleState.PREWARMING:
                if not await self._ensure_smart_turn_ready(lifecycle, epoch):
                    return
                asr_session = self._asr_session
                if asr_session is not None and getattr(asr_session, "is_ready", True):
                    lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
                else:
                    self._asr_pending_speech_confirmed = True
            if lifecycle.snapshot.state is not previous_state:
                await self._send_asr_lifecycle_state(lifecycle.snapshot.state)
            if (
                lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
                and previous_state is not VoiceLifecycleState.ACTIVE
            ):
                self._asr_turn_audio_started_at = time.monotonic()
                self._asr_first_partial_recorded = False
        if event not in {
            SpeechActivityEvent.SPEECH_STARTED,
            SpeechActivityEvent.SPEECH_RESUMED,
        } or self._asr_turn_prepared:
            return
        if (
            lifecycle is not None
            and lifecycle.snapshot.state is not VoiceLifecycleState.ACTIVE
        ):
            return

        await self._prepare_independent_asr_turn(epoch)

    async def _prepare_independent_asr_turn(self, epoch: int) -> None:
        """Prepare an identified turn without deciding its endpoint."""

        if epoch != self._asr_session_epoch or self._asr_turn_prepared:
            return

        lifecycle = self._asr_lifecycle
        if lifecycle is None or lifecycle.snapshot.state is not VoiceLifecycleState.ACTIVE:
            return
        turn_token = self._capture_turn_token(lifecycle)
        final_key = FinalKey.from_turn(turn_token)
        if not self._asr_transcript_dispatcher.try_reserve(final_key):
            await self._handle_independent_asr_error(
                epoch,
                self._asr_provider or "unknown",
                status_code="ASR_CORE_TRANSCRIPT_BACKPRESSURE",
            )
            return
        self._asr_reserved_final_key = final_key

        self._asr_turn_prepared = True
        try:
            accepted = await self._callbacks.on_prepare_turn(turn_token)
        except Exception:
            accepted = False
            logger.warning(
                "[%s] independent ASR turn preparation failed",
                self.display_name,
            )
        if epoch != self._asr_session_epoch or not accepted:
            self._asr_transcript_dispatcher.release(final_key)
            if self._asr_reserved_final_key == final_key:
                self._asr_reserved_final_key = None
            self._asr_turn_prepared = False

    async def _handle_independent_asr_endpoint(self, epoch: int) -> None:
        """Seal the current turn immediately at its semantic endpoint."""

        if epoch != self._asr_session_epoch:
            return
        lifecycle = self._asr_lifecycle
        if lifecycle is None:
            return
        if lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE:
            turn_token = self._capture_turn_token(lifecycle)
            detector = self._asr_detector
            if not self._asr_endpointing_ready(lifecycle, detector, turn_token):
                await self._handle_independent_asr_error(
                    epoch,
                    self._asr_provider or "unknown",
                    status_code="ASR_BLOCKED_ENDPOINTING",
                )
                return
            final_key = FinalKey.from_turn(turn_token)
            if not self._asr_transcript_dispatcher.try_reserve(final_key):
                await self._handle_independent_asr_error(
                    epoch,
                    self._asr_provider or "unknown",
                    status_code="ASR_CORE_TRANSCRIPT_BACKPRESSURE",
                )
                return
            self._asr_reserved_final_key = final_key
            lifecycle.transition(VoiceLifecycleEvent.TURN_SEALED)
            self._asr_sealed_turn_token = self._capture_transport_token(lifecycle)
            self._asr_turn_endpointed_at = time.monotonic()
            self._schedule_provider_final_watchdog(
                epoch,
                lifecycle,
                self._asr_sealed_turn_token,
            )
            await self._send_asr_lifecycle_state(VoiceLifecycleState.DRAINING)

    async def _activate_pending_independent_turn(self, epoch: int) -> None:
        """Start the pending turn after the previous final completes."""

        if epoch != self._asr_session_epoch:
            return
        lifecycle = self._asr_lifecycle
        if lifecycle is None or not lifecycle.has_pending_turn:
            if lifecycle is not None:
                lifecycle.discard_unconfirmed_pending_audio()
            return
        payload = lifecycle.begin_pending_turn()
        if not payload:
            return
        self._asr_turn_audio_started_at = time.monotonic()
        self._asr_first_partial_recorded = False
        if not await self._ensure_smart_turn_ready(lifecycle, epoch):
            return
        await self._send_asr_lifecycle_state(VoiceLifecycleState.ACTIVE)
        await self._prepare_independent_asr_turn(epoch)
        asr_session = self._asr_session
        if (
            epoch != self._asr_session_epoch
            or asr_session is None
            or not getattr(asr_session, "is_ready", True)
        ):
            await self._handle_independent_asr_error(
                epoch,
                self._asr_provider or "unknown",
            )
            return
        detector = self._asr_detector
        turn_token = self._capture_turn_token(lifecycle)
        if not self._asr_endpointing_ready(lifecycle, detector, turn_token):
            await self._handle_independent_asr_error(
                epoch,
                self._asr_provider or "unknown",
                status_code="ASR_BLOCKED_ENDPOINTING",
            )
            return
        pending_candidate = self._asr_pending_detector_candidate
        self._asr_pending_detector_candidate = None
        if pending_candidate is not None:
            bound = await detector.bind_candidate(pending_candidate, turn_token)
            if bound is None:
                await self._handle_independent_asr_error(
                    epoch,
                    self._asr_provider or "unknown",
                    status_code="ASR_ENDPOINTING_FAILED",
                )
                return
        if not self._activate_asr_audio_dispatcher(
            lifecycle,
            turn_token,
            buffered_pcm16=payload,
        ):
            await self._handle_independent_asr_error(
                epoch,
                self._asr_provider or "unknown",
                status_code="ASR_AUDIO_ORDERING_FAILED",
            )
            return
        self._asr_received_audio = True
        self._asr_audio_bytes += len(payload)

    async def _send_independent_asr_preview(self, text: str, epoch: int) -> None:
        """Send display-only ASR partials without writing conversation history."""

        clean = str(text or "").strip()
        if not clean or epoch != self._asr_session_epoch:
            return
        lifecycle = self._asr_lifecycle
        if (
            lifecycle is not None
            and not self._asr_first_partial_recorded
            and self._asr_turn_audio_started_at is not None
        ):
            lifecycle.metrics.first_partial_latency_ms = int(
                (time.monotonic() - self._asr_turn_audio_started_at) * 1_000
            )
            self._asr_first_partial_recorded = True
        try:
            await self._callbacks.on_partial(clean, epoch)
        except Exception:
            logger.debug(
                "[%s] independent ASR preview delivery failed",
                self.display_name,
            )

    async def _handle_independent_asr_final(
        self,
        text: str,
        epoch: int,
        provider: str,
    ) -> None:
        clean = str(text or "").strip()
        if epoch != self._asr_session_epoch:
            return

        lifecycle_ref: VoiceInputLifecycleController | None = None
        detector_ref: DetectorRuntime | None = None
        has_pending_turn = False
        envelope: TranscriptEnvelope | None = None
        accepted_turn_token: VoiceTurnToken | None = None
        async with self._asr_final_lock:
            if epoch != self._asr_session_epoch:
                return
            lifecycle_ref = self._asr_lifecycle
            sealed_token = self._asr_sealed_turn_token
            if (
                lifecycle_ref is None
                or sealed_token is None
                or lifecycle_ref.snapshot.state is not VoiceLifecycleState.DRAINING
                or not self._transport_token_matches(sealed_token, lifecycle_ref)
            ):
                return
            final_key = FinalKey.from_turn(sealed_token.turn)
            if not self._asr_transcript_dispatcher.try_reserve(final_key):
                return
            if not self._accept_final_key(final_key):
                return
            if self._asr_turn_endpointed_at is not None:
                lifecycle_ref.metrics.final_latency_ms = int(
                    (time.monotonic() - self._asr_turn_endpointed_at) * 1_000
                )
            has_pending_turn = lifecycle_ref.has_pending_turn
            accepted_turn_token = sealed_token.turn
            lifecycle_ref.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
            detector_ref = self._asr_detector
            self._asr_turn_prepared = False
            self._asr_received_audio = False
            self._asr_sealed_turn_token = None
            self._asr_turn_endpointed_at = None
            self._asr_reserved_final_key = None
            watchdog = self._asr_final_watchdog_task
            self._asr_final_watchdog_task = None
            if watchdog is not None and watchdog is not asyncio.current_task():
                watchdog.cancel()
            if clean:
                envelope = TranscriptEnvelope(
                    turn_token=sealed_token.turn,
                    provider=provider,
                    text=clean,
                )
            else:
                lifecycle_ref.metrics.false_wake_count += 1
                self._asr_transcript_dispatcher.release(final_key)
            if not has_pending_turn:
                self._schedule_transport_warm_expiry(epoch)

        assert lifecycle_ref is not None
        assert accepted_turn_token is not None
        lease = self._asr_smart_turn_lease
        if lease is not None and lease.token == accepted_turn_token:
            self._asr_smart_turn_lease = None
            await lease.release()
        if envelope is not None:
            self._asr_transcript_dispatcher.submit(envelope)
        await self._send_asr_lifecycle_state(VoiceLifecycleState.WARM_IDLE)

        await self._activate_pending_independent_turn(epoch)
        if (
            detector_ref is not None
            and self._asr_lifecycle is lifecycle_ref
            and self._asr_detector is detector_ref
        ):
            try:
                await detector_ref.release_deferred_turn()
            except Exception:
                await self._handle_independent_asr_error(
                    epoch,
                    self._asr_provider or provider,
                    status_code="ASR_ENDPOINTING_FAILED",
                )

    async def _dispatch_asr_transcript_envelope(
        self,
        envelope: TranscriptEnvelope,
    ) -> None:
        ingress_token = envelope.turn_token.ingress
        if not self._ingress_token_matches(ingress_token):
            return
        try:
            await self._callbacks.on_final(
                VoiceTranscriptEvent(
                    turn_token=envelope.turn_token,
                    provider=envelope.provider,
                    text=envelope.text,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._send_asr_status(
                "ASR_INDEPENDENT_INJECTION_FAILED",
                envelope.provider,
            )

    async def _wait_asr_transcript_dispatch_idle(self) -> None:
        await self._asr_transcript_dispatcher.wait_idle()

    async def _handle_independent_asr_error(
        self,
        epoch: int,
        provider: str,
        *,
        status_code: str = "ASR_INDEPENDENT_FAILED",
    ) -> None:
        if epoch != self._asr_session_epoch:
            return
        # The provider callback that reported failure must not be allowed to
        # deliver a queued final into the surviving Omni session.
        self._asr_session_epoch += 1
        self._asr_audio_generation += 1
        self._asr_transcript_dispatcher.invalidate_all()
        self._asr_detector_dispatcher.invalidate_all()
        self._asr_audio_dispatcher.abort()
        asr_session = self._asr_session
        self._asr_session = None
        self._asr_provider = None
        self._asr_required = True
        self._asr_route_mode = "blocked"
        watchdog, self._asr_final_watchdog_task = (
            self._asr_final_watchdog_task,
            None,
        )
        if watchdog is not None and watchdog is not asyncio.current_task():
            watchdog.cancel()
        await self._send_asr_lifecycle_state(VoiceLifecycleState.BLOCKED)
        lifecycle = self._asr_lifecycle
        self._asr_lifecycle = None
        if lifecycle is not None:
            lifecycle.stop()
        detector, self._asr_detector = self._asr_detector, None
        self._asr_smart_turn_lease = None
        if detector is not None:
            task = asyncio.create_task(detector.close())
            self._asr_close_tasks.add(task)
            task.add_done_callback(self._asr_close_tasks.discard)
        self._asr_received_audio = False
        self._asr_turn_prepared = False
        self._asr_accepted_final_keys.clear()
        self._asr_reserved_final_key = None
        self._asr_sealed_turn_token = None
        if asr_session is not None:
            task = asyncio.create_task(self._close_asr_session(asr_session))
            self._asr_close_tasks.add(task)
            task.add_done_callback(self._asr_close_tasks.discard)
        try:
            await self._callbacks.on_failure(
                AsrFailureEvent(
                    code=status_code,
                    provider=provider,
                    session_epoch=self._asr_session_epoch,
                )
            )
        except Exception:
            logger.debug(
                "[%s] independent ASR failure callback failed",
                self.display_name,
            )
        await self._send_asr_status(status_code, provider)

    async def _close_asr_session(self, asr_session: Any) -> None:
        try:
            await asr_session.close()
        except Exception:
            logger.warning(
                "[%s] independent ASR background close failed",
                self.display_name,
            )

    async def _send_asr_status(self, code: str, provider: str) -> None:
        try:
            await self._callbacks.on_status(code, provider)
        except Exception:
            logger.debug(
                "[%s] independent ASR status delivery failed",
                self.display_name,
            )

    async def _send_asr_lifecycle_state(self, state: VoiceLifecycleState) -> None:
        try:
            await self._callbacks.on_lifecycle(
                state,
                self._asr_provider or "",
                self._asr_route_mode,
            )
        except Exception:
            logger.debug(
                "[%s] ASR lifecycle status delivery failed",
                self.display_name,
            )
