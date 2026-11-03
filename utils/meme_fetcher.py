# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import ssl
import httpx
import random
import re
from typing import List, Dict, Any, Optional, Union
import sys
import os
import jmespath
from urllib.parse import quote, quote_plus, urljoin, urlparse

# Global meme-image source allowlist registry, maintained by scrapers and referenced by other modules
MEME_ALLOWED_HOSTS = [
    # 2026-04-16: disable doutub.com after ownership changed and malicious gambling content appeared
    # 'qn.doutub.com',
    # 'doutub.com',
    'img.soutula.com', 'i.imgflip.com',
    'fabiaoqing.com', 'soutula.com',
    'img.doutupk.com', 'doutupk.com',
]

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
            # Try modern APIs first (Python 3.11+)
            try:
                # getlocale() may return (None, None) without raising in some environments
                res = locale.getlocale()
                current_locale = res[0] if res else None
            except Exception:
                current_locale = None

            # If getlocale() fails or returns empty, try getdefaultlocale() or environment variables
            if not current_locale:
                try:
                    current_locale = locale.getdefaultlocale()[0]
                except Exception:
                    current_locale = os.environ.get('LANG') or os.environ.get('LC_ALL')
                
            if current_locale:
                return current_locale.lower().startswith('zh_cn')
        except Exception as e:
            logger.debug(f"获取区域设置失败: {e}")
        return False

try:
    from utils.source_locale import source_region_from_locale
except ImportError:
    def source_region_from_locale(source_locale: str | None) -> str | None:
        return None

# Broader modern User-Agent pool
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
    """Get a random User-Agent"""
    return random.choice(USER_AGENTS)


def _clean_imgflip_title(raw: str) -> str:
    """
    Clean title/alt text from Imgflip search results.
    The raw text may be concatenation produced by get_text(separator=' '), e.g.
      '(Daffy Duck Meme) user-captioned meme, 114 views WHAT'S WRONG WITH YOU?'
    or from img.alt:
      'WHAT'S WRONG WITH YOU? | image tagged in daffy duck | made w/ Imgflip meme maker'

    Cleaning strategy: strip the metadata, keeping only the parts meaningful to users.
    """
    s = raw.strip()
    # Remove "| image tagged in ..." and everything afterward
    s = re.split(r'\s*\|\s*image tagged\b', s, maxsplit=1, flags=re.I)[0]
    # Remove "| made w/ Imgflip ..."
    s = re.split(r'\s*\|\s*made w/', s, maxsplit=1, flags=re.I)[0]
    # Remove "user-captioned meme" / "user-generated gif" and optional following ", N views"
    s = re.sub(
        r',?\s*\(?\buser-(?:captioned meme|generated gif)\)?,?\s*(?:\d[\d,]*\s*views?\b)?',
        '', s, flags=re.I,
    )
    # Remove standalone "N views"
    s = re.sub(r',?\s*\b\d[\d,]*\s+views?\b', '', s, flags=re.I)
    # Remove parenthesized template names such as "(Template Name Meme)" only when substantive content remains
    candidate = re.sub(r'\([^)]{1,60}\b[Mm]eme\)\s*', '', s).strip()
    if candidate:
        s = candidate
    # Unwrap results wholly enclosed in parentheses, such as "(Some Name)"
    s = re.sub(r'^\(([^)]+)\)$', r'\1', s.strip())
    # Collapse whitespace and trim leading/trailing pipes/commas
    s = re.sub(r'\s+', ' ', s).strip().strip('|,').strip()
    return s


