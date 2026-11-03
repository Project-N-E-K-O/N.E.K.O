"""
web_search parsing layer: pure functions without SDK/network dependencies, suitable for unit tests.

Security constraints:
- All LLM/TTS-bound text (titles/snippets) must first pass sanitize_text. External pages are untrusted;
  Baidu text nodes mix private-use iconfont characters (e.g. U+E687), decoding errors can produce
  U+FFFD, and zero-width/bidi controls may obscure injections.
- Accept only absolute HTTP(S) result URLs; reject javascript: and relative links such as related-search navigation.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from typing import Dict, List, Optional
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup  # type: ignore[import-untyped]

MAX_TITLE_LEN = 200
MAX_SNIPPET_LEN = 320

_WS_RE = re.compile(r"\s+")
_HEADER_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?\s*([A-Za-z0-9_\-]+)", re.I)
_META_CHARSET_RE = re.compile(
    rb"<meta[^>]{0,120}?charset\s*=\s*[\"']?\s*([A-Za-z0-9_\-]+)", re.I
)
# gb2312/gbk decoders reject some extended characters on valid pages; upgrade both to superset gb18030.
_ENCODING_ALIASES = {"gb2312": "gb18030", "gbk": "gb18030"}
class SearchBlockedError(RuntimeError):
    """The search engine returned an anti-scraping verification page instead of results."""

    is_search_block = True

    def __init__(
        self, message: str, *, retry_after_seconds: Optional[float] = None
    ) -> None:
        super().__init__(message)
        delay = None if retry_after_seconds is None else float(retry_after_seconds)
        self.retry_after_seconds = (
            max(0.0, delay) if delay is not None and math.isfinite(delay) else None
        )


class SearchResponseError(RuntimeError):
    """A search response could not be interpreted as a usable result page."""


def sanitize_text(text: str) -> str:
    """Sanitize untrusted page text: remove control/format/private-use/surrogate characters and U+FFFD, then collapse whitespace."""
    if not text:
        return ""
    kept: List[str] = []
    for ch in text:
        if ch.isspace():
            kept.append(" ")
            continue
        if ord(ch) == 0xFFFD:
            continue
        # Cc/Cf/Co/Cs: control, format (including zero-width/bidi overrides), private-use (iconfont), and surrogate characters.
        if unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs"):
            continue
        kept.append(ch)
    return _WS_RE.sub(" ", "".join(kept)).strip()


def decode_html(content: bytes, content_type: str = "") -> str:
    """Decode strictly in order: header charset -> page meta -> utf-8 -> gb18030.

    Baidu headers sometimes omit charset or contradict the actual encoding. Trusting default utf-8
    turns GBK pages into U+FFFD sequences. Try candidates strictly, using replacement only after all fail.
    """
    candidates: List[str] = []
    m = _HEADER_CHARSET_RE.search(content_type or "")
    if m:
        candidates.append(m.group(1))
    m = _META_CHARSET_RE.search(content[:4096])
    if m:
        candidates.append(m.group(1).decode("ascii", errors="ignore"))
    candidates.extend(["utf-8", "gb18030"])
    for enc in candidates:
        enc = _ENCODING_ALIASES.get(enc.lower(), enc)
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("utf-8", errors="replace")


def is_http_url(url: str) -> bool:
    # Validate hostname rather than netloc: malformed URLs such as "https://@" or "http://:80"
    # have nonempty netloc but hostname=None.
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        return False


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


# ---------------------------------------------------------------------------
# DuckDuckGo
# ---------------------------------------------------------------------------

def extract_real_url(href: str) -> str:
    if "uddg=" in href:
        match = re.search(r"uddg=([^&]+)", href)
        if match:
            return unquote(match.group(1))
    return href


def is_ddg_ad_url(url: str) -> bool:
    # Ads are identified by duckduckgo.com/y.js redirect wrappers; do not inspect target URLs for
    # ad_provider= or similar parameters, which legitimate results may also contain.
    try:
        parsed = urlparse(url)
    except ValueError:
        return True
    host = (parsed.hostname or "").lower()
    is_ddg_host = host == "duckduckgo.com" or host.endswith(".duckduckgo.com")
    return is_ddg_host and parsed.path == "/y.js"


def is_ddg_blocked(html: str) -> bool:
    """Detect known DuckDuckGo challenge pages returned with a 2xx status."""
    soup = BeautifulSoup(html[:20000], "html.parser")
    if soup.select_one("form#anomaly-modal") is not None:
        return True

    for tag, attribute in (("form", "action"), ("script", "src")):
        for node in soup.find_all(tag):
            url = str(node.get(attribute, "")).strip()
            if not url:
                continue
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            if parsed.path == "/anomaly.js" and (
                not host or host == "duckduckgo.com" or host.endswith(".duckduckgo.com")
            ):
                return True
    return False


def is_ddg_no_results(html: str) -> bool:
    """Detect DuckDuckGo's explicit empty-results container."""
    soup = BeautifulSoup(html, "html.parser")
    return soup.select_one(".no-results, #no-results") is not None


def is_baidu_no_results(html: str) -> bool:
    """Detect Baidu's explicit empty-results response."""
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("#content_left")
    if container is None:
        return False
    if container.select_one(".nors") is not None:
        return True
    text = sanitize_text(container.get_text(" ", strip=True))
    return any(
        marker in text
        for marker in (
            "抱歉，没有找到与",
            "抱歉没有找到与",
            "没有找到与您查询的",
        )
    )


