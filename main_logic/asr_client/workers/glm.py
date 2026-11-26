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
from collections.abc import Callable

import httpx

from .._infra import AsrSessionConfig, _AsrWorkerEvent, _AsrWorkerRequest
from ..delivery import begin_transport_write, complete_transport_write, interval_delivery_spans, retire_interval_deliveries
from ._http_transcription import HttpClient, post_wav_transcription
from ._shared import (
    MAX_SEGMENT_PCM_BYTES,
    PCM16_SAMPLE_WIDTH_BYTES,
    AsrRequestFailure,
    encode_pcm16_wav,
)


GLM_ASR_URL = "https://open.bigmodel.cn/api/paas/v4/audio/transcriptions"
GLM_ASR_MODEL = "glm-asr-2512"

_HTTP_TIMEOUT_SECONDS = 35.0

_UtteranceKey = tuple[int, int, int]


# Kept under the worker's historical names; the request itself is shared with
# the offline layer through ``post_wav_transcription``.
_HttpClient = HttpClient
_GlmRequestFailure = AsrRequestFailure


async def request_glm_transcript(
    client: HttpClient,
    api_key: str,
    wav_audio: bytes,
    *,
    model: str = GLM_ASR_MODEL,
    on_sent: Callable[[], None] | None = None,
) -> str:
    """Submit one WAV file to GLM ASR and return its transcript."""

    return await post_wav_transcription(
        client,
        url=GLM_ASR_URL,
        api_key=api_key,
        wav_audio=wav_audio,
        form_fields={"model": model},
        code_prefix="ASR_GLM",
        provider_label="GLM",
        on_sent=on_sent,
    )


async def _transcribe(
    client: _HttpClient,
    api_key: str,
    key: _UtteranceKey,
    pcm16: bytes,
    *,
    request_queue: asyncio.Queue[_AsrWorkerRequest],
) -> _AsrWorkerEvent:
    generation, buffer_epoch, utterance_id = key
    delivery_spans = interval_delivery_spans(request_queue, key)
    try:
        wav_audio = encode_pcm16_wav(pcm16)
        evidence = begin_transport_write(request_queue, delivery_spans=delivery_spans)
        text = await request_glm_transcript(
            client,
            api_key,
            wav_audio,
            on_sent=lambda: complete_transport_write(
                evidence, len(pcm16), generation=generation,
                buffer_epoch=buffer_epoch, provider="glm",
                delivery_spans=delivery_spans, takes_ownership=True,
            ),
        )
    except (asyncio.CancelledError, AsrRequestFailure):
        raise
    except Exception as exc:
        raise _GlmRequestFailure(
            "ASR_GLM_WORKER_FAILED",
            "GLM transcription request failed",
        ) from exc
    finally:
        retire_interval_deliveries(request_queue, only_spans=delivery_spans)

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
    committed: set[_UtteranceKey] = set()
    owned_client = http_client is None
    client: _HttpClient | None = http_client
    failure_sent = False

    async def emit_error(
        code: str,
        message: str,
        *,
        item_key: _UtteranceKey | None = None,
    ) -> None:
        nonlocal failure_sent
        if failure_sent:
            return
        failure_sent = True
        generation, buffer_epoch, utterance_id = (
            item_key if item_key is not None else (last_generation, 0, None)
        )
        await response_queue.put(
            _AsrWorkerEvent(
                kind="error",
                generation=generation,
                buffer_epoch=buffer_epoch,
                utterance_id=utterance_id,
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

            should_stop = False
            if request_task in done:
                completed_request_task = request_task
                request_task = None
                request = completed_request_task.result()
                last_generation = request.generation
                try:
                    if request.kind == "shutdown":
                        current_generation = request.generation
                        current_buffer_epoch = request.buffer_epoch
                        buffers.clear()
                        committed.clear()
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
                            committed.clear()
                            await cancel_pending(keep_current_scope=True)

                        if stale:
                            pass
                        elif request.kind == "clear":
                            buffers.clear()
                            committed.clear()
                            await cancel_pending()
                        elif request.utterance_id is None:
                            await emit_error(
                                "ASR_GLM_PROTOCOL_ERROR",
                                "GLM worker received a command without an utterance ID",
                            )
                            should_stop = True
                        elif request.kind == "audio":
                            key = (
                                request.generation,
                                request.buffer_epoch,
                                request.utterance_id,
                            )
                            if key in committed or key in pending.values():
                                # Late audio for a committed utterance must not
                                # re-accumulate a second buffer for its key.
                                pass
                            elif len(request.audio) % PCM16_SAMPLE_WIDTH_BYTES:
                                await emit_error(
                                    "ASR_GLM_PROTOCOL_ERROR",
                                    "GLM worker received invalid PCM16 audio",
                                )
                                should_stop = True
                            else:
                                buffer = buffers.setdefault(key, bytearray())
                                buffer.extend(request.audio)
                                if len(buffer) > MAX_SEGMENT_PCM_BYTES:
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
                            if key in committed or key in pending.values():
                                # A duplicate commit for an utterance that is
                                # already inflight or finished must not post a
                                # second provider request or second final.
                                pass
                            else:
                                pcm16 = buffers.pop(key, None)
                                if pcm16:
                                    committed.add(key)
                                    assert client is not None
                                    task = asyncio.create_task(
                                        _transcribe(
                                            client, api_key, key, bytes(pcm16),
                                            request_queue=request_queue,
                                        ),
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

            completed_transcriptions = [
                task for task in done if task in pending
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
                    await emit_error(exc.code, exc.message, item_key=key)
                    return
                if key[:2] == (current_generation, current_buffer_epoch):
                    await response_queue.put(event)
    except asyncio.CancelledError:
        raise
    except Exception:
        await emit_error(
            "ASR_GLM_WORKER_FAILED",
            "GLM transcription worker failed",
        )
    finally:
        if request_task is not None:
            if not request_task.done():
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
            if not request_task.cancelled():
                try:
                    request_task.result()
                except Exception:
                    pass
                else:
                    request_queue.task_done()
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
