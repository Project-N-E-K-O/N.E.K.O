"""Use Verse's ranked choices for an authenticated proactive conversation."""

import asyncio
import json
import re
from urllib.parse import urlsplit

import httpx

from utils.proactive_preferences.personalization import RecommendationContext
from utils.proactive_preferences.search_policy import may_search_phrase
from utils.social_base import community_bearer_transport_allowed, social_base_url

from .decisions import _should_skip_source
from .phase1_ranking import link_cooldown_key


VERSE_MODES = frozenset({"news", "video", "home", "community", "music", "meme"})
_LINK_HOSTS = {
    "news": {"news.google.com", "store.steampowered.com"},
    "video": {"www.youtube.com", "www.bilibili.com"},
    "home": {"www.youtube.com", "www.bilibili.com"},
    "meme": {"i.imgflip.com"},
}
_MAX_CHOICES = 12


async def fetch_verse_plan(
    context: RecommendationContext,
    modes: set[str],
    *,
    locale: str,
    china: bool,
) -> list[dict] | None:
    origin = social_base_url().rstrip("/")
    if (
        not modes
        or not context.bearer
        or not context.account_id
        or context.community.rstrip("/") != origin
        or not community_bearer_transport_allowed(origin)
    ):
        return None
    try:
        async with (
            asyncio.timeout(7.0),
            httpx.AsyncClient(
                timeout=7.0,
                follow_redirects=False,
            ) as client,
            client.stream(
                "POST",
                f"{origin}/api/proactive/recommendations",
                headers={"Authorization": f"Bearer {context.bearer}"},
                json={
                    "version": 1,
                    "account_id": context.account_id,
                    "modes": sorted(modes),
                    "locale": locale,
                    "china": china,
                },
            ) as response,
        ):
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > 64 * 1024:
                    return None
                body.extend(chunk)
        payload = json.loads(body)
        if (
            not isinstance(payload, dict)
            or type(payload.get("version")) is not int
            or payload["version"] != 1
        ):
            return None
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) > _MAX_CHOICES:
            return None
        return [item for item in choices if isinstance(item, dict)]
    except (httpx.HTTPError, TimeoutError, ValueError, RecursionError):
        return None


def select_verse_source(
    choices: list[dict],
    context: RecommendationContext,
    modes: set[str],
) -> tuple[str, dict] | None:
    """Keep the first cloud choice that can still be delivered on this device."""
    profile = context.preferences.profile
    for item in choices:
        mode = item.get("mode")
        if not isinstance(mode, str) or mode not in modes:
            continue
        if mode == "music":
            keyword = item.get("keyword")
            if not isinstance(keyword, str) or not may_search_phrase(keyword):
                continue
            if profile.score(mode, profile.match(mode, {"name": keyword})) is not None:
                return mode, {"placeholder": True, "verse_keyword": keyword}
            continue
        title, url, source, description = (
            item.get(field, "")
            for field in (
                "title",
                "url",
                "source",
                "description_hint",
            )
        )
        if (
            not all(
                isinstance(value, str) for value in (title, url, source, description)
            )
            or not title.strip()
            or not source.strip()
            or len(url) > 2048
            or any(char.isspace() or char in "\\|<>" for char in url)
        ):
            continue
        link = {
            "title": title.strip()[:200],
            "url": url,
            "source": source.strip()[:80],
            "description_hint": description.strip()[:300],
            "verse_selected": True,
            "grounded_news": True,
        }
        tags = item.get("tags")
        if isinstance(tags, list):
            link["tags"] = [tag[:48] for tag in tags[:12] if isinstance(tag, str)]
        if mode == "community":
            card_id = item.get("card_id")
            if not re.fullmatch(r"/cards/[0-9a-fA-F-]{1,36}", url) or not (
                isinstance(card_id, str) and re.fullmatch(r"[0-9a-fA-F-]{36}", card_id)
            ):
                continue
            origin = context.community.rstrip("/")
            link.update(
                url=origin + url,
                source="喵宇宙社区",
                dedupe_key=f"neko-community:{origin.casefold()}|{card_id}",
            )
        else:
            try:
                parts = urlsplit(url)
                if parts.scheme != "https" or parts.netloc not in _LINK_HOSTS[mode]:
                    continue
            except (KeyError, ValueError):
                continue
        if profile.score(
            mode, profile.match(mode, link)
        ) is not None and not _should_skip_source(link_cooldown_key(link)):
            if mode == "meme":
                return mode, {
                    "placeholder": True,
                    "verse_meme": {
                        field: link[field] for field in ("title", "url", "source")
                    },
                }
            return mode, {"links": [link], "formatted_content": "", "raw_data": {}}
    return None