def parse_ddg_html(html: str, max_results: int = 8) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, str]] = []

    for link_tag in soup.select("a.result__a"):
        if link_tag.find_parent("div", class_=re.compile(r"result--ad")):
            continue

        title = sanitize_text(link_tag.get_text(strip=True))
        real_url = extract_real_url(str(link_tag.get("href", "")))
        if not title or not is_http_url(real_url) or is_ddg_ad_url(real_url):
            continue

        snippet = ""
        parent = link_tag.find_parent("div", class_="result")
        if parent is not None:
            sn = parent.select_one("a.result__snippet")
            if sn is not None:
                snippet = sanitize_text(sn.get_text(strip=True))

        results.append({
            "title": _clip(title, MAX_TITLE_LEN),
            "url": real_url,
            "snippet": _clip(snippet, MAX_SNIPPET_LEN),
        })
        if len(results) >= max_results:
            break

    return results


def parse_ddg_lite_html(html: str, max_results: int = 8) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, str]] = []

    for row in soup.find_all("tr"):
        link = row.find("a", href=True)
        if link is None:
            continue
        href = str(link.get("href", ""))
        # Lite endpoint redirects may be protocol-relative (//duckduckgo.com/l/?uddg=...).
        if href.startswith("//"):
            href = "https:" + href
        if not href.startswith(("http://", "https://")):
            continue

        title = sanitize_text(link.get_text(strip=True))
        real_url = extract_real_url(href)
        if not title or not is_http_url(real_url) or is_ddg_ad_url(real_url):
            continue

        snippet = ""
        next_row = row.find_next_sibling("tr")
        if next_row is not None:
            cell = next_row.find("td", class_="result-snippet")
            if cell is not None:
                snippet = sanitize_text(cell.get_text(strip=True))

        results.append({
            "title": _clip(title, MAX_TITLE_LEN),
            "url": real_url,
            "snippet": _clip(snippet, MAX_SNIPPET_LEN),
        })
        if len(results) >= max_results:
            break

    return results


# ---------------------------------------------------------------------------
# Baidu
# ---------------------------------------------------------------------------

def is_baidu_blocked(html: str) -> bool:
    head = html[:5000]
    if "百度安全验证" in head or "wappass.baidu.com" in head:
        return True

    # Baidu may answer non-browser clients with a tiny JavaScript redirect
    # document while still returning HTTP 200.  httpx does not execute the
    # redirect, so treating it as a normal empty result would bypass the
    # backend cooldown and encourage an immediate retry burst.
    if len(html) <= 4096 and not re.search(
        r"id\s*=\s*['\"]content_left['\"]", head, re.I
    ):
        if re.search(
            r"<meta\b[^>]*\bhttp-equiv\s*=\s*['\"]?\s*refresh\b",
            head,
            re.I,
        ):
            return True
        compact = re.sub(r"\s+", "", head).casefold()
        return any(
            marker in compact
            for marker in (
                "location.replace(",
                "location.assign(",
                "window.location=",
                "location.href=",
            )
        )
    return False


def parse_baidu_html(html: str, max_results: int = 8) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, str]] = []
    seen_urls: set = set()

    for item in soup.select("div.result, div.c-container"):
        # Promotional containers carry data-tuiguang attributes.
        if item.has_attr("data-tuiguang"):
            continue

        # Accept title links only under h3: a container's first <a> may be a card sublink (e.g. a
        # 40-day weather forecast link) or related search term, rather than the result title.
        title_link = item.select_one("h3 a[href]")
        if title_link is None:
            continue

        title = sanitize_text(title_link.get_text(strip=True))
        href = str(title_link.get("href", "")).strip()
        if not title or not is_http_url(href) or href in seen_urls:
            continue
        seen_urls.add(href)

        snippet = ""
        # content-right class names have CSS-module hash suffixes (e.g. content-right_8Zs40), so match by prefix.
        for sel in ("div.c-abstract", "span[class*='content-right']", "div.c-span-last"):
            sn = item.select_one(sel)
            if sn is not None:
                snippet = sanitize_text(sn.get_text(strip=True))
                break
        if not snippet:
            abs_tag = item.find("div", class_=re.compile(r"abstract|summary|desc"))
            if abs_tag is not None:
                snippet = sanitize_text(abs_tag.get_text(strip=True))

        results.append({
            "title": _clip(title, MAX_TITLE_LEN),
            "url": href,
            "snippet": _clip(snippet, MAX_SNIPPET_LEN),
        })
        if len(results) >= max_results:
            break

    return results


def parse_baidu_mobile_html(
    html: str,
    max_results: int = 8,
) -> List[Dict[str, str]]:
    """Parse Baidu's mobile SSR cards without executing page JavaScript."""
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, str]] = []
    seen_urls: set[str] = set()

    for item in soup.select("div.c-result.result"):
        try:
            metadata = json.loads(str(item.get("data-log") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        url = str(metadata.get("mu") or "").strip()
        if not is_http_url(url) or url in seen_urls:
            continue

        # Recommendation/search-suggestion cards use Baidu-owned synthetic
        # targets and are not ordinary web results.
        host = (urlparse(url).hostname or "").lower()
        if host.endswith("recommend_list.baidu.com"):
            continue

        title_tag = item.select_one("h3.cosc-title, h3")
        if title_tag is None:
            continue
        title = sanitize_text(title_tag.get_text(" ", strip=True))
        if not title:
            continue

        snippet = ""
        snippet_tag = item.select_one(
            "span[class*='summary-text'], div[class*='summary-gap']"
        )
        if snippet_tag is not None:
            snippet = sanitize_text(snippet_tag.get_text(" ", strip=True))

        seen_urls.add(url)
        results.append(
            {
                "title": _clip(title, MAX_TITLE_LEN),
                "url": url,
                "snippet": _clip(snippet, MAX_SNIPPET_LEN),
            }
        )
        if len(results) >= max_results:
            break

    return results
