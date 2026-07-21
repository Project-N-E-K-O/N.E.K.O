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

"""DashScope region URL normalization helpers."""

from __future__ import annotations

from contextlib import contextmanager
import socket
import threading
from urllib.parse import urlparse, urlunparse


# DashScope SDK 的 api_key / base_websocket_api_url / base_http_api_url 是模块级
# 全局，同一进程里 cosyvoice TTS worker、/voice_preview、voice_clone 三条流程
# 都会写 + 读。两条流程并发跑（典型场景：用户在 worker 跑 TTS 时点击克隆按钮）
# 会在 "设 global → 构造 SDK 对象 / 调用" 之间互相覆盖，导致请求带着别人的
# key/地域发出去 (Codex P1 #3258691457)。
# 所有调用方在写 global + 构造 SDK + 同步调用这一整段都拿这把锁。
# 拿到 SDK 实例后由实例自己的内部状态承载请求，可以解锁继续跑。
DASHSCOPE_GLOBAL_LOCK = threading.Lock()
# dashscope.audio.tts_v2 uses websocket-client, which attempts DNS results
# serially. A black-holed IPv6 route can consume its fixed 5-second connection
# window before it reaches a working IPv4 address. Limit the workaround to the
# websocket-client module and DashScope hosts; HTTP and other providers stay
# untouched.
DASHSCOPE_WEBSOCKET_DNS_LOCK = threading.RLock()


DASHSCOPE_ALLOWED_HOSTS = {
    "dashscope.aliyuncs.com",
    "dashscope-intl.aliyuncs.com",
    "dashscope-us.aliyuncs.com",
}
DASHSCOPE_DEFAULT_HTTP_API_URL = "https://dashscope.aliyuncs.com/api/v1"


@contextmanager
def prefer_dashscope_websocket_ipv4():
    """Temporarily make websocket-client prefer IPv4 for DashScope hosts.

    websocket-client does not implement Happy Eyeballs. Keep IPv6 as a
    fallback when DNS has no IPv4 address, so IPv6-only networks still work.
    """
    import websocket._http as websocket_http

    original_socket_module = websocket_http.socket

    class _DashScopeSocketFacade:
        def getaddrinfo(self, host, *args, **kwargs):
            results = original_socket_module.getaddrinfo(host, *args, **kwargs)
            hostname = str(host or "").rstrip(".").lower()
            if hostname not in DASHSCOPE_ALLOWED_HOSTS:
                return results
            ipv4_results = [result for result in results if result[0] == socket.AF_INET]
            return ipv4_results or results

        def __getattr__(self, name):
            return getattr(original_socket_module, name)

    with DASHSCOPE_WEBSOCKET_DNS_LOCK:
        websocket_http.socket = _DashScopeSocketFacade()
        try:
            yield
        finally:
            websocket_http.socket = original_socket_module


def _dashscope_default_ws_url(path_tail: str) -> str:
    return urlunparse((
        "wss",
        "dashscope.aliyuncs.com",
        f"/api-ws/v1/{path_tail.strip('/')}",
        "",
        "",
        "",
    ))


def dashscope_ws_url_from_base(base_url: str, path_tail: str, default_url: str = "") -> str:
    """Derive the corresponding WebSocket API address from a DashScope REST/WS address."""
    try:
        parsed = urlparse((base_url or "").strip())
    except Exception:
        parsed = None
    host = (parsed.netloc if parsed else "").lower()
    if host not in DASHSCOPE_ALLOWED_HOSTS:
        return default_url
    scheme = "wss" if parsed.scheme in ("https", "wss", "") else "ws"
    return urlunparse((scheme, host, f"/api-ws/v1/{path_tail.strip('/')}", "", "", ""))


def dashscope_http_url_from_base(base_url: str, default_url: str = "") -> str:
    """Derive the corresponding HTTP API address from a DashScope REST/WS address."""
    try:
        parsed = urlparse((base_url or "").strip())
    except Exception:
        parsed = None
    host = (parsed.netloc if parsed else "").lower()
    if host not in DASHSCOPE_ALLOWED_HOSTS:
        return default_url
    scheme = "https" if parsed.scheme in ("https", "wss", "") else "http"
    return urlunparse((scheme, host, "/api/v1", "", "", ""))


def configure_dashscope_sdk_urls(
    dashscope_module,
    base_url: str,
    *,
    websocket_path: str = "inference",
    set_http: bool = True,
) -> None:
    """Make the DashScope SDK's HTTP / WebSocket addresses follow the current region."""
    ws_url = dashscope_ws_url_from_base(
        base_url,
        websocket_path,
        _dashscope_default_ws_url(websocket_path),
    )
    dashscope_module.base_websocket_api_url = ws_url
    if set_http:
        http_url = dashscope_http_url_from_base(base_url, DASHSCOPE_DEFAULT_HTTP_API_URL)
        dashscope_module.base_http_api_url = http_url
