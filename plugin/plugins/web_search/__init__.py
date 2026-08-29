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
import time
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
    MAX_SNIPPET_LEN,
    MAX_TITLE_LEN,
    SearchBlockedError,
    SearchResponseError,
    decode_html,
    is_http_url,
    is_baidu_no_results,
    is_baidu_blocked,
    is_ddg_blocked,
    is_ddg_no_results,
    parse_baidu_html,
    parse_baidu_mobile_html,
    parse_ddg_html,
    parse_ddg_lite_html,
    sanitize_text,
)
from ._resilience import (
    SearchCoordinator,
    SearchBusyError,
    SearchCooldownError,
    request_with_retry,
    retry_after_seconds,
    should_skip_fallback,
)

_UA = "N.E.K.O-WebSearch/0.1.6 (+https://github.com/Project-N-E-K-O/N.E.K.O)"
# Baidu currently answers plain bot-style user agents with a tiny JavaScript
# redirect shell even after the normal BAIDUID cookie warm-up.  Use a
# browser-compatible UA for Baidu while retaining the N.E.K.O product token;
# DuckDuckGo continues to receive the honest crawler UA above.
_BAIDU_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36 "
    "N.E.K.O-WebSearch/0.1.6"
)

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
_ANYSEARCH_SEARCH_URL = "https://api.anysearch.com/v1/search"
_ANYSEARCH_MAX_RESULTS = 20
_BAIDU_HOME_URL = "https://www.baidu.com/"
_BAIDU_SEARCH_URL = "https://www.baidu.com/s"
_BAIDU_MOBILE_SEARCH_URL = "https://m.baidu.com/s"
_BAIDU_MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 10; K) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Mobile Safari/537.36 "
    "N.E.K.O-WebSearch/0.1.6"
)
_GEOIP_PROVIDERS = (
    ("https://ipwho.is/?fields=success,country_code", "country_code"),
    ("https://ipapi.co/json/", "country_code"),
)

# Countries that cannot reliably access DuckDuckGo
_CN_COUNTRIES = frozenset({"CN"})
_BAIDU_COOKIE_STORE_KEY = "baidu_anonymous_cookies"
_ERROR_CODE_BLOCKED = "WEB_SEARCH_BACKEND_BLOCKED"
_ERROR_CODE_BUSY = "WEB_SEARCH_BACKEND_BUSY"
_ERROR_CODE_COOLDOWN = "WEB_SEARCH_BACKEND_COOLDOWN"


def _search_sdk_error(error: Exception) -> SdkError:
    if isinstance(error, SearchBlockedError):
        code = _ERROR_CODE_BLOCKED
    elif isinstance(error, SearchBusyError):
        code = _ERROR_CODE_BUSY
    elif isinstance(error, SearchCooldownError):
        code = _ERROR_CODE_COOLDOWN
    else:
        code = None
    return SdkError(str(error), code=code)


def _select_backend(configured: object, country: Optional[str]) -> str:
    backend = str(configured or "auto").strip().lower()
    if backend in {"anysearch", "baidu", "duckduckgo"}:
        return backend
    return "anysearch"


def _select_anysearch_zone(country: Optional[str]) -> Optional[str]:
    """Map a known GeoIP country to AnySearch's documented region values."""
    if country == "CN":
        return "cn"
    if country:
        return "intl"
    return None


def _fallback_backend(primary_backend: str, country: Optional[str]) -> Optional[str]:
    """Choose the automatic cross-engine fallback for an unforced search."""
    if primary_backend == "anysearch":
        return "baidu" if country in _CN_COUNTRIES or country is None else "duckduckgo"
    if primary_backend == "baidu":
        return "duckduckgo"
    return None


def _clip_untrusted_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _snapshot_baidu_cookies(client: httpx.AsyncClient) -> List[Dict[str, Any]]:
    """Copy anonymous Baidu cookies without touching a user's browser profile."""
    snapshot: List[Dict[str, Any]] = []
    now = time.time()
    for cookie in client.cookies.jar:
        domain = str(cookie.domain or "").lower().lstrip(".")
        if domain != "baidu.com" and not domain.endswith(".baidu.com"):
            continue
        name = str(cookie.name or "")[:128]
        value = str(cookie.value or "")[:4096]
        if not name or not value:
            continue
        expires = int(cookie.expires) if cookie.expires is not None else None
        if expires is not None and expires <= now:
            continue
        item: Dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": str(cookie.domain or ".baidu.com")[:255],
            "path": str(cookie.path or "/")[:255],
        }
        if expires is not None:
            item["expires"] = expires
        snapshot.append(item)
    return snapshot[:64]


