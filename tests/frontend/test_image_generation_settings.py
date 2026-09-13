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
        document.getElementById('custom-api-container').style.display = 'grid';
    }""")
    mock_page.set_viewport_size({"width": 1280, "height": 1000})
    # Existing pairs retain their columns; image generation sits beside mini-games.
    for left, right in [("conversation", "vision"), ("summary", "correction"), ("emotion", "omni"), ("agent", "tts"), ("game", "image")]:
        bounds = mock_page.evaluate("""([left, right]) => {
            const box = type => document.getElementById(type + '-model-content').parentElement.getBoundingClientRect();
            const a = box(left), b = box(right);
            return {sameRow: Math.abs(a.top - b.top) < 1, ordered: a.left < b.left};
        }""", [left, right])
        assert bounds == {"sameRow": True, "ordered": True}
    for model in ["correction", "omni", "tts", "image"]:
        mock_page.evaluate("type => toggleModelConfig(type)", model)
        mock_page.wait_for_function("""type => {
            const outer = document.getElementById('custom-api-container').getBoundingClientRect();
            const inner = document.getElementById(type + '-model-content').getBoundingClientRect();
            return inner.width > outer.width * 0.8 && inner.left >= outer.left - 1 && inner.right <= outer.right + 1;
        }""", arg=model)
        mock_page.evaluate("type => toggleModelConfig(type)", model)
        mock_page.wait_for_function("type => !document.getElementById(type + '-model-content').classList.contains('is-collapsing')", arg=model)
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

    # A saved restricted provider survives unrelated saves in the real select widget.
    mock_page.evaluate("""() => {
        isMainlandChinaUser = true;
        _apiKeyRegistry.openai = {..._apiKeyRegistry.openai, restricted: true};
        populateImageProviders(_imageProviders);
        loadImageSettings({imageModelProvider: 'openai', imageModelId: 'saved-image'});
    }""")
    assert mock_page.evaluate("imageSettingsPayload().imageModelProvider") == "openai"
    assert mock_page.evaluate("imageSettingsPayload().imageModelId") == "saved-image"
    assert mock_page.locator('#imageModelProvider option[value="openai"]').is_disabled()
    mock_page.evaluate("confirmClearCustomApi()")
    assert mock_page.evaluate("imageSettingsPayload().imageModelProvider") == "disabled"

    # Locale/registry reloads must also retain an unknown saved provider.
    mock_page.evaluate("""() => {
        loadImageSettings({imageModelProvider: 'future-provider', imageModelId: ' saved-future ', imageModelUrl: ' https://future.example/v1 '});
        populateImageProviders(_imageProviders);
    }""")
    assert mock_page.evaluate("imageSettingsPayload().imageModelProvider") == "future-provider"
    assert mock_page.evaluate("imageSettingsPayload().imageModelId") == " saved-future "
    assert mock_page.evaluate("imageSettingsPayload().imageModelUrl") == " https://future.example/v1 "
