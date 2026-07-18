"""Production bridge between microphone audio, independent ASR, and Omni."""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from typing import Any, Literal

from main_logic import core as _core_facade
from main_logic.asr_client import (
    _attach_partial_callback,
    _create_asr_session_from_selection,
    _resolve_core_follow_selection,
    _resolve_asr_selection,
)
from main_logic.asr_client._registry_meta import CORE_ASR_ROUTES
from main_logic.asr_client.audio_pipeline import (
    ProcessedVoiceFrame,
    VoiceInputAudioPipeline,
)
from main_logic.asr_client.detector_contracts import (
    DetectorActivityEvent,
    DetectorRuntimeEvent,
    DetectorSubmitStatus,
    DetectorTurnEvent,
)
from main_logic.asr_client.detector_runtime import DetectorRuntime, SmartTurnLease
from main_logic.asr_client.lifecycle_contracts import (
    FinalKey,
    VoiceIngressToken,
    VoiceLifecycleEvent,
    VoiceLifecycleState,
    VoiceRouteMode,
    VoiceTransportToken,
    VoiceTurnToken,
)
from main_logic.asr_client.lifecycle_controller import (
    AudioDisposition,
    VoiceInputLifecycleController,
)
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_turn.contracts import SpeechActivityEvent

from ._shared import logger
from .asr_audio_dispatcher import AsrAudioDispatcher
from .asr_detector_dispatcher import (
    AsrDetectorDispatcher,
    CoreDetectorEventEnvelope,
)
from .asr_transcript_dispatcher import (
    CoreTranscriptDispatcher,
    TranscriptEnvelope,
)
from .voice_input_consumer import (
    VoiceInputConsumerBinding,
    VoiceTranscriptCallback,
    VoiceTranscriptEvent,
)


