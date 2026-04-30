import json
import re

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


def _continue_storage_intro(page: Page) -> None:
    expect(page.get_by_role("heading", name="为什么要迁移？")).to_have_count(0)
    expect(page.locator(".storage-location-intro-card")).to_be_visible(timeout=15_000)
    expect(page.locator(".storage-location-intro-image")).to_be_visible(timeout=15_000)
    expect(page.locator(".storage-location-intro-image")).to_have_attribute(
        "src",
        re.compile(r"/static/icons/small_easter_egg\.png$"),
    )
    expect(page.locator("text=喵呜～人类注意啦！")).to_be_visible(timeout=15_000)
    expect(page.get_by_role("heading", name="存储位置选择")).to_be_hidden(timeout=5_000)
    expect(page.get_by_role("button", name="使用专属小窝")).to_be_visible(timeout=15_000)
    page.get_by_role("button", name="其他小窝").click()
    expect(page.get_by_role("heading", name="存储位置选择")).to_be_visible(timeout=15_000)


def _expect_storage_migration_has_no_scrollbars(page: Page) -> None:
    metrics = page.evaluate(
        """
        () => {
            const overlay = document.querySelector('#storage-location-overlay');
            const modal = document.querySelector('.storage-location-modal');
            const activeView = document.querySelector('.storage-location-view:not([hidden])');
            const missing = [];
            if (!overlay) {
                missing.push('overlay');
            }
            if (!modal) {
                missing.push('modal');
            }
            if (!activeView) {
                missing.push('activeView');
            }
            if (missing.length) {
                return { ok: false, missing };
            }
            const overlayStyle = getComputedStyle(overlay);
            const modalStyle = getComputedStyle(modal);
            const viewStyle = getComputedStyle(activeView);
            const toMetrics = (element, style) => ({
                overflowX: style.overflowX,
                overflowY: style.overflowY,
                clientWidth: element.clientWidth,
                scrollWidth: element.scrollWidth,
                clientHeight: element.clientHeight,
                scrollHeight: element.scrollHeight,
            });
            return {
                ok: true,
                overlay: toMetrics(overlay, overlayStyle),
                modal: toMetrics(modal, modalStyle),
                activeView: toMetrics(activeView, viewStyle),
            };
        }
        """
    )
    assert metrics.get("ok"), {"missing": metrics.get("missing"), "metrics": metrics}
    for name in ("overlay", "modal", "activeView"):
        item = metrics[name]
        assert item["overflowX"] != "scroll", {"element": name, "metric": "overflowX", "metrics": item}
        assert item["overflowY"] != "scroll", {"element": name, "metric": "overflowY", "metrics": item}
        assert item["scrollWidth"] <= item["clientWidth"] + 1, {
            "element": name,
            "metric": "scrollWidth",
            "metrics": item,
        }
        assert item["scrollHeight"] <= item["clientHeight"] + 1, {
            "element": name,
            "metric": "scrollHeight",
            "metrics": item,
        }


def _mock_selection_required_state(
    page: Page,
    *,
    current_root: str = "/tmp/runtime/N.E.K.O",
    recommended_root: str | None = None,
    legacy_sources: list[str] | None = None,
    recovery_required: bool = False,
    migration_pending: bool = False,
    last_error: str = "",
) -> None:
    effective_recommended_root = recommended_root or current_root
    if legacy_sources is None:
        effective_legacy_sources = []
    elif isinstance(legacy_sources, str):
        effective_legacy_sources = json.loads(legacy_sources)
    else:
        effective_legacy_sources = legacy_sources
    blocking_reason = "migration_pending" if migration_pending else ("recovery_required" if recovery_required else "selection_required")

    page.route(
        "**/api/system/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "status": "migration_required",
                    "ready": False,
                    "storage": {
                        "selection_required": True,
                        "migration_pending": migration_pending,
                        "recovery_required": recovery_required,
                        "blocking_reason": blocking_reason,
                        "last_error_summary": last_error,
                        "stage": "stage3_web_restart",
                    },
                },
                ensure_ascii=False,
            ),
        ),
    )
    page.route(
        "**/api/storage/location/bootstrap",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "current_root": current_root,
                    "recommended_root": effective_recommended_root,
                    "legacy_sources": effective_legacy_sources,
                    "anchor_root": effective_recommended_root,
                    "cloudsave_root": f"{effective_recommended_root}/cloudsave",
                    "selection_required": True,
                    "migration_pending": migration_pending,
                    "recovery_required": recovery_required,
                    "blocking_reason": blocking_reason,
                    "legacy_cleanup_pending": False,
                    "last_known_good_root": current_root,
                    "last_error_summary": last_error,
                    "migration": {
                        "last_error": last_error,
                    },
                    "stage": "stage3_web_restart",
                    "poll_interval_ms": 1200,
                },
                ensure_ascii=False,
            ),
        ),
    )


