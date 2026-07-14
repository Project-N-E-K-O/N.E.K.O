# -*- coding: utf-8 -*-
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

"""MiniMax voice registration adapter for China and international endpoints.

MiniMax voice cloning:
  2-step flow: upload audio → create voice
  CN base URL:            https://api.minimaxi.com
  International base URL: https://api.minimax.io
  Auth: Authorization: Bearer {api_key}

MiniMax voice design:
  prompt + preview text → reusable remote voice_id
"""

import io
import binascii
import logging
from typing import Optional
from urllib.parse import urlparse

import httpx

from utils.http.external_client import get_external_http_client

from .base import VoiceCloneError, first_nested_value

logger = logging.getLogger(__name__)


# ============================================================================
# MiniMax 语音克隆
# ============================================================================

# MiniMax 国服 API 端点（默认）
MINIMAX_DOMESTIC_BASE_URL = "https://api.minimaxi.com"
# MiniMax 国际服 API 端点
MINIMAX_INTL_BASE_URL = "https://api.minimax.io"

# 内部语言代码 → MiniMax 语言代码
_MINIMAX_LANGUAGE_CODE_MAP = {
    'ch': 'zh', 'zh': 'zh',
    'en': 'en',
    'ja': 'ja', 'jp': 'ja',
    'ko': 'ko',
    'de': 'de', 'fr': 'fr', 'ru': 'ru',
    'es': 'es', 'it': 'it', 'pt': 'pt',
}

# voice_storage 中标识 MiniMax 音色的前缀
MINIMAX_VOICE_STORAGE_KEY = '__MINIMAX__'
# voice_storage 中标识 MiniMax 国际服音色的前缀
MINIMAX_INTL_VOICE_STORAGE_KEY = '__MINIMAX_INTL__'
MINIMAX_PREFIX_MAX_LENGTH = 10


class MinimaxVoiceCloneError(VoiceCloneError):
    """MiniMax voice-clone related error"""


def minimax_normalize_language(lang: str) -> str:
    """Convert the project's internal language codes to MiniMax language codes."""
    return _MINIMAX_LANGUAGE_CODE_MAP.get(lang.lower().strip(), 'zh')


def get_minimax_base_url(provider: str = 'minimax') -> str:
    """Return the MiniMax API base URL for the given provider."""
    if provider == 'minimax_intl':
        return MINIMAX_INTL_BASE_URL
    return MINIMAX_DOMESTIC_BASE_URL


def get_minimax_storage_prefix(provider: str = 'minimax') -> str:
    """Return the voice_storage key prefix for the given provider."""
    if provider == 'minimax_intl':
        return MINIMAX_INTL_VOICE_STORAGE_KEY
    return MINIMAX_VOICE_STORAGE_KEY


def sanitize_minimax_voice_prefix(
    prefix: str,
    default_prefix: str = 'voice',
    *,
    max_length: Optional[int] = MINIMAX_PREFIX_MAX_LENGTH,
) -> str:
    """Restrict the MiniMax prefix to ASCII alphanumerics.

    MiniMax is stricter about the ``voice_id`` character set when creating voices.
    Here we keep only English letters and digits; when the result is empty, fall back
    to ``voice``.
    """
    normalized = ''.join(ch for ch in str(prefix or '') if ch.isascii() and ch.isalnum())
    if max_length is not None:
        normalized = normalized[:max_length]
    if normalized:
        return normalized

    fallback = ''.join(ch for ch in str(default_prefix or '') if ch.isascii() and ch.isalnum())
    if max_length is not None:
        fallback = fallback[:max_length]
    return fallback or 'voice'


