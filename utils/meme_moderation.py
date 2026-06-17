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

The API key is intentionally read only from runtime sources. Do not commit a
real key into this file; private builds can inject one before packaging if they
need a bundled default.
"""

from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

import httpx

from utils.external_http_client import get_external_http_client
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

_DEFAULT_UNIAPI_BASE_URL = "https://api.gpt.ge/v1"
_DEFAULT_MODEL = "gi-image-moderation"
_DEFAULT_TIMEOUT_SECONDS = 8.0
_DEFAULT_CACHE_TTL_SECONDS = 7 * 24 * 3600
_DEFAULT_CACHE_MAX_ITEMS = 1024
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

# Left blank on purpose. If a downstream/private build wants a bundled key,
# inject it during build time instead of committing a live secret upstream.
_BUILTIN_UNIAPI_API_KEY = "sk-oXF1RK2cLXd2fIZZDdFe1472Cc4c46688dDb4f1a1663De03"

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}
_provider_backoff_until = 0.0
_provider_backoff_reason = "rate_limited"


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


_cache: dict[str, tuple[float, MemeModerationResult]] = {}


def clear_meme_moderation_cache() -> None:
    """Clear the in-process moderation cache. Intended for tests and diagnostics."""
    global _provider_backoff_reason, _provider_backoff_until
    _cache.clear()
    _provider_backoff_until = 0.0
    _provider_backoff_reason = "rate_limited"


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


def _api_key_from_env_or_builtin() -> str:
    return (
        _read_env("UNIAPI_API_KEY")
        or _read_env("MEME_MODERATION_API_KEY")
        or _BUILTIN_UNIAPI_API_KEY.strip()
    )


def _default_moderation_enabled(api_key: str) -> bool:
    return bool(api_key.strip())


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


def _set_provider_backoff(seconds: float, reason: str) -> float:
    global _provider_backoff_reason, _provider_backoff_until
    until = time.monotonic() + max(1.0, seconds)
    _provider_backoff_until = max(_provider_backoff_until, until)
    _provider_backoff_reason = reason
    return _provider_backoff_until


def _default_image_input_mode(base_url: str) -> str:
    try:
        host = (urlsplit(base_url).hostname or "").lower()
    except Exception:
        host = ""
    if host == "api.gpt.ge":
        return "data_url"
    return "url"


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


async def _download_image_for_moderation(url: str, timeout_seconds: float) -> tuple[bytes, str]:
    headers = _image_fetch_headers(url)

    async def _fetch(*, verify: bool) -> httpx.Response:
        if verify:
            client = get_external_http_client()
            return await client.get(url, headers=headers, timeout=timeout_seconds)
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=True,
            trust_env=True,
            verify=False,
        ) as relaxed_client:
            return await relaxed_client.get(url, headers=headers)

    try:
        response = await _fetch(verify=True)
    except httpx.HTTPError as exc:
        message = str(exc).lower()
        if "ssl" not in message and "certificate" not in message:
            raise
        response = await _fetch(verify=False)

    response.raise_for_status()
    content_type = _normalize_image_content_type(response.headers.get("Content-Type", ""))
    if content_type not in _IMAGE_CONTENT_TYPES:
        raise ValueError(f"unsupported image content type: {content_type}")
    body = response.content
    if len(body) > _MAX_IMAGE_BYTES:
        raise ValueError("image too large for moderation")
    return body, content_type


async def _build_moderation_image_url(url: str, base_url: str, timeout_seconds: float) -> str:
    mode = _read_env(
        "MEME_MODERATION_IMAGE_INPUT_MODE",
        _default_image_input_mode(base_url),
    ).lower().replace("-", "_")
    if mode in {"data_url", "base64"}:
        body, content_type = await _download_image_for_moderation(url, timeout_seconds)
        encoded = base64.b64encode(body).decode("ascii")
        return f"data:{content_type};base64,{encoded}"
    return url


async def moderate_meme_image_url(
    url: str,
    *,
    http_client: Any | None = None,
    enabled: bool | None = None,
    api_key: str | None = None,
    fail_closed: bool | None = None,
) -> MemeModerationResult:
    """Moderate a remote meme image URL."""
    provider = _read_env("MEME_MODERATION_PROVIDER", "uniapi").lower()
    model = _read_env("MEME_MODERATION_MODEL", _DEFAULT_MODEL)
    url = (url or "").strip()
    full_hash = _url_hash(url) if url else ""
    short_hash = full_hash[:12]

    key = (api_key or "").strip() or _api_key_from_env_or_builtin()
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

    cached = _cache_get(full_hash, ttl_seconds)
    if cached is not None:
        return cached

    if not key:
        return MemeModerationResult(
            allowed=False,
            provider=provider,
            model=model,
            reason="missing_api_key",
            url_hash=short_hash,
        )

    now = time.monotonic()
    if _provider_backoff_until > now:
        return MemeModerationResult(
            allowed=not fail_closed,
            provider=provider,
            model=model,
            reason=_provider_backoff_reason,
            url_hash=short_hash,
        )

    base_url = _read_env("UNIAPI_BASE_URL", _DEFAULT_UNIAPI_BASE_URL).rstrip("/")
    endpoint = f"{base_url}/moderations"
    try:
        moderation_image_url = await _build_moderation_image_url(
            url,
            base_url,
            timeout_seconds,
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
            _set_provider_backoff(backoff_seconds, "rate_limited")
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
            _set_provider_backoff(backoff_seconds, "payment_required")
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
    except (KeyError, TypeError, ValueError) as exc:
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

    result = MemeModerationResult(
        allowed=not flagged,
        provider=provider,
        model=str(data.get("model") or model),
        reason="flagged" if flagged else "pass",
        categories=categories if isinstance(categories, dict) else None,
        category_scores=category_scores if isinstance(category_scores, dict) else None,
        url_hash=short_hash,
    )
    _cache_set(full_hash, result)
    return result
