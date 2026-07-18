"""DashScope 地域 URL 归一化回归测试。"""

from types import SimpleNamespace

from utils.dashscope_region import configure_dashscope_sdk_urls


def test_configure_dashscope_sdk_urls_resets_empty_base_to_default():
    """空 base_url 不能保留上一次的国际地域全局状态。"""
    dashscope_module = SimpleNamespace(
        base_websocket_api_url="wss://dashscope-us.aliyuncs.com/api-ws/v1/inference",
        base_http_api_url="https://dashscope-us.aliyuncs.com/api/v1",
    )

    configure_dashscope_sdk_urls(dashscope_module, "", websocket_path="inference")

    assert dashscope_module.base_websocket_api_url == "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    assert dashscope_module.base_http_api_url == "https://dashscope.aliyuncs.com/api/v1"
