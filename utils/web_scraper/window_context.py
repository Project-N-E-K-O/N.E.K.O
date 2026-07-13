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
"""Active-window inspection, search, and context formatting."""

from __future__ import annotations

import asyncio
import httpx
from utils.external_http_client import get_external_http_client
import random
import re
import unicodedata
import platform
from typing import TYPE_CHECKING, Dict, List, Any, Optional, Union
from urllib.parse import quote
from utils.token_tracker import set_call_type
from utils.llm_client import SystemMessage, HumanMessage, create_chat_llm_async

# bs4 惰性 import（各解析函数内首用加载，utils.module_warmup 后台预热兜底）：本模块被
# system_router 顶层引用、坐在 main_server 启动 import 链上，顶层 bs4 会拖慢端口就绪。
if TYPE_CHECKING:
    pass

from ._shared import _extract_llm_text_content, get_random_user_agent, is_china_region, logger

def get_active_window_title(include_raw: bool = False) -> Optional[Union[str, Dict[str, str]]]:
    """
    Get the title of the currently active window (Windows only)
    
    Args:
        include_raw: whether to return the raw title. Default False, returning only the truncated safe title.
                     When True, returns a dict containing sanitized and raw.
    
    Returns:
        Default: the truncated safe title string (first 30 chars), or None on failure
        With include_raw=True: {'sanitized': 'truncated title', 'raw': 'full title'}, or None on failure
    """
    if platform.system() != 'Windows':
        logger.warning("获取活跃窗口标题仅支持Windows系统")
        return None
    
    try:
        import pygetwindow as gw
    except ImportError:
        logger.error("pygetwindow模块未安装。在Windows系统上请安装: pip install pygetwindow")
        return None
    
    try:
        active_window = gw.getActiveWindow()
        if active_window:
            raw_title = active_window.title
            # 截断标题以避免记录敏感信息
            if len(raw_title) > 30:
                sanitized_title = raw_title[:30] + '...'
            else:
                sanitized_title = raw_title
            # 窗口标题是用户面对的内容，不写 logger
            logger.info(f"获取到活跃窗口标题 (len={len(raw_title)})")
            print(f"获取到活跃窗口标题: {sanitized_title}")
            
            if include_raw:
                return {
                    'sanitized': sanitized_title,
                    'raw': raw_title
                }
            else:
                return sanitized_title
        else:
            logger.warning("没有找到活跃窗口")
            return None
    except Exception as e:
        logger.exception(f"获取活跃窗口标题失败: {e}")
        return None

