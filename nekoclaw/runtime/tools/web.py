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

"""The ``web_fetch`` and ``web_search`` tools.

Result layout, caps and error wording follow dsh ``packages/web/tool-web`` and
``web-fetch-http``. One rule is stricter than dsh: ``web_fetch`` only reaches
public addresses. Without a proxy the host name is resolved here, every
address must be public, and the connection goes to exactly the address that
was checked. Behind a proxy the proxy resolves the name, so only address
literals and local host names can be refused.

``web_search`` has no backend in the runtime; the host callback runs the
queries and this module merges and renders the results.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import SplitResult, urljoin, urlsplit

from config.prompts.prompts_nekoclaw import (
    EXTERNAL_WEB_CONTENT_NOTICE,
    WEB_FETCH_HTML_OMITTED,
    WEB_FETCH_TRUNCATION_FOOTER,
)
from nekoclaw.runtime.loop.tools import AgentTool
from nekoclaw.runtime.tools.base import (
    ToolCallInfo,
    ToolContext,
    ToolError,
    define_tool,
    run_cancellable,
)

MAX_URL_LENGTH = 2048
MAX_RESPONSE_BYTES = 5_000_000
MAX_BODY_CHARS = 100_000
MAX_OUTPUT_CHARS = 200_000
MAX_REDIRECTS = 5
FETCH_TIMEOUT_SECONDS = 30.0
SEARCH_MAX_QUERIES = 10
SEARCH_MAX_RESULTS = 10
SEARCH_SUMMARY_MAX_CHARS = 800

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_ACCEPT = "text/html,application/xhtml+xml,text/*;q=0.9,application/json;q=0.8"
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
_REMOVED_TAGS = ("script", "style", "noscript", "template", "iframe", "object", "embed")
_CHARSET_RE = re.compile(r';\s*charset\s*=\s*"?([^";]+)"?', re.IGNORECASE)


# ---------------------------------------------------------------------------
# URL and address checks
# ---------------------------------------------------------------------------


def parse_fetch_url(raw: str) -> SplitResult:
    """Validate a URL the way dsh ``web-fetch-http`` does and return its parts."""
    if len(raw) > MAX_URL_LENGTH:
        raise ToolError(f"URL exceeds the maximum length of {MAX_URL_LENGTH}")
    try:
        parts = urlsplit(raw.strip())
        port = parts.port
    except ValueError as exc:
        raise ToolError(f"invalid URL: {raw}") from exc
    del port
    if parts.scheme not in ("http", "https"):
        if not parts.scheme or not parts.netloc:
            raise ToolError(f"invalid URL: {raw}")
        raise ToolError(
            f'unsupported URL scheme "{parts.scheme}:" (only http and https are allowed)'
        )
    if not parts.hostname:
        raise ToolError(f"invalid URL: {raw}")
    if parts.username or parts.password:
        raise ToolError("credentials in URLs are not allowed")
    return parts


def is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return whether ``address`` is routable on the public internet."""
    if isinstance(address, ipaddress.IPv6Address):
        mapped = address.ipv4_mapped
        if mapped is not None:
            return is_public_address(mapped)
        if address.sixtofour is not None:
            return is_public_address(address.sixtofour)
        if address.teredo is not None:
            return False
    return bool(
        address.is_global
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_private
        and not address.is_unspecified
    )


def _literal_address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def check_host_name(host: str) -> None:
    """Refuse address literals that are not public and names that are local by definition."""
    lowered = host.lower().rstrip(".")
    literal = _literal_address(lowered)
    if literal is not None:
        if not is_public_address(literal):
            raise ToolError(f"blocked URL: {host} is not a public address")
        return
    if lowered == "localhost" or lowered.endswith(_LOCAL_SUFFIXES) or "." not in lowered:
        raise ToolError(f"blocked URL: {host} is not a public host name")


async def resolve_public(host: str, port: int) -> str:
    """Resolve ``host`` and return one address, after checking that all are public."""
    literal = _literal_address(host)
    if literal is not None:
        return str(literal)
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ToolError(f"could not resolve {host}: {exc.strerror or exc}") from exc
    addresses: list[str] = []
    for info in infos:
        text = str(info[4][0])
        try:
            parsed = ipaddress.ip_address(text.split("%", 1)[0])
        except ValueError:
            continue
        if not is_public_address(parsed):
            raise ToolError(f"blocked URL: {host} resolves to {text}, which is not a public address")
        addresses.append(str(parsed))
    if not addresses:
        raise ToolError(f"could not resolve {host}")
    for candidate in addresses:
        if ":" not in candidate:
            return candidate
    return addresses[0]


