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

"""OpenAI TTS worker."""

from functools import partial

import httpx
import numpy as np

from .._infra import _resample_audio, _run_sentence_tts_worker
from .._telemetry import _record_tts_telemetry
from utils.config_manager import _as_bool
from utils.logger_config import get_module_logger
from utils.openai_tts import (
    OPENAI_TTS_DEFAULT_BASE_URL,
    OPENAI_TTS_DEFAULT_MODEL,
    OPENAI_TTS_DEFAULT_VOICE,
    build_openai_tts_payload,
    openai_tts_headers,
    openai_tts_speech_url,
)

logger = get_module_logger(__name__, "Main")


def openai_tts_worker(
    request_queue,
    response_queue,
    audio_api_key,
    voice_id,
    *,
    base_url=OPENAI_TTS_DEFAULT_BASE_URL,
    model=OPENAI_TTS_DEFAULT_MODEL,
    voice=OPENAI_TTS_DEFAULT_VOICE,
):
    """OpenAI-compatible TTS: sentence input with a streamed PCM response."""

    effective_model = str(model or "").strip() or OPENAI_TTS_DEFAULT_MODEL
    effective_voice = str(voice_id or "").strip() or str(voice or "").strip()

    async def setup(response_queue):
        # Validate the URL inside setup so the shared worker skeleton reports
        # configuration failures through its normal __ready__ channel.
        endpoint = openai_tts_speech_url(base_url)
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=90.0))

        async def synthesize(text: str, speech_id: str) -> None:
            payload = build_openai_tts_payload(text, effective_model, effective_voice)
            async with client.stream(
                "POST",
                endpoint,
                headers=openai_tts_headers(audio_api_key),
                json=payload,
            ) as response:
                response.raise_for_status()
                _record_tts_telemetry(effective_model, len(text))
                pending = b""
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    if chunk:
                        pcm = pending + chunk
                        even_length = len(pcm) - (len(pcm) % 2)
                        pending = pcm[even_length:]
                        if not even_length:
                            continue
                        audio_array = np.frombuffer(pcm[:even_length], dtype="<i2")
                        response_queue.put(_resample_audio(audio_array, 24000, 48000))

        return synthesize, client.aclose

    _run_sentence_tts_worker(request_queue, response_queue, setup, label="OpenAI TTS")


def _custom_openai_tts_is_selected(ctx) -> bool:
    """Whether the user selected the custom OpenAI-compatible TTS endpoint."""

    if not _as_bool(ctx.core_config.get("ENABLE_CUSTOM_API"), False):
        return False
    try:
        raw = ctx.cm.load_json_config("core_config.json", {}) or {}
    except Exception:
        raw = {}
    if str(raw.get("ttsModelProvider") or "").strip() != "custom":
        return False

    configured_voice = str(raw.get("ttsVoiceId") or "").strip()
    # A saved clone/design voice owns its provider route. The custom endpoint is
    # the configured fallback, not a blanket override of existing voice vendors.
    if ctx.voice_id and ctx.has_custom_voice and ctx.voice_meta:
        return False
    if ctx.voice_id and configured_voice and str(ctx.voice_id).strip() != configured_voice:
        return False
    return bool(
        str(raw.get("ttsModelUrl") or "").strip()
        and str(raw.get("ttsModelId") or "").strip()
        and configured_voice
    )


def _custom_openai_tts_resolve(ctx):
    try:
        raw = ctx.cm.load_json_config("core_config.json", {}) or {}
    except Exception:
        raw = {}
    worker = partial(
        openai_tts_worker,
        base_url=str(raw.get("ttsModelUrl") or "").strip(),
        model=str(raw.get("ttsModelId") or "").strip(),
        voice=str(raw.get("ttsVoiceId") or "").strip(),
    )
    return worker, str(raw.get("ttsModelApiKey") or "").strip(), "custom"
