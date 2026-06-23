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

"""GPT-SoVITS runtime configuration helpers."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse, urlunparse


GSV_DISABLED_VOICE_PREFIX = "__gptsovits_disabled__|"
DEFAULT_GSV_API_URL = "http://127.0.0.1:9881"


def is_gsv_disabled_voice_id(voice_id: str | None) -> bool:
    return str(voice_id or "").startswith(GSV_DISABLED_VOICE_PREFIX)


def normalize_gsv_api_url(url: str | None, *, default: str = DEFAULT_GSV_API_URL) -> str:
    """Return a normalized HTTP(S) GPT-SoVITS base URL.

    Empty values use the local default. Non-HTTP(S) values are returned stripped
    so callers can surface a precise configuration error instead of silently
    rewriting an unrelated provider URL.
    """
    raw = str(url or "").strip()
    if not raw:
        raw = default
    return raw.rstrip("/")


def is_local_http_url(url: str | None) -> bool:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.strip().lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_valid_http_url(url: str | None) -> bool:
    """True for any well-formed ``http(s)://host`` URL — local OR remote.

    GPT-SoVITS may be self-hosted on a remote box, so (per the maintainer's
    explicit SSRF posture decision, matching vLLM-Omni) the host is no longer
    restricted to loopback; only the scheme and a non-empty host are validated to
    reject garbage / non-HTTP schemes.

    Malformed URLs (e.g. an unterminated IPv6 literal ``http://[::1``) make
    ``parsed.hostname`` raise ``ValueError``; that is caught here so the contract
    "invalid URL -> False" always holds, rather than leaking the exception.
    """
    try:
        parsed = urlparse(str(url or "").strip())
        hostname = parsed.hostname
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(hostname)


def gsv_ws_url_from_http_base(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme == "http":
        parsed = parsed._replace(scheme="ws")
    elif parsed.scheme == "https":
        parsed = parsed._replace(scheme="wss")
    return urlunparse(parsed).rstrip("/") + "/api/v3/tts/stream-input"


def redact_url_for_log(url: str | None) -> str:
    parsed = urlparse(str(url or ""))
    if not parsed.scheme or not parsed.netloc:
        return str(url or "")
    netloc = parsed.netloc
    if "@" in netloc:
        netloc = "***:***@" + netloc.split("@", 1)[1]
    return urlunparse(parsed._replace(netloc=netloc, query="" if parsed.query else parsed.query))
