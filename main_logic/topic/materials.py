"""Low-frequency topic material helpers for proactive chat.

This module does not trigger proactive chat and does not own long-term memory.
It only turns existing memory/recent candidates into compact hook materials,
optionally enriching the top hooks with existing lightweight online fetchers.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from copy import deepcopy
from typing import Any

from main_logic.topic.common import ZH_LINK_STOP_CHARS, clean_text, is_zh_lang, topic_units
from utils.source_locale import source_region_from_locale
from utils.tokenize import truncate_to_tokens


logger = logging.getLogger("N.E.K.O.Main.topic.materials")


Fetcher = Callable[[str, int], Awaitable[Mapping[str, Any]]]
_DEFAULT_ONLINE_INTENTS = ("video", "meme")
_TOKEN_PRECAP_CHARS_PER_TOKEN = 8


def _clean_text(value: Any, *, token_limit: int) -> str:
    precap = max(token_limit, token_limit * _TOKEN_PRECAP_CHARS_PER_TOKEN)
    return truncate_to_tokens(clean_text(value, limit=precap), token_limit)


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
    return normalized


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
        search_duckduckgo,
    )

    async def search(keyword: str, limit: int) -> Mapping[str, Any]:
        source_region = source_region_from_locale(_source_locale_for_lang(lang))
        use_mainland_source = (
            source_region == "china"
            or (source_region is None and is_zh_lang(lang))
        )
        # Non-mainland uses DuckDuckGo, not Google: scripted Google requests
        # almost always hit the /sorry / 429 anti-bot flow (same reason the
        # window-search path in utils/web_scraper.py switched). Baidu is the
        # cross-region fallback of last resort.
        primary = search_baidu if use_mainland_source else search_duckduckgo
        fallback = search_duckduckgo if use_mainland_source else search_baidu
        result = await primary(keyword, limit=limit)
        if not result.get("success"):
            fallback_result = await fallback(keyword, limit=limit)
            if fallback_result.get("success"):
                result = fallback_result
        return {
            "success": bool(result.get("success")),
            "region": "china" if use_mainland_source else "non-china",
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
    # A big-model-derived deep_query (Phase-2 delivery-time deep search) wins
    # when present. Otherwise: the small candidate model no longer authors a
    # search string, so the cheap background pre-fetch query is just the
    # keywords joined; interest/hook stay as fallbacks when none survived.
    deep_query = _clean_text(material.get("deep_query"), token_limit=80)
    if deep_query:
        return deep_query
    keywords = [_clean_text(kw, token_limit=30) for kw in (material.get("keywords") or [])]
    query = truncate_to_tokens(" ".join(kw for kw in keywords if kw), 80)
    if query:
        return query
    interest = _clean_text(material.get("interest"), token_limit=32)
    if interest:
        return interest
    return _clean_text(material.get("hook"), token_limit=32)


def _items_from_result(kind: str, result: Mapping[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []

    def add(title: Any, url: Any = "") -> None:
        title_text = _clean_text(title, token_limit=80)
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


def _topic_bigram_units(text: str) -> set[str]:
    # 2gram-only view: CJK bigrams + multi-char latin/other tokens, dropping
    # single CJK chars. The single-char overlap is too noisy to gate on, so the
    # ngram fallback below leans on ≥2-char units only.
    units = topic_units(
        text,
        limit=120,
        stop_chars=ZH_LINK_STOP_CHARS,
        include_cjk_bigrams=True,
    )
    return {unit for unit in units if len(unit) >= 2}


def _is_related_link(query: str, keywords: Iterable[str], link: Mapping[str, str]) -> bool:
    """Two-tier relevance: LLM keyword link first, ngram fallback second.

    Primary: keep the link if its title literally contains one of the
    candidate's keywords. Fallback (only when no keyword matched, kept
    deliberately strict): require ≥2 shared 2gram units between query and
    title, so single-char coincidences can't pass an off-topic title.
    """
    title = str(link.get("title", "") or "")
    if not title:
        return False
    title_lower = title.lower()
    for kw in keywords or ():
        kw_text = str(kw or "").strip().lower()
        if kw_text and kw_text in title_lower:
            return True

    query_bigrams = _topic_bigram_units(query)
    if not query_bigrams:
        return False
    shared = len(query_bigrams & _topic_bigram_units(title))
    if shared >= 2:
        logger.info(
            "topic link ngram fallback matched (keyword miss): shared=%d title=%s",
            shared, title[:40],
        )
        return True
    return False


def _filter_related_links(
    query: str, keywords: Iterable[str], links: list[dict[str, str]]
) -> list[dict[str, str]]:
    return [link for link in links if _is_related_link(query, keywords, link)]


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
        intents = [kind for kind in _DEFAULT_ONLINE_INTENTS if kind in available_fetchers]
        if not intents:
            intents = list(available_fetchers)[:2]

        results = await asyncio.gather(*[
            _safe_fetch(kind, available_fetchers[kind], query, fetch_limit, timeout_s)
            for kind in intents
        ])
        links: list[dict[str, str]] = []
        for kind, result in results:
            if result is None:
                continue
            links.extend(_items_from_result(kind, result)[:2])
        links = _filter_related_links(query, material.get("keywords") or [], links)

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
