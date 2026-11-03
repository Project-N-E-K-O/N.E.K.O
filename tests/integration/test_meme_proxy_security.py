import pytest
import httpx
import asyncio
import os
import sys
from unittest.mock import patch, MagicMock

# Add project root to sys.path.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

# Mock FastAPI app or test route logic directly.
# system_router has many dependencies; test its logic functions directly where possible,
# or use httpx against a running local server if environment allows.
# Here mock httpx calls to backend logic.

@pytest.mark.integration
@pytest.mark.asyncio
async def test_meme_proxy_host_validation():
    """Verify Meme Proxy domain validation (fixed exact/suffix matching)."""
    from main_routers.system_router import proxy_meme_image
    # Build asynchronous context-manager mock for client.stream.
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

    # Case 1: allowed exact domain.
    url_ok = "https://i.imgflip.com/test.jpg"
    with patch("httpx.AsyncClient.stream", side_effect=create_mock_stream(200, {"Content-Type": "image/jpeg"}, b"fake-image")):
        response = await proxy_meme_image(url_ok)
        assert response.status_code == 200
        
    # Case 2: allowed subdomain via suffix matching.
    url_sub_ok = "https://sub.fabiaoqing.com/test.jpg"
    with patch("httpx.AsyncClient.stream", side_effect=create_mock_stream(200, {"Content-Type": "image/png"}, b"fake-image")):
        response = await proxy_meme_image(url_sub_ok)
        assert response.status_code == 200

    # Case 3: malicious domain containing but not matching allowed name.
    url_evil = "https://i.imgflip.com.evil.com/test.jpg"
    response = await proxy_meme_image(url_evil)
    assert response.status_code == 403
    
    # Case 4: absent scheme / invalid URL.
    url_invalid = "not-a-url"
    response = await proxy_meme_image(url_invalid)
    assert response.status_code == 400

@pytest.mark.integration
@pytest.mark.asyncio
async def test_meme_proxy_redirect_safety():
    """Verify Meme Proxy revalidates domains when following retries."""
    from main_routers.system_router import proxy_meme_image
    from contextlib import asynccontextmanager
    url_trigger = "https://i.imgflip.com/redirect"
    
    # Mock initial 302 redirect to malicious domain.
    mock_resp_302 = MagicMock()
    mock_resp_302.status_code = 302
    mock_resp_302.headers = {"Location": "http://malicious.com/ssrf"}
    
    @asynccontextmanager
    async def mock_stream_302(*args, **kwargs):
        yield mock_resp_302

    # Mock second response (accessed if validation omitted).
    mock_resp_evil = MagicMock()
    mock_resp_evil.status_code = 200
    mock_resp_evil.headers = {"Content-Type": "image/jpeg"}
    async def mock_evil_aiter(): yield b"secret-data"
    mock_resp_evil.aiter_bytes = mock_evil_aiter
    
    @asynccontextmanager
    async def mock_stream_evil(*args, **kwargs):
        yield mock_resp_evil
        
    with patch("httpx.AsyncClient.stream", side_effect=[mock_stream_302(), mock_stream_evil()]) as mock_stream:
        response = await proxy_meme_image(url_trigger)
        # Block before second request, returning 403.
        assert response.status_code == 403
        assert mock_stream.call_count == 1

@pytest.mark.integration
@pytest.mark.asyncio
async def test_meme_proxy_content_type_filtering():
    """Verify Meme Proxy permits image types only."""
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
        # Return 403 for disallowed content type.
        assert response.status_code == 403

if __name__ == "__main__":
    pytest.main([__file__])
