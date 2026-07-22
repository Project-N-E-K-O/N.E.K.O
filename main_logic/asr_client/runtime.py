"""Provider-neutral independent-ASR runtime with explicit Core callbacks."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from main_logic.asr_client import (
    _attach_partial_callback,
    _create_asr_session_from_selection,
    _resolve_asr_selection,
)
from main_logic.voice_turn.contracts import (
    AsrFailureEvent,
    AsrLifecycleNotification,
    AsrStatusEvent,
    AsrSubmitResult,
    AsrSubmitStatus,
    SpeechActivityEvent,
    VoicePartialEvent,
    VoiceTranscriptEvent,
    VoiceTurnToken,
)
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame

from ._infra import logger
from .activity_evidence import RnnoiseEvidence
from .audio import AsrAudioDispatcher
from ._registry_meta import AsrProviderAvailability
from .detector import (
    AsrDetectorDispatcher,
    CoreDetectorEventEnvelope,
    DetectorActivityEvent,
    DetectorPrewarmEvent,
    DetectorTransportPrewarmEvent,
    DetectorRuntimeEvent,
    DetectorSubmitStatus,
    DetectorTurnEvent,
    ProviderCandidateFence,
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
    on_partial: Callable[[VoicePartialEvent], Awaitable[None]]
    on_final: Callable[[VoiceTranscriptEvent], Awaitable[None]]
    on_failure: Callable[[AsrFailureEvent], Awaitable[None]]
    on_status: Callable[[AsrStatusEvent], Awaitable[None]]
    on_lifecycle: Callable[[AsrLifecycleNotification], Awaitable[None]]


class IndependentAsrRuntime:
    """Own one independent ASR session without reading Core manager state."""

    def __init__(self, callbacks: AsrRuntimeCallbacks) -> None:
        self._callbacks = callbacks
        self._init_asr_runtime_state()

    @property
    def display_name(self) -> str:
        return self._callbacks.display_name()

    async def close(self) -> None:
        await self._close_independent_asr()

    def capture_ingress_token(
        self,
        *,
        connection_id: str,
        lease_generation: int,
        route_generation: int,
    ) -> VoiceIngressToken:
        return VoiceIngressToken(
            session_epoch=self._asr_session_epoch,
            connection_id=connection_id,
            lease_generation=lease_generation,
            route_generation=route_generation,
            audio_generation=self._asr_audio_generation,
        )

    async def suspend(self, reason: str) -> None:
        lifecycle = self._asr_lifecycle
        if lifecycle is not None and lifecycle.snapshot.state not in {
            VoiceLifecycleState.OFF,
            VoiceLifecycleState.BLOCKED,
            VoiceLifecycleState.SUSPENDED,
        }:
            lifecycle.transition(VoiceLifecycleEvent.GAME_TAKEOVER)
        await self.abort(reason)

    async def resume(self, reason: str) -> None:
        del reason
        lifecycle = self._asr_lifecycle
        if lifecycle is not None and (
            lifecycle.snapshot.state is VoiceLifecycleState.SUSPENDED
        ):
            lifecycle.transition(VoiceLifecycleEvent.GAME_RELEASED)
            await self._send_asr_lifecycle_state(lifecycle.snapshot.state)

    async def abort(self, reason: str) -> None:
        if reason == "ingress_backpressure":
            token = self._asr_current_ingress_token
            if token is not None and self._ingress_token_matches(token):
                await self._handle_audio_ingress_backpressure(token)
                return
        lifecycle = self._asr_lifecycle
        if lifecycle is not None:
            lifecycle.invalidate_audio()
        await self._abort_transport(reason)
        if reason == "ingress_backpressure":
            await self._send_asr_status(
                "ASR_INGRESS_BACKPRESSURE",
                self._asr_provider or "unknown",
            )
        detector = self._asr_detector
        if detector is not None:
            try:
                await detector.reset()
            except Exception:
                logger.warning(
                    "[%s] detector reset failed during voice abort",
                    self.display_name,
                )
        lifecycle = self._asr_lifecycle
        if lifecycle is not None:
            await self._send_asr_lifecycle_state(lifecycle.snapshot.state)

    async def wait_transcript_idle(self) -> None:
        await self._asr_transcript_dispatcher.wait_idle()

    def _init_asr_runtime_state(self) -> None:
        self._asr_session = None
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
        self._asr_session_factory = None
        self._asr_transport_selection = None
        self._asr_transport_task: asyncio.Task[None] | None = None
        self._asr_transport_lock = asyncio.Lock()
        self._asr_warm_expiry_task: asyncio.Task[None] | None = None
        self._asr_final_watchdog_task: asyncio.Task[None] | None = None
        self._asr_pending_speech_confirmed = False
        self._asr_pending_detector_candidate = None
        self._asr_sealed_turn_token: VoiceTransportToken | None = None
        self._asr_provider_candidate_fence: ProviderCandidateFence | None = None
        self._asr_audio_sequence = 0
        self._asr_audio_generation = 0
        self._asr_current_ingress_token: VoiceIngressToken | None = None
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

    def _capture_turn_token(
        self,
        lifecycle: VoiceInputLifecycleController,
    ) -> VoiceTurnToken:
        ingress_token = self._asr_current_ingress_token
        if ingress_token is None or not self._ingress_token_matches(ingress_token):
            raise RuntimeError("ASR_INGRESS_TOKEN_REQUIRED")
        return VoiceTurnToken(
            ingress=ingress_token,
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
        return bool(
            token.session_epoch == self._asr_session_epoch
            and token.audio_generation == self._asr_audio_generation
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
            lifecycle is not None
            and detector is not None
            and self._asr_session is session_ref
            and self._ingress_token_matches(turn_token.ingress)
            and lifecycle.snapshot.turn_id == turn_token.turn_id
            and self._asr_endpointing_ready(lifecycle, detector, turn_token)
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
        if isinstance(event, DetectorTransportPrewarmEvent):
            await self._handle_transport_prewarm_event(
                event,
                detector,
                lifecycle,
                envelope.session_epoch,
            )
            return
        if isinstance(event, DetectorPrewarmEvent):
            await self._handle_detector_prewarm_event(
                event,
                detector,
                lifecycle,
                envelope.session_epoch,
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
            await self._abort_transport("detector_audio_backpressure")
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
        await self._close_independent_asr()
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
            availability = getattr(
                selection,
                "availability",
                AsrProviderAvailability.IMPLEMENTED,
            )
            if availability is not AsrProviderAvailability.IMPLEMENTED:
                failure_code = "ASR_INDEPENDENT_UNAVAILABLE"
                await self._send_asr_status(failure_code, provider)
                return AsrStartResult(
                    AsrStartStatus.UNAVAILABLE,
                    provider=provider,
                    failure_code=failure_code,
                )
            policy = resolve_provider_policy(provider, endpointing_mode)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Configuration errors must not abort the already-started Core
            # session. Keep the microphone fail-closed and report only the
            # fixed status code/provider category.
            self._asr_session = None
            self._asr_provider = None
            failure_code = "ASR_INDEPENDENT_FAILED"
            await self._send_asr_status(failure_code, core_type or "unknown")
            return AsrStartResult(
                AsrStartStatus.FAILED,
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

        try:
            self._asr_provider = provider
            self._asr_lifecycle = VoiceInputLifecycleController(
                provider_policy=policy,
                shadow_mode=False,
                resource_optimization_enabled=(
                    self._voice_input_resource_optimization_enabled
                ),
            )
            self._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)

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
                resource_optimization_enabled=(
                    self._voice_input_resource_optimization_enabled
                ),
                on_endpointing_failure=(
                    on_detector_endpointing_failure
                    if policy.endpoint_authority == "smart_turn"
                    else None
                ),
                on_event=on_detector_event,
            )
            self._asr_detector = detector_ref
            self._asr_session_factory = create_candidate
            self._asr_transport_selection = selection
            lifecycle = self._asr_lifecycle
            if not self._voice_input_resource_optimization_enabled:
                await self._restart_transport(
                    max_attempts=policy.connect_max_attempts,
                )
                if (
                    epoch != self._asr_session_epoch
                    or lifecycle is not self._asr_lifecycle
                    or self._asr_session is None
                    or not getattr(self._asr_session, "is_ready", True)
                ):
                    raise RuntimeError("ASR_CONTINUOUS_STARTUP_FAILED")
            await self._send_asr_lifecycle_state(VoiceLifecycleState.LOCAL_LISTEN)
            await self._send_asr_status("ASR_INDEPENDENT_READY", provider)
            return AsrStartResult(AsrStartStatus.READY, provider=provider)
        except asyncio.CancelledError:
            active_session = self._asr_session
            if active_session is not None:
                await active_session.close()
            raise
        except Exception:
            active_session = self._asr_session
            if active_session is not None:
                try:
                    await active_session.close()
                except Exception:
                    pass
            if epoch == self._asr_session_epoch:
                self._asr_session = None
                self._asr_provider = None
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
        self._asr_current_ingress_token = None
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
        self._asr_provider_candidate_fence = None
        if asr_session is not None:
            try:
                await asr_session.close()
            except Exception:
                logger.warning("[%s] independent ASR close failed", self.display_name)
        close_tasks = tuple(self._asr_close_tasks)
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        await self._asr_detector_dispatcher.close()
        await self._asr_audio_dispatcher.close()
        self._asr_transcript_dispatcher.invalidate_all()
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
    async def submit(
        self,
        frame: ProcessedVoiceFrame,
        *,
        ingress_token: VoiceIngressToken,
    ) -> AsrSubmitResult:
        """Submit one normalized frame to the independent-ASR hard route."""

        self._ensure_asr_runtime_state()
        if self._asr_lifecycle is None:
            return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
        if not self._ingress_token_matches(ingress_token):
            return AsrSubmitResult(AsrSubmitStatus.STALE)
        self._asr_current_ingress_token = ingress_token

        pcm16 = frame.pcm16
        sample_rate_hz = frame.sample_rate_hz
        speech_probability = frame.speech_probability
        rnnoise_available = frame.rnnoise_available
        rnnoise_evidence = frame.rnnoise_evidence

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
                        rnnoise_evidence=rnnoise_evidence,
                        allow_baseline_update=(
                            lifecycle.snapshot.state
                            in {
                                VoiceLifecycleState.LOCAL_LISTEN,
                                VoiceLifecycleState.WARM_IDLE,
                            }
                        ),
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
                    shadow_metrics = detector.throttle_shadow_metrics
                    lifecycle.metrics.rnnoise_evidence_chunk_count = (
                        shadow_metrics.evidence_chunk_count
                    )
                    lifecycle.metrics.rnnoise_incomplete_chunk_count = (
                        shadow_metrics.incomplete_chunk_count
                    )
                    lifecycle.metrics.rnnoise_shadow_trigger_count = (
                        shadow_metrics.rnnoise_trigger_count
                    )
                    lifecycle.metrics.silero_shadow_trigger_count = (
                        shadow_metrics.silero_trigger_count
                    )
                    lifecycle.metrics.fusion_shadow_trigger_count = (
                        shadow_metrics.fusion_trigger_count
                    )
                    lifecycle.metrics.rnnoise_silero_disagreement_count = (
                        shadow_metrics.rnnoise_silero_disagreement_count
                    )
                    if not ingress_is_current():
                        return AsrSubmitResult(AsrSubmitStatus.STALE)
                    if submitted.status is DetectorSubmitStatus.BACKPRESSURE:
                        lifecycle.metrics.detector_overflow_count += 1
                        await self._handle_audio_ingress_backpressure(ingress_token)
                        return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
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
                        return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
                    if not submitted.throttle_available:
                        lifecycle.enable_independent_asr_fail_open()
                        if (
                            lifecycle.independent_asr_fail_open
                            and not submitted.control_event_emitted
                            and submitted.identity is not None
                            and submitted.candidate is not None
                        ):
                            accepted = self._asr_detector_dispatcher.submit_nowait(
                                CoreDetectorEventEnvelope(
                                    event=DetectorPrewarmEvent(
                                        ingress=submitted.identity,
                                        candidate=submitted.candidate,
                                        kind="continuous",
                                    ),
                                    detector_ref=detector,
                                    lifecycle_ref=lifecycle,
                                    session_epoch=self._asr_session_epoch,
                                )
                            )
                            if not accepted:
                                await self._handle_independent_asr_error(
                                    self._asr_session_epoch,
                                    self._asr_provider or "unknown",
                                    status_code="ASR_ENDPOINTING_FAILED",
                                )
                                return True
                else:
                    detector_result = await detector.feed(
                        pcm16,
                        speech_probability=speech_probability,
                        rnnoise_available=rnnoise_available,
                        rnnoise_evidence=rnnoise_evidence,
                        ingress_token=ingress_token,
                    )
                    if not ingress_is_current():
                        return AsrSubmitResult(AsrSubmitStatus.STALE)
                    if not detector_result.endpointing_available:
                        await self._handle_independent_asr_error(
                            self._asr_session_epoch,
                            self._asr_provider or "unknown",
                            status_code="ASR_ENDPOINTING_FAILED",
                        )
                        return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
                    if not detector_result.throttle_available:
                        lifecycle.enable_independent_asr_fail_open()
                    else:
                        for event in detector_result.events:
                            await self._handle_independent_asr_activity(
                                event,
                                self._asr_session_epoch,
                            )
                            if not ingress_is_current():
                                return AsrSubmitResult(AsrSubmitStatus.STALE)
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
                            return AsrSubmitResult(AsrSubmitStatus.STALE)
            if lifecycle is not None and not ingress_is_current():
                return AsrSubmitResult(AsrSubmitStatus.STALE)
            decision = (
                lifecycle.accept_audio(pcm16, sample_rate_hz=sample_rate_hz)
                if lifecycle is not None
                else None
            )
            if decision is not None and decision.disposition is AudioDisposition.BLOCK:
                if decision.backpressure:
                    await self._handle_audio_ingress_backpressure(ingress_token)
                return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
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
                return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
            if lifecycle is None or detector is None:
                await self._handle_independent_asr_error(
                    self._asr_session_epoch,
                    self._asr_provider or "unknown",
                    status_code="ASR_BLOCKED_ENDPOINTING",
                )
                return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
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
                return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
            asr_session = self._asr_session
            if asr_session is None or not getattr(asr_session, "is_ready", True):
                if lifecycle is None:
                    await self._handle_independent_asr_error(
                        self._asr_session_epoch,
                        self._asr_provider or "unknown",
                    )
                    return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
                self._ensure_transport_restart_task()
                return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
            payload = (
                decision.pre_roll
                if decision is not None
                and decision.disposition is AudioDisposition.FORWARD_WITH_PRE_ROLL
                else pcm16
            )
            if not payload:
                return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)
            if not ingress_is_current():
                return AsrSubmitResult(AsrSubmitStatus.STALE)
            if self._asr_audio_dispatcher.active_turn != turn_token:
                if not self._activate_asr_audio_dispatcher(lifecycle, turn_token):
                    await self._handle_independent_asr_error(
                        self._asr_session_epoch,
                        self._asr_provider or "unknown",
                        status_code="ASR_AUDIO_ORDERING_FAILED",
                    )
                    return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
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
                return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
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
            return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)

        return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)

    def _ensure_transport_restart_task(self) -> None:
        task = self._asr_transport_task
        if task is not None and not task.done():
            return
        task = asyncio.create_task(
            self._restart_transport(),
            name="independent-asr-transport-restart",
        )
        self._asr_transport_task = task

    async def _connect_transport(self) -> None:
        """Connect only the independent ASR transport."""

        lifecycle = self._asr_lifecycle
        await self._restart_transport(
            max_attempts=(
                lifecycle.provider_policy.connect_max_attempts
                if lifecycle is not None
                else 1
            )
        )

    async def _restart_transport(self, *, max_attempts: int = 3) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        async with self._asr_transport_lock:
            lifecycle = self._asr_lifecycle
            if lifecycle is None:
                return
            existing = self._asr_session
            if existing is not None and getattr(existing, "is_ready", True):
                return
            factory = self._asr_session_factory
            selection = self._asr_transport_selection
            if factory is None or selection is None or lifecycle is None:
                await self._handle_independent_asr_error(
                    self._asr_session_epoch,
                    self._asr_provider or "unknown",
                )
                return

            session_epoch = self._asr_session_epoch
            audio_generation = self._asr_audio_generation
            selected_provider = str(
                getattr(selection, "provider_key", "") or ""
            ).strip().lower()
            for attempt in range(max_attempts):
                if (
                    session_epoch != self._asr_session_epoch
                    or audio_generation != self._asr_audio_generation
                    or lifecycle is not self._asr_lifecycle
                    or factory is not self._asr_session_factory
                ):
                    return
                if lifecycle.snapshot.state is VoiceLifecycleState.BACKOFF:
                    lifecycle.transition(VoiceLifecycleEvent.RETRY)
                    lifecycle.metrics.reconnect_count += 1
                    await self._send_asr_lifecycle_state(VoiceLifecycleState.PREWARMING)
                candidate = None
                try:
                    connect_started_at = time.monotonic()
                    candidate = factory(selection)
                    await candidate.connect()
                    if (
                        session_epoch != self._asr_session_epoch
                        or audio_generation != self._asr_audio_generation
                        or lifecycle is not self._asr_lifecycle
                        or factory is not self._asr_session_factory
                    ):
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
                        try:
                            await candidate.close()
                        except Exception:
                            pass
                    raise
                except Exception:
                    if candidate is not None:
                        try:
                            await candidate.close()
                        except Exception:
                            pass
                    if (
                        session_epoch != self._asr_session_epoch
                        or audio_generation != self._asr_audio_generation
                        or lifecycle is not self._asr_lifecycle
                        or factory is not self._asr_session_factory
                    ):
                        return
                    if lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING:
                        lifecycle.transition(VoiceLifecycleEvent.CONNECT_FAILED)
                        await self._send_asr_lifecycle_state(VoiceLifecycleState.BACKOFF)
                    if attempt + 1 < max_attempts:
                        await asyncio.sleep(
                            min(
                                lifecycle.provider_policy.connect_retry_cap_seconds,
                                lifecycle.provider_policy.connect_retry_base_seconds
                                * (2**attempt),
                            )
                        )
                        continue
            if lifecycle.snapshot.state is VoiceLifecycleState.BACKOFF:
                lifecycle.transition(VoiceLifecycleEvent.RETRIES_EXHAUSTED)
            failed_provider = selected_provider or self._asr_provider or "unknown"
            await self._handle_independent_asr_error(
                self._asr_session_epoch,
                failed_provider,
                status_code=(
                    "ASR_INDEPENDENT_PROVIDER_UNAVAILABLE"
                    if failed_provider == "soniox"
                    else "ASR_INDEPENDENT_FAILED"
                ),
            )

    async def _abort_transport(self, reason: str) -> None:
        """Invalidate provider I/O before closing a live transport."""

        self._asr_audio_generation += 1
        self._asr_transcript_dispatcher.invalidate_all()
        self._asr_detector_dispatcher.invalidate_all()
        self._asr_audio_dispatcher.abort()
        self._asr_reserved_final_key = None
        self._asr_sealed_turn_token = None
        self._asr_provider_candidate_fence = None
        self._asr_turn_prepared = False
        self._asr_received_audio = False
        self._asr_pending_speech_confirmed = False
        self._asr_pending_detector_candidate = None
        self._asr_audio_sequence = 0
        self._asr_current_ingress_token = None
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

    async def _close_transport_only(self) -> None:
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

    def _schedule_transport_warm_expiry(
        self,
        epoch: int,
        *,
        ttl_ms: int,
    ) -> None:
        task = self._asr_warm_expiry_task
        if task is not None:
            task.cancel()
        lifecycle = self._asr_lifecycle
        if lifecycle is None or not self._voice_input_resource_optimization_enabled:
            return

        async def expire() -> None:
            try:
                await asyncio.sleep(ttl_ms / 1_000)
                if epoch != self._asr_session_epoch:
                    return
                current = self._asr_lifecycle
                if current is lifecycle:
                    if current.snapshot.state is VoiceLifecycleState.PREWARMING:
                        detector = self._asr_detector
                        lease, self._asr_smart_turn_lease = (
                            self._asr_smart_turn_lease,
                            None,
                        )
                        if lease is not None:
                            await lease.release()
                        if detector is not None and detector is self._asr_detector:
                            await detector.reset()
                        if (
                            epoch != self._asr_session_epoch
                            or current is not self._asr_lifecycle
                            or current.snapshot.state
                            is not VoiceLifecycleState.PREWARMING
                        ):
                            return
                        current.transition(VoiceLifecycleEvent.PREWARM_EXPIRED)
                        self._asr_pending_speech_confirmed = False
                        self._asr_pending_detector_candidate = None
                        await self._send_asr_lifecycle_state(
                            VoiceLifecycleState.LOCAL_LISTEN
                        )
                    if current.snapshot.state in {
                        VoiceLifecycleState.LOCAL_LISTEN,
                        VoiceLifecycleState.WARM_IDLE,
                    }:
                        await self._close_transport_only()
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

    async def _handle_detector_prewarm_event(
        self,
        event: DetectorPrewarmEvent,
        detector: DetectorRuntime,
        lifecycle: VoiceInputLifecycleController,
        epoch: int,
    ) -> None:
        """Prepare local endpointing and transport without granting endpoint authority."""

        if (
            epoch != self._asr_session_epoch
            or detector is not self._asr_detector
            or lifecycle is not self._asr_lifecycle
            or not self._ingress_token_matches(event.ingress.ingress_token)
        ):
            return
        state = lifecycle.snapshot.state
        if state is VoiceLifecycleState.DRAINING:
            if event.kind == "continuous":
                lifecycle.mark_pending_turn_speech()
                self._asr_pending_detector_candidate = event.candidate
            return
        if state in {
            VoiceLifecycleState.LOCAL_LISTEN,
            VoiceLifecycleState.WARM_IDLE,
            VoiceLifecycleState.DEEP_SLEEP,
        }:
            warm_task = self._asr_warm_expiry_task
            if warm_task is not None:
                warm_task.cancel()
                self._asr_warm_expiry_task = None
            if state is VoiceLifecycleState.WARM_IDLE:
                lifecycle.metrics.warm_hit_count += 1
            lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
            await self._send_asr_lifecycle_state(VoiceLifecycleState.PREWARMING)
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
            if event.kind == "continuous":
                await self._prepare_independent_asr_turn(epoch)
            return

        smart_turn_task = asyncio.create_task(
            self._ensure_smart_turn_ready(lifecycle, epoch),
            name="independent-asr-prewarm-smart-turn",
        )
        transport_task = asyncio.create_task(
            self._restart_transport(),
            name="independent-asr-prewarm-transport",
        )
        smart_turn_ready, _transport_result = await asyncio.gather(
            smart_turn_task,
            transport_task,
            return_exceptions=True,
        )
        if (
            smart_turn_ready is not True
            or epoch != self._asr_session_epoch
            or detector is not self._asr_detector
            or lifecycle is not self._asr_lifecycle
            or lifecycle.snapshot.state is not VoiceLifecycleState.PREWARMING
        ):
            return
        if event.kind != "continuous":
            self._schedule_transport_warm_expiry(
                epoch,
                ttl_ms=lifecycle.config.idle_transport_close_ms,
            )
            return
        session_ref = self._asr_session
        if session_ref is None or not getattr(session_ref, "is_ready", True):
            self._asr_pending_speech_confirmed = True
            return
        lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
        await self._send_asr_lifecycle_state(VoiceLifecycleState.ACTIVE)
        self._asr_turn_audio_started_at = time.monotonic()
        self._asr_first_partial_recorded = False
        self._activate_asr_audio_dispatcher(lifecycle, turn_token)
        await self._prepare_independent_asr_turn(epoch)

    async def _handle_transport_prewarm_event(
        self,
        event: DetectorTransportPrewarmEvent,
        detector: DetectorRuntime,
        lifecycle: VoiceInputLifecycleController,
        epoch: int,
    ) -> None:
        """Preconnect the selected transport without granting turn authority."""

        if (
            epoch != self._asr_session_epoch
            or detector is not self._asr_detector
            or lifecycle is not self._asr_lifecycle
            or not self._ingress_token_matches(event.ingress.ingress_token)
        ):
            return
        state = lifecycle.snapshot.state
        if state is VoiceLifecycleState.DRAINING:
            return
        if state in {
            VoiceLifecycleState.LOCAL_LISTEN,
            VoiceLifecycleState.WARM_IDLE,
            VoiceLifecycleState.DEEP_SLEEP,
        }:
            warm_task = self._asr_warm_expiry_task
            if warm_task is not None:
                warm_task.cancel()
                self._asr_warm_expiry_task = None
            if state is VoiceLifecycleState.WARM_IDLE:
                lifecycle.metrics.warm_hit_count += 1
            lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
            await self._send_asr_lifecycle_state(VoiceLifecycleState.PREWARMING)
        if lifecycle.snapshot.state is not VoiceLifecycleState.PREWARMING:
            return
        session = self._asr_session
        if session is None or not getattr(session, "is_ready", True):
            await self._restart_transport()
        if (
            epoch != self._asr_session_epoch
            or detector is not self._asr_detector
            or lifecycle is not self._asr_lifecycle
            or lifecycle.snapshot.state is not VoiceLifecycleState.PREWARMING
        ):
            return
        self._schedule_transport_warm_expiry(
            epoch,
            ttl_ms=lifecycle.config.idle_transport_close_ms,
        )

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
            warm_task = self._asr_warm_expiry_task
            if warm_task is not None:
                warm_task.cancel()
                self._asr_warm_expiry_task = None
            previous_state = lifecycle.snapshot.state
            state = lifecycle.snapshot.state
            if state in {
                VoiceLifecycleState.LOCAL_LISTEN,
                VoiceLifecycleState.DEEP_SLEEP,
                VoiceLifecycleState.WARM_IDLE,
            }:
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
            if lifecycle.provider_policy.endpoint_authority == "provider":
                fence = await detector.seal_provider_candidate()
                if fence is None:
                    self._asr_transcript_dispatcher.release(final_key)
                    self._asr_reserved_final_key = None
                    await self._handle_independent_asr_error(
                        epoch,
                        self._asr_provider or "unknown",
                        status_code="ASR_ENDPOINTING_FAILED",
                    )
                    return
                self._asr_provider_candidate_fence = fence
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
            await self._callbacks.on_partial(
                VoicePartialEvent(text=clean, session_epoch=epoch)
            )
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
        successor_present = False
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
            detector_ref = self._asr_detector
            if lifecycle_ref.provider_policy.endpoint_authority == "provider":
                provider_fence = self._asr_provider_candidate_fence
                if provider_fence is None or detector_ref is None:
                    return
                completion = await detector_ref.complete_provider_candidate(
                    provider_fence
                )
                if completion is None:
                    return
                successor_present = completion
                self._asr_provider_candidate_fence = None
            lifecycle_ref.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
            self._asr_turn_prepared = False
            self._asr_received_audio = False
            self._asr_sealed_turn_token = None
            self._asr_provider_candidate_fence = None
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
            if successor_present and not has_pending_turn:
                lifecycle_ref.preserve_unconfirmed_pending_audio()
            if not has_pending_turn:
                self._schedule_transport_warm_expiry(
                    epoch,
                    ttl_ms=lifecycle_ref.provider_policy.warm_transport_ms,
                )

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
        self._asr_current_ingress_token = None
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
        self._asr_provider_candidate_fence = None
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
            await self._callbacks.on_status(
                AsrStatusEvent(code=code, provider=provider)
            )
        except Exception:
            logger.debug(
                "[%s] independent ASR status delivery failed",
                self.display_name,
            )

    async def _send_asr_lifecycle_state(self, state: VoiceLifecycleState) -> None:
        try:
            await self._callbacks.on_lifecycle(
                AsrLifecycleNotification(
                    state=state.value,
                    provider=self._asr_provider or "",
                    session_epoch=self._asr_session_epoch,
                )
            )
        except Exception:
            logger.debug(
                "[%s] ASR lifecycle status delivery failed",
                self.display_name,
            )
