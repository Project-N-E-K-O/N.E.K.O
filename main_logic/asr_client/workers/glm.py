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

"""GLM segmented-request ASR worker.

The worker receives provider-neutral 16 kHz mono PCM16 chunks, buffers them by
session/epoch/utterance identity, and submits one WAV file per manual commit.
It deliberately has no VAD or Smart Turn knowledge; endpoint selection remains
the caller's responsibility.
"""

from __future__ import annotations

import asyncio
import io
import wave
from collections.abc import Mapping
from typing import Any, Protocol

import httpx

from .._infra import AsrSessionConfig, _AsrWorkerEvent, _AsrWorkerRequest


GLM_ASR_URL = "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions"
GLM_ASR_MODEL = "glm-asr-2512"

_SAMPLE_RATE_HZ = 16_000
_SAMPLE_WIDTH_BYTES = 2
_MAX_AUDIO_SECONDS = 28
_MAX_PCM_BYTES = _SAMPLE_RATE_HZ * _SAMPLE_WIDTH_BYTES * _MAX_AUDIO_SECONDS
_HTTP_TIMEOUT_SECONDS = 35.0

_UtteranceKey = tuple[int, int, int]


class _HttpResponse(Protocol):
    status_code: int

    def raise_for_status(self) -> None: ...

    def json(self) -> Any: ...


class _HttpClient(Protocol):
    async def post(self, url: str, **kwargs: Any) -> _HttpResponse: ...


