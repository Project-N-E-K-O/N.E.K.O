import json
import re

import pytest
from playwright.sync_api import Page, expect

STORAGE_CSRF_TOKEN = "storage-location-test-token"


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


def _set_home_tutorial_startup_released(page: Page, released: bool) -> None:
    page.evaluate(
        """
        (released) => {
            window.dispatchEvent(new CustomEvent('neko:startup-greeting-release', {
                detail: {
                    released,
                    page: 'home',
                    reason: released ? 'test-tutorial-settled' : 'test-tutorial-started',
                },
            }));
        }
        """,
        released,
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
    expect(page.get_by_role("button", name="推荐存储位置")).to_be_visible(timeout=15_000)
    page.get_by_role("button", name="其他位置").click()
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
    effective_recommended_root = current_root if recommended_root is None else recommended_root
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
                    "autostart_csrf_token": STORAGE_CSRF_TOKEN,
                    "current_root": current_root,
                    "recommended_root": effective_recommended_root,
                    "legacy_sources": effective_legacy_sources,
                    "anchor_root": effective_recommended_root,
                    "cloudsave_root": f"{effective_recommended_root}/cloudsave" if effective_recommended_root else "",
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
    status_requests = {"count": 0}

    def handle_status_error(route):
        status_requests["count"] += 1
        route.fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"ok": False, "error": "temporary unavailable"}),
        )

    page.route(
        "**/api/system/status",
        handle_status_error,
    )
    page.route(
        "**/api/storage/location/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "autostart_csrf_token": STORAGE_CSRF_TOKEN}),
        ),
    )
    page.route(
        "**/api/storage/location/exit",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "result": "shutdown_initiated"}),
        ),
    )
    page.add_init_script(
        """
        window.nekoHost = {
            closeWindow: async () => ({ ok: false, error: 'simulated close failure' }),
        };
        """
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    expect(page.get_by_role("heading", name="暂时无法读取存储位置引导信息")).to_be_visible(timeout=15_000)
    _expect_storage_migration_has_no_scrollbars(page)
    _arm_page_config_resolution_probe(page)
    page.locator(".storage-location-modal > .storage-location-close").click()
    expect(page.locator("#storage-location-host-close-feedback")).to_be_visible(timeout=5_000)
    status_count_after_shutdown = status_requests["count"]
    page.get_by_role("button", name="重试", exact=True).click(force=True)
    page.wait_for_timeout(300)
    assert status_requests["count"] == status_count_after_shutdown
    assert _page_config_state(page) == "pending"


@pytest.mark.frontend
def test_storage_location_error_retry_is_invalidated_by_controlled_shutdown(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    retry_probe_routes = []
    exit_requests = {"count": 0}
    initial_probe_finished = {"value": False}

    def handle_system_status(route):
        if initial_probe_finished["value"]:
            retry_probe_routes.append(route)
            return
        route.fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"ok": False, "error": "temporary unavailable"}),
        )

    def handle_exit(route):
        exit_requests["count"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "result": "shutdown_initiated"}),
        )

    page.route("**/api/system/status", handle_system_status)
    page.route(
        "**/api/storage/location/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "autostart_csrf_token": STORAGE_CSRF_TOKEN}),
        ),
    )
    page.route("**/api/storage/location/exit", handle_exit)
    page.add_init_script(
        """
        window.__nekoHostCloseCalls = 0;
        window.nekoHost = {
            closeWindow: async () => {
                window.__nekoHostCloseCalls += 1;
                return { ok: false, error: 'simulated close failure' };
            },
        };
        """
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="暂时无法读取存储位置引导信息")).to_be_visible(
        timeout=15_000
    )
    _arm_page_config_resolution_probe(page)
    initial_probe_finished["value"] = True

    retry_button = page.get_by_role("button", name="重试", exact=True)
    retry_button.click()
    expect(retry_button).to_be_disabled(timeout=5_000)
    page.wait_for_timeout(100)
    assert len(retry_probe_routes) == 1

    page.locator(".storage-location-modal > .storage-location-close").click(force=True)
    expect(page.locator("#storage-location-host-close-feedback")).to_be_visible(timeout=5_000)
    assert exit_requests["count"] == 1
    assert page.evaluate("window.__nekoHostCloseCalls") == 1
    assert _page_config_state(page) == "pending"

    retry_probe_routes[0].fulfill(
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
            }
        ),
    )
    page.wait_for_timeout(300)
    expect(page.locator("#storage-location-overlay")).to_be_visible()
    assert _page_config_state(page) == "pending"


@pytest.mark.frontend
def test_storage_location_failed_close_resumes_probe_without_stale_state_pollution(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    retry_probe_routes = []
    initial_probe_finished = {"value": False}
    select_requests = []

    def handle_system_status(route):
        if initial_probe_finished["value"]:
            retry_probe_routes.append(route)
            return
        route.fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"ok": False, "error": "temporary unavailable"}),
        )

    page.route("**/api/system/status", handle_system_status)
    page.route(
        "**/api/storage/location/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "autostart_csrf_token": "close-token"}),
        ),
    )
    page.route(
        "**/api/storage/location/exit",
        lambda route: route.fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"ok": False, "error": "shutdown unavailable"}),
        ),
    )
    page.route(
        "**/api/storage/location/bootstrap",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "autostart_csrf_token": "new-generation-token",
                    "current_root": "/tmp/new-current/N.E.K.O",
                    "recommended_root": "/tmp/new-target/N.E.K.O",
                    "legacy_sources": [],
                    "anchor_root": "/tmp/new-target/N.E.K.O",
                    "cloudsave_root": "/tmp/new-target/N.E.K.O/cloudsave",
                    "selection_required": True,
                    "migration_pending": False,
                    "recovery_required": False,
                    "blocking_reason": "selection_required",
                    "legacy_cleanup_pending": False,
                    "last_known_good_root": "/tmp/new-current/N.E.K.O",
                    "last_error_summary": "",
                    "migration": {},
                    "stage": "stage3_web_restart",
                    "poll_interval_ms": 1200,
                }
            ),
        ),
    )

    def handle_select(route):
        select_requests.append(
            {
                "headers": route.request.headers,
                "payload": json.loads(route.request.post_data or "{}"),
            }
        )
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "result": "restart_required",
                    "restart_operation_id": "new-generation-operation",
                    "restart_mode": "migrate_after_shutdown",
                    "selected_root": "/tmp/new-target/N.E.K.O",
                    "selection_source": "recommended",
                    "permission_ok": True,
                    "warning_codes": [],
                    "target_has_existing_content": False,
                    "requires_existing_target_confirmation": False,
                    "blocking_error_code": "",
                    "blocking_error_message": "",
                }
            ),
        )

    page.route("**/api/storage/location/select", handle_select)
    page.add_init_script(
        """
        window.nekoHost = {
            getBackendRecoveryState: async () => ({
                state: 'ready',
                reason: 'backend_ready',
                generation: 1,
            }),
            closeWindow: async () => ({ ok: false, error: 'simulated close failure' }),
        };
        """
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="暂时无法读取存储位置引导信息")).to_be_visible(
        timeout=15_000
    )
    initial_probe_finished["value"] = True
    page.get_by_role("button", name="重试", exact=True).click()
    page.wait_for_timeout(100)
    assert len(retry_probe_routes) == 1

    page.locator(".storage-location-modal > .storage-location-close").click(force=True)
    page.wait_for_timeout(300)
    assert len(retry_probe_routes) == 2

    retry_probe_routes[1].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {
                "ok": True,
                "status": "migration_required",
                "ready": False,
                "autostart_csrf_token": "new-generation-token",
                "storage": {
                    "selection_required": True,
                    "migration_pending": False,
                    "recovery_required": False,
                    "blocking_reason": "selection_required",
                },
            }
        ),
    )
    expect(page.locator(".storage-location-intro-card")).to_be_visible(timeout=10_000)

    retry_probe_routes[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {
                "ok": True,
                "status": "ready",
                "ready": True,
                "autostart_csrf_token": "stale-generation-token",
                "storage": {
                    "selection_required": False,
                    "migration_pending": False,
                    "recovery_required": False,
                    "blocking_reason": "",
                },
            }
        ),
    )
    page.wait_for_timeout(200)
    expect(page.locator("#storage-location-overlay")).to_be_visible()

    page.get_by_role("button", name="其他位置").click()
    page.get_by_role("button", name="使用推荐路径").click()
    page.wait_for_timeout(200)
    assert len(select_requests) == 1
    assert select_requests[0]["payload"]["selected_root"] == "/tmp/new-target/N.E.K.O"
    assert select_requests[0]["headers"].get("x-csrf-token") == "new-generation-token"


