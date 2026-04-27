import pytest
from pathlib import Path
from playwright.sync_api import BrowserContext, Page, expect

from utils.file_utils import atomic_write_json
from utils.storage_policy import save_storage_policy


def _request_json(route):
    post_data_json = route.request.post_data_json
    return post_data_json() if callable(post_data_json) else post_data_json


@pytest.fixture
def seed_memory_file(clean_user_data_dir, running_server):
    """Create a seed memory file in the test memory directory."""
    app_root = Path(clean_user_data_dir) / "N.E.K.O"
    save_storage_policy(
        None,
        selected_root=app_root,
        anchor_root=app_root,
        selection_source="test",
    )

    memory_dir = app_root / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    catgirl_dir = memory_dir / "测试猫娘"
    catgirl_dir.mkdir(parents=True, exist_ok=True)
    
    # Create a minimal recent memory file for a test catgirl
    test_data = [
        {
            "type": "system",
            "data": {
                "content": "先前对话的备忘录: 这是测试备忘录内容。",
                "additional_kwargs": {},
                "response_metadata": {},
                "type": "system",
                "name": None,
                "id": None,
                "example": False
            }
        },
        {
            "type": "human",
            "data": {
                "content": "你好，测试猫娘！",
                "additional_kwargs": {},
                "response_metadata": {},
                "type": "human",
                "name": None,
                "id": None,
                "example": False
            }
        },
        {
            "type": "ai",
            "data": {
                "content": "[2026-01-01 12:00:00] 你好主人！我是测试猫娘喵~",
                "additional_kwargs": {},
                "response_metadata": {},
                "type": "ai",
                "name": None,
                "id": None,
                "example": False,
                "tool_calls": [],
                "invalid_tool_calls": [],
                "usage_metadata": None
            }
        }
    ]
    
    memory_file = catgirl_dir / "recent.json"
    atomic_write_json(memory_file, test_data, ensure_ascii=False, indent=2)
    
    return memory_file


def _install_ready_memory_browser_routes(page: Page | BrowserContext, memory_file: Path) -> None:
    """Mock storage + memory APIs so the page (or whole context) is tested in ready mode."""
    app_root = memory_file.parents[2]
    review_state = {"enabled": True}

    def handle_bootstrap(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "current_root": str(app_root),
                "recommended_root": str(app_root),
                "legacy_sources": [],
                "selection_required": False,
                "migration_pending": False,
                "recovery_required": False,
                "blocking_reason": "",
            },
        )

    def handle_recent_files(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"files": ["recent_测试猫娘.json"]},
        )

    def handle_current_catgirl(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"current_catgirl": "测试猫娘"},
        )

    def handle_recent_file(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"content": memory_file.read_text(encoding="utf-8")},
        )

    def handle_review_config(route):
        if route.request.method == "POST":
            payload = _request_json(route)
            review_state["enabled"] = bool(payload.get("enabled"))
            route.fulfill(status=200, content_type="application/json", json={"success": True, "enabled": review_state["enabled"]})
            return
        route.fulfill(status=200, content_type="application/json", json={"enabled": review_state["enabled"]})

    def handle_save(route):
        route.fulfill(status=200, content_type="application/json", json={"success": True, "need_refresh": False})

    page.route("**/api/storage/location/bootstrap", handle_bootstrap)
    page.route("**/api/memory/recent_files", handle_recent_files)
    page.route("**/api/characters/current_catgirl", handle_current_catgirl)
    page.route("**/api/memory/recent_file?**", handle_recent_file)
    page.route("**/api/memory/review_config", handle_review_config)
    page.route("**/api/memory/recent_file/save", handle_save)


