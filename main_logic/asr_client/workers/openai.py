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

"""OpenAI transcription-only Realtime ASR worker."""

from __future__ import annotations

import asyncio
import base64
import json
from collections import deque
from typing import Any

import numpy as np
import soxr
import websockets

from .._infra import AsrSessionConfig, _AsrWorkerEvent, _AsrWorkerRequest


_OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
_OPENAI_MODEL = "gpt-realtime-whisper"
_CLOSE_TIMEOUT_SECONDS = 0.5

_UtteranceKey = tuple[int, int, int | None]


def _normalize_openai_language(language: str) -> str | None:
    normalized = language.strip().lower()
    if normalized == "auto":
        return None
    if normalized in {"zh", "zh-cn"}:
        return "zh"
    if normalized in {"en", "en-us"}:
        return "en"
    raise ValueError("ASR_LANGUAGE_NOT_SUPPORTED: OpenAI language is unsupported")


def _openai_is_auth_rejection(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is None:
        status_code = getattr(exc, "status_code", None)
    return status_code in {401, 403}


def _resample_pcm_16k_to_24k(
    resampler: soxr.ResampleStream,
    audio: bytes,
    *,
    last: bool = False,
) -> bytes:
    if audio:
        samples = np.frombuffer(audio, dtype="<i2").astype(np.float32)
        samples /= 32768.0
    else:
        samples = np.empty(0, dtype=np.float32)
    output = resampler.resample_chunk(samples, last=last)
    if len(output) == 0:
        return b""
    return (output * 32768.0).clip(-32768, 32767).astype("<i2").tobytes()


async def openai_asr_worker(
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    api_key: str,
    config: AsrSessionConfig,
) -> None:
    """Stream 16 kHz worker PCM into OpenAI's 24 kHz transcription session."""

    last_generation = 0
    closed_sent = False
    failure_sent = False
    ready_sent = False
    shutdown_requested = asyncio.Event()
    receiver_task: asyncio.Task[None] | None = None
    sender_task: asyncio.Task[None] | None = None
    websocket = None

    pending_commits: deque[_UtteranceKey] = deque()
    item_keys: dict[str, _UtteranceKey] = {}
    resampler = soxr.ResampleStream(
        16_000,
        24_000,
        1,
        dtype="float32",
        quality="HQ",
    )

    async def _emit_error(error_code: str, error_message: str) -> None:
        nonlocal failure_sent
        if failure_sent:
            return
        failure_sent = True
        await response_queue.put(
            _AsrWorkerEvent(
                kind="error",
                generation=last_generation,
                error_code=error_code,
                error_message=error_message,
            )
        )

    async def _handle_transcript_event(event: dict[str, Any]) -> None:
        item_id = event.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            return
        key = item_keys.get(item_id)
        if key is None:
            # input_audio_buffer.committed is ordered before transcript events.
            # An unknown ID therefore belongs to an item invalidated by clear.
            return
        generation, buffer_epoch, utterance_id = key
        event_type = event.get("type")
        if event_type == "conversation.item.input_audio_transcription.delta":
            text = event.get("delta", "")
            if isinstance(text, str):
                await response_queue.put(
                    _AsrWorkerEvent(
                        kind="partial",
                        generation=generation,
                        buffer_epoch=buffer_epoch,
                        utterance_id=utterance_id,
                        text=text,
                    )
                )
            return
        if event_type == "conversation.item.input_audio_transcription.completed":
            text = event.get("transcript", "")
            if isinstance(text, str):
                await response_queue.put(
                    _AsrWorkerEvent(
                        kind="final",
                        generation=generation,
                        buffer_epoch=buffer_epoch,
                        utterance_id=utterance_id,
                        text=text,
                    )
                )
            item_keys.pop(item_id, None)

    async def _receive_events(ready_event: asyncio.Event) -> None:
        nonlocal ready_sent
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    await _emit_error(
                        "ASR_OPENAI_PROTOCOL_ERROR",
                        "OpenAI returned an unexpected binary event",
                    )
                    return
                try:
                    event = json.loads(message)
                except (TypeError, json.JSONDecodeError):
                    await _emit_error(
                        "ASR_OPENAI_PROTOCOL_ERROR",
                        "OpenAI returned an invalid event",
                    )
                    return
                if not isinstance(event, dict):
                    continue

                event_type = event.get("type")
                if event_type == "session.updated":
                    if not ready_sent:
                        ready_sent = True
                        ready_event.set()
                        await response_queue.put(
                            _AsrWorkerEvent(kind="ready", generation=last_generation)
                        )
                    continue
                if event_type == "input_audio_buffer.committed":
                    item_id = event.get("item_id")
                    if isinstance(item_id, str) and item_id and pending_commits:
                        item_keys[item_id] = pending_commits.popleft()
                    continue
                if event_type in {
                    "conversation.item.input_audio_transcription.delta",
                    "conversation.item.input_audio_transcription.completed",
                }:
                    await _handle_transcript_event(event)
                    continue
                if event_type == "conversation.item.input_audio_transcription.failed":
                    item_id = event.get("item_id")
                    if isinstance(item_id, str):
                        item_keys.pop(item_id, None)
                    await _emit_error(
                        "ASR_OPENAI_TRANSCRIPTION_FAILED",
                        "OpenAI failed to transcribe a committed audio item",
                    )
                    return
                if event_type == "error":
                    await _emit_error(
                        "ASR_OPENAI_ERROR",
                        "OpenAI realtime transcription failed",
                    )
                    return
        except asyncio.CancelledError:
            raise
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception:
            await _emit_error(
                "ASR_OPENAI_WORKER_FAILED",
                "OpenAI realtime transcription failed",
            )
            return

        if not shutdown_requested.is_set() and not failure_sent:
            await _emit_error(
                "ASR_OPENAI_DISCONNECTED",
                "OpenAI realtime transcription disconnected unexpectedly",
            )

    async def _send_requests() -> None:
        nonlocal last_generation, resampler
        while True:
            request = await request_queue.get()
            try:
                last_generation = request.generation

                if request.kind == "audio":
                    wire_audio = _resample_pcm_16k_to_24k(resampler, request.audio)
                    if wire_audio:
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "input_audio_buffer.append",
                                    "audio": base64.b64encode(wire_audio).decode(
                                        "ascii"
                                    ),
                                }
                            )
                        )
                    continue

                if request.kind == "commit":
                    tail = _resample_pcm_16k_to_24k(resampler, b"", last=True)
                    if tail:
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "input_audio_buffer.append",
                                    "audio": base64.b64encode(tail).decode("ascii"),
                                }
                            )
                        )
                    pending_commits.append(
                        (
                            request.generation,
                            request.buffer_epoch,
                            request.utterance_id,
                        )
                    )
                    await websocket.send(
                        json.dumps({"type": "input_audio_buffer.commit"})
                    )
                    resampler = soxr.ResampleStream(
                        16_000,
                        24_000,
                        1,
                        dtype="float32",
                        quality="HQ",
                    )
                    continue

                if request.kind == "clear":
                    # Keep already-sent commit keys in FIFO order. Their late
                    # acknowledgements must consume the old epoch keys instead
                    # of stealing a post-clear key; Session drops old finals.
                    item_keys.clear()
                    resampler.clear()
                    resampler = soxr.ResampleStream(
                        16_000,
                        24_000,
                        1,
                        dtype="float32",
                        quality="HQ",
                    )
                    await websocket.send(
                        json.dumps({"type": "input_audio_buffer.clear"})
                    )
                    continue

                if request.kind == "shutdown":
                    shutdown_requested.set()
                    await websocket.close()
                    return

                await _emit_error(
                    "ASR_OPENAI_PROTOCOL_ERROR",
                    "OpenAI worker received an unsupported command",
                )
                return
            finally:
                request_queue.task_done()

    try:
        if config.endpointing_mode != "manual":
            await _emit_error(
                "ASR_ENDPOINTING_NOT_SUPPORTED",
                "OpenAI realtime transcription only supports manual endpointing",
            )
            return

        language = _normalize_openai_language(config.language)
        transcription: dict[str, Any] = {"model": _OPENAI_MODEL}
        if language is not None:
            transcription["language"] = language
        session_update = {
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": 24_000},
                        "transcription": transcription,
                        "turn_detection": None,
                    }
                },
            },
        }

        websocket = await websockets.connect(
            _OPENAI_REALTIME_URL,
            additional_headers={"Authorization": f"Bearer {api_key}"},
            close_timeout=_CLOSE_TIMEOUT_SECONDS,
        )
        ready_event = asyncio.Event()
        receiver_task = asyncio.create_task(
            _receive_events(ready_event), name="openai-asr-receiver"
        )
        await websocket.send(json.dumps(session_update))

        ready_wait_task = asyncio.create_task(ready_event.wait())
        done, _ = await asyncio.wait(
            {ready_wait_task, receiver_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if ready_wait_task not in done:
            ready_wait_task.cancel()
            await asyncio.gather(ready_wait_task, return_exceptions=True)
            await receiver_task
            return

        await ready_wait_task
        sender_task = asyncio.create_task(_send_requests(), name="openai-asr-sender")
        done, pending = await asyncio.wait(
            {sender_task, receiver_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            await task
        if pending:
            if shutdown_requested.is_set():
                await asyncio.gather(*pending, return_exceptions=True)
            else:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
    except asyncio.CancelledError:
        raise
    except ValueError as exc:
        await _emit_error(
            "ASR_LANGUAGE_NOT_SUPPORTED",
            str(exc).partition(": ")[2] or "OpenAI language is unsupported",
        )
    except Exception as exc:
        await _emit_error(
            (
                "ASR_CREDENTIALS_REJECTED"
                if _openai_is_auth_rejection(exc)
                else "ASR_OPENAI_WORKER_FAILED"
            ),
            (
                "OpenAI credentials were rejected"
                if _openai_is_auth_rejection(exc)
                else "OpenAI realtime transcription failed"
            ),
        )
    finally:
        shutdown_requested.set()
        for task in (sender_task, receiver_task):
            if task is not None and not task.done():
                task.cancel()
        tasks = [task for task in (sender_task, receiver_task) if task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if websocket is not None:
            try:
                await websocket.close()
            except Exception:
                pass
        if not closed_sent:
            await response_queue.put(
                _AsrWorkerEvent(kind="closed", generation=last_generation)
            )
            closed_sent = True
