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
import logging
import errno
import socket
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, TypeAlias

import websockets
from websockets.exceptions import ConnectionClosed

from ..delivery import (
    TransportDeliveryEvidence,
    begin_transport_write,
    complete_transport_write,
    retire_interval_deliveries,
    delivery_evidence,
    log_delivery_phase,
)
from .._infra import (
    AsrSessionConfig, _AsrRequestQueue, _AsrWorkerEvent, _AsrWorkerRequest,
    _QueuedAudioHold,
)
from ..provider_policy import resolve_provider_policy
from ..connection_cleanup import ConnectionRetirement, connection_registry
from ._shared import is_auth_rejection

logger = logging.getLogger(__name__)

_QWEN_MODEL = "qwen3-asr-flash-realtime"
_QWEN_CN_URL = f"wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model={_QWEN_MODEL}"
_QWEN_INTL_URL = (
    f"wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime?model={_QWEN_MODEL}"
)
_QWEN_FINISH_TIMEOUT_SECONDS = 3.0
_QWEN_SETUP_TIMEOUT_SECONDS = 10.0
_QWEN_RECOVERY_TIMEOUT_SECONDS = 12.0
_QWEN_FINAL_DELIVERY_TIMEOUT_SECONDS = 5.0
_QWEN_RECONNECT_MAX_ATTEMPTS = 3
# Local pause candidates do not take endpoint authority away from Qwen.  They
# only start this grace period; resumed speech or a provider endpoint cancels
# it.  If the provider remains silent, session.finish asks it to settle the
# current buffer and then the worker reconnects a fresh session.
_QWEN_LOCAL_FINISH_GRACE_SECONDS = 1.5
# Server VAD publishes speech_stopped/committed as the logical endpoint of a
# turn, but the transcription completed event may be delayed or never arrive.
# An item that outlives this deadline after its endpoint is completed with an
# empty final so the upstream utterance lifecycle converges instead of waiting
# unboundedly. Mirrors the OpenAI worker's stalled-item deadline.
_QWEN_STALLED_ITEM_TIMEOUT_SECONDS = 30.0
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


class _QwenResponseDeliveryTimeout(TimeoutError):
    """A response consumer failed to make capacity within the delivery budget."""


@dataclass(slots=True)
class _QwenConnectionState:
    generation: int
    buffer_epoch: int
    next_utterance_id: int
    emit_ready: bool
    delivery: TransportDeliveryEvidence | None = None
    item_keys: dict[str, _ItemKey] = field(default_factory=dict)
    final_deliveries: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    pending_manual_commits: deque[_ItemKey] = field(default_factory=deque)
    # Monotonic timestamps of provider endpoints whose transcription final is
    # still outstanding, keyed by item id (see _qwen_watch_stalled_items).
    item_deadlines: dict[str, float] = field(default_factory=dict)
    stalled_deadline_armed: asyncio.Event = field(default_factory=asyncio.Event)
    configured: asyncio.Event = field(default_factory=asyncio.Event)
    finish_received: asyncio.Event = field(default_factory=asyncio.Event)
    fallback_due: asyncio.Event = field(default_factory=asyncio.Event)
    fallback_key: _ItemKey | None = None
    fallback_timer_task: asyncio.Task[None] | None = None
    pending_local_pause: tuple[int, int] | None = None
    pending_pause_audio_bytes: int = 0
    pending_pause_from_item: int | None = None
    wire_audio_bytes: int = 0
    local_speech_cycle: int = 0
    provider_speech_cycles: dict[int, int | None] = field(default_factory=dict)
    last_provider_final_cycle: int = -1
    unclaimed_provider_final_audio_bytes: int | None = None
    local_speech_active: bool = False
    provider_endpoint_utterance_ids: set[int] = field(default_factory=set)
    provider_endpoint_audio_bytes: dict[int, int] = field(default_factory=dict)
    reconnect_after_finish: bool = False
    intentional_close: asyncio.Event = field(default_factory=asyncio.Event)
    error_sent: asyncio.Event = field(default_factory=asyncio.Event)
    closed_sent: asyncio.Event = field(default_factory=asyncio.Event)
    last_utterance_id: int | None = None
    # Provider VAD allocates its own utterance ids from speech_started.  Audio
    # requests carry the runtime's local id and must not overwrite this one.
    current_provider_utterance_id: int | None = None
    # Legacy DashScope domains can omit the documented ``item_id`` fields.
    # Their manual stream is ordered, so retain the head commit until final.
    legacy_manual_key: _ItemKey | None = None
    shutdown_request: _AsrWorkerRequest | None = None
    retirement: ConnectionRetirement | None = None


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
    return is_auth_rejection(exc)


def _qwen_setup_error_code(exc: BaseException) -> str:
    """Only typed transient setup failures authorize another connection."""
    if _qwen_is_auth_rejection(exc):
        return "ASR_CREDENTIALS_REJECTED"
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if status is not None:
        if isinstance(status, int) and (status == 429 or 500 <= status <= 599):
            return "ASR_QWEN_CONNECTION_FAILED"
        return "ASR_QWEN_SETUP_FAILED"
    if isinstance(exc, (ConnectionError, TimeoutError, socket.gaierror)):
        return "ASR_QWEN_CONNECTION_FAILED"
    if isinstance(exc, OSError) and exc.errno in {
        errno.ENETUNREACH, errno.ENETDOWN, errno.ENETRESET,
        errno.EHOSTUNREACH, errno.ECONNREFUSED, errno.ECONNRESET,
        errno.ECONNABORTED, errno.ETIMEDOUT, errno.EPIPE,
    }:
        return "ASR_QWEN_CONNECTION_FAILED"
    return "ASR_QWEN_SETUP_FAILED"


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
    await asyncio.wait_for(response_queue.put(
        _AsrWorkerEvent(
            kind="error",
            generation=generation,
            buffer_epoch=buffer_epoch,
            utterance_id=utterance_id,
            error_code=error_code,
            error_message=error_message,
        )
    ), _QWEN_FINAL_DELIVERY_TIMEOUT_SECONDS)


