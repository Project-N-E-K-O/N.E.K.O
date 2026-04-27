import pytest
from pathlib import Path
from playwright.sync_api import Page, expect

from utils.file_utils import atomic_write_json
from utils.storage_policy import save_storage_policy


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


def _install_ready_memory_browser_routes(page: Page, memory_file: Path):
    """Mock storage + memory APIs so the page is tested in ready mode."""
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
            post_data_json = route.request.post_data_json
            payload = post_data_json() if callable(post_data_json) else post_data_json
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
    expect(mock_page.locator("#storage-location-manage-btn")).to_be_disabled()
    expect(mock_page.locator("#storage-recommended-root")).to_have_count(0)
    expect(mock_page.locator("#storage-current-root")).not_to_have_text("加载中...", timeout=5000)


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

    assert "/api/storage/location/bootstrap" in requested_paths
    assert not any("/api/memory/recent_files" in path for path in requested_paths)
    assert not any("/api/memory/review_config" in path for path in requested_paths)