@pytest.mark.frontend
def test_storage_location_loading_view_has_no_scrollbars(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    pending_status_routes = []

    def hold_status(route):
        pending_status_routes.append(route)

    page.route("**/api/system/status", hold_status)
    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    expect(page.get_by_role("heading", name="正在确认存储布局状态")).to_be_visible(timeout=15_000)
    _expect_storage_migration_has_no_scrollbars(page)

    assert pending_status_routes
    pending_status_routes[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {
                "ok": True,
                "status": "ready",
                "ready": True,
                "storage": {
                    "selection_required": False,
                    "migration_pending": False,
                    "recovery_required": False,
                    "blocking_reason": "",
                },
            },
            ensure_ascii=False,
        ),
    )
    expect(page.locator("#storage-location-overlay")).to_be_hidden(timeout=10_000)


@pytest.mark.frontend
def test_storage_location_error_view_has_no_scrollbars(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    page.route(
        "**/api/system/status",
        lambda route: route.fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"ok": False, "error": "temporary unavailable"}),
        ),
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    expect(page.get_by_role("heading", name="暂时无法读取存储位置引导信息")).to_be_visible(timeout=15_000)
    _expect_storage_migration_has_no_scrollbars(page)


@pytest.mark.frontend
def test_storage_location_overlay_blocks_page_config_until_current_path_confirmed(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    _mock_selection_required_state(page)
    page.route(
        "**/api/storage/location/select",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body="""
            {
              "ok": true,
              "result": "continue_current_session",
              "selected_root": "/tmp/runtime/N.E.K.O",
              "selection_source": "user_selected"
            }
            """,
        ),
    )
    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    overlay = page.locator("#storage-location-overlay")
    intro_card = page.locator(".storage-location-intro-card")
    selection_title = page.get_by_role("heading", name="存储位置选择")

    expect(overlay).to_be_visible(timeout=15_000)
    expect(page.get_by_role("heading", name="为什么要迁移？")).to_have_count(0)
    expect(intro_card).to_be_visible(timeout=15_000)
    expect(selection_title).to_be_hidden(timeout=5_000)
    _expect_storage_migration_has_no_scrollbars(page)

    _arm_page_config_resolution_probe(page)
    assert _page_config_state(page) == "pending"

    page.get_by_role("button", name="使用专属小窝").click()

    expect(overlay).to_be_hidden(timeout=10_000)
    page.wait_for_function(
        "() => window.__nekoPageConfigResolved === true",
        timeout=10_000,
    )


@pytest.mark.frontend
def test_storage_location_close_requests_app_shutdown_while_startup_is_blocked(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    exit_requests = {"count": 0}
    _mock_selection_required_state(page)
    page.add_init_script(
        """
        window.__nekoHostCloseCalls = 0;
        window.nekoHost = {
            closeWindow: async () => {
                window.__nekoHostCloseCalls += 1;
                return { ok: true };
            },
        };
        """
    )

    def handle_exit(route):
        exit_requests["count"] += 1
        assert route.request.headers.get("x-neko-storage-action") == "exit"
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "result": "shutdown_initiated"}),
        )

    page.route("**/api/storage/location/exit", handle_exit)
    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    expect(page.locator("#storage-location-overlay")).to_be_visible(timeout=15_000)
    expect(page.locator(".storage-location-intro-card")).to_be_visible(timeout=15_000)

    page.locator(".storage-location-modal > .storage-location-close").click()
    page.wait_for_function("() => window.__nekoHostCloseCalls === 1", timeout=10_000)

    assert exit_requests["count"] == 1
    assert _page_config_state(page) == "pending"


@pytest.mark.frontend
def test_storage_location_close_keeps_window_open_when_app_shutdown_request_fails(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    exit_requests = {"count": 0}
    _mock_selection_required_state(page)
    page.add_init_script(
        """
        window.__nekoHostCloseCalls = 0;
        window.nekoHost = {
            closeWindow: async () => {
                window.__nekoHostCloseCalls += 1;
                return { ok: true };
            },
        };
        """
    )

    def handle_exit(route):
        exit_requests["count"] += 1
        route.fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"ok": False, "error": "shutdown unavailable"}),
        )

    page.route("**/api/storage/location/exit", handle_exit)
    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    expect(page.locator("#storage-location-overlay")).to_be_visible(timeout=15_000)
    page.locator(".storage-location-modal > .storage-location-close").click()
    page.wait_for_timeout(500)

    assert exit_requests["count"] == 1
    assert page.evaluate("window.__nekoHostCloseCalls") == 0
    expect(page.locator("#storage-location-overlay")).to_be_visible()


