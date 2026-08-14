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
from typing import Any, Dict, List, NoReturn, Optional

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
    SearchResponseError,
    decode_html,
    is_baidu_no_results,
    is_baidu_blocked,
    is_ddg_blocked,
    is_ddg_no_results,
    parse_baidu_html,
    parse_ddg_html,
    parse_ddg_lite_html,
)
from ._resilience import (
    SearchCoordinator,
    SearchBusyError,
    SearchCooldownError,
    request_with_retry,
    retry_after_seconds,
    should_skip_fallback,
)

_UA = "N.E.K.O-WebSearch/0.1.4 (+https://github.com/Project-N-E-K-O/N.E.K.O)"

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
_BAIDU_HOME_URL = "https://www.baidu.com/"
_BAIDU_SEARCH_URL = "https://www.baidu.com/s"
_GEOIP_PROVIDERS = (
    ("https://ipwho.is/?fields=success,country_code", "country_code"),
    ("https://ipapi.co/json/", "country_code"),
)

# Countries that cannot reliably access DuckDuckGo
_CN_COUNTRIES = frozenset({"CN"})


def _select_backend(configured: object, country: Optional[str]) -> str:
    backend = str(configured or "auto").strip().lower()
    if backend in {"baidu", "duckduckgo"}:
        return backend
    return "duckduckgo" if country and country not in _CN_COUNTRIES else "baidu"


# ---------------------------------------------------------------------------
# GeoIP detection (same approach as ConfigManager, real IP, no proxy)
# ---------------------------------------------------------------------------

async def _detect_country(timeout: float = 4.0) -> Optional[str]:
    provider_timeout = timeout / max(1, len(_GEOIP_PROVIDERS))
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                proxy=None,
                trust_env=False,
            ) as client:
                for url, field in _GEOIP_PROVIDERS:
                    try:
                        async with asyncio.timeout(provider_timeout):
                            resp = await client.get(
                                url,
                                headers={"User-Agent": "NEKO-WebSearch/0.1"},
                            )
                        resp.raise_for_status()
                        data = resp.json()
                        country = str(data.get(field) or "").strip().upper()
                        if len(country) == 2 and country.isalpha():
                            return country
                    except (TimeoutError, httpx.HTTPError, ValueError, TypeError):
                        continue
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Fetchers (shared client: keeps cookies + connection reuse across searches)
# ---------------------------------------------------------------------------

def _ddg_headers(user_agent: str = _UA) -> Dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _ddg_retry_after(response: httpx.Response) -> float:
    return retry_after_seconds(response.headers) or 300.0


def _raise_ddg_block(error: httpx.HTTPStatusError) -> NoReturn:
    if error.response.status_code not in {403, 429}:
        raise error
    raise SearchBlockedError(
        f"DuckDuckGo 请求受限（{error.response.status_code}）；已停止重试并进入冷却",
        retry_after_seconds=_ddg_retry_after(error.response),
    ) from error


def _check_ddg_block(resp: httpx.Response, html: str) -> None:
    if resp.status_code == 202 or is_ddg_blocked(html):
        raise SearchBlockedError(
            "DuckDuckGo 返回反自动化验证页；已停止重试并进入冷却",
            retry_after_seconds=_ddg_retry_after(resp),
        )


async def _search_ddg_html(
    client: httpx.AsyncClient,
    query: str,
    max_results: int = 8,
    region: str = "wt-wt",
    timeout: float = 15.0,
    user_agent: str = _UA,
    retry_attempts: int = 2,
    retry_base_delay: float = 0.5,
) -> List[Dict[str, str]]:
    try:
        resp = await request_with_retry(
            lambda: client.post(
                _DDG_HTML_URL,
                data={"q": query, "kl": region},
                headers=_ddg_headers(user_agent),
                timeout=timeout,
            ),
            max_attempts=retry_attempts,
            base_delay=retry_base_delay,
        )
    except httpx.HTTPStatusError as error:
        _raise_ddg_block(error)
    html = decode_html(resp.content, resp.headers.get("content-type", ""))
    _check_ddg_block(resp, html)
    results = parse_ddg_html(html, max_results)
    if not results and not is_ddg_no_results(html):
        raise SearchResponseError("DuckDuckGo HTML 未返回可解析结果")
    return results