@pytest.mark.frontend
def test_storage_location_current_path_confirmation_keeps_page_blocked_for_safe_restart(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    _mock_selection_required_state(page)
    restart_requested = {"value": False}
    restart_operation_id = "same-root-rebind-operation"

    def handle_select(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body="""
            {
              "ok": true,
              "result": "restart_required",
              "restart_operation_id": "%s",
              "restart_mode": "rebind_only",
              "selected_root": "/tmp/runtime/N.E.K.O",
              "selection_source": "user_selected",
              "migration_phase": "awaiting_shutdown",
              "shutdown_retry_allowed": true
            }
            """ % restart_operation_id,
        )

    page.route(
        "**/api/storage/location/select",
        handle_select,
    )

    def handle_restart(route):
        request_payload = json.loads(route.request.post_data or "{}")
        assert request_payload["restart_operation_id"] == restart_operation_id
        restart_requested["value"] = True
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "result": "restart_initiated",
                    "restart_operation_id": restart_operation_id,
                    "restart_mode": "rebind_only",
                    "selected_root": "/tmp/runtime/N.E.K.O",
                    "selection_source": "user_selected",
                    "migration_phase": "awaiting_shutdown",
                    "shutdown_retry_allowed": True,
                }
            ),
        )

    page.route("**/api/storage/location/restart", handle_restart)

    def handle_maintenance_status(route):
        if not restart_requested["value"]:
            route.fallback()
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "instance_id": "same-generation",
                    "ready": False,
                    "status": "migration_required",
                    "lifecycle_state": "maintenance",
                    "migration_stage": "awaiting_shutdown",
                    "blocking_reason": "migration_pending",
                    "migration_phase": "awaiting_shutdown",
                    "shutdown_retry_allowed": True,
                    "storage": {"migration_pending": True},
                }
            ),
        )

    page.route("**/api/system/status", handle_maintenance_status)
    page.route("**/api/storage/location/status", handle_maintenance_status)
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

    page.get_by_role("button", name="推荐存储位置").click()
    expect(page.get_by_role("button", name="确认并重启到原路径")).to_be_visible(timeout=10_000)
    page.get_by_role("button", name="确认并重启到原路径").click()

    expect(overlay).to_be_visible(timeout=10_000)
    expect(page.get_by_role("heading", name="正在优化存储布局...")).to_be_visible(timeout=10_000)
    assert _page_config_state(page) == "pending"


@pytest.mark.frontend
@pytest.mark.parametrize("close_phase", ["selection_intro", "selection_required", "preview"])
def test_storage_location_close_requests_app_shutdown_while_startup_is_blocked(
    mock_page: Page,
    running_server: str,
    close_phase: str,
):
    page = mock_page
    exit_requests = {"count": 0}
    select_requests = {"count": 0}
    restart_requests = {"count": 0}
    pending_exit_routes = []
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
        assert route.request.headers.get("x-csrf-token") == STORAGE_CSRF_TOKEN
        pending_exit_routes.append(route)

    def handle_select(route):
        select_requests["count"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "result": "restart_required",
                    "restart_operation_id": "close-freeze-operation",
                    "restart_mode": "rebind_only",
                    "selected_root": "/tmp/runtime/N.E.K.O",
                    "selection_source": "recommended",
                    "permission_ok": True,
                    "warning_codes": [],
                    "target_has_existing_content": False,
                    "requires_existing_target_confirmation": False,
                    "blocking_error_code": "",
                    "blocking_error_message": "",
                }
            ),
        )

    def handle_restart(route):
        restart_requests["count"] += 1
        route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    page.route("**/api/storage/location/exit", handle_exit)
    page.route("**/api/storage/location/select", handle_select)
    page.route("**/api/storage/location/restart", handle_restart)
    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    expect(page.locator("#storage-location-overlay")).to_be_visible(timeout=15_000)
    expect(page.locator(".storage-location-intro-card")).to_be_visible(timeout=15_000)
    if close_phase != "selection_intro":
        _continue_storage_intro(page)
    if close_phase == "preview":
        page.get_by_role("button", name="使用推荐路径").click()
        expect(page.get_by_role("button", name="确认并重启到原路径")).to_be_visible(
            timeout=10_000
        )
        select_requests["count"] = 0

    if close_phase == "selection_intro":
        mutation_button = page.get_by_role("button", name="推荐存储位置")
    elif close_phase == "selection_required":
        mutation_button = page.get_by_role("button", name="使用推荐路径")
    else:
        mutation_button = page.get_by_role("button", name="确认并重启到原路径")

    page.locator(".storage-location-modal > .storage-location-close").click()
    expect(mutation_button).to_be_disabled(timeout=5_000)
    assert len(pending_exit_routes) == 1
    page.evaluate("window.dispatchEvent(new Event('localechange'))")
    expect(mutation_button).to_be_disabled()
    mutation_button.click(force=True)
    page.wait_for_timeout(100)
    assert select_requests["count"] == 0
    assert restart_requests["count"] == 0

    pending_exit_routes[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps({"ok": True, "result": "shutdown_initiated"}),
    )
    page.wait_for_function("() => window.__nekoHostCloseCalls === 1", timeout=10_000)

    assert exit_requests["count"] == 1
    expect(mutation_button).to_be_disabled()
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
    expect(page.locator("#storage-location-host-close-feedback")).to_be_visible(timeout=5_000)
    page.get_by_role("button", name="其他位置").click()
    expect(page.locator("#storage-location-host-close-feedback")).to_be_hidden()


@pytest.mark.frontend
def test_storage_location_close_uses_verified_host_for_non_maintenance_failure(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    _mock_selection_required_state(page)
    page.add_init_script(
        """
        window.__nekoHostCloseCalls = 0;
        window.nekoHost = {
            getBackendRecoveryState: async () => ({
                state: 'ready',
                reason: 'backend_ready',
                generation: 0,
                retry_allowed: false,
                quit_allowed: false,
            }),
            closeWindow: async () => {
                window.__nekoHostCloseCalls += 1;
                return { ok: true };
            },
        };
        """
    )
    page.route(
        "**/api/storage/location/exit",
        lambda route: route.fulfill(
            status=503,
            content_type="application/json",
            body=json.dumps({"ok": False, "error": "shutdown unavailable"}),
        ),
    )
    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    expect(page.locator("#storage-location-overlay")).to_be_visible(timeout=15_000)
    page.locator(".storage-location-modal > .storage-location-close").click()
    page.wait_for_function("() => window.__nekoHostCloseCalls === 1", timeout=10_000)
    expect(page.locator("#storage-location-overlay")).to_be_visible()


@pytest.mark.frontend
@pytest.mark.parametrize("close_phase", ["selection_intro", "selection_required"])
def test_storage_location_rejected_host_close_does_not_fall_through_to_window_close(
    mock_page: Page,
    running_server: str,
    close_phase: str,
):
    page = mock_page
    _mock_selection_required_state(page)
    page.add_init_script(
        """
        window.__nekoWindowCloseCalls = 0;
        window.close = () => { window.__nekoWindowCloseCalls += 1; };
        window.nekoHost = {
            closeWindow: async () => ({
                ok: false,
                error: 'backend_recovery_active',
                recovery: { state: 'active', quit_allowed: false },
            }),
        };
        """
    )
    page.route(
        "**/api/storage/location/exit",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "result": "shutdown_initiated"}),
        ),
    )
    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    expect(page.locator("#storage-location-overlay")).to_be_visible(timeout=15_000)
    if close_phase == "selection_required":
        _continue_storage_intro(page)
        expect(page.get_by_role("heading", name="存储位置选择")).to_be_visible(timeout=10_000)
    page.locator(".storage-location-modal > .storage-location-close").click()
    expect(page.locator("#storage-location-host-close-feedback")).to_be_visible(timeout=5_000)

    assert page.evaluate("window.__nekoWindowCloseCalls") == 0
    expect(page.locator("#storage-location-overlay")).to_be_visible()
    expect(page.locator("#storage-location-host-close-feedback")).to_contain_text(
        "安全退出未能启动"
    )
    _expect_storage_migration_has_no_scrollbars(page)
    if close_phase == "selection_intro":
        expect(page.get_by_role("button", name="其他位置")).to_be_disabled()
    else:
        expect(page.get_by_role("button", name="选择文件夹")).to_be_disabled()


