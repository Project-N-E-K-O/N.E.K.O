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

"""xAI Grok streaming speech-to-text worker."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any, Literal
from urllib.parse import urlencode

import websockets

from .._infra import AsrSessionConfig, _AsrWorkerEvent, _AsrWorkerRequest
from ._shared import is_auth_rejection, normalize_zh_en_language


_GROK_STT_URL = "wss://api.x.ai/v1/stt"
_CLOSE_TIMEOUT_SECONDS = 0.5
_SHUTDOWN_TIMEOUT_SECONDS = 3.0

_UtteranceKey = tuple[int, int, int | None]
_ConnectionAction = Literal["reconnect", "shutdown", "failed"]


def _normalize_grok_language(language: str) -> str | None:
    return normalize_zh_en_language(language, provider_name="xAI")


def _grok_is_auth_rejection(exc: BaseException) -> bool:
    return is_auth_rejection(exc)


async def grok_asr_worker(
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    api_key: str,
    config: AsrSessionConfig,
) -> None:
    """Stream raw 16 kHz PCM to xAI's dedicated STT WebSocket."""

    last_generation = 0
    failure_sent = False
    ready_sent = False
    shutdown_requested = asyncio.Event()
    websocket = None

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

    async def _connect_ready(url: str):
        connection = await websockets.connect(
            url,
            additional_headers={"Authorization": f"Bearer {api_key}"},
            close_timeout=_CLOSE_TIMEOUT_SECONDS,
        )
        try:
            while True:
                message = await connection.recv()
                if isinstance(message, bytes):
                    await _emit_error(
                        "ASR_GROK_PROTOCOL_ERROR",
                        "xAI returned an unexpected binary event",
                    )
                    await connection.close()
                    return None
                try:
                    event = json.loads(message)
                except (TypeError, json.JSONDecodeError):
                    await _emit_error(
                        "ASR_GROK_PROTOCOL_ERROR",
                        "xAI returned an invalid event",
                    )
                    await connection.close()
                    return None
                if not isinstance(event, dict):
                    continue
                event_type = event.get("type")
                if event_type == "transcript.created":
                    return connection
                if event_type == "error":
                    await _emit_error(
                        "ASR_GROK_ERROR",
                        "xAI streaming transcription failed",
                    )
                    await connection.close()
                    return None
        except asyncio.CancelledError:
            raise
        except Exception:
            try:
                await connection.close()
            except Exception:
                pass
            raise

    async def _run_connection(connection) -> _ConnectionAction:
        nonlocal last_generation

        latest_audio_key: _UtteranceKey | None = None
        pending_manual_commits: deque[_UtteranceKey] = deque()
        manual_locked_segments: dict[_UtteranceKey, list[str]] = {}
        active_server_key: _UtteranceKey | None = None
        next_server_utterance_id: int | None = None
        intentional_close = asyncio.Event()

        async def _receive_events() -> None:
            nonlocal active_server_key, next_server_utterance_id
            try:
                async for message in connection:
                    if isinstance(message, bytes):
                        await _emit_error(
                            "ASR_GROK_PROTOCOL_ERROR",
                            "xAI returned an unexpected binary event",
                        )
                        return
                    try:
                        event = json.loads(message)
                    except (TypeError, json.JSONDecodeError):
                        await _emit_error(
                            "ASR_GROK_PROTOCOL_ERROR",
                            "xAI returned an invalid event",
                        )
                        return
                    if not isinstance(event, dict):
                        continue

                    event_type = event.get("type")
                    if event_type == "error":
                        await _emit_error(
                            "ASR_GROK_ERROR",
                            "xAI streaming transcription failed",
                        )
                        return
                    if event_type == "transcript.done":
                        return
                    if event_type != "transcript.partial":
                        continue

                    text = event.get("text", "")
                    if not isinstance(text, str):
                        text = ""
                    is_final = event.get("is_final") is True
                    speech_final = event.get("speech_final") is True

                    if config.endpointing_mode == "manual":
                        key = (
                            pending_manual_commits[0]
                            if pending_manual_commits
                            else latest_audio_key
                        )
                        if key is None:
                            continue
                        if is_final and speech_final:
                            if pending_manual_commits:
                                final_key = pending_manual_commits.popleft()
                                segments = manual_locked_segments.pop(final_key, [])
                                segments.append(text)
                                await response_queue.put(
                                    _AsrWorkerEvent(
                                        kind="final",
                                        generation=final_key[0],
                                        buffer_epoch=final_key[1],
                                        utterance_id=final_key[2],
                                        text="".join(segments),
                                    )
                                )
                            else:
                                # Natural endpointing can race ahead of a PTT
                                # commit. Keep every locked segment as partial;
                                # the public commit still sends ``finalize`` so
                                # later speech cannot be lost or overwritten.
                                segments = manual_locked_segments.setdefault(key, [])
                                segments.append(text)
                                await response_queue.put(
                                    _AsrWorkerEvent(
                                        kind="partial",
                                        generation=key[0],
                                        buffer_epoch=key[1],
                                        utterance_id=key[2],
                                        text="".join(segments),
                                    )
                                )
                            continue
                        # Both mutable interim results and locked chunks
                        # (is_final=true, speech_final=false) remain partial.
                        await response_queue.put(
                            _AsrWorkerEvent(
                                kind="partial",
                                generation=key[0],
                                buffer_epoch=key[1],
                                utterance_id=key[2],
                                text="".join(
                                    [*manual_locked_segments.get(key, []), text]
                                ),
                            )
                        )
                        continue

                    if active_server_key is None:
                        if latest_audio_key is None:
                            continue
                        if next_server_utterance_id is None:
                            next_server_utterance_id = latest_audio_key[2] or 1
                        active_server_key = (
                            latest_audio_key[0],
                            latest_audio_key[1],
                            next_server_utterance_id,
                        )
                        next_server_utterance_id += 1
                        await response_queue.put(
                            _AsrWorkerEvent(
                                kind="utterance_started",
                                generation=active_server_key[0],
                                buffer_epoch=active_server_key[1],
                                utterance_id=active_server_key[2],
                            )
                        )

                    key = active_server_key
                    kind = "final" if is_final and speech_final else "partial"
                    await response_queue.put(
                        _AsrWorkerEvent(
                            kind=kind,
                            generation=key[0],
                            buffer_epoch=key[1],
                            utterance_id=key[2],
                            text=text,
                        )
                    )
                    if kind == "final":
                        active_server_key = None
            except asyncio.CancelledError:
                raise
            except websockets.exceptions.ConnectionClosed:
                pass
            except Exception:
                await _emit_error(
                    "ASR_GROK_WORKER_FAILED",
                    "xAI streaming transcription failed",
                )
                return

            if (
                not intentional_close.is_set()
                and not shutdown_requested.is_set()
                and not failure_sent
            ):
                await _emit_error(
                    "ASR_GROK_DISCONNECTED",
                    "xAI streaming transcription disconnected unexpectedly",
                )

        async def _send_requests() -> _ConnectionAction:
            nonlocal latest_audio_key, last_generation
            while True:
                request = await request_queue.get()
                try:
                    last_generation = request.generation
                    key = (
                        request.generation,
                        request.buffer_epoch,
                        request.utterance_id,
                    )

                    if request.kind == "audio":
                        latest_audio_key = key
                        if request.audio:
                            await connection.send(request.audio)
                        continue

                    if request.kind == "commit":
                        if config.endpointing_mode != "manual":
                            await _emit_error(
                                "ASR_GROK_PROTOCOL_ERROR",
                                "xAI server VAD received an unexpected commit",
                            )
                            return "failed"
                        pending_manual_commits.append(key)
                        await connection.send(json.dumps({"type": "finalize"}))
                        continue

                    if request.kind == "clear":
                        # xAI STT has no native clear. Reconnect to the same
                        # provider, and don't consume later queued audio until the
                        # new transcript.created handshake completes.
                        intentional_close.set()
                        return "reconnect"

                    if request.kind == "shutdown":
                        shutdown_requested.set()
                        await connection.send(json.dumps({"type": "audio.done"}))
                        return "shutdown"

                    await _emit_error(
                        "ASR_GROK_PROTOCOL_ERROR",
                        "xAI worker received an unsupported command",
                    )
                    return "failed"
                finally:
                    request_queue.task_done()

        receiver_task = asyncio.create_task(_receive_events(), name="grok-asr-receiver")
        sender_task = asyncio.create_task(_send_requests(), name="grok-asr-sender")
        done, _ = await asyncio.wait(
            {sender_task, receiver_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        action = await sender_task if sender_task in done else None
        if receiver_task in done:
            await receiver_task
            if action is not None:
                return action
            if not sender_task.done():
                sender_task.cancel()
                await asyncio.gather(sender_task, return_exceptions=True)
            return "failed"

        assert action is not None
        if action == "shutdown":
            try:
                await asyncio.wait_for(
                    asyncio.shield(receiver_task),
                    timeout=_SHUTDOWN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                receiver_task.cancel()
                await asyncio.gather(receiver_task, return_exceptions=True)
            return action

        intentional_close.set()
        try:
            await connection.close()
        except Exception:
            pass
        if not receiver_task.done():
            receiver_task.cancel()
        await asyncio.gather(receiver_task, return_exceptions=True)
        return action

    try:
        if config.endpointing_mode not in {"manual", "provider"}:
            await _emit_error(
                "ASR_ENDPOINTING_NOT_SUPPORTED",
                "xAI endpointing mode is unsupported",
            )
            return

        language = _normalize_grok_language(config.language)
        query: dict[str, Any] = {
            "sample_rate": 16_000,
            "encoding": "pcm",
            "interim_results": "true",
        }
        if config.endpointing_mode == "provider":
            # Pin xAI's documented default so provider behavior cannot drift
            # silently if the upstream default changes.
            query["endpointing"] = 10
        if language is not None:
            query["language"] = language
        url = f"{_GROK_STT_URL}?{urlencode(query)}"

        while True:
            websocket = await _connect_ready(url)
            if websocket is None:
                return
            if not ready_sent:
                ready_sent = True
                await response_queue.put(
                    _AsrWorkerEvent(kind="ready", generation=last_generation)
                )

            action = await _run_connection(websocket)
            try:
                await websocket.close()
            except Exception:
                pass
            websocket = None

            if action == "reconnect":
                continue
            return
    except asyncio.CancelledError:
        raise
    except ValueError as exc:
        await _emit_error(
            "ASR_LANGUAGE_NOT_SUPPORTED",
            str(exc).partition(": ")[2] or "xAI language is unsupported",
        )
    except Exception as exc:
        await _emit_error(
            (
                "ASR_CREDENTIALS_REJECTED"
                if _grok_is_auth_rejection(exc)
                else "ASR_GROK_WORKER_FAILED"
            ),
            (
                "xAI credentials were rejected"
                if _grok_is_auth_rejection(exc)
                else "xAI streaming transcription failed"
            ),
        )
    finally:
        shutdown_requested.set()
        if websocket is not None:
            try:
                await websocket.close()
            except Exception:
                pass
        await response_queue.put(
            _AsrWorkerEvent(kind="closed", generation=last_generation)
        )
