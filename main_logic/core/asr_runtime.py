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
from main_logic.voice_turn.contracts import SpeechActivityEvent

from ._shared import (
    _ASR_DUPLICATE_FINAL_WINDOW_SECONDS,
    logger,
)


class AsrRuntimeMixin:
    """Own one independent ASR session for the active Core manager."""

    def _init_asr_runtime_state(self) -> None:
        self._asr_session = None
        self._asr_route_mode = "native"
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

    def _ensure_asr_runtime_state(self) -> None:
        # A number of focused unit tests intentionally construct the manager via
        # __new__. Keep those narrow lifecycle doubles compatible.
        if not hasattr(self, "_asr_session_epoch"):
            self._init_asr_runtime_state()

    async def _start_independent_asr_if_enabled(self, input_mode: str) -> None:
        """Resolve the hard microphone route before opening the input gate."""

        self._ensure_asr_runtime_state()
        next_route_mode: Literal["native", "blocked"] = (
            "blocked" if input_mode == "audio" else "native"
        )
        self._asr_required = input_mode == "audio"
        await self._close_independent_asr(next_route_mode=next_route_mode)
        self._asr_audio_bytes = 0
        self._omni_mic_audio_bytes = 0
        if input_mode != "audio":
            return

        core_type = str(getattr(self, "core_api_type", "") or "").strip().lower()
        # Remember attempted native/disabled/failed routes too. Hot-swap
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
            self._asr_required = False
            self._asr_route_mode = "native"
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
                asr_session = create_candidate(core_selection)
                await asr_session.connect()
            if epoch != self._asr_session_epoch:
                await asr_session.close()
                return
            self._asr_session = asr_session
            self._asr_provider = provider
            self._asr_route_mode = "independent"
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
        next_route_mode: Literal["native", "blocked"],
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
        self._asr_route_mode = next_route_mode
        self._asr_required = next_route_mode != "native"
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
                provider or "native",
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
        if self._asr_route_mode == "native":
            if self._asr_required:
                self._asr_route_mode = "blocked"
                return True
            return False
        if self._asr_route_mode == "blocked":
            return True

        asr_session = self._asr_session
        if asr_session is None or not asr_session.is_ready:
            await self._handle_independent_asr_error(
                self._asr_session_epoch,
                self._asr_provider or "unknown",
            )
            return True

        try:
            await asr_session.stream_audio(pcm16, sample_rate_hz=sample_rate_hz)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._asr_received_audio = True
            await self._handle_independent_asr_error(
                self._asr_session_epoch,
                self._asr_provider or "unknown",
                status_code="ASR_INDEPENDENT_STREAM_FAILED",
            )
            return True

        self._asr_received_audio = True
        self._asr_audio_bytes += len(pcm16)
        return True

    def _record_omni_microphone_audio(self, byte_count: int) -> None:
        self._ensure_asr_runtime_state()
        self._omni_mic_audio_bytes += max(0, int(byte_count))

    async def _reconcile_independent_asr_after_core_change(self) -> None:
        """Switch providers only at a completed Omni hot-swap boundary."""

        self._ensure_asr_runtime_state()
        core_type = str(getattr(self, "core_api_type", "") or "").strip().lower()
        if core_type == self._asr_core_type:
            return
        await self._start_independent_asr_if_enabled(
            str(getattr(self, "input_mode", "audio") or "audio")
        )

    async def _handle_independent_asr_activity(
        self,
        event: SpeechActivityEvent,
        epoch: int,
    ) -> None:
        if epoch != self._asr_session_epoch:
            return
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