@pytest.mark.frontend
def test_storage_location_overlay_blocks_independent_startup_requests_while_barrier_is_pending(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    page_config_requests = {"count": 0}
    playtime_requests = {"count": 0}
    _mock_selection_required_state(page)

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
    intro_card = page.locator(".storage-location-intro-card")

    expect(overlay).to_be_visible(timeout=15_000)
    expect(page.get_by_role("heading", name="为什么要迁移？")).to_have_count(0)
    expect(intro_card).to_be_visible(timeout=15_000)
    _expect_storage_migration_has_no_scrollbars(page)

    page.wait_for_timeout(800)
    assert _page_config_state(page) == "pending"
    assert page_config_requests["count"] == 0
    assert playtime_requests["count"] == 0


@pytest.mark.frontend
def test_storage_location_selection_view_hides_internal_paths_and_supports_folder_picker(
    mock_page: Page,
    running_server: str,
    tmp_path,
):
    page = mock_page
    picked_parent = str((tmp_path / "picked-root").resolve())
    picked_root = str((tmp_path / "picked-root" / "N.E.K.O").resolve())
    _mock_selection_required_state(
        page,
        legacy_sources='["/tmp/runtime/legacy-a/N.E.K.O"]',
    )
    page.route(
        "**/api/storage/location/pick-directory",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "cancelled": False,
                    "selected_root": picked_root,
                },
                ensure_ascii=False,
            ),
        ),
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    _continue_storage_intro(page)
    assert page.locator("text=固定锚点目录").count() == 0
    assert page.locator("text=固定 cloudsave 目录").count() == 0
    assert page.locator("text=已检测到的旧数据目录").count() == 0
    assert page.locator("text=本阶段提示").count() == 0

    assert page.locator("text=应用会使用其中独立的 N.E.K.O 子文件夹").count() == 0
    page.get_by_role("button", name="选择文件夹").click()

    custom_input = page.locator(".storage-location-input")
    submit_other_button = page.get_by_role("button", name="提交该位置")
    expect(custom_input).to_have_value(picked_root, timeout=10_000)
    expect(submit_other_button).to_be_enabled(timeout=10_000)


@pytest.mark.frontend
def test_storage_location_desktop_picker_normalizes_parent_directory_before_submit(
    mock_page: Page,
    running_server: str,
    tmp_path,
):
    page = mock_page
    picked_parent = str((tmp_path / "desktop-picked-root").resolve())
    picked_root = str((tmp_path / "desktop-picked-root" / "N.E.K.O").resolve())
    _mock_selection_required_state(page)
    page.add_init_script(
        """
        window.nekoHost = {
            pickDirectory: async (options) => {
                window.__storagePickOptions = options;
                return { cancelled: false, selected_root: %s };
            }
        };
        """
        % json.dumps(picked_parent)
    )
    page.route(
        "**/api/storage/location/pick-directory",
        lambda route: route.fulfill(
            status=500,
            content_type="application/json",
            body='{"ok": false, "error": "backend picker should not be used when host picker succeeds"}',
        ),
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    _continue_storage_intro(page)
    page.get_by_role("button", name="选择文件夹").click()

    custom_input = page.locator(".storage-location-input")
    expect(custom_input).to_have_value(picked_root, timeout=10_000)
    assert page.evaluate("window.__storagePickOptions && window.__storagePickOptions.title") == "选择文件夹"


@pytest.mark.frontend
def test_storage_location_overlay_keeps_page_config_blocked_on_restart_required_preview(
    mock_page: Page,
    running_server: str,
    tmp_path,
):
    page = mock_page
    select_requests = []
    target_root = tmp_path / "alt-storage" / "N.E.K.O"
    _mock_selection_required_state(page)

    def handle_select(route):
        select_requests.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "result": "restart_required",
                    "selected_root": str(target_root.resolve()),
                    "selection_source": "custom",
                },
                ensure_ascii=False,
            ),
        )

    page.route("**/api/storage/location/select", handle_select)
    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    overlay = page.locator("#storage-location-overlay")
    selection_title = page.get_by_role("heading", name="存储位置选择")
    submit_other_button = page.get_by_role("button", name="提交该位置")
    custom_input = page.locator(".storage-location-input")
    preview_note = page.locator("text=更改存储位置会先关闭当前实例")

    expect(overlay).to_be_visible(timeout=15_000)
    _continue_storage_intro(page)
    expect(selection_title).to_be_visible(timeout=15_000)
    _expect_storage_migration_has_no_scrollbars(page)

    _arm_page_config_resolution_probe(page)
    assert _page_config_state(page) == "pending"

    expect(custom_input).to_be_visible(timeout=5_000)

    custom_input.fill(str(tmp_path / "alt-storage"))
    submit_other_button.click()

    expect(preview_note).to_be_visible(timeout=10_000)
    _expect_storage_migration_has_no_scrollbars(page)
    assert select_requests[-1]["selected_root"] == str(target_root.resolve())
    assert _page_config_state(page) == "pending"


