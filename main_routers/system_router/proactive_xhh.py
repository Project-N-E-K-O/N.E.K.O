# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Read-only Xiaoheihe feed source for proactive chat.

The request shape and hkey algorithm follow the public Openxhh implementation.
This module deliberately contains no login, comment publishing, or plugin
dependency: proactive chat only reads the public community feed.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
from typing import Any

from utils.cookies_login import load_cookies_from_file
from utils.http.external_client import get_external_http_client


_XHH_API_BASE = "https://api.xiaoheihe.cn"
_XHH_FEEDS_PATH = "/bbs/app/feeds"
_XHH_WEB_LINK = "https://www.xiaoheihe.cn/app/bbs/link/{link_id}"
_XHH_SIGNING_KEY = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"
_XHH_TOKEN_PHRASES = ("唉？！云朵！", "哒哒哒哒哒，好想玩原神", "云！原！神！")
_XHH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


def _vm(num: int) -> int:
    return (255 & ((num << 1) ^ 27)) if num & 128 else num << 1


def _qm(num: int) -> int:
    return _vm(num) ^ num


def _mm(num: int) -> int:
    return _qm(_vm(num))


def _ym(num: int) -> int:
    return _mm(_qm(_vm(num)))


def _gm(num: int) -> int:
    return _ym(num) ^ _mm(num) ^ _qm(num)


def _mixed(values: list[int]) -> list[int]:
    return [
        _gm(values[0]) ^ _ym(values[1]) ^ _mm(values[2]) ^ _qm(values[3]),
        _qm(values[0]) ^ _gm(values[1]) ^ _ym(values[2]) ^ _mm(values[3]),
        _mm(values[0]) ^ _qm(values[1]) ^ _gm(values[2]) ^ _ym(values[3]),
        _ym(values[0]) ^ _mm(values[1]) ^ _qm(values[2]) ^ _gm(values[3]),
        values[4],
        values[5],
    ]


def _av(value: str, key: str, n: int) -> str:
    pool = key[: len(key) + n]
    return "".join(pool[ord(char) % len(pool)] for char in value)


def _sv(value: str, key: str) -> str:
    return "".join(key[ord(char) % len(key)] for char in value)


def _interleave(values: list[str]) -> str:
    output: list[str] = []
    for index in range(len(values[2])):
        for value in values:
            if index < len(value):
                output.append(value[index])
    return "".join(output)


