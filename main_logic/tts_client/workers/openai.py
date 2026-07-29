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
from inspect import signature

import numpy as np
import soxr

from .._infra import TTS_SHUTDOWN_SENTINEL, _resample_audio, _run_sentence_tts_worker
from .._telemetry import _record_tts_telemetry
from utils.config_manager import _as_bool
from utils.logger_config import get_module_logger
from utils.openai_tts import (
    OPENAI_TTS_DEFAULT_BASE_URL,
    OPENAI_TTS_DEFAULT_MODEL,
    OPENAI_TTS_DEFAULT_VOICE,
    OPENAI_TTS_PCM_SAMPLE_RATE,
    openai_tts_base_url,
    openai_tts_extra_body,
    openai_tts_sdk_options,
)

logger = get_module_logger(__name__, "Main")


def _openai_auth_client_kwargs(client_type, audio_api_key):
    """Build SDK credentials for authenticated and auth-free endpoints."""
    client_kwargs = {"api_key": audio_api_key or ""}
    if audio_api_key:
        return client_kwargs

    # OpenAI SDK 2.34+ 默认拒绝空 key，但提供免凭证兼容开关；旧版没有该参数。
    # 仅在当前 SDK 明确支持时关闭初始化校验，请求层仍显式删除 Authorization。
    try:
        supports_auth_free = "_enforce_credentials" in signature(client_type).parameters
    except (TypeError, ValueError):
        supports_auth_free = False
    if supports_auth_free:
        client_kwargs["_enforce_credentials"] = False
    return client_kwargs


def openai_tts_worker(
    request_queue,
    response_queue,
    audio_api_key,
    voice_id,
    *,
    base_url=None,
    model=OPENAI_TTS_DEFAULT_MODEL,
    voice=OPENAI_TTS_DEFAULT_VOICE,
):
    """OpenAI-compatible TTS: sentence input with a streamed PCM response."""

    # 纯空白 key 等同未填写，避免误发 ``Bearer <空白>``。
    audio_api_key = str(audio_api_key or "").strip()

    try:
        from openai import AsyncOpenAI, omit as openai_omit
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

    effective_model = str(model or "").strip() or OPENAI_TTS_DEFAULT_MODEL
    effective_voice = str(voice_id or "").strip() or str(voice or "").strip()

    async def setup(response_queue):
        client_kwargs = _openai_auth_client_kwargs(AsyncOpenAI, audio_api_key)
        if base_url:
            # Validate/normalize inside setup so configuration failures travel
            # through the shared worker skeleton's normal __ready__ channel.
            sdk_base_url, default_query = openai_tts_sdk_options(base_url)
            client_kwargs["base_url"] = sdk_base_url
            if default_query:
                client_kwargs["default_query"] = default_query
        client = AsyncOpenAI(**client_kwargs)
        extra_body = openai_tts_extra_body(base_url or OPENAI_TTS_DEFAULT_BASE_URL)

        async def synthesize(text: str, speech_id: str) -> None:
            request_kwargs = {
                "model": effective_model,
                "voice": effective_voice,
                "input": text,
                "response_format": "pcm",
            }
            if extra_body:
                request_kwargs["extra_body"] = extra_body
            if not audio_api_key:
                # 新版 SDK 还会在发请求前检查鉴权方式；Omit 保证免鉴权服务
                # 不会收到占位或空白 Bearer 头。
                request_kwargs["extra_headers"] = {
                    "Authorization": openai_omit,
                }
            async with client.audio.speech.with_streaming_response.create(
                **request_kwargs,
            ) as response:
                _record_tts_telemetry(effective_model, len(text))
                pending = b""
                received_samples = False
                resampler = soxr.ResampleStream(
                    OPENAI_TTS_PCM_SAMPLE_RATE,
                    48000,
                    1,
                    dtype="float32",
                )
                async for chunk in response.iter_bytes(chunk_size=4096):
                    if chunk:
                        pcm = pending + chunk
                        even_length = len(pcm) - (len(pcm) % 2)
                        pending = pcm[even_length:]
                        if not even_length:
                            continue
                        audio_array = np.frombuffer(pcm[:even_length], dtype="<i2")
                        received_samples = True
                        resampled = _resample_audio(
                            audio_array,
                            OPENAI_TTS_PCM_SAMPLE_RATE,
                            48000,
                            resampler,
                        )
                        if len(resampled):
                            response_queue.put(resampled)

                if pending:
                    raise RuntimeError("OpenAI TTS returned a truncated PCM sample")
                if not received_samples:
                    raise RuntimeError("OpenAI TTS returned an empty PCM response")

                tail = _resample_audio(
                    np.empty(0, dtype=np.int16),
                    OPENAI_TTS_PCM_SAMPLE_RATE,
                    48000,
                    resampler,
                    last=True,
                )
                if len(tail):
                    response_queue.put(tail)

        return synthesize, client.close

    _run_sentence_tts_worker(request_queue, response_queue, setup, label="OpenAI TTS")


def _custom_openai_tts_is_selected(ctx) -> bool:
    """Whether the user selected the custom OpenAI-compatible TTS endpoint."""

    if not _as_bool(ctx.core_config.get("ENABLE_CUSTOM_API"), False):
        return False
    if str(ctx.core_config.get("ttsModelProvider") or "").strip() != "custom":
        return False

    configured_voice = str(ctx.core_config.get("ttsVoiceId") or "").strip()
    # A saved clone/design voice owns its provider route. The custom endpoint is
    # the configured fallback, not a blanket override of existing voice vendors.
    if ctx.voice_id and ctx.has_custom_voice and ctx.voice_meta:
        return False
    if ctx.voice_id and configured_voice and str(ctx.voice_id).strip() != configured_voice:
        return False
    effective_voice = str(ctx.voice_id or "").strip() or configured_voice
    return bool(
        str(ctx.core_config.get("ttsModelUrl") or "").strip()
        and str(ctx.core_config.get("ttsModelId") or "").strip()
        and effective_voice
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
