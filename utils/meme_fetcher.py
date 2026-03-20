import asyncio
import httpx
import random
import re
from typing import List, Dict, Any, Optional, Union
from bs4 import BeautifulSoup
import sys
import os

try:
    from utils.logger_config import get_module_logger
    logger = get_module_logger(__name__)
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

try:
    from utils.language_utils import is_china_region
except ImportError:
    def is_china_region() -> bool:
        import locale
        try:
            current_locale = locale.getdefaultlocale()[0]
            if current_locale:
                return current_locale.lower().startswith('zh_cn')
        except:
            pass
        return False

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
                    q_val = params.get('q') or params.get('keywords') if params else 'N/A'
                    logger.warning(f"触发频率限制 (429)，对于关键词: {q_val}")
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
                        "title": title,
                        "source": "Imgflip"
                    })
                elif item_type == "gif" and search_type in ["all", "gif"]:
                    results.append({
                        "type": "gif",
                        "id": item_id,
                        "url": f"https://i.imgflip.com/{item_id}.gif",
                        "page_url": f"{self.base_url}/gif/{item_id}",
                        "title": title,
                        "source": "Imgflip"
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

MEME_HOT_KEYWORDS = [
    "funny", "cat", "dog", "surprised", "laugh", "happy", "sad", "angry",
    "love", "cute", "anime", "gaming", "reaction", "mood", "relatable",
    "monday", "friday", "weekend", "work", "sleep", "coffee", "food",
    "confused", "excited", "tired", "bored", "awkward", "cringe", "wholesome",
    "sarcastic", "dramatic", "crying", "shocked", "scared",
    "thumbs up", "facepalm", "eye roll", "wink", "smile", "wave",
    "thank you", "sorry", "please", "no", "yes", "maybe", "whatever",
    "good luck", "congrats", "happy birthday", "get well", "miss you",
    "panda", "bear", "rabbit", "frog", "duck", "penguin",
    "drake", "distracted boyfriend", "woman yelling", "stonks", "this is fine",
    "galaxy brain", "expanding brain", "two buttons", "change my mind",
    "disaster girl", "hide the pain harold", "bad luck brian", "success kid",
]

MEME_HOT_KEYWORDS_CN = [
    "搞笑", "猫", "狗", "惊讶", "笑", "开心", "难过", "生气",
    "爱", "可爱", "动漫", "游戏", "反应", "心情", "真实",
    "周一", "周五", "周末", "工作", "睡觉", "咖啡", "美食",
    "熊猫头", "沙雕", "狗头", "滑稽", "大佬", "萌萌", "震惊",
    "无语", "疑惑", "期待", "满足", "无奈", "嘲讽", "佩服",
    "打工人", "社畜", "摸鱼", "躺平", "内卷", "摆烂", "卷王",
    "早安", "午安", "晚安", "早安打工人", "晚安玛卡巴卡", "起床困难",
    "加班", "下班", "上班", "迟到", "早退", "请假", "摸鱼中",
    "谢谢", "对不起", "没关系", "好的", "收到", "明白", "懂了",
    "牛逼", "厉害", "666", "绝了", "太强了", "膜拜",
    "不行", "不可以", "拒绝", "达咩", "不要", "别吧", "算了",
    "哈哈哈", "笑死", "笑哭", "笑不活了", "哈哈哈哈", "笑晕",
    "哭了", "泪目", "感动", "破防", "绷不住了", "泪崩",
    "迷茫", "懵逼", "黑人问号", "什么情况", "咋回事",
    "等待", "坐等", "蹲一个", "蹲后续", "催更",
    "加油", "冲", "冲鸭", "奥利给", "干饭", "干饭人",
    "猫猫", "狗狗", "兔兔", "猪猪", "鸭鸭", "鼠鼠", "牛牛",
    "表情包", "斗图", "怼人", "互怼", "吵架", "骂人",
    "想你", "思念", "想你啦", "想你了", "好想你",
    "生日快乐", "新年快乐", "节日快乐", "恭喜发财",
    "努力", "奋斗", "拼搏", "坚持", "不放弃",
]


class DoutubFetcher:
    """
    斗图吧 (doutub.com) 表情包爬取类
    国内网站，无需代理即可访问
    """
    def __init__(self):
        self.base_url = "https://www.doutub.com"
        self.search_url = f"{self.base_url}/search"
        self._session: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "DoutubFetcher":
        if self._session is None:
            self._session = httpx.AsyncClient(timeout=15.0, follow_redirects=True, trust_env=True)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def close(self):
        session = self._session
        if session:
            await session.aclose()
            self._session = None

    def _get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": f"{self.base_url}/",
            "Connection": "keep-alive",
        }

    async def _fetch_html(self, url: str, max_retries: int = 3) -> str:
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    delay = random.uniform(1.0, 2.0) * (2 ** attempt)
                    logger.info(f"斗图吧第 {attempt + 1} 次重试中，延迟 {delay:.2f}s...")
                    await asyncio.sleep(delay)
                else:
                    await asyncio.sleep(random.uniform(0.3, 0.8))

                headers = self._get_headers()
                session = self._session
                
                if session:
                    response = await session.get(url, headers=headers)
                else:
                    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, trust_env=True) as client:
                        response = await client.get(url, headers=headers)
                    
                response.raise_for_status()
                return response.text
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                logger.warning(f"斗图吧网络连接异常 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    raise
            except httpx.HTTPStatusError as e:
                logger.error(f"斗图吧HTTP错误 (状态码 {e.response.status_code}): {e}")
                if attempt == max_retries - 1:
                    raise
            except Exception as e:
                logger.error(f"斗图吧发生非预期异常: {e}")
                if attempt == max_retries - 1:
                    raise e
        return ""

    async def search(self, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        if not keyword:
            return []

        search_url = f"{self.search_url}/{keyword}"
        try:
            html = await self._fetch_html(search_url)
            if not html:
                return []
            
            soup = BeautifulSoup(html, 'html.parser')
            results = []
            
            def is_valid_meme_url(url: str) -> bool:
                if not url:
                    return False
                invalid_patterns = [
                    '/images/beian', 'beian_ico', 'footer', 'logo', 'avatar',
                    'icon', 'banner', 'ad_', 'loading', 'placeholder', 'qrcode'
                ]
                url_lower = url.lower()
                for pattern in invalid_patterns:
                    if pattern in url_lower:
                        return False
                return True
            
            img_items = soup.select('img.sc-fHeRUl')
            
            for img in img_items:
                if len(results) >= limit:
                    break
                
                src = img.get('src', '')
                title = img.get('alt', '') or img.get('title', '')
                
                if not src or not is_valid_meme_url(src):
                    continue
                
                if src.startswith('//'):
                    src = 'https:' + src
                
                item_id = ''
                if '/' in src:
                    item_id = src.split('/')[-1].split('.')[0]
                
                if not title:
                    title = f"表情包_{len(results) + 1}"
                
                img_type = 'gif' if '.gif' in src.lower() else 'meme'
                
                results.append({
                    "type": img_type,
                    "id": item_id,
                    "url": src,
                    "page_url": search_url,
                    "title": title,
                    "source": "斗图吧"
                })
            
            if not results:
                img_items = soup.select('div.cell a img')
                for img in img_items:
                    if len(results) >= limit:
                        break
                    
                    src = img.get('src', '') or img.get('data-src', '')
                    title = img.get('alt', '') or img.get('title', '')
                    
                    if not src or not is_valid_meme_url(src):
                        continue
                    
                    if src.startswith('//'):
                        src = 'https:' + src
                    
                    item_id = ''
                    if '/' in src:
                        item_id = src.split('/')[-1].split('.')[0]
                    
                    if not title:
                        title = f"表情包_{len(results) + 1}"
                    
                    img_type = 'gif' if '.gif' in src.lower() else 'meme'
                    
                    results.append({
                        "type": img_type,
                        "id": item_id,
                        "url": src,
                        "page_url": search_url,
                        "title": title,
                        "source": "斗图吧"
                    })
            
            if not results:
                img_items = soup.select('img[class*="sc-"]')
                for img in img_items:
                    if len(results) >= limit:
                        break
                    
                    src = img.get('src', '') or img.get('data-src', '')
                    title = img.get('alt', '') or img.get('title', '')
                    
                    if not src or not is_valid_meme_url(src):
                        continue
                    
                    if src.startswith('//'):
                        src = 'https:' + src
                    
                    item_id = ''
                    if '/' in src:
                        item_id = src.split('/')[-1].split('.')[0]
                    
                    if not title:
                        title = f"表情包_{len(results) + 1}"
                    
                    img_type = 'gif' if '.gif' in src.lower() else 'meme'
                    
                    results.append({
                        "type": img_type,
                        "id": item_id,
                        "url": src,
                        "page_url": search_url,
                        "title": title,
                        "source": "斗图吧"
                    })
            
            if not results:
                all_imgs = soup.find_all('img')
                for img in all_imgs:
                    if len(results) >= limit:
                        break
                    
                    src = img.get('src', '') or img.get('data-src', '')
                    title = img.get('alt', '') or img.get('title', '')
                    
                    if not src or not is_valid_meme_url(src):
                        continue
                    
                    if 'qn.doutub.com' not in src and 'doutub.com' not in src:
                        if not src.startswith('//'):
                            continue
                    
                    if src.startswith('//'):
                        src = 'https:' + src
                    
                    item_id = ''
                    if '/' in src:
                        item_id = src.split('/')[-1].split('.')[0]
                    
                    if not title:
                        title = f"表情包_{len(results) + 1}"
                    
                    img_type = 'gif' if '.gif' in src.lower() else 'meme'
                    
                    results.append({
                        "type": img_type,
                        "id": item_id,
                        "url": src,
                        "page_url": search_url,
                        "title": title,
                        "source": "斗图吧"
                    })
            
            logger.info(f"斗图吧搜索 '{keyword}' 完成，获得 {len(results)} 条结果")
            return results
            
        except Exception as e:
            logger.error(f"解析斗图吧搜索结果时出错: {e}")
            return []


class FabiaoqingFetcher:
    """
    发表情 (fabiaoqing.com) 表情包爬取类
    国内网站，无需代理即可访问
    """
    def __init__(self):
        self.base_url = "https://fabiaoqing.com"
        self.search_url = f"{self.base_url}/search/search/keyword"
        self._session: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "FabiaoqingFetcher":
        if self._session is None:
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.set_ciphers('DEFAULT@SECLEVEL=1')
            self._session = httpx.AsyncClient(
                timeout=15.0, 
                follow_redirects=True, 
                trust_env=True,
                verify=ssl_context
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def close(self):
        session = self._session
        if session:
            await session.aclose()
            self._session = None

    def _get_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": get_random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": f"{self.base_url}/",
            "Connection": "keep-alive",
        }

    async def _fetch_html(self, url: str, max_retries: int = 3) -> str:
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    delay = random.uniform(1.0, 2.0) * (2 ** attempt)
                    logger.info(f"发表情第 {attempt + 1} 次重试中，延迟 {delay:.2f}s...")
                    await asyncio.sleep(delay)
                else:
                    await asyncio.sleep(random.uniform(0.3, 0.8))

                headers = self._get_headers()
                session = self._session
                
                if session:
                    response = await session.get(url, headers=headers)
                else:
                    import ssl
                    ssl_context = ssl.create_default_context()
                    ssl_context.set_ciphers('DEFAULT@SECLEVEL=1')
                    async with httpx.AsyncClient(
                        timeout=15.0, 
                        follow_redirects=True, 
                        trust_env=True,
                        verify=ssl_context
                    ) as client:
                        response = await client.get(url, headers=headers)
                    
                response.raise_for_status()
                return response.text
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                logger.warning(f"发表情网络连接异常 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt == max_retries - 1:
                    raise
            except httpx.HTTPStatusError as e:
                logger.error(f"发表情HTTP错误 (状态码 {e.response.status_code}): {e}")
                if attempt == max_retries - 1:
                    raise
            except Exception as e:
                logger.error(f"发表情发生非预期异常: {e}")
                if attempt == max_retries - 1:
                    raise e
        return ""

    async def search(self, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        if not keyword:
            return []

        search_url = f"{self.search_url}/{keyword}"
        try:
            html = await self._fetch_html(search_url)
            if not html:
                return []
            
            soup = BeautifulSoup(html, 'html.parser')
            results = []
            
            def is_valid_meme_url(url: str) -> bool:
                if not url:
                    return False
                invalid_patterns = [
                    '/images/beian', 'beian_ico', 'footer', 'logo', 'avatar',
                    'icon', 'banner', 'ad_', 'loading', 'placeholder', 'qrcode'
                ]
                url_lower = url.lower()
                for pattern in invalid_patterns:
                    if pattern in url_lower:
                        return False
                return True
            
            img_items = soup.select('img.bqppsearch')
            
            for img in img_items:
                if len(results) >= limit:
                    break
                
                src = img.get('data-original', '') or img.get('src', '')
                title = img.get('alt', '') or img.get('title', '')
                
                if not src or not is_valid_meme_url(src):
                    continue
                
                if src.startswith('//'):
                    src = 'https:' + src
                
                item_id = ''
                if '/' in src:
                    item_id = src.split('/')[-1].split('.')[0]
                
                if not title:
                    title = f"表情包_{len(results) + 1}"
                
                img_type = 'gif' if '.gif' in src.lower() else 'meme'
                
                results.append({
                    "type": img_type,
                    "id": item_id,
                    "url": src,
                    "page_url": search_url,
                    "title": title,
                    "source": "发表情"
                })
            
            logger.info(f"发表情搜索 '{keyword}' 完成，获得 {len(results)} 条结果")
            return results
            
        except Exception as e:
            logger.error(f"解析发表情搜索结果时出错: {e}")
            return []


async def fetch_meme_content(keyword: str = '', limit: int = 5) -> dict:
    """
    高层封装：搜索表情包并返回结构化数据及格式化内容。
    用于主动搭话流程。
    根据用户区域选择表情包源：
    - 中文区域：优先使用国内网站（斗图吧、发表情）
    - 非中文区域：直接使用 Imgflip
    
    Args:
        keyword: 搜索关键词，为空时随机选择热门关键词
        limit: 返回结果数量限制
    
    Returns:
        dict: 包含 success, data, formatted_content, raw_data, keyword_used, source, region
    """
    china_region = is_china_region()
    
    actual_keyword = keyword
    
    if not actual_keyword:
        if china_region:
            actual_keyword = random.choice(MEME_HOT_KEYWORDS_CN)
        else:
            actual_keyword = random.choice(MEME_HOT_KEYWORDS)
        logger.info(f"未指定关键词，随机选择热门关键词: {actual_keyword}")
    
    results = []
    source_name = ""
    
    CN_FETCHERS = [
        (DoutubFetcher, "斗图吧"),
        (FabiaoqingFetcher, "发表情"),
    ]
    
    async def try_fetch(fetcher_class, name: str) -> bool:
        """尝试从指定源获取表情包，返回是否成功"""
        nonlocal results, source_name
        try:
            async with fetcher_class() as fetcher:
                fetched = await fetcher.search(actual_keyword, limit=limit)
                if fetched:
                    results = fetched
                    source_name = name
                    return True
        except Exception as e:
            logger.warning(f"{name}获取失败: {e}")
        return False
    
    if china_region:
        logger.info("检测到中文区域，使用国内表情包源")
        for fetcher_class, name in CN_FETCHERS:
            if await try_fetch(fetcher_class, name):
                break
        
        if not results:
            await try_fetch(MemeFetcher, "Imgflip")
    else:
        logger.info("检测到非中文区域，使用 Imgflip 表情包源")
        if not await try_fetch(MemeFetcher, "Imgflip"):
            for fetcher_class, name in CN_FETCHERS:
                if await try_fetch(fetcher_class, name):
                    break
    
    if not results:
        logger.error(f"所有表情包源均获取失败，关键词: {actual_keyword}")
        return {
            "success": False, 
            "data": [], 
            "formatted_content": "",
            "raw_data": {"data": []},
            "region": "china" if china_region else "non-china",
            "error": "所有表情包源均获取失败"
        }
    
    lines = [f"--- 搜到的表情包 ({actual_keyword}) - 来源: {source_name} ---"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']} | URL: {r['url']}")
    
    return {
        "success": True,
        "data": results,
        "formatted_content": "\n".join(lines),
        "raw_data": {"data": results},
        "keyword_used": actual_keyword,
        "source": source_name,
        "region": "china" if china_region else "non-china"
    }

# ==========================================
# 测试模块
# ==========================================

async def main():
    """单元测试功能"""
    test_keywords_cn = ["搞笑", "猫", "熊猫头"]
    
    print("=== 斗图吧 Meme Fetcher Test ===")
    async with DoutubFetcher() as fetcher:
        for kw in test_keywords_cn[:2]:
            print(f"\nSearching 斗图吧 for '{kw}'...")
            results = await fetcher.search(kw, limit=2)
            print(f"Total results: {len(results)}")
            for r in results:
                print(f"  - {r['title']}: {r['url']}")
    
    print("\n=== 发表情 Meme Fetcher Test ===")
    async with FabiaoqingFetcher() as fetcher:
        for kw in test_keywords_cn[:2]:
            print(f"\nSearching 发表情 for '{kw}'...")
            results = await fetcher.search(kw, limit=2)
            print(f"Total results: {len(results)}")
            for r in results:
                print(f"  - {r['title']}: {r['url']}")
    
    print("\n=== fetch_meme_content 综合测试 ===")
    for kw in ["", "搞笑", "猫"]:
        print(f"\n测试关键词: '{kw if kw else '(随机)'}'")
        result = await fetch_meme_content(keyword=kw, limit=3)
        print(f"成功: {result['success']}")
        print(f"来源: {result.get('source', 'N/A')}")
        print(f"关键词: {result.get('keyword_used', 'N/A')}")
        print(f"结果数: {len(result['data'])}")
        for r in result['data']:
            print(f"  - {r['title']}: {r['url']}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