def build_xhh_request_keys(
    path: str,
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> tuple[str, str, int]:
    """Build Xiaoheihe's hkey, nonce and request timestamp."""
    request_time = int(timestamp or time.time())
    request_nonce = nonce or hashlib.md5(
        f"{request_time}{secrets.randbelow(max(2, int(time.time() * 1000)))}".encode()
    ).hexdigest().upper()
    values = [
        _av(str(request_time), _XHH_SIGNING_KEY, -2),
        _sv(path, _XHH_SIGNING_KEY),
        _sv(request_nonce, _XHH_SIGNING_KEY),
    ]
    values.sort(key=len)
    digest = hashlib.md5(_interleave(values).encode()[:20]).hexdigest()
    checksum = sum(_mixed([ord(char) for char in digest[-6:]])) % 100
    return f"{_av(digest[:5], _XHH_SIGNING_KEY, -4)}{checksum:02d}", request_nonce, request_time


def build_xhh_token_id(*, timestamp: int | None = None) -> str:
    """Build the short-lived browser token used by Xiaoheihe requests."""
    current = int(timestamp or time.time())
    raw = bytearray(hashlib.md5(str(current).encode()).digest())
    for phrase in _XHH_TOKEN_PHRASES:
        raw.extend(hashlib.md5(phrase.encode()).digest())
    raw.append(0)
    return base64.b64encode(bytes(raw)).decode("ascii")


def build_xhh_request_params(
    path: str,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hkey, nonce, request_time = build_xhh_request_keys(path)
    params: dict[str, Any] = dict(extra or {})
    params.update(
        {
            "os_type": "web",
            "app": "web",
            "client_type": "web",
            "version": "999.0.4",
            "web_version": "2.5",
            "x_client_type": "web",
            "x_app": "heybox_website",
            "x_os_type": "Windows",
            "device_info": "Chrome",
            "hkey": hkey,
            "_time": str(request_time),
            "nonce": nonce,
            "_notip": "true",
        }
    )
    return params


def build_xhh_cookie_header(cookies: dict[str, str]) -> str:
    normalized = {
        str(key).strip(): str(value).strip()
        for key, value in (cookies or {}).items()
        if str(key).strip() and str(value).strip()
    }
    normalized["x_xhh_tokenid"] = build_xhh_token_id()
    return "; ".join(f"{key}={value}" for key, value in normalized.items())


def _plain_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _label_values(items: Any) -> list[str]:
    labels: list[str] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            value = item.get("name") or item.get("title") or item.get("text")
        else:
            value = item
        normalized = _plain_text(value)
        if normalized and normalized not in labels:
            labels.append(normalized)
    return labels


def normalize_xhh_feed(payload: dict[str, Any], *, limit: int = 10) -> list[dict[str, Any]]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    raw_links = result.get("links") if isinstance(result.get("links"), list) else []
    posts: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for raw in raw_links:
        if not isinstance(raw, dict):
            continue
        try:
            link_id = int(raw.get("linkid") or raw.get("link_id") or 0)
        except (TypeError, ValueError):
            continue
        title = _plain_text(raw.get("title"))
        if link_id <= 0 or not title or link_id in seen_ids:
            continue
        seen_ids.add(link_id)
        user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
        posts.append(
            {
                "link_id": link_id,
                "title": title,
                "description": _plain_text(raw.get("description")),
                "author": _plain_text(user.get("username")),
                "topics": _label_values(raw.get("topics")),
                "tags": _label_values(raw.get("hashtags")),
                "url": _XHH_WEB_LINK.format(link_id=link_id),
                "create_at": raw.get("create_at"),
            }
        )
        if len(posts) >= max(1, int(limit)):
            break
    return posts


def format_xhh_feed(posts: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, post in enumerate(posts, start=1):
        details: list[str] = []
        if post.get("author"):
            details.append(f"作者: {post['author']}")
        labels = [*post.get("topics", []), *post.get("tags", [])]
        if labels:
            details.append("话题: " + "、".join(labels[:5]))
        description = _plain_text(post.get("description"))
        suffix = f"（{'；'.join(details)}）" if details else ""
        line = f"{index}. {post['title']}{suffix}"
        if description:
            line += f"\n   {description[:300]}"
        lines.append(line)
    return "\n".join(lines)


async def fetch_xhh_feed_content(limit: int = 10) -> dict[str, Any]:
    """Fetch the feed, optionally using credentials saved in the credential center."""
    try:
        cookies = await asyncio.to_thread(load_cookies_from_file, "xhh")
        headers = {
            "Referer": "https://www.xiaoheihe.cn/",
            "User-Agent": _XHH_USER_AGENT,
        }
        if cookies:
            headers["Cookie"] = build_xhh_cookie_header(cookies)
        response = await get_external_http_client().get(
            f"{_XHH_API_BASE}{_XHH_FEEDS_PATH}",
            params=build_xhh_request_params(_XHH_FEEDS_PATH, extra={"pull": "1"}),
            headers=headers,
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("响应不是 JSON 对象")
        status = str(payload.get("status") or payload.get("stat") or "ok").lower()
        if status not in {"ok", "success"}:
            raise ValueError(str(payload.get("msg") or payload.get("message") or status))
        posts = normalize_xhh_feed(payload, limit=limit)
        if not posts:
            return {"success": False, "error": "小黑盒 feeds 未返回可用帖子", "posts": []}
        return {
            "success": True,
            "posts": posts,
            "formatted_content": format_xhh_feed(posts),
        }
    except Exception as exc:
        return {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
            "posts": [],
        }


__all__ = [
    "build_xhh_cookie_header",
    "build_xhh_request_keys",
    "build_xhh_request_params",
    "build_xhh_token_id",
    "fetch_xhh_feed_content",
    "format_xhh_feed",
    "normalize_xhh_feed",
]
