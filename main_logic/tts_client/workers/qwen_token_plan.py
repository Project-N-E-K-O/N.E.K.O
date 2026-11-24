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

"""Alibaba Bailian Token Plan TTS worker (qwen-audio-3.0-tts-plus, SSE streaming)."""

from __future__ import annotations

import json
import struct
from functools import partial

import numpy as np
import soxr

from utils.logger_config import get_module_logger
from utils.qwen_token_plan_tts import (
    QWEN_TOKEN_PLAN_TTS_DEFAULT_BASE_URL,
    QWEN_TOKEN_PLAN_TTS_DEFAULT_MODEL,
    QWEN_TOKEN_PLAN_TTS_SAMPLE_RATE,
    QwenTokenPlanTtsError,
    build_qwen_token_plan_tts_payload,
    extract_qwen_token_plan_audio,
    is_qwen_token_plan_url,
    qwen_token_plan_api_headers,
    qwen_token_plan_audio_download_url,
    qwen_token_plan_tts_url,
    select_qwen_token_plan_tts_voice,
)

from .._http_pool import make_tts_http_client
from .._infra import _enqueue_error, _resample_audio, _run_sentence_tts_worker
from .._telemetry import _record_tts_telemetry
from .dummy import dummy_tts_worker

logger = get_module_logger(__name__, "Main")

_PROVIDER_KEY = "qwen_token_plan"
_WAVE_FORMAT_PCM = 0x0001
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE


def _decode_downloaded_audio(data: bytes) -> tuple[bytes, int]:
    """Raw PCM16 mono and its sample rate from a downloaded result file.

    The request asks for raw PCM, but the signed result file may still be a WAV;
    its RIFF header must not be played as samples. Chunks are read directly
    because Python 3.11's ``wave`` rejects WAVE_FORMAT_EXTENSIBLE headers.
    """
    if data[:4] != b"RIFF":
        return data, QWEN_TOKEN_PLAN_TTS_SAMPLE_RATE
    if data[8:12] != b"WAVE":
        raise QwenTokenPlanTtsError("invalid WAV audio")
    fmt = None
    frames = None
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        body = data[pos + 8:pos + 8 + size]
        if chunk_id == b"fmt " and len(body) >= 16:
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif chunk_id == b"data":
            frames = body  # A truncated or streamed data chunk keeps what arrived.
            break
        pos += 8 + size + (size & 1)
    if fmt is None or frames is None:
        raise QwenTokenPlanTtsError("invalid WAV audio")
    format_tag, channels, sample_rate, _byte_rate, _align, bits = fmt
    if format_tag not in (_WAVE_FORMAT_PCM, _WAVE_FORMAT_EXTENSIBLE) or channels < 1 or not sample_rate:
        raise QwenTokenPlanTtsError(f"unsupported WAV format: {format_tag}")
    if bits != 16:
        raise QwenTokenPlanTtsError(f"unsupported WAV sample width: {bits} bit")
    if channels > 1:
        samples = np.frombuffer(frames[: len(frames) - len(frames) % (2 * channels)], dtype="<i2")
        frames = samples.reshape(-1, channels).mean(axis=1).astype("<i2").tobytes()
    return frames, sample_rate


