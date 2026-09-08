import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _RequestStub:
    def __init__(self, payload, *, origin="http://testserver"):
        self._payload = payload
        self.headers = {"origin": origin}
        self.base_url = "http://testserver/"

    async def json(self):
        return self._payload


async def _reset_lifecycle(web_app) -> None:
    web_app._reset_browser_lifecycle_state()
    await asyncio.sleep(0)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_browser_refresh_replaces_page_lease_without_stopping_backend():
    from app.main_server import web_app

    await _reset_lifecycle(web_app)
    shutdown = AsyncMock()
    try:
        with (
            patch.object(web_app, "_BROWSER_LIFECYCLE_CLOSE_GRACE_SECONDS", 0.02),
            patch.object(web_app.runtime, "shutdown_server_async", shutdown),
        ):
            assert web_app._touch_browser_lifecycle_client("old-document") is True
            web_app._release_browser_lifecycle_client("old-document")
            await asyncio.sleep(0)
            assert web_app._touch_browser_lifecycle_client("replacement-document") is True
            await asyncio.sleep(0.04)

        shutdown.assert_not_awaited()
    finally:
        await _reset_lifecycle(web_app)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_browser_close_stops_backend_after_last_page_releases():
    from app.main_server import web_app

    await _reset_lifecycle(web_app)
    shutdown = AsyncMock()
    try:
        with (
            patch.object(web_app, "_BROWSER_LIFECYCLE_CLOSE_GRACE_SECONDS", 0.01),
            patch.object(web_app.runtime, "shutdown_server_async", shutdown),
        ):
            assert web_app._touch_browser_lifecycle_client("page-a") is True
            assert web_app._touch_browser_lifecycle_client("page-b") is True
            web_app._release_browser_lifecycle_client("page-a")
            await asyncio.sleep(0.02)
            shutdown.assert_not_awaited()

            web_app._release_browser_lifecycle_client("page-b")
            await asyncio.sleep(0.03)

        shutdown.assert_awaited_once_with()
    finally:
        await _reset_lifecycle(web_app)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_browser_heartbeat_timeout_cleans_up_after_page_crash():
    from app.main_server import web_app

    await _reset_lifecycle(web_app)
    shutdown = AsyncMock()
    try:
        with (
            patch.object(web_app, "_BROWSER_LIFECYCLE_HEARTBEAT_TIMEOUT_SECONDS", 0.01),
            patch.object(web_app, "_BROWSER_LIFECYCLE_CLOSE_GRACE_SECONDS", 0.01),
            patch.object(web_app.runtime, "shutdown_server_async", shutdown),
        ):
            assert web_app._touch_browser_lifecycle_client("crashed-page") is True
            await asyncio.sleep(0.04)

        shutdown.assert_awaited_once_with()
    finally:
        await _reset_lifecycle(web_app)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_packaged_mode_ignores_browser_lifecycle_signal():
    from app.main_server import web_app

    await _reset_lifecycle(web_app)
    shutdown = AsyncMock()
    try:
        with (
            patch.object(
                web_app.runtime,
                "get_start_config",
                return_value={"browser_mode_enabled": False},
            ),
            patch.object(web_app.runtime, "shutdown_server_async", shutdown),
        ):
            response = await web_app.beacon_shutdown(
                _RequestStub({"action": "release", "client_id": "steam-page"})
            )
            await asyncio.sleep(0)

        assert response == {
            "success": True,
            "ignored": True,
            "reason": "browser_mode_disabled",
        }
        assert web_app._browser_lifecycle_clients == {}
        shutdown.assert_not_awaited()
    finally:
        await _reset_lifecycle(web_app)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_browser_mode_endpoint_closes_after_registered_page_releases():
    from app.main_server import web_app

    await _reset_lifecycle(web_app)
    shutdown = AsyncMock()
    try:
        with (
            patch.object(
                web_app.runtime,
                "get_start_config",
                return_value={"browser_mode_enabled": True},
            ),
            patch.object(web_app, "_BROWSER_LIFECYCLE_CLOSE_GRACE_SECONDS", 0.01),
            patch.object(web_app.runtime, "shutdown_server_async", shutdown),
        ):
            register_response = await web_app.beacon_shutdown(
                _RequestStub({"action": "register", "client_id": "source-page"})
            )
            release_response = await web_app.beacon_shutdown(
                _RequestStub({"action": "release", "client_id": "source-page"})
            )
            await asyncio.sleep(0.03)

        assert register_response["success"] is True
        assert release_response["success"] is True
        shutdown.assert_awaited_once_with()
    finally:
        await _reset_lifecycle(web_app)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_browser_mode_rejects_cross_origin_lifecycle_signal():
    from app.main_server import web_app

    await _reset_lifecycle(web_app)
    shutdown = AsyncMock()
    try:
        with (
            patch.object(
                web_app.runtime,
                "get_start_config",
                return_value={"browser_mode_enabled": True},
            ),
            patch.object(web_app.runtime, "shutdown_server_async", shutdown),
        ):
            response = await web_app.beacon_shutdown(
                _RequestStub(
                    {"action": "register", "client_id": "foreign-page"},
                    origin="https://example.invalid",
                )
            )

        assert response.status_code == 403
        assert web_app._browser_lifecycle_clients == {}
        shutdown.assert_not_awaited()
    finally:
        await _reset_lifecycle(web_app)


@pytest.mark.unit
def test_browser_lifecycle_script_is_shared_by_all_standalone_pages():
    lifecycle = (
        PROJECT_ROOT / "static/js/browser-mode-lifecycle.js"
    ).read_text(encoding="utf-8")
    assert "sendSignal('register', false)" in lifecycle
    assert "sendSignal('heartbeat', false)" in lifecycle
    assert "sendSignal('release', true)" in lifecycle
    assert "payload.ignored === true" in lifecycle

    for template_name in (
        "index.html",
        "character_card_manager.html",
        "api_key_settings.html",
    ):
        template = (PROJECT_ROOT / "templates" / template_name).read_text(
            encoding="utf-8"
        )
        assert "/static/js/browser-mode-lifecycle.js" in template

    character_lifecycle = (
        PROJECT_ROOT / "static/js/character_card_manager/sync-and-legacy-memory.js"
    ).read_text(encoding="utf-8")
    api_key_settings = (PROJECT_ROOT / "static/js/api_key_settings.js").read_text(
        encoding="utf-8"
    )
    assert "/api/beacon/shutdown" not in character_lifecycle
    assert "/api/beacon/shutdown" not in api_key_settings
