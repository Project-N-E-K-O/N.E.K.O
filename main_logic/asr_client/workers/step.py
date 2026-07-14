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

"""StepFun bidirectional streaming ASR worker."""

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

_STEP_URL = "wss://api.stepfun.com/v1/realtime/asr/stream"
_STEP_MODEL = "stepaudio-2.5-asr-stream"
_STEP_SUPPORTED_LANGUAGES = frozenset({"en", "zh"})

_ItemKey: TypeAlias = tuple[int, int, int]


@dataclass(slots=True)
class _StepConnectionState:
    generation: int
    buffer_epoch: int
    next_utterance_id: int
    emit_ready: bool
    item_keys: dict[str, _ItemKey] = field(default_factory=dict)
    pending_manual_commits: deque[_ItemKey] = field(default_factory=deque)
    unbound_manual_item_ids: deque[str] = field(default_factory=deque)
    unbound_manual_item_id_set: set[str] = field(default_factory=set)
    configured: asyncio.Event = field(default_factory=asyncio.Event)
    intentional_close: asyncio.Event = field(default_factory=asyncio.Event)
    error_sent: asyncio.Event = field(default_factory=asyncio.Event)
    closed_sent: asyncio.Event = field(default_factory=asyncio.Event)
    last_utterance_id: int | None = None


def _step_bind_pending_manual_items(state: _StepConnectionState) -> None:
    while state.pending_manual_commits and state.unbound_manual_item_ids:
        item_id = state.unbound_manual_item_ids.popleft()
        if item_id not in state.unbound_manual_item_id_set:
            continue
        state.unbound_manual_item_id_set.remove(item_id)
        if item_id in state.item_keys:
            continue
        state.item_keys[item_id] = state.pending_manual_commits.popleft()


def _step_manual_item_key(
    state: _StepConnectionState,
    item_id: str,
) -> _ItemKey | None:
    key = state.item_keys.get(item_id)
    if key is not None:
        return key
    if item_id not in state.unbound_manual_item_id_set:
        state.unbound_manual_item_ids.append(item_id)
        state.unbound_manual_item_id_set.add(item_id)
    _step_bind_pending_manual_items(state)
    return state.item_keys.get(item_id)


def _step_event_id() -> str:
    return f"event_{uuid.uuid4().hex}"


def _step_language_code(language: str) -> str | None:
    normalized = language.strip().lower()
    if normalized == "auto":
        return None
    code = normalized.split("-", 1)[0]
    if code not in _STEP_SUPPORTED_LANGUAGES:
        raise ValueError("unsupported Step ASR language")
    return code


