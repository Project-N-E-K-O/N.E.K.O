import pytest
from playwright.sync_api import expect


@pytest.mark.frontend
def test_image_settings_real_page_round_trip(mock_page, running_server):
    mock_page.add_init_script("localStorage.setItem('neko_tutorial_settings', 'seen')")
    def seed(route):
        route.fulfill(json={
            "success": True, "coreApi": "free", "assistApi": "free",
            "api_key": "free-access", "enableCustomApi": True,
            "imageModelProvider": "custom", "imageModelUrl": "https://custom.example/v1",
            "imageModelId": "custom-image", "imageModelApiKey": "__NEKO_SECRET_MASKED__",
        })
    mock_page.route("**/api/config/core_api", seed)
    mock_page.goto(running_server + "/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_function("document.getElementById('imageModelProvider').value === 'custom'")
    state = mock_page.evaluate("imageSettingsPayload()")
    assert state["imageModelUrl"] == "https://custom.example/v1"
    assert state["imageModelApiKey"] == "__NEKO_SECRET_MASKED__"
    mock_page.evaluate("""() => {
        const select = document.getElementById('imageModelProvider');
        select.value = 'qwen';
        select.dispatchEvent(new Event('change', {bubbles: true}));
    }""")
    state = mock_page.evaluate("imageSettingsPayload()")
    assert state["imageModelProvider"] == "qwen"
    assert state["imageModelUrl"] == "https://dashscope.aliyuncs.com"
    assert state["imageModelId"] == "wanx2.1-t2i-turbo"
    assert state["imageModelApiKey"] == ""
    assert mock_page.locator("#imageModelApiKey").is_disabled()
    assert mock_page.evaluate("CONNECTIVITY_TESTABLE_TYPES.includes('image')") is False

    # Keyboard activation uses the native button and keeps aria state in sync.
    mock_page.evaluate("""() => {
        document.getElementById('custom-api-options').style.display = 'block';
        document.getElementById('custom-api-container').style.display = 'block';
    }""")
    header = mock_page.locator('button[aria-controls="image-model-content"]')
    header.focus()
    header.press("Enter")
    expect(header).to_have_attribute("aria-expanded", "true")
    header.press("Space")
    expect(header).to_have_attribute("aria-expanded", "false")
    mock_page.evaluate("confirmClearCustomApi()")
    state = mock_page.evaluate("imageSettingsPayload()")
    assert state == {
        "imageModelProvider": "disabled", "imageModelUrl": "",
        "imageModelId": "", "imageModelApiKey": "",
    }
