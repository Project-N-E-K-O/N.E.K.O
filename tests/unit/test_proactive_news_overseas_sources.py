from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from main_logic.proactive_chat.sources import _extract_links_from_raw
from utils.web_scraper import google_trends_feed, steam_store_feed
from utils.web_scraper.google_trends_feed import (
    fetch_google_trends,
    google_trends_geo_for_language,
    parse_google_trends_rss,
)
from utils.web_scraper.steam_store_feed import (
    fetch_steam_featured,
    normalize_steam_featured,
    steam_store_locale_for_language,
)
from utils.web_scraper.trending_content import fetch_news_content, format_news_content


SAMPLE_TRENDS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0">
  <channel>
    <title>Daily Search Trends</title>
    <item>
      <title>World Series</title>
      <ht:approx_traffic>500K+</ht:approx_traffic>
      <link>https://trends.google.com/trending/rss?geo=US</link>
      <ht:news_item>
        <ht:news_item_title>Game 7 heads to extra innings</ht:news_item_title>
        <ht:news_item_url>https://news.example.com/game7</ht:news_item_url>
        <ht:news_item_source>Example News</ht:news_item_source>
      </ht:news_item>
      <ht:news_item>
        <ht:news_item_title>Broken link item</ht:news_item_title>
        <ht:news_item_url>javascript:alert(1)</ht:news_item_url>
      </ht:news_item>
    </item>
    <item>
      <title>  Halloween   costumes </title>
      <ht:approx_traffic>200K+</ht:approx_traffic>
    </item>
    <item>
      <title></title>
    </item>
    <item>
      <title>Third trend</title>
    </item>
  </channel>