def _qwen_arm_stalled_item_deadline(
    state: _QwenConnectionState,
    item_id: str,
) -> None:
    if item_id and item_id in state.item_keys and item_id not in state.item_deadlines:
        state.item_deadlines[item_id] = time.monotonic()
        state.stalled_deadline_armed.set()


def _qwen_cancel_provider_fallback(
    state: _QwenConnectionState,
    *,
    clear_pending_pause: bool = True,
) -> None:
    state.fallback_key = None
    if clear_pending_pause:
        state.pending_local_pause = None
        state.pending_pause_from_item = None
        state.pending_pause_audio_bytes = 0
    state.fallback_due.clear()
    timer = state.fallback_timer_task
    state.fallback_timer_task = None
    if timer is not None and not timer.done():
        timer.cancel()


def _qwen_retire_provider_key(state: _QwenConnectionState, key: _ItemKey) -> None:
    cycle = state.provider_speech_cycles.pop(key[2], None)
    endpoint_bytes = state.provider_endpoint_audio_bytes.pop(key[2], None)
    if cycle is not None:
        state.last_provider_final_cycle = max(state.last_provider_final_cycle, cycle)
    else:
        # An unconfirmed provider item must not consume a future local cycle.
        # Only provider speech_stopped timestamps describe the old item.
        # The wire position at final arrival may already include newer speech.
        # Without an authoritative boundary, preserve recovery conservatively.
        state.unclaimed_provider_final_audio_bytes = endpoint_bytes
    state.provider_endpoint_utterance_ids.discard(key[2])
    if state.current_provider_utterance_id == key[2]:
        state.current_provider_utterance_id = None
    if state.fallback_key == key:
        _qwen_cancel_provider_fallback(state, clear_pending_pause=False)


def _qwen_arm_provider_fallback(
    state: _QwenConnectionState,
    key: _ItemKey,
) -> None:
    _qwen_cancel_provider_fallback(state)
    state.fallback_key = key

    async def wait_for_grace() -> None:
        try:
            await asyncio.sleep(_QWEN_LOCAL_FINISH_GRACE_SECONDS)
        except asyncio.CancelledError:
            return
        if state.fallback_key == key and not state.intentional_close.is_set():
            state.fallback_due.set()

    state.fallback_timer_task = asyncio.create_task(
        wait_for_grace(), name="qwen-asr-local-finish-grace"
    )


async def _qwen_emit_empty_finals_for_pending_items(
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    state: _QwenConnectionState,
) -> None:
    """Fence unresolved provider items before retiring a finished connection."""

    try:
        async with asyncio.timeout(_QWEN_FINAL_DELIVERY_TIMEOUT_SECONDS):
            for item_id, key in list(state.item_keys.items()):
                await _qwen_publish_item_final(response_queue, state, item_id, key, "")
    except TimeoutError as exc:
        raise _QwenResponseDeliveryTimeout("Qwen final settlement timed out") from exc


