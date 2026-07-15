"""Manager-facing glue for the independent ASR runtime."""

from __future__ import annotations

import asyncio
import json
import os

from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.voice_turn.contracts import SmartTurnConfig
from utils.config_manager import _as_bool

from ._shared import logger
from .asr_runtime import ExternalTextTurn, IndependentAsrRuntime


class AsrOrchestrationMixin:
    def _independent_asr_requested(self, core_config: dict) -> bool:
        configured = core_config.get("INDEPENDENT_ASR_ENABLED")
        raw = os.getenv("NEKO_INDEPENDENT_ASR_ENABLED", configured)
        return _as_bool(raw, False)

    async def _start_independent_asr_if_needed(self, core_config: dict) -> None:
        requested = self.input_mode == "audio" and self._independent_asr_requested(
            core_config
        )
        self._independent_asr_enabled = requested
        if not requested:
            await self._stop_independent_asr()
            return
        if getattr(self, "_external_asr_runtime", None) is not None:
            return

        config = SmartTurnConfig(
            enabled=True,
            evaluation_threshold=float(
                os.getenv(
                    "NEKO_SMART_TURN_THRESHOLD",
                    str(core_config.get("SMART_TURN_THRESHOLD", 0.5)),
                )
            ),
            candidate_silence_ms=int(
                os.getenv(
                    "NEKO_SMART_TURN_CANDIDATE_SILENCE_MS",
                    str(core_config.get("SMART_TURN_CANDIDATE_SILENCE_MS", 300)),
                )
            ),
        )
        runtime = IndependentAsrRuntime(
            core_type=str(
                os.getenv("NEKO_INDEPENDENT_ASR_CORE_TYPE", "")
                or core_config.get("INDEPENDENT_ASR_CORE_TYPE")
                or self.core_api_type
            ),
            language=str(
                os.getenv("NEKO_INDEPENDENT_ASR_LANGUAGE", "")
                or core_config.get("INDEPENDENT_ASR_LANGUAGE")
                or "zh"
            ),
            on_caption=self._handle_external_asr_caption,
            on_turn_complete=self._handle_external_asr_turn,
            on_speech_started=self._handle_external_asr_speech_started,
            on_connection_error=self.handle_connection_error,
            on_status_message=self.send_status,
            smart_turn_config=config,
            routing_mode=str(
                os.getenv("ASR_ROUTING_MODE")
                or core_config.get("ASR_ROUTING_MODE")
                or "auto"
            ),
            user_region=str(
                os.getenv("ASR_USER_REGION")
                or core_config.get("ASR_USER_REGION")
                or "cn"
            ),
        )
        try:
            await runtime.start()
        except Exception:
            self._independent_asr_enabled = False
            try:
                await runtime.close()
            except Exception as close_exc:
                logger.warning(
                    "[%s] failed ASR startup cleanup also failed: %s",
                    self.lanlan_name,
                    type(close_exc).__name__,
                )
            raise
        self._external_asr_runtime = runtime
        logger.info(
            "[%s] independent ASR ready provider=%s turn_boundary=%s",
            self.lanlan_name,
            runtime.provider_key,
            runtime.turn_boundary_owner,
        )

    async def _stop_independent_asr(self) -> None:
        runtime = getattr(self, "_external_asr_runtime", None)
        self._external_asr_runtime = None
        self._independent_asr_enabled = False
        if runtime is not None:
            try:
                await runtime.close()
            except Exception as exc:
                logger.warning("[%s] independent ASR close failed: %s", self.lanlan_name, exc)

    async def _stream_to_independent_asr(self, pcm16_le: bytes) -> bool:
        runtime = getattr(self, "_external_asr_runtime", None)
        if not getattr(self, "_independent_asr_enabled", False) or runtime is None:
            return False
        await runtime.feed_audio(pcm16_le)
        return True

    async def _handle_external_asr_caption(
        self, text: str, is_final: bool, turn_id: str | None
    ) -> None:
        """Publish a non-persistent caption preview only.

        The frontend preview is replaced in place and never enters the local
        history. The authoritative full transcript is emitted later by the one
        ``handle_input_transcript`` call in ``_handle_external_asr_turn``.
        """
        websocket = self.websocket
        if not websocket or not hasattr(websocket, "client_state"):
            return
        if websocket.client_state != websocket.client_state.CONNECTED:
            return
        try:
            await websocket.send_json(
                {
                    "type": "user_transcript_preview",
                    "text": text,
                    "is_final": bool(is_final),
                    "turn_id": turn_id,
                }
            )
        except Exception as exc:
            logger.debug("external ASR caption preview failed: %s", exc)

    async def _handle_external_asr_speech_started(self) -> None:
        session = self.session
        if not isinstance(session, OmniRealtimeClient):
            return
        session._ensure_response_arbiter().pause_dispatch()
        # Stop local playback first and rotate the speech id so late packets
        # from the cancelled response cannot leak into the new user turn.
        await self.handle_new_message()
        if session.is_active_response():
            try:
                await session.cancel_response(wait=True, timeout=3.0)
            except Exception as exc:
                logger.warning("external ASR interruption cancel failed: %s", exc)

    async def _handle_external_asr_turn(self, turn: ExternalTextTurn) -> None:
        if not self._independent_asr_enabled:
            return
        if self._takeover_active or self._takeover_input_dispatcher is not None:
            await self.handle_input_transcript(
                turn.text,
                is_voice_source=True,
                source="independent_asr",
                metadata={"turn_id": turn.turn_id},
            )
            if isinstance(self.session, OmniRealtimeClient):
                self.session._ensure_response_arbiter().resume_dispatch()
            return
        if self._should_suppress_dirty_voice_transcript(turn.text):
            await self.handle_input_transcript(
                turn.text,
                is_voice_source=True,
                source="independent_asr",
                metadata={"turn_id": turn.turn_id},
            )
            if isinstance(self.session, OmniRealtimeClient):
                self.session._ensure_response_arbiter().resume_dispatch()
            return

        await self.handle_input_transcript(
            turn.text,
            is_voice_source=True,
            source="independent_asr",
            metadata={
                "turn_id": turn.turn_id,
                "generation": turn.generation,
                "buffer_epoch": turn.buffer_epoch,
                "utterance_ids": list(turn.utterance_ids),
            },
        )
        session = self.session
        if not isinstance(session, OmniRealtimeClient) or not self.is_active:
            if isinstance(session, OmniRealtimeClient):
                session._ensure_response_arbiter().resume_dispatch()
            return
        try:
            ticket = await session.submit_external_text_turn(
                turn.text, turn_id=turn.turn_id
            )
        except Exception as exc:
            logger.error(
                "external ASR submit failed turn=%s error=%s",
                turn.turn_id,
                type(exc).__name__,
            )
            await self.send_status(
                json.dumps(
                    {
                        "code": "EXTERNAL_ASR_RESPONSE_FAILED",
                        "details": {"turn_id": turn.turn_id},
                    }
                )
            )
            return
        self._fire_task(self._watch_external_asr_response(turn.turn_id, ticket))

    async def _watch_external_asr_response(self, turn_id: str, ticket) -> None:
        try:
            result = await ticket.done
            logger.info(
                "external_turn response_done turn=%s item_ack=%s persistence_uncertain=%s",
                turn_id,
                result.item_acknowledged,
                result.context_persistence_uncertain,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "external_turn response_failed turn=%s error=%s",
                turn_id,
                type(exc).__name__,
            )
            if isinstance(exc, asyncio.TimeoutError):
                await self.handle_connection_error(
                    json.dumps(
                        {
                            "code": "REALTIME_RESPONSE_LIFECYCLE_TIMEOUT",
                            "details": {"turn_id": turn_id},
                        }
                    )
                )
