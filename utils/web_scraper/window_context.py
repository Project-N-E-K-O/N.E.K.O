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

# Import bs4 lazily on first use inside parsers, with utils.module_warmup background warmup; system_router imports this module
# at top level during main_server startup, so eager bs4 delays port readiness.
if TYPE_CHECKING:
    pass

from ._shared import _extract_llm_text_content, get_random_user_agent, is_china_region, logger
from .search_gateway import search_via_plugin

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
            # Truncate titles to avoid logging sensitive information.
            if len(raw_title) > 30:
                sanitized_title = raw_title[:30] + '...'
            else:
                sanitized_title = raw_title
            # Window titles are user-facing content; never write them through logger.
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
        # Import the configuration manager.
        from utils.config_manager import ConfigManager
        config_manager = ConfigManager()
        
        # Use summary-model configuration.
        summary_config = await config_manager.aget_model_api_config('summary')
        
        from config import LLM_OUTPUT_GUARD_MAX_TOKENS
        llm = await create_chat_llm_async(
            summary_config['model'], summary_config['base_url'],
            summary_config['api_key'],
            timeout=10.0, max_retries=0,
            max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; short keyword output but covers a thinking model's reasoning too
            provider_type=summary_config.get('provider_type'),
        )
        
        # Clean/redact window titles for log display.
        if len(window_title) > 30:
            sanitized_title = window_title[:30] + '...'
        else:
            sanitized_title = window_title
        
        # Detect region and select the appropriate prompt.
        # china_region -> 'zh' (Baidu); otherwise select by user language (Google).
        from config.prompts.prompts_sys import _loc, SEARCH_KEYWORD_SYSTEM, SEARCH_KEYWORD_USER
        from utils.language_utils import get_global_language
        china_region = is_china_region()
        keyword_lang = 'zh' if china_region else get_global_language()
        system_prompt = _loc(SEARCH_KEYWORD_SYSTEM, keyword_lang)
        user_prompt = _loc(SEARCH_KEYWORD_USER, keyword_lang).format(window_title=window_title)

        # Gemini's OpenAI-compatible API requires actual user content;
        # sending only system messages may become empty contents in underlying adapters.
        set_call_type("web_scraper")
        async with llm:  # ensure the per-call client is closed (no connection leak on repeated calls)
            response = await llm.ainvoke([  # noqa: LLM_INPUT_BUDGET  # input is the OS window title (uncapped by design, cf. llm-prompt-budget.md §6).
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
        response_text = _extract_llm_text_content(getattr(response, 'content', None))
        if not response_text:
            # Never log window titles through logger; retain length metadata for debugging.
            logger.warning(f"为窗口标题生成搜索关键词时收到空包 (title_len={len(window_title)})")
            print(f"为窗口标题「{sanitized_title}」生成搜索关键词时收到空包")
            clean_title = clean_window_title(window_title)
            return [clean_title, clean_title, clean_title] if clean_title else []

        # Parse the response and extract three keywords.
        queries = []
        lines = response_text.split('\n')
        for line in lines:
            line = line.strip()
            # Remove possible numbering, punctuation, etc.
            line = re.sub(r'^[\d\.\-\*\)\]】]+\s*', '', line)
            line = line.strip('.,;:，。；：')
            if line and len(line) >= 2:
                queries.append(line)
                if len(queries) >= 3:
                    break
        
        # Fill missing queries with the original title if fewer than three were generated.
        if len(queries) < 3:
            clean_title = clean_window_title(window_title)
            while len(queries) < 3 and clean_title:
                queries.append(clean_title)
        
        # Never write window titles or generated query keywords through logger.
        logger.info(f"窗口标题→查询关键词生成完成 (queries_count={len(queries[:3])})")
        print(f"为窗口标题「{sanitized_title}」生成的查询关键词: {queries}")
        return queries[:3]
        
    except Exception as e:
        # Use redacted titles in exception logs too.
        if len(window_title) > 30:
            sanitized_title = window_title[:30] + '...'
        else:
            sanitized_title = window_title
        logger.warning(f"窗口标题→多样化查询生成失败，回退默认清理方法: {e}")
        print(f"为窗口标题「{sanitized_title}」生成多样化查询失败: {e}")
        # Fall back to the original cleaning method.
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
    
    # Remove common application suffixes and meaningless content.
    patterns_to_remove = [
        r'\s*[-–—]\s*(Google Chrome|Mozilla Firefox|Microsoft Edge|Opera|Safari|Brave).*$',
        r'\s*[-–—]\s*(Visual Studio Code|VS Code|VSCode).*$',
        r'\s*[-–—]\s*(记事本|Notepad\+*|Sublime Text|Atom).*$',
        r'\s*[-–—]\s*(Microsoft Word|Excel|PowerPoint).*$',
        r'\s*[-–—]\s*(QQ音乐|网易云音乐|酷狗音乐|Spotify).*$',
        r'\s*[-–—]\s*(哔哩哔哩|bilibili|YouTube|优酷|爱奇艺|腾讯视频).*$',
        r'\s*[-–—]\s*\d+\s*$',  # Remove trailing numbers such as page numbers.
        r'^\*\s*',  # Remove leading asterisks marking unsaved changes.
        r'\s*\[.*?\]\s*$',  # Remove square-bracketed content.
        r'\s*\(.*?\)\s*$',  # Remove parenthesized content.
        r'https?://\S+',  # Remove URLs.
        r'www\.\S+',  # Remove www-prefixed addresses.
        r'\.py\s*$',  # Remove .py suffixes.
        r'\.js\s*$',  # Remove .js suffixes.
        r'\.html?\s*$',  # Remove .html suffixes.
        r'\.css\s*$',  # Remove .css suffixes.
        r'\.md\s*$',  # Remove .md suffixes.
        r'\.txt\s*$',  # Remove .txt suffixes.
        r'\.json\s*$',  # Remove .json suffixes.
    ]
    
    cleaned = title
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    # Remove excess spaces.
    cleaned = ' '.join(cleaned.split())
    
    # If cleaning leaves an empty/short title, return part of the original.
    if len(cleaned) < 3:
        # Try extracting the original title's first meaningful section.
        parts = re.split(r'\s*[-–—|]\s*', title)
        if parts and len(parts[0]) >= 3:
            cleaned = parts[0].strip()
    
    return cleaned[:100]  # Limit length.

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
        
        # Clean search terms.
        query = query.strip()
        encoded_query = quote(query)
        
        # Google search URL.
        url = f"https://www.google.com/search?q={encoded_query}&hl=en"
        
        headers = {
            'User-Agent': get_random_user_agent(),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
            'DNT': '1',
            'Cache-Control': 'no-cache',
        }
        
        # Add a random delay.
        await asyncio.sleep(random.uniform(0.2, 0.5))
        
        client = get_external_http_client()
        response = await client.get(url, headers=headers, timeout=5.0)
        response.raise_for_status()
        html_content = response.text

        # Parse search results; offload synchronous BS4 parsing of large HTML to avoid blocking the event loop.
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

        # Find search-result containers.
        # Google uses varied class names; try multiple selectors.
        result_divs = soup.find_all('div', class_='g')
        
        for div in result_divs[:limit * 2]:
            # Extract titles and links.
            link = div.find('a')
            if link:
                # Use h3 as the title.
                h3 = div.find('h3')
                if h3:
                    title = h3.get_text(strip=True)
                else:
                    title = link.get_text(strip=True)
                
                if title and 3 < len(title) < 200:
                    # Extract URL.
                    href = link.get('href', '')
                    if href:
                        # Google sometimes wraps URLs.
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
                    
                    # Extract snippets.
                    abstract = ""
                    # Find snippet text.
                    snippet_div = div.find('div', class_=lambda x: x and ('VwiC3b' in x if x else False))
                    if snippet_div:
                        abstract = snippet_div.get_text(strip=True)[:200]
                    else:
                        # Try other common snippet selectors.
                        spans = div.find_all('span')
                        for span in spans:
                            text = span.get_text(strip=True)
                            if len(text) > 50:
                                abstract = text[:200]
                                break
                    
                    # Skip ads and unwanted results.
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
    Search DuckDuckGo through the controlled web-search plugin gateway.

    Args:
        query: search keywords
        limit: max number of results

    Returns:
        Dict with search results
    """
    return await search_via_plugin(query, limit, backend="duckduckgo")

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

        # html.duckduckgo.com represents results with div.result; normal ones also have web-result.
        # Do not pretruncate: ads/invalid URLs may come first; stop below once limit valid results accumulate.
        result_divs = soup.find_all('div', class_='result')

        for div in result_divs:
            # Skip ads identified by result--ad classes or ad variants outside results_links_deep.
            cls = div.get('class') or []
            if any('ad' in c for c in cls):
                continue

            link = div.find('a', class_='result__a')
            if not link:
                continue

            title = _sanitize_search_text(link.get_text(strip=True))
            if not title or not (3 < len(title) < 200):
                continue

            # DDG wraps actual URLs in redirects: //duckduckgo.com/l/?uddg=<urlencoded>&rut=...
            href = link.get('href', '')
            url = ''
            if href:
                if 'uddg=' in href:
                    # //duckduckgo.com/l/?uddg=<urlencoded>&rut=... ——
                    # parse_qs already percent-decodes uddg once, yielding the actual URL;
                    # do not unquote again, which would corrupt literal percent encodings in the real URL.
                    parsed = urlparse(href if href.startswith('http') else 'https:' + href)
                    qs = parse_qs(parsed.query)
                    url = qs.get('uddg', [''])[0]
                elif href.startswith('http'):
                    url = href
            # Accept only absolute HTTP(S) URLs with actual hostnames; uddg can wrap
            # javascript: or incomplete targets such as "https://". Skip invalid results entirely.
            try:
                parsed_target = urlparse(url)
                url_ok = parsed_target.scheme in ('http', 'https') and bool(parsed_target.hostname)
            except ValueError:
                url_ok = False
            if not url_ok:
                continue

            # Snippet.
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
    Search Baidu through the controlled web-search plugin gateway.
    
    Args:
        query: search keywords
        limit: max number of results
    
    Returns:
        Dict with search results
    """
    return await search_via_plugin(query, limit, backend="baidu")

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

        # Extract result containers without pretruncation; rejected related searches/cards may
        # precede valid results. Stop below at len(results) >= limit.
        containers = soup.find_all('div', class_=lambda x: x and 'c-container' in x)
        
        for container in containers:
            # Accept titles only from h3 links; a container's first <a> may be a card sublink,
            # e.g. a 40-day forecast, or a related search term rather than the result title.
            link = container.select_one('h3 a[href]')
            if link:
                title = _sanitize_search_text(link.get_text(strip=True))
                if title and 5 < len(title) < 200:
                    # Accept only absolute HTTP(S) URLs: relative site links such as /s?wd=...
                    # and javascript: pseudoprotocols are not search results; do not legitimize them with urljoin.
                    href = link.get('href', '')
                    if not href.startswith(('http://', 'https://')):
                        continue
                    url = href
                    if url in seen_urls:
                        continue

                    # Extract snippets.
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
        
        # If no results were found, try h3 titles, also without pretruncation; rejected related-search
        # h3 elements may lead the list. Stop after limit valid entries.
        if not results:
            for h3 in soup.find_all('h3'):
                link = h3.find('a')
                if link:
                    title = _sanitize_search_text(link.get_text(strip=True))
                    if title and 5 < len(title) < 200:
                        # Match the main loop: HTTP(S) validation, advertising-keyword filtering, and URL deduplication.
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
            # Limit snippet length.
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
    
    The web_search plugin selects the backend and owns all upstream resilience.
    
    Args:
        limit: max number of search results
    
    Returns:
        Dict with the window title and search results
        Note: window_title is the sanitized version to protect privacy
    """
    try:
        # Detect region.
        china_region = is_china_region()
        
        # Get active window titles in both raw and redacted forms.
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
        
        # Clean window titles to remove sensitive information before sending to the LLM.
        cleaned_title = clean_window_title(raw_title)
        
        # Submit one stable cleaned query. The search plugin owns caching, throttling, cooldowns, and
        # backend fallback, preventing each proactive-context refresh from becoming three public-network requests.
        search_queries = [cleaned_title] if cleaned_title else []
        
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
        
        # Never write window titles or queries through logger.
        logger.info("窗口标题已生成受控查询 (queries_count=1)")
        
        # Search and merge results.
        all_results = []
        successful_queries = []
        
        query = search_queries[0]
        logger.info(f"使用受控窗口查询 (len={len(query)})")
        search_result = await search_via_plugin(
            query,
            limit,
        )
        if search_result.get('success') and search_result.get('results'):
            all_results.extend(search_result['results'])
            successful_queries.append(query)
        
        # Deduplicate by URL, falling back to title when missing.
        seen_keys = set()
        unique_results = []
        for result in all_results:
            url = result.get('url', '')
            title = result.get('title', '')
            
            # Prefer URL-based deduplication, falling back to title.
            if url:
                dedup_key = url
            else:
                dedup_key = title
            
            if dedup_key and dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                unique_results.append(result)
        
        # Limit total result count.
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