@pytest.mark.frontend
def test_storage_location_restart_confirmation_enters_maintenance_page_and_recovers_when_service_is_ready(
    mock_page: Page,
    running_server: str,
    tmp_path,
):
    page = mock_page
    status_requests = {"count": 0}
    storage_status_requests = {"count": 0}
    target_root = str((tmp_path / "alt-storage" / "N.E.K.O").resolve())

    def handle_status(route):
        status_requests["count"] += 1
        if status_requests["count"] == 1:
            route.fulfill(
                status=200,
                content_type="application/json",
                body="""
                {
                  "ok": true,
                  "status": "migration_required",
                  "ready": false,
                  "storage": {
                    "selection_required": true,
                    "migration_pending": false,
                    "recovery_required": false,
                    "blocking_reason": "selection_required",
                    "last_error_summary": "",
                    "stage": "stage3_web_restart"
                  }
                }
                """,
            )
            return

        route.fulfill(
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
                "blocking_reason": "",
                "last_error_summary": "",
                "stage": "stage3_web_restart"
              }
            }
            """,
        )

    page.route("**/api/system/status", handle_status)

    def handle_storage_location_status(route):
        storage_status_requests["count"] += 1
        if storage_status_requests["count"] <= 2:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        "ready": False,
                        "status": "maintenance",
                        "lifecycle_state": "maintenance",
                        "migration_stage": "pending",
                        "maintenance_message": "当前实例即将关闭，数据会在关闭后迁移并自动重启。",
                        "poll_interval_ms": 200,
                        "effective_root": "/tmp/runtime/N.E.K.O",
                        "last_error_summary": "",
                        "blocking_reason": "migration_pending",
                        "storage": {
                            "selection_required": False,
                            "migration_pending": True,
                            "recovery_required": False,
                            "stage": "stage3_web_restart",
                        },
                        "migration": {
                            "status": "pending",
                            "target_root": target_root,
                        },
                    },
                    ensure_ascii=False,
                ),
            )
            return

        route.fulfill(
            status=200,
            content_type="application/json",
            body="""
            {
              "ok": true,
              "ready": true,
              "status": "ready",
              "lifecycle_state": "ready",
              "migration_stage": "",
              "maintenance_message": "",
              "poll_interval_ms": 200,
              "effective_root": "/tmp/runtime/N.E.K.O",
              "last_error_summary": "",
              "blocking_reason": "",
              "storage": {
                "selection_required": false,
                "migration_pending": false,
                "recovery_required": false,
                "stage": "stage3_web_restart"
              },
              "migration": {
                "status": ""
              }
            }
            """,
        )

    page.route(
        "**/api/storage/location/status",
        handle_storage_location_status,
    )
    page.route(
        "**/api/storage/location/bootstrap",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body="""
            {
              "current_root": "/tmp/runtime/N.E.K.O",
              "recommended_root": "/tmp/runtime/N.E.K.O/recommended",
              "legacy_sources": [],
              "anchor_root": "/tmp/runtime/N.E.K.O/recommended",
              "cloudsave_root": "/tmp/runtime/N.E.K.O/recommended/cloudsave",
              "selection_required": true,
              "migration_pending": false,
              "recovery_required": false,
              "blocking_reason": "selection_required",
              "legacy_cleanup_pending": false,
              "last_known_good_root": "/tmp/runtime/N.E.K.O",
              "last_error_summary": "",
              "migration": {
                "status": "",
                "source_root": "",
                "target_root": "",
                "selection_source": "",
                "requested_at": "",
                "backup_root": "",
                "last_error": ""
              },
              "stage": "stage3_web_restart",
              "poll_interval_ms": 1200
            }
            """,
        ),
    )
    page.route(
        "**/api/storage/location/select",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "result": "restart_required",
                    "selected_root": target_root,
                    "selection_source": "custom",
                    "target_root": target_root,
                    "estimated_required_bytes": 4096,
                    "target_free_bytes": 1048576,
                    "permission_ok": True,
                    "warning_codes": [],
                    "blocking_error_code": "",
                    "blocking_error_message": "",
                },
                ensure_ascii=False,
            ),
        ),
    )
    page.route(
        "**/api/storage/location/restart",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "result": "restart_initiated",
                    "selected_root": target_root,
                    "target_root": target_root,
                    "selection_source": "custom",
                    "estimated_required_bytes": 4096,
                    "target_free_bytes": 1048576,
                    "permission_ok": True,
                    "warning_codes": [],
                    "blocking_error_code": "",
                    "blocking_error_message": "",
                    "migration": {
                        "status": "pending",
                        "source_root": "/tmp/runtime/N.E.K.O",
                        "target_root": target_root,
                        "selection_source": "custom",
                        "requested_at": "2026-04-24T00:00:00Z",
                        "backup_root": "",
                        "error_code": "",
                        "error_message": "",
                        "updated_at": "2026-04-24T00:00:00Z",
                    },
                },
                ensure_ascii=False,
            ),
        ),
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    _continue_storage_intro(page)

    submit_other_button = page.get_by_role("button", name="提交该位置")
    confirm_restart_button = page.get_by_role("button", name="确认关闭并迁移")
    custom_input = page.locator(".storage-location-input")
    maintenance_title = page.get_by_role("heading", name="正在优化存储布局...")
    maintenance_progress = page.locator('[role="progressbar"]')

    expect(custom_input).to_be_visible(timeout=5_000)
    custom_input.fill(str(tmp_path / "alt-storage" / "N.E.K.O"))
    submit_other_button.click()

    expect(page.locator(".storage-location-selection-actions")).to_be_hidden(timeout=10_000)
    expect(page.locator(".storage-location-restart-actions")).to_be_visible(timeout=10_000)
    expect(confirm_restart_button).to_be_visible(timeout=10_000)
    confirm_restart_button.click()

    expect(maintenance_title).to_be_visible(timeout=10_000)
    expect(maintenance_progress).to_be_visible(timeout=10_000)
    _expect_storage_migration_has_no_scrollbars(page)
    assert int(maintenance_progress.get_attribute("aria-valuenow") or "0") >= 10
    assert "目标路径已记录" not in (maintenance_progress.get_attribute("aria-valuetext") or "")
    assert target_root not in page.locator("#storage-location-overlay").inner_text()
    assert _page_config_state(page) == "pending"
    expect(page.locator("#storage-location-overlay")).to_be_hidden(timeout=15_000)
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
        timeout=15_000,
    )


@pytest.mark.frontend
def test_storage_location_external_restart_notice_reuses_maintenance_overlay(
    mock_page: Page,
    running_server: str,
    tmp_path,
):
    page = mock_page
    target_root = str((tmp_path / "memory-page-target" / "N.E.K.O").resolve())

    page.route(
        "**/api/system/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "status": "ready",
                    "ready": True,
                    "storage": {
                        "selection_required": False,
                        "migration_pending": False,
                        "recovery_required": False,
                        "blocking_reason": "",
                        "last_error_summary": "",
                        "stage": "external_restart_notice",
                    },
                },
                ensure_ascii=False,
            ),
        ),
    )
    page.route(
        "**/api/storage/location/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "ready": False,
                    "status": "maintenance",
                    "lifecycle_state": "maintenance",
                    "migration_stage": "pending",
                    "maintenance_message": "当前实例即将关闭，数据会在关闭后迁移并自动重启。",
                    "poll_interval_ms": 500,
                    "effective_root": "/tmp/runtime/N.E.K.O",
                    "last_error_summary": "",
                    "blocking_reason": "migration_pending",
                    "storage": {
                        "selection_required": False,
                        "migration_pending": True,
                        "recovery_required": False,
                        "stage": "external_restart_notice",
                    },
                    "migration": {
                        "status": "pending",
                        "target_root": target_root,
                    },
                },
                ensure_ascii=False,
            ),
        ),
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    expect(page.locator("#storage-location-overlay")).to_be_hidden(timeout=10_000)

    page.evaluate(
        """(targetRoot) => {
            window.postMessage({
                type: 'storage_location_restart_initiated',
                payload: {
                    ok: true,
                    result: 'restart_initiated',
                    restart_mode: 'migrate_after_shutdown',
                    selected_root: targetRoot,
                    target_root: targetRoot,
                    migration: {
                        status: 'pending',
                        target_root: targetRoot
                    }
                }
            }, window.location.origin);
        }""",
        target_root,
    )

    expect(page.get_by_role("heading", name="正在优化存储布局...")).to_be_visible(timeout=10_000)
    expect(page.locator('[role="progressbar"]')).to_be_visible(timeout=10_000)
    page.wait_for_function(
        "() => document.body.classList.contains('storage-location-modal-open')",
        timeout=10_000,
    )


@pytest.mark.frontend
def test_storage_location_existing_target_requires_second_confirmation_before_restart(
    mock_page: Page,
    running_server: str,
    tmp_path,
):
    page = mock_page
    target_root = str((tmp_path / "existing-target" / "N.E.K.O").resolve())
    restart_requests = []
    _mock_selection_required_state(page)

    page.route(
        "**/api/storage/location/select",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "result": "restart_required",
                    "selected_root": target_root,
                    "selection_source": "custom",
                    "target_root": target_root,
                    "estimated_required_bytes": 4096,
                    "target_free_bytes": 1048576,
                    "permission_ok": True,
                    "warning_codes": [],
                    "target_has_existing_content": True,
                    "requires_existing_target_confirmation": True,
                    "existing_target_confirmation_message": "目标路径已经包含现有数据。确认后迁移会覆盖目标中的同名运行时数据目录。",
                    "blocking_error_code": "",
                    "blocking_error_message": "",
                },
                ensure_ascii=False,
            ),
        ),
    )

    def handle_restart(route):
        restart_requests.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "result": "restart_initiated",
                    "selected_root": target_root,
                    "target_root": target_root,
                    "selection_source": "custom",
                    "target_has_existing_content": True,
                    "requires_existing_target_confirmation": True,
                    "existing_target_confirmation_message": "目标路径已经包含现有数据。确认后迁移会覆盖目标中的同名运行时数据目录。",
                    "blocking_error_code": "",
                    "blocking_error_message": "",
                    "migration": {
                        "status": "pending",
                        "source_root": "/tmp/runtime/N.E.K.O",
                        "target_root": target_root,
                        "selection_source": "custom",
                    },
                },
                ensure_ascii=False,
            ),
        )

    page.route(
        "**/api/storage/location/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "ready": False,
                    "status": "maintenance",
                    "lifecycle_state": "maintenance",
                    "migration_stage": "pending",
                    "maintenance_message": "",
                    "poll_interval_ms": 200,
                    "effective_root": "/tmp/runtime/N.E.K.O",
                    "last_error_summary": "",
                    "blocking_reason": "migration_pending",
                    "storage": {
                        "selection_required": False,
                        "migration_pending": True,
                        "recovery_required": False,
                        "legacy_cleanup_pending": False,
                        "stage": "stage3_web_restart",
                    },
                    "migration": {
                        "status": "pending",
                        "source_root": "/tmp/runtime/N.E.K.O",
                        "target_root": target_root,
                        "selection_source": "custom",
                    },
                },
                ensure_ascii=False,
            ),
        ),
    )
    page.route("**/api/storage/location/restart", handle_restart)
    page.on("dialog", lambda dialog: dialog.accept())

    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    _continue_storage_intro(page)
    page.locator(".storage-location-input").fill(str(tmp_path / "existing-target"))
    page.get_by_role("button", name="提交该位置").click()

    confirm_restart_button = page.get_by_role("button", name="确认关闭并迁移")
    expect(confirm_restart_button).to_be_enabled(timeout=10_000)
    expect(page.locator("text=目标文件夹已经包含 N.E.K.O 运行时数据")).to_be_visible(timeout=10_000)
    confirm_restart_button.click()

    expect(page.get_by_role("heading", name="正在优化存储布局...")).to_be_visible(timeout=10_000)
    assert restart_requests[-1]["confirm_existing_target_content"] is True


@pytest.mark.frontend
def test_storage_location_pending_migration_refresh_stays_on_maintenance_page_instead_of_returning_to_selection(
    mock_page: Page,
    running_server: str,
):
    page = mock_page

    page.route(
        "**/api/system/status",
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
                "migration_pending": true,
                "recovery_required": false,
                "blocking_reason": "migration_pending",
                "last_error_summary": "",
                "stage": "stage3_web_restart"
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
              "recommended_root": "/tmp/runtime/N.E.K.O/recommended",
              "legacy_sources": [],
              "anchor_root": "/tmp/runtime/N.E.K.O/recommended",
              "cloudsave_root": "/tmp/runtime/N.E.K.O/recommended/cloudsave",
              "selection_required": false,
              "migration_pending": true,
              "recovery_required": false,
              "blocking_reason": "migration_pending",
              "legacy_cleanup_pending": false,
              "last_known_good_root": "/tmp/runtime/N.E.K.O",
              "last_error_summary": "",
              "migration": {
                "status": "pending",
                "source_root": "/tmp/runtime/N.E.K.O",
                "target_root": "/tmp/runtime/N.E.K.O/recommended",
                "selection_source": "recommended",
                "requested_at": "2026-04-24T00:00:00Z",
                "backup_root": "",
                "last_error": ""
              },
              "stage": "stage3_web_restart",
              "poll_interval_ms": 1200
            }
            """,
        ),
    )
    page.route(
        "**/api/storage/location/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body="""
            {
              "ok": true,
              "ready": false,
              "status": "maintenance",
              "lifecycle_state": "maintenance",
              "migration_stage": "pending",
              "maintenance_message": "当前实例即将关闭，数据会在关闭后迁移并自动重启。",
              "poll_interval_ms": 1200,
              "effective_root": "/tmp/runtime/N.E.K.O",
              "last_error_summary": "",
              "blocking_reason": "migration_pending",
              "storage": {
                "selection_required": false,
                "migration_pending": true,
                "recovery_required": false,
                "stage": "stage3_web_restart"
              },
              "migration": {
                "status": "pending",
                "target_root": "/tmp/runtime/N.E.K.O/recommended"
              }
            }
            """,
        ),
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    overlay = page.locator("#storage-location-overlay")
    maintenance_title = page.get_by_role("heading", name="正在优化存储布局...")
    selection_title = page.get_by_role("heading", name="存储位置选择")

    expect(overlay).to_be_visible(timeout=15_000)
    expect(maintenance_title).to_be_visible(timeout=15_000)
    expect(selection_title).to_be_hidden(timeout=5_000)
    assert _page_config_state(page) == "pending"


@pytest.mark.frontend
def test_storage_location_overlay_stays_open_for_recovery_required_state_even_if_first_run_selection_flag_is_false(
    mock_page: Page,
    running_server: str,
):
    page = mock_page

    page.route(
        "**/api/system/status",
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
                "blocking_reason": "recovery_required",
                "last_error_summary": "mock recovery",
                "stage": "stage3_web_restart"
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
              "blocking_reason": "recovery_required",
              "legacy_cleanup_pending": false,
              "last_known_good_root": "/tmp/runtime/N.E.K.O",
              "last_error_summary": "mock recovery",
              "migration": {
                "last_error": "mock recovery"
              },
              "stage": "stage3_web_restart",
              "poll_interval_ms": 1200
            }
            """,
        ),
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    overlay = page.locator("#storage-location-overlay")
    selection_title = page.get_by_role("heading", name="存储位置选择")

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
        "**/api/system/status",
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
                "blocking_reason": "",
                "last_error_summary": "",
                "stage": "stage3_web_restart"
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
    _mock_selection_required_state(page)
    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    overlay = page.locator("#storage-location-overlay")
    react_chat_window = page.locator("#react-chat-window-overlay")

    expect(overlay).to_be_visible(timeout=15_000)
    expect(react_chat_window).to_be_hidden(timeout=5_000)
    assert _page_config_state(page) == "pending"


