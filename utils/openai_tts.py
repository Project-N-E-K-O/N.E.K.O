# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Shared helpers for OpenAI-compatible ``/v1/audio/speech`` endpoints."""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse


OPENAI_TTS_DEFAULT_BASE_URL = "https://api.openai.com/v1"
OPENAI_TTS_DEFAULT_MODEL = "gpt-4o-mini-tts"
OPENAI_TTS_DEFAULT_VOICE = "marin"
OPENAI_TTS_PCM_SAMPLE_RATE = 24000

_SILICONFLOW_TTS_HOSTS = frozenset({
    "api.siliconflow.cn",
    "api.siliconflow.com",
})


class OpenAITtsConfigError(ValueError):
    """Raised when an OpenAI-compatible TTS configuration is incomplete."""


def openai_tts_base_url(base_url: str) -> str:
    """Normalize a configured URL into an OpenAI-compatible API base URL.

    Accepts a server root, a versioned base URL, or the already-complete speech
    endpoint. Only HTTP(S) is valid: WebSocket TTS providers use their own
    protocol-specific workers.
    """

    raw = str(base_url or "").strip().rstrip("/")
    if not raw:
        raise OpenAITtsConfigError("缺少 OpenAI-compatible TTS URL")

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise OpenAITtsConfigError("OpenAI-compatible TTS URL 必须使用 http:// 或 https://")

    path = (parsed.path or "").rstrip("/")
    if not path:
        path = "/v1"
    if path.endswith("/audio/speech"):
        path = path[:-len("/audio/speech")].rstrip("/") or "/v1"

    return urlunparse(
        (parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment)
    )


def openai_tts_speech_url(base_url: str) -> str:
    """Return the full OpenAI-compatible speech endpoint."""

    parsed = urlparse(openai_tts_base_url(base_url))
    path = f"{parsed.path.rstrip('/')}/audio/speech"
    return urlunparse(
        (parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment)
    )


def openai_tts_extra_body(base_url: str) -> dict[str, int | bool]:
    """Return vendor extensions needed to preserve the PCM wire contract.

    OpenAI-compatible PCM is 24 kHz. SiliconFlow exposes a configurable sample
    rate and does not guarantee that its default matches that contract, so pin
    it explicitly while leaving strict OpenAI-compatible providers untouched.
    """

    parsed = urlparse(str(base_url or "").strip())
    if (parsed.hostname or "").lower() in _SILICONFLOW_TTS_HOSTS:
        return {"sample_rate": OPENAI_TTS_PCM_SAMPLE_RATE, "stream": True}
    return {}


def build_openai_tts_payload(text: str, model: str, voice: str) -> dict[str, str]:
    """Build the strict OpenAI speech request used by runtime and probes."""

    effective_model = str(model or "").strip()
    effective_voice = str(voice or "").strip()
    if not effective_model:
        raise OpenAITtsConfigError("缺少 OpenAI-compatible TTS Model ID")
    if not effective_voice:
        raise OpenAITtsConfigError("缺少 OpenAI-compatible TTS Voice ID")
    return {
        "model": effective_model,
        "input": str(text or ""),
        "voice": effective_voice,
        "response_format": "pcm",
    }


def openai_tts_headers(api_key: str) -> dict[str, str]:
    """Return OpenAI-compatible request headers, allowing auth-free local APIs."""

    headers = {"Content-Type": "application/json", "Accept": "audio/*"}
    key = str(api_key or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers
