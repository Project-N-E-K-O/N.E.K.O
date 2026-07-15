from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from utils.web_scraper.trending_content import (
    fetch_xhh_feed_content,
    format_xhh_feed,
    normalize_xhh_feed,
)
from utils.web_scraper.personal_dynamics import (
    fetch_personal_dynamics,
    fetch_xhh_personal_dynamic,
    format_personal_dynamics,
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


def test_proactive_presets_route_xhh_through_personal_updates():
    from main_routers.proactive_router import PROACTIVE_PRESETS

    for mode in ("normal", "frequent"):
        assert PROACTIVE_PRESETS[mode]["proactivePersonalChatEnabled"] is True


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
    ):
        result = await fetch_xhh_feed_content(limit=1)

    assert result["success"] is True
    assert len(result["posts"]) == 1
    url, kwargs = client.call
    assert url == "https://api.xiaoheihe.cn/bbs/app/feeds"
    assert kwargs["params"]["pull"] == "1"
    assert kwargs["params"]["hkey"]
    assert kwargs["headers"]["Referer"] == "https://www.xiaoheihe.cn/"
    assert "Cookie" not in kwargs["headers"]


@pytest.mark.asyncio
async def test_fetch_xhh_personal_dynamic_injects_saved_credentials():
    client = _FakeClient()
    with patch(
        "utils.web_scraper.personal_dynamics.get_external_http_client",
        return_value=client,
    ), patch(
        "utils.web_scraper.personal_dynamics.load_cookies_from_file",
        return_value={"user_heybox_id": "123", "user_pkey": "secret"},
    ):
        result = await fetch_xhh_personal_dynamic(limit=1)

    assert result["success"] is True
    _, kwargs = client.call
    cookie_header = kwargs["headers"]["Cookie"]
    assert "user_heybox_id=123" in cookie_header
    assert "user_pkey=secret" in cookie_header
    assert "x_xhh_tokenid=" in cookie_header


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
    ):
        result = await fetch_xhh_feed_content()

    assert result["success"] is False
    assert result["posts"] == []
    assert "未返回可用帖子" in result["error"]


@pytest.mark.asyncio
async def test_personal_xhh_source_requires_saved_credentials():
    client = _FakeClient()
    with patch(
        "utils.web_scraper.personal_dynamics.get_external_http_client",
        return_value=client,
    ), patch(
        "utils.web_scraper.personal_dynamics.load_cookies_from_file",
        return_value={},
    ):
        result = await fetch_xhh_personal_dynamic(limit=1)

    assert result == {
        "success": False,
        "error": "未提供小黑盒认证信息",
        "posts": [],
    }
    assert client.call is None


@pytest.mark.asyncio
async def test_personal_dynamics_aggregates_xhh_account_homepage():
    failed = {"success": False, "error": "not connected"}
    xhh = {
        "success": True,
        "posts": SAMPLE_PAYLOAD["result"]["links"][:1],
    }
    with patch(
        "utils.web_scraper.personal_dynamics.is_china_region",
        return_value=True,
    ), patch(
        "utils.web_scraper.personal_dynamics.fetch_bilibili_personal_dynamic",
        new=AsyncMock(return_value=failed),
    ), patch(
        "utils.web_scraper.personal_dynamics.fetch_weibo_personal_dynamic",
        new=AsyncMock(return_value=failed),
    ), patch(
        "utils.web_scraper.personal_dynamics.fetch_douyin_personal_dynamic",
        new=AsyncMock(return_value=failed),
    ), patch(
        "utils.web_scraper.personal_dynamics.fetch_kuaishou_personal_dynamic",
        new=AsyncMock(return_value=failed),
    ), patch(
        "utils.web_scraper.personal_dynamics.fetch_xhh_personal_dynamic",
        new=AsyncMock(return_value=xhh),
    ) as fetch_xhh:
        result = await fetch_personal_dynamics(limit=3)

    assert result["success"] is True
    assert result["xhh_dynamic"] is xhh
    fetch_xhh.assert_awaited_once_with(limit=3)
    assert "小黑盒账号首页" in format_personal_dynamics(result)
    assert "今天玩什么游戏" in format_personal_dynamics(result)


def test_personal_links_round_robin_xhh_into_shared_candidate_pool():
    def items(prefix: str, count: int, key: str = "content"):
        return [
            {key: f"{prefix}-{index}", "url": f"https://example.com/{prefix}/{index}"}
            for index in range(count)
        ]

    raw = {
        "region": "china",
        "bilibili_dynamic": {"dynamics": items("bili", 10)},
        "weibo_dynamic": {"statuses": items("weibo", 10)},
        "douyin_dynamic": {"dynamics": items("douyin", 10)},
        "kuaishou_dynamic": {"dynamics": items("kuaishou", 10)},
        "xhh_dynamic": {"posts": items("xhh", 10, key="title")},
    }

    links = _extract_links_from_raw("personal", raw)

    assert [link["source"] for link in links[:5]] == [
        "B站", "微博", "抖音", "快手", "小黑盒"
    ]
    assert any(link["source"] == "小黑盒" for link in links[:12])


def test_xhh_is_hidden_as_a_standalone_menu_mode():
    root = Path(__file__).resolve().parents[2]
    menu_source = (root / "static/avatar/avatar-ui-drag.js").read_text(encoding="utf-8")
    proactive_source = (root / "static/app/app-proactive.js").read_text(encoding="utf-8")

    assert "mode: 'xhh'" not in menu_source
    assert "availableModes.push('xhh')" not in proactive_source
    assert "availableModes.push('personal')" in proactive_source