async def _qwen_publish_item_final(
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    state: _QwenConnectionState,
    item_id: str,
    key: _ItemKey,
    text: str,
) -> None:
    """Retire only after delivery; receiver cancellation must not lose a final."""
    task = state.final_deliveries.get(item_id)
    if task is None:
        async def deliver() -> None:
            await response_queue.put(_AsrWorkerEvent(
                kind="final", generation=key[0], buffer_epoch=key[1],
                utterance_id=key[2], text=text,
            ))
            state.item_keys.pop(item_id, None)
            state.item_deadlines.pop(item_id, None)
            _qwen_retire_provider_key(state, key)

        task = asyncio.create_task(deliver(), name="qwen-asr-final-delivery")
        state.final_deliveries[item_id] = task
        # Keep the task until connection teardown, including when the receiver
        # is cancelled while it is blocked on the response queue.
        def delivered(completed: asyncio.Task[None]) -> None:
            if not completed.cancelled() and completed.exception() is None:
                state.final_deliveries.pop(item_id, None)

        task.add_done_callback(delivered)
    try:
        await asyncio.wait_for(asyncio.shield(task), _QWEN_FINAL_DELIVERY_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise _QwenResponseDeliveryTimeout("Qwen final delivery timed out") from exc


async def _qwen_send_finish(ws: Any, state: _QwenConnectionState) -> None:
    state.finish_received.clear()
    await ws.send(json.dumps({"event_id": _qwen_event_id(), "type": "session.finish"}))


async def _qwen_close_transport(ws: Any, state: _QwenConnectionState) -> None:
    state.intentional_close.set()
    if state.retirement is not None and state.retirement.connection is ws:
        await state.retirement.retire()
        return
    try:
        await ws.close()
    except Exception:
        pass


async def _qwen_emit_closed(
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    state: _QwenConnectionState,
    request: _AsrWorkerRequest,
) -> None:
    if not state.closed_sent.is_set():
        if request.kind == "finish" and not state.finish_received.is_set():
            await _emit_qwen_error_once(
                response_queue, state, "ASR_FINISH_TIMEOUT",
                "Qwen ASR did not acknowledge explicit finish",
            )
        state.closed_sent.set()
        await response_queue.put(_AsrWorkerEvent(
            kind="finished" if request.kind == "finish" and state.finish_received.is_set() else "closed",
            generation=request.generation,
            buffer_epoch=request.buffer_epoch, utterance_id=request.utterance_id,
        ))


async def _qwen_finish_and_reconnect(
    ws: Any,
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    state: _QwenConnectionState,
    deferred_requests: deque[_AsrWorkerRequest],
    audio_holds: dict[int, _QueuedAudioHold] | None = None,
) -> tuple[str, _AsrWorkerRequest | None]:
    if isinstance(request_queue, _AsrRequestQueue):
        request_queue.transport_recovery_deadline = (
            time.monotonic() + _QWEN_RECOVERY_TIMEOUT_SECONDS
        )
    state.reconnect_after_finish = True
    await _qwen_send_finish(ws, state)
    # session.finish is one-way.  Requests arriving while the provider flushes
    # belong to the successor session and must stay in FIFO order.
    finish_task = asyncio.create_task(state.finish_received.wait())
    queue_task: asyncio.Task[_AsrWorkerRequest] | None = asyncio.create_task(
        _qwen_get_request(request_queue, deferred_requests, audio_holds)
    )
    deadline = asyncio.get_running_loop().time() + _QWEN_FINISH_TIMEOUT_SECONDS
    deferred_shutdown: _AsrWorkerRequest | None = None
    deferred_clear: _AsrWorkerRequest | None = None
    try:
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            waiting = {finish_task}
            if queue_task is not None:
                waiting.add(queue_task)
            done, _ = await asyncio.wait(
                waiting,
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if queue_task is not None and queue_task in done:
                arrived = queue_task.result()
                queue_task = None
                if arrived.kind in {"shutdown", "finish"}:
                    deferred_shutdown = arrived
                    # Closing the microphone does not cancel the flush already
                    # sent to the provider. Preserve its final within the same
                    # original deadline, without opening a successor session.
                    if finish_task in done:
                        break
                    continue
                if arrived.kind == "clear":
                    deferred_clear = arrived
                    break
                # Keep the deferral bounded: leave subsequent audio in the
                # public queue so its existing backpressure still applies.
                deferred_requests.append(arrived)
                if finish_task in done:
                    break
                # Keep waiting for the provider completion without reading a
                # second request. Later requests stay in the public queue so
                # normal FIFO ordering and audio backpressure remain intact.
                continue
            break
    finally:
        for task in (finish_task, queue_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (finish_task, queue_task) if task is not None),
            return_exceptions=True,
        )
        # A getter can finish after wait() took its snapshot. Preserve that
        # request rather than losing it during cancellation of the waiter.
        if (
            queue_task is not None and not queue_task.cancelled()
            and queue_task.exception() is None
        ):
            late = queue_task.result()
            if late.kind in {"shutdown", "finish"}:
                deferred_shutdown = late
            elif late.kind == "clear":
                deferred_clear = late
            else:
                deferred_requests.append(late)

    # A provider may acknowledge the session without returning a final for an
    # outstanding item.  Keep the upstream lifecycle bounded in that case.
    await _qwen_emit_empty_finals_for_pending_items(response_queue, state)
    await _qwen_close_transport(ws, state)
    queued_requests = (*deferred_requests, *getattr(request_queue, "_queue", ()))
    # finish already sealed this transport. Preserve accepted tail PCM for a
    # successor, then process shutdown in FIFO order there. Only clear may
    # invalidate audio before it; activity alone does not require a successor.
    shutdown_index = next(
        (i for i, request in enumerate(queued_requests) if request.kind in {"shutdown", "finish"}),
        len(queued_requests),
    )
    before_shutdown = queued_requests[:shutdown_index]
    last_clear = max(
        (i for i, request in enumerate(before_shutdown) if request.kind == "clear"),
        default=-1,
    )
    has_tail_audio = any(
        request.kind == "audio" and request.audio
        for request in before_shutdown[last_clear + 1:]
    )
    if has_tail_audio:
        if deferred_shutdown is not None:
            deferred_requests.append(deferred_shutdown)
            deferred_shutdown = None
        if deferred_clear is not None:
            request_queue.task_done()
            return "clear", deferred_clear
        if last_clear < 0:
            return "reconnect", None
    if deferred_shutdown is None and any(
        request.kind in {"shutdown", "finish", "clear"}
        for request in queued_requests
    ):
        # clear invalidates preceding PCM. Preserve any new-epoch PCM before
        # shutdown; with no valid tail, shutdown avoids a needless connection.
        terminal_kind = "clear" if last_clear >= 0 and has_tail_audio else next(
            (request.kind for request in queued_requests if request.kind in {"shutdown", "finish"}),
            "clear",
        )
        if deferred_clear is not None:
            request_queue.task_done()
            deferred_clear = None
        holds = audio_holds if audio_holds is not None else {}
        while True:
            arrived = _qwen_get_request_nowait(request_queue, deferred_requests, holds)
            if arrived.kind == terminal_kind:
                if arrived.kind in {"shutdown", "finish"}:
                    deferred_shutdown = arrived
                else:
                    deferred_clear = arrived
                break
            hold = holds.pop(id(arrived), None)
            if hold is not None:
                hold.release()
            request_queue.task_done()
    if deferred_shutdown is not None:
        state.shutdown_request = deferred_shutdown
        await _qwen_emit_closed(response_queue, state, deferred_shutdown)
        request_queue.task_done()
        return "shutdown", deferred_shutdown
    if deferred_clear is not None:
        request_queue.task_done()
        return "clear", deferred_clear
    return "reconnect", None


async def _qwen_expire_stalled_items(
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    state: _QwenConnectionState,
) -> None:
    now = time.monotonic()
    expired_ids = [
        item_id
        for item_id, armed_at in state.item_deadlines.items()
        if now - armed_at >= _QWEN_STALLED_ITEM_TIMEOUT_SECONDS
    ]
    for item_id in expired_ids:
        armed_at = state.item_deadlines.pop(item_id)
        # Popping the key tombstones the item: a late completed event finds
        # no mapping and is dropped instead of resurrecting the closed turn.
        key = state.item_keys.pop(item_id, None)
        if key is None:
            continue
        logger.warning(
            "ASR provider wait stage=awaiting_final generation=%s buffer_epoch=%s utterance_id=%s elapsed_ms=%s failure_code=ASR_PROVIDER_FINAL_TIMEOUT",
            *key, round((now - armed_at) * 1000),
        )
        await _emit_qwen_error_once(
            response_queue, state, "ASR_PROVIDER_FINAL_TIMEOUT",
            "Qwen ASR final did not arrive after the provider endpoint",
            item_key=key,
        )


async def _qwen_watch_stalled_items(
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    state: _QwenConnectionState,
) -> None:
    # Runs beside the receiver because the receiver blocks on provider
    # frames; a provider that goes silent after server VAD reported the end
    # of speech would otherwise never trigger the sweep, leaving the
    # upstream turn open unboundedly.
    while True:
        if not state.item_deadlines:
            await state.stalled_deadline_armed.wait()
            state.stalled_deadline_armed.clear()
            continue
        earliest = min(state.item_deadlines.values())
        remaining = (
            earliest + _QWEN_STALLED_ITEM_TIMEOUT_SECONDS - time.monotonic()
        )
        if remaining > 0:
            await asyncio.sleep(remaining)
            continue
        await _qwen_expire_stalled_items(response_queue, state)


async def _qwen_get_request(
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    deferred_requests: deque[_AsrWorkerRequest],
    audio_holds: dict[int, _QueuedAudioHold] | None = None,
) -> _AsrWorkerRequest:
    if deferred_requests:
        return deferred_requests.popleft()
    if isinstance(request_queue, _AsrRequestQueue) and audio_holds is not None:
        request, hold = await request_queue.get_with_audio_hold()
        if hold is not None:
            audio_holds[id(request)] = hold
    else:
        request = await request_queue.get()
    return request


def _qwen_get_request_nowait(
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    deferred_requests: deque[_AsrWorkerRequest],
    audio_holds: dict[int, _QueuedAudioHold],
) -> _AsrWorkerRequest:
    if deferred_requests:
        return deferred_requests.popleft()
    request = request_queue.get_nowait()
    if isinstance(request_queue, _AsrRequestQueue):
        hold = request_queue.hold_dequeued_audio(request)
        if hold is not None:
            audio_holds[id(request)] = hold
    return request


async def _qwen_sender(
    ws: Any,
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    config: AsrSessionConfig,
    state: _QwenConnectionState,
    deferred_requests: deque[_AsrWorkerRequest] | None = None,
    audio_holds: dict[int, _QueuedAudioHold] | None = None,
) -> tuple[str, _AsrWorkerRequest | None]:
    delivery_evidence(request_queue)
    await state.configured.wait()
    if deferred_requests is None:
        deferred_requests = deque()
    owns_holds = audio_holds is None
    if audio_holds is None:
        audio_holds = {}
    fallback_task = asyncio.create_task(state.fallback_due.wait())
    try:
        while True:
            queue_task = asyncio.create_task(
                _qwen_get_request(request_queue, deferred_requests, audio_holds)
            )
            # Keep an event waiter alive even before the provider utterance ID
            # exists. A provider speech_started event can arm the fallback
            # while this loop is already waiting for the next request.
            if fallback_task.done() and not state.fallback_due.is_set():
                fallback_task = asyncio.create_task(state.fallback_due.wait())
            request: _AsrWorkerRequest | None = None
            try:
                done, _ = await asyncio.wait(
                    {queue_task, fallback_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                queue_task.cancel()
                fallback_task.cancel()
                await asyncio.gather(
                    queue_task,
                    fallback_task,
                    return_exceptions=True,
                )
                raise
            if queue_task in done:
                request = queue_task.result()
            elif fallback_task in done:
                # Awaiting a getter after cancelling it lets a simultaneous
                # completion win; only an actually cancelled getter requires a
                # second queue read.
                queue_task.cancel()
                try:
                    request = await queue_task
                except asyncio.CancelledError:
                    # Only absorb getter cancellation, never owner cancellation.
                    if asyncio.current_task().cancelling():
                        raise
                    try:
                        request = _qwen_get_request_nowait(
                            request_queue, deferred_requests, audio_holds,
                        )
                    except asyncio.QueueEmpty:
                        request = None
            if request is None:
                # Endpoint/final processing may have cancelled fallback while
                # we joined the getter.  Never read a cancelled getter result.
                key = state.fallback_key
                if (
                    key is not None
                    and state.fallback_due.is_set()
                    and (
                        key[2] == state.current_provider_utterance_id
                        or (
                            state.pending_local_pause == (key[0], key[1])
                            and key[2] == state.next_utterance_id
                        )
                    )
                    and key[2] not in state.provider_endpoint_utterance_ids
                ):
                    _qwen_cancel_provider_fallback(state)
                    return await _qwen_finish_and_reconnect(
                        ws,
                        request_queue,
                        response_queue,
                        state,
                        deferred_requests,
                        audio_holds,
                    )
                continue
            try:
                if request.kind == "audio":
                    state.last_utterance_id = request.utterance_id
                    delivery = begin_transport_write(request_queue, delivery_spans=request.delivery_spans)
                    state.delivery = delivery
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
                    complete_transport_write(
                        delivery, len(request.audio), generation=request.generation,
                        buffer_epoch=request.buffer_epoch, provider="qwen",
                            delivery_spans=request.delivery_spans, takes_ownership=True,
                    )
                    state.wire_audio_bytes += len(request.audio)
                    continue

                if request.kind == "activity":
                    if config.endpointing_mode == "provider":
                        if request.speech_active:
                            if not state.local_speech_active:
                                state.local_speech_cycle += 1
                                # Provider VAD can detect the initial onset
                                # before the local detector confirms it.
                                current = state.current_provider_utterance_id
                                if (
                                    current is not None
                                    and current not in state.provider_endpoint_utterance_ids
                                    and state.provider_speech_cycles.get(current) is None
                                ):
                                    state.provider_speech_cycles[current] = state.local_speech_cycle
                            state.local_speech_active = True
                            _qwen_cancel_provider_fallback(state)
                        elif (
                            state.current_provider_utterance_id is not None
                            and (
                                state.current_provider_utterance_id
                                not in state.provider_endpoint_utterance_ids
                                or (
                                    (provider_cycle := state.provider_speech_cycles.get(
                                        state.current_provider_utterance_id
                                    )) is not None
                                    and provider_cycle >= state.local_speech_cycle
                                )
                            )
                        ):
                            # Local resume need not create a provider turn.
                            # Until server VAD seals the current item, that
                            # item owns the fallback regardless of local cycles.
                            state.local_speech_active = False
                            key = (
                                request.generation,
                                request.buffer_epoch,
                                state.current_provider_utterance_id,
                            )
                            if key[2] not in state.provider_endpoint_utterance_ids:
                                if state.provider_speech_cycles.get(key[2]) is None:
                                    # A physical pause can claim an unsealed
                                    # provider item even when onset preceded
                                    # local observation (including cycle zero).
                                    state.provider_speech_cycles[key[2]] = state.local_speech_cycle
                                _qwen_arm_provider_fallback(state, key)
                                if state.provider_speech_cycles.get(key[2], 0) != state.local_speech_cycle:
                                    # The provider may group this resume into
                                    # its current item, or publish another item
                                    # after a delayed endpoint. Preserve the
                                    # pause as an observation, not a second
                                    # timer. Only a new provider start adopts it.
                                    state.pending_local_pause = (
                                        request.generation, request.buffer_epoch
                                    )
                                    state.pending_pause_from_item = key[2]
                                    state.pending_pause_audio_bytes = state.wire_audio_bytes
                        elif (
                            state.local_speech_cycle > state.last_provider_final_cycle
                            and (
                                state.unclaimed_provider_final_audio_bytes is None
                                or state.wire_audio_bytes > state.unclaimed_provider_final_audio_bytes
                            )
                        ):
                            # A pending pause owns a fresh local speech cycle,
                            # not the arrival time of another turn's final.
                            # Start its bounded grace even without a provider
                            # item: session.finish can settle buffered speech.
                            _qwen_arm_provider_fallback(state, (
                                request.generation, request.buffer_epoch,
                                state.next_utterance_id,
                            ))
                            state.pending_local_pause = (
                                request.generation,
                                request.buffer_epoch,
                            )
                            state.pending_pause_audio_bytes = state.wire_audio_bytes
                            state.local_speech_active = False
                        else:
                            state.local_speech_active = False
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
                    _qwen_cancel_provider_fallback(state)
                    await _qwen_close_transport(ws, state)
                    return "clear", request

                if request.kind in {"shutdown", "finish"}:
                    _qwen_cancel_provider_fallback(state)
                    state.shutdown_request = request
                    await _qwen_send_finish(ws, state)
                    try:
                        await asyncio.wait_for(
                            state.finish_received.wait(),
                            timeout=_QWEN_FINISH_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        if request.kind == "finish":
                            await _emit_qwen_error_once(
                                response_queue, state, "ASR_FINISH_TIMEOUT",
                                "Qwen ASR did not acknowledge explicit finish",
                            )
                    if request.kind != "finish" or not state.finish_received.is_set():
                        await _qwen_emit_closed(response_queue, state, request)
                    await _qwen_close_transport(ws, state)
                    return "shutdown", request

                await _emit_qwen_error_once(
                    response_queue,
                    state,
                    "ASR_QWEN_PROTOCOL_ERROR",
                    "Qwen ASR received an unsupported command",
                )
                return "error", request
            finally:
                if request.kind == "audio":
                    retire_interval_deliveries(request_queue, only_spans=request.delivery_spans)
                hold = audio_holds.pop(id(request), None)
                if hold is not None:
                    hold.release()
                request_queue.task_done()
                if (
                    isinstance(request_queue, _AsrRequestQueue)
                    and request_queue.waiting_audio_items == 0
                ):
                    # Configured is not recovered: blocked producers need the
                    # original recovery budget until retained PCM is drained.
                    request_queue.transport_recovery_deadline = 0.0
    except asyncio.CancelledError:
        raise
    except ConnectionClosed:
        if state.reconnect_after_finish:
            await _qwen_emit_empty_finals_for_pending_items(response_queue, state)
            await _qwen_close_transport(ws, state)
            return "reconnect", None
        if not state.intentional_close.is_set():
            await _emit_qwen_error_once(
                response_queue,
                state,
                "ASR_QWEN_CONNECTION_CLOSED",
                "Qwen ASR connection closed unexpectedly",
            )
        return "error", None
    except Exception as exc:
        await _emit_qwen_error_once(
            response_queue,
            state,
            "ASR_STREAM_BACKPRESSURE" if isinstance(exc, _QwenResponseDeliveryTimeout)
            else "ASR_QWEN_WORKER_FAILED",
            "Qwen ASR response delivery timed out" if isinstance(exc, _QwenResponseDeliveryTimeout)
            else "Qwen ASR sender failed",
        )
        return "error", None
    finally:
        _qwen_cancel_provider_fallback(state)
        fallback_task.cancel()
        await asyncio.gather(fallback_task, return_exceptions=True)
        if owns_holds:
            for hold in audio_holds.values():
                hold.release()
            audio_holds.clear()


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
            if event_type in (
                "input_audio_buffer.speech_stopped",
                "conversation.item.input_audio_transcription.completed",
            ):
                log_delivery_phase(
                    state.delivery,
                    phase=("provider_endpoint_received" if event_type ==
                           "input_audio_buffer.speech_stopped" else
                           "provider_final_received"),
                    generation=state.generation,
                    buffer_epoch=state.buffer_epoch,
                )
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
                    if item_id not in state.item_keys:
                        state.item_keys[item_id] = (
                            state.pending_manual_commits.popleft()
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
                pending_pause = state.pending_local_pause
                audio_start_ms = event.get("audio_start_ms")
                if isinstance(audio_start_ms, int) and not isinstance(audio_start_ms, bool):
                    # Qwen timestamps share the session PCM timeline. A start
                    # beyond the observed pause belongs to fresh audio, even
                    # when the corresponding local resume hint arrives later.
                    pause_matches_audio = (
                        0 <= audio_start_ms * 32 < state.pending_pause_audio_bytes
                    )
                else:
                    # Without stream position, a pause attached to an earlier
                    # provider item cannot safely migrate to its successor.
                    pause_matches_audio = state.pending_pause_from_item is None
                if not pause_matches_audio:
                    pending_pause = None
                # Adopt the already-running grace; a delayed start must not
                # discard the pause or extend its recovery deadline.
                pending_fallback = (
                    pending_pause == (key[0], key[1]) and state.fallback_key == key
                )
                if not pending_fallback:
                    _qwen_cancel_provider_fallback(state, clear_pending_pause=False)
                state.pending_local_pause = None
                state.pending_pause_from_item = None
                state.pending_pause_audio_bytes = 0
                state.current_provider_utterance_id = key[2]
                state.item_keys[item_id] = key
                state.unclaimed_provider_final_audio_bytes = None
                # Only a confirmed local observation can own a local cycle.
                state.provider_speech_cycles[key[2]] = (
                    state.local_speech_cycle
                    if state.local_speech_active or pending_pause == (key[0], key[1])
                    else None
                )
                if pending_pause == (key[0], key[1]) and not pending_fallback and not state.reconnect_after_finish:
                    _qwen_arm_provider_fallback(state, key)
                await response_queue.put(
                    _AsrWorkerEvent(
                        kind="utterance_started",
                        generation=key[0],
                        buffer_epoch=key[1],
                        utterance_id=key[2],
                    )
                )
                continue

            if event_type == "input_audio_buffer.speech_stopped":
                if config.endpointing_mode != "provider":
                    continue
                item_id = str(event.get("item_id") or "")
                key = state.item_keys.get(item_id)
                if key is not None:
                    state.provider_endpoint_utterance_ids.add(key[2])
                    audio_end_ms = event.get("audio_end_ms")
                    if (
                        isinstance(audio_end_ms, int) and not isinstance(audio_end_ms, bool)
                        and 0 <= audio_end_ms * 32 <= state.wire_audio_bytes
                    ):
                        state.provider_endpoint_audio_bytes[key[2]] = audio_end_ms * 32
                    if state.fallback_key == key:
                        _qwen_cancel_provider_fallback(state, clear_pending_pause=False)
                # Server VAD sealed the turn; the transcription final is
                # still outstanding. Arm the stalled-item deadline so a
                # delayed or missing completed event cannot leave the
                # upstream turn open unboundedly.
                _qwen_arm_stalled_item_deadline(state, item_id)
                continue

            if event_type == "input_audio_buffer.committed":
                item_id = str(event.get("item_id") or "")
                if config.endpointing_mode == "provider":
                    # Some server VAD turns publish committed without (or
                    # after) speech_stopped; either event is the endpoint.
                    key = state.item_keys.get(item_id)
                    if key is not None:
                        state.provider_endpoint_utterance_ids.add(key[2])
                        if state.fallback_key == key:
                            _qwen_cancel_provider_fallback(state, clear_pending_pause=False)
                    _qwen_arm_stalled_item_deadline(state, item_id)
                    continue
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
                    if item_id in state.item_deadlines:
                        # Streaming text proves the transcription is alive;
                        # push the stalled-item deadline forward instead of
                        # expiring mid-stream.
                        state.item_deadlines[item_id] = time.monotonic()
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
                if item_id:
                    state.item_deadlines.pop(item_id, None)
                key = (
                    state.item_keys.get(item_id)
                    if item_id
                    else state.legacy_manual_key
                )
                if key is not None:
                    if item_id:
                        await _qwen_publish_item_final(
                            response_queue, state, item_id, key,
                            str(event.get("transcript") or ""),
                        )
                        continue
                    _qwen_retire_provider_key(state, key)
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
                if not state.reconnect_after_finish and not state.closed_sent.is_set():
                    state.closed_sent.set()
                    request = state.shutdown_request
                    await response_queue.put(
                        _AsrWorkerEvent(
                            kind="finished" if request is not None and request.kind == "finish" else "closed",
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

        if state.reconnect_after_finish:
            state.finish_received.set()
            return "closed"
        if not state.emit_ready and not state.configured.is_set():
            # A successor transport that closes before setup completes is
            # retried by _qwen_open_connection, rather than failing the session.
            return "closed"
        if not state.intentional_close.is_set() and not state.closed_sent.is_set():
            await _emit_qwen_error_once(
                response_queue,
                state,
                "ASR_QWEN_READ_DISCONNECTED",
                "Qwen ASR connection closed unexpectedly",
            )
            return "error"
        return "closed"
    except asyncio.CancelledError:
        raise
    except ConnectionClosed:
        if state.reconnect_after_finish:
            state.finish_received.set()
            return "closed"
        if not state.emit_ready and not state.configured.is_set():
            return "closed"
        if not state.intentional_close.is_set() and not state.closed_sent.is_set():
            await _emit_qwen_error_once(
                response_queue,
                state,
                "ASR_QWEN_READ_DISCONNECTED",
                "Qwen ASR connection closed unexpectedly",
            )
            return "error"
        return "closed"
    except Exception as exc:
        if state.intentional_close.is_set():
            return "closed"
        await _emit_qwen_error_once(
            response_queue,
            state,
            "ASR_STREAM_BACKPRESSURE" if isinstance(exc, _QwenResponseDeliveryTimeout)
            else "ASR_QWEN_WORKER_FAILED",
            "Qwen ASR response delivery timed out" if isinstance(exc, _QwenResponseDeliveryTimeout)
            else "Qwen ASR receiver failed",
        )
        return "error"


async def _qwen_open_connection(
    url: str,
    api_key: str,
    session_update: dict[str, Any],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    config: AsrSessionConfig,
    state: _QwenConnectionState,
    *, recovery_deadline: float = 0.0,
    request_queue: asyncio.Queue[_AsrWorkerRequest] | None = None,
) -> tuple[Any, asyncio.Task[str]]:
    policy = resolve_provider_policy("qwen", config.endpointing_mode)
    # Initial connection retries are owned by the runtime. Keep its policy
    # unchanged; only internal successor sessions get the recovery budget.
    attempts = 1 if state.emit_ready else _QWEN_RECONNECT_MAX_ATTEMPTS
    for attempt in range(attempts):
        ws = None
        receiver = None
        try:
            # One owner-level deadline bounds the whole setup and preserves
            # external cancellation even when an inner await just completed.
            setup_timeout = _QWEN_SETUP_TIMEOUT_SECONDS
            if recovery_deadline:
                setup_timeout = min(setup_timeout, recovery_deadline - time.monotonic())
            if setup_timeout <= 0:
                raise asyncio.TimeoutError
            async with asyncio.timeout(setup_timeout):
                ws = await websockets.connect(
                    url, additional_headers={"Authorization": f"Bearer {api_key}"},
                    close_timeout=0.5,
                )
                if request_queue is not None:
                    state.retirement = connection_registry(request_queue).register(ws, worker_identity="qwen")
                receiver = asyncio.create_task(
                    _qwen_receiver(ws, response_queue, config, state),
                    name="qwen-asr-receiver",
                )
                if request_queue is not None:
                    connection_registry(request_queue).register_tasks(receiver)
                await ws.send(json.dumps(session_update))
                configured = asyncio.create_task(state.configured.wait())
                try:
                    await asyncio.wait(
                        {configured, receiver}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if not state.configured.is_set():
                        raise ConnectionError("Qwen closed before session.updated")
                finally:
                    configured.cancel()
                    await asyncio.gather(configured, return_exceptions=True)
            return ws, receiver
        except BaseException as exc:
            if receiver is not None:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)
            if ws is not None:
                await _qwen_close_transport(ws, state)
            if (
                not isinstance(exc, Exception) or _qwen_is_auth_rejection(exc)
                or state.error_sent.is_set() or attempt + 1 == attempts
            ):
                raise
            state.intentional_close.clear()
            state.configured.clear()
            delay = min(policy.connect_retry_cap_seconds,
                        policy.connect_retry_base_seconds * 2**attempt)
            if recovery_deadline:
                delay = min(delay, max(0.0, recovery_deadline - time.monotonic()))
            await asyncio.sleep(delay)
    raise AssertionError("unreachable Qwen connection attempt")


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
    deferred_requests: deque[_AsrWorkerRequest] = deque()
    audio_holds: dict[int, _QueuedAudioHold] = {}
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
            stalled_watch_task: asyncio.Task[None] | None = None
            outcome = "error"
            outcome_request: _AsrWorkerRequest | None = None
            try:
                ws, receiver_task = await _qwen_open_connection(
                    url, api_key, session_update, response_queue, config, state,
                    request_queue=request_queue,
                    recovery_deadline=(
                        request_queue.transport_recovery_deadline
                        if isinstance(request_queue, _AsrRequestQueue) else 0.0
                    ),
                )
                sender_task = asyncio.create_task(
                    _qwen_sender(
                        ws,
                        request_queue,
                        response_queue,
                        config,
                        state,
                        deferred_requests,
                        audio_holds,
                    ),
                    name="qwen-asr-sender",
                )
                # A failed final delivery in the watchdog must retire the
                # connection, just like failure in the sender or receiver.
                stalled_watch_task = asyncio.create_task(
                    _qwen_watch_stalled_items(response_queue, state),
                    name="qwen-asr-stalled-watch",
                )
                connection_registry(request_queue).register_tasks(sender_task, receiver_task, stalled_watch_task)
                done, pending = await asyncio.wait(
                    {sender_task, receiver_task, stalled_watch_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stalled_watch_task in done:
                    await stalled_watch_task
                if sender_task in done:
                    outcome, outcome_request = await sender_task
                if (
                    receiver_task in done
                    and sender_task not in done
                    and (state.intentional_close.is_set() or state.reconnect_after_finish)
                    and receiver_task.result() != "error"
                ):
                    # Response queue backpressure and the closing handshake
                    # are part of the sender's finish, not evidence of shutdown.
                    outcome, outcome_request = await asyncio.wait_for(
                        sender_task, _QWEN_RECOVERY_TIMEOUT_SECONDS,
                    )
                if receiver_task in done:
                    receiver_outcome = await receiver_task
                    if receiver_outcome == "error":
                        outcome = "error"
                    elif (
                        receiver_outcome == "closed"
                        and outcome not in {"clear", "reconnect"}
                    ):
                        outcome = "shutdown"
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                async with asyncio.timeout(_QWEN_FINAL_DELIVERY_TIMEOUT_SECONDS):
                    await _emit_qwen_error_once(
                        response_queue, state,
                        "ASR_CREDENTIALS_REJECTED" if _qwen_is_auth_rejection(exc)
                        else "ASR_STREAM_BACKPRESSURE" if isinstance(exc, _QwenResponseDeliveryTimeout)
                        else _qwen_setup_error_code(exc),
                        "Qwen ASR credentials were rejected" if _qwen_is_auth_rejection(exc)
                        else "Qwen ASR response delivery timed out" if isinstance(exc, _QwenResponseDeliveryTimeout)
                        else "Qwen ASR connection or session setup failed",
                    )
                outcome = "error"
            finally:
                if state.retirement is not None:
                    state.intentional_close.set()
                    state.retirement.start()
                connection_registry(request_queue).register_tasks(sender_task, receiver_task, stalled_watch_task)
                for task in (sender_task, receiver_task, stalled_watch_task):
                    if task is not None and not task.done():
                        task.cancel()
                deliveries = list(state.final_deliveries.values())
                connection_registry(request_queue).register_tasks(*deliveries)
                for task in deliveries:
                    if not task.done():
                        task.cancel()
                # Retire the transport before joining children: session failure
                # and its background close can both cancel this worker. A
                # second cancellation during gather must not skip ws.close().
                if ws is not None:
                    await _qwen_close_transport(ws, state)
                pending_tasks = [
                    task
                    for task in (sender_task, receiver_task, stalled_watch_task)
                    if task is not None and not task.done()
                ]
                if pending_tasks:
                    await asyncio.gather(*pending_tasks, return_exceptions=True)
                if deliveries:
                    await asyncio.gather(*deliveries, return_exceptions=True)
                await connection_registry(request_queue).join_tasks()

            closed_sent = state.closed_sent.is_set()
            if outcome == "reconnect":
                generation = state.generation
                buffer_epoch = state.buffer_epoch
                next_utterance_id = state.next_utterance_id
                first_connection = False
                continue
            if outcome == "clear" and outcome_request is not None:
                if (
                    isinstance(request_queue, _AsrRequestQueue)
                    and request_queue.waiting_audio_items == 0
                ):
                    # clear discarded the recovery tail. A fresh empty epoch
                    # uses the normal setup budget, not the retired deadline.
                    request_queue.transport_recovery_deadline = 0.0
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
        if isinstance(request_queue, _AsrRequestQueue):
            request_queue.transport_recovery_deadline = 0.0
        for hold in audio_holds.values():
            hold.release()
        audio_holds.clear()
        if active_state is not None:
            closed_sent = closed_sent or active_state.closed_sent.is_set()
        if not closed_sent:
            await asyncio.wait_for(response_queue.put(
                _AsrWorkerEvent(
                    kind="closed",
                    generation=generation,
                    buffer_epoch=buffer_epoch,
                    utterance_id=(
                        active_state.last_utterance_id if active_state else None
                    ),
                )
            ), _QWEN_FINAL_DELIVERY_TIMEOUT_SECONDS)