async def generate_diverse_queries(window_title: str) -> List[str]:
    """
    Use the LLM to generate 3 diversified search keywords based on the window title
    
    Automatically uses the appropriate language per user region:
    - Chinese region: Chinese prompts, for Baidu search
    - non-Chinese region: English prompts, for Google search
    
    Args:
        window_title: window title (should be a cleaned title without sensitive information)
    
    Returns:
        List of 3 search keywords
    
    Note:
        For privacy, clean the title with clean_window_title() before calling,
        to avoid sending file paths, accounts and other sensitive info to the LLM API
    """
    try:
        # 导入配置管理器
        from utils.config_manager import ConfigManager
        config_manager = ConfigManager()
        
        # 使用summary模型配置
        summary_config = config_manager.get_model_api_config('summary')
        
        from config import LLM_OUTPUT_GUARD_MAX_TOKENS
        llm = await create_chat_llm_async(
            summary_config['model'], summary_config['base_url'],
            summary_config['api_key'],
            timeout=10.0, max_retries=0,
            max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; short keyword output but covers a thinking model's reasoning too
            provider_type=summary_config.get('provider_type'),
        )
        
        # 清理/脱敏窗口标题用于日志显示
        if len(window_title) > 30:
            sanitized_title = window_title[:30] + '...'
        else:
            sanitized_title = window_title
        
        # 检测区域并使用适当的提示词
        # china_region → 'zh'（百度），否则按用户语言选择（Google）
        from config.prompts.prompts_sys import _loc, SEARCH_KEYWORD_SYSTEM, SEARCH_KEYWORD_USER
        from utils.language_utils import get_global_language
        china_region = is_china_region()
        keyword_lang = 'zh' if china_region else get_global_language()
        system_prompt = _loc(SEARCH_KEYWORD_SYSTEM, keyword_lang)
        user_prompt = _loc(SEARCH_KEYWORD_USER, keyword_lang).format(window_title=window_title)

        # Gemini 的 OpenAI 兼容接口需要实际的 user content；
        # 仅发送 system message 可能被底层适配为空 contents。
        set_call_type("web_scraper")
        async with llm:  # ensure the per-call client is closed (no connection leak on repeated calls)
            response = await llm.ainvoke([  # noqa: LLM_INPUT_BUDGET  # input is the OS window title (uncapped by design, cf. llm-prompt-budget.md §6).
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
        response_text = _extract_llm_text_content(getattr(response, 'content', None))
        if not response_text:
            # 窗口标题不写 logger，但记下长度元数据便于调试
            logger.warning(f"为窗口标题生成搜索关键词时收到空包 (title_len={len(window_title)})")
            print(f"为窗口标题「{sanitized_title}」生成搜索关键词时收到空包")
            clean_title = clean_window_title(window_title)
            return [clean_title, clean_title, clean_title] if clean_title else []

        # 解析响应，提取3个关键词
        queries = []
        lines = response_text.split('\n')
        for line in lines:
            line = line.strip()
            # 移除可能的序号、标点等
            line = re.sub(r'^[\d\.\-\*\)\]】]+\s*', '', line)
            line = line.strip('.,;:，。；：')
            if line and len(line) >= 2:
                queries.append(line)
                if len(queries) >= 3:
                    break
        
        # 如果生成的查询不足3个，用原始标题填充
        if len(queries) < 3:
            clean_title = clean_window_title(window_title)
            while len(queries) < 3 and clean_title:
                queries.append(clean_title)
        
        # 窗口标题 + AI 生成的查询关键词都不写 logger
        logger.info(f"窗口标题→查询关键词生成完成 (queries_count={len(queries[:3])})")
        print(f"为窗口标题「{sanitized_title}」生成的查询关键词: {queries}")
        return queries[:3]
        
    except Exception as e:
        # 异常日志中也使用脱敏标题
        if len(window_title) > 30:
            sanitized_title = window_title[:30] + '...'
        else:
            sanitized_title = window_title
        logger.warning(f"窗口标题→多样化查询生成失败，回退默认清理方法: {e}")
        print(f"为窗口标题「{sanitized_title}」生成多样化查询失败: {e}")
        # 回退到原始清理方法
        clean_title = clean_window_title(window_title)
        return [clean_title, clean_title, clean_title]

def clean_window_title(title: str) -> str:
    """
    Clean a window title, extracting meaningful search keywords
    
    Args:
        title: raw window title
    
    Returns:
        Cleaned search keywords
    """
    if not title:
        return ""
    
    # 移除常见的应用程序后缀和无意义内容
    patterns_to_remove = [
        r'\s*[-–—]\s*(Google Chrome|Mozilla Firefox|Microsoft Edge|Opera|Safari|Brave).*$',
        r'\s*[-–—]\s*(Visual Studio Code|VS Code|VSCode).*$',
        r'\s*[-–—]\s*(记事本|Notepad\+*|Sublime Text|Atom).*$',
        r'\s*[-–—]\s*(Microsoft Word|Excel|PowerPoint).*$',
        r'\s*[-–—]\s*(QQ音乐|网易云音乐|酷狗音乐|Spotify).*$',
        r'\s*[-–—]\s*(哔哩哔哩|bilibili|YouTube|优酷|爱奇艺|腾讯视频).*$',
        r'\s*[-–—]\s*\d+\s*$',  # 移除末尾的数字（如页码）
        r'^\*\s*',  # 移除开头的星号（未保存标记）
        r'\s*\[.*?\]\s*$',  # 移除方括号内容
        r'\s*\(.*?\)\s*$',  # 移除圆括号内容
        r'https?://\S+',  # 移除URL
        r'www\.\S+',  # 移除www开头的网址
        r'\.py\s*$',  # 移除.py后缀
        r'\.js\s*$',  # 移除.js后缀
        r'\.html?\s*$',  # 移除.html后缀
        r'\.css\s*$',  # 移除.css后缀
        r'\.md\s*$',  # 移除.md后缀
        r'\.txt\s*$',  # 移除.txt后缀
        r'\.json\s*$',  # 移除.json后缀
    ]
    
    cleaned = title
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    # 移除多余空格
    cleaned = ' '.join(cleaned.split())
    
    # 如果清理后太短或为空，返回原标题的一部分
    if len(cleaned) < 3:
        # 尝试提取原标题中的第一个有意义的部分
        parts = re.split(r'\s*[-–—|]\s*', title)
        if parts and len(parts[0]) >= 3:
            cleaned = parts[0].strip()
    
    return cleaned[:100]  # 限制长度

async def search_google(query: str, limit: int = 10) -> Dict[str, Any]:
    """
    Search keywords on Google and fetch results (for non-Chinese regions)
    
    Args:
        query: search keywords
        limit: max number of results
    
    Returns:
        Dict with search results
    """
    try:
        if not query or len(query.strip()) < 2:
            return {
                'success': False,
                'error': '搜索关键词太短'
            }
        
        # 清理查询词
        query = query.strip()
        encoded_query = quote(query)
        
        # Google搜索URL
        url = f"https://www.google.com/search?q={encoded_query}&hl=en"
        
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
            'DNT': '1',
            'Cache-Control': 'no-cache',
        }
        
        # 添加随机延迟
        await asyncio.sleep(random.uniform(0.2, 0.5))
        
        client = get_external_http_client()
        response = await client.get(url, headers=headers, timeout=5.0)
        response.raise_for_status()
        html_content = response.text

        # 解析搜索结果（BS4 大 HTML 同步解析放线程池，避免阻塞 event loop）
        results = await asyncio.to_thread(parse_google_results, html_content, limit)

        if results:
            return {
                'success': True,
                'query': query,
                'results': results
            }
        else:
            return {
                'success': False,
                'error': '未能解析到搜索结果',
                'query': query
            }

    except httpx.TimeoutException:
        logger.exception("Google搜索超时")
        return {
            'success': False,
            'error': '搜索超时'
        }
    except Exception as e:
        logger.exception(f"Google搜索失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }

def parse_google_results(html_content: str, limit: int = 5) -> List[Dict[str, str]]:
    """
    Parse a Google search results page
    
    Args:
        html_content: HTML page content
        limit: result count limit
    
    Returns:
        Search result list; each result contains title, abstract, url
    """
    results = []
    
    try:
        from urllib.parse import urljoin, urlparse, parse_qs
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_content, 'lxml')

        # 查找搜索结果容器
        # Google使用各种类名，尝试多个选择器
        result_divs = soup.find_all('div', class_='g')
        
        for div in result_divs[:limit * 2]:
            # 提取标题和链接
            link = div.find('a')
            if link:
                # 获取h3标签作为标题
                h3 = div.find('h3')
                if h3:
                    title = h3.get_text(strip=True)
                else:
                    title = link.get_text(strip=True)
                
                if title and 3 < len(title) < 200:
                    # 提取URL
                    href = link.get('href', '')
                    if href:
                        # Google有时会包装URL
                        if href.startswith('/url?'):
                            parsed = urlparse(href)
                            qs = parse_qs(parsed.query)
                            url = qs.get('q', [href])[0]
                        elif href.startswith('http'):
                            url = href
                        else:
                            url = urljoin('https://www.google.com', href)
                    else:
                        url = ''
                    
                    # 提取摘要/片段
                    abstract = ""
                    # 查找片段文本
                    snippet_div = div.find('div', class_=lambda x: x and ('VwiC3b' in x if x else False))
                    if snippet_div:
                        abstract = snippet_div.get_text(strip=True)[:200]
                    else:
                        # 尝试其他常见的片段选择器
                        spans = div.find_all('span')
                        for span in spans:
                            text = span.get_text(strip=True)
                            if len(text) > 50:
                                abstract = text[:200]
                                break
                    
                    # 跳过广告和不需要的结果
                    if not any(skip in title.lower() for skip in ['ad', 'sponsored', 'javascript']):
                        results.append({
                            'title': title,
                            'abstract': abstract,
                            'url': url
                        })
                        if len(results) >= limit:
                            break
        
        logger.info(f"解析到 {len(results)} 条Google搜索结果")
        return results[:limit]
        
    except Exception as e:
        logger.exception(f"解析Google搜索结果失败: {e}")
        return []

async def search_duckduckgo(query: str, limit: int = 10) -> Dict[str, Any]:
    """
    Search keywords on DuckDuckGo and fetch results (for non-Chinese regions).

    Replaces Google: Google's anti-bot measures are nearly guaranteed to trip for
    headless/scripted requests (302 → /sorry/index → 429), so the proactive-chat
    window-context search got basically no results. DuckDuckGo's HTML endpoint
    (html.duckduckgo.com) is far more tolerant of scripted access, and results are
    embedded directly in the HTML, easy to parse.

    Args:
        query: search keywords
        limit: max number of results

    Returns:
        Dict with search results
    """
    try:
        if not query or len(query.strip()) < 2:
            return {
                'success': False,
                'error': '搜索关键词太短'
            }

        # 清理查询词
        query = query.strip()
        encoded_query = quote(query)

        # DuckDuckGo 无 JS 的 HTML 端点（kl=us-en 对齐非中文区域口径）
        url = f"https://html.duckduckgo.com/html/?q={encoded_query}&kl=us-en"

        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://duckduckgo.com/',
            'Connection': 'keep-alive',
            'DNT': '1',
            'Cache-Control': 'no-cache',
        }

        # 添加随机延迟
        await asyncio.sleep(random.uniform(0.2, 0.5))

        client = get_external_http_client()
        response = await client.get(url, headers=headers, timeout=5.0)
        response.raise_for_status()
        html_content = response.text

        # 解析搜索结果（BS4 大 HTML 同步解析放线程池，避免阻塞 event loop）
        results = await asyncio.to_thread(parse_duckduckgo_results, html_content, limit)

        if results:
            return {
                'success': True,
                'query': query,
                'results': results
            }
        else:
            return {
                'success': False,
                'error': '未能解析到搜索结果',
                'query': query
            }

    except httpx.TimeoutException:
        logger.exception("DuckDuckGo搜索超时")
        return {
            'success': False,
            'error': '搜索超时'
        }
    except Exception as e:
        logger.exception(f"DuckDuckGo搜索失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }

_SEARCH_TEXT_WS_RE = re.compile(r"\s+")

def _sanitize_search_text(text: str) -> str:
    """Sanitize untrusted search-result text: drop control/format/private-use/
    surrogate characters and U+FFFD, and normalize whitespace.

    Baidu pages mix iconfont private-use glyphs (e.g. U+E687) into text nodes,
    mis-decoded pages yield U+FFFD, and zero-width/bidi controls can be abused
    for injection obfuscation — none of these should reach the LLM/TTS.
    """
    if not text:
        return ""
    kept = []
    for ch in text:
        if ch.isspace():
            kept.append(" ")
            continue
        if ord(ch) == 0xFFFD:
            continue
        if unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs"):
            continue
        kept.append(ch)
    return _SEARCH_TEXT_WS_RE.sub(" ", "".join(kept)).strip()

def parse_duckduckgo_results(html_content: str, limit: int = 5) -> List[Dict[str, str]]:
    """
    Parse a DuckDuckGo HTML-endpoint search results page

    Args:
        html_content: HTML page content
        limit: result count limit

    Returns:
        Search result list; each result contains title, abstract, url
    """
    results = []

    try:
        from urllib.parse import urlparse, parse_qs
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_content, 'lxml')

        # html.duckduckgo.com 每条结果是 div.result（正常结果还带 web-result）。
        # 不预截断：广告/无效 URL 的条目可能排在前面，靠下方攒够 limit 提前退出
        result_divs = soup.find_all('div', class_='result')

        for div in result_divs:
            # 跳过广告（class 含 result--ad / results_links_deep 之外的 ad 变体）
            cls = div.get('class') or []
            if any('ad' in c for c in cls):
                continue

            link = div.find('a', class_='result__a')
            if not link:
                continue

            title = _sanitize_search_text(link.get_text(strip=True))
            if not title or not (3 < len(title) < 200):
                continue

            # DDG 用跳转链接包裹真实地址：//duckduckgo.com/l/?uddg=<urlencoded>&rut=...
            href = link.get('href', '')
            url = ''
            if href:
                if 'uddg=' in href:
                    # //duckduckgo.com/l/?uddg=<urlencoded>&rut=... ——
                    # parse_qs 已对 uddg 值做一次百分号解码，得到的就是真实地址，
                    # 不要再 unquote（否则真实 URL 里的字面 % 会被二次解码损坏）。
                    parsed = urlparse(href if href.startswith('http') else 'https:' + href)
                    qs = parse_qs(parsed.query)
                    url = qs.get('uddg', [''])[0]
                elif href.startswith('http'):
                    url = href
            # 只接受带真实主机名的 http(s) 绝对地址（uddg 里可能包着
            # javascript: 或 "https://" 这种残缺目标），无效的整条跳过
            try:
                parsed_target = urlparse(url)
                url_ok = parsed_target.scheme in ('http', 'https') and bool(parsed_target.hostname)
            except ValueError:
                url_ok = False
            if not url_ok:
                continue

            # 摘要片段
            abstract = ''
            snippet = div.find(class_='result__snippet')
            if snippet:
                abstract = _sanitize_search_text(snippet.get_text(strip=True))[:200]

            results.append({
                'title': title,
                'abstract': abstract,
                'url': url
            })
            if len(results) >= limit:
                break

        logger.info(f"解析到 {len(results)} 条DuckDuckGo搜索结果")
        return results[:limit]

    except Exception as e:
        logger.exception(f"解析DuckDuckGo搜索结果失败: {e}")
        return []

