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

from __future__ import annotations

import io
import wave
from functools import partial

import numpy as np
import soxr

from utils.doubao_tts import (
    DOUBAO_TTS_DEFAULT_BASE_URL,
    DOUBAO_TTS_DEFAULT_CONTEXT_TEXTS,
    DOUBAO_TTS_DEFAULT_RESOURCE_ID,
    DOUBAO_TTS_DEFAULT_SAMPLE_RATE,
    DoubaoTtsError,
    build_doubao_tts_payload,
    doubao_api_headers,
    doubao_tts_url,
    extract_doubao_audio_bytes,
)
from utils.logger_config import get_module_logger

from .._infra import _enqueue_error, _resample_audio, _run_sentence_tts_worker
from .._telemetry import _record_tts_telemetry
from .dummy import dummy_tts_worker

logger = get_module_logger(__name__, "Main")


def _decode_doubao_audio(audio_bytes: bytes) -> tuple[np.ndarray, int]:
    if audio_bytes[:4] == b"RIFF":
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
            sample_rate = wav.getframerate()
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            frames = wav.readframes(wav.getnframes())
        if sample_width != 2:
            raise DoubaoTtsError(f"豆包 TTS 返回了不支持的 WAV 位宽: {sample_width * 8}bit")
        audio = np.frombuffer(frames, dtype=np.int16)
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1).astype(np.int16)
        return audio, sample_rate
    usable_len = len(audio_bytes) - (len(audio_bytes) % 2)
    if usable_len <= 0:
        raise DoubaoTtsError("豆包 TTS 返回了空音频")
    return np.frombuffer(audio_bytes[:usable_len], dtype=np.int16), DOUBAO_TTS_DEFAULT_SAMPLE_RATE


def doubao_tts_worker(
    request_queue,
    response_queue,
    audio_api_key,
    voice_id,
    *,
    base_url=None,
    resource_id=None,
    configured_voice=None,
    context_texts=None,
):
    import httpx

    resource = (resource_id or DOUBAO_TTS_DEFAULT_RESOURCE_ID).strip()
    api_url = doubao_tts_url(base_url or DOUBAO_TTS_DEFAULT_BASE_URL)
    voice = (configured_voice or voice_id or "").strip()
    context = tuple(context_texts or (DOUBAO_TTS_DEFAULT_CONTEXT_TEXTS,))

    async def setup(response_queue):
        if not audio_api_key:
            _enqueue_error(response_queue, {
                "code": "DOUBAO_TTS_API_KEY_MISSING",
                "provider": "doubao_tts",
                "message": "豆包语音 API Key 未配置",
            })
            raise RuntimeError("Doubao TTS API key is not configured")
        if not voice:
            _enqueue_error(response_queue, {
                "code": "DOUBAO_TTS_VOICE_MISSING",
                "provider": "doubao_tts",
                "message": "豆包语音 Voice ID 未配置",
            })
            raise RuntimeError("Doubao TTS voice id is not configured")

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=60, write=10, pool=10),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

        async def synthesize(text: str, speech_id: str) -> None:
            payload = build_doubao_tts_payload(text, voice, context_texts=context)
            headers = doubao_api_headers(audio_api_key, resource)
            resampler = soxr.ResampleStream(24000, 48000, 1, dtype="float32")
            try:
                resp = await client.post(api_url, headers=headers, json=payload)
                if resp.status_code != 200:
                    _enqueue_error(
                        response_queue,
                        f"豆包 TTS API错误 ({resp.status_code}): {resp.text[:300]}",
                    )
                    return
                audio_bytes = extract_doubao_audio_bytes(await resp.aread())
                if not audio_bytes:
                    _enqueue_error(response_queue, "豆包 TTS 响应未包含音频")
                    return
                audio, sample_rate = _decode_doubao_audio(audio_bytes)
                response_queue.put(_resample_audio(audio, sample_rate, 48000, resampler))
                _record_tts_telemetry(resource, len(text))
            except Exception as exc:
                _enqueue_error(response_queue, f"豆包 TTS 请求失败: {exc}")

        return synthesize, client.aclose

    _run_sentence_tts_worker(request_queue, response_queue, setup, label="Doubao TTS")


def _doubao_voice_meta_is_clone(vm) -> bool:
    return bool(vm and vm.get("provider") == "doubao_tts")


def _doubao_is_selected(ctx) -> bool:
    cc = ctx.core_config
    tts_provider = str(cc.get("TTS_PROVIDER") or cc.get("ttsProvider") or "").strip().lower()
    if tts_provider == "doubao_tts":
        return True
    try:
        raw = ctx.cm.load_json_config("core_config.json", {})
    except Exception:
        raw = {}
    if str(raw.get("ttsModelProvider") or "").strip().lower() == "doubao_tts":
        return True
    return _doubao_voice_meta_is_clone(ctx.voice_meta)


def _doubao_resolve(ctx):
    try:
        raw = ctx.cm.load_json_config("core_config.json", {})
    except Exception:
        raw = {}
    vm = ctx.voice_meta or {}
    api_key = (ctx.cm.get_tts_api_key("doubao_tts") or "").strip()
    if "***" in api_key:
        api_key = ""
    if not api_key:
        logger.warning("豆包 TTS 已选中但 API Key 缺失，改用 dummy TTS worker")
        return dummy_tts_worker, None, None

    if _doubao_voice_meta_is_clone(vm):
        base_url = vm.get("doubao_base_url") or DOUBAO_TTS_DEFAULT_BASE_URL
        resource_id = vm.get("doubao_resource_id") or DOUBAO_TTS_DEFAULT_RESOURCE_ID
        configured_voice = ctx.voice_id
    else:
        base_url = raw.get("ttsModelUrl") or DOUBAO_TTS_DEFAULT_BASE_URL
        resource_id = raw.get("ttsModelId") or DOUBAO_TTS_DEFAULT_RESOURCE_ID
        configured_voice = raw.get("ttsVoiceId") or ""
    worker = partial(
        doubao_tts_worker,
        base_url=base_url,
        resource_id=resource_id,
        configured_voice=configured_voice,
    )
    return worker, api_key, "doubao_tts"
