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
"""YouTube Home Feed fetcher backed by the web Innertube API.

This mirrors the small part of youtubei.js that N.E.K.O. needs: bootstrap the
web client configuration from youtube.com, then browse ``FEwhat_to_watch``.
Stored YouTube cookies are optional.  When SAPISID-compatible credentials are
present the request carries SAPISIDHASH and returns the signed-in home feed;
otherwise YouTube's anonymous home feed is used, with public discovery as a
fallback when YouTube intentionally leaves the signed-out Home tab empty.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, Iterator

import httpx

from utils.external_http_client import get_external_http_client
from utils.language_utils import get_global_language_full

from ._shared import get_random_user_agent, logger
from .platform_helpers import _get_platform_cookies


_YOUTUBE_ORIGIN = "https://www.youtube.com"
_HOME_BROWSE_ID = "FEwhat_to_watch"
_DEFAULT_CLIENT_NAME = "1"  # WEB
_DEFAULT_CLIENT_VERSION = "2.20250710.01.00"
_AUTH_HEADER_NAMES = {"Authorization", "X-Goog-AuthUser", "X-Origin"}
_LANGUAGE_SETTINGS = {
    "zh": ("zh-CN,zh;q=0.9,en;q=0.8", "热门视频"),
    "zh-TW": ("zh-TW,zh;q=0.9,en;q=0.8", "熱門影片"),
    "en": ("en-US,en;q=0.9", "trending videos"),
    "ja": ("ja-JP,ja;q=0.9,en;q=0.8", "話題の動画"),
    "ko": ("ko-KR,ko;q=0.9,en;q=0.8", "인기 동영상"),
    "ru": ("ru-RU,ru;q=0.9,en;q=0.8", "популярные видео"),
    "es": ("es-ES,es;q=0.9,en;q=0.8", "videos populares"),
    "pt": ("pt-BR,pt;q=0.9,en;q=0.8", "vídeos em alta"),
}


def _format_fetch_error(exc: Exception) -> str:
    """Return a useful error even when httpx exceptions have an empty message."""
    error_name = type(exc).__name__
    detail = str(exc).strip()
    if detail:
        return f"{error_name}: {detail}"
    if isinstance(exc, httpx.TimeoutException):
        return (
            f"{error_name}: 连接 YouTube 超时，请检查 HTTPS_PROXY/ALL_PROXY "
            "或桌面端系统代理设置"
        )
    if isinstance(exc, httpx.ConnectError):
        return f"{error_name}: 无法连接 YouTube，请检查网络或代理设置"
    return error_name


def _extract_ytcfg(html: str) -> dict[str, Any]:
    """Merge JSON objects passed to ``ytcfg.set(...)`` in the bootstrap page."""
    config: dict[str, Any] = {}
    decoder = json.JSONDecoder()
    marker = "ytcfg.set("
    cursor = 0

    while True:
        marker_index = html.find(marker, cursor)
        if marker_index < 0:
            break
        value_index = marker_index + len(marker)
        while value_index < len(html) and html[value_index].isspace():
            value_index += 1
        try:
            value, consumed = decoder.raw_decode(html[value_index:])
            if isinstance(value, dict):
                config.update(value)
            cursor = value_index + consumed
        except (json.JSONDecodeError, ValueError):
            cursor = value_index + 1

    return config


def _text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    simple = value.get("simpleText")
    if isinstance(simple, str):
        return simple.strip()
    runs = value.get("runs")
    if isinstance(runs, list):
        return "".join(
            run.get("text", "") for run in runs if isinstance(run, dict)
        ).strip()
    content = value.get("content")
    return content.strip() if isinstance(content, str) else ""


def _thumbnail_sources(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    sources = value.get("thumbnails") or value.get("sources")
    if isinstance(sources, list):
        return [source for source in sources if isinstance(source, dict) and source.get("url")]
    for child in value.values():
        found = _thumbnail_sources(child)
        if found:
            return found
    return []


def _walk_renderers(value: Any) -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {
                "videoRenderer",
                "gridVideoRenderer",
                "compactVideoRenderer",
                "reelItemRenderer",
                "lockupViewModel",
            } and isinstance(child, dict):
                yield key, child
            yield from _walk_renderers(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_renderers(child)


def _parse_classic_renderer(renderer: dict[str, Any], *, is_short: bool = False) -> dict[str, Any] | None:
    video_id = renderer.get("videoId")
    if not isinstance(video_id, str) or not video_id:
        return None

    title = _text(renderer.get("title")) or _text(renderer.get("headline"))
    if not title:
        return None

    thumbnails = _thumbnail_sources(renderer.get("thumbnail", {}))
    return {
        "video_id": video_id,
        "title": title,
        "author": _text(renderer.get("ownerText")) or _text(renderer.get("longBylineText")),
        "url": f"{_YOUTUBE_ORIGIN}/watch?v={video_id}",
        "duration": _text(renderer.get("lengthText")),
        "view_count": _text(renderer.get("viewCountText")) or _text(renderer.get("shortViewCountText")),
        "published_text": _text(renderer.get("publishedTimeText")),
        "thumbnail": thumbnails[-1].get("url", "") if thumbnails else "",
        "thumbnails": thumbnails,
        "rcmd_reason": _text(renderer.get("reelWatchEndpoint", {}).get("overlay", {})),
        "source": "YouTube",
        "is_short": is_short,
    }


def _parse_lockup_view_model(renderer: dict[str, Any]) -> dict[str, Any] | None:
    """Parse the newer Polymer lockup representation used by YouTube Home."""
    video_id = renderer.get("contentId")
    content_type = renderer.get("contentType", "")
    if not isinstance(video_id, str) or not video_id or "VIDEO" not in str(content_type):
        return None

    metadata = renderer.get("metadata", {}).get("lockupMetadataViewModel", {})
    title = _text(metadata.get("title"))
    if not title:
        return None

    metadata_texts: list[str] = []
    rows = metadata.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
    for row in rows if isinstance(rows, list) else []:
        parts = row.get("metadataParts", []) if isinstance(row, dict) else []
        for part in parts if isinstance(parts, list) else []:
            part_text = _text(part.get("text", {})) if isinstance(part, dict) else ""
            if part_text:
                metadata_texts.append(part_text)

    thumbnails = _thumbnail_sources(renderer.get("contentImage", {}))
    return {
        "video_id": video_id,
        "title": title,
        "author": metadata_texts[0] if metadata_texts else "",
        "url": f"{_YOUTUBE_ORIGIN}/watch?v={video_id}",
        "duration": "",
        "view_count": metadata_texts[1] if len(metadata_texts) > 1 else "",
        "published_text": metadata_texts[2] if len(metadata_texts) > 2 else "",
        "thumbnail": thumbnails[-1].get("url", "") if thumbnails else "",
        "thumbnails": thumbnails,
        "rcmd_reason": "",
        "source": "YouTube",
        "is_short": False,
    }


def _extract_videos(payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    videos: list[dict[str, Any]] = []
    seen: set[str] = set()

    for renderer_name, renderer in _walk_renderers(payload):
        if renderer_name == "lockupViewModel":
            video = _parse_lockup_view_model(renderer)
        else:
            video = _parse_classic_renderer(renderer, is_short=renderer_name == "reelItemRenderer")
        if not video or video["video_id"] in seen:
            continue
        seen.add(video["video_id"])
        videos.append(video)
        if len(videos) >= limit:
            break

    return videos


def _build_sapisid_authorization(cookies: dict[str, str], now: int | None = None) -> str:
    sapisid = cookies.get("SAPISID") or ""
    if not sapisid:
        return ""
    timestamp = int(time.time()) if now is None else now
    digest = hashlib.sha1(f"{timestamp} {sapisid} {_YOUTUBE_ORIGIN}".encode("utf-8")).hexdigest()
    return f"SAPISIDHASH {timestamp}_{digest}"


def _cookie_header(cookies: dict[str, str]) -> str:
    values = dict(cookies)
    values.setdefault("CONSENT", "YES+cb.20210328-17-p0.en+FX+")
    return "; ".join(f"{key}={value}" for key, value in values.items())


def _request_language_settings() -> tuple[str, str]:
    """Return the YouTube request locale and localized discovery query."""
    language = get_global_language_full()
    if language == "zh-TW":
        return _LANGUAGE_SETTINGS["zh-TW"]
    short_language = language.split("-", 1)[0]
    return _LANGUAGE_SETTINGS.get(short_language, _LANGUAGE_SETTINGS["en"])


def _without_authentication(headers: dict[str, str]) -> dict[str, str]:
    """Build headers for a genuinely anonymous retry or discovery request."""
    anonymous_headers = {
        key: value for key, value in headers.items() if key not in _AUTH_HEADER_NAMES
    }
    anonymous_headers["Cookie"] = _cookie_header({})
    return anonymous_headers


def _youtube_confirms_authentication(
    payload: dict[str, Any],
    bootstrap_config: dict[str, Any],
    *,
    auth_sent: bool,
) -> bool:
    """Return true only when YouTube explicitly reports a signed-in session."""
    if not auth_sent:
        return False

    response_context = payload.get("responseContext", {})
    if isinstance(response_context, dict):
        web_context = response_context.get("mainAppWebResponseContext", {})
        if isinstance(web_context, dict):
            logged_out = web_context.get("loggedOut")
            if isinstance(logged_out, bool):
                return not logged_out

    logged_in = bootstrap_config.get("LOGGED_IN")
    return logged_in if isinstance(logged_in, bool) else False


async def fetch_youtube_home_feed(limit: int = 30) -> dict[str, Any]:
    """Fetch YouTube's anonymous or signed-in homepage recommendations."""
    client: httpx.AsyncClient | None = None
    owns_client = False
    try:
        cookies = await asyncio.to_thread(_get_platform_cookies, "youtube")
        logger.info(
            "YouTube 凭证状态: cookie_count=%d, sapisid_present=%s",
            len(cookies),
            bool(cookies.get("SAPISID")),
        )
        cookie_header = _cookie_header(cookies)
        user_agent = get_random_user_agent()
        accept_language, discovery_query = _request_language_settings()

        # Credentialed requests must not use the process-wide shared client:
        # httpx persists response Set-Cookie values in the client jar, which
        # would keep deleted or changed account state alive across later calls.
        if cookies:
            client = httpx.AsyncClient(
                timeout=15.0,
                trust_env=True,
                follow_redirects=True,
            )
            owns_client = True
        else:
            client = get_external_http_client()

        bootstrap_headers = {
            "Accept-Language": accept_language,
            "Cookie": cookie_header,
            "User-Agent": user_agent,
        }
        bootstrap = await client.get(_YOUTUBE_ORIGIN, headers=bootstrap_headers, timeout=12.0)
        bootstrap.raise_for_status()
        config = _extract_ytcfg(bootstrap.text)

        api_key = config.get("INNERTUBE_API_KEY")
        context = config.get("INNERTUBE_CONTEXT")
        if not isinstance(api_key, str) or not api_key or not isinstance(context, dict):
            raise ValueError("YouTube bootstrap page did not expose Innertube configuration")

        client_config = context.get("client", {}) if isinstance(context.get("client"), dict) else {}
        client_name = str(config.get("INNERTUBE_CONTEXT_CLIENT_NAME") or _DEFAULT_CLIENT_NAME)
        client_version = str(client_config.get("clientVersion") or _DEFAULT_CLIENT_VERSION)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Cookie": cookie_header,
            "Origin": _YOUTUBE_ORIGIN,
            "User-Agent": user_agent,
            "X-YouTube-Client-Name": client_name,
            "X-YouTube-Client-Version": client_version,
        }
        visitor_data = client_config.get("visitorData") or config.get("VISITOR_DATA")
        if visitor_data:
            headers["X-Goog-Visitor-Id"] = str(visitor_data)

        authorization = _build_sapisid_authorization(cookies)
        auth_requested = bool(authorization)
        if authorization:
            headers.update({
                "Authorization": authorization,
                "X-Goog-AuthUser": "0",
                "X-Origin": _YOUTUBE_ORIGIN,
            })

        browse_url = f"{_YOUTUBE_ORIGIN}/youtubei/v1/browse"
        browse_params = {"prettyPrint": "false", "key": api_key}
        browse_payload = {"context": context, "browseId": _HOME_BROWSE_ID}
        response = await client.post(
            browse_url,
            params=browse_params,
            headers=headers,
            json=browse_payload,
            timeout=15.0,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if not authorization or status_code not in {401, 403}:
                raise
            logger.warning(
                "YouTube 登录认证被拒绝(status=%d)，重试匿名首页 Feed",
                status_code,
            )
            client.cookies.clear()
            headers = _without_authentication(headers)
            authorization = ""
            response = await client.post(
                browse_url,
                params=browse_params,
                headers=headers,
                json=browse_payload,
                timeout=15.0,
            )
            response.raise_for_status()
        browse_data = response.json()
        authenticated = _youtube_confirms_authentication(
            browse_data,
            config,
            auth_sent=bool(authorization),
        )
        videos = _extract_videos(browse_data, max(1, limit))
        feed_kind = "home"

        # YouTube currently returns an intentionally empty Home tab for some
        # signed-out users (and for accounts with watch history disabled).  A
        # public search keeps the non-login video source useful while preserving
        # the real Home Feed whenever YouTube supplies one.
        if not videos:
            logger.warning(
                "YouTube Home Feed 未返回可解析视频，切换匿名 public_discovery: "
                "auth_requested=%s, auth_confirmed=%s",
                auth_requested,
                authenticated,
            )
            if authorization:
                client.cookies.clear()
                headers = _without_authentication(headers)
                authorization = ""
            authenticated = False
            discovery_response = await client.post(
                f"{_YOUTUBE_ORIGIN}/youtubei/v1/search",
                params={"prettyPrint": "false", "key": api_key},
                headers=headers,
                json={"context": context, "query": discovery_query},
                timeout=15.0,
            )
            discovery_response.raise_for_status()
            videos = _extract_videos(discovery_response.json(), max(1, limit))
            feed_kind = "public_discovery"

        if not videos:
            raise ValueError("YouTube returned no Home Feed or public discovery videos")

        logger.info(
            "YouTube Feed 获取成功: feed_kind=%s, auth_requested=%s, "
            "auth_confirmed=%s, videos=%d",
            feed_kind,
            auth_requested,
            authenticated,
            len(videos),
        )

        return {
            "success": True,
            "source": "youtube",
            "feed_kind": feed_kind,
            "authenticated": authenticated,
            "videos": videos,
        }
    except Exception as exc:
        error = _format_fetch_error(exc)
        logger.warning(f"获取 YouTube 首页 Feed 失败: {error}")
        return {
            "success": False,
            "source": "youtube",
            "feed_kind": "unavailable",
            "authenticated": False,
            "videos": [],
            "error": error,
        }
    finally:
        if owns_client and client is not None:
            try:
                await client.aclose()
            except Exception:
                logger.debug("关闭 YouTube 独立 HTTP 客户端失败", exc_info=True)
