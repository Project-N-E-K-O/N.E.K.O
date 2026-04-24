import pytest
from playwright.sync_api import Page, expect


def _arm_page_config_resolution_probe(page: Page) -> None:
    page.evaluate(
        """
        () => {
            window.__nekoPageConfigResolved = false;
            if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
                window.pageConfigReady.then(() => {
                    window.__nekoPageConfigResolved = true;
                });
            }
        }
        """
    )


def _page_config_state(page: Page, timeout_ms: int = 250) -> str:
    return page.evaluate(
        """
        async (timeoutMs) => {
            if (!window.pageConfigReady || typeof window.pageConfigReady.then !== 'function') {
                return 'missing';
            }
            return await Promise.race([
                window.pageConfigReady.then(() => 'resolved'),
                new Promise((resolve) => setTimeout(() => resolve('pending'), timeoutMs)),
            ]);
        }
        """,
        timeout_ms,
    )


@pytest.mark.frontend
def test_storage_location_overlay_blocks_page_config_until_current_path_confirmed(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    overlay = page.locator("#storage-location-overlay")
    selection_title = page.get_by_role("heading", name="请选择本次运行使用的存储位置")
    keep_current_button = page.get_by_role("button", name="保持当前路径")

    expect(overlay).to_be_visible(timeout=15_000)
    expect(selection_title).to_be_visible(timeout=15_000)

    _arm_page_config_resolution_probe(page)
    assert _page_config_state(page) == "pending"

    keep_current_button.click()

    expect(overlay).to_be_hidden(timeout=10_000)
    page.wait_for_function(
        "() => window.__nekoPageConfigResolved === true",
        timeout=10_000,
    )


@pytest.mark.frontend
def test_storage_location_overlay_blocks_independent_startup_requests_while_barrier_is_pending(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    page_config_requests = {"count": 0}
    playtime_requests = {"count": 0}

    def handle_page_config(route):
        page_config_requests["count"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body="""
            {
              "success": true,
              "lanlan_name": "Test",
              "model_path": "",
              "model_type": "live2d"
            }
            """,
        )

    def handle_playtime(route):
        playtime_requests["count"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body='{"totalPlayTime": 0}',
        )

    page.route("**/api/config/page_config**", handle_page_config)
    page.route("**/api/steam/update-playtime", handle_playtime)

    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    overlay = page.locator("#storage-location-overlay")
    selection_title = page.get_by_role("heading", name="请选择本次运行使用的存储位置")

    expect(overlay).to_be_visible(timeout=15_000)
    expect(selection_title).to_be_visible(timeout=15_000)

    page.wait_for_timeout(800)
    assert _page_config_state(page) == "pending"
    assert page_config_requests["count"] == 0
    assert playtime_requests["count"] == 0


@pytest.mark.frontend
def test_storage_location_overlay_keeps_page_config_blocked_on_restart_required_preview(
    mock_page: Page,
    running_server: str,
    tmp_path,
):
    page = mock_page
    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    overlay = page.locator("#storage-location-overlay")
    selection_title = page.get_by_role("heading", name="请选择本次运行使用的存储位置")
    choose_other_button = page.get_by_role("button", name="选择其他位置")
    submit_other_button = page.get_by_role("button", name="提交该位置")
    custom_input = page.locator(".storage-location-input")
    preview_title = page.get_by_role("heading", name="该选择需要后续关闭并迁移")

    expect(overlay).to_be_visible(timeout=15_000)
    expect(selection_title).to_be_visible(timeout=15_000)

    _arm_page_config_resolution_probe(page)
    assert _page_config_state(page) == "pending"

    choose_other_button.click()
    expect(custom_input).to_be_visible(timeout=5_000)

    target_root = tmp_path / "alt-storage" / "N.E.K.O"
    custom_input.fill(str(target_root))
    submit_other_button.click()

    expect(preview_title).to_be_visible(timeout=10_000)
    assert _page_config_state(page) == "pending"


@pytest.mark.frontend
def test_storage_location_overlay_stays_open_for_recovery_required_state_even_if_first_run_selection_flag_is_false(
    mock_page: Page,
    running_server: str,
):
    page = mock_page

    page.route(
        "**/api/v1/system/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body="""
            {
              "ok": true,
              "status": "migration_required",
              "ready": false,
              "storage": {
                "selection_required": false,
                "migration_pending": false,
                "recovery_required": true,
                "stage": "stage2_web_selection"
              }
            }
            """,
        ),
    )
    page.route(
        "**/api/storage/location/bootstrap",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body="""
            {
              "current_root": "/tmp/runtime/N.E.K.O",
              "recommended_root": "/tmp/runtime/N.E.K.O",
              "legacy_sources": [],
              "anchor_root": "/tmp/runtime/N.E.K.O",
              "cloudsave_root": "/tmp/runtime/N.E.K.O/cloudsave",
              "selection_required": false,
              "migration_pending": false,
              "recovery_required": true,
              "legacy_cleanup_pending": false,
              "last_known_good_root": "/tmp/runtime/N.E.K.O",
              "migration": {
                "last_error": "mock recovery"
              },
              "stage": "stage2_web_selection"
            }
            """,
        ),
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    overlay = page.locator("#storage-location-overlay")
    selection_title = page.get_by_role("heading", name="请选择本次运行使用的存储位置")

    expect(overlay).to_be_visible(timeout=15_000)
    expect(selection_title).to_be_visible(timeout=15_000)
    assert _page_config_state(page) == "pending"


@pytest.mark.frontend
def test_storage_location_ready_state_skips_overlay_and_allows_normal_startup(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    bootstrap_requests = {"count": 0}

    page.route(
        "**/api/v1/system/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body="""
            {
              "ok": true,
              "status": "ready",
              "ready": true,
              "storage": {
                "selection_required": false,
                "migration_pending": false,
                "recovery_required": false,
                "stage": "stage2_web_selection"
              }
            }
            """,
        ),
    )

    def handle_bootstrap(route):
        bootstrap_requests["count"] += 1
        route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":"bootstrap should not be called in ready state"}',
        )

    page.route("**/api/storage/location/bootstrap", handle_bootstrap)

    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    overlay = page.locator("#storage-location-overlay")
    expect(overlay).to_be_hidden(timeout=10_000)

    page.wait_for_function(
        """
        async () => {
            if (!window.pageConfigReady || typeof window.pageConfigReady.then !== 'function') {
                return false;
            }
            await window.pageConfigReady;
            return true;
        }
        """,
        timeout=10_000,
    )

    assert bootstrap_requests["count"] == 0


@pytest.mark.frontend
def test_storage_location_overlay_keeps_react_chat_window_closed_before_startup_barrier_is_released(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    overlay = page.locator("#storage-location-overlay")
    react_chat_window = page.locator("#react-chat-window-overlay")

    expect(overlay).to_be_visible(timeout=15_000)
    expect(react_chat_window).to_be_hidden(timeout=5_000)
    assert _page_config_state(page) == "pending"
