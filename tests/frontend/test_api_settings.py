import pytest
from playwright.sync_api import Page, expect

@pytest.mark.frontend
def test_api_key_settings(mock_page: Page, running_server: str):
    """Test that the API key settings page loads and can save configurations."""
    # Capture console logs
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    
    # Go to the settings page (route is /api_key)
    url = f"{running_server}/api_key"
    mock_page.goto(url)
    
    # Wait for loading overlay to disappear
    # The overlay has id "loading-overlay" and initially display: flex
    # We wait for it to be hidden
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=10000)
    
    # Select qwen as core provider (universally available, openai may be filtered by region)
    # Wait for options to populate (use state='attached' since <option> inside <select> 
    # are not considered 'visible' by Playwright until the dropdown is expanded)
    mock_page.wait_for_selector("#coreApiSelect option[value='qwen']", state="attached", timeout=10000)
    mock_page.select_option("#coreApiSelect", "qwen")
    
    # Fill in a fake key
    test_key = "sk-test-1234567890"
    mock_page.fill("#apiKeyInput", test_key)
    
    # Click Save
    save_btn = mock_page.locator("#save-settings-btn")
    
    # Expect a response from /api/config/core_api
    # predicate: url ends with /api/config/core_api and method is POST and status is 200
    with mock_page.expect_response(lambda r: r.url.endswith("/api/config/core_api") and r.request.method == "POST" and r.status == 200) as response_info:
        save_btn.click()
        
    # Check for success message in status div
    # The JS shows status in #status div; message may be i18n-translated
    # Wait for the status div to become visible (it's hidden by default)
    expect(mock_page.locator("#status")).to_be_visible(timeout=5000)
    
    # Reload page to verify persistence
    mock_page.reload()
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=10000)
    
    # Verify value
    # Ensure options are loaded before checking value, or check if value is set
    # The JS sets the value asynchronously after fetching config
    expect(mock_page.locator("#apiKeyInput")).to_have_value(test_key, timeout=5000)
    expect(mock_page.locator("#coreApiSelect")).to_have_value("qwen", timeout=5000)
