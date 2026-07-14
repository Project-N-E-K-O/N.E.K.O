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

"""Qwen-ASR Realtime worker for the China and international regions."""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, TypeAlias

import websockets
from websockets.exceptions import ConnectionClosed

from .._infra import AsrSessionConfig, _AsrWorkerEvent, _AsrWorkerRequest

_QWEN_MODEL = "qwen3-asr-flash-realtime"
_QWEN_CN_URL = f"wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model={_QWEN_MODEL}"
_QWEN_INTL_URL = (
    f"wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime?model={_QWEN_MODEL}"
)
_QWEN_FINISH_TIMEOUT_SECONDS = 3.0
_QWEN_SUPPORTED_LANGUAGES = frozenset(
    {
        "ar",
        "cs",
        "da",
        "de",
        "en",
        "es",
        "fi",
        "fil",
        "fr",
        "hi",
        "id",
        "is",
        "it",
        "ja",
        "ko",
        "ms",
        "no",
        "pl",
        "pt",
        "ru",
        "sv",
        "th",
        "tr",
        "uk",
        "vi",
        "yue",
        "zh",
    }
)

_ItemKey: TypeAlias = tuple[int, int, int]


@dataclass(slots=True)
class _QwenConnectionState:
    generation: int
    buffer_epoch: int
    next_utterance_id: int
    emit_ready: bool
    item_keys: dict[str, _ItemKey] = field(default_factory=dict)
    pending_manual_commits: deque[_ItemKey] = field(default_factory=deque)
    configured: asyncio.Event = field(default_factory=asyncio.Event)
    finish_received: asyncio.Event = field(default_factory=asyncio.Event)
    intentional_close: asyncio.Event = field(default_factory=asyncio.Event)
    error_sent: asyncio.Event = field(default_factory=asyncio.Event)
    closed_sent: asyncio.Event = field(default_factory=asyncio.Event)
    last_utterance_id: int | None = None
    # Legacy DashScope domains can omit the documented ``item_id`` fields.
    # Their manual stream is ordered, so retain the head commit until final.
    legacy_manual_key: _ItemKey | None = None
    shutdown_request: _AsrWorkerRequest | None = None


def _qwen_event_id() -> str:
    return f"event_{uuid.uuid4().hex}"


def _qwen_language_code(language: str) -> str | None:
    normalized = language.strip().lower()
    if normalized == "auto":
        return None
    code = normalized.split("-", 1)[0]
    if code not in _QWEN_SUPPORTED_LANGUAGES:
        raise ValueError("unsupported Qwen ASR language")
    return code