@pytest.mark.frontend
def test_storage_location_maintenance_refuses_active_and_stale_ready_close(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    _mock_selection_required_state(page, migration_pending=True)
    exit_requests = {"count": 0}
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
                    "poll_interval_ms": 50,
                    "blocking_reason": "migration_pending",
                    "storage": {"migration_pending": True},
                    "migration": {"status": "pending"},
                }
            ),
        ),
    )
    def handle_exit(route):
        exit_requests["count"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "result": "shutdown_initiated"}),
        )

    page.route("**/api/storage/location/exit", handle_exit)
    page.add_init_script(
        """
        window.__nekoRecoveryState = 'active';
        window.__nekoHostCloseCalls = 0;
        window.__nekoSafeQuitCalls = 0;
        window.nekoHost = {
            getBackendRecoveryState: async () => ({
                state: window.__nekoRecoveryState,
                reason: window.__nekoRecoveryState === 'ready' ? 'backend_ready' : 'migration_active',
                generation: 1,
                retry_allowed: false,
                quit_allowed: false,
            }),
            requestSafeQuit: async () => {
                window.__nekoSafeQuitCalls += 1;
                return { ok: true, action: 'quit_app' };
            },
            closeWindow: async () => {
                window.__nekoHostCloseCalls += 1;
                return { ok: true };
            },
        };
        """
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="正在优化存储布局...")).to_be_visible(timeout=15_000)
    page.locator(".storage-location-modal > .storage-location-close").click()
    page.wait_for_timeout(300)
    page.evaluate("window.__nekoRecoveryState = 'ready'")
    page.locator(".storage-location-modal > .storage-location-close").click()
    page.wait_for_timeout(300)

    assert exit_requests["count"] == 0
    assert page.evaluate("window.__nekoHostCloseCalls") == 0
    assert page.evaluate("window.__nekoSafeQuitCalls") == 0
    expect(page.locator("#storage-location-overlay")).to_be_visible()
    expect(page.locator("#storage-location-host-close-feedback")).to_be_visible()
    expect(page.locator("#storage-location-host-close-feedback")).to_contain_text(
        "安全退出未能启动"
    )


@pytest.mark.frontend
def test_storage_location_maintenance_does_not_reload_on_first_same_instance_ready(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    instance_id = "backend-before-restart"
    restart_operation_id = "restart-op-main-flow"
    status_requests = {"count": 0}
    reported_instance = {"id": instance_id}
    reported_pending = {"value": False}

    def handle_system_status(route):
        restarted = reported_instance["id"] != instance_id
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "instance_id": reported_instance["id"],
                    "status": "ready" if restarted else "migration_required",
                    "ready": restarted,
                    "storage": {
                        "selection_required": not restarted,
                        "migration_pending": False,
                        "recovery_required": False,
                        "blocking_reason": "" if restarted else "selection_required",
                    },
                }
            ),
        )

    page.route("**/api/system/status", handle_system_status)
    page.route(
        "**/api/storage/location/bootstrap",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "autostart_csrf_token": STORAGE_CSRF_TOKEN,
                    "current_root": "/tmp/current/N.E.K.O",
                    "recommended_root": "/tmp/recommended/N.E.K.O",
                    "selection_required": True,
                    "migration_pending": False,
                    "recovery_required": False,
                    "blocking_reason": "selection_required",
                    "legacy_cleanup_pending": False,
                    "stage": "stage3_web_restart",
                }
            ),
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
                    "selected_root": "/tmp/target/N.E.K.O",
                    "selection_source": "custom",
                    "target_root": "/tmp/target/N.E.K.O",
                    "restart_operation_id": restart_operation_id,
                    "blocking_error_code": "",
                    "blocking_error_message": "",
                    "requires_existing_target_confirmation": False,
                }
            ),
        ),
    )
    def handle_restart(route):
        restart_payload = json.loads(route.request.post_data or "{}")
        assert restart_payload["restart_operation_id"] == restart_operation_id
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "result": "restart_initiated",
                    "restart_mode": "migrate_after_shutdown",
                    "selected_root": "/tmp/target/N.E.K.O",
                    "migration": {"status": "pending"},
                }
            ),
        )

    page.route("**/api/storage/location/restart", handle_restart)

    def handle_storage_status(route):
        status_requests["count"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "instance_id": reported_instance["id"],
                    "ready": True,
                    "status": "ready",
                    "lifecycle_state": "ready",
                    "migration_stage": "publishing" if reported_pending["value"] else "",
                    "blocking_reason": "migration_pending" if reported_pending["value"] else "",
                    "poll_interval_ms": 50,
                    "storage": {"migration_pending": reported_pending["value"]},
                }
            ),
        )

    page.route("**/api/storage/location/status", handle_storage_status)
    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    _continue_storage_intro(page)
    page.locator(".storage-location-input").fill("/tmp/target/N.E.K.O")
    page.get_by_role("button", name="提交该位置").click()
    expect(page.get_by_role("button", name="确认并重启")).to_be_visible(timeout=10_000)
    page.get_by_role("button", name="确认并重启").click()

    expect(page.get_by_role("heading", name="正在优化存储布局...")).to_be_visible(timeout=10_000)
    page.wait_for_timeout(300)
    assert status_requests["count"] >= 1
    expect(page.locator("#storage-location-overlay")).to_be_visible()
    reported_instance["id"] = "backend-after-restart"
    reported_pending["value"] = True
    page.wait_for_timeout(300)
    expect(page.locator("#storage-location-overlay")).to_be_visible()
    reported_pending["value"] = False
    expect(page.locator("#storage-location-overlay")).to_be_hidden(timeout=10_000)


@pytest.mark.frontend
def test_unknown_restart_waits_for_backend_operation_terminal_state(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    instance_id = "backend-same-instance"
    operation_id = "0123456789abcdef0123456789abcdef"
    target_root = "/tmp/target/N.E.K.O"
    system_requests = {"count": 0}
    storage_requests = {"count": 0}
    storage_request_urls = []
    cancel_requests = {"count": 0}
    operation_state = {"value": "in_flight"}
    lifecycle_state = {"value": "maintenance"}

    def handle_system_status(route):
        system_requests["count"] += 1
        ready = system_requests["count"] > 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "instance_id": instance_id,
                    "status": "ready" if ready else "migration_required",
                    "ready": ready,
                    "storage": {
                        "selection_required": not ready,
                        "migration_pending": False,
                        "recovery_required": False,
                        "blocking_reason": "" if ready else "selection_required",
                    },
                }
            ),
        )

    page.route("**/api/system/status", handle_system_status)
    page.route(
        "**/api/storage/location/bootstrap",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "autostart_csrf_token": STORAGE_CSRF_TOKEN,
                    "current_root": "/tmp/current/N.E.K.O",
                    "recommended_root": target_root,
                    "selection_required": True,
                    "migration_pending": False,
                    "recovery_required": False,
                    "blocking_reason": "selection_required",
                    "legacy_cleanup_pending": False,
                    "stage": "stage3_web_restart",
                }
            ),
        ),
    )

    def handle_storage_status(route):
        storage_requests["count"] += 1
        storage_request_urls.append(route.request.url)
        maintenance_active = lifecycle_state["value"] == "maintenance"
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "instance_id": instance_id,
                    "ready": False,
                    "status": "maintenance" if maintenance_active else "selection_required",
                    "lifecycle_state": "maintenance" if maintenance_active else "selection_required",
                    "migration_stage": "pending" if maintenance_active else "",
                    "blocking_reason": "migration_pending" if maintenance_active else "selection_required",
                    "poll_interval_ms": 50,
                    "storage": {
                        "selection_required": not maintenance_active,
                        "migration_pending": maintenance_active,
                        "recovery_required": False,
                    },
                    "migration": {},
                    "restart_operation": {
                        "operation_id": operation_id,
                        "state": operation_state["value"],
                        "target_root": target_root,
                        "instance_id": instance_id,
                    },
                }
            ),
        )

    def handle_cancel(route):
        cancel_requests["count"] += 1
        assert route.request.headers.get("x-csrf-token") == STORAGE_CSRF_TOKEN
        assert json.loads(route.request.post_data or "{}")["restart_operation_id"] == operation_id
        operation_state["value"] = "cancelled"
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "restart_operation": {
                        "operation_id": operation_id,
                        "state": "cancelled",
                        "target_root": target_root,
                        "instance_id": instance_id,
                    },
                }
            ),
        )

    page.route("**/api/storage/location/status**", handle_storage_status)
    page.route("**/api/storage/location/restart/cancel", handle_cancel)
    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    expect(page.locator("#storage-location-overlay")).to_be_visible(timeout=15_000)

    page.evaluate(
        """
        ([operationId, targetRoot, instanceId]) => {
            window.appStorageLocation.enterExternalMaintenanceMode({
                result: 'restart_outcome_unknown',
                restart_operation_id: operationId,
                target_root: targetRoot,
                instance_id: instanceId,
                migration: { status: 'pending', target_root: targetRoot },
            });
        }
        """,
        [operation_id, target_root, instance_id],
    )

    page.wait_for_function("() => document.querySelector('[role=progressbar]') !== null")
    page.wait_for_timeout(250)
    assert storage_requests["count"] >= 1
    assert cancel_requests["count"] == 0
    expect(page.locator("#storage-location-overlay")).to_be_visible()

    # A different tab's migration can set the sticky observation while this
    # operation is still queued. Once that other migration rolls back, the
    # current in-flight operation must still prevent reopening selection.
    lifecycle_state["value"] = "selection_required"
    page.wait_for_timeout(250)
    expect(page.get_by_role("heading", name="正在优化存储布局...")).to_be_visible()
    assert cancel_requests["count"] == 0

    operation_state["value"] = "prepared"
    expect(page.get_by_role("heading", name="存储位置选择")).to_be_visible(timeout=10_000)
    assert cancel_requests["count"] == 1
    assert any(
        f"restart_operation_id={operation_id}" in url
        for url in storage_request_urls
    )


