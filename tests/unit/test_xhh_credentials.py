from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException

from main_routers.cookies_login_router import (
    _get_xhh_qr_code,
    _poll_xhh_qr_login,
    validate_platform_fields,
)
from utils import cookies_login
from utils.cookies_login import _read_encryption_key, _write_encryption_key
from utils.cookies_login import validate_cookies


def test_xhh_credential_tab_is_present():
    template = Path("templates/cookies_login.html").read_text(encoding="utf-8")

    assert "switchTab('xhh', this)" in template
    assert 'data-i18n="cookiesLogin.xhh"' in template


def _response_with_cookies(cookies: dict[str, str]) -> httpx.Response:
    response = httpx.Response(200, request=httpx.Request("GET", "https://example.test"))
    for key, value in cookies.items():
        response.cookies.set(key, value)
    return response


def test_xhh_manual_credentials_require_core_fields():
    validate_platform_fields(
        "xhh", {"user_heybox_id": "123", "user_pkey": "secret"}
    )
    assert validate_cookies(
        "xhh", {"user_heybox_id": "123", "user_pkey": "secret"}
    )

    with pytest.raises(HTTPException, match="user_pkey"):
        validate_platform_fields("xhh", {"user_heybox_id": "123"})
    assert not validate_cookies("xhh", {"user_heybox_id": "123"})


def test_xhh_encryption_key_uses_json(tmp_path: Path):
    key_file = tmp_path / "xhh_key.json"
    key = b"test-fernet-key"

    _write_encryption_key("xhh", key_file, key)

    assert _read_encryption_key("xhh", key_file) == key
    assert '"key": "test-fernet-key"' in key_file.read_text(encoding="utf-8")


def test_xhh_encryption_key_rejects_invalid_json_payload(tmp_path: Path):
    key_file = tmp_path / "xhh_key.json"
    key_file.write_text('{"unexpected": "value"}', encoding="utf-8")

    with pytest.raises(ValueError, match="key 字段"):
        _read_encryption_key("xhh", key_file)


def test_xhh_encrypted_credentials_round_trip_with_json_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    cookie_file = tmp_path / "xhh_cookies.json"
    key_file = tmp_path / "xhh_key.json"
    monkeypatch.setattr(cookies_login, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        cookies_login,
        "COOKIE_FILES",
        {"xhh": cookie_file, "xhh_key": key_file},
    )
    credentials = {"user_heybox_id": "123", "user_pkey": "secret"}

    assert cookies_login.save_cookies_to_file("xhh", credentials)
    assert key_file.exists()
    assert not (tmp_path / "xhh_key.key").exists()
    assert cookies_login.load_cookies_from_file("xhh") == credentials


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
async def test_poll_xhh_qr_rejects_top_level_error_before_waiting_state():
    payload = {"status": "error", "msg": "服务暂不可用"}
    with patch(
        "main_routers.cookies_login_router._request_xhh_qr",
        new=AsyncMock(return_value=(_response_with_cookies({}), payload)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _poll_xhh_qr_login("state=abc")

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "服务暂不可用"


@pytest.mark.asyncio
async def test_poll_xhh_qr_saves_and_returns_credentials():
    response = _response_with_cookies(
        {"user_heybox_id": "123", "user_pkey": "secret"}
    )
    payload = {"status": "ok", "result": {"error": "ok", "nickname": "盒友"}}
    with patch(
        "main_routers.cookies_login_router._request_xhh_qr",
        new=AsyncMock(return_value=(response, payload)),
    ), patch(
        "main_routers.cookies_login_router.save_cookies_to_file",
        return_value=True,
    ) as save_mock:
        result = await _poll_xhh_qr_login("state=abc")

    assert result["success"] is True
    assert result["data"]["status"] == "success"
    assert result["data"]["local_save_failed"] is False
    save_mock.assert_called_once_with(
        "xhh",
        {
            "user_heybox_id": "123",
            "user_pkey": "secret",
        },
    )


@pytest.mark.asyncio
async def test_poll_xhh_qr_returns_credentials_when_local_save_fails():
    response = _response_with_cookies(
        {"user_heybox_id": "123", "user_pkey": "secret"}
    )
    payload = {"status": "ok", "result": {"error": "ok", "nickname": "盒友"}}
    with patch(
        "main_routers.cookies_login_router._request_xhh_qr",
        new=AsyncMock(return_value=(response, payload)),
    ), patch(
        "main_routers.cookies_login_router.save_cookies_to_file",
        return_value=False,
    ), patch("main_routers.cookies_login_router.logger.warning") as warning_mock:
        result = await _poll_xhh_qr_login("state=abc")

    assert result["success"] is True
    assert result["data"]["local_save_failed"] is True
    warning_mock.assert_called_once_with("⚠️ 小黑盒登录凭证自动保存失败 (不影响登录)")
    assert result["data"]["cookies"] == {
        "user_heybox_id": "123",
        "user_pkey": "secret",
    }


@pytest.mark.asyncio
async def test_poll_xhh_qr_logs_once_when_local_save_raises():
    response = _response_with_cookies(
        {"user_heybox_id": "123", "user_pkey": "secret"}
    )
    payload = {"status": "ok", "result": {"error": "ok", "nickname": "盒友"}}
    with patch(
        "main_routers.cookies_login_router._request_xhh_qr",
        new=AsyncMock(return_value=(response, payload)),
    ), patch(
        "main_routers.cookies_login_router.save_cookies_to_file",
        side_effect=OSError("disk full"),
    ), patch("main_routers.cookies_login_router.logger.warning") as warning_mock:
        result = await _poll_xhh_qr_login("state=abc")

    assert result["success"] is True
    assert result["data"]["local_save_failed"] is True
    warning_mock.assert_called_once_with(
        "⚠️ 小黑盒登录凭证自动保存异常 (不影响登录): OSError"
    )
