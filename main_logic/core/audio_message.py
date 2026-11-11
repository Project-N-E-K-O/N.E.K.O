"""Bounded, idempotent submission of an explicitly sent recording."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import re
import time
from dataclasses import dataclass
from sqlite3 import Error as SQLiteError
from typing import Callable

from services.chat_audio_history import history_for_character
from utils.external_route_registry import is_external_route_active
from utils.theater_activity import is_theater_active
from main_logic.session_state import session_reply_in_progress


AUDIO_MESSAGE_MODE = "audio_message"
MAX_AUDIO_BYTES = 16_000 * 2 * 30
MESSAGE_LIFETIME_SECONDS = 15 * 60
_MESSAGE_ID = re.compile(r"^(\d{13})-[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$")


@dataclass(slots=True)
class AudioMessageRecord:
    digest: bytes
    expires_at: float
    state: str = "sending"
    code: str = ""


def decode_audio_message(message: dict) -> tuple[str, bytes, bytes, float]:
    message_id = message.get("request_id")
    match = _MESSAGE_ID.fullmatch(message_id) if isinstance(message_id, str) else None
    if match is None or message.get("sample_rate_hz") != 16_000:
        raise ValueError("invalid")
    created_at = int(match[1]) / 1000
    now = time.time()
    if created_at > now + 60 or now - created_at > MESSAGE_LIFETIME_SECONDS:
        raise ValueError("expired")
    encoded = message.get("audio")
    if not isinstance(encoded, str) or len(encoded) > MAX_AUDIO_BYTES * 4 // 3:
        raise ValueError("invalid")
    try:
        pcm = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid") from exc
    if not 3200 <= len(pcm) <= MAX_AUDIO_BYTES or len(pcm) % 2:
        raise ValueError("invalid")
    return message_id, pcm, hashlib.sha256(pcm).digest(), created_at + MESSAGE_LIFETIME_SECONDS + 60


class AudioMessageMixin:
    async def submit_audio_message(
        self, message: dict, websocket, *, is_current: Callable[[], bool], save_local=False
    ) -> None:
        """Reserve before submitting. Repeated IDs only report their first outcome.

        A failure after reservation is terminal, including uncertain provider
        delivery. Never resend an accepted or partially submitted recording.
        """
        request_id = message.get("request_id")
        if not isinstance(request_id, str) or len(request_id) != 50:
            request_id = None

        async def report(state: str, code: str = "") -> None:
            try:
                async with asyncio.timeout(2):
                    await websocket.send_json({
                        "type": "audio_message_result", "request_id": request_id,
                        "state": state, "code": code,
                    })
            except Exception:
                pass  # Reporting failure cannot undo a provider submission.

        character = self.lanlan_name

        async def history_changed() -> None:
            if not is_current():
                return
            try:
                async with asyncio.timeout(1):
                    await websocket.send_json({"type": "chat_audio_changed", "character": character})
            except Exception:
                pass

        submitted_at = time.time()
        try:
            request_id, pcm, digest, expires_at = decode_audio_message(message)
        except ValueError as exc:
            await report("rejected", str(exc))
            return

        def receipt_result(receipt):
            if receipt is None:
                return "failed", "unconfirmed"
            if receipt["digest"] != digest:
                return "rejected", "id_conflict"
            if receipt["state"] == "completed":
                return "completed", ""
            if receipt["state"] in ("sending", "accepted"):
                cached = self._audio_message_records.get(request_id)
                if cached is not None and cached.digest == digest:
                    return cached.state, cached.code
                return "sending", ""
            return "failed", "unconfirmed"

        try:
            history = await history_for_character(self._config_manager, character)
            if history is None:
                raise ValueError("missing character")
            receipt = await asyncio.to_thread(history.receipt, request_id)
        except (OSError, SQLiteError, ValueError):
            await report("rejected", "storage")
            return
        if receipt is not None:
            await report(*receipt_result(receipt))
            return
        now = time.time()
        for key, value in list(self._audio_message_records.items()):
            if value.state != "sending" and value.expires_at < now:
                del self._audio_message_records[key]
        existing = self._audio_message_records.get(request_id)
        if existing is not None:
            if existing.digest != digest:
                await report("rejected", "id_conflict")
            else:
                await report(existing.state, existing.code)
            return
        if (
            not is_current()
            or is_external_route_active(self.lanlan_name)
            or is_theater_active(self.lanlan_name)
            or self._audio_message_lock.locked()
            or self._starting_session_count > 0
            or session_reply_in_progress(self.session)
            or getattr(self, "_voice_lease_owner", "none") != "none"
            or len(self._audio_message_records) >= 128
        ):
            await report("rejected", "busy")
            return
        record = AudioMessageRecord(digest, expires_at)
        self._audio_message_records[request_id] = record
        target = None
        reserved = False
        accepted = False
        reply_completed = False
        transcript_recorded = False
        memory_transcript = None
        memory_queue = getattr(self, "sync_message_queue", None)

        async def update_history(**changes) -> bool | None:
            try:
                updated = await asyncio.to_thread(history.update, request_id, **changes)
            except (OSError, SQLiteError):
                return None
            if updated and save_local:
                await history_changed()
            return updated

        async def on_accepted() -> None:
            nonlocal accepted
            accepted = True
            await update_history(state="accepted")

        async def on_transcript(transcript: str, *, final: bool = True) -> bool | None:
            nonlocal transcript_recorded, memory_transcript
            text = transcript.strip()
            if owns_session() and memory_queue is not None and memory_transcript != (text, final):
                if not transcript_recorded:
                    transcript_recorded = await self.handle_input_transcript(
                        transcript, source_game_route_identity=None, audio_message_request_id=request_id,
                        audio_message_transcript_final=final,
                    )
                else:
                    memory_queue.put({
                        "type": "audio_message_memory_transcript", "request_id": request_id, "final": final,
                        "data": {"input_type": "transcript", "data": text, "request_id": request_id},
                    })
                if transcript_recorded:
                    memory_transcript = text, final
            # The receipt survives deletion for dedupe. Its playback and
            # transcript availability are independent of the delivered turn.
            if save_local and await update_history(transcript=text) is None:
                return False
            return True

        try:
            async with self._audio_message_lock, asyncio.timeout(90):
                try:
                    reservation = asyncio.create_task(asyncio.to_thread(
                        history.reserve, request_id, pcm, digest, expires_at,
                        save_local=save_local, submitted_at=submitted_at,
                    ))
                    try:
                        reserved = await asyncio.shield(reservation)
                    except asyncio.CancelledError:
                        reserved = await reservation
                        raise
                    if not reserved:
                        receipt = await asyncio.to_thread(history.receipt, request_id)
                        record.state, record.code = receipt_result(receipt)
                        return
                except (OSError, SQLiteError, ValueError):
                    self._audio_message_records.pop(request_id, None)
                    record.state, record.code = "rejected", "storage"
                    return
                if save_local:
                    await history_changed()
                if (
                    not is_current() or self._starting_session_count > 0
                    or session_reply_in_progress(self.session)
                    or getattr(self, "_voice_lease_owner", "none") != "none"
                    or is_external_route_active(character) or is_theater_active(character)
                ):
                    raise ConnectionError("superseded")
                # Retirement may join producers that use the admission lock,
                # so finish startup before acquiring that lock ourselves.
                self.reset_session_start_circuit()
                await self.start_session(
                    websocket, input_mode=AUDIO_MESSAGE_MODE,
                    user_initiated=True, request_id=request_id,
                )
                operation = self._start_operation
                if (
                    operation is None or not operation.valid
                    or operation.request_id != request_id
                    or operation.task is not asyncio.current_task()
                ):
                    raise ConnectionError("superseded")
                target = self.session
                # Shares the admission lock with proactive/context delivery.
                async with self._voice_proactive_inject_lock:
                    if (
                        not is_current() or not self.session_ready
                        or self.input_mode != AUDIO_MESSAGE_MODE or target is None
                        or is_external_route_active(self.lanlan_name)
                        or is_theater_active(self.lanlan_name)
                    ):
                        raise ConnectionError("superseded")

                    def owns_session() -> bool:
                        return (
                            is_current() and self.session is target and self.is_active
                            and self._session_generation == operation.generation
                            and self.input_mode == AUDIO_MESSAGE_MODE
                            and not is_external_route_active(self.lanlan_name)
                            and not is_theater_active(self.lanlan_name)
                        )

                    if memory_queue is not None:
                        memory_queue.put({
                            "type": "audio_message_memory_start", "request_id": request_id,
                            "context_version": getattr(memory_queue, "recent_context_version", 0),
                        })
                    await target.submit_audio_message(
                        pcm, request_id, is_current=owns_session, on_accepted=on_accepted,
                        on_transcript=on_transcript,
                    )
                    if not owns_session():
                        raise ConnectionError("superseded")
                    reply_completed = True
        except (Exception, asyncio.CancelledError) as exc:
            record.state = "failed"
            record.code = "unconfirmed"
            # Only retire this operation's session. A successor owns its own
            # connection and must never be closed by this delayed failure.
            if target is not None and self.session is target:
                self.request_end_session(by_server=True)
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            if reserved:
                completing = reply_completed and record.state == "sending"
                if completing:
                    state = "completed"
                elif accepted or getattr(target, "_audio_message_input_committed", False):
                    state = "accepted_unconfirmed"
                else:
                    state = "unconfirmed"
                stored = await update_history(state=state)
                if completing:
                    record.state, record.code = ("completed", "") if stored is not None else ("failed", "unconfirmed")
            await report(record.state, record.code)