def select_proxy(parts: SplitResult) -> str | None:
    """Return the proxy a request to ``parts`` goes through, or ``None`` for a direct one.

    This one answer decides both how the request is sent and whether the
    target's address is resolved and checked here. The HTTP client is never
    left to read the proxy settings itself: if it could route differently, a
    request believed to be proxied could go out directly and unchecked.
    """
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        proxies = urllib.request.getproxies()
        proxy = proxies.get(parts.scheme) or proxies.get("all")
        if not proxy:
            return None
        # Exclusion lists name hosts with or without a port; the port is passed
        # along so that both spellings are recognised.
        if urllib.request.proxy_bypass(f"{host}:{port}"):
            return None
    except Exception:  # noqa: BLE001 - platform proxy lookup must not fail the fetch
        return None
    return proxy if "://" in proxy else f"http://{proxy}"


def same_origin(a: SplitResult, b: SplitResult) -> bool:
    def port(parts: SplitResult) -> int:
        return parts.port or (443 if parts.scheme == "https" else 80)

    return a.scheme == b.scheme and a.hostname == b.hostname and port(a) == port(b)


# ---------------------------------------------------------------------------
# Body handling
# ---------------------------------------------------------------------------


def classify_content_type(content_type: str | None) -> str | None:
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime in ("text/html", "application/xhtml+xml"):
        return "html"
    if mime.startswith("text/"):
        return "text"
    if mime in ("application/json", "application/xml") or mime.endswith(("+json", "+xml")):
        return "text"
    return None


def decode_body(data: bytes, content_type: str | None) -> str:
    match = _CHARSET_RE.search(content_type or "")
    charset = match.group(1).strip().lower() if match else "utf-8"
    try:
        return data.decode(charset, errors="replace")
    except LookupError as exc:
        raise ToolError(f'unsupported charset "{charset}"') from exc


def _is_hidden(tag: Any) -> bool:
    attrs = getattr(tag, "attrs", None) or {}
    if "hidden" in attrs or str(attrs.get("aria-hidden", "")).lower() == "true":
        return True
    if tag.name == "input" and str(attrs.get("type", "")).lower() == "hidden":
        return True
    for declaration in str(attrs.get("style", "")).split(";"):
        name, separator, value = declaration.partition(":")
        if not separator:
            continue
        name = name.strip().lower()
        value = re.sub(r"\s*!important\s*$", "", value.strip().lower())
        if name == "display" and value == "none":
            return True
        if name == "visibility" and value in ("hidden", "collapse"):
            return True
    return False


def html_to_markdown(html: str) -> str:
    """Convert HTML to markdown, dropping scripts and hidden elements.

    Returns a fixed marker when the page cannot be converted safely.
    """
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(list(_REMOVED_TAGS)):
            tag.decompose()
        for tag in soup.find_all(True):
            if not getattr(tag, "decomposed", False) and _is_hidden(tag):
                tag.decompose()
        try:
            from markdownify import markdownify
        except Exception:  # noqa: BLE001 - optional converter; plain text is the fallback
            text = soup.get_text("\n")
        else:
            text = markdownify(str(soup), heading_style="ATX", bullets="-")
    except RecursionError:
        return WEB_FETCH_HTML_OMITTED
    except Exception:  # noqa: BLE001 - a page we cannot parse is reported, not raised
        return WEB_FETCH_HTML_OMITTED
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def format_fetch_output(
    url: str,
    status: int,
    body: str,
    truncated: bool,
    max_output_chars: int = MAX_OUTPUT_CHARS,
) -> str:
    header = f"Fetched {url} (HTTP {status})\n\n{EXTERNAL_WEB_CONTENT_NOTICE}\n\n"
    prefix = f"{header}{body}"
    truncated = truncated or len(prefix) > max_output_chars
    full = f"{prefix}{WEB_FETCH_TRUNCATION_FOOTER if truncated else ''}"
    if len(full) <= max_output_chars:
        return full
    keep = max_output_chars - len(WEB_FETCH_TRUNCATION_FOOTER)
    return f"{prefix[:keep]}{WEB_FETCH_TRUNCATION_FOOTER}"


