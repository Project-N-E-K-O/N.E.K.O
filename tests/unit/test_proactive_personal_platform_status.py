from pathlib import Path
from unittest.mock import patch

import pytest

from main_routers.cookies_login_router import (
    PERSONAL_DYNAMIC_PLATFORMS,
    delete_platform_cookies,
    get_all_cookies_status,
)


@pytest.mark.asyncio
async def test_cookie_status_marks_only_personal_dynamic_platforms():
    platforms = {
        "netease": {},
        "xhh": {},
        "bilibili": {},
        "weibo": {},
    }

    with (
        patch(
            "main_routers.cookies_login_router.login_manager.get_supported_platforms",
            return_value=platforms,
        ),
        patch(
            "main_routers.cookies_login_router.credential_manager.status",
            side_effect=lambda platform: {
                "has_cookies": True,
                "cookies_count": 1,
                "credential_state": "ready",
            },
        ),
    ):
        response = await get_all_cookies_status()

    data = response["data"]
    assert data["netease"]["has_cookies"] is True
    assert data["netease"]["supports_personal_dynamic"] is False
    assert data["xhh"]["supports_personal_dynamic"] is False
    assert data["bilibili"]["supports_personal_dynamic"] is True
    assert data["weibo"]["supports_personal_dynamic"] is True


def test_personal_dynamic_platform_contract_matches_scraper_sources():
    assert PERSONAL_DYNAMIC_PLATFORMS == {
        "bilibili",
        "douyin",
        "kuaishou",
        "weibo",
        "reddit",
        "twitter",
    }

    root = Path(__file__).resolve().parents[2]
    source = (root / "static/app/app-proactive.js").read_text(encoding="utf-8")
    assert "info.has_cookies && info.supports_personal_dynamic === true" in source


@pytest.mark.asyncio
async def test_delete_route_uses_manager_atomic_delete():
    with (
        patch(
            "main_routers.cookies_login_router.login_manager.get_supported_platforms",
            return_value={"weibo": {}},
        ),
        patch(
            "main_routers.cookies_login_router.credential_manager.delete_stored_credentials",
            return_value=(True, True),
        ) as delete,
    ):
        response = await delete_platform_cookies("weibo")

    delete.assert_called_once_with("weibo")
    assert response["success"] is True
