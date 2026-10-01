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

"""Zhipu GLM (bigmodel.cn) TTS helpers — voice cloning + preview.

Structural dual of ``utils/doubao_tts.py`` (a remotely-registered clone provider):
  - ``GlmVoiceCloneClient.upload_file``    → POST /paas/v4/files (purpose=voice-clone-input)
  - ``GlmVoiceCloneClient.clone_voice``    → POST /paas/v4/voice/clone (model=glm-tts-clone)
  - ``GlmVoiceCloneClient.synthesize_preview`` → POST /paas/v4/audio/speech (voice=<cloned id>)

Per the official docs (docs.bigmodel.cn, API reference → model APIs → voice clone /
text-to-speech / file upload):
  - Upload: multipart ``file`` + ``purpose``; clone samples only accept mp3/wav,
    ≤10MB per file, 3-30 seconds recommended; response ``{id, object:"file", ...}``.
  - Clone: JSON ``{model, voice_name, input, file_id, text?, request_id?}``;
    ``voice_name`` must be unique per account; ``input`` is the preview text
    synthesized during cloning (required); response ``{voice, file_id,
    file_purpose, request_id}`` where ``voice`` is the cloned id used for synthesis.
  - Synthesis: the ``voice`` field of ``/audio/speech`` officially supports both
    system voices and cloned voices.

httpx is NOT imported at module top level (same convention as doubao_tts): the
GLM_VOICE_STORAGE_KEY string constant is referenced by utils/config_manager and
thus sits on the launcher's startup import chain, where httpx's eager CLI import
(rich/pygments/click) would slow down startup-to-port-bind; import it lazily.
"""

from __future__ import annotations

import io
import re
import uuid
from typing import Any

GLM_TTS_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
# 官方 /audio/speech 文档 model 枚举仅 glm-tts；复刻音色的合成与试听都用它。
# 原生 CogTTS 路径（workers/cogtts.py 默认 model="cogtts"）保持不变。
GLM_TTS_SPEECH_MODEL = "glm-tts"
GLM_VOICE_CLONE_MODEL = "glm-tts-clone"
GLM_VOICE_STORAGE_KEY = "__GLM_TTS__"
# voice_clone 接口「input」必填：克隆时同步生成一段试听语音。固定一句短中文即可，
# 产物 file_id（voice-clone-output）不落库——试听走 synthesize_preview 按需合成。
GLM_VOICE_CLONE_PREVIEW_INPUT = "你好呀，很高兴认识你。"
# 官方建议示例音频 3-30 秒；文件 ≤10MB（与上传接口 purpose=voice-clone-input 的限制一致）。
GLM_VOICE_CLONE_MAX_AUDIO_BYTES = 10 * 1024 * 1024
# voice_name 需账号内唯一；neko_ 前缀 + 用户前缀（sanitize 后）+ 音频 MD5 片段：
# 同一账号重传同一段音频时先被本地 MD5 去重拦下，撞名只发生在绕过去重的极小概率场景。
GLM_VOICE_NAME_PREFIX = "neko"
GLM_VOICE_NAME_MAX_LENGTH = 64


class GlmTtsError(Exception):
    pass


def glm_normalize_base_url(base_url: str | None) -> str:
    return (base_url or GLM_TTS_DEFAULT_BASE_URL).strip().rstrip("/")


def glm_files_upload_url(base_url: str | None) -> str:
    return f"{glm_normalize_base_url(base_url)}/files"


def glm_voice_clone_url(base_url: str | None) -> str:
    return f"{glm_normalize_base_url(base_url)}/voice/clone"


def glm_speech_url(base_url: str | None) -> str:
    return f"{glm_normalize_base_url(base_url)}/audio/speech"


