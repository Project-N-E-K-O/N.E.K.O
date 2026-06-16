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
from urllib.parse import urlparse, urlunparse
from utils.mimo_tts_voices import MIMO_TTS_BASE_URL, MIMO_TTS_MODEL, normalize_mimo_tts_voice

from .._infra import _resample_audio, _enqueue_error, _run_sentence_tts_worker
from .._telemetry import _record_tts_telemetry
from .dummy import dummy_tts_worker
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

def _get_mimo_chat_completions_url(base_url: str | None = None) -> str:
    """Normalize a MiMo API base URL to the chat-completions endpoint."""
    raw_url = (base_url or MIMO_TTS_BASE_URL).strip().rstrip("/")
    if raw_url.startswith("ws://"):
        raw_url = "http://" + raw_url[5:]
    elif raw_url.startswith("wss://"):
        raw_url = "https://" + raw_url[6:]
    elif not raw_url.startswith(("http://", "https://")):
        raw_url = "https://" + raw_url

    parsed = urlparse(raw_url)
    if not parsed.netloc:
        raise ValueError(f"无效的 MiMo base_url: {base_url!r}")

    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        endpoint_path = path
    else:
        if not path or path == "/":
            endpoint_path = "/v1/chat/completions"
        elif path.endswith("/v1"):
            endpoint_path = f"{path}/chat/completions"
        else:
            endpoint_path = f"{path}/v1/chat/completions"
    return urlunparse((parsed.scheme, parsed.netloc, endpoint_path, "", "", ""))

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

def mimo_tts_worker(request_queue, response_queue, audio_api_key, voice_id, base_url=None):
    """Xiaomi MiMo-V2.5-TTS worker — chat-completions JSON returns PCM16."""
    import httpx

    requested_voice_id = (voice_id or "").strip()
    voice_id, voice_recognized = normalize_mimo_tts_voice(voice_id)
    if requested_voice_id and not voice_recognized:
        logger.warning(
            "MiMo TTS voice '%s' is not in the supported catalog; falling back to '%s'",
            requested_voice_id,
            voice_id,
        )

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
            payload = {
                "model": MIMO_TTS_MODEL,
                "messages": [
                    {"role": "assistant", "content": text},
                ],
                "audio": {
                    "format": "pcm16",
                    "voice": voice_id,
                },
                "stream": True,
            }
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

                    _record_tts_telemetry(MIMO_TTS_MODEL, len(text))
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

# ── MiMo（hosted SaaS，assistApi=mimo / TTS_PROVIDER=mimo 选中）────────────────

def _mimo_is_selected(ctx) -> bool:
    cc = ctx.core_config
    tts_provider = str(cc.get('TTS_PROVIDER') or cc.get('ttsProvider') or '').strip().lower()
    assist_api_type = str(cc.get('assistApi') or '').strip().lower()
    return tts_provider == 'mimo' or assist_api_type == 'mimo'

def _mimo_resolve(ctx):
    cc = ctx.core_config
    assist_api_type = str(cc.get('assistApi') or '').strip().lower()
    mimo_base_url = cc.get('OPENROUTER_URL') if assist_api_type == 'mimo' else None
    mimo_api_key = (ctx.cm.get_tts_api_key('mimo') or '').strip()
    if not mimo_api_key:
        logger.warning(
            "MiMo TTS 已选中但 MiMo API Key 缺失，改用 dummy TTS worker 避免复用主 TTS Key")
        return dummy_tts_worker, None, None
    return partial(mimo_tts_worker, base_url=mimo_base_url), mimo_api_key, 'mimo'
