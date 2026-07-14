"""Xiaomi MiMo clone and design registration adapter."""

import base64
import binascii
from typing import Optional

import httpx

from .base import VoiceCloneError


MIMO_VOICE_STORAGE_KEY = '__MIMO__'
_MIMO_PREVIEW_AUDIO_FORMAT = 'wav'


class MimoVoiceCloneError(VoiceCloneError):
    """MiMo voice registration error."""


def _extract_mimo_audio_bytes(payload: dict) -> bytes:
    """Pull base64 audio out of a non-streaming MiMo response."""
    candidates: list = [payload.get('audio')]
    for choice in payload.get('choices') or []:
        if isinstance(choice, dict):
            candidates.append((choice.get('message') or {}).get('audio'))
            candidates.append(choice.get('audio'))
    for candidate in candidates:
        audio_b64 = ''
        if isinstance(candidate, str):
            audio_b64 = candidate
        elif isinstance(candidate, dict):
            audio_b64 = candidate.get('data') or candidate.get('audio') or candidate.get('content') or ''
        if not audio_b64:
            continue
        try:
            return base64.b64decode(audio_b64)
        except (binascii.Error, ValueError, TypeError):
            continue
    return b''


class MimoVoiceCloneClient:
    """MiMo clone and design client.

    MiMo has no remote enrollment endpoint. Clone requests carry the reference
    sample and design requests carry the description on every synthesis call.
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
                'format': _MIMO_PREVIEW_AUDIO_FORMAT,
                'voice': mimo_voice_clone_data_uri(audio_bytes, mime_type),
            },
            'stream': False,
        }

    def _build_design_payload(self, design_prompt: str, text: str) -> dict:
        from utils.tts.providers.mimo import MIMO_TTS_VOICEDESIGN_MODEL

        return {
            'model': MIMO_TTS_VOICEDESIGN_MODEL,
            'messages': [
                {'role': 'user', 'content': str(design_prompt or '').strip()},
                {'role': 'assistant', 'content': text},
            ],
            'audio': {'format': _MIMO_PREVIEW_AUDIO_FORMAT},
            'stream': False,
        }

    async def _post(self, payload: dict) -> dict:
        from utils.tts.providers.mimo import mimo_chat_completions_url

        url = mimo_chat_completions_url(self.base_url)
        headers = {'Content-Type': 'application/json', 'api-key': self.api_key}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise MimoVoiceCloneError("MiMo 请求超时，请稍后重试") from exc
        except Exception as exc:
            raise MimoVoiceCloneError(f"MiMo 请求失败: {exc}") from exc
        if response.status_code != 200:
            raise MimoVoiceCloneError(
                f"MiMo 请求失败: HTTP {response.status_code}, {response.text[:300]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise MimoVoiceCloneError("MiMo 返回了无法解析的响应") from exc
        if isinstance(data, dict) and data.get('error'):
            error = data['error']
            message = error.get('message') if isinstance(error, dict) else str(error)
            raise MimoVoiceCloneError(f"MiMo 请求失败: {message}")
        return data if isinstance(data, dict) else {}

    async def validate_sample(
        self,
        audio_bytes: bytes,
        mime_type: str = 'audio/wav',
        *,
        sample_text: str = '你好呀，很高兴认识你。',
    ) -> None:
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
        data = await self._post(self._build_payload(audio_bytes, mime_type, text))
        audio = _extract_mimo_audio_bytes(data)
        if not audio:
            raise MimoVoiceCloneError("MiMo 预览成功但未返回音频")
        return audio

    async def validate_design_prompt(
        self,
        design_prompt: str,
        *,
        sample_text: str = '你好呀，很高兴认识你。',
    ) -> None:
        prompt = str(design_prompt or '').strip()
        if not prompt:
            raise MimoVoiceCloneError("MiMo 设计音色描述不能为空")
        data = await self._post(self._build_design_payload(prompt, sample_text))
        if not _extract_mimo_audio_bytes(data):
            raise MimoVoiceCloneError("MiMo 设计音色校验未产出音频")

    async def synthesize_design_preview(
        self,
        design_prompt: str,
        *,
        text: str = '你好呀，很高兴认识你。',
    ) -> bytes:
        prompt = str(design_prompt or '').strip()
        if not prompt:
            raise MimoVoiceCloneError("MiMo 设计音色描述不能为空")
        data = await self._post(self._build_design_payload(prompt, text))
        audio = _extract_mimo_audio_bytes(data)
        if not audio:
            raise MimoVoiceCloneError("MiMo 设计音色预览成功但未返回音频")
        return audio


# The provider contract uses the same client for clone and design because MiMo
# exposes both operations through chat-completions rather than enrollment IDs.
VoiceRegistrationClient = MimoVoiceCloneClient
