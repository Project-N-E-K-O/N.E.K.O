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

"""Voice cloning API wrapper module — MiniMax + Qwen/CosyVoice.

Centralizes each vendor's voice-clone logic, providing a unified exception base class
and symmetric client interfaces.

MiniMax voice cloning (CN + international):
  2-step flow: upload audio → create voice
  CN base URL:            https://api.minimaxi.com
  International base URL: https://api.minimax.io
  Auth: Authorization: Bearer {api_key}

Qwen/CosyVoice voice cloning:
  3-step flow: upload to tfLink → get direct link → DashScope registration
  Called via the Alibaba Cloud DashScope SDK
"""

import asyncio
import base64
import io
import binascii
import logging
from typing import Optional

import httpx

from utils.dashscope_region import DASHSCOPE_GLOBAL_LOCK, configure_dashscope_sdk_urls
from utils.tts.providers.minimax import (
    MINIMAX_DOMESTIC_BASE_URL,
    MINIMAX_INTL_BASE_URL,
    MINIMAX_INTL_VOICE_STORAGE_KEY,
    MINIMAX_PREFIX_MAX_LENGTH,
    MINIMAX_VOICE_STORAGE_KEY,
    get_minimax_base_url,
    get_minimax_storage_prefix,
    sanitize_minimax_voice_prefix,
)  # noqa: F401 - intentional compatibility re-exports for existing Clone callers.

logger = logging.getLogger(__name__)

# Compatibility re-exports for established Clone callers. New shared provider
# code must import from ``utils.tts.providers.minimax`` or ``.mimo`` directly.


# ============================================================================
# 公共基类
# ============================================================================

class VoiceCloneError(Exception):
    """Voice cloning base error"""


# ============================================================================
# MiniMax 语音克隆
# ============================================================================

# 内部语言代码 → MiniMax 语言代码
_MINIMAX_LANGUAGE_CODE_MAP = {
    'ch': 'zh', 'zh': 'zh',
    'en': 'en',
    'ja': 'ja', 'jp': 'ja',
    'ko': 'ko',
    'de': 'de', 'fr': 'fr', 'ru': 'ru',
    'es': 'es', 'it': 'it', 'pt': 'pt',
}

class MinimaxVoiceCloneError(VoiceCloneError):
    """MiniMax voice-clone related error"""


def minimax_normalize_language(lang: str) -> str:
    """Convert the project's internal language codes to MiniMax language codes."""
    return _MINIMAX_LANGUAGE_CODE_MAP.get(lang.lower().strip(), 'zh')



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


# ============================================================================
# Xiaomi MiMo 语音克隆
# ============================================================================
#
# 与 MiniMax/CosyVoice/ElevenLabs 的「上传样本 → 远端注册 → 拿 voice_id」不同，MiMo 的
# voiceclone（mimo-v2.5-tts-voiceclone）没有注册步骤（已核实官方文档：无 create-voice /
# 远端 voice_id）：参考音频每次合成请求内联在 OpenAI 兼容 chat-completions 的 ``audio.voice``
# 里（``data:audio/...;base64,...``）。
#
# **对偶 MiniMax 的存储模型**：MiniMax 在 voice_storage.json 的 voice_meta 里存远端 voice_id
# 作克隆身份；MiMo 在同一处存**参考音频本身（base64）**作克隆身份——整段落进 voice_meta、
# 随 voice_storage.json 云同步，不另起本地文件存储。enrollment 时用 MiMo 做一次校验性合成
# 确认 key + 样本可用（对偶其它家真打远端注册接口）；试听（synthesize_preview）同样内联样本，
# 对偶 MiniMax 的预览。dispatch 由 mimo provider 按 voice_meta.provider 选中后读出样本内联。

# voice_storage 中标识 MiMo 克隆音色的前缀（按 MiMo API key 末 8 位分桶）
# MiMo 校验 / 试听用 wav（自包含、非流式一次性返回，便于直接取音频）；运行时 worker 才用
# pcm16 流式。Codex review #1851：非流式请求不应再要 pcm16 裸流。
_MIMO_PREVIEW_AUDIO_FORMAT = 'wav'


class MimoVoiceCloneError(VoiceCloneError):
    """MiMo voice-clone related error"""


