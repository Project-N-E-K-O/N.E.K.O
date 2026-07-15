from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from utils.web_scraper.trending_content import (
    fetch_news_content,
    fetch_xhh_feed_content,
    format_news_content,
    format_xhh_feed,
    normalize_xhh_feed,
)
from main_routers.system_router.proactive_parsing import _extract_links_from_raw
from utils.web_scraper.platform_helpers import (
    build_xhh_cookie_header,
    build_xhh_request_keys,
    build_xhh_token_id,
)


SAMPLE_PAYLOAD = {
    "status": "ok",
    "result": {
        "links": [
            {
                "linkid": 181099114,
                "title": "  今天玩什么游戏？  ",
                "description": " 一起聊聊最近在玩的游戏。\n",
                "create_at": 1710000000,
                "user": {"username": "盒友甲"},
                "topics": [{"name": "游戏"}],
                "hashtags": [{"name": "闲聊"}],
            },
            {
                "linkid": 181099114,
                "title": "重复帖子",
            },
            {"linkid": 2, "title": ""},
        ]
    },
}


def test_proactive_presets_route_xhh_through_news():
    from main_routers.proactive_router import PROACTIVE_PRESETS

    for mode in ("normal", "frequent"):
        assert PROACTIVE_PRESETS[mode]["proactiveNewsChatEnabled"] is True


def test_build_xhh_request_keys_matches_openxhh_vector():
    assert build_xhh_request_keys(
        "/bbs/app/feeds",
        timestamp=1710000000,
        nonce="0123456789ABCDEF0123456789ABCDEF",
    ) == ("TUD7U74", "0123456789ABCDEF0123456789ABCDEF", 1710000000)


def test_build_xhh_token_and_cookie_header():
    token = build_xhh_token_id(timestamp=1710000000)

    assert len(base64.b64decode(token)) == 65
    header = build_xhh_cookie_header(
        {"user_heybox_id": "123", "user_pkey": "secret"}
    )
    assert "user_heybox_id=123" in header
    assert "user_pkey=secret" in header
    assert "x_xhh_tokenid=" in header


def test_build_xhh_cookie_header_replaces_saved_token():
    with patch(
        "utils.web_scraper.platform_helpers.build_xhh_token_id",
        return_value="fresh-token",
    ):
        header = build_xhh_cookie_header(
            {"user_heybox_id": "123", "x_xhh_tokenid": "stale-token"}
        )

    assert "x_xhh_tokenid=fresh-token" in header
    assert "stale-token" not in header


def test_normalize_and_format_xhh_feed():
    posts = normalize_xhh_feed(SAMPLE_PAYLOAD, limit=10)

    assert posts == [
        {
            "link_id": 181099114,
            "title": "今天玩什么游戏？",
            "description": "一起聊聊最近在玩的游戏。",
            "author": "盒友甲",
            "topics": ["游戏"],
            "tags": ["闲聊"],
            "url": "https://www.xiaoheihe.cn/app/bbs/link/181099114",
            "create_at": 1710000000,
        }
    ]
    formatted = format_xhh_feed(posts)
    assert "今天玩什么游戏？" in formatted
    assert "作者: 盒友甲" in formatted
    assert "话题: 游戏、闲聊" in formatted


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self):
        return SAMPLE_PAYLOAD


class _FakeClient:
    def __init__(self):
        self.call = None

    async def get(self, url, **kwargs):
        self.call = (url, kwargs)
        return _FakeResponse()


@pytest.mark.asyncio
async def test_fetch_xhh_feed_uses_read_only_public_endpoint():
    client = _FakeClient()
    with patch(
        "utils.web_scraper.trending_content.get_external_http_client",
        return_value=client,
    ), patch(
        "utils.web_scraper.trending_content.load_cookies_from_file",
        return_value={},
    ):
        result = await fetch_xhh_feed_content(limit=1)

    assert result["success"] is True
    assert result["authenticated"] is False
    assert len(result["posts"]) == 1
    url, kwargs = client.call
    assert url == "https://api.xiaoheihe.cn/bbs/app/feeds"
    assert kwargs["params"]["pull"] == "1"
    assert kwargs["params"]["hkey"]
    assert kwargs["headers"]["Referer"] == "https://www.xiaoheihe.cn/"
    assert "Cookie" not in kwargs["headers"]


@pytest.mark.asyncio
async def test_fetch_xhh_feed_injects_saved_credentials_when_available():
    client = _FakeClient()
    with patch(
        "utils.web_scraper.trending_content.get_external_http_client",
        return_value=client,
    ), patch(
        "utils.web_scraper.trending_content.load_cookies_from_file",
        return_value={"user_heybox_id": "123", "user_pkey": "secret"},
    ):
        result = await fetch_xhh_feed_content(limit=1)

    assert result["success"] is True
    assert result["authenticated"] is True
    _, kwargs = client.call
    cookie_header = kwargs["headers"]["Cookie"]
    assert "user_heybox_id=123" in cookie_header
    assert "user_pkey=secret" in cookie_header
    assert "x_xhh_tokenid=" in cookie_header


