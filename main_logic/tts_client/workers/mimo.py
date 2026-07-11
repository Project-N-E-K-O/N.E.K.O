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

"""MiMo TTS worker."""

import numpy as np
import soxr
import json
import base64

from functools import partial
from utils.mimo_tts_voices import (
    MIMO_TTS_MODEL,
    MIMO_TTS_VOICECLONE_MODEL,
    MIMO_TTS_VOICEDESIGN_MODEL,
    mimo_chat_completions_url,
    normalize_mimo_tts_voice,
)

from .._infra import _resample_audio, _enqueue_error, _run_sentence_tts_worker
from .._telemetry import _record_tts_telemetry
from .dummy import dummy_tts_worker
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

# Backwards-compatible alias — the URL-derivation rule now lives in the utils
# layer (utils.mimo_tts_voices.mimo_chat_completions_url) so the clone-enrollment
# client can share it without importing main_logic.
_get_mimo_chat_completions_url = mimo_chat_completions_url

def _extract_mimo_tts_audio_bytes(payload: dict) -> bytes | None:
    """Extract base64 PCM16 audio from MiMo's chat-completions response."""
    candidates: list[object] = [payload.get("audio")]
    for choice in payload.get("choices") or []:
        if isinstance(choice, dict):
            candidates.extend([
                choice.get("audio"),
                (choice.get("message") or {}).get("audio"),
                (choice.get("delta") or {}).get("audio"),
            ])
            content = (choice.get("message") or {}).get("content")
            if isinstance(content, list):
                candidates.extend(content)

    for candidate in candidates:
        audio_b64 = ""
        if isinstance(candidate, str):
            audio_b64 = candidate
        elif isinstance(candidate, dict):
            audio_b64 = (
                candidate.get("data")
                or candidate.get("audio")
                or candidate.get("content")
                or ""
            )
        if not audio_b64:
            continue
        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception:
            continue
        usable_len = len(audio_bytes) - (len(audio_bytes) % 2)
        if usable_len > 0:
            return audio_bytes[:usable_len]
    return None

