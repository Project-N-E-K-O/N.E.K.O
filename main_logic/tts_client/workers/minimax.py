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

"""MiniMax TTS worker."""

import numpy as np
import soxr
import json

from functools import partial
from urllib.parse import urlparse, urlunparse

from .._infra import _resample_audio, _enqueue_error, _run_sentence_tts_worker
from .._telemetry import _record_tts_telemetry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

def _get_minimax_tts_http_url(base_url: str | None = None) -> str:
    """Normalize the MiniMax API base URL into the TTS HTTP SSE address."""
    raw_url = (base_url or "https://api.minimaxi.com").strip().rstrip("/")
    # 将 ws/wss 协议转为 http/https
    if raw_url.startswith("ws://"):
        raw_url = "http://" + raw_url[5:]
    elif raw_url.startswith("wss://"):
        raw_url = "https://" + raw_url[6:]
    elif not raw_url.startswith(("http://", "https://")):
        raw_url = "https://" + raw_url

    parsed = urlparse(raw_url)
    if not parsed.netloc:
        raise ValueError(f"无效的 MiniMax base_url: {base_url!r}")
    return urlunparse((parsed.scheme, parsed.netloc, "/v1/t2a_v2", "", "", ""))

async def _minimax_sse_synthesize(
    client, api_url: str, headers: dict, model: str,
    text: str, voice_id: str, speech_id: str,
    response_queue, agg_flush_bytes: int,
):
    """Issue one synthesis request to the MiniMax T2A v2 HTTP SSE endpoint and stream the audio."""
    import binascii

    payload = {
        "model": model,
        "text": text,
        "stream": True,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": 1.0,
            "vol": 1.0,
            "pitch": 0,
        },
        "audio_setting": {
            "sample_rate": 24000,
            "bitrate": 128000,
            "format": "pcm",
            "channel": 1,
        },
        "output_format": "hex",
        "stream_options": {
            "exclude_aggregated_audio": True,
        },
    }

    resampler = None
    audio_chunk_buffer = bytearray()

    def flush_audio(force: bool = False) -> None:
        nonlocal audio_chunk_buffer
        while len(audio_chunk_buffer) >= agg_flush_bytes:
            chunk = bytes(audio_chunk_buffer[:agg_flush_bytes])
            del audio_chunk_buffer[:agg_flush_bytes]
            response_queue.put(("__audio__", speech_id, chunk))
        if force and audio_chunk_buffer:
            response_queue.put(("__audio__", speech_id, bytes(audio_chunk_buffer)))
            audio_chunk_buffer.clear()

    def process_audio_chunk(audio_hex: str) -> None:
        """Process a single audio chunk (hex encoded)"""
        nonlocal resampler
        if not audio_hex:
            return
        try:
            pcm_bytes = binascii.unhexlify(audio_hex)
        except (binascii.Error, ValueError) as exc:
            _enqueue_error(response_queue, f"MiniMax TTS 音频解码失败: {exc}")
            return
        if pcm_bytes:
            audio_array = np.frombuffer(pcm_bytes, dtype=np.int16)
            if resampler is None:
                resampler = soxr.ResampleStream(24000, 48000, 1, dtype="float32")
            audio_chunk_buffer.extend(
                _resample_audio(audio_array, 24000, 48000, resampler)
            )
            flush_audio(force=False)

    def process_event(event: dict) -> bool:
        """Process a single event; returning False means an error occurred and we must stop"""
        base_resp = event.get("base_resp") or {}
        if base_resp.get("status_code", 0) != 0:
            _enqueue_error(
                response_queue,
                f"MiniMax TTS 服务端错误: {base_resp.get('status_msg', '')} (code={base_resp.get('status_code')})",
            )
            return False
        
        data = event.get("data") or {}
        audio_hex = data.get("audio", "")
        process_audio_chunk(audio_hex)
        return True

    try:
        async with client.stream("POST", api_url, json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                error_text = ""
                async for chunk in resp.aiter_text():
                    error_text += chunk
                _enqueue_error(response_queue, f"MiniMax TTS API错误 ({resp.status_code}): {error_text[:300]}")
                return

            _record_tts_telemetry("minimax", len(text))

            content_type = resp.headers.get("content-type", "").lower()

            # SSE 格式: text/event-stream
            if "text/event-stream" in content_type:
                buffer = ""
                async for raw_chunk in resp.aiter_text():
                    buffer += raw_chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue
                        # SSE 格式: "data: {json}"
                        if line.startswith("data:"):
                            json_str = line[5:].strip()
                            if not json_str or json_str == "[DONE]":
                                continue
                            try:
                                event = json.loads(json_str)
                                if not process_event(event):
                                    flush_audio(force=True)
                                    return
                            except json.JSONDecodeError:
                                # 上游响应可能含 TTS 原文，不写 logger
                                logger.warning("MiniMax TTS SSE JSON 解析失败 (len=%d)", len(json_str))
                                print(f"[MiniMax TTS] SSE JSON 解析失败 raw: {json_str[:200]}")
                                continue

                # 处理流结束后 buffer 中可能残留的最后一行（服务端未发尾部换行）
                residual = buffer.strip()
                if residual:
                    if residual.startswith("data:"):
                        json_str = residual[5:].strip()
                        if json_str and json_str != "[DONE]":
                            try:
                                event = json.loads(json_str)
                                process_event(event)
                            except json.JSONDecodeError:
                                logger.warning("MiniMax TTS SSE JSON 解析失败 (残留, len=%d)", len(json_str))
                                print(f"[MiniMax TTS] SSE JSON 解析失败 (残留) raw: {json_str[:200]}")

            # JSON 流格式: application/json (逐行 JSON 对象)
            else:
                buffer = ""
                async for raw_chunk in resp.aiter_text():
                    buffer += raw_chunk
                    # 尝试按行分割 JSON 对象
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if not line:
                            continue

                        # 移除可能的逗号分隔符
                        if line.startswith(","):
                            line = line[1:].strip()
                        if line.endswith(","):
                            line = line[:-1].strip()

                        # 跳过数组开始/结束标记
                        if line in ("[", "]"):
                            continue

                        try:
                            event = json.loads(line)
                            if not process_event(event):
                                flush_audio(force=True)
                                return
                        except json.JSONDecodeError:
                            # 不完整的 JSON 或格式错误，记录警告后跳过；不写原文到 logger
                            logger.warning("MiniMax TTS JSON 解析失败 (len=%d)", len(line))
                            print(f"[MiniMax TTS] JSON 解析失败 raw: {line[:200]}")
                            continue

                # 处理流结束后 buffer 中可能残留的最后一行
                residual = buffer.strip()
                if residual:
                    if residual.startswith(","):
                        residual = residual[1:].strip()
                    if residual.endswith(","):
                        residual = residual[:-1].strip()
                    if residual and residual not in ("[", "]"):
                        try:
                            event = json.loads(residual)
                            process_event(event)
                        except json.JSONDecodeError:
                            logger.warning("MiniMax TTS JSON 解析失败 (残留, len=%d)", len(residual))
                            print(f"[MiniMax TTS] JSON 解析失败 (残留) raw: {residual[:200]}")

            flush_audio(force=True)

    except Exception as exc:
        _enqueue_error(response_queue, f"MiniMax TTS 合成失败: {exc}")
        flush_audio(force=True)

def minimax_tts_worker(request_queue, response_queue, audio_api_key, voice_id, base_url=None):
    """MiniMax TTS worker — per-sentence synthesis, HTTP SSE streaming audio output."""
    import httpx

    async def setup(response_queue):
        api_url = _get_minimax_tts_http_url(base_url)
        headers = {
            "Authorization": f"Bearer {audio_api_key}",
            "Content-Type": "application/json",
        }
        model_name = "speech-2.8-turbo"
        agg_flush_bytes = 4096

        # 连通性探测
        # per-call AsyncClient: 一次性 probe，紧接着下面会构造 per-worker 持久 client
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=10)) as probe:
            probe_resp = await probe.post(
                api_url, headers=headers,
                json={"model": model_name, "text": "", "stream": False,
                      "voice_setting": {"voice_id": voice_id}},
                timeout=10,
            )
            if probe_resp.status_code not in (200, 400):
                error_text = probe_resp.text[:200]
                _enqueue_error(
                    response_queue,
                    f"MiniMax TTS 探测失败 ({probe_resp.status_code}): {error_text}",
                )
                raise RuntimeError("MiniMax TTS 探测失败")

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=None, write=10, pool=10),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

        async def synthesize(text: str, speech_id: str) -> None:
            await _minimax_sse_synthesize(
                client, api_url, headers, model_name,
                text, voice_id, speech_id,
                response_queue, agg_flush_bytes,
            )

        return synthesize, client.aclose

    _run_sentence_tts_worker(request_queue, response_queue, setup, label="MiniMax TTS")

