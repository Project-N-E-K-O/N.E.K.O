import os
import sys
import pytest
import asyncio
import re
from unittest.mock import patch, MagicMock
from typing import List, Dict, Any
import httpx

# 添加项目根目录到 sys.path，确保可以导入 utils
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils.meme_fetcher import MemeFetcher

# 模拟 HTML 数据，用于测试解析逻辑
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
    """验证搜索解析逻辑：ID 提取、URL 构建、类型识别和去重"""
    fetcher = MemeFetcher()
    
    # 模拟 _fetch_html 返回预定义的 HTML
    with patch.object(MemeFetcher, '_fetch_html', return_value=MOCK_SEARCH_HTML):
        results: List[Dict[str, Any]] = await fetcher.search("test", limit=5)
        
        # 验证结果数量（去重后应为 2）
        assert len(results) == 2
        
        # 验证第一个结果 (Meme)
        # 现在逻辑优先使用 img alt
        assert results[0]['type'] == 'meme'
        assert results[0]['id'] == 'abcde123'
        assert results[0]['url'] == 'https://i.imgflip.com/abcde123.jpg'
        assert results[0]['title'] == "Cat Meme 1"
        
        # 验证第二个结果 (GIF)
        assert results[1]['type'] == 'gif'
        assert results[1]['id'] == 'fghij456'
        assert results[1]['url'] == 'https://i.imgflip.com/fghij456.gif'
        assert results[1]['title'] == "Funny GIF"

@pytest.mark.unit
@pytest.mark.asyncio
async def test_session_management():
    """验证异步上下文管理器的 Session 生命周期"""
    async with MemeFetcher() as fetcher:
        # 进入上下文后 session 应该已初始化
        assert fetcher._session is not None
        assert isinstance(fetcher._session, httpx.AsyncClient)
        
        # 记录 session 对象
        session_obj = fetcher._session
        
        # 模拟请求以验证 session 被复用
        with patch.object(httpx.AsyncClient, 'get', return_value=MagicMock(status_code=200, text="<html></html>")):
            await fetcher.search("test", limit=1)
            # 这里虽然由于 patch 可能不会真正调用内部逻辑，但我们可以通过断言验证 session 状态
            assert fetcher._session is session_obj
            
    # 退出上下文后 session 应该已关闭并置为空
    assert fetcher._session is None

@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_keyword_behavior():
    """验证空关键词不发起请求"""
    fetcher = MemeFetcher()
    with patch.object(MemeFetcher, '_fetch_html') as mock_fetch:
        results = await fetcher.search("", limit=5)
        assert results == []
        mock_fetch.assert_not_called()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_retry_on_429():
    """验证触发 429 时的重试逻辑"""
    fetcher = MemeFetcher()
    
    # 定义一个副作用函数，模拟先失败后成功
    responses = [
        MagicMock(status_code=429), # 第一次 429
        MagicMock(status_code=200, text=MOCK_SEARCH_HTML) # 第二次成功
    ]
    
    # 需要 mock httpx.AsyncClient 中的 get
    with patch("httpx.AsyncClient.get", side_effect=responses):
        # 减小 sleep 时间以加快测试
        with patch("asyncio.sleep", return_value=None):
            results = await fetcher.search("test", limit=1)
            assert len(results) > 0

@pytest.mark.manual
@pytest.mark.asyncio
async def test_real_site_connectivity():
    """
    集成测试：验证真实站点连接性
    注意：此测试由于依赖外部站点，默认可能被跳过或仅在 --run-manual 时运行
    """
    async with MemeFetcher() as fetcher:
        try:
            results: List[Dict[str, Any]] = await fetcher.search("cat dog", limit=2)
            if not results:
                pytest.skip("未找到结果，可能是关键词问题或站点变更")
            
            # 验证返回的 URL 格式正确且可访问（HEAD 请求）
            for r in results:
                assert r['url'].startswith('https://i.imgflip.com/')
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                    resp = await client.head(r['url'])
                    # 某些 CDN 可能拒绝 HEAD，如果 403 但 URL 结构正确也算通过
                    assert resp.status_code in [200, 301, 302, 403, 405]
        except httpx.ConnectError:
            pytest.skip("无法连接到 Imgflip，可能由于网络环境或代理设置")
        except Exception as e:
            pytest.fail(f"集成测试失败: {e}")

if __name__ == "__main__":
    # 允许直接运行此文件进行快速测试
    asyncio.run(test_search_parsing_logic())
    print("Parsing logic test passed!")