@pytest.mark.frontend
def test_memory_browser_page_load(mock_page: Page, running_server: str, seed_memory_file):
    """Test that the memory browser page loads and displays the file list."""
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    
    # Navigate to the memory browser page
    mock_page.goto(f"{running_server}/memory_browser")
    
    # Wait for the file list to populate (the JS fetches /api/memory/recent_files on load)
    # We should see a button with the catgirl name in the list
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)

    # The list should show our seeded catgirl
    expect(mock_page.locator("#memory-file-list button.cat-btn", has_text="测试猫娘")).to_have_count(1, timeout=5000)

    # Stage 1 storage-location entry is read-only and must not auto-start migration.
    expect(mock_page.locator(".storage-location-section")).to_be_visible()
    expect(mock_page.locator("#storage-location-manage-btn")).to_be_enabled()
    expect(mock_page.locator("#storage-recommended-root")).to_have_count(0)
    expect(mock_page.locator("#storage-current-root")).not_to_have_text("加载中...", timeout=5000)
    expect(mock_page.locator("#storage-location-overlay")).to_have_count(0)
    assert mock_page.evaluate("typeof window.appStorageLocation") == "object"
    assert mock_page.evaluate("typeof window.waitForStorageLocationStartupBarrier") == "undefined"
    assert mock_page.evaluate("typeof window.__nekoStorageLocationStartupBarrier") == "undefined"


@pytest.mark.frontend
def test_memory_browser_select_file(mock_page: Page, running_server: str, seed_memory_file):
    """Test that selecting a memory file loads and renders its chat content."""
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    
    mock_page.goto(f"{running_server}/memory_browser")
    
    # Wait for the file list
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    
    # Click the cat button to load the memory file
    target_cat_btn = mock_page.locator("#memory-file-list button.cat-btn", has_text="测试猫娘")
    expect(target_cat_btn).to_have_count(1, timeout=5000)
    target_cat_btn.first.click()
    
    # Wait for the chat content to render in the editor area
    # The chat items should appear in #memory-chat-edit
    mock_page.wait_for_selector("#memory-chat-edit .chat-item", timeout=5000)
    
    # Verify that chat items are displayed (we seeded 3: system, human, ai)
    chat_items = mock_page.locator("#memory-chat-edit .chat-item")
    expect(chat_items).to_have_count(3, timeout=5000)
    
    # Verify the save row is now visible
    expect(mock_page.locator("#save-row")).to_be_visible()


@pytest.mark.frontend
def test_memory_browser_auto_review_toggle(mock_page: Page, running_server: str, seed_memory_file):
    """Test that the auto-review toggle works and persists."""
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    
    mock_page.goto(f"{running_server}/memory_browser")
    
    # Wait for the page to fully initialize
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    
    # The auto-review checkbox should be present
    checkbox = mock_page.locator("#review-toggle-checkbox")
    expect(checkbox).to_be_attached()
    
    # Default is enabled (checked), toggle it off
    initial_state = checkbox.is_checked()
    
    # Toggle the checkbox via its label (since checkbox is styled via label)
    label = mock_page.locator("label[for='review-toggle-checkbox']")
    
    # Intercept the POST to /api/memory/review_config
    with mock_page.expect_response(
        lambda r: "/api/memory/review_config" in r.url and r.request.method == "POST" and r.status == 200
    ):
        label.click()
    
    # Verify the checkbox state toggled
    new_state = checkbox.is_checked()
    assert new_state != initial_state, "Checkbox state should have toggled"
    
    # Reload and verify the state persisted
    mock_page.reload()
    mock_page.wait_for_selector("#review-toggle-checkbox", state="attached", timeout=10000)
    expect(mock_page.locator("#review-toggle-checkbox")).to_be_checked(checked=new_state, timeout=5000)


@pytest.mark.frontend
def test_memory_browser_storage_bootstrap_blocks_memory_apis(mock_page: Page, running_server: str):
    """Storage bootstrap must run before ordinary memory APIs in limited mode."""
    requested_paths = []

    def handle_bootstrap(route):
        requested_paths.append("/api/storage/location/bootstrap")
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "current_root": "/tmp/current/N.E.K.O",
                "recommended_root": "/tmp/recommended/N.E.K.O",
                "legacy_sources": [],
                "selection_required": True,
                "migration_pending": False,
                "recovery_required": False,
                "blocking_reason": "selection_required",
            },
        )

    def handle_memory_api(route):
        requested_paths.append(route.request.url)
        route.fulfill(status=500, content_type="application/json", json={"error": "memory api should not be called"})

    mock_page.route("**/api/storage/location/bootstrap", handle_bootstrap)
    mock_page.route("**/api/memory/recent_files", handle_memory_api)
    mock_page.route("**/api/memory/review_config", handle_memory_api)

    mock_page.goto(f"{running_server}/memory_browser")

    expect(mock_page.locator("#storage-location-status")).to_contain_text("存储位置", timeout=5000)
    expect(mock_page.locator("#memory-chat-edit .memory-limited-state")).to_be_visible()
    expect(mock_page.locator("#review-toggle-checkbox")).to_be_disabled()
    expect(mock_page.locator("#storage-location-manage-btn")).to_be_disabled()

    assert "/api/storage/location/bootstrap" in requested_paths
    assert not any("/api/memory/recent_files" in path for path in requested_paths)
    assert not any("/api/memory/review_config" in path for path in requested_paths)


