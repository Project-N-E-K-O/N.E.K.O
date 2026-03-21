import pytest
import httpx
import asyncio
import os
import sys
from unittest.mock import patch, MagicMock

# 添加项目根目录到 sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

# 模拟 FastAPI app 或直接测试路由逻辑
# 由于 system_router 依赖较多，我们直接测试其中的逻辑函数如果可行，
# 或者使用 httpx 模拟对本地正在运行的服务器发起请求（如果环境允许）。
# 这里我们选择模拟 httpx 对后端逻辑的调用。

@pytest.mark.integration
@pytest.mark.asyncio
async def test_meme_proxy_host_validation():
    """验证 Meme Proxy 的域名校验逻辑（修复后的精确匹配/后缀匹配）"""
    from main_routers.system_router import proxy_meme_image
    # 为 client.stream 构建异步上下文管理器 Mock
    from typing import AsyncIterator
    from contextlib import asynccontextmanager
    
    def create_mock_stream(status_code=200, headers=None, content=b""):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.headers = headers or {}
        
        async def mock_aiter_bytes() -> AsyncIterator[bytes]:
            yield content
            
        mock_resp.aiter_bytes = mock_aiter_bytes
        mock_resp.raise_for_status = MagicMock()
        
        @asynccontextmanager
        async def mock_stream(*args, **kwargs):
            yield mock_resp
            
        return mock_stream

    # 测试 case 1: 允许的域名 (精确匹配)
    url_ok = "https://i.imgflip.com/test.jpg"
    with patch("httpx.AsyncClient.stream", side_effect=create_mock_stream(200, {"Content-Type": "image/jpeg"}, b"fake-image")):
        response = await proxy_meme_image(url_ok)
        assert response.status_code == 200
        
    # 测试 case 2: 允许的子域名 (后缀匹配)
    url_sub_ok = "https://sub.qn.doutub.com/test.jpg"
    with patch("httpx.AsyncClient.stream", side_effect=create_mock_stream(200, {"Content-Type": "image/png"}, b"fake-image")):
        response = await proxy_meme_image(url_sub_ok)
        assert response.status_code == 200

    # 测试 case 3: 恶意域名 (包含但不匹配)
    url_evil = "https://i.imgflip.com.evil.com/test.jpg"
    response = await proxy_meme_image(url_evil)
    assert response.status_code == 403
    
    # 测试 case 4: 无协议/非法 URL
    url_invalid = "not-a-url"
    response = await proxy_meme_image(url_invalid)
    assert response.status_code == 400

@pytest.mark.integration
@pytest.mark.asyncio
async def test_meme_proxy_redirect_safety():
    """验证 Meme Proxy 在跟随重试时是否重新校验域名"""
    from main_routers.system_router import proxy_meme_image
    from contextlib import asynccontextmanager
    url_trigger = "https://i.imgflip.com/redirect"
    
    # 模拟第一次返回 302 重定向到恶意域名
    mock_resp_302 = MagicMock()
    mock_resp_302.status_code = 302
    mock_resp_302.headers = {"Location": "http://malicious.com/ssrf"}
    
    @asynccontextmanager
    async def mock_stream_302(*args, **kwargs):
        yield mock_resp_302

    # 模拟第二次返回（如果不校验就会访问这个）
    mock_resp_evil = MagicMock()
    mock_resp_evil.status_code = 200
    mock_resp_evil.headers = {"Content-Type": "image/jpeg"}
    async def mock_evil_aiter(): yield b"secret-data"
    mock_resp_evil.aiter_bytes = mock_evil_aiter
    
    @asynccontextmanager
    async def mock_stream_evil(*args, **kwargs):
        yield mock_resp_evil
        
    with patch("httpx.AsyncClient.stream", side_effect=[mock_stream_302(), mock_stream_evil()]):
        response = await proxy_meme_image(url_trigger)
        # 应该在第二次请求前拦截并返回 403
        assert response.status_code == 403

@pytest.mark.integration
@pytest.mark.asyncio
async def test_meme_proxy_content_type_filtering():
    """验证 Meme Proxy 是否只允许图片类型"""
    from main_routers.system_router import proxy_meme_image
    url = "https://i.imgflip.com/not-an-image"
    
    from contextlib import asynccontextmanager
    mock_resp_html = MagicMock()
    mock_resp_html.status_code = 200
    mock_resp_html.headers = {"Content-Type": "text/html; charset=utf-8"}
    
    @asynccontextmanager
    async def mock_stream_html(*args, **kwargs):
        yield mock_resp_html
        
    with patch("httpx.AsyncClient.stream", side_effect=mock_stream_html):
        response = await proxy_meme_image(url)
        # 应该因为内容类型不符返回 403
        assert response.status_code == 403

if __name__ == "__main__":
    pytest.main([__file__])
