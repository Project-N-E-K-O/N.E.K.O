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
from .audio import AsrAudioDispatcher
from ._registry_meta import AsrProviderAvailability
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
    session_epoch: int = -1


@dataclass(frozen=True, slots=True)
class AsrRuntimeCallbacks:
    display_name: Callable[[], str]
    on_prepare_turn: Callable[[VoiceTurnToken], Awaitable[bool]]
    on_partial: Callable[[VoicePartialEvent], Awaitable[None]]
    on_final: Callable[[VoiceTranscriptEvent], Awaitable[None]]
    on_turn_abandoned: Callable[[VoiceTurnToken], Awaitable[None]]
    on_failure: Callable[[AsrFailureEvent], Awaitable[None]]
    on_status: Callable[[AsrStatusEvent], Awaitable[None]]
    on_lifecycle: Callable[[AsrLifecycleNotification], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _AsrRuntimeIdentity:
    start_generation: int
    session_epoch: int
    audio_generation: int
    lifecycle: VoiceInputLifecycleController | None
    transport_generation: int | None
    detector: DetectorRuntime | None
    session: Any
    provider: str | None
    session_factory: Any
    transport_selection: Any
    transport_task: asyncio.Task[None] | None
    ingress_token: VoiceIngressToken | None = None
    turn_token: VoiceTurnToken | None = None


class IndependentAsrRuntime:
    """Own one independent ASR session without reading Core manager state."""

    def __init__(self, callbacks: AsrRuntimeCallbacks) -> None:
        self._callbacks = callbacks
        self._init_asr_runtime_state()

    @property
    def display_name(self) -> str:
        return self._callbacks.display_name()

    async def close(self) -> None:
        operation_generation = self._begin_asr_start_operation()
        await self._close_independent_asr(
            operation_generation=operation_generation,
        )

    def _begin_asr_start_operation(self) -> int:
        self._asr_start_generation += 1
        return self._asr_start_generation

    def _asr_start_operation_matches(self, operation_generation: int) -> bool:
        return operation_generation == self._asr_start_generation

    def _invalidate_asr_start(self) -> None:
        self._begin_asr_start_operation()

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
        epoch = self._asr_session_epoch
        provider = self._asr_provider or "unknown"
        lifecycle = self._asr_lifecycle
        if lifecycle is not None and (
            lifecycle.snapshot.state is VoiceLifecycleState.SUSPENDED
        ):
            lifecycle.transition(VoiceLifecycleEvent.GAME_RELEASED)
            identity = self._capture_runtime_identity()
            await self._send_asr_lifecycle_state(
                lifecycle.snapshot.state,
                provider=provider,
                session_epoch=epoch,
                expected_identity=identity,
            )

    def _asr_runtime_refs_match(
        self,
        epoch: int,
        lifecycle: VoiceInputLifecycleController | None,
        detector: DetectorRuntime | None,
    ) -> bool:
        return bool(
            epoch == self._asr_session_epoch
            and self._asr_lifecycle is lifecycle
            and self._asr_detector is detector
        )

    def _capture_runtime_identity(
        self,
        *,
        ingress_token: VoiceIngressToken | None = None,
        turn_token: VoiceTurnToken | None = None,
    ) -> _AsrRuntimeIdentity:
        lifecycle = self._asr_lifecycle
        return _AsrRuntimeIdentity(
            start_generation=self._asr_start_generation,
            session_epoch=self._asr_session_epoch,
            audio_generation=self._asr_audio_generation,
            lifecycle=lifecycle,
            transport_generation=(
                lifecycle.snapshot.transport_generation
                if lifecycle is not None
                else None
            ),
            detector=self._asr_detector,
            session=self._asr_session,
            provider=self._asr_provider,
            session_factory=self._asr_session_factory,
            transport_selection=self._asr_transport_selection,
            transport_task=self._asr_transport_task,
            ingress_token=ingress_token,
            turn_token=turn_token,
        )

    def _runtime_identity_matches(
        self,
        identity: _AsrRuntimeIdentity,
    ) -> bool:
        lifecycle = self._asr_lifecycle
        if (
            identity.start_generation != self._asr_start_generation
            or identity.session_epoch != self._asr_session_epoch
            or identity.audio_generation != self._asr_audio_generation
            or lifecycle is not identity.lifecycle
            or self._asr_detector is not identity.detector
            or self._asr_session is not identity.session
            or self._asr_provider != identity.provider
            or self._asr_session_factory is not identity.session_factory
            or self._asr_transport_selection is not identity.transport_selection
            or self._asr_transport_task is not identity.transport_task
        ):
            return False
        transport_generation = (
            lifecycle.snapshot.transport_generation if lifecycle is not None else None
        )
        if transport_generation != identity.transport_generation:
            return False
        if identity.ingress_token is not None and (
            self._asr_current_ingress_token != identity.ingress_token
            or not self._ingress_token_matches(identity.ingress_token)
        ):
            return False
        if identity.turn_token is not None and (
            lifecycle is None
            or identity.turn_token.ingress != identity.ingress_token
            or lifecycle.snapshot.turn_id != identity.turn_token.turn_id
        ):
            return False
        return True

    async def abort(self, reason: str) -> None:
        if reason == "ingress_backpressure":
            token = self._asr_current_ingress_token
            if token is not None and self._ingress_token_matches(token):
                await self._handle_audio_ingress_backpressure(token)
                return
        epoch = self._asr_session_epoch
        lifecycle = self._asr_lifecycle
        detector = self._asr_detector
        provider = self._asr_provider or "unknown"
        if lifecycle is not None:
            lifecycle.invalidate_audio()
        post_detach = await self._abort_transport(reason)
        if (
            post_detach is None
            or not self._runtime_identity_matches(post_detach)
            or not self._asr_runtime_refs_match(epoch, lifecycle, detector)
        ):
            return
        if reason == "ingress_backpressure":
            await self._send_asr_status(
                "ASR_INGRESS_BACKPRESSURE",
                provider,
                session_epoch=epoch,
                expected_identity=post_detach,
            )
        if detector is not None:
            try:
                await detector.reset()
            except Exception:
                logger.warning(
                    "[%s] detector reset failed during voice abort",
                    self.display_name,
                )
            if not self._runtime_identity_matches(
                post_detach
            ) or not self._asr_runtime_refs_match(epoch, lifecycle, detector):
                return
        if lifecycle is not None:
            await self._send_asr_lifecycle_state(
                lifecycle.snapshot.state,
                provider=provider,
                session_epoch=epoch,
                expected_identity=post_detach,
            )

    async def wait_transcript_idle(self) -> None:
        await self._asr_transcript_dispatcher.wait_idle()

    def _init_asr_runtime_state(self) -> None:
        self._asr_session = None
        self._asr_session_epoch = 0
        self._asr_start_generation = 0
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
        if not hasattr(self, "_asr_start_generation"):
            self._asr_start_generation = 0

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
        if not self._ingress_token_matches(turn_token.ingress):
            return
        identity = self._capture_runtime_identity(
            ingress_token=turn_token.ingress,
            turn_token=turn_token,
        )
        status_code = (
            "ASR_STREAM_BACKPRESSURE"
            if "BACKPRESSURE" in str(error)
            else "ASR_INDEPENDENT_STREAM_FAILED"
        )
        await self._handle_independent_asr_error(
            identity.session_epoch,
            identity.provider or "unknown",
            status_code=status_code,
            expected_identity=identity,
        )

    async def _handle_asr_detector_dispatcher_failure(
        self,
        envelope: CoreDetectorEventEnvelope,
        error: BaseException,
    ) -> None:
        detector = self._asr_detector
        lifecycle = self._asr_lifecycle
        event = envelope.event
        if (
            envelope.session_epoch != self._asr_session_epoch
            or detector is not envelope.detector_ref
            or lifecycle is not envelope.lifecycle_ref
            or detector is None
            or lifecycle is None
            or event.ingress.detector_epoch != detector.detector_epoch
            or not self._ingress_token_matches(event.ingress.ingress_token)
        ):
            return
        logger.error(
            "[%s] detector event dispatcher failed epoch=%s",
            self.display_name,
            envelope.session_epoch,
            exc_info=(type(error), error, error.__traceback__),
        )
        identity = self._capture_runtime_identity(
            ingress_token=event.ingress.ingress_token,
        )
        await self._handle_independent_asr_error(
            identity.session_epoch,
            identity.provider or "unknown",
            status_code="ASR_ENDPOINTING_FAILED",
            expected_identity=identity,
        )

    def _detector_envelope_is_current(
        self,
        envelope: CoreDetectorEventEnvelope,
    ) -> bool:
        detector = self._asr_detector
        lifecycle = self._asr_lifecycle
        event = envelope.event
        return bool(
            envelope.session_epoch == self._asr_session_epoch
            and detector is envelope.detector_ref
            and lifecycle is envelope.lifecycle_ref
            and detector is not None
            and lifecycle is not None
            and event.ingress.detector_epoch == detector.detector_epoch
            and self._ingress_token_matches(event.ingress.ingress_token)
        )

    async def _dispatch_asr_detector_event(
        self,
        envelope: CoreDetectorEventEnvelope,
    ) -> None:
        event = envelope.event
        detector = self._asr_detector
        lifecycle = self._asr_lifecycle
        if not self._detector_envelope_is_current(envelope):
            stale_metrics = getattr(envelope.lifecycle_ref, "metrics", None)
            if stale_metrics is not None:
                stale_metrics.detector_stale_event_count += 1
            return
        assert detector is not None
        assert lifecycle is not None
        lifecycle.metrics.smart_turn_inference_ms = detector.smart_turn_evaluation_ms
        lifecycle.metrics.smart_turn_stale_result_count = (
            detector.smart_turn_stale_result_count
        )
        lifecycle.metrics.smart_turn_coalesced_evaluation_count = (
            detector.smart_turn_coalesced_evaluation_count
        )
        if isinstance(event, DetectorRuntimeEvent):
            identity = self._capture_runtime_identity(
                ingress_token=event.ingress.ingress_token,
            )
            await self._handle_independent_asr_error(
                envelope.session_epoch,
                identity.provider or "unknown",
                status_code=(
                    "ASR_INGRESS_BACKPRESSURE"
                    if event.kind == "audio_backpressure"
                    else "ASR_ENDPOINTING_FAILED"
                ),
                expected_identity=identity,
            )
            return
        if isinstance(event, DetectorActivityEvent):
            await self._handle_independent_asr_activity(
                event.activity,
                envelope.session_epoch,
            )
            if not self._detector_envelope_is_current(envelope):
                return
            lifecycle = self._asr_lifecycle
            assert lifecycle is envelope.lifecycle_ref
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
            if not self._detector_envelope_is_current(envelope):
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
        await self._handle_independent_asr_endpoint(envelope.session_epoch)
        if not self._detector_envelope_is_current(envelope):
            return
        session_ref = self._asr_session
        if session_ref is None:
            return
        if not self._asr_audio_dispatcher.seal(
            turn_token,
            session_ref,
            after_sequence=self._asr_audio_sequence,
        ):
            identity = self._capture_runtime_identity(
                ingress_token=turn_token.ingress,
                turn_token=turn_token,
            )
            await self._handle_independent_asr_error(
                envelope.session_epoch,
                identity.provider or "unknown",
                status_code="ASR_AUDIO_ORDERING_FAILED",
                expected_identity=identity,
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
        identity = self._capture_runtime_identity(
            ingress_token=turn_token.ingress,
            turn_token=turn_token,
        )
        if detector is None:
            await self._handle_independent_asr_error(
                epoch,
                identity.provider or "unknown",
                status_code="ASR_BLOCKED_ENDPOINTING",
                expected_identity=identity,
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
            if self._asr_smart_turn_lease is not lease:
                return False
            self._asr_smart_turn_lease = None
            if not self._runtime_identity_matches(identity):
                return False
        lease = await detector.prepare_endpointing(turn_token)
        if (
            not self._runtime_identity_matches(identity)
            or self._asr_smart_turn_lease is not None
        ):
            if lease is not None:
                await lease.release()
            return False
        if lease is None or not detector.endpointing_ready(turn_token):
            if lease is not None:
                await lease.release()
                if not self._runtime_identity_matches(identity):
                    return False
            await self._handle_independent_asr_error(
                epoch,
                identity.provider or "unknown",
                status_code="ASR_BLOCKED_ENDPOINTING",
                expected_identity=identity,
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
        epoch = self._asr_session_epoch
        detector = self._asr_detector
        provider = self._asr_provider or "unknown"
        state = lifecycle.snapshot.state
        if state is VoiceLifecycleState.DRAINING:
            lifecycle.discard_pending_turn()
            self._asr_pending_speech_confirmed = False
            self._asr_pending_detector_candidate = None
            if detector is not None:
                identity = self._capture_runtime_identity(ingress_token=token)
                await detector.reset()
                if not self._runtime_identity_matches(
                    identity
                ) or not self._asr_runtime_refs_match(
                    epoch,
                    lifecycle,
                    detector,
                ):
                    return
            identity = self._capture_runtime_identity(ingress_token=token)
            await self._send_asr_status(
                "ASR_INGRESS_BACKPRESSURE",
                provider,
                session_epoch=epoch,
                expected_identity=identity,
            )
            return
        if state in {
            VoiceLifecycleState.LOCAL_LISTEN,
            VoiceLifecycleState.WARM_IDLE,
            VoiceLifecycleState.DEEP_SLEEP,
        }:
            self._asr_audio_generation += 1
            lifecycle.invalidate_audio()
            if detector is not None:
                identity = self._capture_runtime_identity()
                try:
                    await detector.reset()
                except Exception:
                    logger.warning(
                        "[%s] detector reset failed after ingress backpressure",
                        self.display_name,
                    )
                if not self._runtime_identity_matches(
                    identity
                ) or not self._asr_runtime_refs_match(
                    epoch,
                    lifecycle,
                    detector,
                ):
                    return
            identity = self._capture_runtime_identity()
            await self._send_asr_status(
                "ASR_INGRESS_BACKPRESSURE",
                provider,
                session_epoch=epoch,
                expected_identity=identity,
            )
            return
        if state in {
            VoiceLifecycleState.PREWARMING,
            VoiceLifecycleState.BACKOFF,
            VoiceLifecycleState.ACTIVE,
        }:
            abandoned_turn = (
                self._capture_turn_token(lifecycle)
                if state is VoiceLifecycleState.ACTIVE and self._asr_turn_prepared
                else None
            )
            try:
                lifecycle.invalidate_audio()
                post_detach = await self._abort_transport(
                    "detector_audio_backpressure"
                )
                if not self._runtime_identity_matches(
                    post_detach
                ) or not self._asr_runtime_refs_match(
                    epoch,
                    lifecycle,
                    detector,
                ):
                    return
                if detector is not None:
                    await detector.reset()
                    if not self._runtime_identity_matches(
                        post_detach
                    ) or not self._asr_runtime_refs_match(
                        epoch,
                        lifecycle,
                        detector,
                    ):
                        return
                await self._send_asr_status(
                    "ASR_INGRESS_BACKPRESSURE",
                    provider,
                    session_epoch=epoch,
                    expected_identity=post_detach,
                )
                if not self._runtime_identity_matches(post_detach):
                    return
                await self._send_asr_lifecycle_state(
                    VoiceLifecycleState.LOCAL_LISTEN,
                    provider=provider,
                    session_epoch=epoch,
                    expected_identity=post_detach,
                )
                return
            finally:
                if abandoned_turn is not None:
                    try:
                        await self._callbacks.on_turn_abandoned(abandoned_turn)
                    except Exception:
                        logger.debug(
                            "[%s] independent ASR turn abandonment callback failed",
                            self.display_name,
                        )
        identity = self._capture_runtime_identity()
        await self._send_asr_status(
            "ASR_INGRESS_BACKPRESSURE",
            provider,
            session_epoch=epoch,
            expected_identity=identity,
        )

    async def start(
        self,
        *,
        route_key: str,
        resource_optimization_enabled: bool,
    ) -> AsrStartResult:
        """Resolve and start one independent-ASR route."""

        self._ensure_asr_runtime_state()
        operation_generation = self._begin_asr_start_operation()
        await self._close_independent_asr(
            operation_generation=operation_generation,
        )
        if not self._asr_start_operation_matches(operation_generation):
            return AsrStartResult(
                AsrStartStatus.FAILED,
                failure_code="ASR_START_STALE",
            )
        epoch = self._asr_session_epoch
        audio_generation = self._asr_audio_generation

        def operation_is_current() -> bool:
            return bool(
                self._asr_start_operation_matches(operation_generation)
                and epoch == self._asr_session_epoch
                and audio_generation == self._asr_audio_generation
            )

        def stale_result(provider: str | None = None) -> AsrStartResult:
            return AsrStartResult(
                AsrStartStatus.FAILED,
                provider=provider,
                failure_code="ASR_START_STALE",
                session_epoch=epoch,
            )

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
                if not operation_is_current():
                    return stale_result(provider)
                failure_code = "ASR_INDEPENDENT_UNAVAILABLE"
                status_identity = self._capture_runtime_identity()
                delivered = await self._send_asr_status(
                    failure_code,
                    provider,
                    session_epoch=epoch,
                    expected_identity=status_identity,
                )
                if not delivered or not operation_is_current():
                    return stale_result(provider)
                return AsrStartResult(
                    AsrStartStatus.UNAVAILABLE,
                    provider=provider,
                    failure_code=failure_code,
                    session_epoch=epoch,
                )
            policy = resolve_provider_policy(provider, endpointing_mode)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Configuration errors must not abort the already-started Core
            # session. Keep the microphone fail-closed and report only the
            # fixed status code/provider category.
            if not operation_is_current():
                return stale_result()
            self._asr_session = None
            self._asr_provider = None
            failure_code = "ASR_INDEPENDENT_FAILED"
            status_identity = self._capture_runtime_identity()
            delivered = await self._send_asr_status(
                failure_code,
                core_type or "unknown",
                session_epoch=epoch,
                expected_identity=status_identity,
            )
            if not delivered or not operation_is_current():
                return stale_result()
            return AsrStartResult(
                AsrStartStatus.FAILED,
                failure_code=failure_code,
                session_epoch=epoch,
            )

        # Provider selection is immutable for this session epoch. Expose the
        # selected provider during connect retries, then clear it only if the
        # startup attempt ultimately fails.
        if not operation_is_current():
            return stale_result(provider)
        self._asr_provider = provider

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
                if not operation_is_current():
                    return stale_result(provider)
                asr_session = create_candidate(selection)
                try:
                    await asr_session.connect()
                    if not operation_is_current():
                        await self._close_asr_session(asr_session)
                        asr_session = None
                        return stale_result(provider)
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
                    if not operation_is_current():
                        return stale_result(provider)
                    if attempt + 1 >= max_attempts:
                        raise
                    await asyncio.sleep(
                        min(
                            policy.connect_retry_cap_seconds,
                            policy.connect_retry_base_seconds * (2**attempt),
                        )
                    )
                    if not operation_is_current():
                        return stale_result(provider)
            if asr_session is None:
                raise RuntimeError("ASR_CONNECT_FAILED")
            if not operation_is_current():
                await self._close_asr_session(asr_session)
                return stale_result(provider)
            self._asr_session = asr_session
            self._asr_last_provider_wire_audio_ms = 0
            self._asr_provider = provider
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
            lifecycle_ref = self._asr_lifecycle
            detector_ref: DetectorRuntime | None = None

            async def on_detector_endpointing_failure() -> None:
                if not self._asr_runtime_refs_match(
                    epoch,
                    lifecycle_ref,
                    detector_ref,
                ):
                    return
                identity = self._capture_runtime_identity(
                    ingress_token=self._asr_current_ingress_token,
                )
                await self._handle_independent_asr_error(
                    epoch,
                    provider,
                    status_code="ASR_ENDPOINTING_FAILED",
                    expected_identity=identity,
                )

            async def on_detector_event(event) -> None:
                current_lifecycle_ref = self._asr_lifecycle
                if (
                    detector_ref is None
                    or current_lifecycle_ref is None
                    or epoch != self._asr_session_epoch
                ):
                    return
                accepted = self._asr_detector_dispatcher.submit_nowait(
                    CoreDetectorEventEnvelope(
                        event=event,
                        detector_ref=detector_ref,
                        lifecycle_ref=current_lifecycle_ref,
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
            start_identity = self._capture_runtime_identity()
            delivered = await self._send_asr_lifecycle_state(
                VoiceLifecycleState.LOCAL_LISTEN,
                provider=provider,
                session_epoch=epoch,
                expected_identity=start_identity,
            )
            if (
                not delivered
                or not operation_is_current()
                or not self._runtime_identity_matches(start_identity)
            ):
                return stale_result(provider)
            delivered = await self._send_asr_status(
                "ASR_INDEPENDENT_READY",
                provider,
                session_epoch=epoch,
                expected_identity=start_identity,
            )
            if (
                not delivered
                or not operation_is_current()
                or not self._runtime_identity_matches(start_identity)
            ):
                return stale_result(provider)
            return AsrStartResult(
                AsrStartStatus.READY,
                provider=provider,
                session_epoch=epoch,
            )
        except asyncio.CancelledError:
            if asr_session is not None:
                await self._close_asr_session(asr_session)
            raise
        except Exception:
            if asr_session is not None:
                await self._close_asr_session(asr_session)
            if operation_is_current():
                self._asr_session = None
                self._asr_provider = None
                failure_code = (
                    "ASR_INDEPENDENT_PROVIDER_UNAVAILABLE"
                    if policy.connect_max_attempts > 1
                    else "ASR_INDEPENDENT_FAILED"
                )
                failure_identity = self._capture_runtime_identity()
                delivered = await self._send_asr_status(
                    failure_code,
                    provider,
                    session_epoch=epoch,
                    expected_identity=failure_identity,
                )
                if not delivered or not operation_is_current():
                    return stale_result(provider)
                return AsrStartResult(
                    AsrStartStatus.UNAVAILABLE
                    if policy.connect_max_attempts > 1
                    else AsrStartStatus.FAILED,
                    provider=provider,
                    failure_code=failure_code,
                    session_epoch=epoch,
                )
            return stale_result(provider)
        return stale_result(provider)

    async def _close_independent_asr(
        self,
        *,
        operation_generation: int | None = None,
    ) -> None:
        """Invalidate callbacks first, then release the detached provider session."""

        self._ensure_asr_runtime_state()
        if operation_generation is None:
            operation_generation = self._begin_asr_start_operation()
        elif not self._asr_start_operation_matches(operation_generation):
            return
        self._asr_session_epoch += 1
        self._asr_audio_generation += 1
        transcript_dispatcher = self._asr_transcript_dispatcher
        detector_dispatcher = self._asr_detector_dispatcher
        audio_dispatcher = self._asr_audio_dispatcher
        transcript_dispatcher.invalidate_all()
        detector_dispatcher.invalidate_all()
        audio_dispatcher.abort()
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
        asr_session, self._asr_session = self._asr_session, None
        lifecycle, self._asr_lifecycle = self._asr_lifecycle, None
        detector, self._asr_detector = self._asr_detector, None
        lease, self._asr_smart_turn_lease = self._asr_smart_turn_lease, None
        detached_tasks: list[asyncio.Task[Any]] = []
        for task_name in (
            "_asr_transport_task",
            "_asr_warm_expiry_task",
            "_asr_final_watchdog_task",
        ):
            task = getattr(self, task_name, None)
            setattr(self, task_name, None)
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                detached_tasks.append(task)
        close_tasks = tuple(self._asr_close_tasks)
        self._asr_close_tasks = set()
        self._asr_provider = None
        self._asr_core_type = None
        if lifecycle is not None:
            lifecycle.stop()
        self._asr_current_ingress_token = None
        self._asr_received_audio = False
        self._asr_turn_prepared = False
        self._asr_accepted_final_keys.clear()
        self._asr_reserved_final_key = None
        self._asr_session_factory = None
        self._asr_transport_selection = None
        self._asr_pending_speech_confirmed = False
        self._asr_pending_detector_candidate = None
        self._asr_audio_sequence = 0
        self._asr_sealed_turn_token = None
        if detector is not None:
            await detector.close()
        if lease is not None:
            try:
                await lease.release()
            except Exception:
                logger.warning(
                    "[%s] SmartTurn lease release failed during ASR close",
                    self.display_name,
                )
        if asr_session is not None:
            try:
                await asr_session.close()
            except Exception:
                logger.warning("[%s] independent ASR close failed", self.display_name)
        wait_tasks = (*detached_tasks, *close_tasks)
        if wait_tasks:
            await asyncio.gather(*wait_tasks, return_exceptions=True)
        await detector_dispatcher.close()
        await audio_dispatcher.close()
        transcript_dispatcher.invalidate_all()

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
        identity = self._capture_runtime_identity(ingress_token=ingress_token)

        pcm16 = frame.pcm16
        sample_rate_hz = frame.sample_rate_hz
        speech_probability = frame.speech_probability
        rnnoise_available = frame.rnnoise_available

        try:
            lifecycle = identity.lifecycle
            detector = identity.detector

            def ingress_is_current() -> bool:
                return self._runtime_identity_matches(identity)

            if lifecycle is not None and detector is not None:
                submit_audio = getattr(detector, "submit_audio", None)
                uses_smart_turn = (
                    lifecycle.provider_policy.endpoint_authority == "smart_turn"
                )
                if uses_smart_turn and callable(submit_audio):
                    detector_submit_started_at = time.perf_counter()
                    submitted = await submit_audio(
                        pcm16,
                        ingress_token=ingress_token,
                        sample_rate_hz=sample_rate_hz,
                        speech_probability=speech_probability,
                        rnnoise_available=bool(rnnoise_available),
                    )
                    if not ingress_is_current():
                        return AsrSubmitResult(AsrSubmitStatus.STALE)
                    lifecycle.metrics.detector_submit_latency_ms = int(
                        (time.perf_counter() - detector_submit_started_at) * 1_000
                    )
                    lifecycle.metrics.detector_queue_audio_ms = detector.queued_audio_ms
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
                            identity.session_epoch,
                            identity.provider or "unknown",
                            status_code="ASR_ENDPOINTING_FAILED",
                            expected_identity=identity,
                        )
                        return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
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
                        forced = await detector.force_speech_started(submitted.identity)
                        if not ingress_is_current():
                            return AsrSubmitResult(AsrSubmitStatus.STALE)
                        if forced:
                            # The detector callback is queued through the
                            # session-owned dispatcher. Advance the lifecycle
                            # synchronously for this frame so fail-open upload
                            # cannot observe LOCAL_LISTEN and tear down the
                            # session before that queued event runs.
                            await self._handle_independent_asr_activity(
                                SpeechActivityEvent.SPEECH_STARTED,
                                identity.session_epoch,
                            )
                            if not ingress_is_current():
                                return AsrSubmitResult(AsrSubmitStatus.STALE)
                else:
                    detector_result = await detector.feed(
                        pcm16,
                        speech_probability=speech_probability,
                        rnnoise_available=rnnoise_available,
                    )
                    if not ingress_is_current():
                        return AsrSubmitResult(AsrSubmitStatus.STALE)
                    if not detector_result.endpointing_available:
                        await self._handle_independent_asr_error(
                            identity.session_epoch,
                            identity.provider or "unknown",
                            status_code="ASR_ENDPOINTING_FAILED",
                            expected_identity=identity,
                        )
                        return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
                    if not detector_result.throttle_available:
                        lifecycle.enable_independent_asr_fail_open()
                    else:
                        for event in detector_result.events:
                            await self._handle_independent_asr_activity(
                                event,
                                identity.session_epoch,
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
                            identity.session_epoch,
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
                    identity.session_epoch,
                    identity.provider or "unknown",
                    status_code="ASR_BLOCKED_ENDPOINTING",
                    expected_identity=identity,
                )
                return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
            turn_token = self._capture_turn_token(lifecycle)
            if (
                lifecycle.snapshot.state is not VoiceLifecycleState.ACTIVE
                or not self._asr_endpointing_ready(lifecycle, detector, turn_token)
            ):
                await self._handle_independent_asr_error(
                    identity.session_epoch,
                    identity.provider or "unknown",
                    status_code="ASR_BLOCKED_ENDPOINTING",
                    expected_identity=identity,
                )
                return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
            asr_session = self._asr_session
            if asr_session is None or not getattr(asr_session, "is_ready", True):
                if lifecycle is None:
                    await self._handle_independent_asr_error(
                        identity.session_epoch,
                        identity.provider or "unknown",
                        expected_identity=identity,
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
                        identity.session_epoch,
                        identity.provider or "unknown",
                        status_code="ASR_AUDIO_ORDERING_FAILED",
                        expected_identity=identity,
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
                    identity.session_epoch,
                    identity.provider or "unknown",
                    status_code="ASR_AUDIO_ORDERING_FAILED",
                    expected_identity=identity,
                )
                return AsrSubmitResult(AsrSubmitStatus.UNAVAILABLE)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._runtime_identity_matches(identity):
                return AsrSubmitResult(AsrSubmitStatus.STALE)
            self._asr_received_audio = True
            status_code = (
                "ASR_STREAM_BACKPRESSURE"
                if str(exc).startswith("ASR_STREAM_BACKPRESSURE:")
                else "ASR_INDEPENDENT_STREAM_FAILED"
            )
            if (
                status_code == "ASR_STREAM_BACKPRESSURE"
                and identity.lifecycle is not None
            ):
                identity.lifecycle.metrics.queue_backpressure_count += 1
            await self._handle_independent_asr_error(
                identity.session_epoch,
                identity.provider or "unknown",
                status_code=status_code,
                expected_identity=identity,
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

        await self._restart_transport(max_attempts=1)

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
            if existing is not None:
                self._asr_session = None
                detached_identity = self._capture_runtime_identity()
                await self._close_asr_session(existing)
                if not self._runtime_identity_matches(detached_identity):
                    return
            lifecycle = self._asr_lifecycle
            factory = self._asr_session_factory
            selection = self._asr_transport_selection
            identity = self._capture_runtime_identity()
            if factory is None or selection is None or lifecycle is None:
                await self._handle_independent_asr_error(
                    identity.session_epoch,
                    identity.provider or "unknown",
                    expected_identity=identity,
                )
                return

            for attempt in range(max_attempts):
                if not self._runtime_identity_matches(identity):
                    return
                if lifecycle.snapshot.state is VoiceLifecycleState.BACKOFF:
                    lifecycle.transition(VoiceLifecycleEvent.RETRY)
                    lifecycle.metrics.reconnect_count += 1
                    identity = self._capture_runtime_identity()
                    await self._send_asr_lifecycle_state(
                        VoiceLifecycleState.PREWARMING,
                        provider=identity.provider or "unknown",
                        session_epoch=identity.session_epoch,
                        expected_identity=identity,
                    )
                    if not self._runtime_identity_matches(identity):
                        return
                candidate = None
                try:
                    connect_started_at = time.monotonic()
                    candidate = factory(selection)
                    await candidate.connect()
                    if not self._runtime_identity_matches(identity):
                        await candidate.close()
                        return
                    self._asr_session = candidate
                    self._asr_last_provider_wire_audio_ms = 0
                    lifecycle.invalidate_transport()
                    connected_identity = self._capture_runtime_identity()
                    lifecycle.metrics.connect_latency_ms = int(
                        (time.monotonic() - connect_started_at) * 1_000
                    )
                    if (
                        self._asr_pending_speech_confirmed
                        and lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING
                    ):
                        detector = self._asr_detector
                        turn_token = self._capture_turn_token(lifecycle)
                        if detector is None or not self._asr_endpointing_ready(
                            lifecycle,
                            detector,
                            turn_token,
                        ):
                            await self._handle_independent_asr_error(
                                connected_identity.session_epoch,
                                connected_identity.provider or "unknown",
                                status_code="ASR_BLOCKED_ENDPOINTING",
                                expected_identity=connected_identity,
                            )
                            return
                        lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
                        self._asr_pending_speech_confirmed = False
                        self._asr_turn_audio_started_at = time.monotonic()
                        self._asr_first_partial_recorded = False
                        await self._send_asr_lifecycle_state(
                            VoiceLifecycleState.ACTIVE,
                            provider=connected_identity.provider or "unknown",
                            session_epoch=connected_identity.session_epoch,
                            expected_identity=connected_identity,
                        )
                        if not self._runtime_identity_matches(connected_identity):
                            return
                        payload = lifecycle.drain_active_start_audio()
                        await self._prepare_independent_asr_turn(
                            connected_identity.session_epoch
                        )
                        if not self._runtime_identity_matches(connected_identity):
                            return
                        if not self._activate_asr_audio_dispatcher(
                            lifecycle,
                            turn_token,
                            buffered_pcm16=payload,
                        ):
                            await self._handle_independent_asr_error(
                                connected_identity.session_epoch,
                                connected_identity.provider or "unknown",
                                status_code="ASR_AUDIO_ORDERING_FAILED",
                                expected_identity=connected_identity,
                            )
                            return
                    return
                except asyncio.CancelledError:
                    if candidate is not None and self._asr_session is candidate:
                        adopted_identity = self._capture_runtime_identity()
                        await self._handle_independent_asr_error(
                            adopted_identity.session_epoch,
                            adopted_identity.provider or "unknown",
                            status_code="ASR_INDEPENDENT_FAILED",
                            expected_identity=adopted_identity,
                        )
                    elif candidate is not None:
                        await candidate.close()
                    raise
                except Exception:
                    if candidate is not None and self._asr_session is candidate:
                        adopted_identity = self._capture_runtime_identity()
                        await self._handle_independent_asr_error(
                            adopted_identity.session_epoch,
                            adopted_identity.provider or "unknown",
                            status_code="ASR_INDEPENDENT_FAILED",
                            expected_identity=adopted_identity,
                        )
                        return
                    if candidate is not None:
                        try:
                            await candidate.close()
                        except Exception:
                            pass
                    if not self._runtime_identity_matches(identity):
                        return
                    if lifecycle.snapshot.state is VoiceLifecycleState.PREWARMING:
                        lifecycle.transition(VoiceLifecycleEvent.CONNECT_FAILED)
                        identity = self._capture_runtime_identity()
                        await self._send_asr_lifecycle_state(
                            VoiceLifecycleState.BACKOFF,
                            provider=identity.provider or "unknown",
                            session_epoch=identity.session_epoch,
                            expected_identity=identity,
                        )
                        if not self._runtime_identity_matches(identity):
                            return
                    if attempt + 1 < max_attempts:
                        await asyncio.sleep(min(1.0, 0.25 * (2**attempt)))
                        if not self._runtime_identity_matches(identity):
                            return
                        continue
            if not self._runtime_identity_matches(identity):
                return
            if lifecycle.snapshot.state is VoiceLifecycleState.BACKOFF:
                lifecycle.transition(VoiceLifecycleEvent.RETRIES_EXHAUSTED)
            await self._handle_independent_asr_error(
                identity.session_epoch,
                identity.provider or "unknown",
                status_code="ASR_INDEPENDENT_FAILED",
                expected_identity=identity,
            )

    async def _abort_transport(
        self,
        reason: str,
    ) -> _AsrRuntimeIdentity:
        """Invalidate provider I/O before closing a live transport."""

        self._begin_asr_start_operation()
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
        self._asr_current_ingress_token = None
        self._asr_turn_endpointed_at = None
        self._asr_accepted_final_keys.clear()
        lease, self._asr_smart_turn_lease = self._asr_smart_turn_lease, None
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
        post_detach = self._capture_runtime_identity()
        if lease is not None:
            await lease.release()
        if asr_session is not None:
            try:
                await asr_session.close()
            except Exception:
                logger.warning(
                    "[%s] independent ASR abort failed reason=%s",
                    self.display_name,
                    reason,
                )
        return post_detach

    async def _close_transport_only(self) -> None:
        """Enter deep sleep while preserving microphone detection."""

        epoch = self._asr_session_epoch
        provider = self._asr_provider or "unknown"
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
                identity = self._capture_runtime_identity()
                await self._send_asr_lifecycle_state(
                    VoiceLifecycleState.DEEP_SLEEP,
                    provider=provider,
                    session_epoch=epoch,
                    expected_identity=identity,
                )
        if asr_session is not None:
            try:
                await asr_session.close()
            except Exception:
                logger.warning(
                    "[%s] independent ASR transport-only close failed",
                    self.display_name,
                )

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
                if current is not None and current.snapshot.state in {
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

    async def _handle_independent_asr_activity(
        self,
        event: SpeechActivityEvent,
        epoch: int,
    ) -> None:
        if epoch != self._asr_session_epoch:
            return
        provider = self._asr_provider or "unknown"
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
        if (
            lifecycle is not None
            and lifecycle.snapshot.state is VoiceLifecycleState.WARM_IDLE
            and lifecycle.has_pending_turn
            and event
            in {
                SpeechActivityEvent.SPEECH_STARTED,
                SpeechActivityEvent.SPEECH_RESUMED,
            }
        ):
            # The DRAINING path already confirmed this pending turn. Re-marking
            # it after PROVIDER_FINAL reaches WARM_IDLE violates the lifecycle
            # guard and can fail the replacement turn during activation.
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
                identity = self._capture_runtime_identity(
                    ingress_token=self._asr_current_ingress_token,
                )
                delivered = await self._send_asr_lifecycle_state(
                    lifecycle.snapshot.state,
                    provider=provider,
                    session_epoch=epoch,
                    expected_identity=identity,
                )
                if not delivered:
                    return
            if (
                lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
                and previous_state is not VoiceLifecycleState.ACTIVE
            ):
                self._asr_turn_audio_started_at = time.monotonic()
                self._asr_first_partial_recorded = False
        if (
            event
            not in {
                SpeechActivityEvent.SPEECH_STARTED,
                SpeechActivityEvent.SPEECH_RESUMED,
            }
            or self._asr_turn_prepared
        ):
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
        if (
            lifecycle is None
            or lifecycle.snapshot.state is not VoiceLifecycleState.ACTIVE
        ):
            return
        turn_token = self._capture_turn_token(lifecycle)
        final_key = FinalKey.from_turn(turn_token)
        transcript_dispatcher = self._asr_transcript_dispatcher
        if not transcript_dispatcher.try_reserve(final_key):
            await self._handle_independent_asr_error(
                epoch,
                self._asr_provider or "unknown",
                status_code="ASR_CORE_TRANSCRIPT_BACKPRESSURE",
            )
            return
        self._asr_reserved_final_key = final_key
        self._asr_turn_prepared = True
        identity = self._capture_runtime_identity(
            ingress_token=turn_token.ingress,
            turn_token=turn_token,
        )
        try:
            accepted = await self._callbacks.on_prepare_turn(turn_token)
        except Exception:
            accepted = False
            if self._runtime_identity_matches(identity):
                logger.warning(
                    "[%s] independent ASR turn preparation failed",
                    self.display_name,
                )
        if accepted and self._runtime_identity_matches(identity):
            return
        transcript_dispatcher.release(final_key)
        if not self._runtime_identity_matches(identity):
            return
        if (
            self._asr_transcript_dispatcher is transcript_dispatcher
            and self._asr_reserved_final_key == final_key
        ):
            self._asr_reserved_final_key = None
            self._asr_turn_prepared = False

    async def _handle_independent_asr_endpoint(self, epoch: int) -> None:
        """Seal the current turn immediately at its semantic endpoint."""

        if epoch != self._asr_session_epoch:
            return
        provider = self._asr_provider or "unknown"
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
            identity = self._capture_runtime_identity(
                ingress_token=turn_token.ingress,
                turn_token=turn_token,
            )
            await self._send_asr_lifecycle_state(
                VoiceLifecycleState.DRAINING,
                provider=provider,
                session_epoch=epoch,
                expected_identity=identity,
            )

    async def _activate_pending_independent_turn(self, epoch: int) -> None:
        """Start the pending turn after the previous final completes."""

        if epoch != self._asr_session_epoch:
            return
        lifecycle = self._asr_lifecycle
        if lifecycle is None or not lifecycle.has_pending_turn:
            if lifecycle is not None:
                lifecycle.discard_unconfirmed_pending_audio()
            return
        if lifecycle.snapshot.state is not VoiceLifecycleState.WARM_IDLE:
            lifecycle.discard_pending_turn()
            self._asr_pending_detector_candidate = None
            return
        payload = lifecycle.begin_pending_turn()
        if not payload:
            return
        turn_token = self._capture_turn_token(lifecycle)
        pending_candidate = self._asr_pending_detector_candidate
        self._asr_pending_detector_candidate = None
        identity = self._capture_runtime_identity(
            ingress_token=turn_token.ingress,
            turn_token=turn_token,
        )
        self._asr_turn_audio_started_at = time.monotonic()
        self._asr_first_partial_recorded = False
        if not await self._ensure_smart_turn_ready(lifecycle, epoch):
            return
        if not self._runtime_identity_matches(identity):
            return
        delivered = await self._send_asr_lifecycle_state(
            VoiceLifecycleState.ACTIVE,
            provider=identity.provider or "unknown",
            session_epoch=epoch,
            expected_identity=identity,
        )
        if not delivered:
            return
        await self._prepare_independent_asr_turn(epoch)
        if not self._runtime_identity_matches(identity):
            return
        asr_session = identity.session
        if asr_session is None or not getattr(asr_session, "is_ready", True):
            await self._handle_independent_asr_error(
                identity.session_epoch,
                identity.provider or "unknown",
                expected_identity=identity,
            )
            return
        detector = identity.detector
        if not self._asr_endpointing_ready(lifecycle, detector, turn_token):
            await self._handle_independent_asr_error(
                identity.session_epoch,
                identity.provider or "unknown",
                status_code="ASR_BLOCKED_ENDPOINTING",
                expected_identity=identity,
            )
            return
        if pending_candidate is not None:
            assert detector is not None
            bound = await detector.bind_candidate(pending_candidate, turn_token)
            if not self._runtime_identity_matches(identity):
                return
            if bound is None:
                await self._handle_independent_asr_error(
                    identity.session_epoch,
                    identity.provider or "unknown",
                    status_code="ASR_ENDPOINTING_FAILED",
                    expected_identity=identity,
                )
                return
        elif not self._runtime_identity_matches(identity):
            return
        if not self._activate_asr_audio_dispatcher(
            lifecycle,
            turn_token,
            buffered_pcm16=payload,
        ):
            await self._handle_independent_asr_error(
                identity.session_epoch,
                identity.provider or "unknown",
                status_code="ASR_AUDIO_ORDERING_FAILED",
                expected_identity=identity,
            )
            return
        if not self._runtime_identity_matches(identity):
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
        transcript_dispatcher: TranscriptDispatcher | None = None
        final_key: FinalKey | None = None
        final_identity: _AsrRuntimeIdentity | None = None
        ordering_failure_identity: _AsrRuntimeIdentity | None = None
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
            transcript_dispatcher = self._asr_transcript_dispatcher
            if not transcript_dispatcher.try_reserve(final_key):
                ordering_failure_identity = self._capture_runtime_identity(
                    ingress_token=sealed_token.turn.ingress,
                    turn_token=sealed_token.turn,
                )
            if ordering_failure_identity is None:
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
                envelope = TranscriptEnvelope(
                    turn_token=sealed_token.turn,
                    provider=provider,
                    text=clean,
                )
                if not clean:
                    lifecycle_ref.metrics.false_wake_count += 1
                if not has_pending_turn:
                    self._schedule_transport_warm_expiry(epoch)
                final_identity = self._capture_runtime_identity(
                    ingress_token=sealed_token.turn.ingress,
                    turn_token=sealed_token.turn,
                )

        if ordering_failure_identity is not None:
            await self._handle_independent_asr_error(
                ordering_failure_identity.session_epoch,
                ordering_failure_identity.provider or provider,
                status_code="ASR_AUDIO_ORDERING_FAILED",
                expected_identity=ordering_failure_identity,
            )
            return

        assert lifecycle_ref is not None
        assert accepted_turn_token is not None
        assert transcript_dispatcher is not None
        assert final_key is not None
        assert final_identity is not None
        lease = self._asr_smart_turn_lease
        if lease is not None and lease.token == accepted_turn_token:
            self._asr_smart_turn_lease = None
            await lease.release()
            if not self._runtime_identity_matches(final_identity):
                transcript_dispatcher.release(final_key)
                return
        elif not self._runtime_identity_matches(final_identity):
            transcript_dispatcher.release(final_key)
            return
        if envelope is not None:
            try:
                transcript_dispatcher.submit(envelope)
            except RuntimeError:
                await self._handle_independent_asr_error(
                    final_identity.session_epoch,
                    final_identity.provider or provider,
                    status_code="ASR_AUDIO_ORDERING_FAILED",
                    expected_identity=final_identity,
                )
                return
        delivered = await self._send_asr_lifecycle_state(
            VoiceLifecycleState.WARM_IDLE,
            provider=provider,
            session_epoch=epoch,
            expected_identity=final_identity,
        )
        if not delivered:
            return

        await self._activate_pending_independent_turn(epoch)
        if (
            detector_ref is not None
            and self._asr_lifecycle is lifecycle_ref
            and self._asr_detector is detector_ref
        ):
            identity = self._capture_runtime_identity(
                ingress_token=self._asr_current_ingress_token,
            )
            try:
                await detector_ref.release_deferred_turn()
            except Exception:
                if not self._runtime_identity_matches(identity):
                    return
                await self._handle_independent_asr_error(
                    identity.session_epoch,
                    identity.provider or "unknown",
                    status_code="ASR_ENDPOINTING_FAILED",
                    expected_identity=identity,
                )
                return
            if not self._runtime_identity_matches(identity):
                return

    async def _dispatch_asr_transcript_envelope(
        self,
        envelope: TranscriptEnvelope,
    ) -> None:
        ingress_token = envelope.turn_token.ingress
        if not self._ingress_token_matches(ingress_token):
            return
        identity = self._capture_runtime_identity(
            ingress_token=ingress_token,
            turn_token=envelope.turn_token,
        )
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
                session_epoch=ingress_token.session_epoch,
                expected_identity=identity,
            )

    async def _wait_asr_transcript_dispatch_idle(self) -> None:
        await self._asr_transcript_dispatcher.wait_idle()

    async def _handle_independent_asr_error(
        self,
        epoch: int,
        provider: str,
        *,
        status_code: str = "ASR_INDEPENDENT_FAILED",
        expected_identity: _AsrRuntimeIdentity | None = None,
    ) -> None:
        if epoch != self._asr_session_epoch or (
            expected_identity is not None
            and not self._runtime_identity_matches(expected_identity)
        ):
            return
        # The provider callback that reported failure must not be allowed to
        # deliver a queued final into the surviving Omni session.
        self._asr_session_epoch += 1
        failure_epoch = self._asr_session_epoch
        self._asr_audio_generation += 1
        transcript_dispatcher = self._asr_transcript_dispatcher
        detector_dispatcher = self._asr_detector_dispatcher
        audio_dispatcher = self._asr_audio_dispatcher
        transcript_dispatcher.invalidate_all()
        detector_dispatcher.invalidate_all()
        audio_dispatcher.abort()
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
        asr_session, self._asr_session = self._asr_session, None
        lifecycle, self._asr_lifecycle = self._asr_lifecycle, None
        detector, self._asr_detector = self._asr_detector, None
        lease, self._asr_smart_turn_lease = self._asr_smart_turn_lease, None
        self._asr_provider = None
        self._asr_session_factory = None
        self._asr_transport_selection = None
        self._asr_current_ingress_token = None
        self._asr_pending_speech_confirmed = False
        self._asr_pending_detector_candidate = None
        self._asr_audio_sequence = 0
        self._asr_turn_endpointed_at = None
        for task_name in (
            "_asr_transport_task",
            "_asr_warm_expiry_task",
            "_asr_final_watchdog_task",
        ):
            task = getattr(self, task_name, None)
            setattr(self, task_name, None)
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        if lifecycle is not None:
            lifecycle.stop()
        if detector is not None:
            task = asyncio.create_task(detector.close())
            self._asr_close_tasks.add(task)
            task.add_done_callback(self._asr_close_tasks.discard)
        if lease is not None:
            task = asyncio.create_task(lease.release())
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
        failure_identity = self._capture_runtime_identity()
        try:
            delivered = await self._send_asr_lifecycle_state(
                VoiceLifecycleState.BLOCKED,
                provider=provider,
                session_epoch=failure_epoch,
                expected_identity=failure_identity,
            )
            if not delivered or not self._runtime_identity_matches(failure_identity):
                return
            try:
                await self._callbacks.on_failure(
                    AsrFailureEvent(
                        code=status_code,
                        provider=provider,
                        session_epoch=failure_epoch,
                    )
                )
            except Exception:
                logger.debug(
                    "[%s] independent ASR failure callback failed",
                    self.display_name,
                )
            if not self._runtime_identity_matches(failure_identity):
                return
            await self._send_asr_status(
                status_code,
                provider,
                session_epoch=failure_epoch,
                expected_identity=failure_identity,
            )
        finally:
            # A dispatcher can report its own failure from inside its worker.
            # Let lifecycle/failure/status delivery finish before closing that
            # worker, otherwise close() can cancel the authoritative callback.
            for dispatcher in (detector_dispatcher, audio_dispatcher):
                task = asyncio.create_task(dispatcher.close())
                self._asr_close_tasks.add(task)
                task.add_done_callback(self._asr_close_tasks.discard)

    async def _close_asr_session(self, asr_session: Any) -> None:
        try:
            await asr_session.close()
        except Exception:
            logger.warning(
                "[%s] independent ASR background close failed",
                self.display_name,
            )

    async def _send_asr_status(
        self,
        code: str,
        provider: str,
        *,
        session_epoch: int,
        expected_identity: _AsrRuntimeIdentity,
    ) -> bool:
        if (
            session_epoch != expected_identity.session_epoch
            or not self._runtime_identity_matches(expected_identity)
        ):
            return False
        try:
            await self._callbacks.on_status(
                AsrStatusEvent(
                    code=code,
                    provider=provider,
                    session_epoch=session_epoch,
                )
            )
        except Exception:
            logger.debug(
                "[%s] independent ASR status delivery failed",
                self.display_name,
            )
        return self._runtime_identity_matches(expected_identity)

    async def _send_asr_lifecycle_state(
        self,
        state: VoiceLifecycleState,
        *,
        provider: str,
        session_epoch: int,
        expected_identity: _AsrRuntimeIdentity,
    ) -> bool:
        if (
            session_epoch != expected_identity.session_epoch
            or not self._runtime_identity_matches(expected_identity)
        ):
            return False
        try:
            await self._callbacks.on_lifecycle(
                AsrLifecycleNotification(
                    state=state.value,
                    provider=provider,
                    session_epoch=session_epoch,
                )
            )
        except Exception:
            logger.debug(
                "[%s] ASR lifecycle status delivery failed",
                self.display_name,
            )
        return self._runtime_identity_matches(expected_identity)
