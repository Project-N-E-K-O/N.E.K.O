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

对偶 ``utils/doubao_tts.py`` 的结构（远端注册型克隆 provider）：
  - ``GlmVoiceCloneClient.upload_file``    → POST /paas/v4/files (purpose=voice-clone-input)
  - ``GlmVoiceCloneClient.clone_voice``    → POST /paas/v4/voice/clone (model=glm-tts-clone)
  - ``GlmVoiceCloneClient.synthesize_preview`` → POST /paas/v4/audio/speech (voice=复刻音色)

官方文档（docs.bigmodel.cn，API 参考 → 模型 API → 音色复刻 / 文本转语音 / 上传文件）：
  - 上传：multipart ``file`` + ``purpose``，音色克隆示例音频仅支持 mp3/wav，
    单文件 ≤10MB，建议时长 3-30 秒；响应 ``{id, object:"file", ...}``。
  - 复刻：JSON ``{model, voice_name, input, file_id, text?, request_id?}``；
    ``voice_name`` 必须账号内唯一；``input`` 是克隆时同步生成的试听文本（必填）；
    响应 ``{voice, file_id, file_purpose, request_id}``，``voice`` 即合成用的复刻音色 ID。
  - 合成：``/audio/speech`` 的 ``voice`` 官方明确「支持系统音色以及复刻音色两种类型」。

httpx 不在模块顶层 import（与 doubao_tts 同构）：GLM_VOICE_STORAGE_KEY 字符串常量被
utils/config_manager 引用、坐在 launcher 启动导入链上，httpx 的 eager CLI import
（rich/pygments/click）会拖慢进程启动到端口 bind 的时间，用到时再 import。
"""

from __future__ import annotations

import io
import re
import uuid
from typing import Any

GLM_TTS_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
GLM_TTS_SPEECH_MODEL = "cogtts"  # 与 workers/cogtts.py 现网模型名保持一致
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
    """用户前缀 → voice_name 安全片段：仅保留字母数字，转小写。"""
    cleaned = re.sub(r"[^0-9a-zA-Z]", "", str(prefix or "")).lower()
    return cleaned[:24]


def build_glm_voice_name(prefix: str, audio_md5: str) -> str:
    """构造账号内唯一的 voice_name（官方必填字段）。

    维度对齐本地 MD5 去重键 (storage_key, audio_md5, ref_language) 中的音频维度：
    同音频换语言重克隆会生成不同的 audio_md5 片段，不撞名。
    """
    safe_prefix = sanitize_glm_voice_prefix(prefix) or "voice"
    digest = re.sub(r"[^0-9a-fA-F]", "", str(audio_md5 or "")).lower()[:12] or uuid.uuid4().hex[:12]
    return f"{GLM_VOICE_NAME_PREFIX}_{safe_prefix}_{digest}"[:GLM_VOICE_NAME_MAX_LENGTH]


def _raise_glm_api_error(action: str, payload: dict[str, Any]) -> None:
    """解析智谱统一错误体 ``{"error": {"code", "message"}}`` 并抛 GlmTtsError。"""
    err = payload.get("error")
    if isinstance(err, dict):
        code = err.get("code") or ""
        message = err.get("message") or err
        raise GlmTtsError(f"{action}失败: [{code}] {message}")
    raise GlmTtsError(f"{action}失败: {payload}")


class GlmVoiceCloneClient:
    """GLM 声音复刻客户端（两步：上传示例音频 → /voice/clone 注册远端音色）。"""

    def __init__(self, api_key: str, *, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = glm_normalize_base_url(base_url)

    async def upload_file(
        self,
        audio_buffer: io.BytesIO,
        filename: str,
        mime_type: str = "audio/wav",
    ) -> str:
        """上传示例音频（purpose=voice-clone-input），返回 file_id。"""
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
        """上传 + 注册组合流程，返回远端复刻音色 ID（合成时作 voice 使用）。"""
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
        """用复刻音色合成试听音频（非流式 wav），返回音频 bytes。

        与 workers/cogtts.py 同一 ``/audio/speech`` 端点；voice 官方支持复刻音色。
        非流式 + response_format=wav 一次性返回（对偶 MiMo 的非流式校验请求，
        不要 pcm16 裸流）。
        """
        import httpx

        payload = {
            "model": model,
            "input": text[:1024],
            "voice": voice_id,
            "response_format": "wav",
            "stream": False,
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