@pytest.mark.frontend
def test_memory_browser_storage_preflight_modal_waits_for_restart_confirmation(mock_page: Page, running_server: str, seed_memory_file):
    """Preflight should not call restart until the user confirms migration."""
    requested_paths = []
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)

    def handle_preflight(route):
        requested_paths.append("/api/storage/location/preflight")
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_required",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": "/tmp/stage2-target/N.E.K.O",
                "target_root": "/tmp/stage2-target/N.E.K.O",
                "estimated_required_bytes": 1024,
                "target_free_bytes": 4096,
                "permission_ok": True,
                "warning_codes": [],
                "target_has_existing_content": False,
                "requires_existing_target_confirmation": False,
                "existing_target_confirmation_message": "",
                "blocking_error_code": "",
                "blocking_error_message": "",
            },
        )

    def handle_forbidden_storage_mutation(route):
        requested_paths.append(route.request.url)
        route.fulfill(status=500, content_type="application/json", json={"error": "mutation should not be called"})

    mock_page.route("**/api/storage/location/preflight", handle_preflight)
    mock_page.route("**/api/storage/location/select", handle_forbidden_storage_mutation)
    mock_page.route("**/api/storage/location/restart", handle_forbidden_storage_mutation)

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)

    mock_page.locator("#storage-location-manage-btn").click()
    expect(mock_page.locator("#storage-location-modal")).to_be_visible()
    mock_page.locator("#storage-target-root-input").fill("/tmp/stage2-target")

    with mock_page.expect_response(lambda r: "/api/storage/location/preflight" in r.url and r.status == 200):
        mock_page.locator("#storage-location-preflight-btn").click()

    expect(mock_page.locator("#storage-location-preflight-result")).to_contain_text("/tmp/stage2-target/N.E.K.O", timeout=5000)
    expect(mock_page.locator("#storage-location-restart-btn")).to_be_visible()
    assert requested_paths == ["/api/storage/location/preflight"]


@pytest.mark.frontend
def test_memory_browser_storage_picker_preflights_selected_directory(mock_page: Page, running_server: str, seed_memory_file):
    """Directory picker selection should flow into preflight without generic failure."""
    requests = []
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)

    mock_page.add_init_script(
        """
        window.nekoHost = {
            pickDirectory: async (options) => {
                window.__storagePickOptions = options;
                return { cancelled: false, selected_root: '/tmp/picked-storage' };
            }
        };
        """
    )

    def handle_preflight(route):
        requests.append(_request_json(route))
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_required",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": "/tmp/picked-storage/N.E.K.O",
                "target_root": "/tmp/picked-storage/N.E.K.O",
                "estimated_required_bytes": 1024,
                "target_free_bytes": 4096,
                "permission_ok": True,
                "warning_codes": [],
                "target_has_existing_content": False,
                "requires_existing_target_confirmation": False,
                "existing_target_confirmation_message": "",
                "blocking_error_code": "",
                "blocking_error_message": "",
                "selection_source": "custom",
            },
        )

    mock_page.route("**/api/storage/location/preflight", handle_preflight)

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    mock_page.locator("#storage-location-manage-btn").click()
    mock_page.locator("#storage-location-pick-btn").click()

    expect(mock_page.locator("#storage-target-root-input")).to_have_value("/tmp/picked-storage", timeout=5000)
    pick_options = mock_page.evaluate("window.__storagePickOptions")
    start_path = pick_options["startPath"]
    app_root = str(seed_memory_file.parents[2])
    assert start_path, "startPath should not be empty"
    assert not start_path.endswith("N.E.K.O"), (
        f"picker should be opened at the parent of the current root, got {start_path!r}"
    )
    assert app_root.startswith(start_path), (
        f"startPath should be a parent of app_root; got start_path={start_path!r} app_root={app_root!r}"
    )

    with mock_page.expect_response(lambda r: "/api/storage/location/preflight" in r.url and r.status == 200):
        mock_page.locator("#storage-location-preflight-btn").click()

    expect(mock_page.locator("#storage-location-preflight-result")).to_contain_text("/tmp/picked-storage/N.E.K.O", timeout=5000)
    assert requests == [{"selected_root": "/tmp/picked-storage", "selection_source": "custom"}]