async def search_baidu(query: str, limit: int = 5) -> Dict[str, Any]:
    """
    Search keywords on Baidu and fetch results
    
    Args:
        query: search keywords
        limit: max number of results
    
    Returns:
        Dict with search results
    """
    try:
        if not query or len(query.strip()) < 2:
            return {
                'success': False,
                'error': '搜索关键词太短'
            }
        
        # 清理查询词
        query = query.strip()
        encoded_query = quote(query)
        
        # 百度搜索URL
        url = f"https://www.baidu.com/s?wd={encoded_query}"
        
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
            'Referer': 'https://www.baidu.com/',
            'DNT': '1',
            'Cache-Control': 'no-cache',
        }
        
        # 添加随机延迟
        await asyncio.sleep(random.uniform(0.2, 0.5))
        
        client = get_external_http_client()
        response = await client.get(url, headers=headers, timeout=5.0)
        response.raise_for_status()
        html_content = response.text

        # 解析搜索结果
        results = await asyncio.to_thread(parse_baidu_results, html_content, limit)

        if results:
            return {
                'success': True,
                'query': query,
                'results': results
            }
        else:
            return {
                'success': False,
                'error': '未能解析到搜索结果',
                'query': query
            }

    except httpx.TimeoutException:
        logger.exception("百度搜索超时")
        return {
            'success': False,
            'error': '搜索超时'
        }
    except Exception as e:
        logger.exception(f"百度搜索失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }

def parse_baidu_results(html_content: str, limit: int = 5) -> List[Dict[str, str]]:
    """
    Parse a Baidu search results page
    
    Args:
        html_content: HTML page content
        limit: result count limit
    
    Returns:
        Search result list; each result contains title, abstract, url
    """
    results = []
    seen_urls = set()

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_content, 'lxml')

        # 提取搜索结果容器。不预截断列表：相关搜索/卡片等会被拒绝的容器可能
        # 排在有效结果前面，靠下方 len(results) >= limit 提前退出即可
        containers = soup.find_all('div', class_=lambda x: x and 'c-container' in x)
        
        for container in containers:
            # 标题只认 h3 下的链接：容器里第一个 <a> 可能是卡片子链接
            # （如天气卡的"查看40天预报"）或相关搜索词，不是结果标题
            link = container.select_one('h3 a[href]')
            if link:
                title = _sanitize_search_text(link.get_text(strip=True))
                if title and 5 < len(title) < 200:
                    # 只接受 http(s) 绝对地址：相关搜索等站内相对链接（/s?wd=...）
                    # 和 javascript: 伪协议都不是搜索结果，不能 urljoin 洗白
                    href = link.get('href', '')
                    if not href.startswith(('http://', 'https://')):
                        continue
                    url = href
                    if url in seen_urls:
                        continue

                    # 提取摘要
                    abstract = ""
                    content_span = container.find('span', class_=lambda x: x and 'content-right' in x)
                    if content_span:
                        abstract = _sanitize_search_text(content_span.get_text(strip=True))[:200]

                    if not any(skip in title.lower() for skip in ['百度', '广告', 'javascript']):
                        seen_urls.add(url)
                        results.append({
                            'title': title,
                            'abstract': abstract,
                            'url': url
                        })
                        if len(results) >= limit:
                            break
        
        # 如果没找到结果，尝试提取 h3 标题。同样不预截断：被拒绝的
        # 相关搜索类 h3 可能排在前面，攒够 limit 条再退出
        if not results:
            for h3 in soup.find_all('h3'):
                link = h3.find('a')
                if link:
                    title = _sanitize_search_text(link.get_text(strip=True))
                    if title and 5 < len(title) < 200:
                        # 与主循环同口径：http(s) 校验、广告关键词过滤、URL 去重
                        href = link.get('href', '')
                        if not href.startswith(('http://', 'https://')):
                            continue
                        url = href
                        if url in seen_urls:
                            continue
                        if any(skip in title.lower() for skip in ['百度', '广告', 'javascript']):
                            continue

                        seen_urls.add(url)
                        results.append({
                            'title': title,
                            'abstract': '',
                            'url': url
                        })
                        if len(results) >= limit:
                            break
        
        logger.info(f"解析到 {len(results)} 条百度搜索结果")
        return results[:limit]
        
    except Exception as e:
        logger.exception(f"解析百度搜索结果失败: {e}")
        return []

