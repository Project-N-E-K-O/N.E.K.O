"""
网络搜索插件 (Web Search)

通过 DuckDuckGo 搜索网络内容，无需 API Key。
优先使用 duckduckgo-search 库，不可用时回退到 httpx + BeautifulSoup 直接抓取。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, unquote

from plugin.sdk.base import NekoPluginBase
from plugin.sdk.decorators import lifecycle, neko_plugin, plugin_entry
from plugin.sdk import ok, fail

_DDGS_AVAILABLE = False
try:
    from duckduckgo_search import DDGS  # type: ignore[import-untyped]
    _DDGS_AVAILABLE = True
except ImportError:
    pass

import httpx
from bs4 import BeautifulSoup  # type: ignore[import-untyped]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"


def _extract_real_url(href: str) -> str:
    """从 DuckDuckGo 跳转链接中提取真实 URL。"""
    if "uddg=" in href:
        match = re.search(r"uddg=([^&]+)", href)
        if match:
            return unquote(match.group(1))
    return href


async def _search_ddg_html(
    query: str,
    max_results: int = 8,
    region: str = "wt-wt",
    timeout: float = 15.0,
) -> List[Dict[str, str]]:
    """通过 DuckDuckGo HTML 端点抓取搜索结果。"""
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://duckduckgo.com/",
    }
    data = {"q": query, "kl": region}

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        resp = await client.post(_DDG_HTML_URL, data=data)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results: List[Dict[str, str]] = []

    for link_tag in soup.select("a.result__a"):
        title = link_tag.get_text(strip=True)
        href = link_tag.get("href", "")
        real_url = _extract_real_url(str(href))
        if not real_url or not title:
            continue

        snippet_tag = link_tag.find_parent("div", class_="result")
        snippet = ""
        if snippet_tag:
            sn = snippet_tag.select_one("a.result__snippet")
            if sn:
                snippet = sn.get_text(strip=True)

        results.append({
            "title": title,
            "url": real_url,
            "snippet": snippet,
        })
        if len(results) >= max_results:
            break

    return results


async def _search_ddg_lite(
    query: str,
    max_results: int = 8,
    region: str = "wt-wt",
    timeout: float = 15.0,
) -> List[Dict[str, str]]:
    """回退: 通过 DuckDuckGo Lite 端点抓取。"""
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    data = {"q": query, "kl": region}

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        resp = await client.post(_DDG_LITE_URL, data=data)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results: List[Dict[str, str]] = []

    for row in soup.find_all("tr"):
        link = row.find("a", href=True)
        if not link:
            continue
        href = str(link.get("href", ""))
        if not href.startswith("http"):
            continue

        title = link.get_text(strip=True)
        real_url = _extract_real_url(href)

        snippet_td = row.find_next_sibling("tr")
        snippet = ""
        if snippet_td:
            snippet_cell = snippet_td.find("td", class_="result-snippet")
            if snippet_cell:
                snippet = snippet_cell.get_text(strip=True)

        if title and real_url:
            results.append({
                "title": title,
                "url": real_url,
                "snippet": snippet,
            })
        if len(results) >= max_results:
            break

    return results


def _search_ddgs_lib(
    query: str,
    max_results: int = 8,
    region: str = "wt-wt",
) -> List[Dict[str, str]]:
    """通过 duckduckgo-search 库搜索。"""
    raw = DDGS().text(query, region=region, max_results=max_results)
    results: List[Dict[str, str]] = []
    for item in raw:
        results.append({
            "title": item.get("title", ""),
            "url": item.get("href", ""),
            "snippet": item.get("body", ""),
        })
    return results


def _search_news_ddgs_lib(
    query: str,
    max_results: int = 8,
    region: str = "wt-wt",
) -> List[Dict[str, str]]:
    """通过 duckduckgo-search 库搜索新闻。"""
    raw = DDGS().news(query, region=region, max_results=max_results)
    results: List[Dict[str, str]] = []
    for item in raw:
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("body", ""),
            "source": item.get("source", ""),
            "date": item.get("date", ""),
        })
    return results


@neko_plugin
class WebSearchPlugin(NekoPluginBase):

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._cfg: Dict[str, Any] = {}

    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = cfg.get("search") if isinstance(cfg.get("search"), dict) else {}

        backend = "ddgs_lib" if _DDGS_AVAILABLE else "httpx_scrape"
        forced = str(self._cfg.get("backend", "auto")).strip().lower()
        if forced == "httpx":
            backend = "httpx_scrape"
        elif forced == "ddgs":
            backend = "ddgs_lib" if _DDGS_AVAILABLE else "httpx_scrape"

        self._backend = backend
        self.logger.info(
            "WebSearch started, backend={}, ddgs_lib_available={}, region={}",
            backend,
            _DDGS_AVAILABLE,
            self._cfg.get("region", "wt-wt"),
        )
        return ok(data={"status": "running", "backend": backend})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        self.logger.info("WebSearch shutdown")
        return ok(data={"status": "shutdown"})

    def _defaults(self):
        return {
            "max_results": int(self._cfg.get("max_results", 8)),
            "region": str(self._cfg.get("region", "wt-wt")),
            "timeout": float(self._cfg.get("timeout_seconds", 15)),
        }

    async def _do_text_search(
        self,
        query: str,
        max_results: int,
        region: str,
        timeout: float,
    ) -> List[Dict[str, str]]:
        if self._backend == "ddgs_lib":
            try:
                return await asyncio.to_thread(
                    _search_ddgs_lib, query, max_results, region,
                )
            except Exception as e:
                self.logger.warning("ddgs lib failed, falling back to httpx: {}", e)

        try:
            return await _search_ddg_html(query, max_results, region, timeout)
        except Exception as e:
            self.logger.warning("html endpoint failed, trying lite: {}", e)

        return await _search_ddg_lite(query, max_results, region, timeout)

    async def _do_news_search(
        self,
        query: str,
        max_results: int,
        region: str,
    ) -> List[Dict[str, str]]:
        if self._backend == "ddgs_lib":
            try:
                return await asyncio.to_thread(
                    _search_news_ddgs_lib, query, max_results, region,
                )
            except Exception as e:
                self.logger.warning("ddgs news failed: {}", e)
                return []
        return []

    @plugin_entry(
        id="search",
        name="网络搜索",
        description="通过 DuckDuckGo 搜索网络内容。返回标题、链接和摘要。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大结果数 (默认 8)",
                    "default": 8,
                },
                "region": {
                    "type": "string",
                    "description": "搜索区域，如 wt-wt(全球), cn-zh(中国), us-en(美国)",
                    "default": "wt-wt",
                },
            },
            "required": ["query"],
        },
    )
    async def search(
        self,
        query: str,
        max_results: int = 0,
        region: str = "",
        **_,
    ):
        if not query or not query.strip():
            return fail("EMPTY_QUERY", "搜索关键词不能为空")

        defs = self._defaults()
        max_r = max_results if max_results > 0 else defs["max_results"]
        rgn = region.strip() or defs["region"]
        timeout = defs["timeout"]

        self.logger.info("Searching: query={!r} max={} region={}", query, max_r, rgn)

        try:
            results = await self._do_text_search(query, max_r, rgn, timeout)
        except Exception as e:
            self.logger.exception("Search failed for query={!r}", query)
            return fail("SEARCH_ERROR", f"搜索失败: {e}")

        self.logger.info("Search returned {} results for {!r}", len(results), query)
        return ok(data={
            "query": query,
            "count": len(results),
            "results": results,
        })

    @plugin_entry(
        id="search_news",
        name="新闻搜索",
        description="通过 DuckDuckGo 搜索最新新闻（需要 duckduckgo-search 库）。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大结果数 (默认 8)",
                    "default": 8,
                },
                "region": {
                    "type": "string",
                    "description": "搜索区域",
                    "default": "wt-wt",
                },
            },
            "required": ["query"],
        },
    )
    async def search_news(
        self,
        query: str,
        max_results: int = 0,
        region: str = "",
        **_,
    ):
        if not query or not query.strip():
            return fail("EMPTY_QUERY", "搜索关键词不能为空")

        defs = self._defaults()
        max_r = max_results if max_results > 0 else defs["max_results"]
        rgn = region.strip() or defs["region"]
        timeout = defs["timeout"]

        if not _DDGS_AVAILABLE:
            return fail(
                "NEWS_UNAVAILABLE",
                "新闻搜索需要 duckduckgo-search 库，请运行: pip install duckduckgo-search",
            )

        self.logger.info("News search: query={!r} max={} region={}", query, max_r, rgn)

        try:
            results = await self._do_news_search(query, max_r, rgn)
        except Exception as e:
            self.logger.exception("News search failed for query={!r}", query)
            return fail("SEARCH_ERROR", f"新闻搜索失败: {e}")

        self.logger.info("News search returned {} results for {!r}", len(results), query)
        return ok(data={
            "query": query,
            "count": len(results),
            "results": results,
        })

    @plugin_entry(
        id="search_summary",
        name="搜索摘要",
        description="搜索并返回适合 AI 阅读的纯文本摘要格式。",
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大结果数",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    )
    async def search_summary(self, query: str, max_results: int = 5, **_):
        if not query or not query.strip():
            return fail("EMPTY_QUERY", "搜索关键词不能为空")

        defs = self._defaults()
        timeout = defs["timeout"]
        rgn = defs["region"]

        try:
            results = await self._do_text_search(query, max_results, rgn, timeout)
        except Exception as e:
            return fail("SEARCH_ERROR", f"搜索失败: {e}")

        lines: list[str] = [f'搜索: "{query}" (共 {len(results)} 条结果)\n']
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            if r.get("snippet"):
                lines.append(f"   {r['snippet']}")
            lines.append(f"   链接: {r['url']}")
            lines.append("")

        summary_text = "\n".join(lines)
        return ok(data={
            "query": query,
            "count": len(results),
            "summary": summary_text,
            "results": results,
        })