@pytest.mark.frontend
def test_memory_browser_open_current_root_uses_host_bridge(mock_page: Page, running_server: str, seed_memory_file):
    """Opening current storage root should call the desktop host bridge when present."""
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script(
        """
        window.nekoHost = {
            openPath: async (payload) => {
                window.__openedStoragePath = payload.path;
                return { ok: true };
            }
        };
        """
    )

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    mock_page.locator("#storage-location-open-btn").click()

    opened_path = mock_page.wait_for_function("window.__openedStoragePath", timeout=5000).json_value()
    assert opened_path == str(seed_memory_file.parents[2])


@pytest.mark.frontend
def test_memory_browser_open_current_root_uses_backend_without_host_bridge(mock_page: Page, running_server: str, seed_memory_file):
    """Plain web usage should ask the backend to open the current storage root."""
    requested_paths = []
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)

    def handle_open_current(route):
        requested_paths.append("/api/storage/location/open-current")
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"ok": True, "current_root": str(seed_memory_file.parents[2])},
        )

    mock_page.route("**/api/storage/location/open-current", handle_open_current)

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)

    with mock_page.expect_response(lambda r: "/api/storage/location/open-current" in r.url and r.status == 200):
        mock_page.locator("#storage-location-open-btn").click()

    assert requested_paths == ["/api/storage/location/open-current"]


@pytest.mark.frontend
def test_memory_browser_open_current_root_falls_back_when_host_bridge_fails(mock_page: Page, running_server: str, seed_memory_file):
    """Host bridge failures should not block the backend open-current fallback."""
    requested_paths = []
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script(
        """
        window.nekoHost = {
            openPath: async () => ({ ok: false, error: 'native open failed' })
        };
        """
    )

    def handle_open_current(route):
        requested_paths.append("/api/storage/location/open-current")
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"ok": True, "current_root": str(seed_memory_file.parents[2])},
        )

    mock_page.route("**/api/storage/location/open-current", handle_open_current)

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)

    with mock_page.expect_response(lambda r: "/api/storage/location/open-current" in r.url and r.status == 200):
        mock_page.locator("#storage-location-open-btn").click()

    assert requested_paths == ["/api/storage/location/open-current"]