@pytest.mark.frontend
def test_storage_location_unmanaged_rollback_closes_shell_without_stopping_backend(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    _mock_selection_required_state(page, migration_pending=True)
    exit_requests = {"count": 0}
    page.route(
        "**/api/storage/location/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "ready": False,
                    "status": "rollback_required",
                    "lifecycle_state": "rollback_required",
                    "migration_stage": "rollback_required",
                    "maintenance_message": "远程后端需要恢复。",
                    "poll_interval_ms": 50,
                    "blocking_reason": "migration_pending",
                    "storage": {"migration_pending": True, "rollback_required": True},
                    "migration": {"status": "rollback_required"},
                }
            ),
        ),
    )

    def handle_exit(route):
        exit_requests["count"] += 1
        route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    page.route("**/api/storage/location/exit", handle_exit)
    page.add_init_script(
        """
        window.__nekoHostCloseCalls = 0;
        window.nekoHost = {
            getBackendRecoveryState: async () => ({
                state: 'ready',
                reason: 'unmanaged_backend',
                generation: 0,
                retry_allowed: false,
                quit_allowed: false,
            }),
            closeWindow: async () => {
                window.__nekoHostCloseCalls += 1;
                return { ok: true };
            },
        };
        """
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="存储迁移需要恢复")).to_be_visible(timeout=15_000)
    expect(page.locator("text=请在后端所在主机安全重启并恢复")).to_be_visible()
    page.get_by_role("button", name="仅关闭桌面端").click()
    page.wait_for_function("() => window.__nekoHostCloseCalls === 1", timeout=10_000)

    assert exit_requests["count"] == 0


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
            body='{"success": true, "totalPlayTime": 0, "added": 0, "progressUnlocked": []}',
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
def test_storage_location_selection_view_disables_recommended_button_without_recommended_root(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    select_requests = {"count": 0}
    _mock_selection_required_state(page, recommended_root="")

    def handle_select(route):
        select_requests["count"] += 1
        route.fulfill(status=500, content_type="application/json", body='{"ok": false}')

    page.route("**/api/storage/location/select", handle_select)
    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    _continue_storage_intro(page)
    recommended_button = page.get_by_role("button", name="使用推荐路径")
    expect(recommended_button).to_be_disabled(timeout=10_000)
    page.evaluate(
        """
        () => {
            const button = document.querySelector('.storage-location-selection-actions .storage-location-btn--primary');
            if (button) button.click();
        }
        """
    )
    page.wait_for_timeout(250)

    assert select_requests["count"] == 0


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
        assert route.request.headers.get("x-csrf-token") == STORAGE_CSRF_TOKEN
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
    preview_note = page.locator("text=更改存储位置后会重启")

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
                        "maintenance_message": "正在关闭，数据会在关闭后迁移并自动重启。",
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
    confirm_restart_button = page.get_by_role("button", name="确认并重启")
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
def test_storage_location_unknown_restart_outcome_polls_before_allowing_retry(
    mock_page: Page,
    running_server: str,
    tmp_path,
):
    page = mock_page
    target_root = str((tmp_path / "unknown-outcome" / "N.E.K.O").resolve())
    pending_status_routes = []
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
                    "blocking_error_code": "",
                    "blocking_error_message": "",
                }
            ),
        ),
    )
    page.route("**/api/storage/location/restart", lambda route: route.abort("failed"))
    page.route("**/api/storage/location/status", lambda route: pending_status_routes.append(route))

    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    _continue_storage_intro(page)
    page.locator(".storage-location-input").fill(target_root)
    page.get_by_role("button", name="提交该位置").click()
    expect(page.get_by_role("button", name="确认并重启")).to_be_visible(timeout=10_000)
    page.get_by_role("button", name="确认并重启").click()

    expect(page.get_by_role("heading", name="正在优化存储布局...")).to_be_visible(timeout=10_000)
    page.wait_for_function("() => document.querySelector('[role=progressbar]') !== null")
    page.wait_for_timeout(100)
    assert len(pending_status_routes) == 1
    pending_status_routes[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {
                "ok": True,
                "ready": False,
                "status": "selection_required",
                "lifecycle_state": "selection_required",
                "migration_stage": "",
                "poll_interval_ms": 50,
                "blocking_reason": "selection_required",
                "storage": {
                    "selection_required": True,
                    "migration_pending": False,
                    "recovery_required": False,
                },
                "migration": {"status": ""},
            }
        ),
    )

    expect(page.get_by_role("heading", name="存储位置选择")).to_be_visible(timeout=10_000)
    expect(page.get_by_role("heading", name="正在优化存储布局...")).to_be_hidden()


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
                    "maintenance_message": "正在关闭，数据会在关闭后迁移并自动重启。",
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
        assert route.request.headers.get("x-csrf-token") == STORAGE_CSRF_TOKEN
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

    confirm_restart_button = page.get_by_role("button", name="确认并重启")
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
              "maintenance_message": "正在关闭，数据会在关闭后迁移并自动重启。",
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
def test_storage_location_retained_staging_reloads_bootstrap_and_enters_recovery_selection(
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
            body=json.dumps(
                {
                    "ok": True,
                    "status": "migration_required",
                    "ready": False,
                    "storage": {
                        "selection_required": False,
                        "migration_pending": True,
                        "recovery_required": False,
                        "blocking_reason": "migration_pending",
                    },
                }
            ),
        ),
    )

    def handle_bootstrap(route):
        bootstrap_requests["count"] += 1
        recovery_required = bootstrap_requests["count"] > 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "autostart_csrf_token": STORAGE_CSRF_TOKEN,
                    "current_root": "/tmp/runtime/N.E.K.O",
                    "recommended_root": "/tmp/recommended/N.E.K.O",
                    "legacy_sources": [],
                    "anchor_root": "/tmp/recommended/N.E.K.O",
                    "cloudsave_root": "/tmp/recommended/N.E.K.O/cloudsave",
                    "selection_required": False,
                    "migration_pending": not recovery_required,
                    "recovery_required": recovery_required,
                    "blocking_reason": "recovery_required" if recovery_required else "migration_pending",
                    "legacy_cleanup_pending": False,
                    "last_known_good_root": "/tmp/runtime/N.E.K.O",
                    "last_error_summary": "模拟迁移失败" if recovery_required else "",
                    "migration": {
                        "status": "recovery_required" if recovery_required else "pending",
                        "source_root": "/tmp/runtime/N.E.K.O",
                        "target_root": "/tmp/recommended/N.E.K.O",
                        "last_error": "模拟迁移失败" if recovery_required else "",
                    },
                    "stage": "stage3_web_restart",
                    "poll_interval_ms": 50,
                },
                ensure_ascii=False,
            ),
        )

    page.route("**/api/storage/location/bootstrap", handle_bootstrap)
    page.route(
        "**/api/storage/location/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "ready": False,
                    "status": "recovery_required",
                    "lifecycle_state": "recovery_required",
                    "migration_stage": "recovery_required",
                    "poll_interval_ms": 50,
                    "last_error_summary": "模拟迁移失败",
                    "blocking_reason": "recovery_required",
                    "storage": {
                        "selection_required": False,
                        "migration_pending": False,
                        "recovery_required": True,
                    },
                    "migration": {"status": "recovery_required"},
                },
                ensure_ascii=False,
            ),
        ),
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    expect(page.get_by_role("heading", name="存储位置选择")).to_be_visible(timeout=15_000)
    expect(page.locator("text=检测到需要恢复的存储状态")).to_be_visible(timeout=10_000)
    expect(page.locator("text=模拟迁移失败")).to_be_visible(timeout=10_000)
    expect(page.get_by_role("heading", name="正在优化存储布局...")).to_be_hidden()
    assert bootstrap_requests["count"] >= 2
    assert _page_config_state(page) == "pending"


