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

"""Safety moderation for meme image candidates.

The API key can be provided by an untracked local config file or by runtime
environment variables. Do not commit a real key into tracked source files.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import os
import time
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from utils.api_config_loader import get_meme_moderation_config
from utils.external_http_client import get_external_http_client
from utils.logger_config import get_module_logger
from utils.meme_fetcher import MEME_ALLOWED_HOSTS

logger = get_module_logger(__name__)

_DEFAULT_UNIAPI_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_MODEL = "omni-moderation-latest"
_DEFAULT_TIMEOUT_SECONDS = 8.0
_DEFAULT_CACHE_TTL_SECONDS = 7 * 24 * 3600
_DEFAULT_CACHE_MAX_ITEMS = 1024
_DEFAULT_IMAGE_PAYLOAD_CACHE_MAX_BYTES = 32 * 1024 * 1024
_MAX_IMAGE_REDIRECTS = 5
_DEFAULT_BLOCK_SCORE_THRESHOLDS = {
    "porn": 0.70,
    "hentai": 0.70,
    "sexy": 0.85,
}
_SCORE_CATEGORY_ALIASES = {
    "porn": ("porn", "sexual"),
    "hentai": ("hentai", "sexual/minors"),
    "sexy": ("sexy",),
}
_ALWAYS_BLOCK_FLAGGED_CATEGORIES = {"sexual/minors"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_IMAGE_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/avif",
    "image/bmp",
}
_REFERER_BY_HOST = {
    "img.doutupk.com": "https://www.doutupk.com/",
    "doutupk.com": "https://www.doutupk.com/",
    "fabiaoqing.com": "https://fabiaoqing.com/",
    "img.soutula.com": "https://fabiaoqing.com/",
    "soutula.com": "https://fabiaoqing.com/",
    "i.imgflip.com": "https://imgflip.com/",
}
_MODERATION_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}
_provider_backoff_until = 0.0
_provider_backoff_reason = "rate_limited"
_provider_backoff_fingerprint = ""


@dataclass(frozen=True)
class MemeModerationResult:
    allowed: bool
    provider: str
    model: str
    reason: str
    categories: dict[str, Any] | None = None
    category_scores: dict[str, Any] | None = None
    cached: bool = False
    url_hash: str = ""


@dataclass(frozen=True)
class _ImagePayloadCacheEntry:
    created_at: float
    payload: str
    size: int
    content_type: str
    etag: str = ""
    last_modified: str = ""


@dataclass(frozen=True)
class _DownloadedImage:
    body: bytes
    content_type: str
    etag: str = ""
    last_modified: str = ""
    not_modified: bool = False


_cache: dict[str, tuple[float, MemeModerationResult]] = {}
_image_payload_cache: dict[str, _ImagePayloadCacheEntry] = {}
_image_payload_cache_bytes = 0


def clear_meme_moderation_cache() -> None:
    """Clear the in-process moderation cache. Intended for tests and diagnostics."""
    global _image_payload_cache_bytes, _provider_backoff_fingerprint
    global _provider_backoff_reason, _provider_backoff_until
    _cache.clear()
    _image_payload_cache.clear()
    _image_payload_cache_bytes = 0
    _provider_backoff_until = 0.0
    _provider_backoff_reason = "rate_limited"
    _provider_backoff_fingerprint = ""


def _read_env(name: str, default: str = "") -> str:
    for key in (f"NEKO_{name}", name):
        raw = os.environ.get(key)
        if raw is None:
            continue
        value = raw.strip()
        if value:
            return value
    return default


def _read_bool_env(name: str, default: bool) -> bool:
    for key in (f"NEKO_{name}", name):
        raw = os.environ.get(key)
        if raw is None:
            continue
        value = raw.strip().lower()
        if value in _TRUTHY:
            return True
        if value in _FALSY:
            return False
        if value:
            logger.warning(
                "[Meme Moderation] Ignoring %s=%r (not a boolean); using default %s",
                key,
                raw,
                default,
            )
    return default


def _read_float_env(name: str, default: float) -> float:
    for key in (f"NEKO_{name}", name):
        raw = os.environ.get(key)
        if raw is None:
            continue
        value = raw.strip()
        if not value:
            continue
        try:
            parsed = float(value)
        except ValueError:
            logger.warning(
                "[Meme Moderation] Ignoring %s=%r (not a number); using default %s",
                key,
                raw,
                default,
            )
            continue
        if parsed > 0:
            return parsed
        logger.warning(
            "[Meme Moderation] Ignoring %s=%r (must be > 0); using default %s",
            key,
            raw,
            default,
        )
    return default


def _read_int_env(name: str, default: int) -> int:
    for key in (f"NEKO_{name}", name):
        raw = os.environ.get(key)
        if raw is None:
            continue
        value = raw.strip()
        if not value:
            continue
        try:
            parsed = int(value)
        except ValueError:
            logger.warning(
                "[Meme Moderation] Ignoring %s=%r (not an integer); using default %s",
                key,
                raw,
                default,
            )
            continue
        if parsed > 0:
            return parsed
        logger.warning(
            "[Meme Moderation] Ignoring %s=%r (must be > 0); using default %s",
            key,
            raw,
            default,
        )
    return default


def _read_probability_env(name: str, default: float) -> float:
    for key in (f"NEKO_{name}", name):
        raw = os.environ.get(key)
        if raw is None:
            continue
        value = raw.strip()
        if not value:
            continue
        try:
            parsed = float(value)
        except ValueError:
            logger.warning(
                "[Meme Moderation] Ignoring %s=%r (not a 0..1 score); using default %s",
                key,
                raw,
                default,
            )
            continue
        if 0.0 <= parsed <= 1.0:
            return parsed
        logger.warning(
            "[Meme Moderation] Ignoring %s=%r (must be between 0 and 1); using default %s",
            key,
            raw,
            default,
        )
    return default


def _is_http_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()


def _cache_get(cache_key: str, ttl_seconds: float) -> MemeModerationResult | None:
    item = _cache.get(cache_key)
    if not item:
        return None
    created_at, result = item
    if time.monotonic() - created_at > ttl_seconds:
        _cache.pop(cache_key, None)
        return None
    return replace(result, cached=True)


def _cache_set(cache_key: str, result: MemeModerationResult) -> None:
    if len(_cache) >= _DEFAULT_CACHE_MAX_ITEMS:
        oldest_key = min(_cache.items(), key=lambda item: item[1][0])[0]
        _cache.pop(oldest_key, None)
    _cache[cache_key] = (time.monotonic(), replace(result, cached=False))


def _moderation_backoff_fingerprint(
    *,
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
) -> str:
    key_hash = _url_hash(api_key) if api_key else ""
    return _url_hash("|".join([provider, model, base_url, key_hash]))


def _moderation_policy_cache_key(
    *,
    url_hash: str,
    image_hash: str,
    provider: str,
    model: str,
    base_url: str,
) -> str:
    threshold_parts = [
        f"{category}:{_score_threshold_for(category, default_threshold):.6f}"
        for category, default_threshold in sorted(_DEFAULT_BLOCK_SCORE_THRESHOLDS.items())
    ]
    policy = "|".join([url_hash, image_hash, provider, model, base_url, *threshold_parts])
    return _url_hash(policy)


def _image_payload_cache_get(
    cache_key: str,
    ttl_seconds: float,
) -> _ImagePayloadCacheEntry | None:
    global _image_payload_cache_bytes
    item = _image_payload_cache.get(cache_key)
    if not item:
        return None
    if time.monotonic() - item.created_at > ttl_seconds:
        _image_payload_cache.pop(cache_key, None)
        _image_payload_cache_bytes = max(0, _image_payload_cache_bytes - item.size)
        return None
    return item


def _image_payload_cache_set(
    cache_key: str,
    payload: str,
    *,
    content_type: str,
    etag: str = "",
    last_modified: str = "",
) -> None:
    global _image_payload_cache_bytes
    payload_size = len(payload)
    max_bytes = _read_int_env(
        "MEME_MODERATION_IMAGE_PAYLOAD_CACHE_MAX_BYTES",
        _DEFAULT_IMAGE_PAYLOAD_CACHE_MAX_BYTES,
    )
    if payload_size > max_bytes:
        old_item = _image_payload_cache.pop(cache_key, None)
        if old_item is not None:
            _image_payload_cache_bytes = max(0, _image_payload_cache_bytes - old_item.size)
        return
    old_item = _image_payload_cache.pop(cache_key, None)
    if old_item is not None:
        _image_payload_cache_bytes = max(0, _image_payload_cache_bytes - old_item.size)
    while (
        len(_image_payload_cache) >= _DEFAULT_CACHE_MAX_ITEMS
        or _image_payload_cache_bytes + payload_size > max_bytes
    ):
        oldest_key = min(_image_payload_cache.items(), key=lambda item: item[1].created_at)[0]
        old_entry = _image_payload_cache.pop(oldest_key)
        _image_payload_cache_bytes = max(0, _image_payload_cache_bytes - old_entry.size)
    _image_payload_cache[cache_key] = _ImagePayloadCacheEntry(
        created_at=time.monotonic(),
        payload=payload,
        size=payload_size,
        content_type=content_type,
        etag=etag,
        last_modified=last_modified,
    )
    _image_payload_cache_bytes += payload_size


def _api_key_from_env() -> str:
    return _read_env("MEME_MODERATION_API_KEY") or _read_env("UNIAPI_API_KEY")


def _read_config_text(config: dict[str, Any], key: str) -> str:
    value = config.get(key, "")
    return str(value or "").strip()


def _default_moderation_enabled(api_key: str) -> bool:
    return bool(api_key.strip())


def _score_threshold_for(category: str, default: float) -> float:
    return _read_probability_env(
        f"MEME_MODERATION_{category.upper()}_THRESHOLD",
        default,
    )


def _score_as_float(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def _blocked_score_categories(category_scores: Any) -> list[str]:
    if not isinstance(category_scores, dict):
        return []
    blocked = []
    for category, default_threshold in _DEFAULT_BLOCK_SCORE_THRESHOLDS.items():
        scores = [
            _score_as_float(category_scores.get(alias))
            for alias in _SCORE_CATEGORY_ALIASES.get(category, (category,))
        ]
        valid_scores = [score for score in scores if score is not None]
        if not valid_scores:
            continue
        score = max(valid_scores)
        if score >= _score_threshold_for(category, default_threshold):
            blocked.append(category)
    return blocked


def _has_threshold_score_categories(category_scores: Any) -> bool:
    if not isinstance(category_scores, dict):
        return False
    return any(
        alias in category_scores
        for aliases in _SCORE_CATEGORY_ALIASES.values()
        for alias in aliases
    )


def _rate_limit_backoff_seconds(response: httpx.Response | None) -> float:
    retry_after = ""
    if response is not None:
        retry_after = (response.headers.get("Retry-After") or "").strip()
    if retry_after:
        try:
            seconds = float(retry_after)
            if seconds > 0:
                return seconds
        except ValueError:
            pass
    return _read_float_env("MEME_MODERATION_RATE_LIMIT_BACKOFF_SECONDS", 60.0)


def _set_provider_backoff(seconds: float, reason: str, fingerprint: str) -> float:
    global _provider_backoff_fingerprint, _provider_backoff_reason, _provider_backoff_until
    until = time.monotonic() + max(1.0, seconds)
    if _provider_backoff_fingerprint == fingerprint:
        _provider_backoff_until = max(_provider_backoff_until, until)
    else:
        _provider_backoff_until = until
    _provider_backoff_reason = reason
    _provider_backoff_fingerprint = fingerprint
    return _provider_backoff_until


def _default_image_input_mode(base_url: str) -> str:
    try:
        host = (urlsplit(base_url).hostname or "").lower()
    except Exception:
        host = ""
    if host == "api.gpt.ge":
        return "data_url"
    return "url"


def _image_input_mode(base_url: str) -> str:
    return _read_env(
        "MEME_MODERATION_IMAGE_INPUT_MODE",
        _default_image_input_mode(base_url),
    ).lower().replace("-", "_")


def _referer_for_url(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except Exception:
        return "https://www.google.com/"
    return _REFERER_BY_HOST.get(host, f"https://{host}/" if host else "https://www.google.com/")


def _image_fetch_headers(url: str) -> dict[str, str]:
    return {
        "User-Agent": _MODERATION_USER_AGENT,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": _referer_for_url(url),
    }


def _normalize_image_content_type(raw: str) -> str:
    content_type = (raw or "").split(";", 1)[0].strip().lower()
    if content_type == "image/jpg":
        return "image/jpeg"
    return content_type or "image/jpeg"


def _ssl_fallback_enabled() -> bool:
    return _read_bool_env("MEME_MODERATION_ALLOW_SSL_FALLBACK", False)


def _is_blocked_host(hostname: str) -> bool:
    if hostname in {"localhost", "localhost.localdomain"}:
        return True
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _is_allowed_meme_image_fetch_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    hostname = (parsed.hostname or "").strip(".").lower()
    if not hostname or _is_blocked_host(hostname):
        return False
    return any(
        hostname == allowed or hostname.endswith("." + allowed)
        for allowed in MEME_ALLOWED_HOSTS
    )


async def _read_limited_response_body(response: httpx.Response) -> bytes:
    raw_length = (response.headers.get("Content-Length") or "").strip()
    if raw_length:
        try:
            content_length = int(raw_length)
        except ValueError:
            content_length = 0
        if content_length > _MAX_IMAGE_BYTES:
            raise ValueError("image too large for moderation")

    body = bytearray()
    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        body.extend(chunk)
        if len(body) > _MAX_IMAGE_BYTES:
            raise ValueError("image too large for moderation")
    return bytes(body)


async def _download_image_for_moderation(
    url: str,
    timeout_seconds: float,
    cached_entry: _ImagePayloadCacheEntry | None = None,
) -> _DownloadedImage:
    if not _is_allowed_meme_image_fetch_url(url):
        raise ValueError("meme image URL is not in the allowed host list")
    headers = _image_fetch_headers(url)
    if cached_entry:
        if cached_entry.etag:
            headers["If-None-Match"] = cached_entry.etag
        if cached_entry.last_modified:
            headers["If-Modified-Since"] = cached_entry.last_modified

    async def _fetch(*, verify: bool) -> _DownloadedImage:
        if verify:
            client = get_external_http_client()
            return await _stream_image_response(
                client,
                url,
                headers,
                timeout_seconds,
                cached_entry=cached_entry,
            )
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=True,
            # Last-resort path for certificate-broken meme hosts. This only runs
            # after strict verification fails and the env flag explicitly opts in.
            verify=False,
        ) as relaxed_client:
            return await _stream_image_response(
                relaxed_client,
                url,
                headers,
                timeout_seconds,
                cached_entry=cached_entry,
            )

    try:
        image_data = await _fetch(verify=True)
    except httpx.HTTPError as exc:
        message = str(exc).lower()
        if "ssl" not in message and "certificate" not in message:
            raise
        if not _ssl_fallback_enabled():
            raise
        host = (urlsplit(url).hostname or "").lower()
        logger.warning(
            "[Meme Moderation] SSL verification fallback enabled for image fetch host=%s error=%s",
            host,
            exc,
        )
        image_data = await _fetch(verify=False)

    return image_data


async def _stream_image_response(
    client: Any,
    url: str,
    headers: dict[str, str],
    timeout_seconds: float,
    *,
    cached_entry: _ImagePayloadCacheEntry | None = None,
) -> _DownloadedImage:
    current_url = url
    for _ in range(_MAX_IMAGE_REDIRECTS + 1):
        if not _is_allowed_meme_image_fetch_url(current_url):
            raise ValueError("meme image redirect target is not in the allowed host list")
        async with client.stream(
            "GET",
            current_url,
            headers=headers,
            timeout=timeout_seconds,
            follow_redirects=False,
        ) as response:
            status_code = int(getattr(response, "status_code", 0) or 0)
            final_url = str(getattr(response, "url", current_url) or current_url)
            if not _is_allowed_meme_image_fetch_url(final_url):
                raise ValueError("meme image redirect target is not in the allowed host list")
            if status_code == 304 and cached_entry is not None:
                return _DownloadedImage(
                    body=b"",
                    content_type=cached_entry.content_type,
                    etag=cached_entry.etag,
                    last_modified=cached_entry.last_modified,
                    not_modified=True,
                )
            if 300 <= status_code < 400:
                location = (response.headers.get("Location") or "").strip()
                if not location:
                    raise ValueError("meme image redirect response is missing Location")
                next_url = urljoin(str(getattr(response, "url", current_url) or current_url), location)
                if not _is_allowed_meme_image_fetch_url(next_url):
                    raise ValueError("meme image redirect target is not in the allowed host list")
                current_url = next_url
                continue
            response.raise_for_status()
            content_type = _normalize_image_content_type(response.headers.get("Content-Type", ""))
            if content_type not in _IMAGE_CONTENT_TYPES:
                raise ValueError(f"unsupported image content type: {content_type}")
            body = await _read_limited_response_body(response)
            return _DownloadedImage(
                body=body,
                content_type=content_type,
                etag=(response.headers.get("ETag") or "").strip(),
                last_modified=(response.headers.get("Last-Modified") or "").strip(),
            )
    raise ValueError("too many meme image redirects")


async def _build_moderation_image_url(
    url: str,
    base_url: str,
    timeout_seconds: float,
    ttl_seconds: float,
) -> tuple[str, str | None]:
    mode = _image_input_mode(base_url)
    if mode in {"data_url", "base64"}:
        payload_cache_key = _url_hash(url)
        cached_entry = _image_payload_cache_get(payload_cache_key, ttl_seconds)
        downloaded = await _download_image_for_moderation(
            url,
            timeout_seconds,
            cached_entry,
        )
        if cached_entry is not None and downloaded.not_modified:
            return cached_entry.payload, _url_hash(cached_entry.payload)
        encoded = base64.b64encode(downloaded.body).decode("ascii")
        payload = f"data:{downloaded.content_type};base64,{encoded}"
        _image_payload_cache_set(
            payload_cache_key,
            payload,
            content_type=downloaded.content_type,
            etag=downloaded.etag,
            last_modified=downloaded.last_modified,
        )
        return payload, _url_hash(payload)
    if not _is_allowed_meme_image_fetch_url(url):
        raise ValueError("meme image URL is not in the allowed host list")
    return url, None


async def moderate_meme_image_url(
    url: str,
    *,
    http_client: Any | None = None,
    enabled: bool | None = None,
    api_key: str | None = None,
    fail_closed: bool | None = None,
) -> MemeModerationResult:
    """Moderate a remote meme image URL."""
    moderation_config = await asyncio.to_thread(get_meme_moderation_config)
    provider = _read_env("MEME_MODERATION_PROVIDER", "uniapi").lower()
    model = (
        _read_env("MEME_MODERATION_MODEL")
        or _read_config_text(moderation_config, "model")
        or _DEFAULT_MODEL
    )
    url = (url or "").strip()
    full_hash = _url_hash(url) if url else ""
    short_hash = full_hash[:12]

    key = (
        (api_key or "").strip()
        or _read_config_text(moderation_config, "api_key")
        or _api_key_from_env()
    )
    if enabled is None:
        enabled = _read_bool_env(
            "MEME_MODERATION_ENABLED",
            _default_moderation_enabled(key),
        )
    if fail_closed is None:
        fail_closed = _read_bool_env("MEME_MODERATION_FAIL_CLOSED", True)

    if not enabled:
        return MemeModerationResult(
            allowed=True,
            provider=provider,
            model=model,
            reason="disabled",
            url_hash=short_hash,
        )

    if provider != "uniapi":
        return MemeModerationResult(
            allowed=not fail_closed,
            provider=provider,
            model=model,
            reason="unsupported_provider",
            url_hash=short_hash,
        )

    if not _is_http_url(url):
        return MemeModerationResult(
            allowed=False,
            provider=provider,
            model=model,
            reason="invalid_url",
            url_hash=short_hash,
        )

    timeout_seconds = _read_float_env(
        "MEME_MODERATION_TIMEOUT_SECONDS",
        _DEFAULT_TIMEOUT_SECONDS,
    )
    ttl_seconds = _read_float_env(
        "MEME_MODERATION_CACHE_TTL_SECONDS",
        _DEFAULT_CACHE_TTL_SECONDS,
    )
    base_url = (
        _read_env("UNIAPI_BASE_URL")
        or _read_config_text(moderation_config, "base_url")
        or _DEFAULT_UNIAPI_BASE_URL
    ).rstrip("/")

    if not key:
        return MemeModerationResult(
            allowed=True,
            provider=provider,
            model=model,
            reason="disabled",
            url_hash=short_hash,
        )

    now = time.monotonic()
    backoff_fingerprint = _moderation_backoff_fingerprint(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=key,
    )
    if (
        _provider_backoff_fingerprint == backoff_fingerprint
        and _provider_backoff_until > now
    ):
        return MemeModerationResult(
            allowed=not fail_closed,
            provider=provider,
            model=model,
            reason=_provider_backoff_reason,
            url_hash=short_hash,
        )

    endpoint = f"{base_url}/moderations"
    try:
        moderation_image_url, moderation_image_hash = await _build_moderation_image_url(
            url,
            base_url,
            timeout_seconds,
            ttl_seconds,
        )
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "[Meme Moderation] image fetch failed url_hash=%s error=%s",
            short_hash,
            exc,
        )
        return MemeModerationResult(
            allowed=not fail_closed,
            provider=provider,
            model=model,
            reason="image_fetch_failed",
            url_hash=short_hash,
        )

    verdict_cache_key = None
    if moderation_image_hash:
        verdict_cache_key = _moderation_policy_cache_key(
            url_hash=full_hash,
            image_hash=moderation_image_hash,
            provider=provider,
            model=model,
            base_url=base_url,
        )
        cached = _cache_get(verdict_cache_key, ttl_seconds)
        if cached is not None:
            return cached

    payload = {
        "model": model,
        "input": [
            {
                "type": "image_url",
                "image_url": {"url": moderation_image_url},
            }
        ],
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    client = http_client or get_external_http_client()
    try:
        response = await client.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 429:
            backoff_seconds = _rate_limit_backoff_seconds(exc.response)
            _set_provider_backoff(backoff_seconds, "rate_limited", backoff_fingerprint)
            logger.warning(
                "[Meme Moderation] UniAPI rate limited url_hash=%s backoff=%.1fs",
                short_hash,
                backoff_seconds,
            )
            return MemeModerationResult(
                allowed=not fail_closed,
                provider=provider,
                model=model,
                reason="rate_limited",
                url_hash=short_hash,
            )
        if status == 402:
            backoff_seconds = _read_float_env(
                "MEME_MODERATION_PAYMENT_BACKOFF_SECONDS",
                10 * 60.0,
            )
            _set_provider_backoff(backoff_seconds, "payment_required", backoff_fingerprint)
            logger.warning(
                "[Meme Moderation] UniAPI payment required url_hash=%s backoff=%.1fs",
                short_hash,
                backoff_seconds,
            )
            return MemeModerationResult(
                allowed=not fail_closed,
                provider=provider,
                model=model,
                reason="payment_required",
                url_hash=short_hash,
            )
        logger.warning(
            "[Meme Moderation] UniAPI HTTP error url_hash=%s status=%s error=%s",
            short_hash,
            status,
            exc,
        )
        return MemeModerationResult(
            allowed=not fail_closed,
            provider=provider,
            model=model,
            reason="http_error",
            url_hash=short_hash,
        )
    except (httpx.HTTPError, TimeoutError) as exc:
        logger.warning(
            "[Meme Moderation] UniAPI request failed url_hash=%s error=%s",
            short_hash,
            exc,
        )
        return MemeModerationResult(
            allowed=not fail_closed,
            provider=provider,
            model=model,
            reason="request_failed",
            url_hash=short_hash,
        )
    except (TypeError, ValueError) as exc:
        logger.warning(
            "[Meme Moderation] UniAPI invalid response url_hash=%s error=%s",
            short_hash,
            exc,
        )
        return MemeModerationResult(
            allowed=not fail_closed,
            provider=provider,
            model=model,
            reason="invalid_response",
            url_hash=short_hash,
        )

    try:
        first_result = data["results"][0]
        flagged = bool(first_result.get("flagged", False))
        categories = first_result.get("categories")
        category_scores = first_result.get("category_scores")
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        logger.warning(
            "[Meme Moderation] UniAPI invalid response url_hash=%s error=%s",
            short_hash,
            exc,
        )
        return MemeModerationResult(
            allowed=not fail_closed,
            provider=provider,
            model=model,
            reason="invalid_response",
            url_hash=short_hash,
        )

    blocked_categories = _blocked_score_categories(category_scores)
    flagged_category_keys = {
        str(name)
        for name, value in (categories or {}).items()
        if value
    } if isinstance(categories, dict) else set()
    threshold_category_aliases = {
        alias
        for aliases in _SCORE_CATEGORY_ALIASES.values()
        for alias in aliases
    }
    flagged_outside_threshold_policy = bool(
        flagged and flagged_category_keys - threshold_category_aliases
    )
    flagged_always_block_policy = bool(
        flagged and flagged_category_keys & _ALWAYS_BLOCK_FLAGGED_CATEGORIES
    )
    has_threshold_scores = _has_threshold_score_categories(category_scores)
    blocked = (
        bool(blocked_categories)
        or flagged_outside_threshold_policy
        or flagged_always_block_policy
        or (flagged and not has_threshold_scores)
    )
    reason = "pass"
    if blocked:
        reason = "flagged" if flagged else "score_threshold"

    result = MemeModerationResult(
        allowed=not blocked,
        provider=provider,
        model=str(data.get("model") or model),
        reason=reason,
        categories=categories if isinstance(categories, dict) else None,
        category_scores=category_scores if isinstance(category_scores, dict) else None,
        url_hash=short_hash,
    )
    # Cache only allowed moderation results. Blocked images should be rechecked
    # after provider or local score thresholds change instead of staying stuck.
    if result.allowed and verdict_cache_key:
        _cache_set(verdict_cache_key, result)
    return result