def _restore_baidu_cookies(
    client: httpx.AsyncClient,
    saved: object,
) -> None:
    if not isinstance(saved, list):
        return
    for item in saved[:64]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")[:128]
        value = str(item.get("value") or "")[:4096]
        domain = str(item.get("domain") or ".baidu.com")[:255]
        path = str(item.get("path") or "/")[:255]
        expires_value = item.get("expires")
        expires: Optional[int] = None
        if expires_value is not None:
            try:
                expires = int(float(expires_value))
            except (TypeError, ValueError):
                continue
            if expires <= time.time():
                continue
        normalized_domain = domain.lower().lstrip(".")
        if (
            not name
            or not value
            or (
                normalized_domain != "baidu.com"
                and not normalized_domain.endswith(".baidu.com")
            )
        ):
            continue
        client.cookies.set(name, value, domain=domain, path=path)
        if expires is not None:
            # httpx.Cookies.set does not expose expiry. Restore it on the
            # underlying CookieJar so the next persisted snapshot cannot turn
            # a time-limited Baidu token into an immortal session cookie.
            for cookie in client.cookies.jar:
                if (
                    cookie.name == name
                    and cookie.domain == domain
                    and cookie.path == path
                ):
                    cookie.expires = expires
                    cookie.discard = False
                    break


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
    user_agent: str = _BAIDU_UA,
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

    try:
        resp = await request_with_retry(
            lambda: client.get(
                _BAIDU_SEARCH_URL, params=params, headers=headers, timeout=timeout
            ),
            max_attempts=retry_attempts,
            base_delay=retry_base_delay,
        )
    except httpx.HTTPStatusError as error:
        if error.response.status_code in {403, 429}:
            raise SearchBlockedError(
                f"百度桌面端请求受限（{error.response.status_code}），请稍后重试",
                retry_after_seconds=retry_after_seconds(error.response.headers),
            ) from error
        raise

    html = decode_html(resp.content, resp.headers.get("content-type", ""))
    # 桌面端风控比移动 SSR 入口更敏感。桌面端返回验证码或 JavaScript
    # 跳转壳时仍留在百度体系内降级，不直接切换搜索引擎。
    desktop_blocked = "wappass.baidu.com" in str(resp.url) or is_baidu_blocked(html)
    results = parse_baidu_html(html, max_results)
    if not desktop_blocked and (results or is_baidu_no_results(html)):
        return results

    mobile_headers = {
        "User-Agent": _BAIDU_MOBILE_UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": _BAIDU_HOME_URL,
    }
    try:
        mobile_resp = await request_with_retry(
            lambda: client.get(
                _BAIDU_MOBILE_SEARCH_URL,
                params={"word": query},
                headers=mobile_headers,
                timeout=min(timeout, 5.0),
            ),
            max_attempts=1,
            base_delay=retry_base_delay,
        )
    except httpx.HTTPError as error:
        blocked_status = (
            error.response.status_code
            if isinstance(error, httpx.HTTPStatusError)
            else None
        )
        if desktop_blocked or blocked_status in {403, 429}:
            declared_delay = (
                retry_after_seconds(error.response.headers)
                if isinstance(error, httpx.HTTPStatusError)
                else None
            )
            raise SearchBlockedError(
                "百度桌面端已触发安全验证，移动端请求失败，请稍后重试",
                retry_after_seconds=declared_delay,
            ) from error
        raise
    mobile_html = decode_html(
        mobile_resp.content,
        mobile_resp.headers.get("content-type", ""),
    )
    if "wappass.baidu.com" in str(mobile_resp.url) or is_baidu_blocked(mobile_html):
        raise SearchBlockedError("百度桌面端和移动端均返回安全验证页，请稍后重试")
    mobile_results = parse_baidu_mobile_html(mobile_html, max_results)
    if not mobile_results:
        if desktop_blocked:
            raise SearchBlockedError(
                "百度桌面端已触发安全验证，移动端未返回可解析结果，请稍后重试"
            )
        raise SearchResponseError("百度桌面端和移动端均未返回可解析结果")
    return mobile_results


