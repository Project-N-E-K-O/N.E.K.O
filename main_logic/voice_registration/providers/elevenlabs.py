"""ElevenLabs clone and two-stage design registration adapter."""

import io
import json

import httpx

from utils.tts.providers.elevenlabs import (
    ELEVENLABS_TTS_DEFAULT_MODEL,
    ELEVENLABS_TTS_VOICE_PREFIX,
)


VOICE_DESIGN_PREVIEW_TEXT = (
    "Hello! This is a preview of your designed voice. I can read your stories, chat "
    "with you about your day, and keep you company whenever you would like a friendly "
    "voice nearby. How do I sound to you so far?"
)


class ElevenLabsUpstreamError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


async def get_base_url(config_manager) -> str:
    return "https://api.elevenlabs.io"


def prefixed_voice_id(raw_voice_id: str) -> str:
    raw = (raw_voice_id or '').strip()
    if raw.startswith(ELEVENLABS_TTS_VOICE_PREFIX):
        return raw
    return f'{ELEVENLABS_TTS_VOICE_PREFIX}{raw}'


def raw_voice_id(voice_id: str) -> str:
    raw = (voice_id or '').strip()
    if raw.startswith(ELEVENLABS_TTS_VOICE_PREFIX):
        return raw[len(ELEVENLABS_TTS_VOICE_PREFIX):].strip()
    return raw


def raise_for_response(response: httpx.Response, action: str) -> None:
    if response.status_code < 400:
        return
    message = f"ElevenLabs {action} API error ({response.status_code}): {response.text[:300]}"
    if response.status_code >= 500:
        raise ElevenLabsUpstreamError(response.status_code, message)
    raise ValueError(message)


async def clone_voice(
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
        response = await client.post(url, headers=headers, data=data, files=files)
    raise_for_response(response, "voice clone")
    try:
        payload = response.json()
    except Exception as exc:
        raise ElevenLabsUpstreamError(
            502,
            "ElevenLabs returned invalid JSON while adding voice",
        ) from exc
    voice_id = payload.get("voice_id") or payload.get("voiceId") or ""
    if not voice_id:
        raise ElevenLabsUpstreamError(502, "ElevenLabs did not return voice_id")
    return prefixed_voice_id(voice_id)


async def design_previews(
    *,
    api_key: str,
    base_url: str,
    voice_description: str,
) -> list[dict]:
    """Generate ElevenLabs design candidates without persisting one."""
    url = f"{base_url.rstrip('/')}/v1/text-to-voice/design"
    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "voice_description": voice_description,
        "text": VOICE_DESIGN_PREVIEW_TEXT,
    }
    async with httpx.AsyncClient(timeout=60, proxy=None, trust_env=False) as client:
        response = await client.post(url, headers=headers, json=payload)
    raise_for_response(response, "voice design")
    try:
        data = response.json()
    except Exception as exc:
        raise ElevenLabsUpstreamError(
            502,
            "ElevenLabs returned invalid JSON while designing voice",
        ) from exc
    previews = data.get("previews") if isinstance(data, dict) else None
    if not isinstance(previews, list) or not previews:
        raise ElevenLabsUpstreamError(502, "ElevenLabs did not return voice previews")
    return previews


async def create_voice_from_preview(
    *,
    api_key: str,
    base_url: str,
    voice_name: str,
    voice_description: str,
    generated_voice_id: str,
) -> str:
    """Persist one ElevenLabs design candidate as a reusable voice."""
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
        response = await client.post(url, headers=headers, json=payload)
    raise_for_response(response, "voice design create")
    try:
        response_payload = response.json()
    except Exception as exc:
        raise ElevenLabsUpstreamError(
            502,
            "ElevenLabs returned invalid JSON while creating designed voice",
        ) from exc
    voice_id = response_payload.get("voice_id") or response_payload.get("voiceId") or ""
    if not voice_id:
        raise ElevenLabsUpstreamError(502, "ElevenLabs did not return voice_id")
    return prefixed_voice_id(voice_id)


async def synthesize_preview(
    config_manager,
    voice_id: str,
    text: str,
    *,
    base_url: str | None = None,
) -> tuple[bytes, str]:
    api_key = config_manager.get_tts_api_key('elevenlabs')
    if not api_key:
        return b'', 'ELEVENLABS_API_KEY_MISSING'
    normalized_voice_id = raw_voice_id(voice_id)
    if not normalized_voice_id:
        return b'', 'TTS_VOICE_ID_MISSING'

    base_url = (base_url or await get_base_url(config_manager)).rstrip('/')
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
    url = f"{base_url}/v1/text-to-speech/{normalized_voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    params = {"output_format": "mp3_44100_128"}
    async with httpx.AsyncClient(timeout=30, proxy=None, trust_env=False) as client:
        response = await client.post(url, headers=headers, params=params, json=payload)
    raise_for_response(response, "preview")
    return response.content, ''


# Compatibility names retained while callers migrate to the provider contract.
ELEVENLABS_VOICE_DESIGN_PREVIEW_TEXT = VOICE_DESIGN_PREVIEW_TEXT
_get_elevenlabs_base_url = get_base_url
_prefixed_elevenlabs_voice_id = prefixed_voice_id
_raw_elevenlabs_voice_id = raw_voice_id
_raise_for_elevenlabs_response = raise_for_response
_elevenlabs_clone_voice = clone_voice
_elevenlabs_design_previews = design_previews
_elevenlabs_create_voice_from_preview = create_voice_from_preview
_elevenlabs_synthesize_preview = synthesize_preview
