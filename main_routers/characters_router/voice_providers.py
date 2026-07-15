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

"""Provider-specific voice helpers: ElevenLabs clone/design/preview,
Minimax request prefix, local voice-clone TTS detection.

Split out of the former monolithic ``main_routers/characters_router.py``.
"""

from ._shared import logger

import json
import io
import httpx
from utils.tts.providers.elevenlabs import (
    ELEVENLABS_TTS_DEFAULT_MODEL,
    ELEVENLABS_TTS_VOICE_PREFIX,
)
from utils.voice_clone import (
    sanitize_minimax_voice_prefix,
    MINIMAX_PREFIX_MAX_LENGTH,
)


class ElevenLabsUpstreamError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def _build_minimax_request_prefix(prefix: str, provider_label: str) -> tuple[str, str]:
    """Normalize the user-entered prefix into a safe prefix that MiniMax accepts."""
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


# ── ElevenLabs voice design (text description → generated voice) ──────────────
# Voice design is the third voice source (besides preset/clone): a text prompt is
# turned into voice previews, the user picks one, and create-from-preview lands it
# as a normal ElevenLabs voice_id (stored with source='design'). Dispatch then
# reuses the existing ElevenLabs clone path (voice_meta.provider=='elevenlabs'),
# so no separate worker is needed (design doc §7).
ELEVENLABS_VOICE_DESIGN_DESC_MIN = 20


ELEVENLABS_VOICE_DESIGN_DESC_MAX = 1000


# ElevenLabs voice-design previews require a ``text`` between 100 and 1000 chars to
# synthesize audible samples. ``auto_generate_text`` only returns generated voice ids
# (no audio), which would yield empty/unplayable previews — so we always pass a fixed
# preview line instead (must stay ≥ 100 chars).
ELEVENLABS_VOICE_DESIGN_PREVIEW_TEXT = (
    "Hello! This is a preview of your designed voice. I can read your stories, chat "
    "with you about your day, and keep you company whenever you would like a friendly "
    "voice nearby. How do I sound to you so far?"
)


async def _elevenlabs_design_previews(
    *,
    api_key: str,
    base_url: str,
    voice_description: str,
) -> list[dict]:
    """Call POST /v1/text-to-voice/design — returns the list of voice previews.

    Each preview has ``generated_voice_id`` (the handle for create-from-preview)
    and ``audio_base_64`` (an mp3 sample for the user to audition). We let
    ElevenLabs auto-generate the preview text so the caller only supplies a
    description.
    """
    url = f"{base_url.rstrip('/')}/v1/text-to-voice/design"
    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "voice_description": voice_description,
        # 显式给 text（≥100 chars）而非 auto_generate_text，确保返回可试听的 audio_base_64。
        "text": ELEVENLABS_VOICE_DESIGN_PREVIEW_TEXT,
    }
    async with httpx.AsyncClient(timeout=60, proxy=None, trust_env=False) as client:
        resp = await client.post(url, headers=headers, json=payload)
    _raise_for_elevenlabs_response(resp, "voice design")
    try:
        data = resp.json()
    except Exception as exc:
        raise ElevenLabsUpstreamError(502, "ElevenLabs returned invalid JSON while designing voice") from exc
    previews = data.get("previews") if isinstance(data, dict) else None
    if not isinstance(previews, list) or not previews:
        raise ElevenLabsUpstreamError(502, "ElevenLabs did not return voice previews")
    return previews


async def _elevenlabs_create_voice_from_preview(
    *,
    api_key: str,
    base_url: str,
    voice_name: str,
    voice_description: str,
    generated_voice_id: str,
) -> str:
    """Call POST /v1/text-to-voice — persist a designed preview into a voice_id."""
    safe_name = (voice_name or 'NEKO Designed Voice').strip()[:100] or 'NEKO Designed Voice'
    url = f"{base_url.rstrip('/')}/v1/text-to-voice"
    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "voice_name": safe_name,
        "voice_description": voice_description,
        "generated_voice_id": generated_voice_id,
        "labels": {"source": "NEKO"},
    }
    async with httpx.AsyncClient(timeout=60, proxy=None, trust_env=False) as client:
        resp = await client.post(url, headers=headers, json=payload)
    _raise_for_elevenlabs_response(resp, "voice design create")
    try:
        payload_resp = resp.json()
    except Exception as exc:
        raise ElevenLabsUpstreamError(502, "ElevenLabs returned invalid JSON while creating designed voice") from exc
    raw_voice_id = payload_resp.get("voice_id") or payload_resp.get("voiceId") or ""
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
