"""
网络搜索插件 (Web Search)

根据用户真实 IP 自动选择搜索引擎：
- 中国大陆 → Baidu
- 海外 → DuckDuckGo HTML 抓取
全部基于 httpx + BeautifulSoup，不依赖任何第三方搜索库。
解析与文本清洗逻辑在 _parsing.py（纯函数，可单测）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

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

from ._parsing import (
    SearchBlockedError,
    decode_html,
    is_baidu_blocked,
    parse_baidu_html,
    parse_ddg_html,
    parse_ddg_lite_html,
)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
_BAIDU_HOME_URL = "https://www.baidu.com/"
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
# Fetchers (shared client: keeps cookies + connection reuse across searches)
# ---------------------------------------------------------------------------

def _ddg_headers() -> Dict[str, str]:
    return {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://duckduckgo.com/",
    }


async def _search_ddg_html(
    client: httpx.AsyncClient,
    query: str,
    max_results: int = 8,
    region: str = "wt-wt",
    timeout: float = 15.0,
) -> List[Dict[str, str]]:
    resp = await client.post(
        _DDG_HTML_URL,
        data={"q": query, "kl": region},
        headers=_ddg_headers(),
        timeout=timeout,
    )
    resp.raise_for_status()
    html = decode_html(resp.content, resp.headers.get("content-type", ""))
    return parse_ddg_html(html, max_results)


async def _search_ddg_lite(
    client: httpx.AsyncClient,
    query: str,
    max_results: int = 8,
    region: str = "wt-wt",
    timeout: float = 15.0,
) -> List[Dict[str, str]]:
    resp = await client.post(
        _DDG_LITE_URL,
        data={"q": query, "kl": region},
        headers=_ddg_headers(),
        timeout=timeout,
    )
    resp.raise_for_status()
    html = decode_html(resp.content, resp.headers.get("content-type", ""))
    return parse_ddg_lite_html(html, max_results)


async def _search_baidu(
    client: httpx.AsyncClient,
    query: str,
    max_results: int = 8,
    timeout: float = 15.0,
) -> List[Dict[str, str]]:
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": _BAIDU_HOME_URL,
    }
    params = {"wd": query, "rn": str(min(max_results, 50)), "ie": "utf-8"}

    # 无 BAIDUID Cookie 的裸请求几乎必中"百度安全验证"页，先访问首页领 Cookie
    if not any(c.name == "BAIDUID" for c in client.cookies.jar):
        try:
            await client.get(_BAIDU_HOME_URL, headers=headers, timeout=timeout)
        except httpx.HTTPError:
            pass

    resp = await client.get(
        _BAIDU_SEARCH_URL, params=params, headers=headers, timeout=timeout
    )
    resp.raise_for_status()

    html = decode_html(resp.content, resp.headers.get("content-type", ""))
    # 被拦截时会 302 到 wappass.baidu.com 验证码页
    if "wappass.baidu.com" in str(resp.url) or is_baidu_blocked(html):
        raise SearchBlockedError("百度返回安全验证页（反爬拦截），请稍后重试")
    return parse_baidu_html(html, max_results)


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
        self._client: Optional[httpx.AsyncClient] = None
        self._client_loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_client(self) -> httpx.AsyncClient:
        # 宿主对 startup / 命令循环 / shutdown 分别 asyncio.run()（plugin/core/host.py），
        # 连接池绑定在创建它的循环上：只在同一循环内复用，循环切换时丢弃重建
        loop = asyncio.get_running_loop()
        if (
            self._client is None
            or self._client.is_closed
            or self._client_loop is not loop
        ):
            self._client = httpx.AsyncClient(follow_redirects=True)
            self._client_loop = loop
        return self._client

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
        client, self._client = self._client, None
        self._client_loop = None
        if client is not None and not client.is_closed:
            try:
                await client.aclose()
            except Exception:
                # shutdown 运行在新的事件循环里，跨循环关闭旧连接池可能报错；
                # 进程即将退出，尽力关闭即可
                pass
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
        client = self._get_client()
        if self._is_cn:
            return await _search_baidu(client, query, max_results, timeout)

        try:
            return await _search_ddg_html(client, query, max_results, timeout=timeout)
        except Exception as e:
            self.logger.warning("DDG html failed, trying lite: {}", e)

        return await _search_ddg_lite(client, query, max_results, timeout=timeout)

    @staticmethod
    def _build_summary(query: str, results: List[Dict[str, str]]) -> str:
        lines: list[str] = [f'搜索: "{query}" (共 {len(results)} 条结果)\n']
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            if r.get("snippet"):
                lines.append(f"   {r['snippet']}")
            lines.append("")
        return "\n".join(lines)

    @plugin_entry(
        id="search",
        name="网络搜索",
        description="搜索网络内容。自动根据用户地区选择搜索引擎（国内百度/海外DuckDuckGo）。"
                    "重要：query 应保留用户原始语言（如中文问题就用中文搜索），"
                    "不要翻译成英文，这样能获得更准确的本地化结果。",
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词（保留用户原始语言，不要翻译）",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大结果数 (默认 8，最少 3)",
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
        max_r = max(3, max_r)
        timeout = defs["timeout"]

        # query / titles / snippets / summary 含外部网页内容 + 用户搜索词，
        # 任何输出渠道（logger/stdout）都只记录长度与条数
        self.logger.info(
            "Searching: query_len={} max={} engine={}",
            len(query), max_r, "baidu" if self._is_cn else "duckduckgo",
        )

        try:
            results = await self._do_text_search(query, max_r, timeout)
        except SearchBlockedError as e:
            return Err(SdkError(str(e)))
        except Exception as e:
            # 异常文本可能带完整请求 URL（含 wd= 查询词），只回传类型名，
            # 细节留在本地文件日志里
            self.logger.exception("Search failed (query_len={})", len(query))
            return Err(SdkError(f"搜索失败: {type(e).__name__}"))

        summary = self._build_summary(query, results)
        self.logger.info(
            "Search returned {} results (query_len={}, summary_len={})",
            len(results), len(query), len(summary),
        )
        return Ok({
            "query": query,
            "count": len(results),
            "summary": summary,
            "results": results,
        })

    @plugin_entry(
        id="search_summary",
        name="搜索摘要",
        description="搜索并返回适合 AI 阅读的纯文本摘要格式。"
                    "重要：query 应保留用户原始语言，不要翻译。",
        llm_result_fields=["summary"],
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词（保留用户原始语言，不要翻译）",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最大结果数（最少 3）",
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
        max_r = max(3, max_r)
        timeout = defs["timeout"]

        try:
            results = await self._do_text_search(query, max_r, timeout)
        except SearchBlockedError as e:
            return Err(SdkError(str(e)))
        except Exception as e:
            self.logger.exception("Search failed (query_len={})", len(query))
            return Err(SdkError(f"搜索失败: {type(e).__name__}"))

        return Ok({
            "query": query,
            "count": len(results),
            "summary": self._build_summary(query, results),
            "results": results,
        })
