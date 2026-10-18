import os
import sys
import pytest
import asyncio
import re
from unittest.mock import patch, MagicMock
from typing import List, Dict, Any
import httpx

# Add the project root to sys.path so utils can be imported.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils.meme_fetcher import MemeFetcher

# Mock HTML data to test parsing.
MOCK_SEARCH_HTML = """
<html>
<body>
    <div id="results">
        <a href="/i/abcde123" title="Meme 1 (user-captioned meme)"><img alt="Cat Meme 1"></a>
        <a href="/gif/fghij456" title="Funny GIF (user-generated gif)"><span>Gif Text</span></a>
        <!-- 重复 ID 测试 -->
        <a href="/i/abcde123">Duplicate Link</a>
        <!-- 无效链接测试 -->
        <a href="/other/link">Not a meme</a>
    </div>
</body>
</html>
"""

@pytest.mark.unit
@pytest.mark.asyncio
async def test_search_parsing_logic():
    """Verify search parsing: ID extraction, URL construction, type detection, and deduplication."""
    fetcher = MemeFetcher()
    
    # Mock _fetch_html to return predefined HTML.
    with patch.object(MemeFetcher, '_fetch_html', return_value=MOCK_SEARCH_HTML):
        results: List[Dict[str, Any]] = await fetcher.search("test", limit=5)
        
        # Verify result count: two after deduplication.
        assert len(results) == 2
        
        # Verify the first result (meme).
        # Current logic prefers image alt text.
        assert results[0]['type'] == 'meme'
        assert results[0]['id'] == 'abcde123'
        assert results[0]['url'] == 'https://i.imgflip.com/abcde123.jpg'
        assert results[0]['title'] == "Cat Meme 1"
        
        # Verify the second result (GIF).
        assert results[1]['type'] == 'gif'
        assert results[1]['id'] == 'fghij456'
        assert results[1]['url'] == 'https://i.imgflip.com/fghij456.gif'
        assert results[1]['title'] == "Funny GIF"

@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_management():
    """Verify asynchronous context-manager session lifecycle."""
    async with MemeFetcher() as fetcher:
        # Session should be initialized after entering the context.
        assert fetcher._session is not None
        assert isinstance(fetcher._session, httpx.AsyncClient)
        
        # Record the session object.
        session_obj = fetcher._session
        
        # Mock requests to verify session reuse.
        with patch.object(httpx.AsyncClient, 'get', return_value=MagicMock(status_code=200, text="<html></html>")):
            await fetcher.search("test", limit=1)
            # Patching may bypass actual internal behavior, but assertions still verify session state.
            assert fetcher._session is session_obj
            
    # Exiting the context should close and clear the session.
    assert fetcher._session is None

@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_keyword_behavior():
    """Verify empty keywords issue no request."""
    fetcher = MemeFetcher()
    with patch.object(MemeFetcher, '_fetch_html') as mock_fetch:
        results = await fetcher.search("", limit=5)
        assert results == []
        mock_fetch.assert_not_called()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_on_429():
    """Verify retry behavior on 429."""
    fetcher = MemeFetcher()
    
    # Define a side effect that fails first, then succeeds.
    responses = [
        MagicMock(status_code=429), # First attempt: 429.
        MagicMock(status_code=200, text=MOCK_SEARCH_HTML) # Second attempt: success.
    ]
    
    # Mock httpx.AsyncClient.get.
    with patch("httpx.AsyncClient.get", side_effect=responses):
        # Shorten sleep to speed up tests.
        with patch("asyncio.sleep", return_value=None):
            results = await fetcher.search("test", limit=1)
            assert len(results) > 0

@pytest.mark.manual
@pytest.mark.asyncio
async def test_real_site_connectivity():
    """
    Integration test: verify connectivity to the real site.
    Depends on an external site, so may be skipped by default or run only with --run-manual.
    """
    async with MemeFetcher() as fetcher:
        try:
            results: List[Dict[str, Any]] = await fetcher.search("cat dog", limit=2)
            if not results:
                pytest.skip("未找到结果，可能是关键词问题或站点变更")
            
            # Verify returned URL format and accessibility with HEAD.
            for r in results:
                assert r['url'].startswith('https://i.imgflip.com/')
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                    resp = await client.head(r['url'])
                    # Some CDNs reject HEAD: 403 still passes if URL structure is correct.
                    assert resp.status_code in [200, 301, 302, 403, 405]
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
            pytest.skip(f"无法连接到 Imgflip (网络错误): {e}")
        except Exception as e:
            pytest.fail(f"集成测试失败 (非网络错误): {e}")

if __name__ == "__main__":
    # Allow direct execution for quick testing.
    asyncio.run(test_search_parsing_logic())
    print("Parsing logic test passed!")