class MemeFetcher:
    """
    Imgflip meme crawler class
    Optimized anti-crawling strategy; supports keyword search for regular memes and GIFs
    Supports async context manager for Session reuse
    """
    def __init__(self):
        self.base_url = "https://imgflip.com"
        self.search_url = f"{self.base_url}/search"
        self._session: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "MemeFetcher":
        """Enter the async context, initializing a persistent Session"""
        if self._session is None:
            self._session = httpx.AsyncClient(timeout=10.0, follow_redirects=True, trust_env=True)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit the async context, closing the Session"""
        await self.close()

    async def close(self):
        """Close the persistent Session"""
        session = self._session
        if session:
            await session.aclose()
            self._session = None

    def _get_random_headers(self) -> Dict[str, str]:
        """Generate random, realistic browser request headers, including Referer and other anti-crawl fields"""
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
        """Fetch HTML content asynchronously, with exponential-backoff retries and random jitter. Supports reusing self._session"""
        for attempt in range(max_retries):
            try:
                # Exponential backoff: 1s, 2s, 4s...
                # Add random jitter
                if attempt > 0:
                    delay = random.uniform(1.0, 2.0) * (2 ** attempt)
                    logger.info(f"第 {attempt + 1} 次重试中，延迟 {delay:.2f}s...")
                    await asyncio.sleep(delay)
                else:
                    # Random delay between normal requests
                    await asyncio.sleep(random.uniform(0.5, 1.5))

                headers = self._get_random_headers()
                session = self._session
                
                if session:
                    # Use a persistent Session
                    response = await session.get(url, params=params, headers=headers)
                else:
                    # Fallback: reuse the external shared client when this instance was not opened
                    from utils.external_http_client import get_external_http_client
                    client = get_external_http_client()
                    response = await client.get(url, params=params, headers=headers, timeout=10.0)
                    
                if response.status_code == 429:
                    q_val = params.get('q') or params.get('keywords') if params else 'N/A'
                    logger.warning(f"触发频率限制 (429)，对于关键词: {q_val}")
                    continue
                elif response.status_code == 403:
                    logger.warning("由于反爬拦截被拒绝 (403)，尝试更换请求头重试...")
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
        Search memes and GIFs on Imgflip, using anti-crawler-optimized request logic
        """
        if not keyword:
            return []

        params = {"q": keyword}
        try:
            html = await self._fetch_html(self.search_url, params=params)
            if not html:
                return []

            # Parse BS4 in a thread pool to avoid blocking the event loop
            from bs4 import BeautifulSoup
            soup = await asyncio.to_thread(BeautifulSoup, html, 'lxml')

            # Imgflip search results
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
                
                # Extract title in priority order: img alt -> link title -> get_text with spaces
                img = link.find('img')
                title = ""
                used_get_text = False
                if img and img.get('alt'):
                    title = img.get('alt')
                if not title:
                    title = link.get('title') or ''
                if not title:
                    title = link.get_text(separator=' ', strip=True)
                    used_get_text = True

                if title:
                    title = _clean_imgflip_title(title)

                if not title and not used_get_text:
                    raw_text = link.get_text(separator=' ', strip=True)
                    if raw_text:
                        title = _clean_imgflip_title(raw_text)

                if not title:
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
        """Search image memes"""
        return await self.search(keyword, limit, search_type="meme")

    async def search_gifs(self, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Search GIF memes"""
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


def _is_valid_meme_url(url: str) -> bool:
    """Check whether a URL is a valid meme image URL"""
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

# ⚠️ 2026-04-16: doutub.com changed owners (RDAP last changed that day), and its actual IP serves malicious gambling pages;
# no official replacement domain was announced. Scheduling disables this scraper (see CN_FETCHERS/main); retain its definition only for reference.
# Restore if Doutub adopts a new domain, updating MEME_ALLOWED_HOSTS and referer_map together.
class DoutubFetcher:
    """
    Doutuba (doutub.com) meme crawler class
    Chinese site, accessible without a proxy
    """
    def __init__(self):
        self.base_url = "https://www.doutub.com"
        self.search_url = f"{self.base_url}/search"
        self._session: Optional[httpx.AsyncClient] = None

    def _add_meme_item(self, results: list, found_urls: set, url: str, title_raw: str, id_raw: str, search_url: str):
        """Unified private helper for data assembly and filtering, following DRY"""
        if not url or not isinstance(url, str) or not _is_valid_meme_url(url):
            return
        
        src = url if url.startswith('http') else ('https:' + url if url.startswith('//') else f"https://www.doutub.com{url}")
        if src in found_urls:
            return
        
        title = title_raw if title_raw else f"表情包_{len(results) + 1}"
        item_id = str(id_raw) or (src.split('/')[-1].split('.')[0] if '/' in src else '')
        img_type = 'gif' if '.gif' in src.lower() else 'meme'
        
        results.append({
            "type": img_type,
            "id": item_id,
            "url": src,
            "page_url": search_url,
            "title": title,
            "source": "斗图吧"
        })
        found_urls.add(src)

    async def __aenter__(self) -> "DoutubFetcher":
        if self._session is None:
            self._session = httpx.AsyncClient(timeout=10.0, follow_redirects=True, trust_env=True)
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

    def _create_client(self, seclevel1: bool = False) -> httpx.AsyncClient:
        """Create an AsyncClient on demand, supporting SECLEVEL=1 downgrade"""
        if seclevel1:
            context = ssl.create_default_context()
            try:
                context.set_ciphers('DEFAULT@SECLEVEL=1')
            except Exception as e:
                logger.warning(f"设置 SECLEVEL=1 失败: {e}")
            
            return httpx.AsyncClient(
                timeout=10.0, 
                follow_redirects=True, 
                verify=context,
                trust_env=True
            )
        else:
            return httpx.AsyncClient(
                timeout=10.0, 
                follow_redirects=True, 
                trust_env=True
            )

    async def _fetch_html(self, url: str, max_retries: int = 3) -> str:
        last_exception = None
        backoff_factor = random.uniform(1.0, 1.5)
        
        for attempt in range(max_retries):
            try:
                if attempt == 0:
                    await asyncio.sleep(random.uniform(0.1, 0.3))
                
                headers = self._get_headers()
                
                # Default TLS attempt
                try:
                    if self._session:
                        response = await self._session.get(url, headers=headers)
                    else:
                        async with self._create_client(seclevel1=False) as temp_client:
                            response = await temp_client.get(url, headers=headers)
                    response.raise_for_status()
                    return response.text
                except ssl.SSLError as e:
                    logger.warning(f"斗图吧 HTTPS SSL握手失败，尝试降低安全级别重试 (第{attempt+1}次): {e}")
                    try:
                        # Attempt downgraded TLS only after SSL errors
                        if self._session:
                            await self.close()
                            self._session = self._create_client(seclevel1=True)
                            response = await self._session.get(url, headers=headers)
                        else:
                            async with self._create_client(seclevel1=True) as temp_client:
                                response = await temp_client.get(url, headers=headers)
                        response.raise_for_status()
                        return response.text
                    except Exception as inner_e:
                        logger.warning(f"斗图吧降级请求依然失败 (第{attempt+1}次): {inner_e}")
                        last_exception = inner_e
                        # Continue to the sleep below and begin the next attempt
                except (httpx.TransportError, httpx.ConnectError) as e:
                    # Check for underlying SSL handshake failure, possibly wrapped by httpx
                    is_ssl_error = isinstance(e.__cause__, ssl.SSLError) or "SSL" in str(e) or "handshake" in str(e).lower()
                    if is_ssl_error:
                        logger.warning(f"检测到可能的 TLS 握手异常，尝试降级重试 (第{attempt+1}次): {e}")
                        try:
                            if self._session:
                                await self.close()
                                self._session = self._create_client(seclevel1=True)
                                response = await self._session.get(url, headers=headers)
                            else:
                                async with self._create_client(seclevel1=True) as temp_client:
                                    response = await temp_client.get(url, headers=headers)
                            response.raise_for_status()
                            return response.text
                        except Exception as inner_e:
                            logger.warning(f"斗图吧降级重试依然失败: {inner_e}")
                            last_exception = inner_e
                    else:
                        logger.warning(f"斗图吧 HTTPS 网络传输异常 (第{attempt+1}次): {e}")
                        last_exception = e
                
            except (httpx.ConnectError, httpx.TimeoutException, ssl.SSLError) as e:
                logger.warning(f"斗图吧网络连接异常 (尝试 {attempt + 1}/{max_retries}): {e}")
                last_exception = e
            except httpx.HTTPStatusError as e:
                logger.error(f"斗图吧HTTP错误 (状态码 {e.response.status_code}): {e}")
                last_exception = e
                # Retrying some HTTP errors such as 404 may be pointless, but follow the global retry policy here
            
            # Exponential-backoff sleep
            if attempt < max_retries - 1:
                delay = backoff_factor * (2 ** attempt)
                await asyncio.sleep(delay)
                
        raise last_exception or Exception(f"达到最大重试次数 ({max_retries})，抓取失败")

    async def search(self, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        if not keyword:
            return []

        search_url = f"{self.search_url}/{quote(keyword, safe='')}"
        try:
            html = await self._fetch_html(search_url)
            if not html:
                return []

            import re
            import json

            # Offload BS4 + DOTALL regex + JSON parsing together; full-HTML .*? scans
            # can block the event loop for hundreds of milliseconds.
            def _parse_html_blob(_html: str) -> tuple:
                from bs4 import BeautifulSoup
                _soup = BeautifulSoup(_html, 'lxml')
                # Try framework SSR formats in priority order: Next.js → Nuxt.js → generic INITIAL_STATE.
                # Silently catch JSON parsing errors for every format, since page scripts may be minified/truncated/obfuscated;
                # leave _ssr=None so callers fall back to XHR API scraping below without interrupting flow.
                _ssr = None
                _nuxt = re.search(r'window\.__NUXT__\s*=\s*({.*?});', _html, re.DOTALL)
                _next = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', _html, re.DOTALL)
                _init = re.search(r'window\.__INITIAL_STATE__\s*=\s*({.*?});', _html, re.DOTALL)
                if _next:
                    try:
                        _ssr = json.loads(_next.group(1))
                    except json.JSONDecodeError:
                        pass  # See SSR fallback notes above
                elif _nuxt:
                    try:
                        _ssr = json.loads(_nuxt.group(1))
                    except json.JSONDecodeError:
                        pass  # See SSR fallback notes above
                elif _init:
                    try:
                        _ssr = json.loads(_init.group(1))
                    except json.JSONDecodeError:
                        pass  # See SSR fallback notes above
                return _soup, _ssr

            soup, ssr_data = await asyncio.to_thread(_parse_html_blob, html)
            results = []

            found_urls = set()
            
            # Extract specific framework structures instead of recursively searching everything indiscriminately
            if ssr_data:
                possible_paths = [
                    "props.pageProps.data.rows",
                    "props.pageProps.list",
                    "props.pageProps.memeList",
                    "payload.data[0].list",
                    "payload.data[0].rows",
                    "data.rows"
                ]
                meme_list = []
                for path in possible_paths:
                    res = jmespath.search(path, ssr_data)
                    if isinstance(res, list) and len(res) > 0:
                        meme_list = res
                        break
                
                for item in meme_list:
                    if len(results) >= limit:
                        break
                    if not isinstance(item, dict):
                        continue
                    url = item.get('url') or item.get('src') or item.get('imgUrl') or item.get('path')
                    title_raw = item.get('title') or item.get('alt') or item.get('name') or ''
                    id_raw = item.get('id', '')
                    self._add_meme_item(results, found_urls, url, title_raw, id_raw, search_url)

            # If SSR extraction leaves results empty, try the internal XHR API directly
            if not results:
                api_url = f"{self.base_url}/api/search/emotion/list"
                api_params = {"keyword": keyword, "page": 1}
                try:
                    if self._session:
                        api_resp = await self._session.get(api_url, params=api_params, headers=self._get_headers())
                    else:
                        # Fallback: reuse the external shared client
                        from utils.external_http_client import get_external_http_client
                        client = get_external_http_client()
                        api_resp = await client.get(api_url, params=api_params, headers=self._get_headers(), timeout=10.0)
                    
                    if api_resp.status_code == 200:
                        api_data = api_resp.json()
                        rows = api_data.get('data', {}).get('rows', []) or api_data.get('data', [])
                        if isinstance(rows, list):
                            for item in rows:
                                if len(results) >= limit:
                                    break
                                src = item.get('url', '') or item.get('path', '')
                                title_raw = item.get('name', '') or item.get('title', '')
                                id_raw = item.get('id', '')
                                self._add_meme_item(results, found_urls, src, title_raw, id_raw, search_url)
                except Exception as e:
                    logger.debug(f"尝试抓取 XHR API 失败: {e}")

            # Last resort: robust generic selectors only; avoid frontend hashed class names such as sc-fHeRUl
            if not results:
                image_selectors = [
                    'div.cell a img',
                    'a.pic-link img',
                    'div.img-box img'
                ]
                for selector in image_selectors:
                    if len(results) >= limit:
                        break
                    img_items = soup.select(selector)
                    for img in img_items:
                        if len(results) >= limit:
                            break
                        src = img.get('src', '') or img.get('data-src', '')
                        title_raw = img.get('alt', '') or img.get('title', '')
                        id_raw = ''
                        self._add_meme_item(results, found_urls, src, title_raw, id_raw, search_url)
            
            logger.info(f"斗图吧搜索 '{keyword}' 完成，获得 {len(results)} 条结果")
            return results
            
        except Exception as e:
            logger.error(f"解析斗图吧搜索结果时出错: {e}")
            return []


class DoutupkFetcher:
    """
    Doutula (doutupk.com) meme crawler class
    Chinese site, server-rendered HTML + lazy-loaded images. Adopted on 2026-04-16 to replace the defunct Doutuba.
    """
    def __init__(self):
        self.base_url = "https://www.doutupk.com"
        self.search_url = f"{self.base_url}/search"
        self._session: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "DoutupkFetcher":
        if self._session is None:
            self._session = httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True,
                trust_env=True,
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

    def _create_client(self, seclevel1: bool = False) -> httpx.AsyncClient:
        if seclevel1:
            context = ssl.create_default_context()
            try:
                context.set_ciphers('DEFAULT@SECLEVEL=1')
            except Exception as e:
                logger.warning(f"设置 SECLEVEL=1 失败: {e}")
            return httpx.AsyncClient(timeout=10.0, follow_redirects=True, verify=context, trust_env=True)
        return httpx.AsyncClient(timeout=10.0, follow_redirects=True, trust_env=True)

    async def _fetch_html(self, url: str, max_retries: int = 3) -> str:
        last_exception = None
        backoff_factor = random.uniform(1.0, 1.5)

        for attempt in range(max_retries):
            try:
                if attempt == 0:
                    await asyncio.sleep(random.uniform(0.1, 0.3))

                headers = self._get_headers()

                try:
                    if self._session:
                        response = await self._session.get(url, headers=headers)
                    else:
                        async with self._create_client(seclevel1=False) as temp_client:
                            response = await temp_client.get(url, headers=headers)
                    response.raise_for_status()
                    return response.text
                except ssl.SSLError as e:
                    logger.warning(f"斗图啦 HTTPS SSL握手失败，尝试降低安全级别重试 (第{attempt+1}次): {e}")
                    try:
                        if self._session:
                            await self.close()
                            self._session = self._create_client(seclevel1=True)
                            response = await self._session.get(url, headers=headers)
                        else:
                            async with self._create_client(seclevel1=True) as temp_client:
                                response = await temp_client.get(url, headers=headers)
                        response.raise_for_status()
                        return response.text
                    except Exception as inner_e:
                        logger.warning(f"斗图啦降级请求依然失败 (第{attempt+1}次): {inner_e}")
                        last_exception = inner_e
                except (httpx.TransportError, httpx.ConnectError) as e:
                    is_ssl_error = isinstance(e.__cause__, ssl.SSLError) or "SSL" in str(e) or "handshake" in str(e).lower()
                    if is_ssl_error:
                        logger.warning(f"检测到可能的 TLS 握手异常，尝试降级重试 (第{attempt+1}次): {e}")
                        try:
                            if self._session:
                                await self.close()
                                self._session = self._create_client(seclevel1=True)
                                response = await self._session.get(url, headers=headers)
                            else:
                                async with self._create_client(seclevel1=True) as temp_client:
                                    response = await temp_client.get(url, headers=headers)
                            response.raise_for_status()
                            return response.text
                        except Exception as inner_e:
                            logger.warning(f"斗图啦降级重试依然失败: {inner_e}")
                            last_exception = inner_e
                    else:
                        logger.warning(f"斗图啦 HTTPS 网络传输异常 (第{attempt+1}次): {e}")
                        last_exception = e

            except (httpx.ConnectError, httpx.TimeoutException, ssl.SSLError) as e:
                logger.warning(f"斗图啦网络连接异常 (尝试 {attempt + 1}/{max_retries}): {e}")
                last_exception = e
            except httpx.HTTPStatusError as e:
                logger.error(f"斗图啦HTTP错误 (状态码 {e.response.status_code}): {e}")
                last_exception = e
            except Exception as e:
                logger.error(f"斗图啦发生非预期异常: {e}")
                last_exception = e

            if attempt < max_retries - 1:
                delay = backoff_factor * (2 ** attempt)
                await asyncio.sleep(delay)

        raise last_exception or Exception(f"达到最大重试次数 ({max_retries})，抓取失败")

    async def search(self, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        if not keyword:
            return []

        search_url = f"{self.search_url}?keyword={quote(keyword, safe='')}"
        try:
            html = await self._fetch_html(search_url)
            if not html:
                return []

            # Parse BS4 in a thread pool
            from bs4 import BeautifulSoup
            soup = await asyncio.to_thread(BeautifulSoup, html, 'lxml')
            results: List[Dict[str, Any]] = []

            # Lazy-loading structure: img.image_dtb[data-original=actual URL], with src containing a loader placeholder
            for img in soup.select('img.image_dtb'):
                if len(results) >= limit:
                    break

                src = img.get('data-original') or img.get('data-backup') or ''
                # Filter placeholders such as loader.gif/gif.png, decorative assets from static.doutupk.com
                if not src or 'static.doutupk.com' in src or not _is_valid_meme_url(src):
                    continue

                # If the site returns a root-relative /uploads/... path, urljoin makes it absolute;
                # existing absolute URLs pass through unchanged. Otherwise frontend safeUrl rejects it and creates a false-positive race winner.
                src = urljoin(self.base_url + '/', src)
                # Upgrade consistently to https, avoiding mixed content and strict validation in some clients
                if src.startswith('http://'):
                    src = 'https://' + src[len('http://'):]

                # Detail-page URLs can serve as click-through page_url
                parent_a = img.find_parent('a')
                page_url = parent_a.get('href') if parent_a and parent_a.get('href') else search_url

                title = img.get('alt') or img.get('title') or ''
                if not title:
                    title = f"表情包_{len(results) + 1}"

                item_id = src.rsplit('/', 1)[-1].split('.')[0] if '/' in src else ''
                img_type = 'gif' if '.gif' in src.lower() else 'meme'

                results.append({
                    "type": img_type,
                    "id": item_id,
                    "url": src,
                    "page_url": page_url,
                    "title": title,
                    "source": "斗图啦",
                })

            logger.info(f"斗图啦搜索 '{keyword}' 完成，获得 {len(results)} 条结果")
            return results

        except Exception as e:
            logger.error(f"解析斗图啦搜索结果时出错: {e}")
            return []


class FabiaoqingFetcher:
    """
    Fabiaoqing (fabiaoqing.com) meme crawler class
    Chinese site, accessible without a proxy
    """
    def __init__(self):
        self.base_url = "https://fabiaoqing.com"
        self.search_url = f"{self.base_url}/search/search/keyword"
        self._session: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "FabiaoqingFetcher":
        if self._session is None:
            # Progressive TLS strategy: system's strongest encryption first, downgrading to SECLEVEL=1 on failure
            self._session = httpx.AsyncClient(
                timeout=10.0, 
                follow_redirects=True, 
                trust_env=True,
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

    def _create_client(self, seclevel1: bool = False) -> httpx.AsyncClient:
        """Create an AsyncClient on demand, supporting SECLEVEL=1 downgrade"""
        if seclevel1:
            # Force SECLEVEL=1 for certain mainland sites using old SSL protocols
            # Although verify=False is insecure, it is a necessary compromise for scraping public, nonsensitive memes here
            context = ssl.create_default_context()
            try:
                context.set_ciphers('DEFAULT@SECLEVEL=1')
            except Exception as e:
                logger.warning(f"设置 SECLEVEL=1 失败: {e}")
            
            return httpx.AsyncClient(
                timeout=10.0, 
                follow_redirects=True, 
                verify=context,
                trust_env=True
            )
        else:
            return httpx.AsyncClient(
                timeout=10.0, 
                follow_redirects=True, 
                trust_env=True
            )

    async def _fetch_html(self, url: str, max_retries: int = 3) -> str:
        last_exception = None
        backoff_factor = random.uniform(1.0, 1.5)

        for attempt in range(max_retries):
            try:
                if attempt == 0:
                    await asyncio.sleep(random.uniform(0.1, 0.3))
                
                headers = self._get_headers()
                
                # Default TLS attempt
                try:
                    if self._session:
                        response = await self._session.get(url, headers=headers)
                    else:
                        async with self._create_client(seclevel1=False) as temp_client:
                            response = await temp_client.get(url, headers=headers)
                    response.raise_for_status()
                    return response.text
                except ssl.SSLError as e:
                    logger.warning(f"发表情 HTTPS SSL握手失败，尝试降低安全级别重试 (第{attempt+1}次): {e}")
                    try:
                        # Attempt downgraded TLS only after SSL errors
                        if self._session:
                            await self.close()
                            self._session = self._create_client(seclevel1=True)
                            response = await self._session.get(url, headers=headers)
                        else:
                            async with self._create_client(seclevel1=True) as temp_client:
                                response = await temp_client.get(url, headers=headers)
                        response.raise_for_status()
                        return response.text
                    except Exception as inner_e:
                        logger.warning(f"发表情降级请求依然失败 (第{attempt+1}次): {inner_e}")
                        last_exception = inner_e
                        # Continue to the sleep below and begin the next attempt
                except (httpx.TransportError, httpx.ConnectError) as e:
                    # Check for underlying SSL handshake failure, possibly wrapped by httpx
                    is_ssl_error = isinstance(e.__cause__, ssl.SSLError) or "SSL" in str(e) or "handshake" in str(e).lower()
                    if is_ssl_error:
                        logger.warning(f"检测到可能的 TLS 握手异常，尝试降级重试 (第{attempt+1}次): {e}")
                        try:
                            if self._session:
                                await self.close()
                                self._session = self._create_client(seclevel1=True)
                                response = await self._session.get(url, headers=headers)
                            else:
                                async with self._create_client(seclevel1=True) as temp_client:
                                    response = await temp_client.get(url, headers=headers)
                            response.raise_for_status()
                            return response.text
                        except Exception as inner_e:
                            logger.warning(f"发表情降级重试依然失败: {inner_e}")
                            last_exception = inner_e
                    else:
                        logger.warning(f"发表情 HTTPS 网络传输异常 (第{attempt+1}次): {e}")
                        last_exception = e
                
            except (httpx.ConnectError, httpx.TimeoutException, ssl.SSLError) as e:
                logger.warning(f"发表情网络连接异常 (尝试 {attempt + 1}/{max_retries}): {e}")
                last_exception = e
            except httpx.HTTPStatusError as e:
                logger.error(f"发表情HTTP错误 (状态码 {e.response.status_code}): {e}")
                last_exception = e
            except Exception as e:
                logger.error(f"发表情发生非预期异常: {e}")
                last_exception = e
            
            # Exponential-backoff sleep
            if attempt < max_retries - 1:
                delay = backoff_factor * (2 ** attempt)
                await asyncio.sleep(delay)
                
        raise last_exception or Exception(f"达到最大重试次数 ({max_retries})，抓取失败")

    async def search(self, keyword: str, limit: int = 10) -> List[Dict[str, Any]]:
        if not keyword:
            return []

        search_url = f"{self.search_url}/{quote(keyword, safe='')}"
        try:
            html = await self._fetch_html(search_url)
            if not html:
                return []

            # Parse BS4 in a thread pool
            from bs4 import BeautifulSoup
            soup = await asyncio.to_thread(BeautifulSoup, html, 'lxml')
            results = []

            img_items = soup.select('img.bqppsearch')
            
            for img in img_items:
                if len(results) >= limit:
                    break
                
                src = img.get('data-original', '') or img.get('src', '')
                title = img.get('alt', '') or img.get('title', '')
                
                if not src or not _is_valid_meme_url(src):
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


async def fetch_meme_content(keyword: str = '', limit: int = 5, source_locale: str | None = None) -> dict:
    """
    High-level wrapper: search memes and return structured data plus formatted content.
    Used by the proactive-chat flow.
    Picks meme sources by user region:
    - Chinese region: prefer Chinese sites (Doutuba, Fabiaoqing)
    - non-Chinese region: use Imgflip directly
    The source order defaults to runtime region detection. When source_locale is
    provided, that content locale controls the source order instead.
    
    Args:
        keyword: search keyword; a random hot keyword is picked when empty
        limit: max number of results
        source_locale: optional content/source locale. When omitted, runtime region detection is used.
    
    Returns:
        dict: contains success, data, formatted_content, raw_data, keyword_used, source, region
    """
    source_region = source_region_from_locale(source_locale)
    china_region = is_china_region() if source_region is None else source_region == "china"
    
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
        # 2026-04-16: doutub.com changed owners and became malicious; disable Doutub and use doutupk.com (Doutula)
        # (DoutubFetcher, "斗图吧"),  # Doutub source
        (DoutupkFetcher, "斗图啦"),
        (FabiaoqingFetcher, "发表情"),
    ]
    
    async def try_fetch_concurrent(fetcher_list):
        """Try a group of sources concurrently, managing task lifecycles properly to avoid leaks"""
        tasks = []
        for f_class, name in fetcher_list:
            async def wrap(fc=f_class, nm=name):
                try:
                    async with fc() as fetcher:
                        res = await fetcher.search(actual_keyword, limit=limit)
                        return res, nm
                except Exception as e:
                    logger.warning(f"{nm}并发探测失败: {e}")
                    return None, nm
            # Explicit create_task is required to manage task lifetime
            tasks.append(asyncio.create_task(wrap()))
        
        try:
            # Return immediately on the first success through as_completed
            for coro in asyncio.as_completed(tasks):
                res, nm = await coro
                if res:
                    return res, nm
        finally:
            # On finding results or exiting with errors, explicitly cancel every other running task
            for task in tasks:
                if not task.done():
                    task.cancel()
            # Give cancelled tasks an opportunity to finalize
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                
        return None, ""

    if china_region:
        logger.info("检测到中文区域，并发开启国内源探测")
        results, source_name = await try_fetch_concurrent(CN_FETCHERS)
        if not results:
            logger.info("国内源全挂，尝试 Imgflip 兜底")
            results, source_name = await try_fetch_concurrent([(MemeFetcher, "Imgflip")])
    else:
        logger.info("检测到非中文区域，首选 Imgflip")
        results, source_name = await try_fetch_concurrent([(MemeFetcher, "Imgflip")])
        if not results:
            logger.info("Imgflip 获取失败，并发尝试国内源兜底")
            results, source_name = await try_fetch_concurrent(CN_FETCHERS)
    
    if not results:
        logger.error(f"所有表情包源均获取失败，关键词: {actual_keyword}")
        return {
            "success": False, 
            "data": [], 
            "formatted_content": "",
            "raw_data": {"data": []},
            "keyword_used": actual_keyword,
            "source": source_name,
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
# Test module
# ==========================================

async def main():
    """Unit-test functionality"""
    test_keywords_cn = ["搞笑", "猫", "熊猫头"]
    
    # 2026-04-16: doutub.com changed owners and became malicious; disable its tests
    # print("=== Doutub Meme Fetcher Test ===")
    # async with DoutubFetcher() as fetcher:
    #     for kw in test_keywords_cn[:2]:
    #         print(f"\nSearching Doutub for '{kw}'...")
    #         results = await fetcher.search(kw, limit=2)
    #         print(f"Total results: {len(results)}")
    #         for r in results:
    #             print(f"  - {r['title']}: {r['url']}")

    print("=== 斗图啦 Meme Fetcher Test ===")
    async with DoutupkFetcher() as fetcher:
        for kw in test_keywords_cn[:2]:
            print(f"\nSearching 斗图啦 for '{kw}'...")
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
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception as e:
            print(f"Failed to set event loop policy: {e}")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Async main failed: {e}")
