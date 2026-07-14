"""Provider clients for reusable voices created from text descriptions.

This module is the Voice Design counterpart to :mod:`utils.voice_clone`.
Clone clients remain in their existing main-branch module; design-only
endpoints, payloads, response parsing, and errors live here.
"""

from __future__ import annotations

import base64
import binascii
from typing import Optional
from urllib.parse import urlparse

import httpx

from utils.http.external_client import get_external_http_client
from utils.tts.providers.elevenlabs import ELEVENLABS_TTS_VOICE_PREFIX


COSYVOICE_VOICE_DESIGN_DEFAULT_MEDIA_TYPE = "audio/wav"
ELEVENLABS_VOICE_DESIGN_PREVIEW_TEXT = (
    "Hello! This is a preview of your designed voice. I can read your stories, chat "
    "with you about your day, and keep you company whenever you would like a friendly "
    "voice nearby. How do I sound to you so far?"
)
_MIMO_PREVIEW_AUDIO_FORMAT = "wav"


class VoiceDesignError(Exception):
    """Base error for provider Voice Design failures."""


class CosyVoiceDesignError(VoiceDesignError):
    """CosyVoice Voice Design request failed."""


class MiniMaxVoiceDesignError(VoiceDesignError):
    """MiniMax Voice Design request failed."""


