# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Read-only Xiaoheihe feed source."""

from __future__ import annotations

import asyncio
from typing import Any

from utils.cookies_login import load_cookies_from_file
from utils.http.external_client import get_external_http_client
from utils.xhh_client import build_xhh_cookie_header, build_xhh_request_params


_XHH_API_BASE = "https://api.xiaoheihe.cn"
_XHH_FEEDS_PATH = "/bbs/app/feeds"
_XHH_WEB_LINK = "https://www.xiaoheihe.cn/app/bbs/link/{link_id}"
_XHH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


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


__all__ = ["fetch_xhh_feed_content", "format_xhh_feed", "normalize_xhh_feed"]
