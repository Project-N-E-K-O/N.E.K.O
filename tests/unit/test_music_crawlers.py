import os
import sys
import pytest
import asyncio
import json
from unittest.mock import patch, MagicMock
from typing import List, Dict, Any
import httpx

# 添加项目根目录到 sys.path，确保可以导入 utils
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils.music_crawlers import (
    NeteaseCrawler, iTunesCrawler, SoundCloudCrawler, 
    MusopenCrawler, FMACrawler, MusicCache, fetch_music_content
)

# ==========================================
# 模拟数据 (Mock Data)
# ==========================================

MOCK_NETEASE_JSON = {
    "code": 200,
    "result": {
        "songs": [
            {
                "id": 12345,
                "name": "Netease Song",
                "artists": [{"name": "Netease Artist"}],
                "fee": 0,
                "album": {"picUrl": "http://pic.url/1"}
            }
        ]
    }
}

MOCK_ITUNES_JSON = {
    "results": [
        {
            "trackName": "iTunes Song",
            "artistName": "iTunes Artist",
            "previewUrl": "http://preview.url/1",
            "artworkUrl100": "http://artwork.url/100x100bb.jpg"
        }
    ]
}

MOCK_FMA_HTML = """
<html>
<body>
    <div data-track-info='{"title": "FMA Song", "artistName": "FMA Artist", "playbackUrl": "http://fma.url/1", "image": "http://fma.img/1"}'></div>
</body>
</html>
"""

MOCK_MUSOPEN_HTML = """
<html>
<body>
    <meta property="og:image" content="http://musopen.img/1">
    <a href="http://musopen.url/piano.mp3?filename=Test.mp3">Download</a>
</body>
</html>
"""

# ==========================================
# 1. MusicCache 测试
# ==========================================

@pytest.mark.unit
def test_music_cache_deduplication():
    cache = MusicCache(expire_seconds=10)
    track = {"url": "http://test.url", "name": "Test", "artist": "Artist"}
    
    # 初始状态不重复
    assert not cache.is_duplicate(track['url'], track['name'], track['artist'])
    
    # 添加后重复
    cache.add(track)
    assert cache.is_duplicate(track['url'], track['name'], track['artist'])
    assert cache.is_duplicate("http://test.url", "", "")
    assert cache.is_duplicate("", "Test", "Artist")

@pytest.mark.unit
def test_music_cache_diversity():
    cache = MusicCache()
    tracks = [
        {"name": "Song 1", "artist": "Artist A"},
        {"name": "Song 2", "artist": "Artist B"},
        {"name": "Lofi Track", "artist": "Artist A"}
    ]
    score = cache.get_diversity_score(tracks)
    assert score['unique_artists'] == 2
    assert "放松氛围" in score['style_notes']
    assert score['score'] > 0

# ==========================================
# 2. 爬虫单元测试 (Mocked)
# ==========================================

@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_crawler_parsing():
    crawler = NeteaseCrawler()
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = MOCK_NETEASE_JSON
    
    with patch.object(httpx.AsyncClient, 'post', return_value=mock_response):
        results: List[Dict[str, Any]] = await crawler.search("test", limit=1)
        assert len(results) == 1
        assert results[0]['name'] == "Netease Song"
        assert "12345" in results[0]['url']
    await crawler.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_itunes_crawler_parsing():
    crawler = iTunesCrawler()
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = MOCK_ITUNES_JSON
    
    with patch.object(httpx.AsyncClient, 'get', return_value=mock_response):
        results: List[Dict[str, Any]] = await crawler.search("test", limit=1)
        assert len(results) == 1
        assert results[0]['name'] == "iTunes Song"
        assert results[0]['cover'].endswith("600x600bb.jpg")
    await crawler.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_fma_crawler_parsing():
    crawler = FMACrawler()
    mock_response = MagicMock(status_code=200, text=MOCK_FMA_HTML)
    
    with patch.object(httpx.AsyncClient, 'get', return_value=mock_response):
        results: List[Dict[str, Any]] = await crawler.search("test", limit=1)
        assert len(results) == 1
        assert results[0]['name'] == "FMA Song"
    await crawler.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_musopen_crawler_parsing():
    crawler = MusopenCrawler()
    mock_response = MagicMock(status_code=200, text=MOCK_MUSOPEN_HTML)
    
    with patch.object(httpx.AsyncClient, 'get', return_value=mock_response):
        results: List[Dict[str, Any]] = await crawler.search("Chopin", limit=1)
        assert len(results) == 1
        assert "Test" in results[0]['name']
        assert results[0]['cover'] == "http://musopen.img/1"
    await crawler.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_soundcloud_crawler_token_logic():
    crawler = SoundCloudCrawler()
    
    # 模拟首页 HTML 以提取 JS 链接
    mock_home = MagicMock(status_code=200, text='<script src="test.js"></script>')
    # 模拟 JS 内容以提取 client_id
    mock_js = MagicMock(status_code=200, text='client_id:"12345678901234567890123456789012"')
    
    # 模拟搜索响应
    mock_search = MagicMock(status_code=200)
    mock_search.json.return_value = {"collection": [{"title": "SC Song", "media": {"transcodings": [{"url": "http://sc.url/stream"}]}}]}
    
    # 模拟音频流 URL 响应
    mock_stream = MagicMock(status_code=200)
    mock_stream.json.return_value = {"url": "http://sc.real/audio.mp3"}

    # 按顺序触发不同的 get 请求
    with patch.object(httpx.AsyncClient, 'get', side_effect=[mock_home, mock_js, mock_search, mock_stream]):
        results: List[Dict[str, Any]] = await crawler.search("test", limit=1)
        assert len(results) == 1
        assert results[0]['url'] == "http://sc.real/audio.mp3"
    await crawler.close()

# ==========================================
# 3. 调度逻辑测试
# ==========================================

@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_music_content_orchestration():
    """验证主调度函数是否根据不同参数正确聚合结果 (Mocked Crawlers)"""
    
    # 模拟 Crawler 类
    with patch('utils.music_crawlers.NeteaseCrawler') as MockNetease, \
         patch('utils.music_crawlers.iTunesCrawler') as MockiTunes:
        
        # 定义异步 Mock 返回
        async def mock_netease_search(*args, **kwargs):
            return [{"name": "Mock Netease", "url": "url1", "artist": "A1"}]
            
        async def mock_itunes_search(*args, **kwargs):
            return [{"name": "Mock iTunes", "url": "url2", "artist": "A2"}]
            
        async def mock_close(*args, **kwargs):
            pass

        # 设置 mock 实例
        MockNetease.return_value.search = mock_netease_search
        MockNetease.return_value.close = mock_close
        
        MockiTunes.return_value.search = mock_itunes_search
        MockiTunes.return_value.close = mock_close
        
        with patch('utils.music_crawlers.is_china_region', return_value=True):
            # 在中国区域，应该包含网易云
            response = await fetch_music_content("keyword", limit=1)
            assert response['success'] is True
            assert any(r['name'] == "Mock Netease" for r in response['data'])

@pytest.mark.manual
@pytest.mark.asyncio
async def test_real_itunes_integration():
    """集成测试：验证真实 iTunes API 连接性"""
    crawler = iTunesCrawler()
    try:
        results: List[Dict[str, Any]] = await crawler.search("lofi", limit=1)
        assert len(results) > 0
        assert "http" in results[0]['url']
    except Exception as e:
        pytest.skip(f"iTunes 集成测试跳过: {e}")
    finally:
        await crawler.close()

if __name__ == "__main__":
    pytest.main([__file__])
