"""Retrieve public candidates based on the local recommendation profile."""

import asyncio
import re
import time
from html import unescape
from urllib.parse import urlsplit

import httpx

from utils.language_utils import get_global_language_full, is_china_region
from utils.proactive_preferences.personalization import LocalPreferences
from utils.proactive_preferences.preference_model import applicable_scopes
from utils.proactive_preferences.search_policy import is_public_topic, may_search_phrase
from utils.web_scraper.bilibili_content import search_bilibili_videos
from utils.web_scraper.interest_catalog import fetch_public_search_words
from utils.web_scraper.news_filters import is_tragic_news_text
from utils.web_scraper.search_gateway import search_via_plugin
from utils.web_scraper.steam_store_feed import search_steam_games, steam_game_summary
from utils.web_scraper.trending_content import search_neko_community_cards
from utils.web_scraper.youtube_feed import search_youtube_videos


_MAX_TOPICS_PER_SOURCE = 2
_RESULT_LIMIT = 6
_FETCH_TIMEOUT = 6.0
_STEAM_FETCH_TIMEOUT = 10.0
_NEWS_FETCH_TIMEOUT = 10.0  # Includes the search gateway's five-second start interval.
_WEB_MODES = {"news", "video", "home", "community"}


def _searchable_topics(preferences: LocalPreferences, mode: str) -> set[str]:
    profile = preferences.profile
    legacy = {
        topic
        for scope, topic in preferences.explicit_topics | profile.recent_focus.keys()
        if scope in applicable_scopes(mode)
    }
    return {
        topic
        for topic in profile.features(mode)
        if may_search_phrase(topic)
        and (profile.score(mode, {topic}) or 0) > 0
        and (
            permission.public
            if (permission := preferences.search_permission(mode, topic)) is not None
            else topic in legacy
        )
    }


def public_interest_topics(
    preferences: LocalPreferences,
    mode: str,
    *,
    public_words: frozenset[str] = frozenset(),
    exploration_round: int = 0,
) -> list[str]:
    """Select full interest phrases; provider tags never determine eligibility."""
    profile = preferences.profile
    scored = [
        (profile.score(mode, {topic}) or 0, topic)
        for topic in _searchable_topics(preferences, mode)
        if is_public_topic(topic, public_words)
    ]
    ordered = [topic for _, topic in sorted(scored, key=lambda row: (-row[0], row[1]))]
    if not ordered:
        return []
    # Reserve one query for the user's current focus, leaving room for another topic.
    lead = max(ordered, key=lambda topic: profile.focus_weight(mode, topic))
    remaining = [topic for topic in ordered if topic != lead]
    if len(ordered) <= _MAX_TOPICS_PER_SOURCE:
        return [lead, *remaining]
    return [lead, remaining[exploration_round % len(remaining)]]


def _plain_text(value: str, limit: int) -> str:
    return " ".join(unescape(re.sub(r"<[^>]{1,120}>", "", value[:1000])).split())[
        :limit
    ]


async def _search_news(query: str, *, china: bool) -> list[dict]:
    reply = await search_via_plugin(
        query,
        limit=_RESULT_LIMIT,
        preferred_backend="baidu" if china else "duckduckgo",
    )
    links = []
    for result in reply.get("results", [])[:_RESULT_LIMIT]:
        url = result["url"]
        if len(url) > 2048 or any(ch.isspace() or ch in "\\|<>" for ch in url):
            continue
        try:
            address = urlsplit(url)
            host = address.hostname
        except ValueError:
            continue
        if (
            address.scheme not in {"http", "https"}
            or not host
            or address.username is not None
        ):
            continue
        title = _plain_text(result["title"], 200)
        summary = _plain_text(result.get("abstract", ""), 300)
        if not title or is_tragic_news_text(f"{title} {summary}"):
            continue
        links.append(
            {
                "title": title,
                "url": url,
                "source": host.removeprefix("www."),
                "description_hint": summary,
                "grounded_news": True,
            }
        )
    return links


async def _fetch_topic(mode: str, query: str, language: str, china: bool) -> list[dict]:
    if mode == "community":
        return await search_neko_community_cards(
            query, limit=_RESULT_LIMIT, locale=language
        )
    if mode == "video":
        if china:
            return await search_bilibili_videos(query, limit=_RESULT_LIMIT)
        return await search_youtube_videos(query, limit=_RESULT_LIMIT)
    if mode == "steam":
        return [
            {
                "title": game["title"],
                "url": game["url"],
                "source": "Steam",
                "description_hint": steam_game_summary(game),
                "grounded_news": True,
            }
            for game in await search_steam_games(
                query, language=language, limit=_RESULT_LIMIT
            )
        ]
    return await _search_news(query, china=china)


async def collect_interest_candidates(
    preferences: LocalPreferences, modes: list[str], *, locale: str | None,
    include_excluded_metadata: bool = False,
) -> dict[str, list[dict]]:
    """Search original interest phrases concurrently with provider-specific adapters."""
    web_modes = [mode for mode in modes if mode in _WEB_MODES]
    profile = preferences.profile
    if not any(
        (profile.score(mode, {feature}) or 0) > 0
        for mode in web_modes
        for feature in profile.features(mode)
    ):
        return {}
    language = locale or get_global_language_full() or "en"
    china = is_china_region()
    exploration_round = int(time.time()) // 600
    requests: dict[tuple[str, str], asyncio.Task[list[dict]]] = {}

    async def retrieve(source: str, topic: str) -> list[dict]:
        limit = {"news": _NEWS_FETCH_TIMEOUT, "steam": _STEAM_FETCH_TIMEOUT}.get(source, _FETCH_TIMEOUT)
        try:
            return await asyncio.wait_for(
                _fetch_topic(source, topic, language, china), limit
            )
        except (httpx.HTTPError, TimeoutError, OSError, ValueError):
            return []

    async def collect_mode(mode: str) -> list[dict]:
        topics = public_interest_topics(
            preferences, mode, exploration_round=exploration_round
        )
        unverified = {
            topic
            for topic in _searchable_topics(preferences, mode)
            if not is_public_topic(topic)
        }
        if unverified and (
            len(topics) < _MAX_TOPICS_PER_SOURCE
            or any(profile.focus_weight(mode, topic) for topic in unverified)
        ):
            topics = public_interest_topics(
                preferences,
                mode,
                public_words=await fetch_public_search_words(language),
                exploration_round=exploration_round,
            )
        tasks = []
        for topic in topics:
            providers = ["video" if mode == "home" else mode]
            permission = preferences.search_permission(mode, topic)
            if mode == "news" and permission is not None and permission.game:
                providers.append("steam")
            for provider in providers:
                key = (provider, topic)
                if key not in requests:
                    requests[key] = asyncio.create_task(retrieve(provider, topic))
                tasks.append(requests[key])
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        return [
            {**item, "interest_match": True}
            for outcome in outcomes
            if isinstance(outcome, list)
            for item in outcome
            if ((score := profile.score(mode, profile.match(mode, item))) or 0) > 0
            or (include_excluded_metadata and score is None)
        ]

    results = await asyncio.gather(*(collect_mode(mode) for mode in web_modes))
    return {
        mode: profile.rank(mode, links) + [
            item for item in links
            if include_excluded_metadata and profile.score(mode, profile.match(mode, item)) is None
        ]
        for mode, links in zip(web_modes, results)
        if links
    }