def mimo_tts_worker(request_queue, response_queue, audio_api_key, voice_id, base_url=None,
                    clone_voice=None, design_prompt=None):
    """Xiaomi MiMo-V2.5-TTS worker — chat-completions JSON returns PCM16.

    ``clone_voice`` is the cloned-voice variant: when set it is a
    ``data:audio/...;base64,...`` reference-audio URI (MiMo has no server-side
    cloned voice id — the sample is inlined per request, see
    ``utils.mimo_tts_voices.mimo_voice_clone_data_uri``). It is passed as
    ``audio.voice`` against the ``mimo-v2.5-tts-voiceclone`` model; the catalog
    ``voice_id`` is ignored in that mode.
    """
    import httpx

    is_clone = bool(clone_voice)
    is_design = bool(str(design_prompt or "").strip())
    if is_design:
        tts_model = MIMO_TTS_VOICEDESIGN_MODEL
    elif is_clone:
        tts_model = MIMO_TTS_VOICECLONE_MODEL
    else:
        tts_model = MIMO_TTS_MODEL
    if is_design:
        voice_param = None
    elif is_clone:
        voice_param = clone_voice
    else:
        requested_voice_id = (voice_id or "").strip()
        voice_id, voice_recognized = normalize_mimo_tts_voice(voice_id)
        if requested_voice_id and not voice_recognized:
            logger.warning(
                "MiMo TTS voice '%s' is not in the supported catalog; falling back to '%s'",
                requested_voice_id,
                voice_id,
            )
        voice_param = voice_id

    async def setup(response_queue):
        if not audio_api_key:
            _enqueue_error(response_queue, {
                "code": "API_KEY_MISSING",
                "provider": "mimo",
                "message": "MiMo API key is not configured",
            })
            raise RuntimeError("MiMo API key is not configured")

        api_url = _get_mimo_chat_completions_url(base_url)
        headers = {
            "Content-Type": "application/json",
            "api-key": audio_api_key,
        }
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )

        async def synthesize(text: str, speech_id: str) -> None:
            messages = [{"role": "assistant", "content": text}]
            if is_design:
                messages = [
                    {"role": "user", "content": str(design_prompt).strip()},
                    {"role": "assistant", "content": text},
                ]
            payload = {
                "model": tts_model,
                "messages": messages,
                "audio": {
                    "format": "pcm16",
                },
                "stream": True,
            }
            if voice_param:
                payload["audio"]["voice"] = voice_param
            resampler = soxr.ResampleStream(24000, 48000, 1, dtype="float32")

            def handle_event(event: dict) -> None:
                audio_bytes = _extract_mimo_tts_audio_bytes(event)
                if not audio_bytes:
                    return
                audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
                response_queue.put(_resample_audio(audio_array, 24000, 48000, resampler))

            try:
                async with client.stream("POST", api_url, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        error_text = ""
                        async for chunk in resp.aiter_text():
                            error_text += chunk
                        _enqueue_error(
                            response_queue,
                            f"MiMo TTS API错误 ({resp.status_code}): {error_text[:300]}",
                        )
                        return

                    _record_tts_telemetry(tts_model, len(text))
                    content_type = resp.headers.get("content-type", "").lower()
                    if "text/event-stream" not in content_type:
                        try:
                            body = await resp.aread()
                            handle_event(json.loads(body.decode("utf-8")))
                        except Exception as exc:
                            _enqueue_error(response_queue, f"MiMo TTS 响应 JSON 解析失败: {exc}")
                        return

                    buffer = ""
                    async for raw_chunk in resp.aiter_text():
                        buffer += raw_chunk
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line or line.startswith(":"):
                                continue
                            if line.startswith("data:"):
                                line = line[5:].strip()
                            if not line or line == "[DONE]":
                                continue
                            try:
                                handle_event(json.loads(line))
                            except json.JSONDecodeError:
                                logger.warning("MiMo TTS SSE JSON 解析失败 (len=%d)", len(line))
                                continue

                    residual = buffer.strip()
                    if residual:
                        if residual.startswith("data:"):
                            residual = residual[5:].strip()
                        if residual and residual != "[DONE]":
                            try:
                                handle_event(json.loads(residual))
                            except json.JSONDecodeError:
                                logger.warning("MiMo TTS SSE JSON 解析失败 (残留, len=%d)", len(residual))
            except Exception as exc:
                _enqueue_error(response_queue, f"MiMo TTS 请求失败: {exc}")
                return

        return synthesize, client.aclose

    _run_sentence_tts_worker(request_queue, response_queue, setup, label="MiMo TTS")

# ── MiMo（hosted SaaS）────────────────────────────────────────────────────────
# 两种选中机制（设计文档 §3.1）合并在一个 provider 条目里：
#   1. 配置选中（preset）——assistApi=mimo / TTS_PROVIDER=mimo 时把 MiMo 当默认 TTS，
#      走预制音色目录。
#   2. 音色元数据选中（clone）——用户挑了某个 MiMo 克隆音色（voice_meta.provider=='mimo'），
#      对偶 cosyvoice/minimax/elevenlabs 的克隆路由。MiMo 克隆没有远端 voice_id：参考音频
#      存在本地，dispatch 时读出来内联进 voiceclone 请求。

def _mimo_voice_meta_is_clone(vm) -> bool:
    return bool(vm and vm.get('provider') == 'mimo')

def _mimo_has_foreign_clone_voice(ctx) -> bool:
    if not (ctx.has_custom_voice and ctx.voice_id):
        return False
    provider = str((ctx.voice_meta or {}).get('provider') or '').strip().lower()
    return bool(provider and provider != 'mimo')

def _mimo_is_selected(ctx) -> bool:
    cc = ctx.core_config
    tts_provider = str(cc.get('TTS_PROVIDER') or cc.get('ttsProvider') or '').strip().lower()
    assist_api_type = str(cc.get('assistApi') or '').strip().lower()
    if tts_provider == 'mimo' or assist_api_type == 'mimo':
        return not _mimo_has_foreign_clone_voice(ctx)
    # 克隆音色选中：按所选音色的 voice_meta.provider 路由（惰性，命中前面 config-selected
    # provider 时不会触发 voice_meta 加载）。
    return _mimo_voice_meta_is_clone(ctx.voice_meta)

def _mimo_resolve(ctx):
    cc = ctx.core_config
    mimo_api_key = (ctx.cm.get_tts_api_key('mimo') or '').strip()
    if not mimo_api_key:
        logger.warning(
            "MiMo TTS 已选中但 MiMo API Key 缺失，改用 dummy TTS worker 避免复用主 TTS Key")
        return dummy_tts_worker, None, None

    assist_api_type = str(cc.get('assistApi') or '').strip().lower()
    # 配置端点：assistApi=mimo 时（Token Plan 的唯一场景）get_core_config 已把 OPENROUTER_URL
    # 解析成对应端点（普通 / token-plan-*），且 get_tts_api_key('mimo') 返回配套 key——必须用
    # 它，保证 key 与端点同源；否则用默认 xiaomimimo。
    config_base_url = cc.get('OPENROUTER_URL') if assist_api_type == 'mimo' else None

    # 克隆音色优先：用户挑了某个具体的 MiMo 克隆音色（voice_meta.provider=='mimo'），即使
    # 同时把 MiMo 配成了默认 TTS 也应当尊重这个更具体的选择，走 voiceclone 内联参考音频。
    vm = ctx.voice_meta
    if vm and vm.get('provider') == 'mimo' and vm.get('source') == 'design':
        design_prompt = str(vm.get('design_prompt') or '').strip()
        if not design_prompt:
            logger.warning(
                "MiMo 设计音色 %s 缺少 design_prompt，改用 dummy TTS worker", ctx.voice_id)
            return dummy_tts_worker, None, None
        design_base_url = config_base_url or (vm or {}).get('mimo_base_url') or None
        return (
            partial(mimo_tts_worker, base_url=design_base_url, design_prompt=design_prompt),
            mimo_api_key,
            'mimo',
        )

    if _mimo_voice_meta_is_clone(vm):
        clone_voice = _build_mimo_clone_data_uri(vm)
        if not clone_voice:
            logger.warning(
                "MiMo 克隆音色 %s 缺少参考音频样本，改用 dummy TTS worker", ctx.voice_id)
            return dummy_tts_worker, None, None
        # base_url：assistApi=mimo 用配置端点（token-plan 同源）；否则用 voice_meta 里存的
        # mimo_base_url（对偶 minimax_base_url），缺省回落默认。
        clone_base_url = config_base_url or (vm or {}).get('mimo_base_url') or None
        return (
            partial(mimo_tts_worker, base_url=clone_base_url, clone_voice=clone_voice),
            mimo_api_key,
            'mimo',
        )

    # 配置选中：MiMo 作为默认 TTS，走预制音色目录。
    return partial(mimo_tts_worker, base_url=config_base_url), mimo_api_key, 'mimo'

def _build_mimo_clone_data_uri(voice_meta) -> str | None:
    """Build the ``data:`` reference-audio URI for a MiMo clone from its
    voice_meta (the clone identity lives entirely in voice_storage.json — the
    sample base64 is stored inline, dual to MiniMax's remote voice_id), or None
    when absent.

    The stored value is already base64, so this only frames it as a data URI —
    no decode/re-encode. Bound into the (same-process) worker thread.
    """
    b64 = str((voice_meta or {}).get('clone_sample_b64') or '').strip()
    if not b64:
        return None
    mime = str((voice_meta or {}).get('clone_sample_mime') or '').strip() or 'audio/wav'
    return f"data:{mime};base64,{b64}"