def glm_api_headers(api_key: str, *, json_body: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {api_key}"}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def sanitize_glm_voice_prefix(prefix: str) -> str:
    """User prefix → voice_name-safe fragment: alphanumerics only, lowercased."""
    cleaned = re.sub(r"[^0-9a-zA-Z]", "", str(prefix or "")).lower()
    return cleaned[:24]


def build_glm_voice_name(prefix: str, audio_md5: str, ref_language: str = "") -> str:
    """Build an account-unique voice_name (a required field upstream).

    The dimensions mirror the local MD5 dedup key (storage_key, audio_md5,
    ref_language): audio_md5 alone does not change when the user re-clones the
    same bytes under a different ref_language (dedup misses), so ref_language
    must be part of the name too — otherwise the second registration reuses
    the first name and GLM rejects it for violating per-account uniqueness.
    """
    safe_prefix = sanitize_glm_voice_prefix(prefix) or "voice"
    lang = re.sub(r"[^0-9a-zA-Z]", "", str(ref_language or "")).lower()[:4]
    digest = re.sub(r"[^0-9a-fA-F]", "", str(audio_md5 or "")).lower()[:12] or uuid.uuid4().hex[:12]
    suffix = f"{lang}_{digest}" if lang else digest
    return f"{GLM_VOICE_NAME_PREFIX}_{safe_prefix}_{suffix}"[:GLM_VOICE_NAME_MAX_LENGTH]


def _raise_glm_api_error(action: str, payload: dict[str, Any]) -> None:
    """Parse Zhipu's uniform error body ``{"error": {"code", "message"}}`` and raise."""
    err = payload.get("error")
    if isinstance(err, dict):
        code = err.get("code") or ""
        message = err.get("message") or err
        raise GlmTtsError(f"{action}失败: [{code}] {message}")
    raise GlmTtsError(f"{action}失败: {payload}")


class GlmVoiceCloneClient:
    """GLM voice-clone client (two steps: upload sample → /voice/clone)."""

    def __init__(self, api_key: str, *, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = glm_normalize_base_url(base_url)

    async def upload_file(
        self,
        audio_buffer: io.BytesIO,
        filename: str,
        mime_type: str = "audio/wav",
    ) -> str:
        """Upload the sample audio (purpose=voice-clone-input) and return file_id."""
        import httpx

        audio_buffer.seek(0)
        data = audio_buffer.getvalue()
        if len(data) > GLM_VOICE_CLONE_MAX_AUDIO_BYTES:
            raise GlmTtsError("示例音频超过 10MB 上限，请裁剪后重试")
        files = {"file": (filename or "prompt_audio.wav", io.BytesIO(data), mime_type)}
        url = glm_files_upload_url(self.base_url)
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    url,
                    headers=glm_api_headers(self.api_key),
                    files=files,
                    data={"purpose": "voice-clone-input"},
                )
        except httpx.TimeoutException as exc:
            raise GlmTtsError("GLM 示例音频上传超时，请稍后重试") from exc
        except Exception as exc:
            raise GlmTtsError(f"GLM 示例音频上传失败: {exc}") from exc
        if resp.status_code != 200:
            raise GlmTtsError(
                f"GLM 示例音频上传失败: HTTP {resp.status_code}, {resp.text[:300]}"
            )
        try:
            result = resp.json()
        except ValueError as exc:
            raise GlmTtsError("GLM 示例音频上传返回了无法解析的响应") from exc
        if not isinstance(result, dict) or result.get("error"):
            _raise_glm_api_error("GLM 示例音频上传", result if isinstance(result, dict) else {})
        file_id = str((result or {}).get("id") or "").strip()
        if not file_id:
            raise GlmTtsError(f"GLM 示例音频上传成功但未返回 file_id: {result}")
        return file_id

    async def clone_voice(
        self,
        audio_buffer: io.BytesIO,
        *,
        voice_name: str,
        filename: str = "prompt_audio.wav",
        input_text: str = GLM_VOICE_CLONE_PREVIEW_INPUT,
        ref_text: str = "",
        mime_type: str = "audio/wav",
    ) -> str:
        """Upload + register in one call; returns the cloned voice id for synthesis."""
        import httpx

        file_id = await self.upload_file(audio_buffer, filename, mime_type=mime_type)
        payload: dict[str, Any] = {
            "model": GLM_VOICE_CLONE_MODEL,
            "voice_name": voice_name,
            "input": input_text,
            "file_id": file_id,
            "request_id": str(uuid.uuid4()),
        }
        if str(ref_text or "").strip():
            payload["text"] = str(ref_text).strip()
        url = glm_voice_clone_url(self.base_url)
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    url,
                    headers=glm_api_headers(self.api_key, json_body=True),
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise GlmTtsError("GLM 声音复刻请求超时，请稍后重试") from exc
        except Exception as exc:
            raise GlmTtsError(f"GLM 声音复刻请求失败: {exc}") from exc
        if resp.status_code != 200:
            raise GlmTtsError(
                f"GLM 声音复刻失败: HTTP {resp.status_code}, {resp.text[:300]}"
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise GlmTtsError("GLM 声音复刻返回了无法解析的响应") from exc
        if not isinstance(data, dict) or data.get("error"):
            _raise_glm_api_error("GLM 声音复刻", data if isinstance(data, dict) else {})
        voice_id = str((data or {}).get("voice") or "").strip()
        if not voice_id:
            raise GlmTtsError(f"GLM 声音复刻成功但未返回 voice: {data}")
        return voice_id

    async def synthesize_preview(
        self,
        voice_id: str,
        text: str,
        *,
        model: str = GLM_TTS_SPEECH_MODEL,
    ) -> bytes:
        """Synthesize a preview WAV with the cloned voice (non-streaming).

        Same ``/audio/speech`` endpoint as workers/cogtts.py; the voice field
        officially supports cloned voices. Non-streaming + response_format=wav
        returns the audio in one shot (dual to MiMo's non-streaming validation
        request — never ask for a raw pcm16 stream here).
        """
        import httpx

        payload = {
            "model": model,
            "input": text[:1024],
            "voice": voice_id,
            "response_format": "wav",
            "stream": False,
            # Same policy as the cogtts worker: takes effect only for accounts
            # that completed the de-watermark setup in the Zhipu console; the
            # server ignores it for everyone else.
            "watermark_enabled": False,
        }
        url = glm_speech_url(self.base_url)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    headers=glm_api_headers(self.api_key, json_body=True),
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise GlmTtsError("GLM 试听音频生成超时，请稍后重试") from exc
        except Exception as exc:
            raise GlmTtsError(f"GLM 试听音频生成失败: {exc}") from exc
        if resp.status_code != 200:
            raise GlmTtsError(
                f"GLM 试听音频生成失败: HTTP {resp.status_code}, {resp.text[:300]}"
            )
        content_type = resp.headers.get("content-type", "")
        if "json" in content_type.lower():
            # 错误体走统一 JSON 结构；解析失败再兜底抛原始片段
            try:
                data = resp.json()
            except ValueError:
                data = {}
            _raise_glm_api_error("GLM 试听音频生成", data if isinstance(data, dict) else {})
        audio = resp.content or b""
        if not audio:
            raise GlmTtsError("GLM 试听音频生成成功但未返回音频")
        return audio
