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

"""Proxy mode switching endpoint and proxy snapshot state.

Split out of the former monolithic ``main_routers/config_router.py``.
"""

from ._shared import logger, router
from ..local_access import is_direct_loopback_request
from ..system_router._shared import (
    _extract_local_csrf_token,
    _is_valid_local_csrf_token,
    _validate_local_mutation_request,
)
from main_logic.activity.system_signals import is_remote_backend_deployment
from utils.deployment import has_forwarding_metadata, is_behind_proxy
from utils.http import PROXY_ENV_LOCK

import os
import urllib.parse
from fastapi import Request
from fastapi.responses import JSONResponse


# --- proxy mode helpers ---
_PROXY_LOCK = PROXY_ENV_LOCK


_proxy_snapshot: dict[str, str] = {}


def _sanitize_proxies(proxies: dict[str, str]) -> dict[str, str]:
    """Remove credentials from proxy URLs before returning to the client."""
    sanitized: dict[str, str] = {}
    for scheme, url in proxies.items():
        if not isinstance(url, str):
            sanitized[scheme] = "<redacted>"
            continue
        try:
            has_explicit_scheme = "://" in url or url.startswith("//")
            candidate = url if has_explicit_scheme else f"//{url}"
            parsed = urllib.parse.urlparse(candidate)
            if (
                parsed.username is not None
                or parsed.password is not None
                or "@" in parsed.netloc
            ):
                if parsed.netloc.count("@") > 1:
                    sanitized[scheme] = "<redacted>"
                    continue
                hostname = parsed.hostname or ""
                if not hostname or any(ch.isspace() for ch in hostname):
                    sanitized[scheme] = "<redacted>"
                    continue
                if ":" in hostname and not hostname.startswith("["):
                    hostname = f"[{hostname}]"
                netloc = f"{hostname}:{parsed.port}" if parsed.port else hostname
                rebuilt = urllib.parse.urlunparse(
                    (
                        parsed.scheme,
                        netloc,
                        parsed.path,
                        parsed.params,
                        parsed.query,
                        parsed.fragment,
                    )
                )
                if not has_explicit_scheme and rebuilt.startswith("//"):
                    rebuilt = rebuilt[2:]
                sanitized[scheme] = "<redacted>" if "@" in rebuilt else rebuilt
            else:
                sanitized[scheme] = "<redacted>" if "@" in url else url
        except Exception:
            sanitized[scheme] = "<redacted>"
    return sanitized


_BROWSER_REQUEST_HEADERS = (
    "origin",
    "referer",
    "sec-fetch-site",
    "sec-fetch-mode",
    "sec-fetch-dest",
    "sec-fetch-user",
)


def _is_allowed_native_loopback_proxy_toggle(request: Request, payload: dict) -> bool:
    """Allow legacy Electron main-process proxy toggles on direct loopback without browser metadata."""
    if is_behind_proxy() or is_remote_backend_deployment():
        return False
    if not is_direct_loopback_request(request):
        return False
    if any(header in request.headers for header in _BROWSER_REQUEST_HEADERS):
        return False
    if has_forwarding_metadata(request.headers):
        return False
    if len(payload) != 1 or "direct" not in payload or not isinstance(payload["direct"], bool):
        return False
    token_supplied, csrf_token = _extract_local_csrf_token(request, payload)
    if token_supplied and not _is_valid_local_csrf_token(csrf_token):
        return False
    return True


@router.post("/set_proxy_mode")
async def set_proxy_mode(request: Request):
    """Hot-switch the proxy mode at runtime.

    body: { "direct": true }   -> direct connection (disable proxy)
    body: { "direct": false }  -> restore the system proxy
    """
    try:
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            return JSONResponse(
                {"success": False, "error": "Content-Type must be application/json"},
                status_code=415,
            )
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError, RecursionError):
            return JSONResponse({"success": False, "error": "无效的JSON数据"}, status_code=400)
        if not isinstance(data, dict):
            return JSONResponse({"success": False, "error": "无效的数据"}, status_code=400)
        if not _is_allowed_native_loopback_proxy_toggle(request, data):
            validation_error = _validate_local_mutation_request(
                request,
                payload=data,
                error_defaults={"success": False},
            )
            if validation_error is not None:
                return validation_error
        raw_direct = data.get("direct", False)
        if isinstance(raw_direct, bool):
            direct = raw_direct
        elif isinstance(raw_direct, str):
            direct = raw_direct.lower() in ("true", "1", "yes")
        else:
            direct = bool(raw_direct)

        # Proxy-related environment-variable keys
        proxy_keys = [
            'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
            'http_proxy', 'https_proxy', 'all_proxy',
        ]

        global _proxy_snapshot
        all_keys = proxy_keys + ['NO_PROXY', 'no_proxy']
        with _PROXY_LOCK:
            if direct:
                # Save a snapshot only on the first switch to direct connections, so repeated calls cannot overwrite original values
                if not _proxy_snapshot:
                    _proxy_snapshot = {k: os.environ[k] for k in all_keys if k in os.environ}
                # Set NO_PROXY=* so httpx/aiohttp/urllib bypass the Windows registry system proxy
                os.environ['NO_PROXY'] = '*'
                os.environ['no_proxy'] = '*'
                for key in proxy_keys:
                    os.environ.pop(key, None)
                logger.info("[ProxyMode] 已切换到直连模式 (NO_PROXY=*)")
            else:
                if _proxy_snapshot:
                    # Restore all proxy-related environment variables from the snapshot (including NO_PROXY)
                    for k in all_keys:
                        if k in _proxy_snapshot:
                            os.environ[k] = _proxy_snapshot[k]
                        else:
                            os.environ.pop(k, None)
                    _proxy_snapshot = {}
                    logger.info("[ProxyMode] 已恢复系统代理模式")
                else:
                    logger.info("[ProxyMode] 无快照可恢复，保持当前环境变量")

        import urllib.request
        proxies_after = _sanitize_proxies(urllib.request.getproxies())
        return {"success": True, "direct": direct, "proxies_after": proxies_after}
    except Exception:
        logger.exception("[ProxyMode] 切换失败")
        return JSONResponse({"success": False, "error": "切换失败，服务器内部错误"}, status_code=500)