@pytest.mark.frontend
def test_storage_location_awaiting_shutdown_offers_only_controlled_exit_retry(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    _mock_selection_required_state(page, migration_pending=True)
    exit_requests = {"count": 0}
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
                    "migration_phase": "awaiting_shutdown",
                    "shutdown_retry_allowed": True,
                    "poll_interval_ms": 50,
                    "blocking_reason": "migration_pending",
                    "storage": {
                        "migration_pending": True,
                        "migration_phase": "awaiting_shutdown",
                        "shutdown_retry_allowed": True,
                    },
                    "migration": {"status": "pending"},
                }
            ),
        ),
    )

    def handle_exit(route):
        exit_requests["count"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "result": "shutdown_initiated"}),
        )

    page.route("**/api/storage/location/exit", handle_exit)
    page.add_init_script(
        """
        window.__nekoHostCloseCalls = 0;
        window.nekoHost = {
            getBackendRecoveryState: async () => ({
                state: 'active',
                reason: 'owner_handoff_awaiting_shutdown',
                generation: 1,
                retry_allowed: false,
                quit_allowed: false,
                shutdown_retry_allowed: true,
            }),
            closeWindow: async () => {
                window.__nekoHostCloseCalls += 1;
                return { ok: true };
            },
        };
        """
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    retry_shutdown = page.get_by_role("button", name="重试安全关闭")
    expect(retry_shutdown).to_be_enabled(timeout=15_000)
    expect(page.get_by_role("button", name="安全退出应用")).to_be_hidden()
    retry_shutdown.click()
    expect(retry_shutdown).to_be_disabled(timeout=5_000)
    retry_shutdown.click(force=True)
    page.wait_for_timeout(300)

    assert exit_requests["count"] == 1
    assert page.evaluate("window.__nekoHostCloseCalls") == 0
    expect(page.locator("#storage-location-overlay")).to_be_visible()


@pytest.mark.frontend
@pytest.mark.parametrize("host_close_mode", ["ok", "rejected", "throws"])
def test_storage_location_unreadable_checkpoint_offers_evidence_preserving_safe_exit(
    mock_page: Page,
    running_server: str,
    host_close_mode: str,
):
    page = mock_page
    exit_requests = {"count": 0}
    status_payload = {
        "ok": True,
        "instance_id": "unreadable-checkpoint-instance",
        "status": "storage_status_unavailable",
        "lifecycle_state": "storage_status_unavailable",
        "ready": False,
        "blocking_reason": "storage_status_unavailable",
        "storage_status_unavailable": True,
        "error_code": "migration_checkpoint_malformed",
        "recovery_action": "safe_exit",
        "storage": {
            "status_unavailable": True,
            "blocking_reason": "storage_status_unavailable",
            "last_error_summary": "暂时无法读取存储状态，主界面将继续保持阻断。",
            "error_code": "migration_checkpoint_malformed",
        },
    }
    page.route(
        "**/api/system/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(status_payload, ensure_ascii=False),
        ),
    )
    page.route(
        "**/api/storage/location/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {**status_payload, "autostart_csrf_token": STORAGE_CSRF_TOKEN},
                ensure_ascii=False,
            ),
        ),
    )

    def handle_exit(route):
        exit_requests["count"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "result": "shutdown_initiated"}),
        )

    page.route("**/api/storage/location/exit", handle_exit)
    page.add_init_script(
        """
        window.__nekoHostCloseMode = %s;
        window.__nekoHostCloseCalls = 0;
        window.__nekoSafeQuitCalls = 0;
        window.nekoHost = {
            getBackendRecoveryState: async () => ({
                state: 'ready',
                reason: 'backend_ready',
                generation: 1,
                retry_allowed: false,
                quit_allowed: false,
            }),
            requestSafeQuit: async () => {
                window.__nekoSafeQuitCalls += 1;
                return { ok: true, action: 'quit_app' };
            },
            closeWindow: async () => {
                window.__nekoHostCloseCalls += 1;
                if (window.__nekoHostCloseMode === 'throws') {
                    throw new Error('simulated host close failure');
                }
                return { ok: window.__nekoHostCloseMode === 'ok' };
            },
        };
        """
        % json.dumps(host_close_mode)
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    expect(page.get_by_role("heading", name="暂时无法读取存储位置引导信息")).to_be_visible(
        timeout=15_000
    )
    expect(page.locator("text=存储状态文件当前无法可靠读取")).to_be_visible()
    expect(page.get_by_role("button", name="重试恢复服务")).to_be_hidden()
    safe_quit = page.get_by_role("button", name="安全退出应用")
    expect(safe_quit).to_be_enabled()
    safe_quit.click()
    page.wait_for_function("() => window.__nekoHostCloseCalls === 1", timeout=10_000)

    assert exit_requests["count"] == 1
    assert page.evaluate("window.__nekoSafeQuitCalls") == 0
    expect(page.locator("#storage-location-overlay")).to_be_visible()
    if host_close_mode != "ok":
        expect(page.locator("#storage-location-host-close-feedback")).to_be_visible()
        expect(page.locator("#storage-location-host-close-feedback")).to_contain_text(
            "安全退出未能启动",
            timeout=5_000,
        )
        _expect_storage_migration_has_no_scrollbars(page)
    else:
        expect(page.locator("#storage-location-host-close-feedback")).to_be_hidden()


@pytest.mark.frontend
def test_storage_location_unreadable_checkpoint_safe_exit_without_host_bridge(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    exit_requests = {"count": 0}
    status_payload = {
        "ok": True,
        "instance_id": "unreadable-checkpoint-browser-instance",
        "autostart_csrf_token": STORAGE_CSRF_TOKEN,
        "status": "storage_status_unavailable",
        "lifecycle_state": "storage_status_unavailable",
        "ready": False,
        "blocking_reason": "storage_status_unavailable",
        "storage_status_unavailable": True,
        "error_code": "migration_checkpoint_malformed",
        "recovery_action": "safe_exit",
        "storage": {
            "status_unavailable": True,
            "blocking_reason": "storage_status_unavailable",
            "last_error_summary": "暂时无法读取存储状态，主界面将继续保持阻断。",
            "error_code": "migration_checkpoint_malformed",
        },
    }
    for endpoint in ("system/status", "storage/location/status"):
        page.route(
            f"**/api/{endpoint}",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(status_payload, ensure_ascii=False),
            ),
        )

    def handle_exit(route):
        exit_requests["count"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "result": "shutdown_initiated"}),
        )

    page.route("**/api/storage/location/exit", handle_exit)
    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    safe_quit = page.get_by_role("button", name="安全退出应用")
    expect(safe_quit).to_be_enabled(timeout=15_000)
    safe_quit.click()
    page.wait_for_function("() => document.visibilityState === 'visible'")

    assert exit_requests["count"] == 1
    expect(page.locator("#storage-location-overlay")).to_be_visible()
    expect(page.locator("#storage-location-host-close-feedback")).to_be_visible(timeout=5_000)
    expect(page.locator("#storage-location-host-close-feedback")).to_contain_text(
        "受控关闭已请求，但浏览器无法自动关闭此窗口"
    )
    safe_quit.click()
    expect(page.locator("#storage-location-host-close-feedback")).to_be_visible(timeout=5_000)
    assert exit_requests["count"] == 1


@pytest.mark.frontend
def test_storage_location_shutdown_invalidates_pending_maintenance_ready_reload(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    pending_status_routes = []
    exit_requests = {"count": 0}
    document_requests = []
    unavailable_payload = {
        "ok": True,
        "instance_id": "pre-shutdown-instance",
        "autostart_csrf_token": STORAGE_CSRF_TOKEN,
        "status": "storage_status_unavailable",
        "lifecycle_state": "storage_status_unavailable",
        "ready": False,
        "blocking_reason": "storage_status_unavailable",
        "storage_status_unavailable": True,
        "error_code": "migration_checkpoint_malformed",
        "storage": {
            "status_unavailable": True,
            "blocking_reason": "storage_status_unavailable",
            "error_code": "migration_checkpoint_malformed",
        },
    }

    page.on(
        "request",
        lambda request: document_requests.append(request.url)
        if request.resource_type == "document"
        else None,
    )
    page.route(
        "**/api/system/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(unavailable_payload),
        ),
    )
    page.route(
        "**/api/storage/location/status",
        lambda route: pending_status_routes.append(route),
    )

    def handle_exit(route):
        exit_requests["count"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "result": "shutdown_initiated"}),
        )

    page.route("**/api/storage/location/exit", handle_exit)
    page.add_init_script(
        """
        window.__nekoHostCloseCalls = 0;
        window.nekoHost = {
            getBackendRecoveryState: async () => ({
                state: 'ready',
                reason: 'backend_ready',
                generation: 1,
                retry_allowed: false,
                quit_allowed: false,
            }),
            closeWindow: async () => {
                window.__nekoHostCloseCalls += 1;
                return { ok: false, error: 'simulated close failure' };
            },
        };
        """
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    safe_quit = page.get_by_role("button", name="安全退出应用")
    expect(safe_quit).to_be_enabled(timeout=15_000)
    page.wait_for_function("() => document.visibilityState === 'visible'")
    assert len(pending_status_routes) == 1

    safe_quit.click()
    expect(page.locator("#storage-location-host-close-feedback")).to_be_visible(timeout=5_000)
    assert exit_requests["count"] == 1
    assert page.evaluate("window.__nekoHostCloseCalls") == 1

    pending_status_routes[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {
                "ok": True,
                "instance_id": "post-migration-instance",
                "status": "ready",
                "lifecycle_state": "ready",
                "ready": True,
                "blocking_reason": "",
                "storage": {
                    "selection_required": False,
                    "migration_pending": False,
                    "recovery_required": False,
                    "blocking_reason": "",
                },
            }
        ),
    )
    page.wait_for_timeout(500)
    assert len(document_requests) == 1
    expect(page.locator("#storage-location-overlay")).to_be_visible()
    assert _page_config_state(page) == "pending"