def qwen_token_plan_tts_worker(
    request_queue,
    response_queue,
    audio_api_key,
    voice_id,
    *,
    base_url=None,
    model=None,
    configured_voice=None,
):
    import httpx

    tts_model = (model or QWEN_TOKEN_PLAN_TTS_DEFAULT_MODEL).strip()
    # The character's voice first, then the configured fallback voice. Voices of
    # other providers would be rejected upstream, so they fall back to the default.
    voice, fell_back = select_qwen_token_plan_tts_voice(voice_id, configured_voice)
    if fell_back:
        logger.warning("Token Plan TTS: voice not supported by this model, fell back to the default voice")

    async def setup(response_queue):
        if not audio_api_key:
            _enqueue_error(response_queue, {
                "code": "QWEN_TOKEN_PLAN_TTS_API_KEY_MISSING",
                "provider": _PROVIDER_KEY,
                "message": "阿里百炼 Token Plan API Key 未配置",
            })
            raise RuntimeError("Token Plan TTS API key is not configured")
        try:
            api_url = qwen_token_plan_tts_url(base_url or QWEN_TOKEN_PLAN_TTS_DEFAULT_BASE_URL)
        except ValueError:
            _enqueue_error(response_queue, {
                "code": "QWEN_TOKEN_PLAN_TTS_URL_INVALID",
                "provider": _PROVIDER_KEY,
                "message": "阿里百炼 Token Plan TTS 地址必须是 https 的 Token Plan 域名",
            })
            raise RuntimeError("Token Plan TTS URL is not an HTTPS Token Plan host")

        headers = qwen_token_plan_api_headers(audio_api_key, stream=True)
        client = make_tts_http_client(
            timeout=httpx.Timeout(connect=10, read=60, write=10, pool=10),
        )

        async def synthesize(text: str, speech_id: str) -> None:
            payload = build_qwen_token_plan_tts_payload(text, voice, model=tts_model)
            resampler = soxr.ResampleStream(QWEN_TOKEN_PLAN_TTS_SAMPLE_RATE, 48000, 1, dtype="float32")
            pending_byte = b""
            received_audio = False
            audio_url = ""

            def emit(chunk: bytes) -> None:
                nonlocal pending_byte, received_audio
                if not chunk:
                    return
                chunk = pending_byte + chunk
                aligned = len(chunk) & ~1
                pending_byte = chunk[aligned:]
                if not aligned:
                    return
                received_audio = True
                pcm = np.frombuffer(chunk[:aligned], dtype="<i2")
                response_queue.put(
                    _resample_audio(pcm, QWEN_TOKEN_PLAN_TTS_SAMPLE_RATE, 48000, resampler)
                )

            def handle_event(event: object) -> None:
                nonlocal audio_url
                chunk, url, _finished = extract_qwen_token_plan_audio(event)
                emit(chunk)
                if url:
                    audio_url = url

            try:
                async with client.stream("POST", api_url, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        # Only the documented error fields; never echo the raw body.
                        try:
                            error = json.loads((await resp.aread()).decode("utf-8", errors="ignore"))
                        except ValueError:
                            error = {}
                        if not isinstance(error, dict):
                            error = {}
                        detail = " ".join(
                            str(error.get(field) or "")[:200] for field in ("code", "message")
                        ).strip()
                        _enqueue_error(
                            response_queue,
                            f"阿里百炼 Token Plan TTS API错误 ({resp.status_code}) {detail}".rstrip(),
                        )
                        return

                    content_type = resp.headers.get("content-type", "").lower()
                    if "text/event-stream" not in content_type:
                        handle_event(json.loads((await resp.aread()).decode("utf-8")))
                    else:
                        buffer = ""
                        data_lines: list[str] = []

                        def dispatch_frame() -> None:
                            # One SSE event: its ``data:`` lines joined by newlines.
                            data = "\n".join(data_lines).strip()
                            data_lines.clear()
                            if data and data != "[DONE]":
                                handle_event(json.loads(data))

                        async for raw_chunk in resp.aiter_text():
                            buffer += raw_chunk
                            while "\n" in buffer:
                                line, buffer = buffer.split("\n", 1)
                                line = line.rstrip("\r")
                                if not line:
                                    dispatch_frame()
                                elif line.startswith("data:"):
                                    data_lines.append(line[5:].lstrip(" "))
                                # ``id:`` / ``event:`` fields and ``:`` comments carry no audio.
                        if buffer.rstrip("\r").startswith("data:"):
                            data_lines.append(buffer.rstrip("\r")[5:].lstrip(" "))
                        dispatch_frame()

                # The last SSE event repeats the whole sentence as a signed URL; only
                # fetch it when no streamed audio arrived (a non-streaming reply).
                if not received_audio and audio_url:
                    download_url = qwen_token_plan_audio_download_url(audio_url)
                    if not download_url:
                        raise QwenTokenPlanTtsError("audio URL is not HTTPS")
                    # Signed URL: never attach the API key.
                    audio_resp = await client.get(download_url)
                    if audio_resp.status_code != 200:
                        raise QwenTokenPlanTtsError(f"audio download HTTP {audio_resp.status_code}")
                    audio, sample_rate = _decode_downloaded_audio(audio_resp.content)
                    if sample_rate == QWEN_TOKEN_PLAN_TTS_SAMPLE_RATE:
                        emit(audio)
                    elif len(audio) >= 2:
                        received_audio = True
                        pcm = np.frombuffer(audio[: len(audio) & ~1], dtype="<i2")
                        response_queue.put(_resample_audio(pcm, sample_rate, 48000))
                if not received_audio:
                    _enqueue_error(response_queue, "阿里百炼 Token Plan TTS 响应未包含音频")
                    return
                # Counted once audio actually arrived, like Doubao Speech.
                _record_tts_telemetry(tts_model, len(text))
                # Flush the resampler tail so the end of the sentence is not cut off.
                tail = _resample_audio(
                    np.empty(0, dtype="<i2"), QWEN_TOKEN_PLAN_TTS_SAMPLE_RATE, 48000, resampler, last=True,
                )
                if tail:
                    response_queue.put(tail)
            except json.JSONDecodeError:
                _enqueue_error(response_queue, "阿里百炼 Token Plan TTS 响应 JSON 解析失败")
            except QwenTokenPlanTtsError as exc:
                _enqueue_error(response_queue, f"阿里百炼 Token Plan TTS 返回错误: {exc}")
            except Exception as exc:
                _enqueue_error(response_queue, f"阿里百炼 Token Plan TTS 请求失败: {exc}")

        return synthesize, client.aclose

    _run_sentence_tts_worker(request_queue, response_queue, setup, label="Token Plan TTS")


def _qwen_token_plan_has_foreign_custom_voice(ctx) -> bool:
    # Same rule as MiMo: a character using another provider's custom voice (e.g. a
    # Doubao or GLM clone, ranked after this provider) keeps that provider.
    if not (ctx.has_custom_voice and ctx.voice_id):
        return False
    provider = str((ctx.voice_meta or {}).get("provider") or "").strip().lower()
    return bool(provider and provider != _PROVIDER_KEY)


def _qwen_token_plan_is_selected(ctx) -> bool:
    cc = ctx.core_config
    tts_provider = str(cc.get("TTS_PROVIDER") or cc.get("ttsProvider") or "").strip().lower()
    if tts_provider != _PROVIDER_KEY:
        try:
            raw = ctx.cm.load_json_config("core_config.json", {})
        except Exception:
            raw = {}
        if str(raw.get("ttsModelProvider") or "").strip().lower() != _PROVIDER_KEY:
            return False
    return not _qwen_token_plan_has_foreign_custom_voice(ctx)


def _qwen_token_plan_resolve(ctx):
    try:
        raw = ctx.cm.load_json_config("core_config.json", {})
    except Exception:
        raw = {}
    api_key = (ctx.cm.get_tts_api_key(_PROVIDER_KEY) or "").strip()
    if "***" in api_key:
        api_key = ""
    if not api_key:
        logger.warning("Token Plan TTS 已选中但 API Key 缺失，改用 dummy TTS worker")
        return dummy_tts_worker, None, None

    base_url = str(raw.get("ttsModelUrl") or "").strip() or QWEN_TOKEN_PLAN_TTS_DEFAULT_BASE_URL
    if not is_qwen_token_plan_url(base_url):
        # Never send a Token Plan key to a host outside the whitelist or over plain HTTP.
        # A URL left over from another provider is ignored rather than silencing TTS.
        logger.warning("Token Plan TTS 地址不是 https 的 Token Plan 域名，改用默认地址")
        base_url = QWEN_TOKEN_PLAN_TTS_DEFAULT_BASE_URL
    worker = partial(
        qwen_token_plan_tts_worker,
        base_url=base_url,
        model=str(raw.get("ttsModelId") or "").strip() or QWEN_TOKEN_PLAN_TTS_DEFAULT_MODEL,
        configured_voice=str(raw.get("ttsVoiceId") or "").strip(),
    )
    return worker, api_key, _PROVIDER_KEY
