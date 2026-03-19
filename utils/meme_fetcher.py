import asyncio
import httpx
import random
import re
from typing import List, Dict, Any, Optional, Union
from bs4 import BeautifulSoup
import sys
import os

# 尝试导入项目内的 logger，如果失败则使用基本的 logging
try:
    from utils.logger_config import get_module_logger
    logger = get_module_logger(__name__)
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

# 更广泛且现代的 User-Agent 池
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Edge/122.0.0.0',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (iPad; CPU OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1',
]

def get_random_user_agent() -> str:
    """随机获取一个User-Agent"""
    return random.choice(USER_AGENTS)

class MemeFetcher:
    """
    Imgflip 表情包爬取类
    优化了反爬虫策略，支持通过关键词搜索普通表情包（meme）和动图（gif）
    支持异步上下文管理器以复用 Session
    """
    def __init__(self):
        self.base_url = "https://imgflip.com"
        self.search_url = f"{self.base_url}/search"
        self._session: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "MemeFetcher":
        """进入异步上下文，初始化持久 Session"""
        if self._session is None:
            self._session = httpx.AsyncClient(timeout=15.0, follow_redirects=True, trust_env=True)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """退出异步上下文，关闭 Session"""
        await self.close()

    async def close(self):
        """关闭持久 Session"""
        session = self._session
        if session:
            await session.aclose()
            self._session = None

    def _get_random_headers(self) -> Dict[str, str]:
        """生成随机且真实的浏览器请求头，包含 Referer 和其他防爬字段"""
        referers = [
            f"{self.base_url}/",
            f"{self.base_url}/memegenerator",
            f"{self.base_url}/memetemplates",
            "https://www.google.com/",
            "https://www.bing.com/",
            "https://duckduckgo.com/"
        ]
        referer = random.choice(referers)
        
        headers = {
            "User-Agent": get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Referer": referer,
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin" if self.base_url in referer else "cross-site",
            "Cache-Control": "max-age=0",
            "DNT": "1", # Do Not Track
        }
        return headers

    async def _fetch_html(self, url: str, params: Optional[Dict[str, str]] = None, max_retries: int = 3) -> str:
        """异步获取 HTML 内容，带指数退避重试和随机抖动。支持复用 self._session"""
        for attempt in range(max_retries):
            try:
                # 指数退避 (Exponential Backoff): 1s, 2s, 4s...
                # 加上随机抖动 (Jitter)
                if attempt > 0:
                    delay = random.uniform(1.0, 2.0) * (2 ** attempt)
                    logger.info(f"第 {attempt + 1} 次重试中，延迟 {delay:.2f}s...")
                    await asyncio.sleep(delay)
                else:
                    # 正常请求之间的随机间隔
                    await asyncio.sleep(random.uniform(0.5, 1.5))

                headers = self._get_random_headers()
                session = self._session
                
                if session:
                    # 使用持久化 Session
                    response = await session.get(url, params=params, headers=headers)
                else:
                    # 使用临时 Client
                    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, trust_env=True) as client:
                        response = await client.get(url, params=params, headers=headers)
                    
                if response.status_code == 429:
                    logger.warning(f"触发频率限制 (429)，对于关键词: {params.get('q') if params else 'N/A'}")
                    continue
                elif response.status_code == 403:
                    logger.warning(f"由于反爬拦截被拒绝 (403)，尝试更换请求头重试...")
                    continue
                    
                response.raise_for_status()
                return response.text
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                logger.warning(f"网络连接异常 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    raise
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP 错误 (状态码 {e.response.status_code}): {e}")
                if attempt == max_retries - 1:
                    raise
            except Exception as e:
                logger.error(f"发生非预期异常 ({url}): {e}")
                if attempt == max_retries - 1:
                    raise e
                    
        return ""

    async def search(self, keyword: str, limit: int = 10, search_type: str = "all") -> List[Dict[str, Any]]:
        """
        在 Imgflip 搜索表情包和动图，采用防爬虫优化的请求逻辑
        """
        if not keyword:
            return []

        params = {"q": keyword}
        try:
            html = await self._fetch_html(self.search_url, params=params)
            if not html:
                return []
                
            soup = BeautifulSoup(html, 'html.parser')
            
            # Imgflip 的搜索结果
            target_links = soup.find_all('a', href=re.compile(r'^/(i|gif)/[a-zA-Z0-9]+$'))
            
            results = []
            seen_ids = set()
            
            for link in target_links:
                if len(results) >= limit:
                    break
                    
                href = link.get('href', '')
                parts = href.strip('/').split('/')
                if len(parts) < 2:
                    continue
                    
                item_type = parts[0]
                item_id = parts[1]
                
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                
                # 提取标题：优先使用图片 alt（通常包含具体文字），其次是链接 title
                img = link.find('img')
                title = ""
                if img and img.get('alt'):
                    title = img.get('alt')
                if not title:
                    title = link.get('title')
                if not title:
                    title = link.get_text(strip=True)
                
                if title:
                    # 使用正则清理 " (user-captioned meme)" 或 " (user-generated gif)"
                    title = re.sub(r'\s*\((user-captioned meme|user-generated gif)\)', '', title, flags=re.I).strip()
                else:
                    title = f"{keyword} {item_type}"

                if item_type == "i" and search_type in ["all", "meme"]:
                    results.append({
                        "type": "meme",
                        "id": item_id,
                        "url": f"https://i.imgflip.com/{item_id}.jpg",
                        "page_url": f"{self.base_url}/i/{item_id}",
                        "title": title
                    })
                elif item_type == "gif" and search_type in ["all", "gif"]:
                    results.append({
                        "type": "gif",
                        "id": item_id,
                        "url": f"https://i.imgflip.com/{item_id}.gif",
                        "page_url": f"{self.base_url}/gif/{item_id}",
                        "title": title
                    })
            
            logger.info(f"Imgflip 搜索 '{keyword}' (type={search_type}) 完成，获得 {len(results)} 条结果")
            return results
            
        except Exception as e:
            logger.error(f"解析 Imgflip 搜索结果时出错: {e}")
            return []

    async def search_memes(self, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        """搜索图片表情包"""
        return await self.search(keyword, limit, search_type="meme")

    async def search_gifs(self, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        """搜索 GIF 表情包"""
        return await self.search(keyword, limit, search_type="gif")

# ==========================================
# 测试模块
# ==========================================

async def main():
    """单元测试功能"""
    test_keywords = ["cat", "surprised", "laugh"]
    
    print("=== Imgflip Meme Fetcher Test (Session Optimized) ===")
    
    # 使用异步上下文管理器以复用 Session，提高批量搜索效率
    async with MemeFetcher() as fetcher:
        for kw in test_keywords:
            print(f"\nSearching for '{kw}'...")
            
            # 测试综合搜索
            results = await fetcher.search(kw, limit=3)
            print(f"Total results: {len(results)}")
            for r in results:
                print(f"[{r['type'].upper()}] {r['title']}")
                print(f"  URL: {r['url']}")
                
            # 测试仅 GIF
            gifs = await fetcher.search_gifs(kw, limit=2)
            print(f"GIFs only for '{kw}': {len(gifs)}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
