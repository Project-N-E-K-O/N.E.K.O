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

import os
import threading
import urllib.parse
from fastapi import Request
from fastapi.responses import JSONResponse


# --- proxy mode helpers ---
_PROXY_LOCK = threading.Lock()


_proxy_snapshot: dict[str, str] = {}


def _sanitize_proxies(proxies: dict[str, str]) -> dict[str, str]:
    """Remove credentials from proxy URLs before returning to the client."""
    sanitized: dict[str, str] = {}
    for scheme, url in proxies.items():
        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.username or parsed.password:
                # Rebuild without credentials
                netloc = parsed.hostname or ""
                if parsed.port:
                    netloc += f":{parsed.port}"
                sanitized[scheme] = urllib.parse.urlunparse(
                    (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
                )
            else:
                sanitized[scheme] = url
        except Exception:
            sanitized[scheme] = "<redacted>"
    return sanitized


@router.post("/set_proxy_mode")
async def set_proxy_mode(request: Request):
    """Hot-switch the proxy mode at runtime.

    body: { "direct": true }   → direct connection (disable proxy)
    body: { "direct": false }  → restore the system proxy
    """
    try:
        data = await request.json()
        raw_direct = data.get("direct", False)
        if isinstance(raw_direct, bool):
            direct = raw_direct
        elif isinstance(raw_direct, str):
            direct = raw_direct.lower() in ("true", "1", "yes")
        else:
            direct = bool(raw_direct)

        # 代理相关环境变量 key 列表
        proxy_keys = [
            'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
            'http_proxy', 'https_proxy', 'all_proxy',
        ]

        global _proxy_snapshot
        all_keys = proxy_keys + ['NO_PROXY', 'no_proxy']
        with _PROXY_LOCK:
            if direct:
                # 仅在首次切换到直连时保存快照，避免重复调用覆盖原始值
                if not _proxy_snapshot:
                    _proxy_snapshot = {k: os.environ[k] for k in all_keys if k in os.environ}
                # 设置 NO_PROXY=* 使 httpx/aiohttp/urllib 跳过 Windows 注册表系统代理
                os.environ['NO_PROXY'] = '*'
                os.environ['no_proxy'] = '*'
                for key in proxy_keys:
                    os.environ.pop(key, None)
                logger.info("[ProxyMode] 已切换到直连模式 (NO_PROXY=*)")
            else:
                if _proxy_snapshot:
                    # 从快照恢复所有代理相关环境变量（含 NO_PROXY）
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
