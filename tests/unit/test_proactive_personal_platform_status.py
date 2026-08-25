from pathlib import Path
from unittest.mock import patch

import pytest

from main_routers.cookies_login_router import (
    PERSONAL_DYNAMIC_PLATFORMS,
    _load_cookie_status_cached,
    get_all_cookies_status,
)


@pytest.fixture(autouse=True)
def clear_cookie_status_cache():
    _load_cookie_status_cached.cache_clear()
    yield
    _load_cookie_status_cached.cache_clear()


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
            "main_routers.cookies_login_router.load_cookies_from_file",
            side_effect=lambda platform: {"credential": platform},
        ),
    ):
        response = await get_all_cookies_status()

    data = response["data"]
    assert data["netease"]["has_cookies"] is True
    assert data["netease"]["supports_personal_dynamic"] is False
    assert data["xhh"]["supports_personal_dynamic"] is False
    assert data["bilibili"]["supports_personal_dynamic"] is True
    assert data["weibo"]["supports_personal_dynamic"] is True


@pytest.mark.asyncio
async def test_cookie_status_reuses_unchanged_credential_snapshot(tmp_path):
    cookie_file = tmp_path / "bilibili_cookies.json"
    key_file = tmp_path / "bilibili_key.key"
    cookie_file.write_bytes(b"encrypted")
    key_file.write_bytes(b"key")

    with (
        patch(
            "main_routers.cookies_login_router.login_manager.get_supported_platforms",
            return_value={"bilibili": {}},
        ),
        patch.dict(
            "main_routers.cookies_login_router.COOKIE_FILES",
            {"bilibili": cookie_file},
        ),
        patch(
            "main_routers.cookies_login_router.get_cookie_key_file",
            return_value=key_file,
        ),
        patch(
            "main_routers.cookies_login_router.load_cookies_from_file",
            return_value={"SESSDATA": "credential"},
        ) as load_cookies,
    ):
        first = await get_all_cookies_status()
        second = await get_all_cookies_status()
        cookie_file.write_bytes(b"changed encrypted payload")
        third = await get_all_cookies_status()

    assert first == second == third
    assert load_cookies.call_count == 2


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
    assert "const PERSONAL_PLATFORM_EMPTY_CACHE_MS = 5 * 60 * 1000;" in source
    assert "if (_personalPlatformsRequest) return _personalPlatformsRequest;" in source
    assert "window.addEventListener('focus', function ()" in source
