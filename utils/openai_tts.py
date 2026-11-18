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

# This module handles only HTTP(S) Speech API; provider-specific workers still handle WS(S)
# bidirectional streams. Keep vendor-specific fields separate from standard OpenAI bodies too.

from __future__ import annotations

from email.message import Message
from typing import NamedTuple
from urllib.parse import parse_qsl, urlparse, urlunparse


OPENAI_TTS_DEFAULT_MODEL = "gpt-4o-mini-tts"
OPENAI_TTS_DEFAULT_VOICE = "marin"
# Raw PCM returned by Speech API enters the project's streaming 24 kHz -> 48 kHz resampler.
# Fix the same source sample rate for configurable providers, or playback speed and pitch become incorrect.
OPENAI_TTS_PCM_SAMPLE_RATE = 24000
# Bounds for a sample rate advertised in ``Content-Type``; anything outside is
# treated as a malformed header and the 24 kHz OpenAI contract is assumed.
_PCM_HEADER_MIN_SAMPLE_RATE = 8000
_PCM_HEADER_MAX_SAMPLE_RATE = 192000
_PCM_HEADER_MAX_CHANNELS = 8

# Match exact domains so SiliconFlow-specific fields never reach unrelated OpenAI-compatible services whose
# domains merely contain the same text, such as user-controlled subdomains.
_SILICONFLOW_TTS_HOSTS = frozenset({
    "api.siliconflow.cn",
    "api.siliconflow.com",
})
# Fish Audio's OpenAI-compatible endpoint (``/compat/v1``) returns 44.1 kHz PCM
# unless ``sample_rate`` is supplied. Same exact-host rule as SiliconFlow.
_FISH_AUDIO_TTS_HOSTS = frozenset({
    "api.fish.audio",
})


class OpenAITtsConfigError(ValueError):
    """Raised when an OpenAI-compatible TTS configuration is incomplete."""


def openai_tts_base_url(base_url: str) -> str:
    """Normalize a configured URL into an OpenAI-compatible API base URL.

    Accepts a server root, a versioned base URL, or the already-complete speech
    endpoint. Only HTTP(S) is valid: WebSocket TTS providers use their own
    protocol-specific workers.
    """

    raw = str(base_url or "").strip()
    if not raw:
        raise OpenAITtsConfigError("缺少 OpenAI-compatible TTS URL")

    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise OpenAITtsConfigError("OpenAI-compatible TTS URL 必须使用 http:// 或 https://")

    path = (parsed.path or "").rstrip("/")
    if not path:
        # If only an origin is supplied, append /v1 according to the standard OpenAI API structure.
        path = "/v1"
    if path.endswith("/audio/speech"):
        # AsyncOpenAI expects an API Base URL, not a specific resource endpoint.
        # Settings allow both forms; remove only known suffixes, preserving the provider's preceding path prefix.
        path = path[:-len("/audio/speech")].rstrip("/") or "/v1"

    # Rebuild from parsed URL components rather than joining raw strings and moving query into the path.
    # Some signed or proxied endpoints require query to remain after the complete path.
    return urlunparse(
        (parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment)
    )


def openai_tts_speech_url(base_url: str) -> str:
    """Return the full OpenAI-compatible speech endpoint."""

    parsed = urlparse(openai_tts_base_url(base_url))
    path = f"{parsed.path.rstrip('/')}/audio/speech"
    # Connectivity probes request this complete URL directly. Component assembly avoids
    # malformed path/query order such as ``...?token=x/audio``.
    return urlunparse(
        (parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, parsed.fragment)
    )


def openai_tts_sdk_options(
    base_url: str,
) -> tuple[str, str]:
    """Split the SDK base URL from query parameters sent with each request."""

    parsed = urlparse(openai_tts_base_url(base_url))
    sdk_base_url = urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, "", "")
    )
    # The SDK does not reliably preserve Base URL query when appending audio/speech;
    # return the raw query and inject it directly in the runtime request hook. Converting to a dict could lose duplicate
    # keys, parameter order, or signature values' original encoding.
    return sdk_base_url, parsed.query


def openai_tts_extra_body(base_url: str) -> dict[str, int | bool]:
    """Return vendor extensions needed to preserve the PCM wire contract.

    OpenAI-compatible PCM is 24 kHz. SiliconFlow and Fish Audio expose a
    configurable sample rate and their defaults do not match that contract, so
    pin it explicitly while leaving strict OpenAI-compatible providers untouched.
    """

    parsed = urlparse(str(base_url or "").strip())
    host = (parsed.hostname or "").lower()
    if host in _SILICONFLOW_TTS_HOSTS:
        # SiliconFlow streams PCM through an OpenAI-compatible HTTP endpoint but uses extension fields for sample rate
        # and streaming. Fix both explicitly so runtime playback and connectivity probes share
        # the same transport format; never send these fields to other generic compatible providers.
        return {"sample_rate": OPENAI_TTS_PCM_SAMPLE_RATE, "stream": True}
    if host in _FISH_AUDIO_TTS_HOSTS:
        # Fish Audio defaults raw PCM to 44.1 kHz; request the 24 kHz OpenAI contract explicitly.
        return {"sample_rate": OPENAI_TTS_PCM_SAMPLE_RATE}
    return {}


class OpenAITtsPcmFormat(NamedTuple):
    """Raw PCM layout advertised by a speech response ``Content-Type``."""

    # ``None`` when the header states no usable rate; callers then fall back
    # to ``OPENAI_TTS_PCM_SAMPLE_RATE``.
    sample_rate: int | None
    channels: int
    # ``audio/L16`` is network byte order (RFC 2586); OpenAI ``pcm`` is little-endian.
    big_endian: bool


def _bounded_int(raw: str | None, low: int, high: int) -> int | None:
    try:
        value = int(str(raw or "").strip())
    except ValueError:
        return None
    return value if low <= value <= high else None


def openai_tts_pcm_format(content_type: str | None) -> OpenAITtsPcmFormat:
    """Read the raw PCM layout advertised by a speech response ``Content-Type``.

    Some compatible providers ignore the 24 kHz contract and describe the real
    format in the header instead, e.g. ``audio/pcm; rate=44100``. ``rate`` (or
    ``sample_rate``) and ``channels`` are honored; a malformed header yields the
    OpenAI defaults rather than failing synthesis. Samples are 16-bit either way.
    """

    message = Message()
    message["Content-Type"] = str(content_type or "")
    try:
        raw_params = message.get_params() or []
    except Exception:
        # Malformed RFC 2231 continuations can raise inside the stdlib parser;
        # the body may still be playable, so keep the default contract.
        raw_params = []
    # Read the media type independently so a malformed parameter list cannot
    # flip ``audio/L16`` back to little-endian decoding.
    media_type = str(content_type or "").split(";", 1)[0].strip().lower()
    params = {
        str(key).lower(): value
        for key, value in raw_params[1:]
        if isinstance(value, str)
    }

    sample_rate = None
    for key in ("rate", "sample_rate"):
        sample_rate = _bounded_int(
            params.get(key),
            _PCM_HEADER_MIN_SAMPLE_RATE,
            _PCM_HEADER_MAX_SAMPLE_RATE,
        )
        if sample_rate is not None:
            break

    channels = _bounded_int(params.get("channels"), 1, _PCM_HEADER_MAX_CHANNELS) or 1
    return OpenAITtsPcmFormat(sample_rate, channels, media_type == "audio/l16")


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
