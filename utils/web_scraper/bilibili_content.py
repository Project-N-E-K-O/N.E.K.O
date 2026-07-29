# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Bilibili content radar and selected-video enrichment.

The list endpoints intentionally stay cheap: native Bilibili ordering is kept
and only the one video selected by proactive Phase 1 is enriched with details
and, when available, subtitles.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import re
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from utils.external_http_client import get_external_http_client
from utils.logger_config import get_module_logger

from .platform_helpers import (
    _bilibili_account_cache_key,
    _get_bilibili_credential,
)


logger = get_module_logger(__name__, "Main")

_HOME_TTL_SECONDS = 5 * 60
_HOT_TTL_SECONDS = 10 * 60
_STALE_TTL_SECONDS = 30 * 60
_ENRICHMENT_TTL_SECONDS = 24 * 60 * 60
_SUBTITLE_MAX_CHARS = 8_000
_SUMMARY_MAX_CHARS = 120
_PREEMPT_POLL_SECONDS = 0.1

_RESULT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_ENRICHMENT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_LOCKS: dict[str, asyncio.Lock] = {}


class BilibiliEnrichmentPreempted(Exception):
    """Raised when a user message supersedes selected-video enrichment."""


def _raise_if_preempted(is_preempted: Callable[[], bool] | None) -> None:
    if is_preempted is not None and is_preempted():
        raise BilibiliEnrichmentPreempted


async def _await_with_preemption(
    awaitable: Awaitable[Any],
    *,
    is_preempted: Callable[[], bool] | None,
) -> Any:
    """Await one operation while allowing proactive-chat preemption."""

    if is_preempted is None:
        return await awaitable

    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            _raise_if_preempted(is_preempted)
            done, _pending = await asyncio.wait(
                {task}, timeout=_PREEMPT_POLL_SECONDS
            )
            if task in done:
                result = task.result()
                _raise_if_preempted(is_preempted)
                return result
    except (BilibiliEnrichmentPreempted, asyncio.CancelledError):
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise


async def _acquire_lock_with_preemption(
    lock: asyncio.Lock,
    *,
    is_preempted: Callable[[], bool] | None,
) -> None:
    """Acquire a cache lock without leaking ownership on preemption."""

    if is_preempted is None:
        await lock.acquire()
        return
    while True:
        _raise_if_preempted(is_preempted)
        try:
            await asyncio.wait_for(
                lock.acquire(), timeout=_PREEMPT_POLL_SECONDS
            )
        except asyncio.TimeoutError:
            continue
        try:
            _raise_if_preempted(is_preempted)
        except BilibiliEnrichmentPreempted:
            lock.release()
            raise
        return


def _cache_lock(key: str) -> asyncio.Lock:
    lock = _CACHE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _CACHE_LOCKS[key] = lock
    return lock


