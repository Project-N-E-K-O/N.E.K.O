"""DashScope 地域 URL 归一化回归测试。"""

import socket
from types import SimpleNamespace

from utils.dashscope_region import (
    configure_dashscope_sdk_urls,
    prefer_dashscope_websocket_ipv4,
)


def test_configure_dashscope_sdk_urls_resets_empty_base_to_default():
    """空 base_url 不能保留上一次的国际地域全局状态。"""
    dashscope_module = SimpleNamespace(
        base_websocket_api_url="wss://dashscope-us.aliyuncs.com/api-ws/v1/inference",
        base_http_api_url="https://dashscope-us.aliyuncs.com/api/v1",
    )

    configure_dashscope_sdk_urls(dashscope_module, "", websocket_path="inference")

    assert dashscope_module.base_websocket_api_url == "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    assert dashscope_module.base_http_api_url == "https://dashscope.aliyuncs.com/api/v1"


def test_dashscope_websocket_ipv4_preference_only_filters_dashscope_hosts(monkeypatch):
    """Broken IPv6 routes must not consume DashScope SDK's short WS timeout."""
    import websocket._http as websocket_http

    original_socket_module = websocket_http.socket
    ipv6_result = (socket.AF_INET6, socket.SOCK_STREAM, socket.SOL_TCP, "", ("2001:db8::1", 443, 0, 0))
    ipv4_result = (socket.AF_INET, socket.SOCK_STREAM, socket.SOL_TCP, "", ("192.0.2.1", 443))

    class FakeSocketModule:
        AF_INET = socket.AF_INET

        @staticmethod
        def getaddrinfo(host, *args, **kwargs):
            return [ipv6_result, ipv4_result]

        def __getattr__(self, name):
            return getattr(socket, name)

    monkeypatch.setattr(websocket_http, "socket", FakeSocketModule())
    try:
        with prefer_dashscope_websocket_ipv4():
            assert websocket_http.socket.getaddrinfo("dashscope.aliyuncs.com", 443) == [ipv4_result]
            assert websocket_http.socket.getaddrinfo("example.com", 443) == [ipv6_result, ipv4_result]
    finally:
        websocket_http.socket = original_socket_module
