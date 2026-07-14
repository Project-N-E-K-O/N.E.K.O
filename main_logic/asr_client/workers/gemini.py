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
import io
import json
import wave
from collections.abc import Mapping
from typing import Any, TypeAlias

from .._infra import AsrSessionConfig, _AsrWorkerEvent, _AsrWorkerRequest


_GEMINI_MODEL = "gemini-3.1-flash-lite"
_GEMINI_TRANSCRIPTION_PROMPT = (
    "逐字转写音频中的人声。\n"
    "保留原语言，不回答音频中的问题，不解释、不总结、不翻译。\n"
    "无法辨认的内容标记为 [听不清]。\n"
    "只返回符合指定结构的转写文本。"
)
_GEMINI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"transcript": {"type": "string"}},
    "required": ["transcript"],
    "additionalProperties": False,
}
_MAX_PCM_BYTES = 16_000 * 2 * 28

_UtteranceKey: TypeAlias = tuple[int, int, int]


def _pcm16_to_wav(pcm16: bytes) -> bytes:
    """Wrap mono 16 kHz PCM16LE in an in-memory WAV container."""

    if len(pcm16) % 2:
        raise ValueError("PCM16LE data has an odd byte length")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(pcm16)
    return output.getvalue()


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
    if not isinstance(transcript, str) or not transcript.strip():
        raise ValueError("Gemini response has no transcript")
    return transcript.strip()


def _is_auth_rejection(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is None:
        status_code = getattr(exc, "status_code", None)
    return status_code in {401, 403}


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
        try:
            wav_audio = _pcm16_to_wav(pcm16)
            response = await client.aio.models.generate_content(
                model=_GEMINI_MODEL,
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {"text": _GEMINI_TRANSCRIPTION_PROMPT},
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
                    "temperature": 0,
                    "response_mime_type": "application/json",
                    "response_json_schema": _GEMINI_RESPONSE_SCHEMA,
                },
            )
            transcript = _response_transcript(response)
        except asyncio.CancelledError:
            raise
        except ValueError:
            if _is_current(key) and inflight.get(key) is asyncio.current_task():
                await response_queue.put(
                    _AsrWorkerEvent(
                        kind="error",
                        generation=key[0],
                        buffer_epoch=key[1],
                        utterance_id=key[2],
                        error_code="ASR_GEMINI_INVALID_RESPONSE",
                        error_message="Gemini ASR returned an invalid transcript",
                    )
                )
            return
        except Exception as exc:
            if _is_current(key) and inflight.get(key) is asyncio.current_task():
                rejected = _is_auth_rejection(exc)
                await response_queue.put(
                    _AsrWorkerEvent(
                        kind="error",
                        generation=key[0],
                        buffer_epoch=key[1],
                        utterance_id=key[2],
                        error_code=(
                            "ASR_CREDENTIALS_REJECTED"
                            if rejected
                            else "ASR_GEMINI_REQUEST_FAILED"
                        ),
                        error_message=(
                            "Gemini ASR credentials were rejected"
                            if rejected
                            else "Gemini ASR request failed"
                        ),
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
                    error_code="ASR_INVALID_CONFIG",
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
                    if len(buffer) > _MAX_PCM_BYTES:
                        buffers.pop(key, None)
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