</rss>
"""


def _steam_item(app_id, name, *, type_=0, discount=0):
    return {"id": app_id, "type": type_, "name": name, "discount_percent": discount}


SAMPLE_STEAM_PAYLOAD = {
    "top_sellers": {
        "items": [
            _steam_item(10, "Top Game A"),
            _steam_item(11, "Steam Deck", type_=2),
            _steam_item(12, "Shared Game", discount=25),
            _steam_item("bad", "Bad Id"),
        ]
    },
    "new_releases": {
        "items": [
            _steam_item(20, "New Game A"),
            _steam_item(12, "Shared Game"),
            _steam_item(21, "New Game B"),
            _steam_item(22, ""),
        ]
    },
    "specials": {"items": [_steam_item(30, "Special Only")]},
}


def _fake_client(response=None, error=None):
    client = MagicMock()
    if error is not None:
        client.get = AsyncMock(side_effect=error)
    else:
        client.get = AsyncMock(return_value=response)
    return client


def _response(url, *, text=None, json_payload=None, status=200):
    request = httpx.Request("GET", url)
    if json_payload is not None:
        return httpx.Response(status, json=json_payload, request=request)
    return httpx.Response(status, text=text or "", request=request)


@pytest.mark.unit
def test_parse_google_trends_rss_keeps_valid_news_and_links_top_article():
    trends = parse_google_trends_rss(SAMPLE_TRENDS_RSS, limit=10)

    assert [trend["word"] for trend in trends] == [
        "World Series",
        "Halloween costumes",
        "Third trend",
    ]
    first = trends[0]
    assert first["traffic"] == "500K+"
    assert first["news"] == [
        {
            "title": "Game 7 heads to extra innings",
            "url": "https://news.example.com/game7",
            "source": "Example News",
        }
    ]
    assert first["url"] == "https://news.example.com/game7"
    assert trends[1]["news"] == []
    assert trends[1]["url"] == "https://www.google.com/search?q=Halloween%20costumes"


@pytest.mark.unit
def test_parse_google_trends_rss_respects_limit():
    assert len(parse_google_trends_rss(SAMPLE_TRENDS_RSS, limit=1)) == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("language", "geo"),
    [
        ("en", "US"),
        ("ja", "JP"),
        ("ko", "KR"),
        ("ru", "RU"),
        ("es", "ES"),
        ("pt", "BR"),
        ("pt-BR", "BR"),
        ("zh-TW", "TW"),
        ("zh", "US"),
        ("zh-CN", "US"),
        ("fr", "US"),
        ("", "US"),
        (None, "US"),
    ],
)
def test_google_trends_geo_follows_user_language(language, geo):
    assert google_trends_geo_for_language(language) == geo


@pytest.mark.unit
@pytest.mark.parametrize(
    ("language", "store"),
    [
        ("en", ("english", "us")),
        ("ja", ("japanese", "jp")),
        ("ko", ("koreana", "kr")),
        ("ru", ("russian", "ru")),
        ("es", ("spanish", "es")),
        ("pt", ("brazilian", "br")),
        ("zh-TW", ("tchinese", "tw")),
        ("zh-CN", ("schinese", "us")),
        ("fr", ("english", "us")),
        (None, ("english", "us")),
    ],
)
def test_steam_store_locale_follows_user_language(language, store):
    assert steam_store_locale_for_language(language) == store


@pytest.mark.unit
def test_normalize_steam_featured_interleaves_dedupes_and_drops_non_games():
    games = normalize_steam_featured(SAMPLE_STEAM_PAYLOAD, limit=10)

    assert [(game["title"], game["category"]) for game in games] == [
        ("Top Game A", "top_seller"),
        ("New Game A", "new_release"),
        ("Shared Game", "top_seller"),
        ("New Game B", "new_release"),
    ]
    assert games[0]["url"] == "https://store.steampowered.com/app/10/"
    assert games[2]["discount_percent"] == 25


@pytest.mark.unit
def test_normalize_steam_featured_respects_limit_and_bad_payload():
    assert len(normalize_steam_featured(SAMPLE_STEAM_PAYLOAD, limit=3)) == 3
    assert normalize_steam_featured(None) == []
    assert normalize_steam_featured({"top_sellers": "oops"}) == []


@pytest.mark.asyncio
async def test_fetch_google_trends_requests_language_geo():
    client = _fake_client(
        _response("https://trends.google.com/trending/rss", text=SAMPLE_TRENDS_RSS)
    )
    with patch.object(google_trends_feed, "get_external_http_client", return_value=client), patch.object(
        google_trends_feed, "get_global_language_full", return_value="ja"
    ):
        result = await fetch_google_trends(limit=2)

    assert result["success"] is True
    assert result["geo"] == "JP"
    assert len(result["trending"]) == 2
    assert client.get.await_args.kwargs["params"] == {"geo": "JP"}


@pytest.mark.asyncio
async def test_fetch_google_trends_reports_http_and_parse_failures():
    error_client = _fake_client(
        _response("https://trends.google.com/trending/rss", text="nope", status=503)
    )
    with patch.object(google_trends_feed, "get_external_http_client", return_value=error_client):
        failed = await fetch_google_trends(limit=2, geo="US")
    assert failed["success"] is False
    assert "503" in failed["error"]

    garbage_client = _fake_client(
        _response("https://trends.google.com/trending/rss", text="<html>not rss")
    )
    with patch.object(google_trends_feed, "get_external_http_client", return_value=garbage_client):
        garbage = await fetch_google_trends(limit=2, geo="US")
    assert garbage["success"] is False

    empty_client = _fake_client(
        _response("https://trends.google.com/trending/rss", text="<rss><channel/></rss>")
    )
    with patch.object(google_trends_feed, "get_external_http_client", return_value=empty_client):
        empty = await fetch_google_trends(limit=2, geo="US")
    assert empty["success"] is False
    assert empty["trending"] == []


@pytest.mark.asyncio
async def test_fetch_steam_featured_requests_localized_store():
    client = _fake_client(
        _response(
            "https://store.steampowered.com/api/featuredcategories",
            json_payload=SAMPLE_STEAM_PAYLOAD,
        )
    )
    with patch.object(steam_store_feed, "get_external_http_client", return_value=client), patch.object(
        steam_store_feed, "get_global_language_full", return_value="ko"
    ):
        result = await fetch_steam_featured(limit=2)

    assert result["success"] is True
    assert [game["title"] for game in result["games"]] == ["Top Game A", "New Game A"]
    assert client.get.await_args.kwargs["params"] == {"l": "koreana", "cc": "kr"}


@pytest.mark.asyncio
async def test_fetch_steam_featured_reports_failures():
    with patch.object(
        steam_store_feed,
        "get_external_http_client",
        return_value=_fake_client(error=httpx.ConnectTimeout("timed out")),
    ):
        failed = await fetch_steam_featured(limit=2)
    assert failed["success"] is False
    assert "timed out" in failed["error"]

    with patch.object(
        steam_store_feed,
        "get_external_http_client",
        return_value=_fake_client(
            _response(
                "https://store.steampowered.com/api/featuredcategories",
                json_payload={"top_sellers": {"items": []}},
            )
        ),
    ):
        empty = await fetch_steam_featured(limit=2)
    assert empty["success"] is False
    assert empty["games"] == []


def _overseas_patches(*, twitter, google_trends, steam):
    return (
        patch("utils.web_scraper.trending_content.is_china_region", return_value=False),
        patch("utils.web_scraper.trending_content.fetch_twitter_trending", new=twitter),
        patch("utils.web_scraper.trending_content.fetch_google_trends", new=google_trends),
        patch("utils.web_scraper.trending_content.fetch_steam_featured", new=steam),
        patch("utils.web_scraper.trending_content.fetch_xhh_feed_content", new=AsyncMock()),
    )


GOOGLE_TRENDS_OK = {
    "success": True,
    "source": "google_trends",
    "geo": "US",
    "trending": parse_google_trends_rss(SAMPLE_TRENDS_RSS, limit=2),
}
STEAM_OK = {
    "success": True,
    "source": "steam",
    "games": normalize_steam_featured(SAMPLE_STEAM_PAYLOAD, limit=3),
}


@pytest.mark.asyncio
async def test_overseas_news_combines_google_trends_and_steam_when_twitter_fails():
    patches = _overseas_patches(
        twitter=AsyncMock(side_effect=RuntimeError("twitter down")),
        google_trends=AsyncMock(return_value=GOOGLE_TRENDS_OK),
        steam=AsyncMock(return_value=STEAM_OK),
    )
    with patches[0], patches[1], patches[2] as fetch_trends, patches[3] as fetch_steam, patches[4] as fetch_xhh:
        result = await fetch_news_content(limit=3)

    assert result["success"] is True
    assert result["news"]["success"] is False
    assert result["google_trends"] is GOOGLE_TRENDS_OK
    assert result["steam"] is STEAM_OK
    fetch_trends.assert_awaited_once_with(3)
    fetch_steam.assert_awaited_once_with(3)
    fetch_xhh.assert_not_awaited()

    formatted = format_news_content(result)
    assert "【Google Trends (US)】" in formatted
    assert "1. World Series (500K+ searches)" in formatted
    assert "Game 7 heads to extra innings - Example News" in formatted
    assert "【Steam Store】" in formatted
    assert "Shared Game (Top seller, 25% off)" in formatted
    assert "New Game A (New release)" in formatted
    assert "Twitter" not in formatted


@pytest.mark.asyncio
async def test_overseas_news_fails_only_when_every_source_fails():
    patches = _overseas_patches(
        twitter=AsyncMock(return_value={"success": False, "error": "twitter down"}),
        google_trends=AsyncMock(side_effect=RuntimeError("trends down")),
        steam=AsyncMock(return_value={"success": False, "error": "steam down", "games": []}),
    )
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = await fetch_news_content(limit=3)

    assert result["success"] is False
    for message in ("twitter down", "trends down", "steam down"):
        assert message in result["error"]
    assert format_news_content(result) == "Unable to fetch trending topics at the moment"


@pytest.mark.unit
def test_overseas_news_links_round_robin_all_sources():
    raw = {
        "region": "non-china",
        "news": {
            "trending": [
                {"word": f"tw-{index}", "url": f"https://twitter.com/{index}"}
                for index in range(2)
            ],
        },
        "google_trends": GOOGLE_TRENDS_OK,
        "steam": STEAM_OK,
    }

    links = _extract_links_from_raw("news", raw)

    assert [link["source"] for link in links[:6]] == [
        "Twitter",
        "Google Trends",
        "Steam",
        "Twitter",
        "Google Trends",
        "Steam",
    ]
    assert links[1]["url"] == "https://news.example.com/game7"
    assert links[2]["url"] == "https://store.steampowered.com/app/10/"
    assert links[-1]["title"] == "Shared Game"