async def _search_ddg_lite(
    client: httpx.AsyncClient,
    query: str,
    max_results: int = 8,
    region: str = "wt-wt",
    timeout: float = 15.0,
    user_agent: str = _UA,
    retry_attempts: int = 2,
    retry_base_delay: float = 0.5,
) -> List[Dict[str, str]]:
    try:
        resp = await request_with_retry(
            lambda: client.post(
                _DDG_LITE_URL,
                data={"q": query, "kl": region},
                headers=_ddg_headers(user_agent),
                timeout=timeout,
            ),
            max_attempts=retry_attempts,
            base_delay=retry_base_delay,
        )
    except httpx.HTTPStatusError as error:
        _raise_ddg_block(error)
    html = decode_html(resp.content, resp.headers.get("content-type", ""))
    _check_ddg_block(resp, html)
    results = parse_ddg_lite_html(html, max_results)
    if not results and not is_ddg_no_results(html):
        raise SearchResponseError("DuckDuckGo Lite 未返回可解析结果")
    return results


async def _search_baidu(
    client: httpx.AsyncClient,
    query: str,
    max_results: int = 8,
    timeout: float = 15.0,
    user_agent: str = _UA,
    retry_attempts: int = 2,
    retry_base_delay: float = 0.5,
) -> List[Dict[str, str]]:
    headers = {
        "User-Agent": user_agent,
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
            # Cookie 预热失败不阻断搜索本身：没拿到 Cookie 时大概率命中
            # 安全验证页，由下方 is_baidu_blocked 显式报错，无需在此处理
            pass

    resp = await request_with_retry(
        lambda: client.get(
            _BAIDU_SEARCH_URL, params=params, headers=headers, timeout=timeout
        ),
        max_attempts=retry_attempts,
        base_delay=retry_base_delay,
    )

    html = decode_html(resp.content, resp.headers.get("content-type", ""))
    # 被拦截时会 302 到 wappass.baidu.com 验证码页
    if "wappass.baidu.com" in str(resp.url) or is_baidu_blocked(html):
        raise SearchBlockedError("百度返回安全验证页（反爬拦截），请稍后重试")
    results = parse_baidu_html(html, max_results)
    if not results and not is_baidu_no_results(html):
        raise SearchResponseError("百度未返回可解析结果")
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
        self._backend: str = "baidu"
        self._client: Optional[httpx.AsyncClient] = None
        self._client_loop: Optional[asyncio.AbstractEventLoop] = None
        self._user_agent = _UA
        self._coordinator = SearchCoordinator()

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
        defs = self._defaults()
        configured_backend = str(self._cfg.get("backend", "auto")).strip().lower()
        self._country = (
            None
            if configured_backend in {"baidu", "duckduckgo"}
            else await _detect_country()
        )
        self._backend = _select_backend(configured_backend, self._country)
        self._is_cn = self._backend == "baidu"
        min_interval = (
            defs["ddg_min_interval"]
            if self._backend == "duckduckgo"
            else defs["min_interval"]
        )
        cooldown = (
            defs["ddg_cooldown"]
            if self._backend == "duckduckgo"
            else defs["cooldown"]
        )
        max_cooldown = (
            defs["ddg_max_cooldown"]
            if self._backend == "duckduckgo"
            # Progressive cooldown is DDG-specific; Baidu keeps a fixed delay.
            else defs["cooldown"]
        )
        self._coordinator = SearchCoordinator(
            ttl_seconds=defs["cache_ttl"],
            stale_seconds=defs["stale_ttl"],
            max_entries=defs["cache_entries"],
            min_interval_seconds=min_interval,
            cooldown_seconds=cooldown,
            max_cooldown_seconds=max_cooldown,
            queue_wait_seconds=defs["queue_wait"],
        )

        self.logger.info(
            "WebSearch started: country={}, configured_backend={}, backend={}",
            self._country, configured_backend, self._backend,
        )
        return Ok({"status": "running", "backend": self._backend, "country": self._country})

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
        def number(name: str, default: float, low: float, high: float) -> float:
            try:
                value = float(self._cfg.get(name, default))
            except (TypeError, ValueError):
                value = default
            return max(low, min(value, high))

        try:
            retry_attempts = int(self._cfg.get("retry_attempts", 2))
        except (TypeError, ValueError):
            retry_attempts = 2
        try:
            cache_entries = int(self._cfg.get("cache_entries", 128))
        except (TypeError, ValueError):
            cache_entries = 128
        return {
            "max_results": mr,
            "timeout": to,
            "retry_attempts": max(1, min(retry_attempts, 3)),
            "retry_base_delay": number("retry_base_delay_seconds", 0.5, 0.0, 5.0),
            "cache_ttl": number("cache_ttl_seconds", 120.0, 0.0, 3600.0),
            "stale_ttl": number("stale_ttl_seconds", 600.0, 0.0, 86400.0),
            "cache_entries": max(1, min(cache_entries, 1024)),
            "min_interval": number("min_interval_seconds", 0.75, 0.0, 10.0),
            "ddg_min_interval": number(
                "duckduckgo_min_interval_seconds", 3.0, 1.0, 15.0
            ),
            "cooldown": number("cooldown_seconds", 60.0, 1.0, 3600.0),
            "ddg_cooldown": number(
                "duckduckgo_cooldown_seconds", 300.0, 60.0, 3600.0
            ),
            "ddg_max_cooldown": number(
                "duckduckgo_max_cooldown_seconds", 3600.0, 300.0, 86400.0
            ),
            "queue_wait": number("queue_wait_seconds", 2.0, 0.1, 5.0),
            "ddg_retry_base_delay": number(
                "duckduckgo_retry_base_delay_seconds", 2.0, 0.5, 5.0
            ),
            "ddg_fallback_delay": number(
                "duckduckgo_fallback_delay_seconds", 3.0, 1.0, 15.0
            ),
            # Keep the complete operation below the host's default 30-second
            # plugin-entry watchdog, including retries and DDG fallback.
            "total_timeout": number("total_timeout_seconds", 25.0, 1.0, 25.0),
        }

    async def _do_text_search(
        self,
        query: str,
        max_results: int,
        timeout: float,
    ) -> List[Dict[str, str]]:
        defs = self._defaults()
        backend = self._backend
        key = (backend, " ".join(query.casefold().split()), max_results)

        async def fetch() -> List[Dict[str, str]]:
            client = self._get_client()
            retry_base_delay = (
                defs["ddg_retry_base_delay"]
                if backend == "duckduckgo"
                else defs["retry_base_delay"]
            )
            kwargs = {
                "timeout": timeout,
                "user_agent": self._user_agent,
                "retry_attempts": defs["retry_attempts"],
                "retry_base_delay": retry_base_delay,
            }
            if backend == "baidu":
                return await _search_baidu(client, query, max_results, **kwargs)

            try:
                return await _search_ddg_html(client, query, max_results, **kwargs)
            except Exception as e:
                if should_skip_fallback(e):
                    raise
                self.logger.warning("DDG html failed, trying lite: {}", e)
            await asyncio.sleep(
                max(defs["ddg_fallback_delay"], defs["ddg_min_interval"])
            )
            return await _search_ddg_lite(client, query, max_results, **kwargs)

        try:
            async with asyncio.timeout(defs["total_timeout"]):
                return await self._coordinator.run(key, fetch)
        except TimeoutError:
            stale = self._coordinator.stale(key)
            if stale is not None:
                return stale
            raise

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
        timeout=30.0,
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
            len(query), max_r, self._backend,
        )

        try:
            results = await self._do_text_search(query, max_r, timeout)
        except (SearchBlockedError, SearchBusyError, SearchCooldownError) as e:
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
        timeout=30.0,
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
        except (SearchBlockedError, SearchBusyError, SearchCooldownError) as e:
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
