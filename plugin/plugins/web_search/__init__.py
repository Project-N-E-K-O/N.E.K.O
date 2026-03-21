"""
网络搜索插件 (Web Search)

根据用户真实 IP 自动选择搜索引擎：
- 中国大陆 → Baidu
- 海外 → DuckDuckGo HTML 抓取
全部基于 httpx + BeautifulSoup，不依赖任何第三方搜索库。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from plugin.sdk.plugin import (
    NekoPluginBase,
    neko_plugin,
    plugin_entry,
    lifecycle,
    Ok,
    Err,
    SdkError,
)

import httpx
from bs4 import BeautifulSoup  # type: ignore[import-untyped]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
_BAIDU_SEARCH_URL = "https://www.baidu.com/s"
_GEOIP_URL = "http://ip-api.com/json/?fields=countryCode"

# Countries that cannot reliably access DuckDuckGo
_CN_COUNTRIES = frozenset({"CN"})


# ---------------------------------------------------------------------------
# GeoIP detection (same approach as ConfigManager, real IP, no proxy)
# ---------------------------------------------------------------------------

async def _detect_country(timeout: float = 4.0) -> Optional[str]:
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            proxy=None,
        ) as client:
            resp = await client.get(
                _GEOIP_URL,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            data = resp.json()
            return (data.get("countryCode") or "").upper() or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# DuckDuckGo HTML scraping (international)
# ---------------------------------------------------------------------------

def _extract_real_url(href: str) -> str:
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
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
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

        results.append({"title": title, "url": real_url, "snippet": snippet})
        if len(results) >= max_results:
            break

    return results


async def _search_ddg_lite(
    query: str,
    max_results: int = 8,
    region: str = "wt-wt",
    timeout: float = 15.0,
) -> List[Dict[str, str]]:
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html",
        "Accept-Language": "en-US,en;q=0.9",
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
            results.append({"title": title, "url": real_url, "snippet": snippet})
        if len(results) >= max_results:
            break

    return results


# ---------------------------------------------------------------------------
# Baidu scraping (China mainland)
# ---------------------------------------------------------------------------

async def _search_baidu(
    query: str,
    max_results: int = 8,
    timeout: float = 15.0,
) -> List[Dict[str, str]]:
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.baidu.com/",
    }
    params = {"wd": query, "rn": str(min(max_results, 50))}

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
    ) as client:
        resp = await client.get(_BAIDU_SEARCH_URL, params=params)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results: List[Dict[str, str]] = []

    for item in soup.select("div.result, div.c-container"):
        link = item.find("a", href=True)
        if not link:
            continue
        title = link.get_text(strip=True)
        href = str(link.get("href", ""))
        if not title or not href:
            continue

        snippet = ""
        for sel in ("div.c-abstract", "span.content-right_8Zs40", "div.c-span-last"):
            sn = item.select_one(sel)
            if sn:
                snippet = sn.get_text(strip=True)
                break
        if not snippet:
            abs_tag = item.find("div", class_=re.compile(r"abstract|summary|desc"))
            if abs_tag:
                snippet = abs_tag.get_text(strip=True)

        results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= max_results:
            break

    return results


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------

@neko_plugin
class WebSearchPlugin(NekoPluginBase):

    def __init__(self, ctx):
        super().__init__(ctx)
        self.file_logger = self.enable_file_logging(log_level="INFO")
        self.logger = self.file_logger
        self._cfg: Dict[str, Any] = {}
        self._country: Optional[str] = None
        self._is_cn: bool = False

    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = cfg.get("search") if isinstance(cfg.get("search"), dict) else {}

        self._country = await _detect_country()
        self._is_cn = self._country in _CN_COUNTRIES if self._country else False

        backend = "baidu" if self._is_cn else "duckduckgo"
        self.logger.info(
            "WebSearch started: country={}, is_cn={}, backend={}",
            self._country, self._is_cn, backend,
        )
        return Ok({"status": "running", "backend": backend, "country": self._country})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        self.logger.info("WebSearch shutdown")
        return Ok({"status": "shutdown"})

    def _defaults(self):
        try:
            mr = int(self._cfg.get("max_results", 8))
        except (TypeError, ValueError):
            mr = 8
        mr = max(1, min(mr, 50))
        try:
            to = float(self._cfg.get("timeout_seconds", 15))
        except (TypeError, ValueError):
            to = 15.0
        if to <= 0:
            to = 15.0
        return {"max_results": mr, "timeout": to}

    async def _do_text_search(
        self,
        query: str,
        max_results: int,
        timeout: float,
    ) -> List[Dict[str, str]]:
        if self._is_cn:
            return await _search_baidu(query, max_results, timeout)

        try:
            return await _search_ddg_html(query, max_results, timeout=timeout)
        except Exception as e:
            self.logger.warning("DDG html failed, trying lite: {}", e)

        return await _search_ddg_lite(query, max_results, timeout=timeout)

    @staticmethod
    def _build_summary(query: str, results: List[Dict[str, str]]) -> str:
        lines: list[str] = [f'搜索: "{query}" (共 {len(results)} 条结果)\n']
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            if r.get("snippet"):
                lines.append(f"   {r['snippet']}")
            lines.append(f"   链接: {r['url']}")
            lines.append("")
        return "\n".join(lines)

    @plugin_entry(
        id="search",
        name="网络搜索",
        description="搜索网络内容。自动根据用户地区选择搜索引擎（国内百度/海外DuckDuckGo）。",
        llm_result_fields=["summary"],
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
            },
            "required": ["query"],
        },
    )
    async def search(
        self,
        query: str,
        max_results: int = 0,
        **_,
    ):
        if not query or not query.strip():
            return Err(SdkError("搜索关键词不能为空"))

        defs = self._defaults()
        max_r = max_results if max_results > 0 else defs["max_results"]
        timeout = defs["timeout"]

        self.logger.info("Searching: query={!r} max={} engine={}", query, max_r, "baidu" if self._is_cn else "duckduckgo")

        try:
            results = await self._do_text_search(query, max_r, timeout)
        except Exception as e:
            self.logger.exception("Search failed for query={!r}", query)
            return Err(SdkError(f"搜索失败: {e}"))

        summary = self._build_summary(query, results)
        self.logger.info("Search returned {} results for {!r}", len(results), query)
        return Ok({
            "query": query,
            "count": len(results),
            "summary": summary,
            "results": results,
        })

    @plugin_entry(
        id="search_summary",
        name="搜索摘要",
        description="搜索并返回适合 AI 阅读的纯文本摘要格式。",
        llm_result_fields=["summary"],
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
            return Err(SdkError("搜索关键词不能为空"))

        defs = self._defaults()
        max_r = max_results if max_results > 0 else defs["max_results"]
        timeout = defs["timeout"]

        try:
            results = await self._do_text_search(query, max_r, timeout)
        except Exception as e:
            return Err(SdkError(f"搜索失败: {e}"))

        return Ok({
            "query": query,
            "count": len(results),
            "summary": self._build_summary(query, results),
            "results": results,
        })
