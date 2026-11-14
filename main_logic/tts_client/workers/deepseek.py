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

"""Bidirectional WebSocket worker for DeepSeek's hosted preset voices."""

import asyncio
import json
from functools import partial
from urllib.parse import urlencode

import numpy as np
import regex
import soxr
import websockets
from websockets.exceptions import InvalidStatus

from utils.logger_config import get_module_logger
from utils.tts.provider_registry import DispatchContext
from utils.tts.providers.deepseek import (
    DEEPSEEK_PRESET_CATALOG,
    DEEPSEEK_TTS_URL,
    normalize_deepseek_tts_voice,
)

from .._infra import (
    AudioDoneEmitter,
    TTS_SHUTDOWN_SENTINEL,
    _enqueue_error,
    _resample_audio,
    make_audio_jitter_buffer,
)
from .._telemetry import _record_tts_telemetry
from .dummy import dummy_tts_worker

logger = get_module_logger(__name__, "Main")

_MAX_BILLED_CHARACTERS = 10_000
_MAX_TEXT_BYTES = 64 * 1024
_HAN_CHARACTERS = regex.compile(r"[\p{Unified_Ideograph}〇]")
_FINISH_ERROR_CODES = {
    "content_filter": "API_POLICY_VIOLATION",
    "invalid_input": "TTS_CONFIG_INVALID",
    "service_error": "UPSTREAM_SERVER_BUSY",
}
_HTTP_ERROR_CODES = {
    401: "API_KEY_REJECTED",
    403: "API_ACCESS_DENIED",
    429: "API_RATE_LIMIT",
    503: "UPSTREAM_SERVER_BUSY",
}


class _SynthesisError(RuntimeError):
    def __init__(self, message: str, code: str = "TTS_CONNECTION_FAILED"):
        super().__init__(message)
        self.code = code


async def _synthesize_speech(
    text_queue: asyncio.Queue[str | None], response_queue, speech_id, url, headers,
) -> None:
    """Own the connection, audio buffer, and terminal events for one speech."""
    resampler = soxr.ResampleStream(24_000, 48_000, 1, dtype="float32")
    jitter = make_audio_jitter_buffer(response_queue, speech_id=speech_id)
    text_finished = False

    async def send_text(ws):
        nonlocal text_finished
        billed_characters = 0
        text_bytes = 0
        while (text := await text_queue.get()) is not None:
            billed_characters += len(text) + len(_HAN_CHARACTERS.findall(text))
            text_bytes += len(text.encode("utf-8"))
            if billed_characters > _MAX_BILLED_CHARACTERS or text_bytes > _MAX_TEXT_BYTES:
                raise _SynthesisError("DeepSeek TTS session text limit exceeded", "TTS_CONFIG_INVALID")
            await ws.send(json.dumps({"event": "text", "text": text}, ensure_ascii=False))
            _record_tts_telemetry("deepseek", len(text))
        text_finished = True
        await ws.send(json.dumps({"event": "finish_text"}))

    async def receive_audio(ws):
        pending_byte = b""
        async for message in ws:
            if isinstance(message, bytes):
                message = pending_byte + message
                aligned_length = len(message) & ~1
                pending_byte = message[aligned_length:]
                if not aligned_length:
                    continue
                pcm = np.frombuffer(message[:aligned_length], dtype="<i2")
                jitter.append(_resample_audio(pcm, 24_000, 48_000, resampler))
                continue
            event = json.loads(message)
            if not isinstance(event, dict) or event.get("event") != "finish":
                raise _SynthesisError("Unexpected DeepSeek TTS WebSocket message")
            code = str(event.get("code") or "")
            if code != "success":
                raise _SynthesisError(
                    f"DeepSeek TTS finish ({code}): {event.get('message', '')}",
                    _FINISH_ERROR_CODES.get(code, "TTS_CONNECTION_FAILED"),
                )
            if not text_finished:
                raise _SynthesisError("DeepSeek TTS finished before the end of input")
            if pending_byte:
                raise _SynthesisError("DeepSeek TTS returned an incomplete PCM sample")
            return
        raise _SynthesisError("DeepSeek TTS closed before sending a finish event")

    try:
        async with asyncio.timeout(30 * 60):
            async with websockets.connect(
                url, additional_headers=headers, open_timeout=5, close_timeout=0.5,
            ) as ws:
                sender = asyncio.create_task(send_text(ws))
                receiver = asyncio.create_task(receive_audio(ws))
                try:
                    completed, _ = await asyncio.wait((sender, receiver), return_when=asyncio.FIRST_COMPLETED)
                    if receiver not in completed:
                        await sender
                    await receiver
                finally:
                    sender.cancel()
                    receiver.cancel()
                    await asyncio.gather(sender, receiver, return_exceptions=True)
        jitter.append(_resample_audio(np.empty(0, dtype="<i2"), 24_000, 48_000, resampler, last=True))
        jitter.flush()
    except asyncio.CancelledError:
        # Interrupts own their own cancellation channel. Drop buffered PCM and close the socket.
        raise
    except Exception as exc:
        code = "TTS_CONNECTION_FAILED"
        if isinstance(exc, _SynthesisError):
            code = exc.code
        elif isinstance(exc, InvalidStatus):
            code = _HTTP_ERROR_CODES.get(exc.response.status_code, code)
        response_queue.put(("__tts_sentence_failed__", speech_id, ""))
        _enqueue_error(response_queue, {
            "code": code,
            "message": f"DeepSeek TTS: {type(exc).__name__}: {exc}",
        })
    else:
        AudioDoneEmitter(response_queue).emit(speech_id)


