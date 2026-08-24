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

"""Provider-specific voice helpers: ElevenLabs clone/preview and local
voice-clone TTS detection.

Voice Design provider behavior is implemented in :mod:`utils.voice_design`.
"""

import json
import io
import httpx
from utils.tts.providers.elevenlabs import (
    ELEVENLABS_TTS_DEFAULT_MODEL,
    ELEVENLABS_TTS_VOICE_PREFIX,
)


class ElevenLabsUpstreamError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


async def _get_elevenlabs_base_url(config_manager) -> str:
    return "https://api.elevenlabs.io"


def _config_value_is_enabled(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'1', 'true', 'yes', 'on'}:
            return True
        if normalized in {'0', 'false', 'no', 'off', ''}:
            return False
    return bool(value)


def _prefixed_elevenlabs_voice_id(raw_voice_id: str) -> str:
    raw = (raw_voice_id or '').strip()
    if raw.startswith(ELEVENLABS_TTS_VOICE_PREFIX):
        return raw
    return f'{ELEVENLABS_TTS_VOICE_PREFIX}{raw}'


def _raw_elevenlabs_voice_id(voice_id: str) -> str:
    raw = (voice_id or '').strip()
    if raw.startswith(ELEVENLABS_TTS_VOICE_PREFIX):
        return raw[len(ELEVENLABS_TTS_VOICE_PREFIX):].strip()
    return raw


def _raise_for_elevenlabs_response(resp: httpx.Response, action: str) -> None:
    if resp.status_code < 400:
        return
    message = f"ElevenLabs {action} API error ({resp.status_code}): {resp.text[:300]}"
    if resp.status_code >= 500:
        raise ElevenLabsUpstreamError(resp.status_code, message)
    raise ValueError(message)


async def _elevenlabs_clone_voice(
    *,
    api_key: str,
    base_url: str,
    audio_buffer: io.BytesIO,
    filename: str,
    name: str,
) -> str:
    audio_buffer.seek(0)
    safe_name = (name or 'NEKO Voice').strip()[:100] or 'NEKO Voice'
    url = f"{base_url.rstrip('/')}/v1/voices/add"
    headers = {"xi-api-key": api_key}
    data = {
        "name": safe_name,
        "description": "Created from NEKO voice clone",
        "labels": json.dumps({"source": "NEKO"}),
    }
    files = [("files", (filename or "voice.wav", audio_buffer, "application/octet-stream"))]
    async with httpx.AsyncClient(timeout=60, proxy=None, trust_env=False) as client:
        resp = await client.post(url, headers=headers, data=data, files=files)
    _raise_for_elevenlabs_response(resp, "voice clone")
    try:
        payload = resp.json()
    except Exception as exc:
        raise ElevenLabsUpstreamError(502, "ElevenLabs returned invalid JSON while adding voice") from exc
    raw_voice_id = payload.get("voice_id") or payload.get("voiceId") or ""
    if not raw_voice_id:
        raise ElevenLabsUpstreamError(502, "ElevenLabs did not return voice_id")
    return _prefixed_elevenlabs_voice_id(raw_voice_id)


def _is_local_voice_clone_tts_config(tts_config: dict, core_config: dict | None = None) -> bool:
    provider = str((core_config or {}).get('ttsModelProvider') or '').strip()
    if provider == 'vllm_omni':
        return False
    base_url = _local_voice_clone_tts_base_url(tts_config, core_config)
    return bool(tts_config.get('is_custom') and base_url.startswith(('ws://', 'wss://')))


def _local_voice_clone_tts_base_url(tts_config: dict, core_config: dict | None = None) -> str:
    return str(
        tts_config.get('base_url')
        or tts_config.get('url')
        or (core_config or {}).get('ttsModelUrl')
        or (core_config or {}).get('TTS_MODEL_URL')
        or ''
    ).strip()


async def _elevenlabs_synthesize_preview(
    config_manager,
    voice_id: str,
    text: str,
    *,
    base_url: str | None = None,
) -> tuple[bytes, str]:
    api_key = config_manager.get_tts_api_key('elevenlabs')
    if not api_key:
        return b'', 'ELEVENLABS_API_KEY_MISSING'
    raw_voice_id = _raw_elevenlabs_voice_id(voice_id)
    if not raw_voice_id:
        return b'', 'TTS_VOICE_ID_MISSING'

    # 优先使用传入的 base_url，否则获取默认值并去除末尾斜杠
    base_url = (base_url or await _get_elevenlabs_base_url(config_manager)).rstrip('/')

    payload = {
        "text": text,
        "model_id": ELEVENLABS_TTS_DEFAULT_MODEL,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }
    url = f"{base_url}/v1/text-to-speech/{raw_voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    params = {"output_format": "mp3_44100_128"}
    async with httpx.AsyncClient(timeout=30, proxy=None, trust_env=False) as client:
        resp = await client.post(url, headers=headers, params=params, json=payload)
    _raise_for_elevenlabs_response(resp, "preview")
    return resp.content, ''