def _qwen_is_auth_rejection(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is None:
        status_code = getattr(exc, "status_code", None)
    return status_code in {401, 403}


def _qwen_session_update(
    config: AsrSessionConfig,
    language: str | None,
) -> dict[str, Any]:
    if config.endpointing_mode == "manual":
        turn_detection: dict[str, str] | None = None
    elif config.endpointing_mode == "provider":
        turn_detection = {"type": "server_vad"}
    else:
        raise ValueError("unsupported Qwen ASR endpointing mode")

    transcription: dict[str, str] = {}
    if language is not None:
        transcription["language"] = language
    return {
        "event_id": _qwen_event_id(),
        "type": "session.update",
        "session": {
            "modalities": ["text"],
            "input_audio_format": "pcm",
            "sample_rate": 16000,
            "input_audio_transcription": transcription,
            "turn_detection": turn_detection,
        },
    }


async def _emit_qwen_error_once(
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    state: _QwenConnectionState,
    error_code: str,
    error_message: str,
    *,
    item_key: _ItemKey | None = None,
) -> None:
    if state.error_sent.is_set():
        return
    state.error_sent.set()
    generation, buffer_epoch, utterance_id = item_key or (
        state.generation,
        state.buffer_epoch,
        state.last_utterance_id,
    )
    await response_queue.put(
        _AsrWorkerEvent(
            kind="error",
            generation=generation,
            buffer_epoch=buffer_epoch,
            utterance_id=utterance_id,
            error_code=error_code,
            error_message=error_message,
        )
    )


async def _qwen_sender(
    ws: Any,
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    config: AsrSessionConfig,
    state: _QwenConnectionState,
) -> tuple[str, _AsrWorkerRequest | None]:
    await state.configured.wait()
    try:
        while True:
            request = await request_queue.get()
            try:
                if request.kind == "audio":
                    state.last_utterance_id = request.utterance_id
                    await ws.send(
                        json.dumps(
                            {
                                "event_id": _qwen_event_id(),
                                "type": "input_audio_buffer.append",
                                "audio": base64.b64encode(request.audio).decode(
                                    "ascii"
                                ),
                            }
                        )
                    )
                    continue

                if request.kind == "commit":
                    if config.endpointing_mode != "manual":
                        await _emit_qwen_error_once(
                            response_queue,
                            state,
                            "ASR_QWEN_PROTOCOL_ERROR",
                            "Qwen ASR received commit while server VAD is active",
                        )
                        return "error", request
                    if request.utterance_id is None:
                        await _emit_qwen_error_once(
                            response_queue,
                            state,
                            "ASR_QWEN_PROTOCOL_ERROR",
                            "Qwen ASR commit is missing an utterance identifier",
                        )
                        return "error", request
                    key = (
                        request.generation,
                        request.buffer_epoch,
                        request.utterance_id,
                    )
                    state.pending_manual_commits.append(key)
                    await ws.send(
                        json.dumps(
                            {
                                "event_id": _qwen_event_id(),
                                "type": "input_audio_buffer.commit",
                            }
                        )
                    )
                    continue

                if request.kind == "clear":
                    state.intentional_close.set()
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    return "clear", request

                if request.kind == "shutdown":
                    state.shutdown_request = request
                    await ws.send(
                        json.dumps(
                            {
                                "event_id": _qwen_event_id(),
                                "type": "session.finish",
                            }
                        )
                    )
                    try:
                        await asyncio.wait_for(
                            state.finish_received.wait(),
                            timeout=_QWEN_FINISH_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        if not state.closed_sent.is_set():
                            state.closed_sent.set()
                            await response_queue.put(
                                _AsrWorkerEvent(
                                    kind="closed",
                                    generation=request.generation,
                                    buffer_epoch=request.buffer_epoch,
                                    utterance_id=request.utterance_id,
                                )
                            )
                    state.intentional_close.set()
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    return "shutdown", request

                await _emit_qwen_error_once(
                    response_queue,
                    state,
                    "ASR_QWEN_PROTOCOL_ERROR",
                    "Qwen ASR received an unsupported command",
                )
                return "error", request
            finally:
                request_queue.task_done()
    except asyncio.CancelledError:
        raise
    except ConnectionClosed:
        if not state.intentional_close.is_set():
            await _emit_qwen_error_once(
                response_queue,
                state,
                "ASR_QWEN_CONNECTION_CLOSED",
                "Qwen ASR connection closed unexpectedly",
            )
        return "error", None
    except Exception:
        await _emit_qwen_error_once(
            response_queue,
            state,
            "ASR_QWEN_WORKER_FAILED",
            "Qwen ASR sender failed",
        )
        return "error", None


async def _qwen_receiver(
    ws: Any,
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    config: AsrSessionConfig,
    state: _QwenConnectionState,
) -> str:
    try:
        async for raw_message in ws:
            try:
                event = json.loads(raw_message)
            except (TypeError, ValueError):
                await _emit_qwen_error_once(
                    response_queue,
                    state,
                    "ASR_QWEN_PROTOCOL_ERROR",
                    "Qwen ASR returned an invalid event",
                )
                return "error"

            event_type = event.get("type")
            if event_type == "session.updated":
                if not state.configured.is_set():
                    state.configured.set()
                    if state.emit_ready:
                        await response_queue.put(
                            _AsrWorkerEvent(
                                kind="ready",
                                generation=state.generation,
                                buffer_epoch=state.buffer_epoch,
                            )
                        )
                continue

            if event_type in (
                "error",
                "conversation.item.input_audio_transcription.failed",
            ):
                if state.intentional_close.is_set():
                    return "closed"
                item_id = str(event.get("item_id") or "")
                await _emit_qwen_error_once(
                    response_queue,
                    state,
                    "ASR_QWEN_PROVIDER_ERROR",
                    "Qwen ASR provider reported an error",
                    item_key=(
                        state.item_keys.get(item_id)
                        if item_id
                        else state.legacy_manual_key
                    ),
                )
                return "error"

            if event_type == "conversation.item.created":
                if config.endpointing_mode != "manual":
                    continue
                item = event.get("item")
                item_id = str(item.get("id") or "") if isinstance(item, dict) else ""
                if not state.pending_manual_commits:
                    continue
                if item_id:
                    state.item_keys.setdefault(
                        item_id, state.pending_manual_commits.popleft()
                    )
                elif state.legacy_manual_key is None:
                    state.legacy_manual_key = state.pending_manual_commits[0]
                continue

            if event_type == "input_audio_buffer.speech_started":
                if config.endpointing_mode != "provider":
                    continue
                item_id = str(event.get("item_id") or "")
                if not item_id or item_id in state.item_keys:
                    continue
                key = (
                    state.generation,
                    state.buffer_epoch,
                    state.next_utterance_id,
                )
                state.next_utterance_id += 1
                state.last_utterance_id = key[2]
                state.item_keys[item_id] = key
                await response_queue.put(
                    _AsrWorkerEvent(
                        kind="utterance_started",
                        generation=key[0],
                        buffer_epoch=key[1],
                        utterance_id=key[2],
                    )
                )
                continue

            if event_type == "input_audio_buffer.committed":
                item_id = str(event.get("item_id") or "")
                if (
                    config.endpointing_mode == "manual"
                    and item_id
                    and item_id not in state.item_keys
                    and state.pending_manual_commits
                ):
                    state.item_keys[item_id] = state.pending_manual_commits.popleft()
                elif (
                    config.endpointing_mode == "manual"
                    and not item_id
                    and state.legacy_manual_key is None
                    and state.pending_manual_commits
                ):
                    state.legacy_manual_key = state.pending_manual_commits[0]
                continue

            if event_type == "conversation.item.input_audio_transcription.text":
                item_id = str(event.get("item_id") or "")
                key = (
                    state.item_keys.get(item_id) if item_id else state.legacy_manual_key
                )
                if key is not None:
                    text = str(event.get("text") or "") + str(event.get("stash") or "")
                    await response_queue.put(
                        _AsrWorkerEvent(
                            kind="partial",
                            generation=key[0],
                            buffer_epoch=key[1],
                            utterance_id=key[2],
                            text=text,
                        )
                    )
                continue

            if event_type == "conversation.item.input_audio_transcription.completed":
                item_id = str(event.get("item_id") or "")
                key = (
                    state.item_keys.pop(item_id, None)
                    if item_id
                    else state.legacy_manual_key
                )
                if key is not None:
                    await response_queue.put(
                        _AsrWorkerEvent(
                            kind="final",
                            generation=key[0],
                            buffer_epoch=key[1],
                            utterance_id=key[2],
                            text=str(event.get("transcript") or ""),
                        )
                    )
                    if not item_id:
                        state.legacy_manual_key = None
                        if (
                            state.pending_manual_commits
                            and state.pending_manual_commits[0] == key
                        ):
                            state.pending_manual_commits.popleft()
                continue

            if event_type == "session.finished":
                state.finish_received.set()
                if not state.closed_sent.is_set():
                    state.closed_sent.set()
                    request = state.shutdown_request
                    await response_queue.put(
                        _AsrWorkerEvent(
                            kind="closed",
                            generation=(
                                request.generation if request else state.generation
                            ),
                            buffer_epoch=(
                                request.buffer_epoch if request else state.buffer_epoch
                            ),
                            utterance_id=(
                                request.utterance_id
                                if request
                                else state.last_utterance_id
                            ),
                        )
                    )
                return "closed"

        if not state.intentional_close.is_set() and not state.closed_sent.is_set():
            await _emit_qwen_error_once(
                response_queue,
                state,
                "ASR_QWEN_CONNECTION_CLOSED",
                "Qwen ASR connection closed unexpectedly",
            )
            return "error"
        return "closed"
    except asyncio.CancelledError:
        raise
    except ConnectionClosed:
        if not state.intentional_close.is_set() and not state.closed_sent.is_set():
            await _emit_qwen_error_once(
                response_queue,
                state,
                "ASR_QWEN_CONNECTION_CLOSED",
                "Qwen ASR connection closed unexpectedly",
            )
            return "error"
        return "closed"
    except Exception:
        if state.intentional_close.is_set():
            return "closed"
        await _emit_qwen_error_once(
            response_queue,
            state,
            "ASR_QWEN_WORKER_FAILED",
            "Qwen ASR receiver failed",
        )
        return "error"


async def qwen_asr_worker(
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    api_key: str,
    config: AsrSessionConfig,
    *,
    region: str = "cn",
) -> None:
    """Stream normalized PCM to Qwen-ASR and normalize provider events."""

    generation = 0
    buffer_epoch = 0
    next_utterance_id = 1
    first_connection = True
    closed_sent = False
    active_state: _QwenConnectionState | None = None

    try:
        if region not in ("cn", "intl"):
            raise ValueError("unsupported Qwen ASR region")
        if not api_key:
            raise PermissionError("Qwen ASR credentials are missing")
        language = _qwen_language_code(config.language)
        session_update = _qwen_session_update(config, language)
        url = _QWEN_CN_URL if region == "cn" else _QWEN_INTL_URL

        while True:
            state = _QwenConnectionState(
                generation=generation,
                buffer_epoch=buffer_epoch,
                next_utterance_id=next_utterance_id,
                emit_ready=first_connection,
            )
            active_state = state
            ws: Any | None = None
            sender_task: asyncio.Task[tuple[str, _AsrWorkerRequest | None]] | None = (
                None
            )
            receiver_task: asyncio.Task[str] | None = None
            outcome = "error"
            outcome_request: _AsrWorkerRequest | None = None
            try:
                ws = await websockets.connect(
                    url,
                    additional_headers={"Authorization": f"Bearer {api_key}"},
                    close_timeout=0.5,
                )
                receiver_task = asyncio.create_task(
                    _qwen_receiver(ws, response_queue, config, state),
                    name="qwen-asr-receiver",
                )
                await ws.send(json.dumps(session_update))
                sender_task = asyncio.create_task(
                    _qwen_sender(
                        ws,
                        request_queue,
                        response_queue,
                        config,
                        state,
                    ),
                    name="qwen-asr-sender",
                )
                done, pending = await asyncio.wait(
                    {sender_task, receiver_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if sender_task in done:
                    outcome, outcome_request = await sender_task
                if (
                    receiver_task in done
                    and state.intentional_close.is_set()
                    and sender_task not in done
                ):
                    try:
                        outcome, outcome_request = await asyncio.wait_for(
                            asyncio.shield(sender_task), timeout=1.0
                        )
                    except asyncio.TimeoutError:
                        pass
                if receiver_task in done:
                    receiver_outcome = await receiver_task
                    if receiver_outcome == "error":
                        outcome = "error"
                    elif receiver_outcome == "closed" and outcome != "clear":
                        outcome = "shutdown"
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await _emit_qwen_error_once(
                    response_queue,
                    state,
                    (
                        "ASR_CREDENTIALS_REJECTED"
                        if _qwen_is_auth_rejection(exc)
                        else "ASR_QWEN_CONNECTION_FAILED"
                    ),
                    (
                        "Qwen ASR credentials were rejected"
                        if _qwen_is_auth_rejection(exc)
                        else "Qwen ASR connection or session setup failed"
                    ),
                )
                outcome = "error"
            finally:
                for task in (sender_task, receiver_task):
                    if task is not None and not task.done():
                        task.cancel()
                pending_tasks = [
                    task
                    for task in (sender_task, receiver_task)
                    if task is not None and not task.done()
                ]
                if pending_tasks:
                    await asyncio.gather(*pending_tasks, return_exceptions=True)
                if ws is not None:
                    state.intentional_close.set()
                    try:
                        await ws.close()
                    except Exception:
                        pass

            closed_sent = state.closed_sent.is_set()
            if outcome == "clear" and outcome_request is not None:
                generation = outcome_request.generation
                buffer_epoch = outcome_request.buffer_epoch
                next_utterance_id = outcome_request.utterance_id or 1
                first_connection = False
                continue
            if outcome_request is not None:
                generation = outcome_request.generation
                buffer_epoch = outcome_request.buffer_epoch
                next_utterance_id = outcome_request.utterance_id or next_utterance_id
            return
    except asyncio.CancelledError:
        raise
    except PermissionError:
        await response_queue.put(
            _AsrWorkerEvent(
                kind="error",
                generation=generation,
                buffer_epoch=buffer_epoch,
                error_code="ASR_CREDENTIALS_MISSING",
                error_message="Qwen ASR credentials are missing",
            )
        )
    except ValueError as exc:
        message = str(exc)
        code = (
            "ASR_LANGUAGE_NOT_SUPPORTED"
            if "language" in message
            else "ASR_INVALID_CONFIG"
        )
        await response_queue.put(
            _AsrWorkerEvent(
                kind="error",
                generation=generation,
                buffer_epoch=buffer_epoch,
                error_code=code,
                error_message="Qwen ASR configuration is not supported",
            )
        )
    finally:
        if active_state is not None:
            closed_sent = closed_sent or active_state.closed_sent.is_set()
        if not closed_sent:
            await response_queue.put(
                _AsrWorkerEvent(
                    kind="closed",
                    generation=generation,
                    buffer_epoch=buffer_epoch,
                    utterance_id=(
                        active_state.last_utterance_id if active_state else None
                    ),
                )
            )
