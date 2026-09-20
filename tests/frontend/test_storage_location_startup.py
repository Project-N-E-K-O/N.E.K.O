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
def test_storage_location_current_path_confirmation_keeps_page_blocked_for_safe_restart(
    mock_page: Page,
    running_server: str,
):
    page = mock_page
    _mock_selection_required_state(page)
    restart_requested = {"value": False}
    preflight_polls = {"count": 0}
    select_requests = {"count": 0}
    restart_operation_id = "same-root-rebind-operation"
    page.add_init_script(
        """(() => {
            const realNow = performance.now.bind(performance);
            window.__storagePreflightClockOffset = 0;
            Object.defineProperty(performance, 'now', {
                configurable: true,
                value: () => realNow() + window.__storagePreflightClockOffset
            });
        })();"""
    )

    def handle_select(route):
        select_requests["count"] += 1
        route.fulfill(
            status=202,
            content_type="application/json",
            body="""
            {
              "ok": true,
              "result": "preflight_pending",
              "preflight_operation_id": "p.same-root-preflight",
              "instance_id": "same-generation"
            }
            """,
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
        if "preflight_operation_id=" in route.request.url:
            preflight_polls["count"] += 1
            if preflight_polls["count"] == 1:
                route.fulfill(status=503, content_type="application/json", json={"error": "temporary"})
                return
            route.fulfill(
                status=200,
                content_type="application/json",
                json={
                    "instance_id": "same-generation",
                    "preflight_operation": {
                        "operation_id": "p.same-root-preflight",
                        "instance_id": "same-generation",
                        "state": "in_flight" if preflight_polls["count"] == 2 else "completed",
                        "response_status_code": 200,
                        "response_payload": {
                            "ok": True,
                            "result": "restart_required",
                            "restart_operation_id": restart_operation_id,
                            "restart_mode": "rebind_only",
                            "selected_root": "/tmp/runtime/N.E.K.O",
                            "selection_source": "user_selected",
                            "migration_phase": "awaiting_shutdown",
                            "shutdown_retry_allowed": True,
                        },
                    },
                },
            )
            return
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
    page.route("**/api/storage/location/status**", handle_maintenance_status)
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

    page.get_by_role("button", name="其他位置").click()
    with page.expect_response(lambda r: "preflight_operation_id=" in r.url and r.status == 503):
        page.get_by_role("button", name="使用推荐路径").click()
    page.evaluate("window.__storagePreflightClockOffset = 121000")
    expect(page.locator(".storage-location-shell--selection > p.storage-location-note")).to_contain_text("仍未完成", timeout=5000)
    page.evaluate("window.__storagePreflightClockOffset = 0")
    page.get_by_role("button", name="使用推荐路径").click()
    expect(page.get_by_role("button", name="确认并重启到原路径")).to_be_visible(timeout=10_000)
    assert preflight_polls["count"] >= 3
    assert select_requests["count"] == 1
    page.get_by_role("button", name="确认并重启到原路径").click()

    expect(overlay).to_be_visible(timeout=10_000)
    expect(page.get_by_role("heading", name="正在优化存储布局...")).to_be_visible(timeout=10_000)
    assert _page_config_state(page) == "pending"


@pytest.mark.frontend
@pytest.mark.parametrize("close_phase", ["selection_intro", "selection_required", "preview", "preflight_wait"])
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
    exit_completed = {"value": False}
    preflight_status_polls = {"count": 0}
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
        if close_phase == "preflight_wait":
            route.fulfill(
                status=202,
                content_type="application/json",
                json={
                    "ok": True,
                    "result": "preflight_pending",
                    "preflight_operation_id": "p.close-preflight",
                    "instance_id": "close-generation",
                },
            )
            return
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
    if close_phase == "preflight_wait":
        def handle_preflight_status(route):
            preflight_status_polls["count"] += 1
            route.fulfill(
                status=200,
                content_type="application/json",
                json={
                    "instance_id": "close-generation",
                    "preflight_operation": {
                        "operation_id": "p.close-preflight",
                        "instance_id": "close-generation",
                        "state": "completed" if exit_completed["value"] else "in_flight",
                        "response_status_code": 200,
                        "response_payload": {
                            "ok": True,
                            "result": "restart_required",
                            "restart_operation_id": "late-preflight-result",
                            "selected_root": "/tmp/runtime/N.E.K.O",
                        },
                    },
                },
            )

        page.route("**/api/storage/location/status?preflight_operation_id=**", handle_preflight_status)
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
    elif close_phase == "preflight_wait":
        with page.expect_response(lambda r: "preflight_operation_id=" in r.url):
            page.get_by_role("button", name="使用推荐路径").click()
        expect(page.get_by_role("button", name="使用推荐路径")).to_be_disabled()
        assert preflight_status_polls["count"] >= 1

    if close_phase == "selection_intro":
        mutation_button = page.get_by_role("button", name="推荐存储位置")
    elif close_phase in {"selection_required", "preflight_wait"}:
        mutation_button = page.get_by_role("button", name="使用推荐路径")
    else:
        mutation_button = page.get_by_role("button", name="确认并重启到原路径")

    initial_select_requests = select_requests["count"]
    page.locator(".storage-location-modal > .storage-location-close").click()
    expect(mutation_button).to_be_disabled(timeout=5_000)
    assert len(pending_exit_routes) == 1
    page.evaluate("window.dispatchEvent(new Event('localechange'))")
    expect(mutation_button).to_be_disabled()
    mutation_button.click(force=True)
    page.wait_for_timeout(100)
    assert select_requests["count"] == initial_select_requests
    assert restart_requests["count"] == 0

    pending_exit_routes[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps({"ok": True, "result": "shutdown_initiated"}),
    )
    exit_completed["value"] = True
    page.wait_for_function("() => window.__nekoHostCloseCalls === 1", timeout=10_000)

    assert exit_requests["count"] == 1
    expect(mutation_button).to_be_disabled()
    assert _page_config_state(page) == "pending"
    if close_phase == "preflight_wait":
        page.wait_for_timeout(1500)
        expect(page.get_by_role("button", name="确认并重启到原路径")).to_have_count(0)


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