def format_baidu_search_results(search_result: Dict[str, Any]) -> str:
    """
    Format Baidu search results into a readable string
    
    Args:
        search_result: result returned by search_baidu
    
    Returns:
        The formatted string
    """
    if not search_result.get('success'):
        return f"搜索失败: {search_result.get('error', '未知错误')}"
    
    output_lines = []
    query = search_result.get('query', '')
    results = search_result.get('results', [])
    
    output_lines.append(f"【关于「{query}」的搜索结果】")
    output_lines.append("")
    
    for i, result in enumerate(results, 1):
        title = result.get('title', '')
        abstract = result.get('abstract', '')
        
        output_lines.append(f"{i}. {title}")
        if abstract:
            # 限制摘要长度
            if len(abstract) > 150:
                abstract = abstract[:150] + '...'
            output_lines.append(f"   {abstract}")
        output_lines.append("")
    
    if not results:
        output_lines.append("未找到相关结果")
    
    return "\n".join(output_lines)

def format_search_results(search_result: Dict[str, Any]) -> str:
    """
    Format search results into a readable string
    Uses the appropriate language automatically per region
    
    Args:
        search_result: result returned by search_baidu or search_google
    
    Returns:
        The formatted string
    """
    china_region = is_china_region()
    
    if not search_result.get('success'):
        if china_region:
            return f"搜索失败: {search_result.get('error', '未知错误')}"
        else:
            return f"Search failed: {search_result.get('error', 'Unknown error')}"
    
    output_lines = []
    query = search_result.get('query', '')
    results = search_result.get('results', [])
    
    if china_region:
        output_lines.append(f"【关于「{query}」的搜索结果】")
    else:
        output_lines.append(f"【Search results for「{query}」】")
    output_lines.append("")
    
    for i, result in enumerate(results, 1):
        title = result.get('title', '')
        abstract = result.get('abstract', '')
        
        output_lines.append(f"{i}. {title}")
        if abstract:
            if len(abstract) > 150:
                abstract = abstract[:150] + '...'
            output_lines.append(f"   {abstract}")
        output_lines.append("")
    
    if not results:
        if china_region:
            output_lines.append("未找到相关结果")
        else:
            output_lines.append("No results found")
    
    return "\n".join(output_lines)