@dataclass(frozen=True, slots=True)
class FetchedPage:
    url: str
    status: int
    kind: str
    content: str
    truncated: bool


async def _request_once(client: Any, parts: SplitResult, proxied: bool) -> Any:
    """Open one GET without following redirects; returns the streaming response."""
    import httpx

    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    headers = {"accept": _ACCEPT}
    extensions: dict[str, Any] = {}
    url = httpx.URL(parts.geturl())
    if not proxied:
        address = await resolve_public(host, port)
        # Connect to the address that was checked; the name stays in Host and SNI.
        url = url.copy_with(host=address)
        headers["host"] = parts.netloc.rsplit("@", 1)[-1]
        if parts.scheme == "https":
            extensions["sni_hostname"] = host
    request = client.build_request("GET", url, headers=headers, extensions=extensions)
    return await client.send(request, stream=True)


async def fetch_page(raw_url: str) -> FetchedPage:
    """Fetch one URL, following same-origin redirects only."""
    import httpx

    from utils.http_client import ensure_user_agent

    current = parse_fetch_url(raw_url)
    check_host_name(current.hostname or "")
    # Redirects are followed within one origin only, so this holds for all of them.
    proxy = select_proxy(current)
    proxied = proxy is not None
    timeout = httpx.Timeout(FETCH_TIMEOUT_SECONDS)
    try:
        client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            # Routing was decided above and is passed in explicitly.
            trust_env=False,
            proxy=proxy,
            # The certificate settings of the environment (SSL_CERT_FILE and
            # the like) apply on either route.
            verify=httpx.create_ssl_context(trust_env=True),
            headers=ensure_user_agent(None),
        )
    except (ImportError, ValueError) as exc:
        raise ToolError(f"the configured proxy cannot be used: {exc}") from exc
    async with client:
        redirects = 0
        while True:
            try:
                response = await _request_once(client, current, proxied)
            except httpx.HTTPError as exc:
                raise ToolError(f"request to {current.hostname} failed: {exc or type(exc).__name__}") from exc
            try:
                if response.status_code in _REDIRECT_STATUSES:
                    if redirects >= MAX_REDIRECTS:
                        raise ToolError(f"exceeded the maximum of {MAX_REDIRECTS} redirects")
                    location = response.headers.get("location")
                    if not location:
                        raise ToolError(
                            f"redirect response (HTTP {response.status_code}) without a Location header"
                        )
                    target = parse_fetch_url(urljoin(current.geturl(), location))
                    if not same_origin(current, target):
                        origin = f"{target.scheme}://{target.netloc}"
                        raise ToolError(
                            f"cross-origin redirect to {origin} is not followed automatically; "
                            "retry against that URL directly"
                        )
                    check_host_name(target.hostname or "")
                    current = target
                    redirects += 1
                    continue
                content_type = response.headers.get("content-type")
                kind = classify_content_type(content_type)
                if kind is None:
                    raise ToolError(f'unsupported content type "{content_type or "unknown"}"')
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
                    raise ToolError(f"response exceeds the maximum of {MAX_RESPONSE_BYTES} bytes")
                collected = bytearray()
                cut = False
                try:
                    async for chunk in response.aiter_bytes():
                        collected += chunk
                        if len(collected) > MAX_RESPONSE_BYTES:
                            del collected[MAX_RESPONSE_BYTES:]
                            cut = True
                            break
                except httpx.HTTPError as exc:
                    raise ToolError(
                        f"reading the response from {current.hostname} failed: {exc or type(exc).__name__}"
                    ) from exc
                decoded = decode_body(bytes(collected), content_type)
                too_long = len(decoded) > MAX_BODY_CHARS
                return FetchedPage(
                    url=current.geturl(),
                    status=response.status_code,
                    kind=kind,
                    content=decoded[:MAX_BODY_CHARS],
                    truncated=cut or too_long,
                )
            finally:
                await response.aclose()