@pytest.mark.frontend
@pytest.mark.parametrize("shutdown_accepted", [True, False])
def test_storage_location_external_maintenance_during_close_obeys_close_outcome(
    mock_page: Page,
    running_server: str,
    shutdown_accepted: bool,
):
    page = mock_page
    _mock_selection_required_state(page)
    pending_exit_routes = []
    maintenance_status_requests = {"count": 0}
    document_requests = []

    page.on(
        "request",
        lambda request: document_requests.append(request.url)
        if request.resource_type == "document"
        else None,
    )
    page.route(
        "**/api/storage/location/exit",
        lambda route: pending_exit_routes.append(route),
    )

    def handle_maintenance_status(route):
        maintenance_status_requests["count"] += 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "instance_id": "external-maintenance-instance",
                    "status": "maintenance",
                    "lifecycle_state": "maintenance",
                    "ready": False,
                    "blocking_reason": "migration_pending",
                    "migration_stage": "copying",
                    "poll_interval_ms": 1000,
                    "storage": {"migration_pending": True},
                }
            ),
        )

    page.route("**/api/storage/location/status", handle_maintenance_status)
    page.add_init_script(
        """
        window.__nekoHostCloseCalls = 0;
        window.__nekoHostCloseShouldSucceed = false;
        window.nekoHost = {
            getBackendRecoveryState: async () => ({
                state: 'ready',
                reason: 'backend_ready',
                generation: 1,
            }),
            closeWindow: async () => {
                window.__nekoHostCloseCalls += 1;
                return window.__nekoHostCloseShouldSucceed
                    ? { ok: true }
                    : { ok: false, error: 'simulated close failure' };
            },
        };
        """
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    expect(page.locator(".storage-location-intro-card")).to_be_visible(timeout=15_000)
    _arm_page_config_resolution_probe(page)
    page.locator(".storage-location-modal > .storage-location-close").click()
    page.wait_for_timeout(100)
    assert len(pending_exit_routes) == 1

    page.evaluate(
        """
        () => window.appStorageLocation.enterExternalMaintenanceMode({
            result: 'restart_initiated',
            restart_mode: 'migrate_after_shutdown',
            target_root: '/tmp/external/N.E.K.O',
            selection_source: 'recommended',
            instance_id: 'external-maintenance-instance',
            status: 'maintenance',
            lifecycle_state: 'maintenance',
            blocking_reason: 'migration_pending',
            migration_stage: 'copying',
            storage: { migration_pending: true },
            migration: { status: 'copying' },
        })
        """
    )
    expect(page.get_by_role("heading", name="正在优化存储布局...")).to_be_visible()
    assert maintenance_status_requests["count"] == 0

    pending_exit_routes[0].fulfill(
        status=200 if shutdown_accepted else 503,
        content_type="application/json",
        body=json.dumps(
            {"ok": True, "result": "shutdown_initiated"}
            if shutdown_accepted
            else {"ok": False, "error": "shutdown unavailable"}
        ),
    )
    page.wait_for_timeout(500)

    assert len(document_requests) == 1
    assert _page_config_state(page) == "pending"
    expect(page.locator("#storage-location-overlay")).to_be_visible()
    if shutdown_accepted:
        assert maintenance_status_requests["count"] == 0
        assert page.evaluate("window.__nekoHostCloseCalls") == 1
        expect(page.locator("#storage-location-host-close-feedback")).to_be_visible()
        page.evaluate(
            """
            () => window.appStorageLocation.enterExternalMaintenanceMode({
                result: 'restart_initiated',
                target_root: '/tmp/late/N.E.K.O',
                status: 'maintenance',
                storage: { migration_pending: true },
            })
            """
        )
        expect(page.locator("#storage-location-host-close-feedback")).to_be_visible()
        assert maintenance_status_requests["count"] == 0
        page.evaluate("window.__nekoHostCloseShouldSucceed = true")
        page.locator(".storage-location-modal > .storage-location-close").click(force=True)
        page.wait_for_timeout(200)
        assert page.evaluate("window.__nekoHostCloseCalls") == 2
        assert len(pending_exit_routes) == 1
    else:
        assert maintenance_status_requests["count"] >= 1
        assert page.evaluate("window.__nekoHostCloseCalls") == 0


@pytest.mark.frontend
def test_storage_location_online_rollback_required_is_actionable_terminal_state(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    _mock_selection_required_state(page, migration_pending=True)
    exit_requests = {"count": 0}
    page.route(
        "**/api/storage/location/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "ready": False,
                    "status": "rollback_required",
                    "lifecycle_state": "rollback_required",
                    "migration_stage": "rollback_required",
                    "maintenance_message": "安全回滚校验失败：目标目录与迁移前状态不一致。",
                    "poll_interval_ms": 50,
                    "blocking_reason": "migration_pending",
                    "storage": {
                        "selection_required": False,
                        "migration_pending": True,
                        "recovery_required": False,
                        "rollback_required": True,
                    },
                    "migration": {"status": "rollback_required"},
                },
                ensure_ascii=False,
            ),
        ),
    )
    page.add_init_script(
        """
        window.__nekoSafeQuitCalls = 0;
        window.__nekoHostCloseCalls = 0;
        window.nekoHost = {
            getBackendRecoveryState: async () => ({
                state: 'ready',
                reason: 'backend_recovery_required',
                generation: 2,
                retry_allowed: false,
                quit_allowed: false,
            }),
            requestSafeQuit: async () => {
                window.__nekoSafeQuitCalls += 1;
                return { ok: true, action: 'quit_app' };
            },
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
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "result": "shutdown_initiated"}),
        )

    page.route("**/api/storage/location/exit", handle_exit)

    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    expect(page.get_by_role("heading", name="存储迁移需要恢复")).to_be_visible(timeout=15_000)
    expect(page.locator("text=安全回滚校验失败：目标目录与迁移前状态不一致。")).to_be_visible()
    expect(page.locator("text=迁移已停止，需要恢复处理")).to_be_visible()
    expect(page.get_by_role("button", name="重试恢复服务")).to_be_hidden()
    rollback_quit = page.get_by_role("button", name="安全退出并在下次启动恢复")
    expect(rollback_quit).to_be_enabled()
    rollback_quit.click()
    page.wait_for_function("() => window.__nekoHostCloseCalls === 1", timeout=10_000)
    assert exit_requests["count"] == 1
    assert page.evaluate("window.__nekoSafeQuitCalls") == 0


@pytest.mark.frontend
def test_storage_location_status_body_timeout_reaches_terminal_host_fallback(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    _mock_selection_required_state(page, migration_pending=True)
    system_requests = {"count": 0}
    page.add_init_script(
        """
        const originalFetch = window.fetch.bind(window);
        window.__nekoHalfBodyStatusCalls = 0;
        window.fetch = (input, options) => {
            const url = String(input && input.url || input || '');
            if (url.includes('/api/storage/location/status')) {
                window.__nekoHalfBodyStatusCalls += 1;
                const stream = new ReadableStream({
                    start(controller) {
                        controller.enqueue(new TextEncoder().encode('{"ok":true'));
                    },
                });
                return Promise.resolve(new Response(stream, {
                    status: 200,
                    headers: { 'Content-Type': 'application/json' },
                }));
            }
            return originalFetch(input, options);
        };
        window.nekoHost = {
            getBackendRecoveryState: async () => ({
                state: 'terminal',
                reason: 'owner_relaunch_failed',
                generation: 2,
                retry_allowed: true,
                quit_allowed: true,
            }),
            retryBackendRecovery: async () => ({ ok: false }),
            requestSafeQuit: async () => ({ ok: true, action: 'quit_app' }),
            closeWindow: async () => ({ ok: false }),
        };
        """
    )
    def handle_system_status(route):
        system_requests["count"] += 1
        if system_requests["count"] == 1:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        "status": "migration_required",
                        "ready": False,
                        "storage": {
                            "selection_required": False,
                            "migration_pending": True,
                            "recovery_required": False,
                            "blocking_reason": "migration_pending",
                        },
                    }
                ),
            )
            return
        route.fulfill(status=503, content_type="application/json", body='{"ok":false}')

    page.route("**/api/system/status", handle_system_status)

    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    expect(page.get_by_role("heading", name="存储服务需要恢复")).to_be_visible(timeout=15_000)
    expect(page.get_by_role("button", name="安全退出应用")).to_be_enabled()
    assert page.evaluate("window.__nekoHalfBodyStatusCalls") >= 1


@pytest.mark.frontend
@pytest.mark.parametrize("safe_quit_mode", ["ok", "rejected", "throws", "pending"])
def test_storage_location_terminal_host_failure_offers_verified_safe_quit(
    mock_page: Page,
    running_server: str,
    safe_quit_mode: str,
):
    page = mock_page
    system_requests = {"count": 0}

    def handle_system_status(route):
        system_requests["count"] += 1
        if system_requests["count"] == 1:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        "status": "migration_required",
                        "ready": False,
                        "storage": {
                            "selection_required": False,
                            "migration_pending": True,
                            "recovery_required": False,
                            "blocking_reason": "migration_pending",
                        },
                    }
                ),
            )
            return
        route.fulfill(status=503, content_type="application/json", body='{"ok":false}')

    page.route("**/api/system/status", handle_system_status)
    page.route(
        "**/api/storage/location/bootstrap",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "autostart_csrf_token": STORAGE_CSRF_TOKEN,
                    "current_root": "/tmp/runtime/N.E.K.O",
                    "recommended_root": "/tmp/recommended/N.E.K.O",
                    "legacy_sources": [],
                    "anchor_root": "/tmp/recommended/N.E.K.O",
                    "cloudsave_root": "/tmp/recommended/N.E.K.O/cloudsave",
                    "selection_required": False,
                    "migration_pending": True,
                    "recovery_required": False,
                    "blocking_reason": "migration_pending",
                    "legacy_cleanup_pending": False,
                    "last_known_good_root": "/tmp/runtime/N.E.K.O",
                    "last_error_summary": "",
                    "migration": {"status": "pending"},
                    "stage": "stage3_web_restart",
                    "poll_interval_ms": 50,
                }
            ),
        ),
    )
    page.route(
        "**/api/storage/location/status",
        lambda route: route.fulfill(status=503, content_type="application/json", body='{"ok":false}'),
    )
    page.add_init_script(
        """
        window.__nekoSafeQuitMode = %s;
        window.__nekoSafeQuitCalls = 0;
        window.__nekoRetryRecoveryCalls = 0;
        window.__resolveNekoSafeQuit = null;
        window.__nekoRetryRecoveryPending = false;
        window.__resolveNekoRetryRecovery = null;
        window.__nekoHostCloseCalls = 0;
        window.nekoHost = {
            getBackendRecoveryState: async () => ({
                state: 'terminal',
                reason: 'owner_relaunch_failed',
                generation: 2,
                retry_allowed: true,
                quit_allowed: true,
            }),
            retryBackendRecovery: async () => {
                window.__nekoRetryRecoveryCalls += 1;
                if (window.__nekoRetryRecoveryPending) {
                    return await new Promise(resolve => {
                        window.__resolveNekoRetryRecovery = resolve;
                    });
                }
                return {
                    state: 'transient',
                    reason: 'retry_started',
                    generation: 3,
                    retry_allowed: false,
                    quit_allowed: false,
                };
            },
            requestSafeQuit: async () => {
                window.__nekoSafeQuitCalls += 1;
                if (window.__nekoSafeQuitMode === 'throws') {
                    throw new Error('simulated safe quit failure');
                }
                if (window.__nekoSafeQuitMode === 'pending') {
                    return await new Promise(resolve => {
                        window.__resolveNekoSafeQuit = resolve;
                    });
                }
                return {
                    ok: window.__nekoSafeQuitMode === 'ok',
                    action: window.__nekoSafeQuitMode === 'ok' ? 'quit_app' : 'rejected',
                };
            },
            closeWindow: async () => {
                window.__nekoHostCloseCalls += 1;
                return { ok: true };
            },
        };
        """
        % json.dumps(safe_quit_mode)
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")

    expect(page.get_by_role("heading", name="存储服务需要恢复")).to_be_visible(timeout=15_000)
    safe_quit = page.get_by_role("button", name="安全退出应用")
    expect(safe_quit).to_be_enabled(timeout=10_000)
    safe_quit.click()
    page.wait_for_function("() => window.__nekoSafeQuitCalls === 1", timeout=10_000)
    if safe_quit_mode == "pending":
        expect(safe_quit).to_be_disabled()
        retry_recovery = page.get_by_role("button", name="重试恢复服务")
        expect(retry_recovery).to_be_disabled()
        retry_recovery.click(force=True)
        page.locator(".storage-location-modal > .storage-location-close").click(force=True)
        page.wait_for_timeout(100)
        assert page.evaluate("window.__nekoSafeQuitCalls") == 1
        assert page.evaluate("window.__nekoRetryRecoveryCalls") == 0
        page.evaluate("window.__resolveNekoSafeQuit({ ok: false, action: 'rejected' })")
        expect(page.locator("#storage-location-host-close-feedback")).to_be_visible(timeout=5_000)
        expect(retry_recovery).to_be_enabled(timeout=5_000)
        page.evaluate("window.__nekoRetryRecoveryPending = true")
        retry_recovery.click()
        page.wait_for_function("() => window.__nekoRetryRecoveryCalls === 1", timeout=5_000)
        expect(safe_quit).to_be_disabled()
        safe_quit.click(force=True)
        page.locator(".storage-location-modal > .storage-location-close").click(force=True)
        page.wait_for_timeout(100)
        assert page.evaluate("window.__nekoSafeQuitCalls") == 1
        assert page.evaluate("window.__nekoRetryRecoveryCalls") == 1
        page.evaluate(
            """
            window.__resolveNekoRetryRecovery({
                state: 'transient',
                reason: 'retry_started',
                generation: 3,
                retry_allowed: false,
                quit_allowed: false,
            })
            """
        )
    assert page.evaluate("window.__nekoHostCloseCalls") == 0
    expect(page.locator("#storage-location-overlay")).to_be_visible()
    if safe_quit_mode == "ok":
        expect(page.locator("#storage-location-host-close-feedback")).to_be_hidden()
    else:
        expect(page.locator("#storage-location-host-close-feedback")).to_be_visible(timeout=5_000)
        expect(page.locator("#storage-location-host-close-feedback")).to_contain_text(
            "安全退出未能启动"
        )


@pytest.mark.frontend
def test_storage_location_system_status_nested_recovery_leaves_external_maintenance(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    system_requests = {"count": 0}

    def handle_system_status(route):
        system_requests["count"] += 1
        ready = system_requests["count"] == 1
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "ok": True,
                    "status": "ready" if ready else "migration_required",
                    "ready": ready,
                    "storage": {
                        "selection_required": not ready,
                        "migration_pending": False,
                        "recovery_required": not ready,
                        "blocking_reason": "" if ready else "recovery_required",
                        "last_error_summary": "系统状态报告需要恢复" if not ready else "",
                    },
                },
                ensure_ascii=False,
            ),
        )

    page.route("**/api/system/status", handle_system_status)
    page.route(
        "**/api/storage/location/status",
        lambda route: route.fulfill(status=503, content_type="application/json", body='{"ok":false}'),
    )
    page.route(
        "**/api/storage/location/bootstrap",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "autostart_csrf_token": STORAGE_CSRF_TOKEN,
                    "current_root": "/tmp/runtime/N.E.K.O",
                    "recommended_root": "/tmp/recommended/N.E.K.O",
                    "legacy_sources": [],
                    "anchor_root": "/tmp/recommended/N.E.K.O",
                    "cloudsave_root": "/tmp/recommended/N.E.K.O/cloudsave",
                    "selection_required": True,
                    "migration_pending": False,
                    "recovery_required": True,
                    "blocking_reason": "recovery_required",
                    "legacy_cleanup_pending": False,
                    "last_known_good_root": "/tmp/runtime/N.E.K.O",
                    "last_error_summary": "系统状态报告需要恢复",
                    "migration": {"status": "failed"},
                    "stage": "stage3_web_restart",
                    "poll_interval_ms": 50,
                },
                ensure_ascii=False,
            ),
        ),
    )

    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    expect(page.locator("#storage-location-overlay")).to_be_hidden(timeout=15_000)
    page.evaluate(
        """
        window.appStorageLocation.enterExternalMaintenanceMode({
            result: 'restart_initiated',
            restart_mode: 'migrate_after_shutdown',
            migration: { status: 'pending' },
        });
        """
    )

    expect(page.get_by_role("heading", name="存储位置选择")).to_be_visible(timeout=15_000)
    expect(page.locator("text=系统状态报告需要恢复")).to_be_visible(timeout=10_000)


@pytest.mark.frontend
def test_storage_location_repeated_external_maintenance_keeps_one_poll_owner(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    pending_status_routes = []
    _mock_selection_required_state(page)
    page.route("**/api/storage/location/status", lambda route: pending_status_routes.append(route))
    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    expect(page.locator(".storage-location-intro-card")).to_be_visible(timeout=15_000)

    page.evaluate(
        """
        window.appStorageLocation.enterExternalMaintenanceMode({
            result: 'restart_initiated',
            restart_mode: 'migrate_after_shutdown',
            target_root: '/tmp/one/N.E.K.O',
            migration: { status: 'pending' },
        });
        """
    )
    page.wait_for_function("() => document.querySelector('[role=progressbar]') !== null")
    page.wait_for_timeout(100)
    page.evaluate(
        """
        window.appStorageLocation.enterExternalMaintenanceMode({
            result: 'restart_initiated',
            restart_mode: 'migrate_after_shutdown',
            target_root: '/tmp/two/N.E.K.O',
            migration: { status: 'pending' },
        });
        """
    )
    page.wait_for_timeout(250)

    assert len(pending_status_routes) == 1
    pending_status_routes[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {
                "ok": True,
                "ready": False,
                "status": "maintenance",
                "lifecycle_state": "maintenance",
                "migration_stage": "pending",
                "poll_interval_ms": 10_000,
                "blocking_reason": "migration_pending",
                "storage": {"migration_pending": True},
                "migration": {"status": "pending"},
            }
        ),
    )


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
        {
            window.__nekoOpenPathCalls = [];
            window.nekoHost = {
                openPath: async (payload) => {
                    window.__nekoOpenPathCalls.push(payload && payload.path);
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
                    "autostart_csrf_token": STORAGE_CSRF_TOKEN,
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
        assert route.request.headers.get("x-csrf-token") == STORAGE_CSRF_TOKEN
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
    defer_button = completion_card.get_by_role("button", name="暂时不处理")
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
    _set_home_tutorial_startup_released(page, False)
    page.evaluate("window.appStorageLocation.refreshCompletionNotice()")
    expect(completion_card).to_be_hidden(timeout=10_000)

    page.evaluate(
        """
        () => window.dispatchEvent(new CustomEvent('neko:tutorial-completed', {
            detail: { page: 'home' },
        }))
        """
    )
    expect(completion_card).to_be_hidden(timeout=10_000)

    _set_home_tutorial_startup_released(page, True)
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
    expect(cleanup_button).to_have_text("清理旧数据")
    expect(defer_button).to_be_visible(timeout=10_000)
    expect(defer_button).to_have_class(re.compile(r"\bstorage-location-completion-later\b"))
    open_target_button = completion_card.get_by_role("button", name="打开当前路径")
    open_retained_button = completion_card.get_by_role("button", name="打开旧数据目录")
    expect(open_target_button).to_be_visible(timeout=10_000)
    expect(open_retained_button).to_be_visible(timeout=10_000)
    expect(open_target_button).to_have_class(re.compile(r"\bstorage-location-completion-link\b"))
    expect(open_retained_button).to_have_class(re.compile(r"\bstorage-location-completion-link\b"))
    expect(open_target_button.locator("xpath=..")).to_have_class(
        re.compile(r"\bstorage-location-completion-path-item\b")
    )
    link_style = open_target_button.evaluate(
        """
        (element) => ({
            borderTopWidth: getComputedStyle(element).borderTopWidth,
            backgroundColor: getComputedStyle(element).backgroundColor,
            underlineHeight: getComputedStyle(element, '::after').height,
            underlineOpacity: getComputedStyle(element, '::after').opacity,
        })
        """
    )
    assert link_style == {
        "borderTopWidth": "0px",
        "backgroundColor": "rgba(0, 0, 0, 0)",
        "underlineHeight": "1px",
        "underlineOpacity": "0.28",
    }
    cleanup_box = cleanup_button.bounding_box()
    defer_box = defer_button.bounding_box()
    assert cleanup_box is not None
    assert defer_box is not None
    assert cleanup_box["x"] < defer_box["x"]
    assert abs(cleanup_box["y"] - defer_box["y"]) <= 1
    defer_style = defer_button.evaluate(
        """
        (element) => ({
            borderTopWidth: getComputedStyle(element).borderTopWidth,
            color: getComputedStyle(element).color,
            backgroundImage: getComputedStyle(element).backgroundImage,
        })
        """
    )
    cleanup_border_width = cleanup_button.evaluate(
        "(element) => getComputedStyle(element).borderTopWidth"
    )
    assert cleanup_border_width == "0px"
    assert defer_style["borderTopWidth"] == "0px"
    assert defer_style["color"] == "rgb(248, 251, 255)"
    assert "linear-gradient" in defer_style["backgroundImage"]
    assert "rgb(174, 181, 188)" in defer_style["backgroundImage"]
    expect(completion_card.locator(".storage-location-actions button", has_text="关闭")).to_have_count(0)

    original_viewport = page.viewport_size
    assert original_viewport is not None
    page.set_viewport_size({"width": 390, "height": 800})
    narrow_card_box = completion_card.bounding_box()
    narrow_path_item_box = open_target_button.locator("xpath=..").bounding_box()
    narrow_link_box = open_target_button.bounding_box()
    narrow_cleanup_box = cleanup_button.bounding_box()
    narrow_defer_box = defer_button.bounding_box()
    assert narrow_card_box is not None
    assert narrow_path_item_box is not None
    assert narrow_link_box is not None
    assert narrow_cleanup_box is not None
    assert narrow_defer_box is not None
    assert narrow_card_box["x"] >= 0
    assert narrow_card_box["x"] + narrow_card_box["width"] <= 390
    assert abs(narrow_link_box["x"] - narrow_path_item_box["x"]) <= 1
    assert abs(narrow_cleanup_box["y"] - narrow_defer_box["y"]) <= 1
    page.set_viewport_size(original_viewport)

    open_target_button.click()
    open_retained_button.click()
    page.wait_for_function(
        "window.__nekoOpenPathCalls && window.__nekoOpenPathCalls.length === 2",
        timeout=10_000,
    )
    assert page.evaluate("window.__nekoOpenPathCalls") == [target_root, source_root]
    assert cleanup_requests["count"] == 0

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
@pytest.mark.parametrize(
    "retained_outcome",
    ["cleaned", "present", "unknown", "in_progress_then_cleaned"],
)
def test_storage_location_cleanup_network_failure_reconciles_authoritative_result(
    mock_page: Page,
    running_server: str,
    tmp_path,
    retained_outcome: str,
):
    page = mock_page
    retained_status_requests = {"count": 0}
    source_root = str((tmp_path / "source-root" / "N.E.K.O").resolve())
    target_root = str((tmp_path / "target-root" / "N.E.K.O").resolve())
    completion_notice = {
        "completed": True,
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
            body=json.dumps({"ok": True, "status": "ready", "ready": True, "storage": {}}),
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
                    "autostart_csrf_token": STORAGE_CSRF_TOKEN,
                    "ready": True,
                    "status": "ready",
                    "lifecycle_state": "ready",
                    "completion_notice": completion_notice,
                    "storage": {},
                }
            ),
        ),
    )
    page.route(
        "**/api/storage/location/retained-source/cleanup",
        lambda route: route.abort("connectionreset"),
    )

    def handle_retained_status(route):
        retained_status_requests["count"] += 1
        if retained_outcome == "unknown":
            route.abort("connectionreset")
            return
        if retained_outcome == "present":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        **completion_notice,
                        "cleanup_in_progress": False,
                    }
                ),
            )
            return
        if (
            retained_outcome == "in_progress_then_cleaned"
            and retained_status_requests["count"] <= 8
        ):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": True,
                        **completion_notice,
                        "cleanup_in_progress": True,
                    }
                ),
            )
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "completed": False}),
        )

    page.route("**/api/storage/location/retained-source", handle_retained_status)
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(f"{running_server}/", wait_until="domcontentloaded")
    page.wait_for_function(
        """
        () => !!(
            window.appStorageLocation
            && typeof window.appStorageLocation.refreshCompletionNotice === 'function'
        )
        """,
        timeout=10_000,
    )
    _set_home_tutorial_startup_released(page, True)
    page.evaluate("window.appStorageLocation.refreshCompletionNotice()")

    completion_card = page.locator(".storage-location-completion-card")
    expect(completion_card).to_be_visible(timeout=10_000)
    completion_card.get_by_role("button", name="清理旧数据").evaluate("button => button.click()")

    if retained_outcome == "in_progress_then_cleaned":
        page.wait_for_timeout(1200)
        assert retained_status_requests["count"] >= 2
        expect(completion_card.get_by_role("button", name="清理旧数据")).to_be_disabled()
        expect(completion_card).to_be_hidden(timeout=10_000)
    elif retained_outcome == "cleaned":
        expect(completion_card).to_be_hidden(timeout=10_000)
    elif retained_outcome == "present":
        expect(completion_card).to_be_visible(timeout=10_000)
        expect(completion_card.get_by_role("button", name="清理旧数据")).to_be_enabled(
            timeout=10_000,
        )
    else:
        page.wait_for_timeout(1000)
        expect(completion_card).to_be_visible(timeout=10_000)
        expect(completion_card.get_by_role("button", name="清理旧数据")).to_be_disabled()
    assert retained_status_requests["count"] >= 1


@pytest.mark.frontend
def test_storage_location_completion_notice_close_is_remembered_and_defer_is_session_only(
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
    _set_home_tutorial_startup_released(page, True)

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

    completion_card.get_by_role("button", name="暂时不处理").click()
    expect(completion_card).to_be_hidden(timeout=10_000)

    page.evaluate("window.appStorageLocation.refreshCompletionNotice()")
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

    completion_notice["completed_at"] = "2026-04-25T00:00:00Z"
    page.evaluate("window.appStorageLocation.refreshCompletionNotice()")
    expect(completion_card).to_be_hidden(timeout=10_000)

    page.reload(wait_until="domcontentloaded")
    _set_home_tutorial_startup_released(page, True)
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
    _set_home_tutorial_startup_released(page, True)
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