async def fetch_window_context_content(limit: int = 5) -> Dict[str, Any]:
    """
    Get the active window title and run a search on it
    
    Region detection decides the search engine:
    - Chinese region: Baidu
    - non-Chinese region: DuckDuckGo (replacing Google to dodge its anti-bot 429)
    
    Args:
        limit: max number of search results
    
    Returns:
        Dict with the window title and search results
        Note: window_title is the sanitized version to protect privacy
    """
    try:
        # 检测区域
        china_region = is_china_region()
        
        # 获取活跃窗口标题（同时获取原始和脱敏版本）
        title_result = get_active_window_title(include_raw=True)
        
        if not title_result:
            if china_region:
                return {
                    'success': False,
                    'error': '无法获取当前活跃窗口标题'
                }
            else:
                return {
                    'success': False,
                    'error': '无法获取当前活跃窗口标题'
                }
        
        sanitized_title = title_result['sanitized']
        raw_title = title_result['raw']
        
        # 清理窗口标题以移除敏感信息，避免发送给LLM
        cleaned_title = clean_window_title(raw_title)
        
        # 使用清理后的标题生成多样化搜索查询（保护隐私）
        search_queries = await generate_diverse_queries(cleaned_title)
        
        if not search_queries or all(not q or len(q) < 2 for q in search_queries):
            if china_region:
                return {
                    'success': False,
                    'error': '窗口标题无法提取有效的搜索关键词',
                    'window_title': sanitized_title
                }
            else:
                return {
                    'success': False,
                    'error': '窗口标题无法提取有效的搜索关键词',
                    'window_title': sanitized_title
                }
        
        # 窗口标题 + 查询都不写 logger
        logger.info(f"从窗口标题生成多样化查询完成 (queries_count={len(search_queries or [])})")
        print(f"从窗口标题「{sanitized_title}」生成多样化查询: {search_queries}")
        
        # 执行搜索并合并结果
        all_results = []
        successful_queries = []
        
        # 根据区域选择搜索函数
        if china_region:
            search_func = search_baidu
        else:
            # 非中文区域改用 DuckDuckGo：Google 对脚本请求几乎必触发 429/sorry 反爬
            search_func = search_duckduckgo
        
        for query in search_queries:
            if not query or len(query) < 2:
                continue
            
            # query 是从用户窗口标题派生的搜索词，不写 logger
            logger.info(f"使用查询关键词 (len={len(query)})")
            print(f"使用查询关键词: {query}")
            
            search_result = await search_func(query, limit)
            
            if search_result.get('success') and search_result.get('results'):
                all_results.extend(search_result['results'])
                successful_queries.append(query)
        
        # 去重结果（优先使用URL，如果URL缺失则使用title）
        seen_keys = set()
        unique_results = []
        for result in all_results:
            url = result.get('url', '')
            title = result.get('title', '')
            
            # 优先使用URL进行去重，回退到title
            if url:
                dedup_key = url
            else:
                dedup_key = title
            
            if dedup_key and dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                unique_results.append(result)
        
        # 限制总结果数量
        unique_results = unique_results[:limit * 2]
        
        if not unique_results:
            if china_region:
                return {
                    'success': False,
                    'error': '所有查询均未获得搜索结果',
                    'window_title': sanitized_title,
                    'search_queries': search_queries
                }
            else:
                return {
                    'success': False,
                    'error': '所有查询均未获得搜索结果',
                    'window_title': sanitized_title,
                    'search_queries': search_queries
                }
        
        return {
            'success': True,
            'window_title': sanitized_title,
            'region': 'china' if china_region else 'non-china',
            'search_queries': successful_queries,
            'search_results': unique_results,
        }
        
    except Exception as e:
        if is_china_region():
            logger.exception(f"获取窗口上下文内容失败: {e}")
        else:
            logger.exception(f"获取窗口上下文内容失败: {e}")
        return {
            'success': False,
            'error': str(e)
        }

