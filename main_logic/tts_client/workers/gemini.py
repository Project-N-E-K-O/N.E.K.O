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

"""Gemini TTS worker."""

import numpy as np
import time
import base64

from utils.tts.native_voice_registry import make_native_tts_resolver, register_tts_worker_resolver
from utils.tts.providers.gemini import GEMINI_TTS_MODEL, normalize_gemini_tts_voice

from .._infra import _resample_audio, _run_sentence_tts_worker
from .._telemetry import _record_tts_telemetry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

def gemini_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """Gemini TTS worker — per-sentence synthesis, direct async httpx connection."""
    import httpx

    requested_voice_id = (voice_id or "").strip()
    voice_id, voice_recognized = normalize_gemini_tts_voice(voice_id)
    if requested_voice_id and not voice_recognized:
        logger.warning(
            "Gemini TTS voice '%s' is not in the supported catalog; falling back to '%s'",
            requested_voice_id,
            voice_id,
        )

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/"
        f"models/{GEMINI_TTS_MODEL}:generateContent?key={audio_api_key}"
    )
    TTS_TIMEOUT = 12
    MAX_RETRIES = 3

    async def setup(response_queue):
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(TTS_TIMEOUT + 2, connect=10),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

        # TLS 连接预热
        try:
            logger.info("Gemini TTS TLS 预热中...")
            await client.get(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_TTS_MODEL}",
                params={"key": audio_api_key},
                timeout=10,
            )
            logger.info("Gemini TTS TLS 预热完成")
        except Exception as e:
            logger.warning(f"Gemini TTS TLS 预热失败（不影响后续使用）: {e}")

        async def synthesize(text: str, speech_id: str) -> None:
            wrapped = (
                "Say the text with a proper tone, "
                f"don't omit or add any words:\n\"{text}\""
            )
            payload = {
                "contents": [{"parts": [{"text": wrapped}]}],
                "generationConfig": {
                    "response_modalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {"voiceName": voice_id}
                        }
                    },
                },
            }
            audio_data = None
            for attempt in range(1, MAX_RETRIES + 1):
                t0 = time.time()
                try:
                    r = await client.post(url, json=payload, timeout=TTS_TIMEOUT)
                    r.raise_for_status()
                    data = r.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            inline = parts[0].get("inlineData", {})
                            audio_b64 = inline.get("data")
                            if audio_b64:
                                audio_data = base64.b64decode(audio_b64)
                    dt = time.time() - t0
                    if audio_data:
                        logger.info(
                            f"Gemini TTS API 返回: {len(audio_data)}B, "
                            f"{dt:.1f}s (attempt {attempt})"
                        )
                    break
                except Exception as e:
                    dt = time.time() - t0
                    logger.warning(
                        f"Gemini TTS attempt {attempt}/{MAX_RETRIES} "
                        f"失败 ({dt:.1f}s): {e}"
                    )
                    if attempt == MAX_RETRIES:
                        raise

            if audio_data:
                _record_tts_telemetry("gemini", len(text))
                audio_array = np.frombuffer(audio_data, dtype=np.int16)
                resampled_bytes = _resample_audio(audio_array, 24000, 48000)
                response_queue.put(resampled_bytes)

        return synthesize, client.aclose

    _run_sentence_tts_worker(request_queue, response_queue, setup, label="Gemini TTS")

# Gemini 内置音色和 realtime/LLM endpoint 共用 CORE_API_KEY，不走自定义 TTS slot。
register_tts_worker_resolver(
    'gemini',
    make_native_tts_resolver(gemini_tts_worker, 'core_api_key'),
)