@pytest.mark.asyncio
async def test_fetch_xhh_feed_falls_back_to_public_when_credentials_fail():
    class AuthFailedResponse(_FakeResponse):
        def json(self):
            return {"status": "fail", "message": "credential expired"}

    class FallbackClient(_FakeClient):
        def __init__(self):
            super().__init__()
            self.calls = []

        async def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return AuthFailedResponse() if len(self.calls) == 1 else _FakeResponse()

    client = FallbackClient()
    with patch(
        "utils.web_scraper.trending_content.get_external_http_client",
        return_value=client,
    ), patch(
        "utils.web_scraper.trending_content.load_cookies_from_file",
        return_value={"user_heybox_id": "123", "user_pkey": "expired"},
    ):
        result = await fetch_xhh_feed_content(limit=1)

    assert result["success"] is True
    assert result["authenticated"] is False
    assert len(client.calls) == 2
    assert "Cookie" in client.calls[0][1]["headers"]
    assert "Cookie" not in client.calls[1][1]["headers"]


@pytest.mark.asyncio
async def test_fetch_xhh_feed_reports_empty_payload_as_source_failure():
    class EmptyResponse(_FakeResponse):
        def json(self):
            return {"status": "ok", "result": {"links": []}}

    class EmptyClient(_FakeClient):
        async def get(self, url, **kwargs):
            self.call = (url, kwargs)
            return EmptyResponse()

    with patch(
        "utils.web_scraper.trending_content.get_external_http_client",
        return_value=EmptyClient(),
    ), patch(
        "utils.web_scraper.trending_content.load_cookies_from_file",
        return_value={},
    ):
        result = await fetch_xhh_feed_content()

    assert result["success"] is False
    assert result["posts"] == []
    assert "未返回可用帖子" in result["error"]


@pytest.mark.asyncio
async def test_news_aggregates_weibo_and_xhh():
    weibo = {
        "success": True,
        "trending": [{"word": "微博话题", "url": "https://s.weibo.com/topic"}],
    }
    xhh = {
        "success": True,
        "posts": normalize_xhh_feed(SAMPLE_PAYLOAD, limit=1),
    }
    with patch(
        "utils.web_scraper.trending_content.is_china_region",
        return_value=True,
    ), patch(
        "utils.web_scraper.trending_content.fetch_weibo_trending",
        new=AsyncMock(return_value=weibo),
    ), patch(
        "utils.web_scraper.trending_content.fetch_xhh_feed_content",
        new=AsyncMock(return_value=xhh),
    ) as fetch_xhh:
        result = await fetch_news_content(limit=3)

    assert result["success"] is True
    assert result["news"] is weibo
    assert result["xhh"] is xhh
    fetch_xhh.assert_awaited_once_with(3)
    formatted = format_news_content(result)
    assert "微博话题" in formatted
    assert "今天玩什么游戏" in formatted


@pytest.mark.asyncio
async def test_news_keeps_xhh_source_outside_china_region():
    twitter = {
        "success": True,
        "trending": [{"word": "#topic", "url": "https://twitter.com/topic"}],
    }
    xhh = {"success": True, "posts": normalize_xhh_feed(SAMPLE_PAYLOAD, limit=1)}
    with patch(
        "utils.web_scraper.trending_content.is_china_region",
        return_value=False,
    ), patch(
        "utils.web_scraper.trending_content.fetch_twitter_trending",
        new=AsyncMock(return_value=twitter),
    ), patch(
        "utils.web_scraper.trending_content.fetch_xhh_feed_content",
        new=AsyncMock(return_value=xhh),
    ):
        result = await fetch_news_content(limit=2)

    assert result["region"] == "non-china"
    assert result["news"] is twitter
    assert result["xhh"] is xhh
    assert "Xiaoheihe Home" in format_news_content(result)


def test_news_links_round_robin_weibo_and_xhh():
    raw = {
        "region": "china",
        "news": {
            "trending": [
                {"word": f"weibo-{index}", "url": f"https://weibo/{index}"}
                for index in range(10)
            ],
        },
        "xhh": {
            "posts": [
                {"title": f"xhh-{index}", "url": f"https://xhh/{index}"}
                for index in range(10)
            ],
        },
    }

    links = _extract_links_from_raw("news", raw)

    assert [link["source"] for link in links[:4]] == ["微博", "小黑盒", "微博", "小黑盒"]
    assert any(link["source"] == "小黑盒" for link in links[:12])


def test_xhh_is_hidden_as_a_standalone_menu_mode():
    root = Path(__file__).resolve().parents[2]
    menu_source = (root / "static/avatar/avatar-ui-drag.js").read_text(encoding="utf-8")
    proactive_source = (root / "static/app/app-proactive.js").read_text(encoding="utf-8")

    assert "mode: 'xhh'" not in menu_source
    assert "availableModes.push('xhh')" not in proactive_source
    assert "availableModes.push('news')" in proactive_source
