"""Production bridge between microphone audio, independent ASR, and Omni."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from main_logic import core as _core_facade
from main_logic.asr_client import (
    _attach_partial_callback,
    _create_core_follow_asr_session,
    _resolve_asr_selection,
    create_asr_session,
)
from main_logic.asr_client._registry_meta import CORE_ASR_ROUTES
from main_logic.voice_turn.contracts import SpeechActivityEvent

from ._shared import (
    _ASR_DUPLICATE_FINAL_WINDOW_SECONDS,
    _ASR_FALLBACK_SILENCE_SECONDS,
    logger,
)


class AsrRuntimeMixin:
    """Own one independent ASR session for the active Core manager."""

    def _init_asr_runtime_state(self) -> None:
        self._asr_session = None
        self._asr_route_mode = "native"
        self._asr_session_epoch = 0
        self._asr_provider = None
        self._asr_core_type = None
        self._asr_turn_prepared = False
        self._asr_final_lock = asyncio.Lock()
        self._asr_fallback_pending = False
        self._asr_audio_bytes = 0
        self._omni_mic_audio_bytes = 0
        self._asr_received_audio = False
        self._asr_fallback_task: asyncio.Task[None] | None = None
        self._asr_close_tasks: set[asyncio.Task[None]] = set()
        self._asr_last_final: tuple[str, float] | None = None

    def _ensure_asr_runtime_state(self) -> None:
        # A number of focused unit tests intentionally construct the manager via
        # __new__. Keep those narrow lifecycle doubles compatible.
        if not hasattr(self, "_asr_session_epoch"):
            self._init_asr_runtime_state()

    async def _start_independent_asr_if_enabled(self, input_mode: str) -> None:
        """Create ASR before opening the input gate; failures keep Omni native."""

        self._ensure_asr_runtime_state()
        await self._close_independent_asr()
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
            enabled = False
        if not enabled:
            return

        route = CORE_ASR_ROUTES.get(core_type)
        selection = _resolve_asr_selection(core_type) if route is not None else None
        # Qwen Realtime's public WebSocket protocol accepts microphone audio
        # turns, but not external user-role text turns.  Sending an ASR final
        # through conversation.item.create is silently ignored by the current
        # service, so keep native Omni audio instead of reporting a false
        # independent-ASR success.  qwen_intl maps to the same provider key.
        if (
            route is None
            or route.provider_key == "free"
        ):
            await self._send_asr_status("ASR_INDEPENDENT_UNAVAILABLE", core_type or "unknown")
            return

        epoch = self._asr_session_epoch
        provider = selection.provider_key

        async def on_final(text: str) -> None:
            await self._handle_independent_asr_final(text, epoch, provider)

        async def on_error(_message: str) -> None:
            await self._handle_independent_asr_error(epoch, provider)

        async def on_status(_message: str) -> None:
            # Provider status strings are intentionally not forwarded verbatim.
            return None

        async def on_activity(event: SpeechActivityEvent) -> None:
            await self._handle_independent_asr_activity(event, epoch)

        asr_session = None
        try:
            asr_session = create_asr_session(
                core_type,
                on_input_transcript=on_final,
                on_connection_error=on_error,
                on_status_message=on_status,
                on_speech_activity=on_activity,
            )
            _attach_partial_callback(
                asr_session,
                lambda text: self._send_independent_asr_preview(text, epoch),
            )
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
                provider = route.provider_key
                asr_session = _create_core_follow_asr_session(
                    core_type,
                    on_input_transcript=on_final,
                    on_connection_error=on_error,
                    on_status_message=on_status,
                    on_speech_activity=on_activity,
                )
                _attach_partial_callback(
                    asr_session,
                    lambda text: self._send_independent_asr_preview(text, epoch),
                )
                await asr_session.connect()
            if epoch != self._asr_session_epoch:
                await asr_session.close()
                return
            self._asr_session = asr_session
            self._asr_provider = provider
            self._asr_route_mode = "independent"
            self._asr_fallback_pending = False
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
                self._asr_route_mode = "native"
                self._asr_fallback_pending = False
                await self._send_asr_status("ASR_INDEPENDENT_FALLBACK", provider)

    async def _close_independent_asr(self) -> None:
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
        self._asr_route_mode = "native"
        self._asr_fallback_pending = False
        self._asr_received_audio = False
        self._asr_turn_prepared = False
        self._asr_last_final = None
        fallback_task = self._asr_fallback_task
        self._asr_fallback_task = None
        if fallback_task is not None:
            fallback_task.cancel()
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
            return False
        if self._asr_route_mode == "fallback_pending":
            self._schedule_native_fallback_after_silence()
            return True

        asr_session = self._asr_session
        if asr_session is None or not asr_session.is_ready:
            if self._asr_received_audio:
                self._asr_route_mode = "fallback_pending"
                self._asr_fallback_pending = True
                self._schedule_native_fallback_after_silence()
                return True
            self._asr_route_mode = "native"
            return False

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
        if self._asr_received_audio:
            self._asr_route_mode = "fallback_pending"
            self._asr_fallback_pending = True
            self._schedule_native_fallback_after_silence()
        else:
            self._asr_route_mode = "native"
            self._asr_fallback_pending = False
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

    def _schedule_native_fallback_after_silence(self) -> None:
        task = self._asr_fallback_task
        if task is not None and not task.done():
            return

        epoch = self._asr_session_epoch

        async def activate() -> None:
            await asyncio.sleep(_ASR_FALLBACK_SILENCE_SECONDS)
            if epoch != self._asr_session_epoch:
                return
            self._asr_route_mode = "native"
            self._asr_fallback_pending = False
            self._asr_received_audio = False
            self._asr_turn_prepared = False

        self._asr_fallback_task = asyncio.create_task(
            activate(),
            name="independent-asr-native-fallback",
        )

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