class ElevenLabsVoiceDesignError(VoiceDesignError):
    """ElevenLabs Voice Design request failed."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class MimoVoiceDesignError(VoiceDesignError):
    """MiMo Voice Design request failed."""


def _first_nested_value(payload: object, names: set[str]) -> object:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in names and value not in (None, ""):
                return value
        for value in payload.values():
            found = _first_nested_value(value, names)
            if found not in (None, ""):
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _first_nested_value(item, names)
            if found not in (None, ""):
                return found
    return None


def _cosyvoice_customization_url(base_url: str) -> str:
    """Build the DashScope customization endpoint used by Voice Design."""
    fallback = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
    raw = (base_url or "").strip()
    if not raw:
        return fallback
    try:
        parsed = urlparse(raw)
    except Exception:
        return fallback
    if not parsed.scheme or not parsed.netloc:
        return fallback
    if parsed.path.rstrip("/").endswith("/api/v1/services/audio/tts/customization"):
        return raw.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}/api/v1/services/audio/tts/customization"


def _cosyvoice_design_language_hints(ref_language: str) -> list[str]:
    normalized = str(ref_language or "ch").strip().lower()
    return ["en" if normalized == "en" else "zh"]


async def _cosyvoice_design_voice(
    *,
    api_key: str,
    base_url: str,
    prefix: str,
    voice_prompt: str,
    preview_text: str,
    ref_language: str,
    target_model: str,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[str, str, str, str | None]:
    """Create a reusable CosyVoice voice from a text description."""
    from utils.api_config_loader import cosyvoice_model_supports_language_hints

    payload_input = {
        "action": "create_voice",
        "target_model": target_model,
        "voice_prompt": voice_prompt,
        "preview_text": preview_text,
        "prefix": prefix,
    }
    if cosyvoice_model_supports_language_hints(target_model):
        payload_input["language_hints"] = _cosyvoice_design_language_hints(ref_language)
    payload = {
        "model": "voice-enrollment",
        "input": payload_input,
        "parameters": {"sample_rate": 24000, "response_format": "wav"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    endpoint = _cosyvoice_customization_url(base_url)
    client = http_client or get_external_http_client()
    try:
        response = await client.post(endpoint, headers=headers, json=payload, timeout=90)
    except httpx.RequestError as exc:
        host = urlparse(endpoint).netloc or endpoint
        raise CosyVoiceDesignError(
            f"CosyVoice voice design network error while connecting to {host}: {exc}. "
            "Please check DashScope base URL, DNS, and proxy settings."
        ) from exc
    if response.status_code >= 400:
        raise CosyVoiceDesignError(
            f"CosyVoice voice design upstream error ({response.status_code}): {response.text[:300]}"
        )
    try:
        data = response.json()
    except Exception as exc:
        raise CosyVoiceDesignError("CosyVoice voice design returned invalid JSON") from exc

    voice_id = _first_nested_value(data, {"voice_id", "voiceId"})
    if not isinstance(voice_id, str) or not voice_id.strip():
        raise CosyVoiceDesignError("CosyVoice voice design did not return voice_id")

    preview_audio_block = _first_nested_value(data, {"preview_audio"})
    if isinstance(preview_audio_block, dict):
        preview_audio = _first_nested_value(
            preview_audio_block,
            {"data", "audio", "audio_base64", "audio_base_64", "audio_url", "url"},
        )
        media_type = _first_nested_value(
            preview_audio_block,
            {"media_type", "mime_type", "content_type", "response_format"},
        )
    else:
        preview_audio = preview_audio_block or _first_nested_value(
            data,
            {"audio", "audio_base64", "audio_base_64", "audio_url", "preview_audio_url", "url"},
        )
        media_type = _first_nested_value(
            data,
            {"media_type", "mime_type", "content_type", "response_format"},
        )
    request_id = _first_nested_value(data, {"request_id", "requestId"})

    audio_text = str(preview_audio or "").strip()
    resolved_media_type = str(media_type or COSYVOICE_VOICE_DESIGN_DEFAULT_MEDIA_TYPE).strip()
    if audio_text.startswith("data:"):
        header, _, audio_text = audio_text.partition(",")
        if ";" in header:
            resolved_media_type = header[5:].split(";", 1)[0] or resolved_media_type
    if resolved_media_type in ("wav", "mp3", "mpeg"):
        resolved_media_type = "audio/mpeg" if resolved_media_type in ("mp3", "mpeg") else "audio/wav"
    return voice_id.strip(), audio_text, resolved_media_type, str(request_id or "").strip() or None


def _minimax_voice_design_url(base_url: str) -> str:
    raw = base_url.strip().rstrip("/")
    if not raw:
        raise ValueError("MiniMax voice design base URL is required")
    if raw.endswith("/v1/voice_design"):
        return raw
    if raw.endswith("/v1"):
        return f"{raw}/voice_design"
    return f"{raw}/v1/voice_design"


async def _minimax_design_voice(
    *,
    api_key: str,
    base_url: str,
    voice_prompt: str,
    preview_text: str,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[str, str | None]:
    """Create a reusable MiniMax voice; the upstream API assigns voice_id."""
    payload = {"prompt": voice_prompt, "preview_text": preview_text}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    endpoint = _minimax_voice_design_url(base_url)
    client = http_client or get_external_http_client()
    try:
        response = await client.post(endpoint, headers=headers, json=payload, timeout=90)
    except httpx.RequestError as exc:
        host = urlparse(endpoint).netloc or endpoint
        raise MiniMaxVoiceDesignError(
            f"MiniMax voice design network error while connecting to {host}: {exc}. "
            "Please check MiniMax base URL, DNS, and proxy settings."
        ) from exc
    if response.status_code >= 400:
        raise MiniMaxVoiceDesignError(
            f"MiniMax voice design upstream error ({response.status_code}): {response.text[:300]}"
        )
    try:
        data = response.json()
    except Exception as exc:
        raise MiniMaxVoiceDesignError("MiniMax voice design returned invalid JSON") from exc

    base_response = data.get("base_resp") if isinstance(data, dict) else None
    if isinstance(base_response, dict) and base_response.get("status_code", 0) not in (0, "0", None):
        raise MiniMaxVoiceDesignError(
            "MiniMax voice design failed: "
            f"{base_response.get('status_msg') or base_response.get('message') or 'Unknown error'}"
        )
    voice_id = _first_nested_value(data, {"voice_id", "voiceId"})
    if not isinstance(voice_id, str) or not voice_id.strip():
        raise MiniMaxVoiceDesignError("MiniMax voice design did not return voice_id")
    request_id = _first_nested_value(data, {"request_id", "requestId", "trace_id", "traceId"})
    return voice_id.strip(), str(request_id or "").strip() or None


def _prefixed_elevenlabs_voice_id(raw_voice_id: str) -> str:
    raw = (raw_voice_id or "").strip()
    if raw.startswith(ELEVENLABS_TTS_VOICE_PREFIX):
        return raw
    return f"{ELEVENLABS_TTS_VOICE_PREFIX}{raw}"


def _raise_for_elevenlabs_design_response(response: httpx.Response, action: str) -> None:
    if response.status_code < 400:
        return
    message = f"ElevenLabs {action} API error ({response.status_code}): {response.text[:300]}"
    if response.status_code >= 500:
        raise ElevenLabsVoiceDesignError(response.status_code, message)
    raise ValueError(message)


async def _elevenlabs_design_previews(
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
        "text": ELEVENLABS_VOICE_DESIGN_PREVIEW_TEXT,
    }
    async with httpx.AsyncClient(timeout=60, proxy=None, trust_env=False) as client:
        response = await client.post(url, headers=headers, json=payload)
    _raise_for_elevenlabs_design_response(response, "voice design")
    try:
        data = response.json()
    except Exception as exc:
        raise ElevenLabsVoiceDesignError(
            502,
            "ElevenLabs returned invalid JSON while designing voice",
        ) from exc
    previews = data.get("previews") if isinstance(data, dict) else None
    if not isinstance(previews, list) or not previews:
        raise ElevenLabsVoiceDesignError(502, "ElevenLabs did not return voice previews")
    return previews


async def _elevenlabs_create_voice_from_preview(
    *,
    api_key: str,
    base_url: str,
    voice_name: str,
    voice_description: str,
    generated_voice_id: str,
) -> str:
    """Persist an ElevenLabs design candidate as a reusable voice."""
    safe_name = (voice_name or "NEKO Designed Voice").strip()[:100] or "NEKO Designed Voice"
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
    _raise_for_elevenlabs_design_response(response, "voice design create")
    try:
        data = response.json()
    except Exception as exc:
        raise ElevenLabsVoiceDesignError(
            502,
            "ElevenLabs returned invalid JSON while creating designed voice",
        ) from exc
    voice_id = data.get("voice_id") or data.get("voiceId") or ""
    if not voice_id:
        raise ElevenLabsVoiceDesignError(502, "ElevenLabs did not return voice_id")
    return _prefixed_elevenlabs_voice_id(voice_id)


def _extract_mimo_design_audio_bytes(payload: dict) -> bytes:
    candidates: list = [payload.get("audio")]
    for choice in payload.get("choices") or []:
        if isinstance(choice, dict):
            candidates.append((choice.get("message") or {}).get("audio"))
            candidates.append(choice.get("audio"))
    for candidate in candidates:
        audio_base64 = ""
        if isinstance(candidate, str):
            audio_base64 = candidate
        elif isinstance(candidate, dict):
            audio_base64 = candidate.get("data") or candidate.get("audio") or candidate.get("content") or ""
        if not audio_base64:
            continue
        try:
            return base64.b64decode(audio_base64)
        except (binascii.Error, ValueError, TypeError):
            continue
    return b""


class MimoVoiceDesignClient:
    """MiMo description-based voice client.

    MiMo has no enrollment endpoint for Voice Design. NEKO validates the prompt
    once, stores it locally, and sends it again on each synthesis request.
    """

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        self.api_key = api_key
        self.base_url = base_url or None

    def _build_design_payload(self, design_prompt: str, text: str) -> dict:
        from utils.tts.providers.mimo import MIMO_TTS_VOICEDESIGN_MODEL

        return {
            "model": MIMO_TTS_VOICEDESIGN_MODEL,
            "messages": [
                {"role": "user", "content": str(design_prompt or "").strip()},
                {"role": "assistant", "content": text},
            ],
            "audio": {"format": _MIMO_PREVIEW_AUDIO_FORMAT},
            "stream": False,
        }

    async def _post(self, payload: dict) -> dict:
        from utils.tts.providers.mimo import mimo_chat_completions_url

        url = mimo_chat_completions_url(self.base_url)
        headers = {"Content-Type": "application/json", "api-key": self.api_key}
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise MimoVoiceDesignError("MiMo request timed out; please try again") from exc
        except Exception as exc:
            raise MimoVoiceDesignError(f"MiMo request failed: {exc}") from exc
        if response.status_code != 200:
            raise MimoVoiceDesignError(
                f"MiMo request failed: HTTP {response.status_code}, {response.text[:300]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise MimoVoiceDesignError("MiMo returned an invalid response") from exc
        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise MimoVoiceDesignError(f"MiMo request failed: {message}")
        return data if isinstance(data, dict) else {}

    async def validate_design_prompt(
        self,
        design_prompt: str,
        *,
        sample_text: str = "你好呀，很高兴认识你。",
    ) -> None:
        prompt = str(design_prompt or "").strip()
        if not prompt:
            raise MimoVoiceDesignError("MiMo voice description cannot be empty")
        data = await self._post(self._build_design_payload(prompt, sample_text))
        if not _extract_mimo_design_audio_bytes(data):
            raise MimoVoiceDesignError("MiMo voice design validation returned no audio")

    async def synthesize_design_preview(
        self,
        design_prompt: str,
        *,
        text: str = "你好呀，很高兴认识你。",
    ) -> bytes:
        prompt = str(design_prompt or "").strip()
        if not prompt:
            raise MimoVoiceDesignError("MiMo voice description cannot be empty")
        data = await self._post(self._build_design_payload(prompt, text))
        audio = _extract_mimo_design_audio_bytes(data)
        if not audio:
            raise MimoVoiceDesignError("MiMo voice design preview returned no audio")
        return audio
