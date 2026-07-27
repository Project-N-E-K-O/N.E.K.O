# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Protocol helpers for OpenAI-compatible ``/v1/audio/speech`` endpoints.

This module deliberately covers only the HTTP(S) Speech API contract shared by
the runtime worker and the connectivity probe. WS(S) duplex providers have
different request/stream semantics and remain in their provider-specific
workers; accepting a WebSocket URL here would blur that routing boundary.

Keep vendor-specific fields out of the common payload. A compatible provider
should receive the standard OpenAI body unless it is explicitly recognized by
``openai_tts_extra_body``.
"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse


OPENAI_TTS_DEFAULT_BASE_URL = "https://api.openai.com/v1"
OPENAI_TTS_DEFAULT_MODEL = "gpt-4o-mini-tts"
OPENAI_TTS_DEFAULT_VOICE = "marin"
# Raw PCM returned by the Speech API is fed into the project's 24 kHz -> 48 kHz
# streaming resampler. Providers with a configurable rate must be pinned to the
# same source rate or playback speed and pitch will be wrong.
OPENAI_TTS_PCM_SAMPLE_RATE = 24000

# Use exact host matching so SiliconFlow-only request fields are never leaked to
# an unrelated OpenAI-compatible server whose hostname merely contains the same
# text (for example, a user-controlled subdomain).
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
        # A bare origin follows the conventional OpenAI API layout.
        path = "/v1"
    if path.endswith("/audio/speech"):
        # AsyncOpenAI expects the API base, not the resource endpoint. The UI
        # accepts either form, so strip only the known suffix and preserve any
        # provider-specific prefix before it.
        path = path[:-len("/audio/speech")].rstrip("/") or "/v1"

    # Rebuild from parsed components rather than concatenating the raw string;
    # this keeps query parameters after the path where signed/proxied endpoints
    # expect them.
    return urlunparse(
        (parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment)
    )


def openai_tts_speech_url(base_url: str) -> str:
    """Return the full OpenAI-compatible speech endpoint."""

    parsed = urlparse(openai_tts_base_url(base_url))
    path = f"{parsed.path.rstrip('/')}/audio/speech"
    # The connectivity probe uses this full URL directly. Component-wise
    # assembly avoids producing malformed URLs such as ``...?token=x/audio``.
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
        # SiliconFlow supports streaming PCM through the OpenAI-compatible HTTP
        # endpoint but exposes these controls as extensions. Pin both explicitly
        # so runtime playback and the connectivity probe exercise the same wire
        # format. Do not send them to generic providers.
        return {"sample_rate": OPENAI_TTS_PCM_SAMPLE_RATE, "stream": True}
    return {}


def build_openai_tts_payload(text: str, model: str, voice: str) -> dict[str, str]:
    """Build the strict OpenAI speech request used by runtime and probes.

    Vendor extensions intentionally live in ``openai_tts_extra_body`` so the
    baseline request remains portable across OpenAI-compatible operators.
    """

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
    """Return OpenAI-compatible request headers, allowing auth-free local APIs.

    Do not invent an Authorization header when the key is empty: self-hosted
    compatible endpoints commonly disable authentication, and the HTTP probe
    should mirror the user's actual configuration.
    """

    headers = {"Content-Type": "application/json", "Accept": "audio/*"}
    key = str(api_key or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers
