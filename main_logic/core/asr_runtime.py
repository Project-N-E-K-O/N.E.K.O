"""Production bridge between microphone audio, independent ASR, and Omni."""

from __future__ import annotations

import asyncio
import json
import time
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
from main_logic.asr_client.detector_runtime import DetectorRuntime
from main_logic.asr_client.lifecycle_contracts import (
    VoiceLifecycleEvent,
    VoiceLifecycleState,
    VoiceRouteMode,
)
from main_logic.asr_client.lifecycle_controller import (
    AudioDisposition,
    VoiceInputLifecycleController,
)
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_turn.contracts import SpeechActivityEvent

from ._shared import (
    _ASR_DUPLICATE_FINAL_WINDOW_SECONDS,
    logger,
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
        self._asr_last_final: tuple[str, float] | None = None
        self._asr_lifecycle: VoiceInputLifecycleController | None = None
        self._asr_detector: DetectorRuntime | None = None
        self._voice_input_audio_pipeline = VoiceInputAudioPipeline()

    def _ensure_asr_runtime_state(self) -> None:
        # A number of focused unit tests intentionally construct the manager via
        # __new__. Keep those narrow lifecycle doubles compatible.
        if not hasattr(self, "_asr_session_epoch"):
            self._init_asr_runtime_state()

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
            )
            _attach_partial_callback(candidate_session, on_partial)
            return candidate_session

        asr_session = None
        active_selection = selection
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
            self._asr_provider = provider
            self._asr_route_mode = "independent"
            endpointing_mode = getattr(active_selection, "endpointing_mode", None)
            if endpointing_mode not in {"manual", "provider"}:
                endpointing_mode = "provider" if provider == "soniox" else "manual"
            policy = resolve_provider_policy(provider, endpointing_mode)
            self._asr_lifecycle = VoiceInputLifecycleController(
                provider_policy=policy,
                shadow_mode=False,
            )
            self._asr_lifecycle.open(route_mode=VoiceRouteMode.INDEPENDENT)
            self._asr_detector = DetectorRuntime()
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
        self._asr_last_final = None
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
    ) -> bool:
        """Return True when this frame must not be sent to Omni."""

        self._ensure_asr_runtime_state()
        if self._asr_route_mode != "independent":
            self._asr_route_mode = "blocked"
            return True

        asr_session = self._asr_session
        if asr_session is None or not asr_session.is_ready:
            await self._handle_independent_asr_error(
                self._asr_session_epoch,
                self._asr_provider or "unknown",
            )
            return True

        try:
            lifecycle = self._asr_lifecycle
            detector = self._asr_detector
            if lifecycle is not None and detector is not None:
                detector_result = await detector.feed(pcm16)
                if not detector_result.throttle_available:
                    lifecycle.enable_independent_asr_fail_open()
                else:
                    for event in detector_result.events:
                        await self._handle_independent_asr_activity(
                            event,
                            self._asr_session_epoch,
                        )
            decision = (
                lifecycle.accept_audio(pcm16, sample_rate_hz=sample_rate_hz)
                if lifecycle is not None
                else None
            )
            if decision is not None and decision.disposition is AudioDisposition.BLOCK:
                return True
            if decision is not None and decision.disposition in {
                AudioDisposition.BUFFER,
                AudioDisposition.SUPPRESS,
            }:
                return True
            payload = (
                decision.pre_roll
                if decision is not None
                and decision.disposition is AudioDisposition.FORWARD_WITH_PRE_ROLL
                else pcm16
            )
            if not payload:
                return True
            await asr_session.stream_audio(payload, sample_rate_hz=sample_rate_hz)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._asr_received_audio = True
            status_code = (
                "ASR_STREAM_BACKPRESSURE"
                if str(exc).startswith("ASR_STREAM_BACKPRESSURE:")
                else "ASR_INDEPENDENT_STREAM_FAILED"
            )
            await self._handle_independent_asr_error(
                self._asr_session_epoch,
                self._asr_provider or "unknown",
                status_code=status_code,
            )
            return True

        self._asr_received_audio = True
        self._asr_audio_bytes += len(payload)
        return True

    def _record_omni_microphone_audio(self, byte_count: int) -> None:
        self._ensure_asr_runtime_state()
        if int(byte_count) > 0:
            raise RuntimeError("OMNI_MICROPHONE_ROUTE_FORBIDDEN")

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
        """Give game voice exclusive input ownership and invalidate pending PCM."""

        self._ensure_asr_runtime_state()
        lifecycle = self._asr_lifecycle
        if lifecycle is None or lifecycle.snapshot.state in {
            VoiceLifecycleState.OFF,
            VoiceLifecycleState.BLOCKED,
            VoiceLifecycleState.SUSPENDED,
        }:
            return
        lifecycle.transition(VoiceLifecycleEvent.GAME_TAKEOVER)
        await self._send_asr_lifecycle_state(VoiceLifecycleState.SUSPENDED)
        self._asr_turn_prepared = False
        self._asr_received_audio = False
        asr_session = self._asr_session
        if asr_session is not None and asr_session.is_ready:
            try:
                await asr_session.clear_audio_buffer()
            except Exception:
                logger.warning(
                    "[%s] provider audio clear failed during game takeover",
                    self.lanlan_name,
                )
        detector = self._asr_detector
        if detector is not None:
            try:
                await detector.reset()
            except Exception:
                logger.warning(
                    "[%s] detector reset failed during game takeover",
                    self.lanlan_name,
                )

    async def _resume_independent_voice_input_after_game(self) -> None:
        """Resume with a clean local-listen turn; never replay pre-game audio."""

        self._ensure_asr_runtime_state()
        lifecycle = self._asr_lifecycle
        if (
            lifecycle is None
            or lifecycle.snapshot.state is not VoiceLifecycleState.SUSPENDED
        ):
            return
        lifecycle.transition(VoiceLifecycleEvent.GAME_RELEASED)
        await self._send_asr_lifecycle_state(VoiceLifecycleState.LOCAL_LISTEN)
        detector = self._asr_detector
        if detector is not None:
            try:
                await detector.reset()
            except Exception:
                logger.warning(
                    "[%s] detector reset failed after game release",
                    self.lanlan_name,
                )

    async def _handle_independent_asr_activity(
        self,
        event: SpeechActivityEvent,
        epoch: int,
    ) -> None:
        if epoch != self._asr_session_epoch:
            return
        lifecycle = self._asr_lifecycle
        if lifecycle is not None and event is SpeechActivityEvent.SPEECH_STARTED:
            previous_state = lifecycle.snapshot.state
            state = lifecycle.snapshot.state
            if state in {
                VoiceLifecycleState.LOCAL_LISTEN,
                VoiceLifecycleState.DEEP_SLEEP,
            }:
                lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
                state = lifecycle.snapshot.state
            if state in {
                VoiceLifecycleState.PREWARMING,
                VoiceLifecycleState.WARM_IDLE,
            }:
                lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)
            if lifecycle.snapshot.state is not previous_state:
                await self._send_asr_lifecycle_state(lifecycle.snapshot.state)
        if event is SpeechActivityEvent.SPEECH_RESUMED:
            return
        if event is not SpeechActivityEvent.SPEECH_STARTED or self._asr_turn_prepared:
            return

        self._asr_turn_prepared = True
        session_ref = self.session
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
            return
        try:
            await self.handle_new_message()
        except Exception:
            logger.warning("[%s] independent ASR turn preparation failed", self.lanlan_name)

    async def _send_independent_asr_preview(self, text: str, epoch: int) -> None:
        """Send display-only ASR partials without writing conversation history."""

        clean = str(text or "").strip()
        if not clean or epoch != self._asr_session_epoch:
            return
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
        if not clean or epoch != self._asr_session_epoch:
            return

        async with self._asr_final_lock:
            if epoch != self._asr_session_epoch:
                return
            now = time.monotonic()
            if (
                self._asr_last_final is not None
                and self._asr_last_final[0] == clean
                and now - self._asr_last_final[1]
                <= _ASR_DUPLICATE_FINAL_WINDOW_SECONDS
            ):
                return
            self._asr_last_final = (clean, now)

            if not self._asr_turn_prepared:
                await self._handle_independent_asr_activity(
                    SpeechActivityEvent.SPEECH_STARTED,
                    epoch,
                )
            if epoch != self._asr_session_epoch:
                return

            session_ref = self.session
            try:
                accepted = await self.handle_input_transcript(
                    clean,
                    is_voice_source=True,
                    source="independent_asr",
                    metadata={"provider": provider},
                )
                if not accepted:
                    return
                if epoch != self._asr_session_epoch or session_ref is not self.session:
                    return
                submit_external_turn = getattr(
                    session_ref, "submit_external_text_turn", None
                )
                if callable(submit_external_turn) and not getattr(
                    session_ref, "_is_gemini", False
                ):
                    await submit_external_turn(
                        clean,
                        turn_id=f"asr-{epoch}-{time.monotonic_ns()}",
                    )
                else:
                    await session_ref.create_response(clean)
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._send_asr_status("ASR_INDEPENDENT_INJECTION_FAILED", provider)
            finally:
                self._asr_turn_prepared = False
                self._asr_received_audio = False
                lifecycle = self._asr_lifecycle
                if lifecycle is not None:
                    if lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE:
                        lifecycle.transition(VoiceLifecycleEvent.TURN_ENDPOINTED)
                    if lifecycle.snapshot.state is VoiceLifecycleState.DRAINING:
                        lifecycle.transition(VoiceLifecycleEvent.PROVIDER_FINAL)
                    await self._send_asr_lifecycle_state(lifecycle.snapshot.state)

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
        asr_session = self._asr_session
        self._asr_session = None
        self._asr_provider = None
        self._asr_required = True
        self._asr_route_mode = "blocked"
        await self._send_asr_lifecycle_state(VoiceLifecycleState.BLOCKED)
        lifecycle = self._asr_lifecycle
        self._asr_lifecycle = None
        if lifecycle is not None:
            lifecycle.stop()
        detector, self._asr_detector = self._asr_detector, None
        if detector is not None:
            task = asyncio.create_task(detector.close())
            self._asr_close_tasks.add(task)
            task.add_done_callback(self._asr_close_tasks.discard)
        self._asr_received_audio = False
        self._asr_turn_prepared = False
        self._asr_last_final = None
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
