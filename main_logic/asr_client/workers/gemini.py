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

"""Segmented Gemini model-audio ASR worker.

This worker deliberately knows nothing about VAD or Smart Turn. It buffers the
already-normalized 16 kHz PCM belonging to one provider-neutral utterance and
submits exactly one WAV request when the session commits that utterance.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from typing import Any, TypeAlias

import httpx

from config.providers import clamp_reasoning_effort

from .._infra import AsrSessionConfig, _AsrWorkerEvent, _AsrWorkerRequest
from ..delivery import begin_transport_write, complete_transport_write, interval_delivery_spans, retire_interval_deliveries
from ._shared import (
    MAX_SEGMENT_PCM_BYTES,
    http_failure_kind,
    transport_failure_kind,
    AsrRequestFailure,
    encode_pcm16_wav,
    is_auth_rejection,
    provider_status_code,
)


_GEMINI_MODEL = "gemini-3.1-flash-lite"
_GEMINI_TRANSCRIPTION_PROMPT = (
    "Transcribe the human speech in the audio verbatim.\n"
    "Preserve the original spoken language; do not answer questions, explain, "
    "summarize, or translate the content.\n"
    "Mark unintelligible audio as [inaudible].\n"
    "Return only the transcript matching the specified response schema."
)
_GEMINI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"transcript": {"type": "string"}},
    "required": ["transcript"],
    "additionalProperties": False,
}
_GOOGLE_API_KEY_REJECTION_REASONS = frozenset(
    {
        "API_KEY_INVALID",
        "API_KEY_SERVICE_BLOCKED",
        "API_KEY_HTTP_REFERRER_BLOCKED",
        "API_KEY_IP_ADDRESS_BLOCKED",
        "API_KEY_ANDROID_APP_BLOCKED",
        "API_KEY_IOS_APP_BLOCKED",
    }
)
_REQUEST_TIMEOUT_SECONDS = 35.0

_UtteranceKey: TypeAlias = tuple[int, int, int]


def _create_gemini_client(api_key: str) -> Any:
    """Create the native Gemini client lazily so module import stays cheap."""

    from google import genai

    return genai.Client(api_key=api_key)


def _response_transcript(response: Any) -> str:
    """Extract and validate the one allowed structured response field."""

    payload = getattr(response, "parsed", None)
    if payload is not None and not isinstance(payload, Mapping):
        model_dump = getattr(payload, "model_dump", None)
        payload = model_dump() if callable(model_dump) else None
    if payload is None:
        raw_text = getattr(response, "text", "")
        if not isinstance(raw_text, str):
            raise ValueError("Gemini response has no JSON text")
        try:
            payload = json.loads(raw_text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Gemini response is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("Gemini response is not an object")
    transcript = payload.get("transcript")
    if not isinstance(transcript, str):
        raise ValueError("Gemini response has no transcript")
    return transcript.strip()


def _has_google_api_key_rejection(details: object) -> bool:
    if not isinstance(details, Mapping):
        return False

    error = details.get("error", details)
    if not isinstance(error, Mapping):
        return False

    error_details = error.get("details")
    if not isinstance(error_details, list):
        return False

    for item in error_details:
        if not isinstance(item, Mapping):
            continue
        if item.get("@type") != "type.googleapis.com/google.rpc.ErrorInfo":
            continue
        if item.get("domain") != "googleapis.com":
            continue
        if item.get("reason") in _GOOGLE_API_KEY_REJECTION_REASONS:
            return True
    return False


def _is_auth_rejection(exc: BaseException) -> bool:
    if is_auth_rejection(exc):
        return True

    response = getattr(exc, "response", None)
    code = getattr(exc, "code", None)
    response_status = getattr(response, "status", None)
    if code in {401, 403} or response_status in {401, 403}:
        return True

    effective_code = code if code is not None else response_status
    if effective_code not in {None, 400}:
        return False
    return _has_google_api_key_rejection(getattr(exc, "details", None))


_TRADITIONAL_CHINESE_HINTS = ("zh-tw", "zh-hk", "zh-mo", "zh-hant")


def _language_hint(language: str | None) -> str:
    """One prompt line naming the expected language, or "" for detection.

    Without it Gemini picks the Chinese script on its own (live tests returned
    Traditional characters for Mandarin from a zh-CN user).
    """

    normalized = str(language or "").strip().lower().replace("_", "-")
    if not normalized or normalized == "auto":
        return ""
    if normalized.startswith("zh"):
        if normalized.startswith(_TRADITIONAL_CHINESE_HINTS):
            return (
                "\nThe user reads Traditional Chinese: write all Chinese text in "
                "Traditional characters only, never Simplified."
            )
        return (
            "\nThe user reads Simplified Chinese: write all Chinese text in "
            "Simplified characters only, never Traditional."
        )
    return f"\nThe speech is most likely in the language with BCP-47 code {normalized}."


def _thinking_config(model: str) -> dict[str, Any]:
    """The lowest thinking setting the selected model accepts.

    Gemini 3.x takes ``thinking_level`` and cannot go below its vendor floor
    (``minimal`` for most, ``low`` for 3.x Pro and 3.7 / 3.8 Flash). Older
    models do not understand ``thinking_level`` at all, so they keep their
    default instead of getting a rejected request.
    """

    upstream = (model or "").rsplit("/", 1)[-1].split(":", 1)[0].strip().lower()
    if not upstream.startswith("gemini-3"):
        return {}
    return {"thinking_config": {"thinking_level": clamp_reasoning_effort(upstream, "minimal")}}


async def request_gemini_transcript(
    client: Any,
    wav_audio: bytes,
    *,
    model: str = _GEMINI_MODEL,
    timeout_seconds: float | None = None,
    single_attempt: bool = False,
    on_sent: Callable[[], None] | None = None,
    language: str | None = None,
) -> str:
    """Transcribe one WAV file with Gemini; shared with the offline layer.

    Raises :class:`AsrRequestFailure` (``ASR_GEMINI_TIMEOUT``,
    ``ASR_GEMINI_INVALID_RESPONSE``, ``ASR_CREDENTIALS_REJECTED`` or
    ``ASR_GEMINI_REQUEST_FAILED``). ``single_attempt`` disables SDK retries,
    which protected-delivery audio requires. ``language`` (offline only; the
    realtime worker never passes it) adds a script/language hint.
    """

    try:
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=model,
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {"text": _GEMINI_TRANSCRIPTION_PROMPT + _language_hint(language)},
                            {
                                "inline_data": {
                                    "mime_type": "audio/wav",
                                    "data": wav_audio,
                                }
                            },
                        ],
                    }
                ],
                config={
                    **({"http_options": {"retry_options": {"attempts": 1}}} if single_attempt else {}),
                    "temperature": 0,
                    **_thinking_config(model),
                    "response_mime_type": "application/json",
                    "response_json_schema": _GEMINI_RESPONSE_SCHEMA,
                },
            ),
            _REQUEST_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds,
        )
        if on_sent is not None:
            on_sent()
        return _response_transcript(response)
    except asyncio.CancelledError:
        raise
    except httpx.TimeoutException as exc:
        # The SDK lets transport timeouts through as httpx errors; keep the
        # realtime code but classify them as timeouts.
        raise AsrRequestFailure(
            "ASR_GEMINI_REQUEST_FAILED",
            "Gemini ASR request failed",
            kind="timeout",
        ) from exc
    except asyncio.TimeoutError as exc:
        raise AsrRequestFailure(
            "ASR_GEMINI_TIMEOUT",
            "Gemini ASR request timed out",
            kind="timeout",
        ) from exc
    except ValueError as exc:
        raise AsrRequestFailure(
            "ASR_GEMINI_INVALID_RESPONSE",
            "Gemini ASR returned an invalid transcript",
            kind="invalid_response",
        ) from exc
    except Exception as exc:
        if _is_auth_rejection(exc):
            raise AsrRequestFailure(
                "ASR_CREDENTIALS_REJECTED",
                "Gemini ASR credentials were rejected",
                kind="auth",
            ) from exc
        status = provider_status_code(exc)
        if status is None:
            status = getattr(exc, "code", None)
        raise AsrRequestFailure(
            "ASR_GEMINI_REQUEST_FAILED",
            "Gemini ASR request failed",
            kind=(
                http_failure_kind(status)
                if isinstance(status, int)
                else transport_failure_kind(exc)
            ),
        ) from exc


async def gemini_asr_worker(
    request_queue: asyncio.Queue[_AsrWorkerRequest],
    response_queue: asyncio.Queue[_AsrWorkerEvent],
    api_key: str,
    config: AsrSessionConfig,
    *,
    client: Any | None = None,
) -> None:
    """Buffer PCM turns and transcribe each committed turn with Gemini."""

    active_generation = 0
    active_buffer_epoch = 0
    last_utterance_id: int | None = None
    closed_sent = False
    owns_client = client is None
    buffers: dict[_UtteranceKey, bytearray] = {}
    committed: set[_UtteranceKey] = set()
    inflight: dict[_UtteranceKey, asyncio.Task[None]] = {}

    def _is_current(key: _UtteranceKey) -> bool:
        return key[:2] == (active_generation, active_buffer_epoch)

    async def _cancel_inflight() -> None:
        tasks = tuple(inflight.values())
        inflight.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _transcribe(key: _UtteranceKey, pcm16: bytes) -> None:
        delivery_spans = interval_delivery_spans(request_queue, key)
        failure: AsrRequestFailure | None = None
        try:
            try:
                wav_audio = encode_pcm16_wav(pcm16)
                evidence = begin_transport_write(request_queue, delivery_spans=delivery_spans)
            except ValueError as exc:
                raise AsrRequestFailure(
                    "ASR_GEMINI_INVALID_RESPONSE",
                    "Gemini ASR returned an invalid transcript",
                    kind="invalid_response",
                ) from exc
            except Exception as exc:
                raise AsrRequestFailure(
                    "ASR_GEMINI_REQUEST_FAILED",
                    "Gemini ASR request failed",
                ) from exc
            transcript = await request_gemini_transcript(
                client,
                wav_audio,
                single_attempt=bool(delivery_spans),
                on_sent=lambda: complete_transport_write(
                    evidence, len(pcm16), generation=key[0],
                    buffer_epoch=key[1], provider="gemini",
                    delivery_spans=delivery_spans, takes_ownership=True,
                ),
            )
        except AsrRequestFailure as exc:
            failure = exc
        finally:
            retire_interval_deliveries(request_queue, only_spans=delivery_spans)

        if failure is not None:
            if _is_current(key) and inflight.get(key) is asyncio.current_task():
                await response_queue.put(
                    _AsrWorkerEvent(
                        kind="error",
                        generation=key[0],
                        buffer_epoch=key[1],
                        utterance_id=key[2],
                        error_code=failure.code,
                        error_message=failure.message,
                    )
                )
            return

        if _is_current(key) and inflight.get(key) is asyncio.current_task():
            await response_queue.put(
                _AsrWorkerEvent(
                    kind="final",
                    generation=key[0],
                    buffer_epoch=key[1],
                    utterance_id=key[2],
                    text=transcript,
                )
            )

    def _discard_finished(task: asyncio.Task[None], key: _UtteranceKey) -> None:
        if inflight.get(key) is task:
            inflight.pop(key, None)

    try:
        if not api_key:
            await response_queue.put(
                _AsrWorkerEvent(
                    kind="error",
                    generation=active_generation,
                    error_code="ASR_CREDENTIALS_MISSING",
                    error_message="Gemini ASR credentials are missing",
                )
            )
            return
        if config.endpointing_mode != "manual":
            await response_queue.put(
                _AsrWorkerEvent(
                    kind="error",
                    generation=active_generation,
                    error_code="ASR_ENDPOINTING_NOT_SUPPORTED",
                    error_message="Gemini segmented ASR requires manual endpointing",
                )
            )
            return
        if client is None:
            try:
                client = _create_gemini_client(api_key)
            except Exception:
                await response_queue.put(
                    _AsrWorkerEvent(
                        kind="error",
                        generation=active_generation,
                        error_code="ASR_GEMINI_SDK_UNAVAILABLE",
                        error_message="Gemini ASR client is unavailable",
                    )
                )
                return

        await response_queue.put(
            _AsrWorkerEvent(kind="ready", generation=active_generation)
        )

        while True:
            request = await request_queue.get()
            try:
                last_utterance_id = request.utterance_id
                cursor = (request.generation, request.buffer_epoch)

                if request.kind == "clear":
                    active_generation, active_buffer_epoch = cursor
                    buffers.clear()
                    committed.clear()
                    await _cancel_inflight()
                    continue

                if request.kind == "shutdown":
                    active_generation, active_buffer_epoch = cursor
                    buffers.clear()
                    committed.clear()
                    await _cancel_inflight()
                    await response_queue.put(
                        _AsrWorkerEvent(
                            kind="closed",
                            generation=request.generation,
                            buffer_epoch=request.buffer_epoch,
                            utterance_id=request.utterance_id,
                        )
                    )
                    closed_sent = True
                    return

                if cursor != (active_generation, active_buffer_epoch):
                    continue
                if request.utterance_id is None:
                    await response_queue.put(
                        _AsrWorkerEvent(
                            kind="error",
                            generation=request.generation,
                            buffer_epoch=request.buffer_epoch,
                            error_code="ASR_GEMINI_PROTOCOL_ERROR",
                            error_message="Gemini ASR request has no utterance identifier",
                        )
                    )
                    continue

                key = (
                    request.generation,
                    request.buffer_epoch,
                    request.utterance_id,
                )
                if request.kind == "audio":
                    if key in committed or key in inflight:
                        continue
                    if len(request.audio) % 2:
                        await response_queue.put(
                            _AsrWorkerEvent(
                                kind="error",
                                generation=key[0],
                                buffer_epoch=key[1],
                                utterance_id=key[2],
                                error_code="ASR_GEMINI_PROTOCOL_ERROR",
                                error_message="Gemini ASR received invalid PCM16 audio",
                            )
                        )
                        continue
                    buffer = buffers.setdefault(key, bytearray())
                    buffer.extend(request.audio)
                    if len(buffer) > MAX_SEGMENT_PCM_BYTES:
                        buffers.pop(key, None)
                        committed.add(key)
                        await response_queue.put(
                            _AsrWorkerEvent(
                                kind="error",
                                generation=key[0],
                                buffer_epoch=key[1],
                                utterance_id=key[2],
                                error_code="ASR_GEMINI_AUDIO_TOO_LONG",
                                error_message=(
                                    "Gemini utterance exceeds the 28 second limit"
                                ),
                            )
                        )
                    continue

                if request.kind == "commit":
                    if key in committed or key in inflight:
                        continue
                    pcm16 = bytes(buffers.pop(key, b""))
                    if not pcm16:
                        continue
                    committed.add(key)
                    task = asyncio.create_task(
                        _transcribe(key, pcm16),
                        name=f"gemini-asr-{key[0]}-{key[1]}-{key[2]}",
                    )
                    inflight[key] = task
                    task.add_done_callback(
                        lambda done, utterance_key=key: _discard_finished(
                            done, utterance_key
                        )
                    )
                    continue

                await response_queue.put(
                    _AsrWorkerEvent(
                        kind="error",
                        generation=key[0],
                        buffer_epoch=key[1],
                        utterance_id=key[2],
                        error_code="ASR_GEMINI_PROTOCOL_ERROR",
                        error_message="Gemini ASR received an unsupported command",
                    )
                )
            finally:
                request_queue.task_done()
    except asyncio.CancelledError:
        raise
    finally:
        buffers.clear()
        committed.clear()
        await _cancel_inflight()
        if owns_client and client is not None:
            close = getattr(getattr(client, "aio", None), "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception:
                    pass
        if not closed_sent:
            await response_queue.put(
                _AsrWorkerEvent(
                    kind="closed",
                    generation=active_generation,
                    buffer_epoch=active_buffer_epoch,
                    utterance_id=last_utterance_id,
                )
            )
