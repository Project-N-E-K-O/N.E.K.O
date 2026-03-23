# -*- coding: utf-8 -*-
"""MiniMax 语音克隆 API 封装模块（国服 + 国际服）。

MiniMax 语音克隆为 2 步流程：
  Step 1 - 上传音频：POST /v1/files/upload  (purpose=voice_clone) → file_id
  Step 2 - 创建音色：POST /v1/voice_clone    (file_id + voice_id)  → voice_id

国服 base URL:   https://api.minimaxi.com  (注意多一个 i)
国际服 base URL: https://api.minimax.io
认证方式: Authorization: Bearer {api_key}（无需 GroupId）
"""

import io
import binascii
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# MiniMax 国服 API 端点（默认）
MINIMAX_DOMESTIC_BASE_URL = "https://api.minimaxi.com"
# MiniMax 国际服 API 端点
MINIMAX_INTL_BASE_URL = "https://api.minimax.io"

# 内部语言代码 → MiniMax 语言代码
_LANGUAGE_CODE_MAP = {
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


def minimax_normalize_language(lang: str) -> str:
    """将项目内部语言代码转换为 MiniMax 语言代码。"""
    return _LANGUAGE_CODE_MAP.get(lang.lower().strip(), 'zh')


class MinimaxVoiceCloneError(Exception):
    """MiniMax 语音克隆相关错误"""


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
