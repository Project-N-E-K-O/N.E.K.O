# -*- coding: utf-8 -*-
"""语音克隆 API 封装模块 — MiniMax + Qwen/CosyVoice。

将各服务商的语音克隆逻辑集中管理，提供统一的异常基类和对称的客户端接口。

MiniMax 语音克隆（国服 + 国际服）:
  2 步流程: 上传音频 → 创建音色
  国服 base URL:   https://api.minimaxi.com
  国际服 base URL: https://api.minimax.io
  认证: Authorization: Bearer {api_key}

Qwen/CosyVoice 语音克隆:
  3 步流程: 上传到 tfLink → 获取直链 → DashScope 注册
  通过阿里云 DashScope SDK 调用
"""

import asyncio
import io
import binascii
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# ============================================================================
# 公共基类
# ============================================================================

class VoiceCloneError(Exception):
    """语音克隆基础错误"""


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


class MinimaxVoiceCloneError(VoiceCloneError):
    """MiniMax 语音克隆相关错误"""


def minimax_normalize_language(lang: str) -> str:
    """将项目内部语言代码转换为 MiniMax 语言代码。"""
    return _MINIMAX_LANGUAGE_CODE_MAP.get(lang.lower().strip(), 'zh')


def get_minimax_base_url(provider: str = 'minimax') -> str:
    """根据 provider 返回对应的 MiniMax API base URL。"""
    if provider == 'minimax_intl':
        return MINIMAX_INTL_BASE_URL
    return MINIMAX_DOMESTIC_BASE_URL


def get_minimax_storage_prefix(provider: str = 'minimax') -> str:
    """根据 provider 返回对应的 voice_storage key 前缀。"""
    if provider == 'minimax_intl':
        return MINIMAX_INTL_VOICE_STORAGE_KEY
    return MINIMAX_VOICE_STORAGE_KEY


class MinimaxVoiceCloneClient:
    """MiniMax 语音克隆客户端（国服 / 国际服通用）"""

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
        """上传音频到 MiniMax，返回 file_id。

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
        """创建音色，返回最终的 voice_id。

        Args:
            file_id: upload_file() 返回的 file_id
            voice_id: 用户自定义的 voice_id（可含 prefix）
            voice_name: 可选的显示名称
            language: MiniMax 语言代码 (zh / en / ja …)
            voice_description: 可选描述

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
        """使用 MiniMax T2A 接口生成预览音频，返回 MP3 bytes。"""
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
        """上传音频并创建音色（组合两步），返回 voice_id。"""
        file_id = await self.upload_file(audio_buffer, filename)
        voice_id = f"custom_{prefix}"
        return await self.create_voice(
            file_id=file_id,
            voice_id=voice_id,
            voice_name=prefix,
            language=language,
            voice_description=f"Cloned by N.E.K.O - {prefix}",
        )


# ============================================================================
# Qwen / CosyVoice 语音克隆
# ============================================================================

class QwenVoiceCloneError(VoiceCloneError):
    """Qwen/CosyVoice 语音克隆相关错误"""


def qwen_language_hints(ref_language: str) -> list[str]:
    """将 ref_language 转换为 DashScope CosyVoice 的 language_hints 参数。

    中文 (ch) → 空列表（DashScope 默认中文）
    其他语言 → [ref_language]
    """
    return [] if ref_language == 'ch' else [ref_language]


class QwenVoiceCloneClient:
    """Qwen/CosyVoice 语音克隆客户端（基于阿里云 DashScope SDK）。

    3 步流程:
      Step 1 - 上传音频到 tfLink 获取公网直链
      Step 2 - 通过 DashScope VoiceEnrollmentService 注册音色
      (Step 1+2 组合为 clone_voice 便捷方法，含重试)
    """

    # 重试配置
    MAX_RETRIES = 3
    RETRY_DELAY = 3  # 秒

    def __init__(self, api_key: str, tflink_upload_url: str):
        self.api_key = api_key
        self.tflink_upload_url = tflink_upload_url

    # ------------------------------------------------------------------
    # Step 1 - 上传音频到 tfLink，获取公网直链
    # ------------------------------------------------------------------
    async def upload_file(
        self,
        audio_buffer: io.BytesIO,
        filename: str,
        mime_type: str = 'audio/wav',
    ) -> str:
        """上传音频到 tfLink，返回可公网访问的临时 URL。

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
        target_model: str = "cosyvoice-v3.5-plus",
    ) -> tuple[str, str | None]:
        """通过 DashScope VoiceEnrollmentService 注册音色（同步调用）。

        Returns:
            (voice_id, request_id) 元组

        Raises:
            QwenVoiceCloneError
        """
        import dashscope
        from dashscope.audio.tts_v2 import VoiceEnrollmentService

        dashscope.api_key = self.api_key
        service = VoiceEnrollmentService()

        try:
            voice_id = service.create_voice(
                target_model=target_model,
                prefix=prefix,
                url=url,
                language_hints=language_hints,
            )
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
        target_model: str = "cosyvoice-v3.5-plus",
    ) -> tuple[str, str, str | None]:
        """上传音频并注册音色（组合两步 + 重试），返回 (voice_id, file_url, request_id)。

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
                is_download_failed = ("download audio failed" in error_detail or "415" in error_detail)

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