def _extract_mimo_audio_bytes(payload: dict) -> bytes:
    """Pull base64 audio out of a MiMo chat-completions (non-stream) response.

    Mirrors the worker's extractor but lives here so utils stays off main_logic.
    Returns decoded bytes, or b'' when no audio field is present.
    """
    candidates: list = [payload.get('audio')]
    for choice in payload.get('choices') or []:
        if isinstance(choice, dict):
            candidates.append((choice.get('message') or {}).get('audio'))
            candidates.append(choice.get('audio'))
    for cand in candidates:
        b64 = ''
        if isinstance(cand, str):
            b64 = cand
        elif isinstance(cand, dict):
            b64 = cand.get('data') or cand.get('audio') or cand.get('content') or ''
        if not b64:
            continue
        try:
            return base64.b64decode(b64)
        except (binascii.Error, ValueError, TypeError):
            # 上游返回了非字符串/非 bytes 的 audio 字段也不应冒泡，按"无音频"继续尝试下一候选。
            continue
    return b''


class MimoVoiceCloneClient:
    """MiMo voice-clone enrollment + preview client.

    MiMo has no remote voice registration; ``validate_sample`` confirms the
    reference sample + API key actually synthesize via the voiceclone model (one
    short non-stream request), so a bad key / unsupported format / oversized
    sample fails fast at enrollment instead of going silent at runtime.
    ``synthesize_preview`` returns audible bytes for the voice-list preview
    button (dual to MiniMax's ``synthesize_preview``).
    """

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        self.api_key = api_key
        self.base_url = base_url or None

    def _build_payload(self, audio_bytes: bytes, mime_type: str, text: str) -> dict:
        from utils.tts.providers.mimo import MIMO_TTS_VOICECLONE_MODEL, mimo_voice_clone_data_uri
        return {
            'model': MIMO_TTS_VOICECLONE_MODEL,
            'messages': [{'role': 'assistant', 'content': text}],
            'audio': {
                # 非流式取一次性音频：wav 自包含，不要 pcm16 裸流（Codex review）。
                'format': _MIMO_PREVIEW_AUDIO_FORMAT,
                'voice': mimo_voice_clone_data_uri(audio_bytes, mime_type),
            },
            'stream': False,
        }

    async def _post(self, payload: dict) -> dict:
        from utils.tts.providers.mimo import mimo_chat_completions_url
        url = mimo_chat_completions_url(self.base_url)
        headers = {'Content-Type': 'application/json', 'api-key': self.api_key}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as e:
            raise MimoVoiceCloneError("MiMo 请求超时，请稍后重试") from e
        except Exception as e:
            raise MimoVoiceCloneError(f"MiMo 请求失败: {e}") from e
        if resp.status_code != 200:
            raise MimoVoiceCloneError(
                f"MiMo 请求失败: HTTP {resp.status_code}, {resp.text[:300]}"
            )
        try:
            data = resp.json()
        except ValueError as e:
            raise MimoVoiceCloneError("MiMo 返回了无法解析的响应") from e
        if isinstance(data, dict) and data.get('error'):
            err = data['error']
            msg = err.get('message') if isinstance(err, dict) else str(err)
            raise MimoVoiceCloneError(f"MiMo 请求失败: {msg}")
        return data if isinstance(data, dict) else {}

    async def validate_sample(
        self,
        audio_bytes: bytes,
        mime_type: str = 'audio/wav',
        *,
        sample_text: str = '你好呀，很高兴认识你。',
    ) -> None:
        """Synthesize a short line with the reference sample to confirm it works.

        Confirms the call actually *produced audio* (not just HTTP 200): if the
        upstream returns success but an empty/missing audio field the sample is
        unusable, and enrollment must fail here rather than going silent at
        runtime / preview.

        Raises:
            MimoVoiceCloneError on a non-200 response / network failure / no audio.
        """
        data = await self._post(self._build_payload(audio_bytes, mime_type, sample_text))
        if not _extract_mimo_audio_bytes(data):
            raise MimoVoiceCloneError("MiMo 校验未产出音频，参考样本可能不可用")

    async def synthesize_preview(
        self,
        audio_bytes: bytes,
        mime_type: str = 'audio/wav',
        *,
        text: str = '你好呀，很高兴认识你。',
    ) -> bytes:
        """Synthesize a preview line with the reference sample, returning wav bytes.

        Raises:
            MimoVoiceCloneError on failure / when no audio is returned.
        """
        data = await self._post(self._build_payload(audio_bytes, mime_type, text))
        audio = _extract_mimo_audio_bytes(data)
        if not audio:
            raise MimoVoiceCloneError("MiMo 预览成功但未返回音频")
        return audio