class AsrRuntimeMixin:
    """Own one independent ASR session for the active Core manager."""

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
        self._omni_mic_audio_bytes = 0
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
        self._asr_reserved_voice_consumer: VoiceInputConsumerBinding | None = None
        self._asr_transcript_dispatcher = CoreTranscriptDispatcher(
            self._dispatch_asr_transcript_envelope,
        )
        self._asr_detector_dispatcher = AsrDetectorDispatcher(
            self._dispatch_asr_detector_event,
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
        self._voice_input_consumer_bindings: dict[
            str,
            VoiceInputConsumerBinding,
        ] = {}

    def _ensure_asr_runtime_state(self) -> None:
        # A number of focused unit tests intentionally construct the manager via
        # __new__. Keep those narrow lifecycle doubles compatible.
        if not hasattr(self, "_asr_session_epoch"):
            self._init_asr_runtime_state()
        elif not hasattr(self, "_asr_transcript_dispatcher"):
            self._asr_transcript_dispatcher = CoreTranscriptDispatcher(
                self._dispatch_asr_transcript_envelope,
            )
        if not hasattr(self, "_asr_detector_dispatcher"):
            self._asr_detector_dispatcher = AsrDetectorDispatcher(
                self._dispatch_asr_detector_event,
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
        return bool(
            token.session_epoch == self._asr_session_epoch
            and token.connection_id == self._voice_lease_connection_id
            and token.lease_generation == self._voice_lease_generation
            and token.audio_generation == self._asr_audio_generation
            and lifecycle is not None
            and token.route_generation == lifecycle.snapshot.route_generation
        )

    def _voice_input_accepts_pcm(self) -> bool:
        owner = self._voice_lease_owner
        owner_has_target = owner == "core" or (
            owner == "game"
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
        """Bind an external final-text target before its MicLease takeover."""

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
        binding = VoiceInputConsumerBinding(
            owner="game",
            on_final=on_final,
        )
        self._voice_input_consumer_bindings[normalized_owner] = binding
        return binding

    def unbind_voice_input_consumer(
        self,
        binding: VoiceInputConsumerBinding,
    ) -> bool:
        """Remove a target only after MicLease has left that owner."""

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
            and detector.endpointing_ready(turn_token)
            and self._voice_input_accepts_pcm()
        )

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

    async def _dispatch_asr_detector_event(
        self,
        envelope: CoreDetectorEventEnvelope,
    ) -> None:
        event = envelope.event
        detector = self._asr_detector
        lifecycle = self._asr_lifecycle
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
            or not detector.endpointing_ready(turn_token)
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
                        self.lanlan_name,
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

    async def _start_independent_asr_if_enabled(self, input_mode: str) -> None:
        """Resolve the hard microphone route before opening the input gate."""

        self._ensure_asr_runtime_state()
        await self._close_independent_asr(next_route_mode="blocked")
        self._asr_required = input_mode == "audio"
        self._asr_audio_bytes = 0
        self._omni_mic_audio_bytes = 0
        if input_mode != "audio":
            return

        core_type = str(getattr(self, "core_api_type", "") or "").strip().lower()
        # Remember attempted disabled/failed routes too. Hot-swap
        # reconciliation should retry only when the Core route truly changes.
        self._asr_core_type = core_type

        try:
            settings = await _core_facade.aload_global_conversation_settings()
            enabled = bool(settings.get("independentAsrEnabled", False))
            optimization_value = settings.get(
                "voice_input_resource_optimization_enabled",
                settings.get("voiceInputResourceOptimizationEnabled", True),
            )
            self._voice_input_resource_optimization_enabled = (
                optimization_value is not False
            )
        except Exception:
            await self._send_asr_status(
                "ASR_INDEPENDENT_FAILED", core_type or "unknown"
            )
            return
        if not enabled:
            await self._send_asr_status("ASR_INDEPENDENT_DISABLED", core_type or "unknown")
            return
        self._asr_required = True
        self._asr_route_mode = "blocked"

        route = CORE_ASR_ROUTES.get(core_type)
        # A missing route and the intentionally blocked Free backend cannot
        # provide an independent-ASR session. The hard microphone route stays
        # blocked instead of silently falling back to Omni.
        if route is None or route.provider_key == "free":
            await self._send_asr_status("ASR_INDEPENDENT_UNAVAILABLE", core_type or "unknown")
            return

        try:
            selection = _resolve_asr_selection(core_type)
            selected_provider = getattr(selection, "provider_key", None)
            if not isinstance(selected_provider, str) or not selected_provider.strip():
                raise ValueError("invalid ASR provider selection")
            provider = selected_provider.strip().lower()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Configuration errors must not abort the already-started Core
            # session. Keep the microphone fail-closed and report only the
            # fixed status code/provider category.
            self._asr_session = None
            self._asr_provider = None
            self._asr_route_mode = "blocked"
            await self._send_asr_status(
                "ASR_INDEPENDENT_FAILED", core_type or "unknown"
            )
            return

        epoch = self._asr_session_epoch

        def create_candidate(candidate_selection: Any) -> Any:
            """Create one startup candidate with callbacks bound to its identity."""

            candidate_provider = candidate_selection.provider_key
            candidate_endpointing = getattr(
                candidate_selection,
                "endpointing_mode",
                "provider" if candidate_provider == "soniox" else "manual",
            )
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
        active_selection = selection
        connect_started_at = time.monotonic()
        try:
            asr_session = create_candidate(selection)
            try:
                await asr_session.connect()
            except asyncio.CancelledError:
                raise
            except Exception:
                if provider != "soniox" or self._asr_received_audio:
                    raise
                try:
                    await asr_session.close()
                except Exception:
                    pass
                asr_session = None
                core_selection = _resolve_core_follow_selection(core_type)
                provider = core_selection.provider_key
                active_selection = core_selection
                asr_session = create_candidate(core_selection)
                await asr_session.connect()
            if epoch != self._asr_session_epoch:
                await asr_session.close()
                return
            self._asr_session = asr_session
            self._asr_last_provider_wire_audio_ms = 0
            self._asr_provider = provider
            self._asr_route_mode = "independent"
            endpointing_mode = getattr(active_selection, "endpointing_mode", None)
            if endpointing_mode not in {"manual", "provider"}:
                endpointing_mode = "provider" if provider == "soniox" else "manual"
            policy = resolve_provider_policy(provider, endpointing_mode)
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
            self._asr_transport_selection = active_selection
            self._schedule_transport_warm_expiry(epoch)
            await self._send_asr_lifecycle_state(VoiceLifecycleState.LOCAL_LISTEN)
            await self._send_asr_status("ASR_INDEPENDENT_READY", provider)
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
                await self._send_asr_status("ASR_INDEPENDENT_FAILED", provider)

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
        provider = self._asr_provider
        asr_audio_bytes = self._asr_audio_bytes
        omni_audio_bytes = self._omni_mic_audio_bytes
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
            logger.warning("[%s] voice input audio pipeline close failed", self.lanlan_name)
        self._voice_input_audio_pipeline = VoiceInputAudioPipeline()
        self._asr_route_mode = next_route_mode
        self._asr_required = True
        self._asr_received_audio = False
        self._asr_turn_prepared = False
        self._asr_accepted_final_keys.clear()
        self._asr_reserved_final_key = None
        self._asr_reserved_voice_consumer = None
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
                logger.warning("[%s] independent ASR close failed", self.lanlan_name)
        close_tasks = tuple(self._asr_close_tasks)
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        if asr_audio_bytes or omni_audio_bytes:
            logger.info(
                "[%s] microphone route metrics provider=%s asr_audio_bytes=%d "
                "omni_mic_audio_bytes=%d",
                self.lanlan_name,
                provider or "blocked",
                asr_audio_bytes,
                omni_audio_bytes,
            )

    async def _route_microphone_audio(
        self,
        pcm16: bytes,
        *,
        sample_rate_hz: int,
        speech_probability: float | None = None,
        rnnoise_available: bool | None = None,
    ) -> bool:
        """Return True when this frame must not be sent to Omni."""

        self._ensure_asr_runtime_state()
        if self._asr_route_mode != "independent":
            self._asr_route_mode = "blocked"
            return True
        if not self._voice_input_accepts_pcm():
            return True

        try:
            lifecycle = self._asr_lifecycle
            detector = self._asr_detector
            ingress_token = (
                self._capture_ingress_token(lifecycle)
                if lifecycle is not None
                else None
            )

            def ingress_is_current() -> bool:
                return bool(
                    lifecycle is not None
                    and self._asr_lifecycle is lifecycle
                    and ingress_token is not None
                    and self._ingress_token_matches(ingress_token)
                    and self._voice_input_accepts_pcm()
                )

            if lifecycle is not None and detector is not None:
                submit_audio = getattr(detector, "submit_audio", None)
                if callable(submit_audio) and ingress_token is not None:
                    submitted = await submit_audio(
                        pcm16,
                        ingress_token=ingress_token,
                        sample_rate_hz=sample_rate_hz,
                        speech_probability=speech_probability,
                        rnnoise_available=bool(rnnoise_available),
                    )
                    if not ingress_is_current():
                        return True
                    if submitted.status is DetectorSubmitStatus.BACKPRESSURE:
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
                if decision.backpressure and ingress_token is not None:
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
                or not detector.endpointing_ready(turn_token)
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
                            or not detector.endpointing_ready(turn_token)
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
        self._asr_reserved_voice_consumer = None
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
            lifecycle.invalidate_transport()
        if asr_session is not None:
            try:
                await asr_session.close()
            except Exception:
                logger.warning(
                    "[%s] independent ASR abort failed reason=%s",
                    self.lanlan_name,
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
                    self.lanlan_name,
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

    def _record_omni_microphone_audio(self, byte_count: int) -> None:
        self._ensure_asr_runtime_state()
        if int(byte_count) > 0:
            raise RuntimeError("OMNI_MICROPHONE_ROUTE_FORBIDDEN")

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

    async def _reconcile_independent_asr_after_core_change(self) -> None:
        """Switch providers only at a completed Omni hot-swap boundary."""

        self._ensure_asr_runtime_state()
        core_type = str(getattr(self, "core_api_type", "") or "").strip().lower()
        if core_type == self._asr_core_type:
            return
        await self._start_independent_asr_if_enabled(
            str(getattr(self, "input_mode", "audio") or "audio")
        )

    async def _suspend_independent_voice_input_for_game(self) -> None:
        """Compatibility wrapper for game ownership transitions."""

        await self._apply_voice_lease_state(
            owner="game",
            hard_muted=self._voice_lease_hard_muted,
            focus_suppressed=self._voice_lease_focus_suppressed,
            reason="game_takeover",
            force_abort=True,
        )

    async def _resume_independent_voice_input_after_game(self) -> None:
        """Compatibility wrapper for returning ownership to Core."""

        await self._apply_voice_lease_state(
            owner="core",
            hard_muted=self._voice_lease_hard_muted,
            focus_suppressed=self._voice_lease_focus_suppressed,
            reason="game_release",
            force_abort=False,
        )

    def _invalidate_voice_pcm_sync(self, reason: str) -> None:
        """Apply the synchronous half of every authoritative PCM barrier."""

        self._asr_audio_generation += 1
        self._asr_transcript_dispatcher.invalidate_all()
        self._asr_reserved_final_key = None
        self._asr_reserved_voice_consumer = None
        self._asr_sealed_turn_token = None
        self._asr_turn_prepared = False
        self._asr_received_audio = False
        self._asr_pending_speech_confirmed = False
        clear_queue = getattr(self, "_clear_audio_stream_queue", None)
        if callable(clear_queue):
            clear_queue(reason)
        hot_swap_audio_cache = getattr(self, "hot_swap_audio_cache", None)
        if hot_swap_audio_cache is not None:
            hot_swap_audio_cache.clear()
        lifecycle = self._asr_lifecycle
        if lifecycle is not None:
            lifecycle.invalidate_audio()

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
        game_consumer = (
            self._voice_input_consumer_bindings.get("game")
            if owner == "game"
            else None
        )
        reasons: set[str] = set()
        if owner == "none":
            reasons.add("owner_none")
        elif owner == "game" and game_consumer is None:
            reasons.add("game")
        if hard_muted:
            reasons.add("hard_mute")
        if focus_suppressed:
            reasons.add("focus")
        self._voice_input_suppression_reasons = reasons
        self._voice_input_suppressed = bool(reasons)
        self._invalidate_voice_pcm_sync(reason)

        lifecycle = self._asr_lifecycle
        if lifecycle is not None:
            if (
                owner == "game"
                and game_consumer is None
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
                    self.lanlan_name,
                )

        current = (owner, hard_muted, focus_suppressed)
        should_abort = force_abort or self._voice_lease_requires_abort or previous != current
        self._voice_lease_requires_abort = False
        if should_abort:
            await self.abort_transport(reason)
        lifecycle = self._asr_lifecycle
        if lifecycle is not None:
            await self._send_asr_lifecycle_state(lifecycle.snapshot.state)

    def _begin_voice_input_connection(self, connection_id: str) -> bool:
        """Start a lease generation scope for the current WebSocket."""

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
        """Apply one complete MicLease snapshot, with legacy event compatibility."""

        self._ensure_asr_runtime_state()
        try:
            generation = int(lease_generation)
        except (TypeError, ValueError):
            return False
        if generation <= self._voice_lease_generation:
            return False
        normalized_event = str(event or "").strip().lower()
        allowed_events = {
            "lease_sync",
            "hard_mute",
            "hard_unmute",
            "focus_suppress",
            "focus_resume",
            "game_takeover",
            "game_release",
        }
        if normalized_event not in allowed_events:
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
            force_abort=normalized_event in {
                "hard_mute",
                "focus_suppress",
                "game_takeover",
            },
        )
        return True

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
        consumer_binding = self._current_voice_input_consumer()
        if self._voice_lease_owner == "game" and consumer_binding is None:
            return
        if not self._asr_transcript_dispatcher.try_reserve(final_key):
            await self._handle_independent_asr_error(
                epoch,
                self._asr_provider or "unknown",
                status_code="ASR_CORE_TRANSCRIPT_BACKPRESSURE",
            )
            return
        self._asr_reserved_final_key = final_key
        self._asr_reserved_voice_consumer = consumer_binding

        self._asr_turn_prepared = True
        session_ref = self.session
        if consumer_binding is not None:
            return
        handle_interruption = getattr(session_ref, "handle_interruption", None)
        try:
            ensure_arbiter = getattr(session_ref, "_ensure_response_arbiter", None)
            if callable(ensure_arbiter) and not getattr(
                session_ref, "_is_gemini", False
            ):
                arbiter = ensure_arbiter()
                arbiter.pause_dispatch()
                await arbiter.cancel_current()
            if callable(handle_interruption):
                await handle_interruption()
        except Exception:
            logger.warning("[%s] independent ASR interruption failed", self.lanlan_name)
        if epoch != self._asr_session_epoch or session_ref is not self.session:
            self._asr_transcript_dispatcher.release(final_key)
            if self._asr_reserved_final_key == final_key:
                self._asr_reserved_final_key = None
            self._asr_turn_prepared = False
            return
        try:
            await self.handle_new_message()
        except Exception:
            logger.warning("[%s] independent ASR turn preparation failed", self.lanlan_name)

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
            if detector is None or not detector.endpointing_ready(turn_token):
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
        if detector is None or not detector.endpointing_ready(turn_token):
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
        websocket = getattr(self, "websocket", None)
        send_json = getattr(websocket, "send_json", None)
        if not callable(send_json):
            return
        turn_id = str(
            getattr(self, "current_speech_id", None) or f"asr-preview-{epoch}"
        )
        try:
            await send_json(
                {
                    "type": "user_transcript_preview",
                    "text": clean,
                    "turn_id": turn_id,
                }
            )
        except Exception:
            logger.debug("[%s] independent ASR preview delivery failed", self.lanlan_name)

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
            consumer_binding = self._asr_reserved_voice_consumer
            lifecycle_ref.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
            detector_ref = self._asr_detector
            self._asr_turn_prepared = False
            self._asr_received_audio = False
            self._asr_sealed_turn_token = None
            self._asr_turn_endpointed_at = None
            self._asr_reserved_final_key = None
            self._asr_reserved_voice_consumer = None
            watchdog = self._asr_final_watchdog_task
            self._asr_final_watchdog_task = None
            if watchdog is not None and watchdog is not asyncio.current_task():
                watchdog.cancel()
            if clean:
                envelope = TranscriptEnvelope(
                    turn_token=sealed_token.turn,
                    core_session_ref=self.session,
                    provider=provider,
                    text=clean,
                    consumer_binding=consumer_binding,
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

        if (
            detector_ref is not None
            and not has_pending_turn
            and self._asr_lifecycle is lifecycle_ref
            and self._asr_detector is detector_ref
        ):
            try:
                await detector_ref.reset()
            except Exception:
                logger.warning(
                    "[%s] detector reset failed after ASR final",
                    self.lanlan_name,
                )

        await self._activate_pending_independent_turn(epoch)
        if (
            has_pending_turn
            and detector_ref is not None
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
        session_ref = envelope.core_session_ref
        if not self._ingress_token_matches(ingress_token):
            return
        consumer_binding = envelope.consumer_binding
        if consumer_binding is not None:
            if (
                self._voice_lease_owner != consumer_binding.owner
                or self._voice_input_consumer_bindings.get(
                    consumer_binding.owner
                ) is not consumer_binding
            ):
                return
            try:
                await consumer_binding.on_final(
                    VoiceTranscriptEvent(
                        turn_token=envelope.turn_token,
                        provider=envelope.provider,
                        text=envelope.text,
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "[%s] external voice transcript consumer failed owner=%s",
                    self.lanlan_name,
                    consumer_binding.owner,
                )
            return
        if self._voice_lease_owner != "core" or session_ref is not self.session:
            return
        try:
            accepted = await self.handle_input_transcript(
                envelope.text,
                is_voice_source=True,
                source="independent_asr",
                metadata={"provider": envelope.provider},
            )
            if not accepted:
                return
            if (
                not self._ingress_token_matches(ingress_token)
                or session_ref is not self.session
            ):
                return
            submit_external_turn = getattr(
                session_ref,
                "submit_external_text_turn",
                None,
            )
            if callable(submit_external_turn) and not getattr(
                session_ref,
                "_is_gemini",
                False,
            ):
                await submit_external_turn(
                    envelope.text,
                    turn_id=(
                        f"asr-{ingress_token.session_epoch}-"
                        f"{envelope.turn_token.turn_id}"
                    ),
                )
            else:
                await session_ref.create_response(envelope.text)
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
        self._asr_reserved_voice_consumer = None
        self._asr_sealed_turn_token = None
        if asr_session is not None:
            task = asyncio.create_task(self._close_asr_session(asr_session))
            self._asr_close_tasks.add(task)
            task.add_done_callback(self._asr_close_tasks.discard)
        await self._send_asr_status(status_code, provider)

    async def _close_asr_session(self, asr_session: Any) -> None:
        try:
            await asr_session.close()
        except Exception:
            logger.warning("[%s] independent ASR background close failed", self.lanlan_name)

    async def _send_asr_status(self, code: str, provider: str) -> None:
        try:
            await self.send_status(
                json.dumps(
                    {
                        "code": code,
                        "details": {"provider": provider},
                    }
                )
            )
        except Exception:
            logger.debug("[%s] independent ASR status delivery failed", self.lanlan_name)

    async def _send_asr_lifecycle_state(self, state: VoiceLifecycleState) -> None:
        try:
            await self.send_status(
                json.dumps(
                    {
                        "code": "ASR_LIFECYCLE_STATE",
                        "details": {
                            "provider": self._asr_provider or "",
                            "state": state.value,
                            "route_mode": self._asr_route_mode,
                        },
                    }
                )
            )
        except Exception:
            logger.debug("[%s] ASR lifecycle status delivery failed", self.lanlan_name)
