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

"""Non-streaming worker for the speech-recognition slot.

Every slot vendor runs here: the worker buffers provider-neutral 16 kHz mono
PCM16 per utterance and sends one request per manual commit (a SmartTurn
seal), exactly like the GLM segmented worker. Only the request differs per
protocol:

- ``openai_multipart``: multipart WAV upload returning ``{"text": ...}``
  (OpenAI, xAI, GLM and any self-hosted OpenAI-compatible server);
- ``qwen_chat``: DashScope OpenAI-compatible chat completion with an
  ``input_audio`` part (qwen3-asr-flash);
- ``gemini``: native Gemini ``generate_content`` with inline audio.

The same request functions serve the offline (whole-recording) layer.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from .._infra import AsrSessionConfig, _AsrWorkerEvent, _AsrWorkerRequest
from ..delivery import (
    begin_transport_write,
    complete_transport_write,
    interval_delivery_spans,
    retire_interval_deliveries,
)
from ..slot import AsrSlotEndpoint
from . import gemini as _gemini
from ._http_transcription import HttpClient, post_wav_transcription
from ._shared import (
    MAX_SEGMENT_PCM_BYTES,
    PCM16_SAMPLE_WIDTH_BYTES,
    http_failure_kind,
    transport_failure_kind,
    AsrRequestFailure,
    encode_pcm16_wav,
    is_auth_rejection,
    normalize_zh_en_language,
    provider_status_code,
)
from .qwen import _qwen_language_code


_VENDOR_TIMEOUT_SECONDS = 35.0
# A self-hosted server may decode on CPU; give it longer than a cloud vendor.
_CUSTOM_TIMEOUT_SECONDS = 55.0

_UtteranceKey = tuple[int, int, int]

_PROVIDER_LABELS: dict[str, str] = {
    "openai": "OpenAI",
    "grok": "xAI",
    "glm": "GLM",
    "gemini": "Gemini",
    "qwen": "Qwen",
    "qwen_intl": "Qwen",
    "custom": "Custom ASR",
}


def request_timeout_seconds(endpoint: AsrSlotEndpoint) -> float:
    return _CUSTOM_TIMEOUT_SECONDS if endpoint.vendor == "custom" else _VENDOR_TIMEOUT_SECONDS


def code_prefix(endpoint: AsrSlotEndpoint) -> str:
    return f"ASR_{endpoint.provider_key.upper()}"


def _provider_label(endpoint: AsrSlotEndpoint) -> str:
    return _PROVIDER_LABELS.get(endpoint.vendor, endpoint.vendor)


def _primary_language(language: str | None) -> str | None:
    normalized = str(language or "").strip().lower().replace("_", "-")
    if not normalized or normalized == "auto":
        return None
    return normalized.split("-", 1)[0] or None


def _multipart_language(endpoint: AsrSlotEndpoint, language: str | None) -> str | None:
    if endpoint.vendor == "custom":
        # ISO-639-1, as the OpenAI transcription API documents it.
        return _primary_language(language)
    if endpoint.vendor == "grok":
        try:
            return normalize_zh_en_language(str(language or "auto"), provider_name="xAI")
        except ValueError:
            return None
    # OpenAI's gpt-transcribe detects the language itself and GLM ignores it.
    return None


def _qwen_language(language: str | None) -> str | None:
    try:
        return _qwen_language_code(str(language or "auto"))
    except ValueError:
        return None


def create_slot_client(endpoint: AsrSlotEndpoint, api_key: str) -> Any:
    """The client one worker or one offline recording reuses for requests."""

    if endpoint.protocol == "gemini":
        return _gemini._create_gemini_client(api_key)
    return httpx.AsyncClient(timeout=request_timeout_seconds(endpoint))


async def _post_qwen_chat(
    client: HttpClient,
    endpoint: AsrSlotEndpoint,
    api_key: str,
    wav_audio: bytes,
    *,
    language: str | None,
    on_sent: Callable[[], None] | None,
) -> str:
    prefix = code_prefix(endpoint)
    asr_options: dict[str, Any] = {"enable_itn": False}
    hint = _qwen_language(language)
    if hint:
        asr_options["language"] = hint
    audio_url = "data:audio/wav;base64," + base64.b64encode(wav_audio).decode("ascii")
    try:
        response = await client.post(
            endpoint.url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": endpoint.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_audio", "input_audio": {"data": audio_url}},
                        ],
                    }
                ],
                "stream": False,
                "asr_options": asr_options,
            },
        )
        if on_sent is not None:
            on_sent()
        response.raise_for_status()
    except asyncio.CancelledError:
        raise
    except httpx.HTTPStatusError as exc:
        if is_auth_rejection(exc):
            raise AsrRequestFailure(
                "ASR_CREDENTIALS_REJECTED",
                "Qwen credentials were rejected",
                kind="auth",
            ) from exc
        status = provider_status_code(exc)
        raise AsrRequestFailure(
            f"{prefix}_ERROR",
            "Qwen transcription request was rejected",
            kind=http_failure_kind(status),
        ) from exc
    except httpx.TimeoutException as exc:
        raise AsrRequestFailure(
            f"{prefix}_TIMEOUT",
            "Qwen transcription request timed out",
            kind="timeout",
        ) from exc
    except Exception as exc:
        raise AsrRequestFailure(
            f"{prefix}_WORKER_FAILED",
            "Qwen transcription request failed",
            kind=transport_failure_kind(exc),
        ) from exc

    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except Exception as exc:
        raise AsrRequestFailure(
            f"{prefix}_PROTOCOL_ERROR",
            "Qwen returned an invalid response",
            kind="invalid_response",
        ) from exc
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            str(part.get("text") or "")
            for part in content
            if isinstance(part, Mapping)
        ]
        return "".join(parts).strip()
    raise AsrRequestFailure(
        f"{prefix}_PROTOCOL_ERROR",
        "Qwen returned an invalid transcript response",
        kind="invalid_response",
    )


async def request_slot_transcript(
    client: Any,
    endpoint: AsrSlotEndpoint,
    api_key: str,
    wav_audio: bytes,
    *,
    language: str | None = None,
    single_attempt: bool = False,
    on_sent: Callable[[], None] | None = None,
) -> str:
    """Send one WAV file to the slot's endpoint and return its transcript.

    Raises :class:`AsrRequestFailure` on every failure.
    """

    if endpoint.protocol == "gemini":
        return await _gemini.request_gemini_transcript(
            client,
            wav_audio,
            model=endpoint.model,
            timeout_seconds=request_timeout_seconds(endpoint),
            single_attempt=single_attempt,
            on_sent=on_sent,
            language=language,
        )
    if endpoint.protocol == "qwen_chat":
        return await _post_qwen_chat(
            client,
            endpoint,
            api_key,
            wav_audio,
            language=language,
            on_sent=on_sent,
        )
    form_fields: dict[str, str] = {}
    if endpoint.model:
        form_fields["model"] = endpoint.model
    hint = _multipart_language(endpoint, language)
    if hint:
        form_fields["language"] = hint
    return await post_wav_transcription(
        client,
        url=endpoint.url,
        api_key=api_key,
        wav_audio=wav_audio,
        form_fields=form_fields,
        code_prefix=code_prefix(endpoint),
        provider_label=_provider_label(endpoint),
        on_sent=on_sent,
    )


async def _transcribe(
    client: Any,
    endpoint: AsrSlotEndpoint,
    api_key: str,
    language: str,
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
        text = await request_slot_transcript(
            client,
            endpoint,
            api_key,
            wav_audio,
            language=language,
            # Protected delivery must never resend audio behind the caller's back.
            single_attempt=True,
            on_sent=lambda: complete_transport_write(
                evidence, len(pcm16), generation=generation,
                buffer_epoch=buffer_epoch, provider=endpoint.provider_key,
                delivery_spans=delivery_spans, takes_ownership=True,
            ),
        )
    except (asyncio.CancelledError, AsrRequestFailure):
        raise
    except Exception as exc:
        raise AsrRequestFailure(
            f"{code_prefix(endpoint)}_WORKER_FAILED",
            f"{_provider_label(endpoint)} transcription request failed",
            kind=transport_failure_kind(exc),
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


async def slot_request_asr_worker(
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    api_key: str,
    config: AsrSessionConfig,
    *,
    endpoint: AsrSlotEndpoint,
    client_factory: Callable[[AsrSlotEndpoint, str], Any] | None = None,
) -> None:
    """Buffer normalized PCM and transcribe each manually committed utterance."""

    prefix = code_prefix(endpoint)
    label = _provider_label(endpoint)
    last_generation = 0
    current_generation = 0
    current_buffer_epoch = 0
    request_task: asyncio.Task[_AsrWorkerRequest] | None = None
    pending: dict[asyncio.Task[_AsrWorkerEvent], _UtteranceKey] = {}
    buffers: dict[_UtteranceKey, bytearray] = {}
    committed: set[_UtteranceKey] = set()
    client: Any = None
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
        # A self-hosted server may run without a key; every vendor needs one.
        if endpoint.vendor != "custom" and not api_key.strip():
            await emit_error(
                "ASR_CREDENTIALS_MISSING",
                f"{label} credentials are missing",
            )
            return
        if config.endpointing_mode != "manual":
            await emit_error(
                "ASR_ENDPOINTING_NOT_SUPPORTED",
                f"{label} non-streaming transcription only supports manual endpointing",
            )
            return

        client = (client_factory or create_slot_client)(endpoint, api_key)
        await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
        request_task = asyncio.create_task(
            request_queue.get(),  # noqa: ASYNC_BLOCK - this is an asyncio.Queue.
            name="slot-asr-request",
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
                                f"{prefix}_PROTOCOL_ERROR",
                                f"{label} worker received a command without an utterance ID",
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
                                    f"{prefix}_PROTOCOL_ERROR",
                                    f"{label} worker received invalid PCM16 audio",
                                )
                                should_stop = True
                            else:
                                buffer = buffers.setdefault(key, bytearray())
                                buffer.extend(request.audio)
                                if len(buffer) > MAX_SEGMENT_PCM_BYTES:
                                    buffers.pop(key, None)
                                    await emit_error(
                                        f"{prefix}_AUDIO_TOO_LONG",
                                        f"{label} utterance exceeds the 28 second limit",
                                    )
                                    should_stop = True
                        elif request.kind == "commit":
                            key = (
                                request.generation,
                                request.buffer_epoch,
                                request.utterance_id,
                            )
                            if key in committed or key in pending.values():
                                # A duplicate commit must not post a second
                                # provider request or a second final.
                                pass
                            else:
                                pcm16 = buffers.pop(key, None)
                                if pcm16:
                                    committed.add(key)
                                    task = asyncio.create_task(
                                        _transcribe(
                                            client, endpoint, api_key,
                                            config.language, key, bytes(pcm16),
                                            request_queue=request_queue,
                                        ),
                                        name="slot-asr-transcribe",
                                    )
                                    pending[task] = key
                        else:
                            await emit_error(
                                f"{prefix}_PROTOCOL_ERROR",
                                f"{label} worker received an unsupported command",
                            )
                            should_stop = True
                finally:
                    request_queue.task_done()

                if should_stop:
                    break
                request_task = asyncio.create_task(
                    request_queue.get(),  # noqa: ASYNC_BLOCK - asyncio.Queue.
                    name="slot-asr-request",
                )

            completed_transcriptions = [task for task in done if task in pending]
            for task in completed_transcriptions:
                key = pending.pop(task)
                try:
                    event = task.result()
                except asyncio.CancelledError:
                    continue
                except AsrRequestFailure as exc:
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
            f"{prefix}_WORKER_FAILED",
            f"{label} transcription worker failed",
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
        if client is not None:
            for owner in (client, getattr(client, "aio", None)):
                close = getattr(owner, "aclose", None)
                if callable(close):
                    try:
                        await close()
                    except Exception:
                        pass
                    break
        await response_queue.put(
            _AsrWorkerEvent(kind="closed", generation=last_generation)
        )


__all__ = [
    "create_slot_client",
    "request_slot_transcript",
    "request_timeout_seconds",
    "slot_request_asr_worker",
]