# ============================================================================
# Qwen / CosyVoice 语音克隆
# ============================================================================

class QwenVoiceCloneError(VoiceCloneError):
    """Qwen/CosyVoice voice-clone related error"""


def qwen_language_hints(ref_language: str) -> list[str]:
    """Convert ref_language to DashScope CosyVoice's language_hints parameter.

    Chinese (ch) → empty list (DashScope defaults to Chinese)
    Other languages → [ref_language]
    """
    return [] if ref_language == 'ch' else [ref_language]


class QwenVoiceCloneClient:
    """Qwen/CosyVoice voice cloning client (based on the Alibaba Cloud DashScope SDK).

    3-step flow:
      Step 1 - upload the audio to tfLink to get a public direct link
      Step 2 - register the voice via DashScope VoiceEnrollmentService
      (Steps 1+2 combine into the clone_voice convenience method, with retries)
    """

    # 重试配置
    MAX_RETRIES = 3
    RETRY_DELAY = 3  # 秒

    def __init__(self, api_key: str, tflink_upload_url: str, dashscope_base_url: str = ""):
        self.api_key = api_key
        self.tflink_upload_url = tflink_upload_url
        self.dashscope_base_url = dashscope_base_url

    # ------------------------------------------------------------------
    # Step 1 - 上传音频到 tfLink，获取公网直链
    # ------------------------------------------------------------------
    async def upload_file(
        self,
        audio_buffer: io.BytesIO,
        filename: str,
        mime_type: str = 'audio/wav',
    ) -> str:
        """Upload audio to tfLink, returning a temporary publicly accessible URL.

        Raises:
            QwenVoiceCloneError
        """
        file_size = len(audio_buffer.getvalue())
        if file_size > 100 * 1024 * 1024:  # 100MB
            raise QwenVoiceCloneError('文件大小超过100MB，超过tfLink的限制')

        audio_buffer.seek(0)
        files = {'file': (filename, audio_buffer, mime_type)}
        headers = {'Accept': 'application/json'}

        logger.info("正在上传文件到tfLink，文件名: %s, 大小: %d bytes, MIME类型: %s",
                     filename, file_size, mime_type)

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(self.tflink_upload_url, files=files, headers=headers)

                if resp.status_code != 200:
                    raise QwenVoiceCloneError(
                        f'上传到tfLink失败，状态码: {resp.status_code}, 详情: {resp.text[:200]}'
                    )

                try:
                    data = resp.json()
                except ValueError as e:
                    raise QwenVoiceCloneError(
                        f'上传成功但响应格式无法解析: {resp.text[:200]}'
                    ) from e

                logger.info("tfLink原始响应: %s", data)

                # 获取下载链接
                tmp_url = None
                possible_keys = ['downloadLink', 'download_link', 'url', 'direct_link', 'link', 'download_url']
                for key in possible_keys:
                    if key in data:
                        tmp_url = data[key]
                        logger.info("找到下载链接键: %s", key)
                        break

                if not tmp_url:
                    raise QwenVoiceCloneError(f'上传成功但无法从响应中提取URL: {data}')

                if not tmp_url.startswith(('http://', 'https://')):
                    raise QwenVoiceCloneError(f'无效的URL格式: {tmp_url}')

                # 测试URL是否可访问
                test_resp = await client.head(tmp_url, timeout=10)
                if test_resp.status_code >= 400:
                    raise QwenVoiceCloneError(
                        f'生成的临时URL无法访问: {tmp_url}, 状态码: {test_resp.status_code}'
                    )

                logger.info("成功获取临时URL并验证可访问性: %s", tmp_url)
                return tmp_url

        except QwenVoiceCloneError:
            raise
        except httpx.TimeoutException as e:
            raise QwenVoiceCloneError("上传音频到tfLink超时，请稍后重试") from e
        except Exception as e:
            raise QwenVoiceCloneError(f"上传音频到tfLink失败: {e}") from e

    # ------------------------------------------------------------------
    # Step 2 - 通过 DashScope 注册音色
    # ------------------------------------------------------------------
    def create_voice(
        self,
        prefix: str,
        url: str,
        language_hints: list[str],
        target_model: str | None = None,
    ) -> tuple[str, str | None]:
        """Register the voice via DashScope VoiceEnrollmentService (sync call).

        Returns:
            (voice_id, request_id) tuple

        Raises:
            QwenVoiceCloneError
        """
        import dashscope
        from dashscope.audio.tts_v2 import VoiceEnrollmentService
        from utils.api_config_loader import (
            cosyvoice_model_supports_language_hints,
            get_cosyvoice_clone_model,
        )

        if target_model is None:
            target_model = get_cosyvoice_clone_model(self.dashscope_base_url)

        kwargs: dict = dict(
            target_model=target_model,
            prefix=prefix,
            url=url,
        )
        if language_hints and cosyvoice_model_supports_language_hints(target_model):
            kwargs["language_hints"] = language_hints

        # 写 module-global + 构造 service + service.create_voice 整段都拿
        # DASHSCOPE_GLOBAL_LOCK：clone_voice 由 asyncio.to_thread 跑在工作线程，
        # 同进程多个 clone 请求并发时会在 "set global → SDK 调用" 之间互相
        # 覆盖 key/地域，请求带着别人的凭证发出去 (Codex P1 #3258691457)。
        # TTS worker / preview 也共用这把锁。
        try:
            with DASHSCOPE_GLOBAL_LOCK:
                dashscope.api_key = self.api_key
                configure_dashscope_sdk_urls(
                    dashscope, self.dashscope_base_url, websocket_path="inference"
                )
                service = VoiceEnrollmentService()
                voice_id = service.create_voice(**kwargs)
                request_id = service.get_last_request_id()
            logger.info("CosyVoice 音色注册成功: voice_id=%s", voice_id)
            return voice_id, request_id
        except Exception as e:
            raise QwenVoiceCloneError(str(e)) from e

    # ------------------------------------------------------------------
    # 组合便捷方法: upload + create，含重试
    # ------------------------------------------------------------------
    async def clone_voice(
        self,
        audio_buffer: io.BytesIO,
        filename: str,
        prefix: str,
        language_hints: list[str],
        mime_type: str = 'audio/wav',
        target_model: str | None = None,
    ) -> tuple[str, str, str | None]:
        """Upload audio and register the voice (two steps combined + retries), returning (voice_id, file_url, request_id).

        Raises:
            QwenVoiceCloneError
        """
        tmp_url = await self.upload_file(audio_buffer, filename, mime_type)

        last_error: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                logger.info("开始音色注册（尝试 %d/%d），使用URL: %s",
                            attempt + 1, self.MAX_RETRIES, tmp_url)
                voice_id, request_id = await asyncio.to_thread(
                    self.create_voice,
                    prefix=prefix,
                    url=tmp_url,
                    language_hints=language_hints,
                    target_model=target_model,
                )
                return voice_id, tmp_url, request_id

            except QwenVoiceCloneError as e:
                last_error = e
                error_detail = str(e)
                is_timeout = any(kw in error_detail.lower() for kw in
                                 ["responsetimeout", "response timeout", "timeout"])
                is_download_failed = ("download audio failed" in error_detail.lower() or "415" in error_detail)

                if (is_timeout or is_download_failed) and attempt < self.MAX_RETRIES - 1:
                    label = '超时' if is_timeout else '文件下载失败'
                    logger.warning("检测到%s错误，等待 %d 秒后重试...", label, self.RETRY_DELAY)
                    await asyncio.sleep(self.RETRY_DELAY)
                    continue

                # 最后一次尝试或非可重试错误
                if is_timeout:
                    raise QwenVoiceCloneError(
                        f'音色注册超时，已尝试{self.MAX_RETRIES}次'
                    ) from e
                elif is_download_failed:
                    raise QwenVoiceCloneError(
                        f'音色注册失败: 无法下载音频文件，已尝试{self.MAX_RETRIES}次'
                    ) from e
                else:
                    raise

        # 理论上不会到这里，但以防万一
        raise last_error or QwenVoiceCloneError("音色注册失败: 未知错误")  # type: ignore[misc]
