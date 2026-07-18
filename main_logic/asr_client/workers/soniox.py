# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Soniox realtime STT worker with semantic ``<end>`` endpointing."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

import websockets

from .._infra import (
    AsrSessionConfig,
    _AsrRequestQueue,
    _AsrWorkerEvent,
    _AsrWorkerRequest,
    _QueuedAudioHold,
)
from ..provider_policy import AsrReplayPolicy


logger = logging.getLogger(__name__)

SONIOX_REGION_URLS = {
    "us": "wss://stt-rt.soniox.com/transcribe-websocket",
    "eu": "wss://stt-rt.eu.soniox.com/transcribe-websocket",
    "jp": "wss://stt-rt.jp.soniox.com/transcribe-websocket",
}

_CONTROL_END = "<end>"
_CONTROL_FIN = "<fin>"
_CONTROL_TOKENS = frozenset({_CONTROL_END, _CONTROL_FIN})
_KEEPALIVE_SECONDS = 20.0
_CLOSE_TIMEOUT_SECONDS = 0.5
_SAFE_ROTATION_SECONDS = 295 * 60
_MAX_REPLAY_BYTES = 16_000 * 2 * 30
_RETRY_BACKOFF_BASE_SECONDS = 0.5

_ConnectionAction = Literal["shutdown", "reconnect", "reset", "rotate", "failed"]


@dataclass(frozen=True, slots=True)
class _PendingFinalize:
    generation: int
    buffer_epoch: int
    utterance_id: int


@dataclass(slots=True)
class _DeferredSonioxRequest:
    request: _AsrWorkerRequest
    audio_hold: _QueuedAudioHold | None = None

    def release(self) -> None:
        if self.audio_hold is not None:
            self.audio_hold.release()


@dataclass(slots=True)
class SonioxUtteranceState:
    generation: int = 0
    buffer_epoch: int = 0
    utterance_id: int = 1
    final_tokens: list[str] = field(default_factory=list)
    provisional_tokens: list[str] = field(default_factory=list)
    speech_started: bool = False
    completed: bool = False
    first_audio_at: float | None = None
    first_partial_reported: bool = False

    def reset_for_next(self) -> None:
        self.utterance_id += 1
        self.final_tokens.clear()
        self.provisional_tokens.clear()
        self.speech_started = False
        self.completed = False
        self.first_audio_at = None
        self.first_partial_reported = False

    def reset_transport(self, *, generation: int, buffer_epoch: int) -> None:
        self.generation = generation
        self.buffer_epoch = buffer_epoch
        self.final_tokens.clear()
        self.provisional_tokens.clear()
        self.speech_started = False
        self.completed = False
        self.first_audio_at = None
        self.first_partial_reported = False

    def reset_for_replay(self) -> None:
        self.final_tokens.clear()
        self.provisional_tokens.clear()
        self.speech_started = False
        self.completed = False
        self.first_partial_reported = False


def _soniox_config(api_key: str, config: AsrSessionConfig) -> dict[str, Any]:
    language = config.language.split("-", 1)[0].lower()
    language_hints = ["en", "ja", "es"] if language == "auto" else [language]
    return {
        "api_key": api_key,
        "model": "stt-rt-v5",
        "audio_format": "pcm_s16le",
        "sample_rate": 16_000,
        "num_channels": 1,
        "enable_endpoint_detection": config.endpointing_mode == "provider",
        "enable_language_identification": True,
        "language_hints": language_hints,
    }


def _soniox_error_code(raw_code: Any) -> tuple[str, bool]:
    code = str(raw_code or "").strip().lower()
    status = "".join(character for character in code if character.isdigit())
    if status.startswith("401"):
        return "ASR_CREDENTIALS_REJECTED", False
    if status.startswith("402"):
        return "ASR_BUDGET_EXHAUSTED", False
    if status.startswith("403"):
        return "ASR_CREDENTIALS_EXPIRED", False
    if status.startswith("429"):
        return "ASR_RATE_LIMITED", True
    if status.startswith("408") or status.startswith("500") or status.startswith("503"):
        return "ASR_SONIOX_RETRYABLE", True
    if status.startswith("413"):
        return "ASR_SONIOX_CONNECTION_LIMIT", True
    return "ASR_SONIOX_PROVIDER_ERROR", False