async def _cached_result(
    key: str | None,
    *,
    ttl: float,
    fetcher: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    if key is None:
        try:
            result = await fetcher()
        except Exception as exc:  # Preserve endpoint isolation without caching.
            return {"success": False, "videos": [], "error": str(exc)}
        result.pop("_cache_key", None)
        return result

    now = time.monotonic()
    for cache_key, (stored_at, _payload) in list(_RESULT_CACHE.items()):
        if now - stored_at > _STALE_TTL_SECONDS:
            _RESULT_CACHE.pop(cache_key, None)
    cached = _RESULT_CACHE.get(key)
    if cached and now - cached[0] <= ttl:
        result = deepcopy(cached[1])
        result["cached"] = True
        return result

    async with _cache_lock(key):
        now = time.monotonic()
        cached = _RESULT_CACHE.get(key)
        if cached and now - cached[0] <= ttl:
            result = deepcopy(cached[1])
            result["cached"] = True
            return result

        try:
            result = await fetcher()
        except Exception as exc:  # Endpoint isolation is part of the contract.
            result = {"success": False, "videos": [], "error": str(exc)}

        if result.get("success"):
            store_key = str(result.pop("_cache_key", "") or key)
            stored = deepcopy(result)
            stored.pop("cached", None)
            stored.pop("stale", None)
            _RESULT_CACHE[store_key] = (now, stored)
            return result

        if cached and now - cached[0] <= _STALE_TTL_SECONDS:
            stale = deepcopy(cached[1])
            stale.update(
                {
                    "cached": True,
                    "stale": True,
                    "warning": result.get("error", "Bilibili request failed"),
                }
            )
            return stale
        return result


def _items_from_result(result: Any, *keys: str) -> list[dict[str, Any]]:
    current = result
    if isinstance(current, dict) and isinstance(current.get("data"), dict):
        current = current["data"]
    if not isinstance(current, dict):
        return []
    for key in keys:
        value = current.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _recommendation_reason(item: dict[str, Any]) -> str:
    reason = item.get("rcmd_reason")
    if isinstance(reason, dict):
        return str(reason.get("content") or "").strip()
    return str(reason or "").strip()


def _normalise_video(
    item: dict[str, Any],
    *,
    lane: str,
    rank: int,
    authenticated: bool,
) -> dict[str, Any] | None:
    bvid = str(item.get("bvid") or "").strip()
    title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
    if not bvid or not title:
        return None
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    author = str(owner.get("name") or item.get("author") or "").strip()
    description = re.sub(
        r"\s+", " ", str(item.get("desc") or item.get("description") or "")
    ).strip()
    native_reason = _recommendation_reason(item)
    if lane == "hot":
        reason = f"全站热门第{rank}名"
    else:
        reason = native_reason or "B站首页推荐"
    published_at = item.get("pubdate") or item.get("ctime") or 0
    try:
        published_at = int(published_at)
    except (TypeError, ValueError):
        published_at = 0
    return {
        "platform": "bilibili",
        "lane": lane,
        "kind": "video",
        "resource_id": bvid,
        "bvid": bvid,
        "title": title,
        "author": author,
        "url": f"https://www.bilibili.com/video/{bvid}",
        "reason": reason,
        "description_hint": description,
        "desc": description,
        "published_at": published_at,
        "native_rank": rank,
        "authenticated": authenticated,
        # Keep the historical fields consumed by formatters/logging.
        "view": (item.get("stat") or {}).get("view", 0),
        "like": (item.get("stat") or {}).get("like", 0),
        "id": item.get("id") or item.get("aid") or 0,
        "goto": item.get("goto", ""),
        "rcmd_reason": native_reason,
    }


async def fetch_bilibili_hot(limit: int = 10) -> dict[str, Any]:
    """Fetch Bilibili's public popular feed, preserving native rank."""

    normalized_limit = max(1, min(int(limit), 20))

    async def _fetch() -> dict[str, Any]:
        from bilibili_api import hot

        raw = await hot.get_hot_videos(pn=1, ps=20)
        videos: list[dict[str, Any]] = []
        for rank, item in enumerate(_items_from_result(raw, "list", "item"), 1):
            video = _normalise_video(
                item, lane="hot", rank=rank, authenticated=False
            )
            if video:
                videos.append(video)
            if len(videos) >= normalized_limit:
                break
        return {
            "success": bool(videos),
            "source": "bilibili_hot",
            "videos": videos,
            **({} if videos else {"error": "Bilibili热门接口没有返回可用视频"}),
        }

    return await _cached_result(
        f"bilibili_hot:{normalized_limit}",
        ttl=_HOT_TTL_SECONDS,
        fetcher=_fetch,
    )


async def fetch_bilibili_home(limit: int = 10) -> dict[str, Any]:
    """Fetch the account homepage feed, retrying anonymously on auth failure."""

    normalized_limit = max(1, min(int(limit), 20))
    credential = _get_bilibili_credential()
    authenticated = bool(credential)
    account_key = (
        _bilibili_account_cache_key(credential)
        if credential
        else "anonymous"
    )
    cache_key = (
        f"bilibili_home:{account_key}:{normalized_limit}"
        if account_key is not None
        else None
    )
    anonymous_cache_key = f"bilibili_home:anonymous:{normalized_limit}"

    async def _fetch() -> dict[str, Any]:
        from bilibili_api import homepage

        used_auth = authenticated
        auth_warning = ""
        try:
            raw = await homepage.get_videos(credential=credential)
        except Exception as exc:
            if not credential:
                raise
            auth_warning = str(exc)
            used_auth = False
            raw = await homepage.get_videos(credential=None)

        videos: list[dict[str, Any]] = []
        for rank, item in enumerate(_items_from_result(raw, "item", "list"), 1):
            video = _normalise_video(
                item, lane="home", rank=rank, authenticated=used_auth
            )
            if video:
                videos.append(video)
            if len(videos) >= normalized_limit:
                break
        result: dict[str, Any] = {
            "success": bool(videos),
            "source": "bilibili_home",
            "authenticated": used_auth,
            "videos": videos,
        }
        if auth_warning:
            result["warning"] = f"登录首页不可用，已使用匿名首页: {auth_warning}"
            result["_cache_key"] = anonymous_cache_key
        if not videos:
            result["error"] = "Bilibili首页没有返回可用视频"
        return result

    return await _cached_result(cache_key, ttl=_HOME_TTL_SECONDS, fetcher=_fetch)


async def fetch_bilibili_radar(limit: int = 10) -> dict[str, Any]:
    """Fetch homepage and hot videos concurrently and interleave both lanes."""

    normalized_limit = max(1, min(int(limit), 20))
    home_result, hot_result = await asyncio.gather(
        fetch_bilibili_home(normalized_limit),
        fetch_bilibili_hot(normalized_limit),
    )
    home_videos = list(home_result.get("videos") or [])
    hot_videos = list(hot_result.get("videos") or [])
    videos: list[dict[str, Any]] = []
    seen: set[str] = set()
    max_len = max(len(home_videos), len(hot_videos), 0)
    for index in range(max_len):
        # Home comes first so a duplicate keeps the more personal explanation.
        for group in (home_videos, hot_videos):
            if index >= len(group):
                continue
            item = group[index]
            key = str(item.get("bvid") or item.get("url") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            videos.append(item)
            if len(videos) >= normalized_limit:
                break
        if len(videos) >= normalized_limit:
            break

    success = bool(videos)
    errors = [
        str(result.get("error"))
        for result in (home_result, hot_result)
        if not result.get("success") and result.get("error")
    ]
    response: dict[str, Any] = {
        "success": success,
        "source": "bilibili_radar",
        "videos": videos,
        "home": home_result,
        "hot": hot_result,
    }
    if errors:
        response["warnings" if success else "error"] = errors if success else "; ".join(errors)
    return response


def _subtitle_score(entry: dict[str, Any], language: str) -> tuple[int, str]:
    code = str(entry.get("lan") or entry.get("lang") or "").lower()
    name = str(entry.get("lan_doc") or entry.get("lang_name") or "").lower()
    combined = f"{code} {name}"
    if any(mark in combined for mark in ("zh-cn", "zh-hans", "简体", "中文（简体）")):
        return (0, code)
    if any(mark in combined for mark in ("zh", "中文", "chinese")):
        return (1, code)
    locale_prefix = str(language or "").lower().split("-", 1)[0]
    if locale_prefix and (code.startswith(locale_prefix) or locale_prefix in name):
        return (2, code)
    return (3, code)


def _choose_subtitle(subtitle_data: Any, language: str) -> dict[str, Any] | None:
    if not isinstance(subtitle_data, dict):
        return None
    entries = subtitle_data.get("subtitles") or subtitle_data.get("list") or []
    entries = [entry for entry in entries if isinstance(entry, dict)]
    if not entries:
        return None
    return min(entries, key=lambda entry: _subtitle_score(entry, language))


async def _download_subtitle(entry: dict[str, Any]) -> str:
    url = str(entry.get("subtitle_url") or entry.get("url") or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
    except ValueError:
        return ""
    if parsed.scheme != "https" or not (
        hostname == "hdslb.com" or hostname.endswith(".hdslb.com")
    ):
        return ""
    response = await get_external_http_client().get(url, timeout=8.0)
    response.raise_for_status()
    payload = response.json()
    body = payload.get("body", []) if isinstance(payload, dict) else []
    lines: list[str] = []
    previous = ""
    for item in body:
        if not isinstance(item, dict):
            continue
        text = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
        if not text or text == previous:
            continue
        lines.append(text)
        previous = text
    return "\n".join(lines)[:_SUBTITLE_MAX_CHARS]


def _bounded_excerpt(text: str) -> str:
    """Return a literal, bounded excerpt without sending content to an LLM."""

    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(normalized) <= _SUMMARY_MAX_CHARS:
        return normalized
    prefix = normalized[:_SUMMARY_MAX_CHARS]
    boundary = max(prefix.rfind(mark) for mark in "。！？；.!?;")
    if boundary >= _SUMMARY_MAX_CHARS // 2:
        return prefix[: boundary + 1].strip()
    return prefix.rstrip()


def _extract_content_excerpt(description: str, transcript: str) -> tuple[str, str]:
    """Prefer the uploader's description, then a literal subtitle excerpt."""

    if description:
        return _bounded_excerpt(description), "metadata"
    if transcript:
        return _bounded_excerpt(transcript), "subtitle"
    return "", ""


async def enrich_bilibili_video(
    candidate: dict[str, Any],
    *,
    language: str = "zh",
    is_preempted: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Enrich one selected Bilibili video; failures retain the candidate."""

    enriched = dict(candidate)
    if (
        candidate.get("platform") != "bilibili"
        or candidate.get("kind") != "video"
        or not candidate.get("bvid")
    ):
        return enriched
    _raise_if_preempted(is_preempted)

    bvid = str(candidate["bvid"])
    now = time.monotonic()
    for cache_bvid, (stored_at, _payload) in list(_ENRICHMENT_CACHE.items()):
        if now - stored_at > _ENRICHMENT_TTL_SECONDS:
            _ENRICHMENT_CACHE.pop(cache_bvid, None)
    cached = _ENRICHMENT_CACHE.get(bvid)
    if cached and now - cached[0] <= _ENRICHMENT_TTL_SECONDS:
        _raise_if_preempted(is_preempted)
        enriched.update(deepcopy(cached[1]))
        enriched["summary_cached"] = True
        return enriched

    lock = _cache_lock(f"enrichment:{bvid}")
    await _acquire_lock_with_preemption(
        lock, is_preempted=is_preempted
    )
    try:
        _raise_if_preempted(is_preempted)
        cached = _ENRICHMENT_CACHE.get(bvid)
        if cached and time.monotonic() - cached[0] <= _ENRICHMENT_TTL_SECONDS:
            _raise_if_preempted(is_preempted)
            enriched.update(deepcopy(cached[1]))
            enriched["summary_cached"] = True
            return enriched

        try:
            from bilibili_api import video

            resource = video.Video(
                bvid=bvid,
                credential=_get_bilibili_credential(),
            )
            info = await _await_with_preemption(
                resource.get_info(), is_preempted=is_preempted
            )
            pages = info.get("pages") if isinstance(info, dict) else None
            if not isinstance(pages, list):
                pages = await _await_with_preemption(
                    resource.get_pages(), is_preempted=is_preempted
                )
            tags: list[dict[str, Any]] = []
            try:
                tags = await _await_with_preemption(
                    resource.get_tags(page_index=0),
                    is_preempted=is_preempted,
                )
            except BilibiliEnrichmentPreempted:
                raise
            except Exception as exc:
                logger.debug("Bilibili tags unavailable for %s: %s", bvid, exc)

            cid = 0
            if pages and isinstance(pages[0], dict):
                try:
                    cid = int(pages[0].get("cid") or 0)
                except (TypeError, ValueError):
                    cid = 0

            description = re.sub(r"\s+", " ", str(info.get("desc") or "")).strip()
            transcript = ""
            # A literal uploader description is already the preferred summary
            # source. Avoid a slower signed-subtitle request when it cannot
            # affect the resulting Phase 2 context.
            if cid and not description:
                try:
                    subtitle_data = await _await_with_preemption(
                        resource.get_subtitle(cid=cid),
                        is_preempted=is_preempted,
                    )
                    subtitle = _choose_subtitle(subtitle_data, language)
                    if subtitle:
                        transcript = await _await_with_preemption(
                            _download_subtitle(subtitle),
                            is_preempted=is_preempted,
                        )
                except BilibiliEnrichmentPreempted:
                    raise
                except Exception as exc:
                    logger.debug("Bilibili subtitles unavailable for %s: %s", bvid, exc)

            owner = info.get("owner") if isinstance(info.get("owner"), dict) else {}
            tag_names = [
                str(tag.get("tag_name") or "").strip()
                for tag in tags
                if isinstance(tag, dict) and tag.get("tag_name")
            ][:12]
            details = {
                "title": str(info.get("title") or candidate.get("title") or ""),
                "author": str(owner.get("name") or candidate.get("author") or ""),
                "description_hint": description or str(candidate.get("description_hint") or ""),
                "category": str(info.get("tname") or ""),
                "published_at": int(info.get("pubdate") or candidate.get("published_at") or 0),
                "duration": int(info.get("duration") or 0),
                "pages": pages or [],
                "tags": tag_names,
            }
            summary, basis = _extract_content_excerpt(
                details["description_hint"], transcript
            )

            details.update(
                {
                    "content_summary": summary,
                    "summary_basis": basis,
                    "enriched": True,
                }
            )
            _raise_if_preempted(is_preempted)
            _ENRICHMENT_CACHE[bvid] = (time.monotonic(), deepcopy(details))
            enriched.update(details)
        except BilibiliEnrichmentPreempted:
            raise
        except Exception as exc:
            logger.warning("Bilibili video enrichment failed for %s: %s", bvid, exc)
            enriched["enrichment_error"] = str(exc)
            fallback_description = re.sub(
                r"\s+", " ", str(candidate.get("description_hint") or "")
            ).strip()
            if fallback_description:
                enriched.update(
                    {
                        "content_summary": fallback_description[:_SUMMARY_MAX_CHARS],
                        "summary_basis": "metadata",
                    }
                )
        _raise_if_preempted(is_preempted)
        return enriched
    finally:
        lock.release()


def format_bilibili_phase2_context(candidate: dict[str, Any]) -> str:
    """Render bounded, truth-preserving material for proactive Phase 2."""

    lane_labels = {
        "hot": "全站热门",
        "home": "首页推荐",
        "following": "关注更新",
    }
    basis_labels = {"subtitle": "字幕", "metadata": "视频简介"}
    lines = [
        "【B站候选资料】",
        f"资源类型：{lane_labels.get(str(candidate.get('lane')), 'B站内容')}",
        f"UP主：{candidate.get('author', '')}",
        f"标题：{candidate.get('title', '')}",
        f"推荐依据：{candidate.get('reason', '')}",
        f"登录态确认：{'是' if candidate.get('authenticated') else '否'}",
    ]
    summary = str(candidate.get("content_summary") or "").strip()
    if summary:
        lines.append(f"内容摘要：{summary}")
        lines.append(
            f"摘要依据：{basis_labels.get(str(candidate.get('summary_basis')), '可用资料')}"
        )
    elif candidate.get("kind") != "video" and candidate.get("description_hint"):
        lines.append(
            f"动态正文：{str(candidate.get('description_hint') or '').strip()[:500]}"
        )
        lines.append("内容依据：动态接口正文")
    else:
        lines.append("内容摘要：无可靠摘要；只能把标题描述为“看起来在聊……”，不得断言具体内容。")
    if candidate.get("published_at"):
        lines.append(f"发布时间戳：{candidate['published_at']}")
    lines.extend(
        [
            f"链接：{candidate.get('url', '')}",
            "表达约束：热门只能说“最近热门/挺火/热门榜靠前”；首页只能说“可能感兴趣”；"
            "只有“登录态确认：是”的关注更新才能说“你关注的UP主更新了”。",
            "请自然说1至2句，不朗读统计数据，不补充资料中不存在的情节。",
            "【B站候选资料结束】",
        ]
    )
    return "\n".join(lines)
