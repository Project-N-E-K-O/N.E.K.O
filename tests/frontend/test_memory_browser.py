import pytest
import json
from pathlib import Path
from playwright.sync_api import Page, expect


@pytest.fixture
def seed_memory_file(clean_user_data_dir):
    """Create a seed memory file in the test memory directory."""
    memory_dir = Path(clean_user_data_dir) / "N.E.K.O" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    
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
    
    memory_file = memory_dir / "recent_测试猫娘.json"
    with open(memory_file, 'w', encoding='utf-8') as f:
        json.dump(test_data, f, ensure_ascii=False, indent=2)
    
    return memory_file


@pytest.mark.frontend
def test_memory_browser_page_load(mock_page: Page, running_server: str, seed_memory_file):
    """Test that the memory browser page loads and displays the file list."""
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    
    # Navigate to the memory browser page
    mock_page.goto(f"{running_server}/memory_browser")
    
    # Wait for the file list to populate (the JS fetches /api/memory/recent_files on load)
    # We should see a button with the catgirl name in the list
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    
    # The list should show our seeded catgirl
    cat_btn = mock_page.locator("#memory-file-list button.cat-btn")
    expect(cat_btn).to_have_count(1, timeout=5000)
    expect(cat_btn.first).to_contain_text("测试猫娘")


@pytest.mark.frontend
def test_memory_browser_select_file(mock_page: Page, running_server: str, seed_memory_file):
    """Test that selecting a memory file loads and renders its chat content."""
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    
    mock_page.goto(f"{running_server}/memory_browser")
    
    # Wait for the file list
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    
    # Click the cat button to load the memory file
    cat_btn = mock_page.locator("#memory-file-list button.cat-btn").first
    cat_btn.click()
    
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