# ── 克隆音色 provider（按 voice_meta.provider 选中，hosted SaaS）─────────────
# 与 vllm/gptsovits 的"配置选中"不同，这三家靠用户所选克隆音色的 voice_meta 路由；
# is_selected 读 ctx.voice_meta（惰性，vllm/gptsovits 命中时不会触发），resolve 复刻
# 原 get_tts_worker 克隆块逻辑（含 cosyvoice_intl key 缺失 → dummy 的凭证兜底）。

def _minimax_clone_is_selected(ctx) -> bool:
    vm = ctx.voice_meta
    return bool(vm and str(vm.get('provider', '')).startswith('minimax'))

def _minimax_clone_resolve(ctx):
    vm = ctx.voice_meta or {}
    provider = vm.get('provider') or 'minimax'
    logger.info("检测到 MiniMax 克隆音色: %s (provider=%s)，使用 MiniMax TTS Worker",
                ctx.voice_id, provider)
    api_key = ctx.cm.get_tts_api_key(provider)
    from utils.tts.providers.minimax import MINIMAX_DOMESTIC_BASE_URL, MINIMAX_INTL_BASE_URL
    base_url = vm.get('minimax_base_url') or (
        MINIMAX_INTL_BASE_URL if provider == 'minimax_intl' else MINIMAX_DOMESTIC_BASE_URL
    )
    return partial(minimax_tts_worker, base_url=base_url), api_key, 'minimax'
