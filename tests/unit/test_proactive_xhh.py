from __future__ import annotations

import base64
from unittest.mock import patch

import pytest

from main_routers.system_router.proactive_xhh import (
    build_xhh_cookie_header,
    build_xhh_request_keys,
    build_xhh_token_id,
    fetch_xhh_feed_content,
    format_xhh_feed,
    normalize_xhh_feed,
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
        {"user_heybox_id": "123", "heybox_token": "secret"}
    )
    assert "user_heybox_id=123" in header
    assert "heybox_token=secret" in header
    assert "x_xhh_tokenid=" in header


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
        "main_routers.system_router.proactive_xhh.get_external_http_client",
        return_value=client,
    ), patch(
        "main_routers.system_router.proactive_xhh.load_cookies_from_file",
        return_value={},
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
async def test_fetch_xhh_feed_injects_saved_credentials():
    client = _FakeClient()
    with patch(
        "main_routers.system_router.proactive_xhh.get_external_http_client",
        return_value=client,
    ), patch(
        "main_routers.system_router.proactive_xhh.load_cookies_from_file",
        return_value={"user_heybox_id": "123", "heybox_token": "secret"},
    ):
        result = await fetch_xhh_feed_content(limit=1)

    assert result["success"] is True
    _, kwargs = client.call
    cookie_header = kwargs["headers"]["Cookie"]
    assert "user_heybox_id=123" in cookie_header
    assert "heybox_token=secret" in cookie_header
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
        "main_routers.system_router.proactive_xhh.get_external_http_client",
        return_value=EmptyClient(),
    ), patch(
        "main_routers.system_router.proactive_xhh.load_cookies_from_file",
        return_value={},
    ):
        result = await fetch_xhh_feed_content()

    assert result["success"] is False
    assert result["posts"] == []
    assert "未返回可用帖子" in result["error"]
