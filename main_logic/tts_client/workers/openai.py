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
    build_openai_tts_payload,
    openai_tts_base_url,
    openai_tts_extra_body,
)

logger = get_module_logger(__name__, "Main")


def openai_tts_worker(
    request_queue,
    response_queue,
    audio_api_key,
    voice_id,
    *,
    base_url=None,
    model=OPENAI_TTS_DEFAULT_MODEL,
    voice=OPENAI_TTS_DEFAULT_VOICE,
    label="OpenAI TTS",
):
    """OpenAI-compatible TTS: sentence input with a streamed PCM response."""

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

    effective_model = str(model or "").strip() or OPENAI_TTS_DEFAULT_MODEL
    effective_voice = str(voice_id or "").strip() or str(voice or "").strip()

    async def setup(response_queue):
        client_kwargs = {
            "api_key": audio_api_key or ("sk-placeholder" if base_url else audio_api_key),
        }
        if base_url:
            # Validate/normalize inside setup so configuration failures travel
            # through the shared worker skeleton's normal __ready__ channel.
            # 配置错误必须走统一的未就绪信号，不能在线程外静默退出。
            client_kwargs["base_url"] = openai_tts_base_url(base_url)
        client = AsyncOpenAI(**client_kwargs)
        extra_body = openai_tts_extra_body(base_url or OPENAI_TTS_DEFAULT_BASE_URL)
        base_payload = build_openai_tts_payload("", effective_model, effective_voice)

        async def synthesize(text: str, speech_id: str) -> None:
            request_kwargs = {**base_payload, "input": text}
            if extra_body:
                request_kwargs["extra_body"] = extra_body
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

    _run_sentence_tts_worker(request_queue, response_queue, setup, label=label)


def _custom_openai_tts_is_selected(ctx) -> bool:
    """Whether the user selected the custom OpenAI-compatible TTS endpoint."""

    if not _as_bool(ctx.core_config.get("ENABLE_CUSTOM_API"), False):
        return False
    if str(ctx.core_config.get("ttsModelProvider") or "").strip() != "custom":
        return False

    configured_voice = str(ctx.core_config.get("ttsVoiceId") or "").strip()
    selected_voice = str(ctx.voice_id or "").strip()
    if selected_voice and configured_voice and selected_voice != configured_voice:
        return False

    # The exact configured voice is an explicit Custom API selection and wins
    # even if an old clone with the same ID still exists in voice storage.
    # 角色选中的音色与配置音色一致时，自定义 API 必须优先于同名历史克隆记录。
    exact_configured_voice = bool(selected_voice and selected_voice == configured_voice)
    if (
        not exact_configured_voice
        and selected_voice
        and ctx.has_custom_voice
        and ctx.voice_meta
    ):
        return False

    effective_voice = selected_voice or configured_voice
    return bool(
        str(ctx.core_config.get("ttsModelUrl") or "").strip()
        and str(ctx.core_config.get("ttsModelId") or "").strip()
        and effective_voice
    )


def _custom_openai_tts_resolve(ctx):
    try:
        raw = ctx.cm.load_json_config("core_config.json", {}) or {}
    except Exception as exc:
        logger.error("无法读取自定义 TTS API 配置，将进入既有保底流程", exc_info=True)
        raise RuntimeError("无法读取自定义 TTS API 配置") from exc

    base_url = str(raw.get("ttsModelUrl") or "").strip()
    model = str(raw.get("ttsModelId") or "").strip()
    configured_voice = str(raw.get("ttsVoiceId") or "").strip()
    effective_voice = str(ctx.voice_id or "").strip() or configured_voice

    # Fail before spawning the worker so registry dispatch can select the next
    # existing provider without changing its established order.
    # 在线程启动前校验配置，失败时让注册表按原顺序选择保底 provider。
    openai_tts_base_url(base_url)
    build_openai_tts_payload("", model, effective_voice)

    worker = partial(
        openai_tts_worker,
        base_url=base_url,
        model=model,
        voice=configured_voice,
        label="自定义 TTS API (Custom OpenAI-compatible TTS)",
    )
    return worker, str(raw.get("ttsModelApiKey") or "").strip(), "custom"