@pytest.mark.frontend
def test_memory_browser_storage_restart_requires_preflight_and_confirms_existing_target(mock_page: Page, running_server: str, seed_memory_file):
    """Stage 3 calls restart after preflight and carries existing-target confirmation."""
    requests = []
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script(
        """
        window.__storageRestartMessages = [];
        window.__storageRestartClosed = false;
        Object.defineProperty(window, 'opener', {
            configurable: true,
            value: {
                closed: false,
                postMessage(message, origin) {
                    window.__storageRestartMessages.push({ message, origin });
                }
            }
        });
        window.close = function () {
            window.__storageRestartClosed = true;
        };
        """
    )

    def handle_preflight(route):
        requests.append(("preflight", _request_json(route)))
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_required",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": "/tmp/stage3-target/N.E.K.O",
                "target_root": "/tmp/stage3-target/N.E.K.O",
                "estimated_required_bytes": 1024,
                "target_free_bytes": 4096,
                "permission_ok": True,
                "warning_codes": [],
                "target_has_existing_content": True,
                "requires_existing_target_confirmation": True,
                "existing_target_confirmation_message": "目标路径已经包含现有数据。",
                "blocking_error_code": "",
                "blocking_error_message": "",
                "selection_source": "custom",
            },
        )

    def handle_restart(route):
        requests.append(("restart", _request_json(route)))
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_initiated",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": "/tmp/stage3-target/N.E.K.O",
                "target_root": "/tmp/stage3-target/N.E.K.O",
            },
        )

    mock_page.route("**/api/storage/location/preflight", handle_preflight)
    mock_page.route("**/api/storage/location/restart", handle_restart)
    mock_page.on("dialog", lambda dialog: dialog.accept())

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    mock_page.locator("#storage-location-manage-btn").click()
    mock_page.locator("#storage-target-root-input").fill("/tmp/stage3-target")

    with mock_page.expect_response(lambda r: "/api/storage/location/preflight" in r.url and r.status == 200):
        mock_page.locator("#storage-location-preflight-btn").click()
    with mock_page.expect_response(lambda r: "/api/storage/location/restart" in r.url and r.status == 200):
        mock_page.locator("#storage-location-restart-btn").click()

    expect(mock_page.locator("#storage-location-preflight-result")).to_contain_text("关闭并迁移", timeout=5000)
    expect(mock_page.locator("#storage-location-pick-btn")).to_be_disabled()
    expect(mock_page.locator("#storage-location-preflight-btn")).to_be_disabled()
    expect(mock_page.locator("#storage-target-root-input")).to_be_disabled()
    expect(mock_page.locator("#storage-location-restart-btn")).to_be_hidden()
    mock_page.wait_for_function("window.__storageRestartMessages.length === 1", timeout=5000)
    assert mock_page.evaluate("window.location.pathname") == "/memory_browser"
    restart_message = mock_page.evaluate("window.__storageRestartMessages[0]")
    assert restart_message["origin"] == running_server
    assert restart_message["message"]["type"] == "storage_location_restart_initiated"
    assert restart_message["message"]["sender_id"]
    assert restart_message["message"]["payload"] == {
        "ok": True,
        "result": "restart_initiated",
        "restart_mode": "migrate_after_shutdown",
        "selected_root": "/tmp/stage3-target/N.E.K.O",
        "target_root": "/tmp/stage3-target/N.E.K.O",
    }
    assert requests[0][0] == "preflight"
    assert requests[1] == (
        "restart",
        {
            "selected_root": "/tmp/stage3-target/N.E.K.O",
            "selection_source": "custom",
            "confirm_existing_target_content": True,
        },
    )
    mock_page.wait_for_function("window.__storageRestartClosed === true", timeout=5000)


@pytest.mark.frontend
def test_memory_browser_storage_restart_standalone_reuses_storage_maintenance_overlay(mock_page: Page, running_server: str, seed_memory_file):
    """Standalone memory page should show the shared storage maintenance overlay after restart."""
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)

    def handle_preflight(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_required",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": "/tmp/standalone-target/N.E.K.O",
                "target_root": "/tmp/standalone-target/N.E.K.O",
                "estimated_required_bytes": 1024,
                "target_free_bytes": 4096,
                "permission_ok": True,
                "warning_codes": [],
                "target_has_existing_content": False,
                "requires_existing_target_confirmation": False,
                "existing_target_confirmation_message": "",
                "blocking_error_code": "",
                "blocking_error_message": "",
                "selection_source": "custom",
            },
        )

    def handle_restart(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_initiated",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": "/tmp/standalone-target/N.E.K.O",
                "target_root": "/tmp/standalone-target/N.E.K.O",
                "migration": {
                    "status": "pending",
                    "target_root": "/tmp/standalone-target/N.E.K.O",
                },
            },
        )

    def handle_storage_status(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "ready": False,
                "status": "maintenance",
                "lifecycle_state": "maintenance",
                "migration_stage": "pending",
                "maintenance_message": "当前实例即将关闭，数据会在关闭后迁移并自动重启。",
                "poll_interval_ms": 500,
                "effective_root": str(seed_memory_file.parents[2]),
                "blocking_reason": "migration_pending",
                "migration": {
                    "status": "pending",
                    "target_root": "/tmp/standalone-target/N.E.K.O",
                },
            },
        )

    mock_page.route("**/api/storage/location/preflight", handle_preflight)
    mock_page.route("**/api/storage/location/restart", handle_restart)
    mock_page.route("**/api/storage/location/status", handle_storage_status)

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    mock_page.locator("#storage-location-manage-btn").click()
    mock_page.locator("#storage-target-root-input").fill("/tmp/standalone-target")

    with mock_page.expect_response(lambda r: "/api/storage/location/preflight" in r.url and r.status == 200):
        mock_page.locator("#storage-location-preflight-btn").click()
    with mock_page.expect_response(lambda r: "/api/storage/location/restart" in r.url and r.status == 200):
        mock_page.locator("#storage-location-restart-btn").click()

    expect(mock_page.get_by_role("heading", name="正在优化存储布局...")).to_be_visible(timeout=10_000)
    expect(mock_page.locator("#storage-location-overlay")).to_be_visible(timeout=10_000)
    expect(mock_page.locator('[role="progressbar"]')).to_be_visible(timeout=10_000)
    assert mock_page.evaluate("typeof window.__nekoStorageLocationStartupBarrier") == "undefined"
    assert mock_page.evaluate("window.location.pathname") == "/memory_browser"