def deepseek_tts_worker(request_queue, response_queue, audio_api_key, voice_id, *, default_voice=None):
    """Stream text and 48 kHz PCM through the standard TTS worker queues."""
    voice, recognized = normalize_deepseek_tts_voice(voice_id)
    if not recognized and default_voice:
        voice, _ = normalize_deepseek_tts_voice(default_voice)
    url = f"{DEEPSEEK_TTS_URL}?{urlencode({'voice_id': voice, 'format': 'pcm'})}"
    headers = {"Authorization": f"Bearer {audio_api_key}"}

    async def run():
        loop = asyncio.get_running_loop()
        speech_id = None
        text_queue = None
        pending_speeches = asyncio.Queue()

        async def synthesize_in_order():
            nonlocal text_queue
            while True:
                sid, queued_text = await pending_speeches.get()
                await _synthesize_speech(queued_text, response_queue, sid, url, headers)
                if text_queue is queued_text:
                    text_queue = None

        async def stop_synthesis():
            synthesis.cancel()
            await asyncio.gather(synthesis, return_exceptions=True)

        def finish_text():
            nonlocal text_queue
            if text_queue is not None:
                text_queue.put_nowait(None)
                text_queue = None

        response_queue.put(("__ready__", True))
        synthesis = asyncio.create_task(synthesize_in_order())
        try:
            while True:
                sid, text = await loop.run_in_executor(None, request_queue.get)
                if sid == TTS_SHUTDOWN_SENTINEL:
                    break
                if sid == "__interrupt__":
                    await stop_synthesis()
                    speech_id = text_queue = None
                    pending_speeches = asyncio.Queue()
                    synthesis = asyncio.create_task(synthesize_in_order())
                    continue
                if sid is None:
                    finish_text()
                    continue
                if not text:
                    continue
                if sid != speech_id:
                    # A new speech may be queued before the previous socket sends its last audio.
                    # Serial synthesis preserves that tail while this loop still accepts interrupts.
                    finish_text()
                    speech_id = sid
                    text_queue = asyncio.Queue()
                    pending_speeches.put_nowait((sid, text_queue))
                if text_queue is not None:
                    text_queue.put_nowait(text)
        finally:
            await stop_synthesis()

    asyncio.run(run())


def _deepseek_is_selected(ctx: DispatchContext) -> bool:
    voice_id = ctx.voice_id.strip()
    provider = str(ctx.core_config.get("TTS_PROVIDER") or ctx.core_config.get("ttsProvider") or "").strip().lower()
    voice, recognized = normalize_deepseek_tts_voice(voice_id)
    # A generic gender alias alone could belong to the current core's native voice.
    if provider and provider != "deepseek":
        return False
    if not provider and not (recognized and voice_id.casefold() == voice.casefold()):
        return False
    if voice_id and (ctx.voice_meta or ctx.cm.voice_id_exists_in_any_storage(voice_id)):
        return False
    return not ctx.has_custom_voice or not voice_id or recognized


def _deepseek_resolve(ctx: DispatchContext):
    api_key = ctx.cm.get_tts_api_key("deepseek")
    if not api_key:
        logger.warning("DeepSeek TTS selected without a DeepSeek API key")
        return dummy_tts_worker, None, None
    default_voice = str(ctx.core_config.get("TTS_VOICE_ID") or "").strip()
    worker = deepseek_tts_worker
    if DEEPSEEK_PRESET_CATALOG.is_voice(default_voice):
        worker = partial(deepseek_tts_worker, default_voice=default_voice)
    return worker, api_key, "deepseek"
