"""Low-frequency topic material helpers for proactive chat.

This module does not trigger proactive chat and does not own long-term memory.
It only turns existing memory/recent candidates into compact hook materials,
optionally enriching the top hooks with existing lightweight online fetchers.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from main_logic.topic.common import ZH_LINK_STOP_CHARS, clean_text, is_zh_lang, topic_units


Fetcher = Callable[[str, int], Awaitable[Mapping[str, Any]]]


def _parse_now(now_iso: str | None) -> datetime:
    if now_iso:
        return datetime.fromisoformat(now_iso)
    return datetime.now().astimezone()


def _hook_id(source: str, interest: str) -> str:
    import hashlib

    digest = hashlib.sha1(f"{source}:{interest}".encode("utf-8")).hexdigest()[:12]
    return f"topic_{digest}"


def _iter_candidate_texts(
    *,
    recent_topics: Iterable[Any] | None = None,
    followup_topics: Iterable[Mapping[str, Any]] | None = None,
    open_threads: Iterable[Any] | None = None,
) -> Iterable[tuple[str, str]]:
    for text in recent_topics or []:
        cleaned = clean_text(text)
        if cleaned:
            yield "recent", cleaned
    for topic in followup_topics or []:
        if not isinstance(topic, Mapping):
            continue
        cleaned = clean_text(topic.get("text"))
        if cleaned:
            yield "memory", cleaned
    for text in open_threads or []:
        cleaned = clean_text(text)
        if cleaned:
            yield "thread", cleaned


def _infer_media_intent(text: str) -> list[str]:
    lower = text.lower()
    if any(token in lower for token in ("表情包", "梗图", "meme", "gif")):
        return ["meme"]
    if any(token in lower for token in ("音乐", "歌", "music", "song")):
        return ["music"]
    if any(token in lower for token in ("热点", "新闻", "微博", "twitter", "news")):
        return ["news"]
    return ["news"]


def build_topic_materials(
    *,
    recent_topics: Iterable[Any] | None = None,
    followup_topics: Iterable[Mapping[str, Any]] | None = None,
    open_threads: Iterable[Any] | None = None,
    max_items: int = 2,
    now_iso: str | None = None,
) -> list[dict[str, Any]]:
    """Build a small set of high-signal hook materials from existing candidates."""
    now = _parse_now(now_iso)
    expires_at = (now + timedelta(days=1)).isoformat()
    materials: list[dict[str, Any]] = []
    seen: set[str] = set()

    for source, interest in _iter_candidate_texts(
        recent_topics=recent_topics,
        followup_topics=followup_topics,
        open_threads=open_threads,
    ):
        if interest in seen:
            continue
        seen.add(interest)
        priority = max(10, 90 - len(materials) * 8)
        materials.append({
            "hook_id": _hook_id(source, interest),
            "source": source,
            "interest": interest,
            "hook": f"围绕「{interest}」自然接住，不做总结报告，只抛一个轻钩子。",
            "opening_intent": "具体、短、像随口一提；可以轻微调侃，不要像问卷。",
            "deepening_hint": "如果用户接话，再顺着用户反应聊偏好、原因、选择或情绪。",
            "media_intent": _infer_media_intent(interest),
            "priority": priority,
            "status": "pending",
            "created_at": now.isoformat(),
            "expires_at": expires_at,
        })
        if len(materials) >= max_items:
            break
    return materials


def _source_locale_for_lang(lang: str | None) -> str | None:
    normalized = str(lang or "").strip().replace("_", "-")
    if not normalized:
        return None
    lower = normalized.lower()
    if lower == "zh":
        return "zh-CN"
    if lower.startswith("zh-hans"):
        return "zh-CN"
    if lower.startswith("zh-hant"):
        return "zh-TW"
    if lower.startswith("zh-"):
        return normalized
    return None


def _summary_template_for_lang(lang: str | None) -> str:
    raw = str(lang or "").strip().replace("_", "-").lower()
    if not raw:
        return "找到了和「{query}」有关的素材：{titles}。必须自然借一个具体点开口，别把联网结果讲成报告。"
    if raw.startswith(("zh-tw", "zh-hant", "zh-hk")):
        return "找到了和「{query}」有關的素材：{titles}。必須自然借一個具體點開口，別把聯網結果講成報告。"
    if raw.startswith("zh"):
        return "找到了和「{query}」有关的素材：{titles}。必须自然借一个具体点开口，别把联网结果讲成报告。"
    if raw.startswith("ja"):
        return "「{query}」に関係する素材が見つかりました：{titles}。具体点を一つだけ自然に借りて切り出し、検索結果の報告にしないでください。"
    if raw.startswith("ko"):
        return '"{query}"와 관련된 소재를 찾았습니다: {titles}. 구체적인 지점 하나만 자연스럽게 빌려 시작하고, 검색 결과 보고처럼 말하지 마세요.'
    if raw.startswith("es"):
        return 'Encontré material relacionado con "{query}": {titles}. Usa un detalle concreto de forma natural para abrir, sin convertirlo en un informe de búsqueda.'
    if raw.startswith("pt"):
        return 'Encontrei material relacionado a "{query}": {titles}. Use um detalhe concreto com naturalidade para abrir, sem transformar isso em relatório de busca.'
    if raw.startswith("ru"):
        return 'Нашлись материалы по запросу "{query}": {titles}. Естественно используй одну конкретную деталь для начала, не превращая это в отчет о поиске.'
    return 'Found material related to "{query}": {titles}. Borrow one concrete detail naturally to open; do not turn the search result into a report.'


async def _default_fetchers(lang: str | None = None) -> dict[str, Fetcher]:
    from utils.meme_fetcher import fetch_meme_content
    from utils.music_crawlers import fetch_music_content
    from utils.web_scraper import (
        search_baidu,
        search_google,
    )

    async def search(keyword: str, limit: int) -> Mapping[str, Any]:
        zh_lang = is_zh_lang(lang)
        primary = search_baidu if zh_lang else search_google
        fallback = search_google if zh_lang else search_baidu
        result = await primary(keyword, limit=limit)
        if not result.get("success"):
            fallback_result = await fallback(keyword, limit=limit)
            if fallback_result.get("success"):
                result = fallback_result
        return {
            "success": bool(result.get("success")),
            "region": "china" if zh_lang else "non-china",
            "search": result,
        }

    async def video(keyword: str, limit: int) -> Mapping[str, Any]:
        return await search(keyword, limit)

    async def news(keyword: str, limit: int) -> Mapping[str, Any]:
        return await search(keyword, limit)

    async def meme(keyword: str, limit: int) -> Mapping[str, Any]:
        return await fetch_meme_content(
            keyword=keyword,
            limit=limit,
            source_locale=_source_locale_for_lang(lang),
        )

    async def music(keyword: str, limit: int) -> Mapping[str, Any]:
        return await fetch_music_content(
            keyword=keyword,
            limit=limit,
            source_locale=_source_locale_for_lang(lang),
        )

    return {
        "video": video,
        "news": news,
        "meme": meme,
        "music": music,
    }


def _query_for_material(material: Mapping[str, Any]) -> str:
    query = clean_text(material.get("search_query"), limit=80)
    if query:
        return query
    interest = clean_text(material.get("interest"), limit=32)
    if interest:
        return interest
    return clean_text(material.get("hook"), limit=32)


def _items_from_result(kind: str, result: Mapping[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []

    def add(title: Any, url: Any = "") -> None:
        title_text = clean_text(title, limit=80)
        if not title_text:
            return
        items.append({
            "type": kind,
            "title": title_text,
            "url": str(url or ""),
        })

    if kind == "meme":
        for item in result.get("data") or result.get("results") or []:
            if isinstance(item, Mapping):
                add(item.get("title"), item.get("url") or item.get("image_url"))
        return items

    if kind == "music":
        for item in result.get("data") or result.get("tracks") or result.get("results") or []:
            if isinstance(item, Mapping):
                add(item.get("title") or item.get("name"), item.get("url"))
        return items

    search = result.get("search")
    if isinstance(search, Mapping):
        for item in search.get("results") or []:
            if isinstance(item, Mapping):
                add(item.get("title"), item.get("url"))
        return items

    nested = result.get(kind)
    if isinstance(nested, Mapping):
        buckets = (
            nested.get("videos")
            or nested.get("items")
            or nested.get("posts")
            or nested.get("trending")
            or nested.get("topics")
            or []
        )
        for item in buckets:
            if isinstance(item, Mapping):
                add(item.get("title") or item.get("word"), item.get("url"))
    return items


def _topic_units(text: str) -> set[str]:
    return topic_units(
        text,
        limit=120,
        stop_chars=ZH_LINK_STOP_CHARS,
        include_cjk_bigrams=False,
    )


def _is_related_link(query: str, link: Mapping[str, str]) -> bool:
    query_units = _topic_units(query)
    if not query_units:
        return False
    title_units = _topic_units(link.get("title", ""))
    if not title_units:
        return False
    overlap = query_units & title_units
    if any(len(unit) >= 3 for unit in overlap):
        return True
    return len(overlap) >= 2


def _filter_related_links(query: str, links: list[dict[str, str]]) -> list[dict[str, str]]:
    return [link for link in links if _is_related_link(query, link)]


async def _safe_fetch(kind: str, fetcher: Fetcher, query: str, limit: int, timeout_s: float) -> tuple[str, Mapping[str, Any] | None]:
    try:
        result = await asyncio.wait_for(fetcher(query, limit), timeout=timeout_s)
    except Exception:
        return kind, None
    if not isinstance(result, Mapping) or not result.get("success"):
        return kind, None
    return kind, result


async def enrich_topic_materials_online(
    materials: Iterable[Mapping[str, Any]],
    *,
    fetchers: Mapping[str, Fetcher] | None = None,
    lang: str | None = None,
    max_materials: int = 2,
    fetch_limit: int = 3,
    timeout_s: float = 4.0,
) -> list[dict[str, Any]]:
    """Enrich top materials with lightweight online hints via existing fetchers."""
    available_fetchers = dict(await _default_fetchers(lang) if fetchers is None else fetchers)
    enriched = [deepcopy(dict(material)) for material in materials]

    for material in enriched[:max_materials]:
        if material.get("material_hint"):
            continue
        query = _query_for_material(material)
        if not query:
            continue
        intents = [
            intent for intent in material.get("media_intent", [])
            if intent in available_fetchers
        ][:2]
        if not intents:
            intents = [kind for kind in ("video", "meme") if kind in available_fetchers]

        results = await asyncio.gather(*[
            _safe_fetch(kind, available_fetchers[kind], query, fetch_limit, timeout_s)
            for kind in intents
        ])
        links: list[dict[str, str]] = []
        for kind, result in results:
            if result is None:
                continue
            links.extend(_items_from_result(kind, result)[:2])
        links = _filter_related_links(query, links)

        if links:
            titles = "、".join(link["title"] for link in links[:3])
            material["material_hint"] = {
                "summary": _summary_template_for_lang(lang).format(query=query, titles=titles),
                "links": links[:4],
                "meme_keyword": query if "meme" in intents else "",
                "music_keyword": query if "music" in intents else "",
            }
            material["online_used"] = True
            material["online_query"] = query
            material["online_angle"] = titles
    return enriched
