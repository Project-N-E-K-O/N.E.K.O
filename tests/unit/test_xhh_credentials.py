from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException

from main_routers.cookies_login_router import (
    _get_xhh_qr_code,
    _poll_xhh_qr_login,
    validate_platform_fields,
)


def _response_with_cookies(cookies: dict[str, str]) -> httpx.Response:
    response = httpx.Response(200, request=httpx.Request("GET", "https://example.test"))
    for key, value in cookies.items():
        response.cookies.set(key, value)
    return response


def test_xhh_manual_credentials_require_core_fields():
    validate_platform_fields(
        "xhh", {"user_heybox_id": "123", "heybox_token": "secret"}
    )

    with pytest.raises(HTTPException, match="heybox_token"):
        validate_platform_fields("xhh", {"user_heybox_id": "123"})


@pytest.mark.asyncio
async def test_get_xhh_qr_code_extracts_state_and_renders_image():
    payload = {
        "status": "ok",
        "result": {
            "qr_url": "https://www.xiaoheihe.cn/qr?state=abc&os_type=web",
            "expire": 120,
        },
    }
    with patch(
        "main_routers.cookies_login_router._request_xhh_qr",
        new=AsyncMock(return_value=(_response_with_cookies({}), payload)),
    ):
        result = await _get_xhh_qr_code()

    assert result["success"] is True
    assert result["data"]["qrcode_key"] == "state=abc&os_type=web"
    assert result["data"]["timeout"] == 120
    assert result["data"]["qrcode_image"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_poll_xhh_qr_reports_waiting_state():
    payload = {"status": "ok", "result": {"error": "pending", "error_msg": "等待扫码"}}
    with patch(
        "main_routers.cookies_login_router._request_xhh_qr",
        new=AsyncMock(return_value=(_response_with_cookies({}), payload)),
    ):
        result = await _poll_xhh_qr_login("state=abc")

    assert result == {
        "success": False,
        "data": {"code": "pending", "status": "waiting", "message": "等待扫码"},
    }


@pytest.mark.asyncio
async def test_poll_xhh_qr_saves_and_returns_credentials():
    response = _response_with_cookies(
        {"user_heybox_id": "123", "heybox_token": "secret"}
    )
    payload = {"status": "ok", "result": {"error": "ok", "nickname": "盒友"}}
    with patch(
        "main_routers.cookies_login_router._request_xhh_qr",
        new=AsyncMock(return_value=(response, payload)),
    ), patch(
        "main_routers.cookies_login_router.build_xhh_token_id",
        return_value="token-id",
    ), patch(
        "main_routers.cookies_login_router.save_cookies_to_file",
        return_value=True,
    ) as save_mock:
        result = await _poll_xhh_qr_login("state=abc")

    assert result["success"] is True
    assert result["data"]["status"] == "success"
    assert result["data"]["cookies"]["x_xhh_tokenid"] == "token-id"
    save_mock.assert_called_once_with(
        "xhh",
        {
            "user_heybox_id": "123",
            "heybox_token": "secret",
            "x_xhh_tokenid": "token-id",
        },
    )