def _anysearch_headers(api_key: str) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Anysearch-Client": "neko-web-search/0.1.6",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


async def _search_anysearch(
    client: httpx.AsyncClient,
    query: str,
    max_results: int = 8,
    timeout: float = 15.0,
    zone: Optional[str] = None,
    api_key: str = "",
    retry_attempts: int = 2,
    retry_base_delay: float = 0.5,
) -> List[Dict[str, str]]:
    """Query AnySearch while allowing anonymous access when no key is configured."""
    result_limit = max(1, min(int(max_results), _ANYSEARCH_MAX_RESULTS))
    payload: Dict[str, object] = {
        "query": query,
        "max_results": result_limit,
    }
    if zone in {"cn", "intl"}:
        payload["zone"] = zone

    try:
        response = await request_with_retry(
            lambda: client.post(
                _ANYSEARCH_SEARCH_URL,
                json=payload,
                headers=_anysearch_headers(api_key),
                timeout=timeout,
            ),
            max_attempts=retry_attempts,
            base_delay=retry_base_delay,
        )
        envelope = response.json()
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        if status == 429:
            raise SearchBlockedError(
                "AnySearch 请求受限；已停止重试并进入冷却",
                retry_after_seconds=retry_after_seconds(error.response.headers),
            ) from error
        if status in {401, 403} and api_key:
            raise SearchResponseError("AnySearch API Key 无效、已失效或无权访问") from error
        raise SearchResponseError(f"AnySearch 请求失败（HTTP {status}）") from error
    except (httpx.HTTPError, ValueError, TypeError) as error:
        raise SearchResponseError(f"AnySearch 响应无效：{type(error).__name__}") from error

    if not isinstance(envelope, dict) or envelope.get("code", 0) != 0:
        raise SearchResponseError("AnySearch 返回了无效响应")
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise SearchResponseError("AnySearch 返回了无效数据")
    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        raise SearchResponseError("AnySearch 返回了无效结果")

    results: List[Dict[str, str]] = []
    for item in raw_results[:_ANYSEARCH_MAX_RESULTS]:
        if not isinstance(item, dict):
            continue
        title = _clip_untrusted_text(
            sanitize_text(str(item.get("title") or "")),
            MAX_TITLE_LEN,
        )
        url = sanitize_text(str(item.get("url") or ""))
        if not title or not is_http_url(url):
            continue
        snippet = _clip_untrusted_text(
            sanitize_text(str(item.get("snippet") or item.get("content") or "")),
            MAX_SNIPPET_LEN,
        )
        results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= result_limit:
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
        self._backend: str = "anysearch"
        self._configured_backend: str = "auto"
        self._anysearch_zone: Optional[str] = None
        self._anysearch_api_key: str = ""
        self._client: Optional[httpx.AsyncClient] = None
        self._client_loop: Optional[asyncio.AbstractEventLoop] = None
        self._baidu_cookies: List[Dict[str, Any]] = []
        self._baidu_pending_cookie_snapshot: Optional[List[Dict[str, Any]]] = None
        self._baidu_persist_task: Optional[asyncio.Task[None]] = None
        self._user_agent = _UA
        self._coordinator = SearchCoordinator()
        self._coordinators: Dict[str, SearchCoordinator] = {
            "anysearch": self._coordinator,
            "baidu": SearchCoordinator(),
            "duckduckgo": SearchCoordinator(),
        }

    def _get_client(self) -> httpx.AsyncClient:
        # The host uses separate asyncio.run() calls for startup, one persistent
        # command loop containing every entry, and shutdown. Rebuild the pool
        # only when crossing those loop boundaries.
        loop = asyncio.get_running_loop()
        if (
            self._client is None
            or self._client.is_closed
            or self._client_loop is not loop
        ):
            if self._client is not None:
                current = _snapshot_baidu_cookies(self._client)
                self._baidu_cookies = current
            self._client = httpx.AsyncClient(follow_redirects=True)
            _restore_baidu_cookies(self._client, self._baidu_cookies)
            self._client_loop = loop
        return self._client

    async def _load_baidu_cookies(self) -> None:
        self._baidu_cookies = []
        store = getattr(self, "store", None)
        if store is None or not getattr(store, "enabled", False):
            return
        result = await store.get(_BAIDU_COOKIE_STORE_KEY, [])
        if hasattr(result, "is_ok") and callable(result.is_ok):
            if not result.is_ok():
                self.logger.warning("Failed to restore Baidu anonymous session")
                return
            saved = result.value
        else:
            saved = getattr(result, "value", result)
        if isinstance(saved, list):
            self._baidu_cookies = [dict(item) for item in saved if isinstance(item, dict)][:64]

    async def _persist_baidu_cookie_snapshot(
        self,
        snapshot: List[Dict[str, Any]],
    ) -> None:
        store = getattr(self, "store", None)
        if store is None or not getattr(store, "enabled", False):
            return
        try:
            result = await store.set(_BAIDU_COOKIE_STORE_KEY, snapshot)
            if (
                hasattr(result, "is_ok")
                and callable(result.is_ok)
                and not result.is_ok()
            ):
                self.logger.warning("Failed to persist Baidu anonymous session")
        except Exception:
            self.logger.warning("Failed to persist Baidu anonymous session")

    def _schedule_baidu_cookie_persist(self, client: httpx.AsyncClient) -> None:
        try:
            current = _snapshot_baidu_cookies(client)
        except Exception:
            self.logger.warning("Failed to snapshot Baidu anonymous session")
            return
        self._baidu_cookies = current
        store = getattr(self, "store", None)
        if store is None or not getattr(store, "enabled", False):
            return
        self._baidu_pending_cookie_snapshot = current
        task = getattr(self, "_baidu_persist_task", None)
        if task is not None and not task.done():
            return

        async def persist_pending() -> None:
            while True:
                snapshot = self._baidu_pending_cookie_snapshot
                self._baidu_pending_cookie_snapshot = None
                if snapshot is None:
                    return
                await self._persist_baidu_cookie_snapshot(snapshot)

        task = asyncio.create_task(persist_pending())
        self._baidu_persist_task = task

        def finish(done: asyncio.Task[None]) -> None:
            if self._baidu_persist_task is done:
                self._baidu_persist_task = None

        task.add_done_callback(finish)

    @lifecycle(id="startup")
    async def startup(self, **_):
        cfg = await self.config.dump(timeout=5.0)
        cfg = cfg if isinstance(cfg, dict) else {}
        self._cfg = cfg.get("search") if isinstance(cfg.get("search"), dict) else {}
        await self._load_baidu_cookies()
        defs = self._defaults()
        configured_backend = str(self._cfg.get("backend", "auto")).strip().lower()
        self._configured_backend = (
            configured_backend
            if configured_backend in {"auto", "anysearch", "baidu", "duckduckgo"}
            else "auto"
        )
        raw_api_key = self._cfg.get("anysearch_api_key", "")
        self._anysearch_api_key = raw_api_key.strip() if isinstance(raw_api_key, str) else ""
        # Entry-level backend="anysearch" is supported even when the configured
        # backend is Baidu or DuckDuckGo, so GeoIP must always be available for
        # AnySearch's per-call zone selection.
        self._country = await _detect_country()
        self._backend = _select_backend(self._configured_backend, self._country)
        self._anysearch_zone = _select_anysearch_zone(self._country)
        self._is_cn = self._country in _CN_COUNTRIES
        common_coordinator_options = {
            "ttl_seconds": defs["cache_ttl"],
            "stale_seconds": defs["stale_ttl"],
            "max_entries": defs["cache_entries"],
            "queue_wait_seconds": defs["queue_wait"],
        }
        self._coordinators = {
            "anysearch": SearchCoordinator(
                **common_coordinator_options,
                min_interval_seconds=defs["min_interval"],
                cooldown_seconds=defs["cooldown"],
                max_cooldown_seconds=defs["cooldown"],
            ),
            "baidu": SearchCoordinator(
                **common_coordinator_options,
                min_interval_seconds=defs["baidu_min_interval"],
                cooldown_seconds=defs["cooldown"],
                # Baidu uses a fixed cooldown; DDG retains progressive backoff.
                max_cooldown_seconds=defs["cooldown"],
            ),
            "duckduckgo": SearchCoordinator(
                **common_coordinator_options,
                min_interval_seconds=defs["ddg_min_interval"],
                cooldown_seconds=defs["ddg_cooldown"],
                max_cooldown_seconds=defs["ddg_max_cooldown"],
            ),
        }
        # Keep the historical attribute for integrations that inspect the
        # primary backend coordinator directly.
        self._coordinator = self._coordinators[self._backend]

        self.logger.info(
            "WebSearch started: country={}, anysearch_zone={}, configured_backend={}, backend={}, anysearch_key={}",
            self._country, self._anysearch_zone, self._configured_backend, self._backend, bool(self._anysearch_api_key),
        )
        return Ok({
            "status": "running",
            "backend": self._backend,
            "country": self._country,
            "anysearch_zone": self._anysearch_zone,
            "anysearch_api_key_configured": bool(self._anysearch_api_key),
        })

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        try:
            async with asyncio.timeout(2.0):
                await self._persist_baidu_cookie_snapshot(self._baidu_cookies)
        except TimeoutError:
            self.logger.warning("Timed out flushing Baidu anonymous session")
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
        total_timeout = number("total_timeout_seconds", 25.0, 5.0, 25.0)
        baidu_interval_high = max(3.0, min(20.0, total_timeout - 2.0))
        return {
            "max_results": mr,
            "timeout": to,
            "retry_attempts": max(1, min(retry_attempts, 3)),
            "retry_base_delay": number("retry_base_delay_seconds", 0.5, 0.0, 5.0),
            "cache_ttl": number("cache_ttl_seconds", 120.0, 0.0, 3600.0),
            "stale_ttl": number("stale_ttl_seconds", 600.0, 0.0, 86400.0),
            "cache_entries": max(1, min(cache_entries, 1024)),
            "min_interval": number("min_interval_seconds", 0.75, 0.0, 10.0),
            "baidu_min_interval": number(
                "baidu_min_interval_seconds", 15.0, 3.0, baidu_interval_high
            ),
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
            "total_timeout": total_timeout,
        }

    async def _do_text_search(
        self,
        query: str,
        max_results: int,
        timeout: float,
        backend: Optional[str] = None,
        preferred_backend: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        defs = self._defaults()
        requested_backend = backend if backend in {"anysearch", "baidu", "duckduckgo"} else None
        hinted_backend = (
            preferred_backend
            if preferred_backend in {"anysearch", "baidu", "duckduckgo"}
            else None
        )
        primary_backend = requested_backend or hinted_backend or self._backend
        configured_is_auto = getattr(self, "_configured_backend", "auto") == "auto"
        entry_is_auto = requested_backend is None
        fallback_backend = (
            _fallback_backend(primary_backend, getattr(self, "_country", None))
            if configured_is_auto and entry_is_auto
            else None
        )
        normalized_query = " ".join(query.casefold().split())
        attempted_backends = [primary_backend]
        total_timeout = defs["total_timeout"]
        deadline = asyncio.get_running_loop().time() + total_timeout

        def coordinator_for(backend: str) -> SearchCoordinator:
            coordinators = getattr(self, "_coordinators", None)
            if isinstance(coordinators, dict) and backend in coordinators:
                return coordinators[backend]
            return self._coordinator

        async def run_backend(
            backend: str,
            budget: float,
        ) -> List[Dict[str, str]]:
            key = (backend, normalized_query, max_results)

            async def fetch() -> List[Dict[str, str]]:
                client = self._get_client()
                retry_base_delay = (
                    defs["ddg_retry_base_delay"]
                    if backend == "duckduckgo"
                    else defs["retry_base_delay"]
                )
                kwargs = {
                    "timeout": timeout,
                    "retry_attempts": defs["retry_attempts"],
                    "retry_base_delay": retry_base_delay,
                }
                if backend == "anysearch":
                    return await _search_anysearch(
                        client,
                        query,
                        max_results,
                        zone=self._anysearch_zone,
                        api_key=self._anysearch_api_key,
                        **kwargs,
                    )
                if backend == "baidu":
                    try:
                        return await _search_baidu(
                            client,
                            query,
                            max_results,
                            user_agent=_BAIDU_UA,
                            **kwargs,
                        )
                    finally:
                        schedule = getattr(
                            self,
                            "_schedule_baidu_cookie_persist",
                            None,
                        )
                        if callable(schedule):
                            schedule(client)

                try:
                    return await _search_ddg_html(
                        client,
                        query,
                        max_results,
                        user_agent=self._user_agent,
                        **kwargs,
                    )
                except Exception as e:
                    if should_skip_fallback(e):
                        raise
                    self.logger.warning("DDG html failed, trying lite: {}", e)
                await asyncio.sleep(
                    max(defs["ddg_fallback_delay"], defs["ddg_min_interval"])
                )
                return await _search_ddg_lite(
                    client,
                    query,
                    max_results,
                    user_agent=self._user_agent,
                    **kwargs,
                )

            async with asyncio.timeout(max(0.01, budget)):
                return await coordinator_for(backend).run(key, fetch)

        try:
            async with asyncio.timeout(total_timeout):
                primary_budget = (
                    total_timeout * 0.72
                    if fallback_backend is not None
                    else total_timeout
                )
                try:
                    return await run_backend(primary_backend, primary_budget)
                except Exception as primary_error:
                    if fallback_backend is None:
                        raise
                    attempted_backends.append(fallback_backend)
                    self.logger.warning(
                        "{} search failed ({}); trying {}",
                        primary_backend, type(primary_error).__name__, fallback_backend,
                    )
                    try:
                        fallback_budget = deadline - asyncio.get_running_loop().time()
                        if fallback_budget <= 0:
                            raise primary_error
                        return await run_backend(fallback_backend, fallback_budget)
                    except TimeoutError:
                        # Let the outer timeout handler inspect retained results
                        # from both attempted backends. Replacing this with the
                        # Baidu error would skip DuckDuckGo's stale cache.
                        raise
                    except Exception:
                        # Preserve the primary error because it describes the
                        # selected backend and has already updated its cooldown.
                        raise primary_error
        except TimeoutError:
            # The coordinator cancels an orphaned shared fetch immediately.
            # At this point asyncio.timeout has restored this task's normal
            # cancellation state, so a loop turn can safely finish the fetch's
            # finally blocks before the plugin entry returns.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            for backend in attempted_backends:
                stale = coordinator_for(backend).stale(
                    (backend, normalized_query, max_results)
                )
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
                "backend": {
                    "type": "string",
                    "enum": ["auto", "anysearch", "baidu", "duckduckgo"],
                    "description": "搜索后端；auto 优先 AnySearch，anysearch 不跨引擎回退",
                    "default": "auto",
                },
            },
            "required": ["query"],
        },
    )
    async def search(
        self,
        query: str,
        max_results: int = 0,
        backend: str = "auto",
        preferred_backend: str = "",
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
            results = await self._do_text_search(
                query,
                max_r,
                timeout,
                backend=backend,
                preferred_backend=preferred_backend,
            )
        except (SearchBlockedError, SearchBusyError, SearchCooldownError) as e:
            return Err(_search_sdk_error(e))
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
                "backend": {
                    "type": "string",
                    "enum": ["auto", "anysearch", "baidu", "duckduckgo"],
                    "description": "搜索后端；auto 优先 AnySearch，anysearch 不跨引擎回退",
                    "default": "auto",
                },
            },
            "required": ["query"],
        },
    )
    async def search_summary(
        self,
        query: str,
        max_results: int = 5,
        backend: str = "auto",
        preferred_backend: str = "",
        **_,
    ):
        if not query or not query.strip():
            return Err(SdkError("搜索关键词不能为空"))

        defs = self._defaults()
        max_r = max_results if max_results > 0 else defs["max_results"]
        max_r = max(3, max_r)
        timeout = defs["timeout"]

        try:
            results = await self._do_text_search(
                query,
                max_r,
                timeout,
                backend=backend,
                preferred_backend=preferred_backend,
            )
        except (SearchBlockedError, SearchBusyError, SearchCooldownError) as e:
            return Err(_search_sdk_error(e))
        except Exception as e:
            self.logger.exception("Search failed (query_len={})", len(query))
            return Err(SdkError(f"搜索失败: {type(e).__name__}"))

        return Ok({
            "query": query,
            "count": len(results),
            "summary": self._build_summary(query, results),
            "results": results,
        })