@pytest.mark.frontend
def test_memory_browser_web_popup_restart_drives_opener_maintenance_overlay(mock_page: Page, running_server: str, seed_memory_file):
    """A real web popup opened from the home page must hand off restart maintenance to its opener."""
    context = mock_page.context
    _install_ready_memory_browser_routes(context, seed_memory_file)

    target_root = "/tmp/web-popup-target/N.E.K.O"

    context.route(
        "**/api/system/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "status": "ready",
                "ready": True,
                "storage": {
                    "selection_required": False,
                    "migration_pending": False,
                    "recovery_required": False,
                    "blocking_reason": "",
                    "last_error_summary": "",
                    "stage": "web_popup_restart",
                },
            },
        ),
    )
    context.route(
        "**/api/storage/location/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "ready": False,
                "status": "maintenance",
                "lifecycle_state": "maintenance",
                "migration_stage": "pending",
                "maintenance_message": "当前实例即将关闭，数据会在关闭后迁移并自动重启。",
                "poll_interval_ms": 500,
                "effective_root": str(seed_memory_file.parents[2]),
                "blocking_reason": "migration_pending",
                "storage": {
                    "selection_required": False,
                    "migration_pending": True,
                    "recovery_required": False,
                    "blocking_reason": "migration_pending",
                    "stage": "web_popup_restart",
                },
                "migration": {
                    "status": "pending",
                    "target_root": target_root,
                },
            },
        ),
    )
    context.route(
        "**/api/storage/location/preflight",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_required",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": target_root,
                "target_root": target_root,
                "estimated_required_bytes": 1024,
                "target_free_bytes": 4096,
                "permission_ok": True,
                "warning_codes": [],
                "target_has_existing_content": False,
                "requires_existing_target_confirmation": False,
                "existing_target_confirmation_message": "",
                "blocking_error_code": "",
                "blocking_error_message": "",
                "selection_source": "custom",
            },
        ),
    )
    context.route(
        "**/api/storage/location/restart",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_initiated",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": target_root,
                "target_root": target_root,
                "migration": {
                    "status": "pending",
                    "target_root": target_root,
                },
            },
        ),
    )

    home_page = mock_page
    home_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    expect(home_page.locator("#storage-location-overlay")).to_be_hidden(timeout=10_000)

    with home_page.expect_popup() as popup_info:
        home_page.evaluate("() => window.open('/memory_browser', 'neko_memory')")
    memory_page = popup_info.value
    memory_page.on("console", lambda msg: print(f"Memory Popup Console: {msg.text}"))
    memory_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    assert memory_page.evaluate("window.opener !== null")

    memory_page.locator("#storage-location-manage-btn").click()
    memory_page.locator("#storage-target-root-input").fill("/tmp/web-popup-target")
    with memory_page.expect_response(lambda r: "/api/storage/location/preflight" in r.url and r.status == 200):
        memory_page.locator("#storage-location-preflight-btn").click()
    with memory_page.expect_response(lambda r: "/api/storage/location/restart" in r.url and r.status == 200):
        memory_page.locator("#storage-location-restart-btn").click()

    expect(home_page.get_by_role("heading", name="正在优化存储布局...")).to_be_visible(timeout=10_000)
    expect(home_page.locator("#storage-location-overlay")).to_be_visible(timeout=10_000)
    expect(home_page.locator('[role="progressbar"]')).to_be_visible(timeout=10_000)
    assert home_page.locator("body").evaluate(
        "node => node.classList.contains('storage-location-modal-open')"
    )