async def web_fetch(url: str) -> str:
    """Fetch ``url`` and render it for the model."""
    try:
        page = await asyncio.wait_for(fetch_page(url), FETCH_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise ToolError(
            f"web_fetch timed out after {int(FETCH_TIMEOUT_SECONDS * 1000)}ms"
        ) from exc
    body = page.content
    if page.kind == "html":
        body = await asyncio.to_thread(html_to_markdown, body)
    return format_fetch_output(page.url, page.status, body, page.truncated)


# ---------------------------------------------------------------------------
# web_search rendering
# ---------------------------------------------------------------------------


def _clean_queries(raw: list[Any]) -> list[str]:
    if not raw:
        raise ToolError("queries must contain at least one query")
    if len(raw) > SEARCH_MAX_QUERIES:
        raise ToolError(f"queries must contain at most {SEARCH_MAX_QUERIES} queries")
    queries: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ToolError("each query must be a non-empty string")
        if item not in queries:
            queries.append(item)
    return queries


def _excerpt(source: dict[str, Any]) -> str:
    text = str(source.get("snippet") or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > SEARCH_SUMMARY_MAX_CHARS:
        text = f"{text[: SEARCH_SUMMARY_MAX_CHARS - 1]}…"
    return text


def _source_label(source: dict[str, Any]) -> str:
    title = str(source.get("title") or "")
    if title:
        return title
    url = str(source.get("url") or "")
    try:
        return urlsplit(url).hostname or url
    except ValueError:
        return url


def format_search_output(queries: list[str], results: list[dict[str, Any]]) -> str:
    """Merge per-query results and render them as dsh ``web_search`` does."""
    per_query = [
        [source for source in (result.get("sources") or []) if isinstance(source, dict) and source.get("url")]
        for result in results
    ]
    seen: set[str] = set()
    sources: list[dict[str, Any]] = []
    dropped = False
    for rank in range(max((len(items) for items in per_query), default=0)):
        for items in per_query:
            if rank >= len(items):
                continue
            source = items[rank]
            url = str(source["url"])
            if url in seen:
                continue
            seen.add(url)
            if len(sources) == SEARCH_MAX_RESULTS:
                dropped = True
                break
            sources.append(source)
        if dropped:
            break
    answers = [
        (queries[index] if index < len(queries) else "", str(result.get("answer") or ""))
        for index, result in enumerate(results)
    ]
    answers = [(query, answer) for query, answer in answers if answer]
    if len(queries) == 1:
        content = answers[0][1] if answers else ""
    else:
        content = "\n\n".join(f"### {query}\n\n{answer}" for query, answer in answers)

    parts = [EXTERNAL_WEB_CONTENT_NOTICE]
    if content:
        parts.append(content)
    if sources:
        lines = []
        for source in sources:
            meta = []
            excerpt = _excerpt(source)
            if excerpt:
                meta.append(excerpt)
            published = str(source.get("published_at") or "")
            if published:
                meta.append(f"({published})")
            suffix = f" — {' '.join(meta)}" if meta else ""
            lines.append(f"- [{_source_label(source)}]({source['url']}){suffix}")
        parts.append("Sources:\n" + "\n".join(lines))
    elif not content:
        parts.append("No results found.")
    if dropped:
        parts.append(f"(Showing the first {len(sources)} sources. Refine the query for more.)")
    parts.append("Cite the relevant URLs above as markdown links in your answer.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Tool wiring
# ---------------------------------------------------------------------------


def build_web_tools(ctx: ToolContext, schemas: dict[str, dict[str, Any]]) -> list[AgentTool]:
    async def fetch(args: dict[str, Any], call: ToolCallInfo) -> str:
        url = str(args["url"])
        if not url.strip():
            raise ToolError("url must be a non-empty string")
        return await run_cancellable(web_fetch(url), call)

    tools = [define_tool(schemas["web_fetch"], fetch, parallel=True)]

    if "web_search" in ctx.host.capabilities:

        async def search(args: dict[str, Any], call: ToolCallInfo) -> str:
            queries = _clean_queries(list(args["queries"]))
            reply = await run_cancellable(
                ctx.host.call(ctx.session_id, "web_search", {"queries": queries}), call
            )
            results = reply.get("results")
            if not isinstance(results, list):
                raise ToolError("the search backend returned no results")
            return format_search_output(
                queries, [item for item in results if isinstance(item, dict)]
            )

        tools.append(define_tool(schemas["web_search"], search, parallel=True))
    return tools
