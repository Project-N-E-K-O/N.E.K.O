"""DashScope 地域 URL 归一化工具。"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse


DASHSCOPE_ALLOWED_HOSTS = {
    "dashscope.aliyuncs.com",
    "dashscope-intl.aliyuncs.com",
    "dashscope-us.aliyuncs.com",
}
DASHSCOPE_DEFAULT_HTTP_API_URL = "https://dashscope.aliyuncs.com/api/v1"


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
    """从 DashScope REST/WS 地址推导对应的 WebSocket API 地址。"""
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
    """从 DashScope REST/WS 地址推导对应的 HTTP API 地址。"""
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
    """让 DashScope SDK 的 HTTP / WebSocket 地址跟随当前地域。"""
    ws_url = dashscope_ws_url_from_base(
        base_url,
        websocket_path,
        _dashscope_default_ws_url(websocket_path),
    )
    dashscope_module.base_websocket_api_url = ws_url
    if set_http:
        http_url = dashscope_http_url_from_base(base_url, DASHSCOPE_DEFAULT_HTTP_API_URL)
        dashscope_module.base_http_api_url = http_url
