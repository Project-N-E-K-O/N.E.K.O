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


class OpenAITtsConfigError(ValueError):
    """Raised when an OpenAI-compatible TTS configuration is incomplete."""


def openai_tts_speech_url(base_url: str) -> str:
    """Return the full OpenAI-compatible speech endpoint.

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
    if not path.endswith("/audio/speech"):
        path = f"{path}/audio/speech"

    return urlunparse(
        (parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment)
    )


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