class MinimaxVoiceCloneClient:
    """MiniMax voice cloning client (works for both the CN and international services)"""

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        self.api_key = api_key
        self.base_url = (base_url or MINIMAX_DOMESTIC_BASE_URL).rstrip('/')

    def _headers(self, *, json_body: bool = False) -> dict:
        h = {'Authorization': f'Bearer {self.api_key}'}
        if json_body:
            h['Content-Type'] = 'application/json'
        return h

    # ------------------------------------------------------------------
    # Step 1 - 上传音频文件，获取 file_id
    # ------------------------------------------------------------------
    async def upload_file(
        self,
        audio_buffer: io.BytesIO,
        filename: str,
    ) -> str:
        """Upload audio to MiniMax, returning file_id.

        Raises:
            MinimaxVoiceCloneError
        """
        url = f"{self.base_url}/v1/files/upload"
        audio_buffer.seek(0)
        files = {'file': (filename, audio_buffer, 'audio/wav')}
        data = {'purpose': 'voice_clone'}

        headers = self._headers()
        logger.info("[MiniMax] Upload URL: %s", url)

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, headers=headers, files=files, data=data)

            logger.info("[MiniMax] Upload response status: %d", resp.status_code)

            if resp.status_code != 200:
                raise MinimaxVoiceCloneError(
                    f"上传音频失败: HTTP {resp.status_code}, {resp.text[:300]}"
                )

            result = resp.json()
            base_resp = result.get('base_resp') or {}
            if base_resp.get('status_code', 0) != 0:
                raise MinimaxVoiceCloneError(
                    f"上传音频失败: {base_resp.get('status_msg', 'Unknown error')}"
                )

            file_id = result.get('file', {}).get('file_id') or result.get('file_id')
            if not file_id:
                raise MinimaxVoiceCloneError(f"上传成功但未返回 file_id: {result}")

            logger.info("MiniMax 音频上传成功: file_id=%s", file_id)
            return file_id

        except MinimaxVoiceCloneError:
            raise
        except httpx.TimeoutException as e:
            raise MinimaxVoiceCloneError("上传音频超时，请稍后重试") from e
        except Exception as e:
            raise MinimaxVoiceCloneError(f"上传音频失败: {e}") from e

    # ------------------------------------------------------------------
    # Step 2 - 用 file_id 创建/注册音色
    # ------------------------------------------------------------------
    async def create_voice(
        self,
        file_id: str,
        voice_id: str,
        *,
        voice_name: Optional[str] = None,
        language: str = "zh",
        voice_description: Optional[str] = None,
    ) -> str:
        """Create a voice, returning the final voice_id.

        Args:
            file_id: file_id returned by upload_file()
            voice_id: user-defined voice_id (may include a prefix)
            voice_name: optional display name
            language: MiniMax language code (zh / en / ja …)
            voice_description: optional description

        Raises:
            MinimaxVoiceCloneError
        """
        url = f"{self.base_url}/v1/voice_clone"
        payload: dict = {
            'file_id': file_id,
            'voice_id': voice_id,
        }
        if voice_name:
            payload['voice_name'] = voice_name
        if language:
            payload['language'] = language
        if voice_description:
            payload['voice_description'] = voice_description

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    url,
                    headers=self._headers(json_body=True),
                    json=payload,
                )

            if resp.status_code != 200:
                raise MinimaxVoiceCloneError(
                    f"创建音色失败: HTTP {resp.status_code}, {resp.text[:300]}"
                )

            result = resp.json()
            base_resp = result.get('base_resp') or {}
            if base_resp.get('status_code', 0) != 0:
                raise MinimaxVoiceCloneError(
                    f"创建音色失败: {base_resp.get('status_msg', 'Unknown error')}"
                )

            returned_voice_id = result.get('voice_id') or voice_id
            logger.info("MiniMax 音色创建成功: voice_id=%s", returned_voice_id)
            return returned_voice_id

        except MinimaxVoiceCloneError:
            raise
        except httpx.TimeoutException as e:
            raise MinimaxVoiceCloneError("创建音色超时，请稍后重试") from e
        except Exception as e:
            raise MinimaxVoiceCloneError(f"创建音色失败: {e}") from e

    async def synthesize_preview(
        self,
        voice_id: str,
        text: str,
        *,
        model: str = "speech-2.8-hd",
    ) -> bytes:
        """Generate preview audio via the MiniMax T2A endpoint, returning MP3 bytes."""
        url = f"{self.base_url}/v1/t2a_v2"
        payload = {
            'model': model,
            'text': text,
            'stream': False,
            'voice_setting': {
                'voice_id': voice_id,
                'speed': 1,
                'vol': 1,
                'pitch': 0,
            },
            'audio_setting': {
                'sample_rate': 32000,
                'bitrate': 128000,
                'format': 'mp3',
                'channel': 1,
            },
            'subtitle_enable': False,
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    headers=self._headers(json_body=True),
                    json=payload,
                )

            if resp.status_code != 200:
                raise MinimaxVoiceCloneError(
                    f"预览音频生成失败: HTTP {resp.status_code}, {resp.text[:300]}"
                )

            result = resp.json()
            base_resp = result.get('base_resp') or {}
            if base_resp.get('status_code', 0) != 0:
                raise MinimaxVoiceCloneError(
                    f"预览音频生成失败: {base_resp.get('status_msg', 'Unknown error')}"
                )

            audio_hex = (result.get('data') or {}).get('audio', '')
            if not audio_hex:
                raise MinimaxVoiceCloneError(f"预览音频生成成功但未返回 audio: {result}")

            try:
                return binascii.unhexlify(audio_hex)
            except (binascii.Error, ValueError) as e:
                raise MinimaxVoiceCloneError("预览音频解码失败") from e

        except MinimaxVoiceCloneError:
            raise
        except httpx.TimeoutException as e:
            raise MinimaxVoiceCloneError("预览音频生成超时，请稍后重试") from e
        except Exception as e:
            raise MinimaxVoiceCloneError(f"预览音频生成失败: {e}") from e

    # ------------------------------------------------------------------
    # 组合便捷方法: upload + create 一步完成
    # ------------------------------------------------------------------
    async def clone_voice(
        self,
        audio_buffer: io.BytesIO,
        filename: str,
        prefix: str,
        language: str = "zh",
    ) -> str:
        """Upload audio and create the voice (the two steps combined), returning voice_id."""
        file_id = await self.upload_file(audio_buffer, filename)
        safe_prefix = sanitize_minimax_voice_prefix(prefix, max_length=None)
        voice_id = f"custom{safe_prefix}"
        return await self.create_voice(
            file_id=file_id,
            voice_id=voice_id,
            voice_name=safe_prefix,
            language=language,
            voice_description=f"Cloned by N.E.K.O - {safe_prefix}",
        )


def build_request_prefix(prefix: str, provider_label: str) -> tuple[str, str]:
    """Normalize a user prefix for MiniMax clone registration."""
    import uuid

    original_prefix = str(prefix or '').strip()
    safe_prefix = sanitize_minimax_voice_prefix(
        original_prefix,
        max_length=MINIMAX_PREFIX_MAX_LENGTH,
    )
    if safe_prefix != original_prefix:
        logger.info(
            "%s 音色前缀已规范化: %r -> %r",
            provider_label,
            original_prefix,
            safe_prefix,
        )
    return original_prefix, f"{safe_prefix}{uuid.uuid4().hex[:8]}"


def voice_design_url(base_url: str) -> str:
    raw = base_url.strip().rstrip("/")
    if not raw:
        raise ValueError("MiniMax voice design base URL is required")
    if raw.endswith("/v1/voice_design"):
        return raw
    if raw.endswith("/v1"):
        return f"{raw}/voice_design"
    return f"{raw}/v1/voice_design"


async def design_voice(
    *,
    api_key: str,
    base_url: str,
    voice_prompt: str,
    preview_text: str,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[str, str | None]:
    """Create a reusable MiniMax voice from a text description."""
    payload = {"prompt": voice_prompt, "preview_text": preview_text}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    endpoint = voice_design_url(base_url)
    client = http_client or get_external_http_client()
    try:
        resp = await client.post(endpoint, headers=headers, json=payload, timeout=90)
    except httpx.RequestError as exc:
        host = urlparse(endpoint).netloc or endpoint
        raise MinimaxVoiceCloneError(
            f"MiniMax voice design network error while connecting to {host}: {exc}. "
            "Please check MiniMax base URL, DNS, and proxy settings."
        ) from exc
    if resp.status_code >= 400:
        raise MinimaxVoiceCloneError(
            f"MiniMax voice design upstream error ({resp.status_code}): {resp.text[:300]}"
        )
    try:
        data = resp.json()
    except Exception as exc:
        raise MinimaxVoiceCloneError("MiniMax voice design returned invalid JSON") from exc

    base_resp = data.get("base_resp") if isinstance(data, dict) else None
    if isinstance(base_resp, dict) and base_resp.get("status_code", 0) not in (0, "0", None):
        raise MinimaxVoiceCloneError(
            f"MiniMax voice design failed: "
            f"{base_resp.get('status_msg') or base_resp.get('message') or 'Unknown error'}"
        )

    returned_voice_id = first_nested_value(data, {"voice_id", "voiceId"})
    if not isinstance(returned_voice_id, str) or not returned_voice_id.strip():
        raise MinimaxVoiceCloneError("MiniMax voice design did not return voice_id")
    request_id = first_nested_value(data, {"request_id", "requestId", "trace_id", "traceId"})
    return returned_voice_id.strip(), str(request_id or "").strip() or None


# Compatibility names retained while callers migrate to the provider contract.
_build_minimax_request_prefix = build_request_prefix
_minimax_voice_design_url = voice_design_url
_minimax_design_voice = design_voice
VoiceRegistrationClient = MinimaxVoiceCloneClient