class _GlmRequestFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _encode_pcm16_wav(pcm16: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(_SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(_SAMPLE_RATE_HZ)
        wav_file.writeframes(pcm16)
    return output.getvalue()


def _http_status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


async def _transcribe(
    client: _HttpClient,
    api_key: str,
    key: _UtteranceKey,
    pcm16: bytes,
) -> _AsrWorkerEvent:
    generation, buffer_epoch, utterance_id = key
    try:
        response = await client.post(
            GLM_ASR_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            data={"model": GLM_ASR_MODEL},
            files={"file": ("audio.wav", _encode_pcm16_wav(pcm16), "audio/wav")},
        )
        response.raise_for_status()
    except asyncio.CancelledError:
        raise
    except httpx.HTTPStatusError as exc:
        if _http_status(exc) in {401, 403}:
            raise _GlmRequestFailure(
                "ASR_CREDENTIALS_REJECTED",
                "GLM credentials were rejected",
            ) from exc
        raise _GlmRequestFailure(
            "ASR_GLM_ERROR",
            "GLM transcription request was rejected",
        ) from exc
    except httpx.TimeoutException as exc:
        raise _GlmRequestFailure(
            "ASR_GLM_TIMEOUT",
            "GLM transcription request timed out",
        ) from exc
    except Exception as exc:
        raise _GlmRequestFailure(
            "ASR_GLM_WORKER_FAILED",
            "GLM transcription request failed",
        ) from exc

    try:
        payload = response.json()
    except Exception as exc:
        raise _GlmRequestFailure(
            "ASR_GLM_PROTOCOL_ERROR",
            "GLM returned an invalid response",
        ) from exc
    if not isinstance(payload, Mapping) or not isinstance(payload.get("text"), str):
        raise _GlmRequestFailure(
            "ASR_GLM_PROTOCOL_ERROR",
            "GLM returned an invalid transcript response",
        )
    text = payload["text"].strip()
    return _AsrWorkerEvent(
        kind="final",
        generation=generation,
        buffer_epoch=buffer_epoch,
        utterance_id=utterance_id,
        text=text,
    )


async def glm_asr_worker(
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    api_key: str,
    config: AsrSessionConfig,
    *,
    http_client: _HttpClient | None = None,
) -> None:
    """Buffer normalized PCM and transcribe each manually committed utterance."""

    last_generation = 0
    current_generation = 0
    current_buffer_epoch = 0
    request_task: asyncio.Task[_AsrWorkerRequest] | None = None
    pending: dict[asyncio.Task[_AsrWorkerEvent], _UtteranceKey] = {}
    buffers: dict[_UtteranceKey, bytearray] = {}
    owned_client = http_client is None
    client: _HttpClient | None = http_client
    failure_sent = False

    async def emit_error(code: str, message: str) -> None:
        nonlocal failure_sent
        if failure_sent:
            return
        failure_sent = True
        await response_queue.put(
            _AsrWorkerEvent(
                kind="error",
                generation=last_generation,
                error_code=code,
                error_message=message,
            )
        )

    async def cancel_pending(*, keep_current_scope: bool = False) -> None:
        tasks = [
            task
            for task, key in pending.items()
            if not keep_current_scope
            or key[:2] != (current_generation, current_buffer_epoch)
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for task in tasks:
            pending.pop(task, None)

    try:
        if not api_key.strip():
            await emit_error(
                "ASR_CREDENTIALS_MISSING",
                "GLM credentials are missing",
            )
            return
        if config.endpointing_mode != "manual":
            await emit_error(
                "ASR_ENDPOINTING_NOT_SUPPORTED",
                "GLM segmented transcription only supports manual endpointing",
            )
            return

        if client is None:
            client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS)
        await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
        request_task = asyncio.create_task(
            request_queue.get(),  # noqa: ASYNC_BLOCK - this is an asyncio.Queue.
            name="glm-asr-request",
        )

        while True:
            done, _ = await asyncio.wait(
                {request_task, *pending},
                return_when=asyncio.FIRST_COMPLETED,
            )

            completed_transcriptions = [
                task for task in done if task is not request_task and task in pending
            ]
            for task in completed_transcriptions:
                key = pending.pop(task)
                try:
                    event = task.result()
                except asyncio.CancelledError:
                    continue
                except _GlmRequestFailure as exc:
                    if key[:2] != (current_generation, current_buffer_epoch):
                        continue
                    await emit_error(exc.code, exc.message)
                    return
                if key[:2] == (current_generation, current_buffer_epoch):
                    await response_queue.put(event)

            if request_task in done:
                request = request_task.result()
                last_generation = request.generation
                should_stop = False
                try:
                    if request.kind == "shutdown":
                        current_generation = request.generation
                        current_buffer_epoch = request.buffer_epoch
                        buffers.clear()
                        await cancel_pending()
                        should_stop = True
                    else:
                        stale = False
                        scope_advanced = False
                        if request.generation < current_generation:
                            stale = True
                        elif request.generation > current_generation:
                            current_generation = request.generation
                            current_buffer_epoch = request.buffer_epoch
                            scope_advanced = True
                        elif request.buffer_epoch < current_buffer_epoch:
                            stale = True
                        elif request.buffer_epoch > current_buffer_epoch:
                            current_buffer_epoch = request.buffer_epoch
                            scope_advanced = True

                        if scope_advanced:
                            buffers.clear()
                            await cancel_pending(keep_current_scope=True)

                        if stale:
                            pass
                        elif request.kind == "clear":
                            buffers.clear()
                            await cancel_pending()
                        elif request.utterance_id is None:
                            await emit_error(
                                "ASR_GLM_PROTOCOL_ERROR",
                                "GLM worker received a command without an utterance ID",
                            )
                            should_stop = True
                        elif request.kind == "audio":
                            if len(request.audio) % _SAMPLE_WIDTH_BYTES:
                                await emit_error(
                                    "ASR_GLM_PROTOCOL_ERROR",
                                    "GLM worker received invalid PCM16 audio",
                                )
                                should_stop = True
                            else:
                                key = (
                                    request.generation,
                                    request.buffer_epoch,
                                    request.utterance_id,
                                )
                                buffer = buffers.setdefault(key, bytearray())
                                buffer.extend(request.audio)
                                if len(buffer) > _MAX_PCM_BYTES:
                                    buffers.pop(key, None)
                                    await emit_error(
                                        "ASR_GLM_AUDIO_TOO_LONG",
                                        "GLM utterance exceeds the 28 second limit",
                                    )
                                    should_stop = True
                        elif request.kind == "commit":
                            key = (
                                request.generation,
                                request.buffer_epoch,
                                request.utterance_id,
                            )
                            pcm16 = buffers.pop(key, None)
                            if pcm16:
                                assert client is not None
                                task = asyncio.create_task(
                                    _transcribe(client, api_key, key, bytes(pcm16)),
                                    name="glm-asr-transcribe",
                                )
                                pending[task] = key
                        else:
                            await emit_error(
                                "ASR_GLM_PROTOCOL_ERROR",
                                "GLM worker received an unsupported command",
                            )
                            should_stop = True
                finally:
                    request_queue.task_done()

                if should_stop:
                    break
                request_task = asyncio.create_task(
                    request_queue.get(),  # noqa: ASYNC_BLOCK - asyncio.Queue.
                    name="glm-asr-request",
                )
    except asyncio.CancelledError:
        raise
    except Exception:
        await emit_error(
            "ASR_GLM_WORKER_FAILED",
            "GLM transcription worker failed",
        )
    finally:
        if request_task is not None and not request_task.done():
            request_task.cancel()
            await asyncio.gather(request_task, return_exceptions=True)
        await cancel_pending()
        buffers.clear()
        if owned_client and client is not None:
            close = getattr(client, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    pass
        await response_queue.put(
            _AsrWorkerEvent(kind="closed", generation=last_generation)
        )
