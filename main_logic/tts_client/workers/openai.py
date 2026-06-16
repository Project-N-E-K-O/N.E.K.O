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

import numpy as np

from .._infra import TTS_SHUTDOWN_SENTINEL, _resample_audio, _run_sentence_tts_worker
from .._telemetry import _record_tts_telemetry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

def openai_tts_worker(request_queue, response_queue, audio_api_key, voice_id):
    """OpenAI TTS worker — per-sentence synthesis, streaming audio reception."""
    try:
        from openai import AsyncOpenAI
    except ImportError:
        logger.error("❌ 无法导入 openai 库，OpenAI TTS 不可用")
        response_queue.put(("__ready__", False))
        while True:
            try:
                sid, _ = request_queue.get()
                if sid == TTS_SHUTDOWN_SENTINEL:
                    break
            except Exception:
                break
        return

    if not voice_id:
        voice_id = "marin"

    async def setup(response_queue):
        client = AsyncOpenAI(api_key=audio_api_key)

        async def synthesize(text: str, speech_id: str) -> None:
            async with client.audio.speech.with_streaming_response.create(
                model="gpt-4o-mini-tts",
                voice=voice_id,
                input=text,
                response_format="pcm",
            ) as response:
                _record_tts_telemetry("gpt-4o-mini-tts", len(text))
                async for chunk in response.iter_bytes(chunk_size=4096):
                    if chunk:
                        audio_array = np.frombuffer(chunk, dtype=np.int16)
                        response_queue.put(_resample_audio(audio_array, 24000, 48000))

        return synthesize, None

    _run_sentence_tts_worker(request_queue, response_queue, setup, label="OpenAI TTS")