async def soniox_asr_worker(
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    api_key: str,
    config: AsrSessionConfig,
    *,
    region: str = "us",
    replay_policy: AsrReplayPolicy = "provider_managed",
) -> None:
    """Stream 16 kHz mono PCM and complete turns at ``<end>`` or manual ``<fin>``."""

    if region not in SONIOX_REGION_URLS:
        await response_queue.put(
            _AsrWorkerEvent(
                kind="error",
                generation=0,
                error_code="ASR_INVALID_CONFIG",
                error_message="Soniox region must be us, eu, or jp",
            )
        )
        await response_queue.put(_AsrWorkerEvent(kind="closed", generation=0))
        return
    if replay_policy not in {"none", "preconnect_only", "provider_managed"}:
        await response_queue.put(
            _AsrWorkerEvent(
                kind="error",
                generation=0,
                error_code="ASR_INVALID_CONFIG",
                error_message="Soniox replay policy is invalid",
            )
        )
        await response_queue.put(_AsrWorkerEvent(kind="closed", generation=0))
        return

    state = SonioxUtteranceState()
    replay_audio = bytearray()
    replay_complete = True
    provider_wire_audio_bytes = 0
    failure_sent = False
    ready_sent = False
    intentional_shutdown = False
    reconnect_attempted = False
    reconnect_count = 0
    audio_frame_count = 0
    audio_bytes_sent = 0
    websocket = None
    pending_finalize: _PendingFinalize | None = None
    deferred_requests: deque[_DeferredSonioxRequest] = deque()
    finalize_completed = asyncio.Event()
    finalize_completed.set()
    worker_started_at = time.monotonic()

    def defer_request(
        request: _AsrWorkerRequest,
        *,
        append_left: bool = False,
        existing: _DeferredSonioxRequest | None = None,
        audio_hold: _QueuedAudioHold | None = None,
    ) -> None:
        if existing is not None:
            if existing.request is not request or audio_hold is not None:
                raise RuntimeError("ASR_SONIOX_DEFERRED_IDENTITY_MISMATCH")
            item = existing
        else:
            item = _DeferredSonioxRequest(request=request, audio_hold=audio_hold)
        if append_left:
            deferred_requests.appendleft(item)
        else:
            deferred_requests.append(item)

    def discard_deferred_requests() -> None:
        while deferred_requests:
            item = deferred_requests.popleft()
            item.release()
            request_queue.task_done()

    async def emit_error(code: str, message: str) -> None:
        nonlocal failure_sent
        if failure_sent:
            return
        failure_sent = True
        await response_queue.put(
            _AsrWorkerEvent(
                kind="error",
                generation=state.generation,
                buffer_epoch=state.buffer_epoch,
                utterance_id=state.utterance_id,
                error_code=code,
                error_message=message,
            )
        )

    async def emit_started() -> None:
        if state.speech_started:
            return
        state.speech_started = True
        await response_queue.put(
            _AsrWorkerEvent(
                kind="utterance_started",
                generation=state.generation,
                buffer_epoch=state.buffer_epoch,
                utterance_id=state.utterance_id,
            )
        )

    async def emit_preview() -> None:
        text = "".join((*state.final_tokens, *state.provisional_tokens)).strip()
        if not text:
            return
        await emit_started()
        if not state.first_partial_reported:
            state.first_partial_reported = True
            latency_ms = (
                int((time.monotonic() - state.first_audio_at) * 1000)
                if state.first_audio_at is not None
                else -1
            )
            logger.info(
                "Soniox first partial region=%s utterance=%d latency_ms=%d",
                region,
                state.utterance_id,
                latency_ms,
            )
        await response_queue.put(
            _AsrWorkerEvent(
                kind="partial",
                generation=state.generation,
                buffer_epoch=state.buffer_epoch,
                utterance_id=state.utterance_id,
                text=text,
            )
        )

    async def complete_utterance() -> None:
        nonlocal pending_finalize, provider_wire_audio_bytes
        nonlocal reconnect_attempted, replay_complete
        if state.completed:
            return
        completion_identity = pending_finalize
        had_pending_finalize = completion_identity is not None
        if completion_identity is not None:
            state.generation = completion_identity.generation
            state.buffer_epoch = completion_identity.buffer_epoch
            state.utterance_id = completion_identity.utterance_id
        pending_finalize = None
        finalize_completed.set()
        text = "".join(state.final_tokens).strip()
        if not text:
            should_complete_empty = (
                had_pending_finalize
                or state.speech_started
                or state.provisional_tokens
                or replay_audio
            )
            if not should_complete_empty:
                return
        state.completed = True
        await emit_started()
        await response_queue.put(
            _AsrWorkerEvent(
                kind="final",
                generation=state.generation,
                buffer_epoch=state.buffer_epoch,
                utterance_id=state.utterance_id,
                text=text,
            )
        )
        endpoint_ms = (
            int((time.monotonic() - state.first_audio_at) * 1000)
            if state.first_audio_at is not None
            else -1
        )
        logger.info(
            "Soniox endpoint region=%s utterance=%d latency_ms=%d chars=%d",
            region,
            state.utterance_id,
            endpoint_ms,
            len(text),
        )
        replay_audio.clear()
        replay_complete = True
        provider_wire_audio_bytes = 0
        reconnect_attempted = False
        state.reset_for_next()

    async def receive_events(connection) -> _ConnectionAction:
        try:
            async for raw_message in connection:
                if isinstance(raw_message, bytes):
                    await emit_error(
                        "ASR_SONIOX_PROTOCOL_ERROR",
                        "Soniox returned an unexpected binary event",
                    )
                    return "failed"
                try:
                    event = json.loads(raw_message)
                except (TypeError, json.JSONDecodeError):
                    await emit_error(
                        "ASR_SONIOX_PROTOCOL_ERROR",
                        "Soniox returned an invalid event",
                    )
                    return "failed"
                if not isinstance(event, dict):
                    continue

                if event.get("error_code") or event.get("error_message"):
                    code, retryable = _soniox_error_code(event.get("error_code"))
                    request_id = str(event.get("request_id") or "")[:128]
                    logger.warning(
                        "Soniox provider error region=%s code=%s request_id=%s",
                        region,
                        code,
                        request_id,
                    )
                    if retryable and not reconnect_attempted:
                        if code == "ASR_RATE_LIMITED":
                            await asyncio.sleep(
                                _RETRY_BACKOFF_BASE_SECONDS * (2**reconnect_count)
                            )
                        return "reconnect"
                    await emit_error(code, "Soniox realtime transcription failed")
                    return "failed"

                tokens = event.get("tokens")
                if not isinstance(tokens, list):
                    continue
                provisional: list[str] = []
                saw_end = False
                saw_fin = False
                for token in tokens:
                    if not isinstance(token, dict):
                        continue
                    text = token.get("text")
                    if not isinstance(text, str):
                        continue
                    if text == _CONTROL_END:
                        saw_end = True
                        continue
                    if text == _CONTROL_FIN:
                        saw_fin = True
                        continue
                    if text in _CONTROL_TOKENS:
                        continue
                    if token.get("is_final") is True:
                        state.final_tokens.append(text)
                    else:
                        provisional.append(text)
                state.provisional_tokens = provisional
                if saw_end or (saw_fin and config.endpointing_mode == "manual"):
                    await complete_utterance()
                else:
                    await emit_preview()
        except asyncio.CancelledError:
            raise
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception:
            return "reconnect" if not reconnect_attempted else "failed"
        return "shutdown" if intentional_shutdown else "reconnect"

    async def send_requests(connection, connected_at: float) -> _ConnectionAction:
        nonlocal audio_bytes_sent, audio_frame_count, intentional_shutdown
        nonlocal pending_finalize, provider_wire_audio_bytes
        nonlocal reconnect_attempted, replay_complete
        while True:
            if deferred_requests and (
                pending_finalize is None
                or deferred_requests[0].request.kind in {"clear", "shutdown"}
            ):
                deferred_item = deferred_requests.popleft()
                request = deferred_item.request
            elif pending_finalize is not None and deferred_requests:
                deferred_item = None
                if isinstance(request_queue, _AsrRequestQueue):
                    request_task = asyncio.create_task(
                        request_queue.get_with_audio_hold()
                    )
                else:
                    request_task = asyncio.create_task(
                        request_queue.get()  # noqa: ASYNC_BLOCK - asyncio.Queue
                    )
                finalized_task = asyncio.create_task(finalize_completed.wait())
                request_saved = False
                request_ready_for_processing = False
                timed_out = False
                try:
                    done, _pending = await asyncio.wait(
                        {request_task, finalized_task},
                        timeout=_KEEPALIVE_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if request_task in done:
                        request_result = request_task.result()
                        if isinstance(request_queue, _AsrRequestQueue):
                            request, audio_hold = request_result
                        else:
                            request = request_result
                            audio_hold = None
                        defer_request(request, audio_hold=audio_hold)
                        request_saved = True
                        if request.kind in {"audio", "commit"}:
                            continue
                        request_ready_for_processing = True
                    elif finalized_task in done:
                        continue
                    else:
                        timed_out = True
                finally:
                    for task in (request_task, finalized_task):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(
                        request_task,
                        finalized_task,
                        return_exceptions=True,
                    )
                    if (
                        not request_saved
                        and request_task.done()
                        and not request_task.cancelled()
                        and request_task.exception() is None
                    ):
                        request_result = request_task.result()
                        if isinstance(request_queue, _AsrRequestQueue):
                            request, audio_hold = request_result
                        else:
                            request = request_result
                            audio_hold = None
                        defer_request(
                            request,
                            append_left=True,
                            audio_hold=audio_hold,
                        )
                if timed_out:
                    await connection.send(json.dumps({"type": "keepalive"}))
                    continue
                if not request_ready_for_processing:
                    continue
                deferred_item = deferred_requests.pop()
                request = deferred_item.request
            else:
                deferred_item = None
                try:
                    if pending_finalize is not None and isinstance(
                        request_queue, _AsrRequestQueue
                    ):
                        request, audio_hold = await asyncio.wait_for(
                            request_queue.get_with_audio_hold(),
                            timeout=_KEEPALIVE_SECONDS,
                        )
                        deferred_item = _DeferredSonioxRequest(
                            request=request,
                            audio_hold=audio_hold,
                        )
                    else:
                        request = await asyncio.wait_for(
                            request_queue.get(),  # noqa: ASYNC_BLOCK - asyncio.Queue
                            timeout=_KEEPALIVE_SECONDS,
                        )
                except asyncio.TimeoutError:
                    if (
                        time.monotonic() - connected_at >= _SAFE_ROTATION_SECONDS
                        and not replay_audio
                    ):
                        return "rotate"
                    await connection.send(json.dumps({"type": "keepalive"}))
                    continue
            request_deferred = False
            try:
                request_utterance_id = (
                    request.utterance_id
                    if request.utterance_id is not None
                    else state.utterance_id
                )
                request_identity = _PendingFinalize(
                    generation=request.generation,
                    buffer_epoch=request.buffer_epoch,
                    utterance_id=request_utterance_id,
                )
                if (
                    pending_finalize is not None
                    and request.kind in {"audio", "commit"}
                    and request_identity != pending_finalize
                ):
                    defer_request(request, existing=deferred_item)
                    deferred_item = None
                    request_deferred = True
                    continue
                state.generation = request.generation
                state.buffer_epoch = request.buffer_epoch
                state.utterance_id = request_utterance_id
                if request.kind == "audio":
                    if request.audio:
                        if state.first_audio_at is None:
                            state.first_audio_at = time.monotonic()
                        await connection.send(request.audio)
                        audio_frame_count += 1
                        audio_bytes_sent += len(request.audio)
                        provider_wire_audio_bytes += len(request.audio)
                        if replay_complete:
                            if (
                                len(replay_audio) + len(request.audio)
                                <= _MAX_REPLAY_BYTES
                            ):
                                replay_audio.extend(request.audio)
                            else:
                                replay_complete = False
                                replay_audio.clear()
                    continue
                if request.kind == "commit":
                    if config.endpointing_mode != "manual":
                        await emit_error(
                            "ASR_SONIOX_PROTOCOL_ERROR",
                            "Soniox provider endpointing received an unexpected finalize",
                        )
                        return "failed"
                    pending_finalize = _PendingFinalize(
                        generation=request.generation,
                        buffer_epoch=request.buffer_epoch,
                        utterance_id=request_utterance_id,
                    )
                    finalize_completed.clear()
                    await connection.send(json.dumps({"type": "finalize"}))
                    continue
                if request.kind == "clear":
                    pending_finalize = None
                    finalize_completed.set()
                    discard_deferred_requests()
                    replay_audio.clear()
                    replay_complete = True
                    provider_wire_audio_bytes = 0
                    reconnect_attempted = False
                    state.reset_transport(
                        generation=request.generation,
                        buffer_epoch=request.buffer_epoch,
                    )
                    state.utterance_id = request_utterance_id
                    return "reset"
                if request.kind == "shutdown":
                    intentional_shutdown = True
                    discard_deferred_requests()
                    await connection.send(b"")
                    return "shutdown"
                await emit_error(
                    "ASR_SONIOX_PROTOCOL_ERROR",
                    "Soniox worker received an unsupported command",
                )
                return "failed"
            finally:
                if not request_deferred:
                    if deferred_item is not None:
                        deferred_item.release()
                    request_queue.task_done()

    try:
        while True:
            connected_at = time.monotonic()
            websocket = await websockets.connect(
                SONIOX_REGION_URLS[region],
                close_timeout=_CLOSE_TIMEOUT_SECONDS,
            )
            await websocket.send(json.dumps(_soniox_config(api_key, config)))
            if replay_audio and replay_complete:
                await websocket.send(bytes(replay_audio))
            if pending_finalize is not None:
                await websocket.send(json.dumps({"type": "finalize"}))
            if not ready_sent:
                ready_sent = True
                await response_queue.put(
                    _AsrWorkerEvent(kind="ready", generation=state.generation)
                )

            receiver = asyncio.create_task(
                receive_events(websocket), name="soniox-asr-receiver"
            )
            sender = asyncio.create_task(
                send_requests(websocket, connected_at), name="soniox-asr-sender"
            )
            done, _ = await asyncio.wait(
                {receiver, sender}, return_when=asyncio.FIRST_COMPLETED
            )
            completed_task = receiver if receiver in done else sender
            try:
                action = await completed_task
            except asyncio.CancelledError:
                raise
            except Exception:
                action = "reconnect"
            for task in (receiver, sender):
                if not task.done():
                    task.cancel()
            await asyncio.gather(receiver, sender, return_exceptions=True)
            try:
                await websocket.close()
            except Exception:
                pass
            websocket = None

            if action == "shutdown":
                return
            if action in {"reconnect", "reset", "rotate"} and not failure_sent:
                if action == "reconnect":
                    has_wire_audio = provider_wire_audio_bytes > 0
                    if has_wire_audio and not replay_complete:
                        await emit_error(
                            "ASR_SONIOX_REPLAY_INCOMPLETE",
                            "Soniox disconnected after replay exceeded its safe limit",
                        )
                        return
                    if replay_policy == "none" or (
                        replay_policy == "preconnect_only" and has_wire_audio
                    ):
                        await emit_error(
                            "ASR_SONIOX_REPLAY_DISABLED",
                            "Soniox disconnected and replay is disabled by policy",
                        )
                        return
                    if reconnect_attempted:
                        await emit_error(
                            "ASR_SONIOX_DISCONNECTED",
                            "Soniox disconnected after one recovery attempt",
                        )
                        return
                    reconnect_attempted = True
                    reconnect_count += 1
                    state.reset_for_replay()
                continue
            return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        code, _ = _soniox_error_code(status)
        await emit_error(
            code if status else "ASR_SONIOX_WORKER_FAILED",
            "Soniox connection or transcription failed",
        )
    finally:
        discard_deferred_requests()
        if websocket is not None:
            try:
                await websocket.close()
            except Exception:
                pass
        duration_ms = int((time.monotonic() - worker_started_at) * 1000)
        logger.info(
            "Soniox connection closed region=%s duration_ms=%d reconnects=%d "
            "audio_frames=%d audio_ms=%d",
            region,
            duration_ms,
            reconnect_count,
            audio_frame_count,
            int(audio_bytes_sent / (16_000 * 2) * 1000),
        )
        await response_queue.put(
            _AsrWorkerEvent(
                kind="closed",
                generation=state.generation,
                buffer_epoch=state.buffer_epoch,
                utterance_id=state.utterance_id,
            )
        )
