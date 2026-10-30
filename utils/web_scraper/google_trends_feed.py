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

"""Google Trends daily search trends for the non-China proactive news source.

Reads the public, login-free trending RSS feed. The feed is per country, so the
country is derived from the user's language to keep topics in a language the
user actually reads.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

from utils.external_http_client import get_external_http_client
from utils.language_utils import get_global_language_full

from ._shared import get_random_user_agent, logger


_TRENDS_RSS_URL = "https://trends.google.com/trending/rss"
_HT_NS = "{https://trends.google.com/trending/rss}"
_DEFAULT_GEO = "US"
_LANGUAGE_TO_GEO = {
    "en": "US",
    "ja": "JP",
    "ko": "KR",
    "ru": "RU",
    "es": "ES",
    "pt": "BR",
    "zh-TW": "TW",
}
_MAX_NEWS_PER_TREND = 2


def google_trends_geo_for_language(language: str | None) -> str:
    """Map a UI language code to the Google Trends country feed to read."""
    code = str(language or "").strip()
    if code in _LANGUAGE_TO_GEO:
        return _LANGUAGE_TO_GEO[code]
    short = code.split("-", 1)[0].lower()
    if short == "zh":
        # Simplified Chinese outside mainland China has no single matching feed.
        return _DEFAULT_GEO
    return _LANGUAGE_TO_GEO.get(short, _DEFAULT_GEO)


def _plain(value: str | None, max_chars: int) -> str:
    return " ".join(str(value or "").split())[:max_chars]


def google_trend_headline(trend: dict[str, Any]) -> str:
    """Top headline of a trend with its publisher, or an empty string."""
    news = trend.get("news") or []
    if not news:
        return ""
    headline = str(news[0].get("title") or "")
    source = str(news[0].get("source") or "")
    if not headline:
        return ""
    return f"{headline} - {source}" if source else headline


def parse_google_trends_rss(xml_text: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Parse the trending RSS feed into normalized trend items."""
    root = ET.fromstring(xml_text)
    trends: list[dict[str, Any]] = []
    for item in root.iter("item"):
        word = _plain(item.findtext("title"), 120)
        if not word:
            continue
        news: list[dict[str, str]] = []
        for news_item in item.findall(f"{_HT_NS}news_item"):
            title = _plain(news_item.findtext(f"{_HT_NS}news_item_title"), 200)
            url = _plain(news_item.findtext(f"{_HT_NS}news_item_url"), 500)
            if not title or not url.startswith(("https://", "http://")):
                continue
            news.append({
                "title": title,
                "url": url,
                "source": _plain(news_item.findtext(f"{_HT_NS}news_item_source"), 80),
            })
            if len(news) >= _MAX_NEWS_PER_TREND:
                break
        trends.append({
            "word": word,
            "traffic": _plain(item.findtext(f"{_HT_NS}approx_traffic"), 20),
            "news": news,
            # The feed's own <link> points back at the feed, so link the top
            # article instead and fall back to a web search for the topic.
            "url": news[0]["url"] if news else f"https://www.google.com/search?q={quote(word)}",
        })
        if len(trends) >= limit:
            break
    return trends


async def fetch_google_trends(limit: int = 10, geo: str | None = None) -> dict[str, Any]:
    """Fetch today's trending searches for the user's country."""
    resolved_geo = geo or google_trends_geo_for_language(get_global_language_full())
    try:
        response = await get_external_http_client().get(
            _TRENDS_RSS_URL,
            params={"geo": resolved_geo},
            headers={"User-Agent": get_random_user_agent()},
            timeout=10.0,
        )
        response.raise_for_status()
        trends = parse_google_trends_rss(response.text, limit=limit)
    except Exception as error:
        logger.warning(f"Google Trends 获取失败: geo={resolved_geo} error={error}")
        return {
            "success": False,
            "source": "google_trends",
            "geo": resolved_geo,
            "trending": [],
            "error": f"Google Trends: {error}",
        }
    if not trends:
        return {
            "success": False,
            "source": "google_trends",
            "geo": resolved_geo,
            "trending": [],
            "error": "Google Trends returned no trends",
        }
    return {"success": True, "source": "google_trends", "geo": resolved_geo, "trending": trends}