@pytest.mark.frontend
def test_storage_location_ready_state_shows_completion_notice_and_allows_manual_cleanup(
    mock_page: Page,
    running_server: str,
    tmp_path,
):
    page = mock_page
    cleanup_requests = {"count": 0, "payload": ""}

    source_root = str((tmp_path / "source-root" / "N.E.K.O").resolve())
    target_root = str((tmp_path / "target-root" / "N.E.K.O").resolve())
    page.add_init_script(
        """
        () => {
            window.__nekoOpenPathCalls = [];
            window.nekoHost = {
                openPath: async (payload) => {
                    window.__nekoOpenPathCalls.push(payload);
                    return { ok: true };
                },
            };
        }
        """
    )

    page.route(
        "**/api/system/status",
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
                "blocking_reason": "",
                "last_error_summary": "",
                "legacy_cleanup_pending": true,
                "stage": "stage5_completion"
              }
            }
            """,
        ),
    )
    page.route(
        "**/api/storage/location/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "ready": True,
                    "status": "ready",
                    "lifecycle_state": "ready",
                    "migration_stage": "completed",
                    "maintenance_message": "",
                    "poll_interval_ms": 200,
                    "effective_root": target_root,
                    "last_error_summary": "",
                    "blocking_reason": "",
                    "storage": {
                        "selection_required": False,
                        "migration_pending": False,
                        "recovery_required": False,
                        "legacy_cleanup_pending": True,
                        "stage": "stage5_completion",
                    },
                    "migration": {
                        "status": "completed",
                        "source_root": source_root,
                        "target_root": target_root,
                        "retained_source_root": source_root,
                        "retained_source_mode": "manual_retention",
                        "completed_at": "2026-04-25T00:00:00Z",
                    },
                    "completion_notice": {
                        "completed": True,
                        "message": "新的运行目录已经生效，旧数据目录目前仍保留。",
                        "source_root": source_root,
                        "target_root": target_root,
                        "retained_root": source_root,
                        "retained_root_exists": True,
                        "cleanup_available": True,
                    },
                },
                ensure_ascii=False,
            ),
        ),
    )

    def handle_cleanup(route):
        cleanup_requests["count"] += 1
        cleanup_requests["payload"] = route.request.post_data or ""
        route.fulfill(
            status=200,
            content_type="application/json",
            body="""
            {
              "ok": true,
              "retained_root": "",
              "cleanup_completed_at": "2026-04-25T00:00:05Z"
            }
            """,
        )

    page.route("**/api/storage/location/retained-source/cleanup", handle_cleanup)
    page.on("dialog", lambda dialog: dialog.accept())

    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    completion_card = page.locator(".storage-location-completion-card")
    cleanup_button = completion_card.locator("button.storage-location-btn--primary")
    completion_paths = page.locator(".storage-location-completion-card .storage-location-path")

    expect(page.locator("#storage-location-overlay")).to_be_hidden(timeout=10_000)
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
    page.wait_for_function(
        """
        () => !!(
            window.appStorageLocation
            && typeof window.appStorageLocation.refreshCompletionNotice === 'function'
        )
        """,
        timeout=10_000,
    )
    page.wait_for_function(
        """
        async () => {
            await window.appStorageLocation.refreshCompletionNotice();
            const card = document.querySelector('.storage-location-completion-card');
            return !!(card && !card.hidden);
        }
        """,
        timeout=10_000,
    )
    expect(completion_card).to_be_visible(timeout=10_000)
    expect(completion_card.locator("text=原始路径")).to_have_count(0)
    expect(completion_paths).to_have_count(2)
    expect(completion_paths.nth(0)).to_have_text(target_root, timeout=10_000)
    expect(completion_paths.nth(1)).to_have_text(source_root, timeout=10_000)
    expect(cleanup_button).to_be_visible(timeout=10_000)
    expect(completion_card.get_by_role("button", name="打开当前目录")).to_have_count(0)
    expect(completion_card.get_by_role("button", name="打开旧目录")).to_have_count(0)
    expect(completion_card.locator(".storage-location-actions button", has_text="关闭")).to_have_count(0)

    before_drag = completion_card.bounding_box()
    assert before_drag is not None
    page.mouse.move(before_drag["x"] + 80, before_drag["y"] + 18)
    page.mouse.down()
    page.mouse.move(before_drag["x"] - 20, before_drag["y"] - 62)
    page.mouse.up()
    after_drag = completion_card.bounding_box()
    assert after_drag is not None
    assert abs(after_drag["x"] - before_drag["x"]) >= 40
    assert abs(after_drag["y"] - before_drag["y"]) >= 30

    cleanup_button.click()

    expect(completion_card).to_be_hidden(timeout=10_000)
    assert cleanup_requests["count"] == 1
    assert json.loads(cleanup_requests["payload"]) == {
        "retained_root": source_root,
    }


@pytest.mark.frontend
def test_storage_location_completion_notice_close_is_remembered_per_migration(
    mock_page: Page,
    running_server: str,
    tmp_path,
):
    page = mock_page

    source_root = str((tmp_path / "source-root" / "N.E.K.O").resolve())
    target_root = str((tmp_path / "target-root" / "N.E.K.O").resolve())
    completion_notice = {
        "completed": True,
        "message": "新的运行目录已经生效，旧数据目录目前仍保留。",
        "source_root": source_root,
        "target_root": target_root,
        "retained_root": source_root,
        "retained_root_exists": True,
        "cleanup_available": True,
        "completed_at": "2026-04-25T00:00:00Z",
    }

    page.route(
        "**/api/system/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "status": "ready",
                    "ready": True,
                    "storage": {
                        "selection_required": False,
                        "migration_pending": False,
                        "recovery_required": False,
                        "blocking_reason": "",
                        "last_error_summary": "",
                        "legacy_cleanup_pending": True,
                        "stage": "stage5_completion",
                    },
                },
                ensure_ascii=False,
            ),
        ),
    )

    def handle_storage_status(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "ready": True,
                    "status": "ready",
                    "lifecycle_state": "ready",
                    "migration_stage": "completed",
                    "maintenance_message": "",
                    "poll_interval_ms": 200,
                    "effective_root": target_root,
                    "last_error_summary": "",
                    "blocking_reason": "",
                    "storage": {
                        "selection_required": False,
                        "migration_pending": False,
                        "recovery_required": False,
                        "legacy_cleanup_pending": True,
                        "stage": "stage5_completion",
                    },
                    "migration": {
                        "status": "completed",
                        "source_root": source_root,
                        "target_root": target_root,
                        "retained_source_root": source_root,
                        "retained_source_mode": "manual_retention",
                        "completed_at": completion_notice["completed_at"],
                    },
                    "completion_notice": dict(completion_notice),
                },
                ensure_ascii=False,
            ),
        )

    page.route("**/api/storage/location/status", handle_storage_status)

    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    completion_card = page.locator(".storage-location-completion-card")

    page.wait_for_function(
        """
        async () => {
            if (!window.appStorageLocation) return false;
            await window.appStorageLocation.refreshCompletionNotice();
            const card = document.querySelector('.storage-location-completion-card');
            return !!(card && !card.hidden);
        }
        """,
        timeout=10_000,
    )
    expect(completion_card).to_be_visible(timeout=10_000)

    completion_card.locator(".storage-location-close").click()
    expect(completion_card).to_be_hidden(timeout=10_000)

    page.reload(wait_until="domcontentloaded")
    page.wait_for_function(
        """
        async () => {
            if (!window.appStorageLocation) return false;
            await window.appStorageLocation.refreshCompletionNotice();
            const card = document.querySelector('.storage-location-completion-card');
            return !!card && card.hidden === true;
        }
        """,
        timeout=10_000,
    )
    expect(completion_card).to_be_hidden(timeout=10_000)

    completion_notice["completed_at"] = "2026-04-26T00:00:00Z"
    page.wait_for_function(
        """
        async () => {
            await window.appStorageLocation.refreshCompletionNotice();
            const card = document.querySelector('.storage-location-completion-card');
            return !!(card && !card.hidden);
        }
        """,
        timeout=10_000,
    )
    expect(completion_card).to_be_visible(timeout=10_000)