def format_window_context_content(content: Dict[str, Any]) -> str:
    """
    Format window-context content into a readable string
    
    Uses the appropriate language automatically per region
    
    Args:
        content: result returned by fetch_window_context_content
    
    Returns:
        The formatted string
    """
    china_region = is_china_region()
    
    if not content.get('success'):
        if china_region:
            return f"获取窗口上下文失败: {content.get('error', '未知错误')}"
        else:
            return f"Failed to fetch window context: {content.get('error', 'Unknown error')}"
    
    output_lines = []
    window_title = content.get('window_title', '')
    search_queries = content.get('search_queries', [])
    results = content.get('search_results', [])
    
    if china_region:
        output_lines.append(f"【当前活跃窗口】{window_title}")
        
        if search_queries:
            if len(search_queries) == 1:
                output_lines.append(f"【搜索关键词】{search_queries[0]}")
            else:
                output_lines.append(f"【搜索关键词】{', '.join(search_queries)}")
        
        output_lines.append("")
        output_lines.append("【相关信息】")
    else:
        output_lines.append(f"【Active Window】{window_title}")
        
        if search_queries:
            if len(search_queries) == 1:
                output_lines.append(f"【Search Keywords】{search_queries[0]}")
            else:
                output_lines.append(f"【Search Keywords】{', '.join(search_queries)}")
        
        output_lines.append("")
        output_lines.append("【Related Information】")
    
    for i, result in enumerate(results, 1):
        title = result.get('title', '')
        abstract = result.get('abstract', '')
        url = result.get('url', '')
        
        output_lines.append(f"{i}. {title}")
        if abstract:
            if len(abstract) > 150:
                abstract = abstract[:150] + '...'
            output_lines.append(f"   {abstract}")
        if url:
            if china_region:
                output_lines.append(f"   链接: {url}")
            else:
                output_lines.append(f"   Link: {url}")
    
    if not results:
        if china_region:
            output_lines.append("未找到相关信息")
        else:
            output_lines.append("No related information found")
    
    return "\n".join(output_lines)