def _step_is_auth_rejection(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is None:
        status_code = getattr(exc, "status_code", None)
    return status_code in {401, 403}


def _step_session_update(
    config: AsrSessionConfig,
    language: str | None,
) -> dict[str, Any]:
    if config.endpointing_mode not in ("manual", "provider"):
        raise ValueError("unsupported Step ASR endpointing mode")

    transcription: dict[str, Any] = {
        "model": _STEP_MODEL,
        "full_rerun_on_commit": True,
        "enable_timestamp_align": False,
    }
    if language is not None:
        transcription["language"] = language
    audio_input: dict[str, Any] = {
        "format": {
            "type": "pcm",
            "codec": "pcm_s16le",
            "rate": 16000,
            "bits": 16,
            "channel": 1,
        },
        "transcription": transcription,
    }
    if config.endpointing_mode == "provider":
        audio_input["turn_detection"] = {"type": "server_vad"}
    return {
        "event_id": _step_event_id(),
        "type": "session.update",
        "session": {"audio": {"input": audio_input}},
    }


async def _emit_step_error_once(
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    state: _StepConnectionState,
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


async def _step_sender(
    ws: Any,
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    config: AsrSessionConfig,
    state: _StepConnectionState,
) -> tuple[str, _AsrWorkerRequest | None]:
    await state.configured.wait()
    try:
        while True:
            request = await request_queue.get()
            try:
                if request.kind == "audio":
                    state.last_utterance_id = request.utterance_id
                    # The configured container and codec are raw PCM16LE. The
                    # official field is Base64 text; no WAV header is added.
                    await ws.send(
                        json.dumps(
                            {
                                "event_id": _step_event_id(),
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
                        await _emit_step_error_once(
                            response_queue,
                            state,
                            "ASR_STEP_PROTOCOL_ERROR",
                            "Step ASR received commit while server VAD is active",
                        )
                        return "error", request
                    if request.utterance_id is None:
                        await _emit_step_error_once(
                            response_queue,
                            state,
                            "ASR_STEP_PROTOCOL_ERROR",
                            "Step ASR commit is missing an utterance identifier",
                        )
                        return "error", request
                    key = (
                        request.generation,
                        request.buffer_epoch,
                        request.utterance_id,
                    )
                    state.pending_manual_commits.append(key)
                    _step_bind_pending_manual_items(state)
                    await ws.send(
                        json.dumps(
                            {
                                "event_id": _step_event_id(),
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
                    state.intentional_close.set()
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
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    return "shutdown", request

                await _emit_step_error_once(
                    response_queue,
                    state,
                    "ASR_STEP_PROTOCOL_ERROR",
                    "Step ASR received an unsupported command",
                )
                return "error", request
            finally:
                request_queue.task_done()
    except asyncio.CancelledError:
        raise
    except ConnectionClosed:
        if not state.intentional_close.is_set():
            await _emit_step_error_once(
                response_queue,
                state,
                "ASR_STEP_CONNECTION_CLOSED",
                "Step ASR connection closed unexpectedly",
            )
        return "error", None
    except Exception:
        await _emit_step_error_once(
            response_queue,
            state,
            "ASR_STEP_WORKER_FAILED",
            "Step ASR sender failed",
        )
        return "error", None


async def _step_receiver(
    ws: Any,
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    config: AsrSessionConfig,
    state: _StepConnectionState,
) -> str:
    try:
        async for raw_message in ws:
            try:
                event = json.loads(raw_message)
            except (TypeError, ValueError):
                await _emit_step_error_once(
                    response_queue,
                    state,
                    "ASR_STEP_PROTOCOL_ERROR",
                    "Step ASR returned an invalid event",
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

            if event_type == "error":
                if state.intentional_close.is_set():
                    return "closed"
                item_id = str(event.get("item_id") or "")
                await _emit_step_error_once(
                    response_queue,
                    state,
                    "ASR_STEP_PROVIDER_ERROR",
                    "Step ASR provider reported an error",
                    item_key=state.item_keys.get(item_id),
                )
                return "error"

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
                # Step assigns this event to the committed audio item. Its
                # transcription events use a different item_id, so manual
                # utterances are bound when a transcription event arrives.
                continue

            if event_type == "conversation.item.input_audio_transcription.delta":
                item_id = str(event.get("item_id") or "")
                key = state.item_keys.get(item_id)
                if key is None and item_id and config.endpointing_mode == "manual":
                    key = _step_manual_item_key(state, item_id)
                if key is not None:
                    await response_queue.put(
                        _AsrWorkerEvent(
                            kind="partial",
                            generation=key[0],
                            buffer_epoch=key[1],
                            utterance_id=key[2],
                            text=str(event.get("text") or ""),
                        )
                    )
                continue

            if event_type == "conversation.item.input_audio_transcription.completed":
                item_id = str(event.get("item_id") or "")
                key = state.item_keys.get(item_id)
                if key is None and item_id and config.endpointing_mode == "manual":
                    key = _step_manual_item_key(state, item_id)
                if key is not None:
                    state.item_keys.pop(item_id, None)
                    await response_queue.put(
                        _AsrWorkerEvent(
                            kind="final",
                            generation=key[0],
                            buffer_epoch=key[1],
                            utterance_id=key[2],
                            text=str(event.get("transcript") or ""),
                        )
                    )
                continue

        if not state.intentional_close.is_set():
            await _emit_step_error_once(
                response_queue,
                state,
                "ASR_STEP_CONNECTION_CLOSED",
                "Step ASR connection closed unexpectedly",
            )
            return "error"
        return "closed"
    except asyncio.CancelledError:
        raise
    except ConnectionClosed:
        if not state.intentional_close.is_set():
            await _emit_step_error_once(
                response_queue,
                state,
                "ASR_STEP_CONNECTION_CLOSED",
                "Step ASR connection closed unexpectedly",
            )
            return "error"
        return "closed"
    except Exception:
        if state.intentional_close.is_set():
            return "closed"
        await _emit_step_error_once(
            response_queue,
            state,
            "ASR_STEP_WORKER_FAILED",
            "Step ASR receiver failed",
        )
        return "error"


async def step_asr_worker(
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    api_key: str,
    config: AsrSessionConfig,
) -> None:
    """Stream normalized PCM to StepFun and normalize provider events."""

    generation = 0
    buffer_epoch = 0
    next_utterance_id = 1
    first_connection = True
    closed_sent = False
    active_state: _StepConnectionState | None = None

    try:
        if not api_key:
            raise PermissionError("Step ASR credentials are missing")
        language = _step_language_code(config.language)
        session_update = _step_session_update(config, language)

        while True:
            state = _StepConnectionState(
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
                    _STEP_URL,
                    additional_headers={"Authorization": f"Bearer {api_key}"},
                    close_timeout=0.5,
                )
                receiver_task = asyncio.create_task(
                    _step_receiver(ws, response_queue, config, state),
                    name="step-asr-receiver",
                )
                await ws.send(json.dumps(session_update))
                sender_task = asyncio.create_task(
                    _step_sender(
                        ws,
                        request_queue,
                        response_queue,
                        config,
                        state,
                    ),
                    name="step-asr-sender",
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
                for task in pending:
                    if not task.done():
                        task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await _emit_step_error_once(
                    response_queue,
                    state,
                    (
                        "ASR_CREDENTIALS_REJECTED"
                        if _step_is_auth_rejection(exc)
                        else "ASR_STEP_CONNECTION_FAILED"
                    ),
                    (
                        "Step ASR credentials were rejected"
                        if _step_is_auth_rejection(exc)
                        else "Step ASR connection or session setup failed"
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
                error_message="Step ASR credentials are missing",
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
                error_message="Step ASR configuration is not supported",
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
