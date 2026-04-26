import pytest
from playwright.sync_api import Page, expect

@pytest.mark.frontend
def test_api_key_settings(mock_page: Page, running_server: str):
    """Test that the API key settings page loads and can save configurations."""
    # Capture console logs
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    # 该用例关注 API 设置保存链路，不验证首次教程流程；先标记教程已读，避免保存按钮被教程锁住。
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    
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
    mock_page.evaluate("""
        () => {
            const currentApiKeyDiv = document.getElementById('current-api-key');
            if (currentApiKeyDiv) {
                currentApiKeyDiv.dataset.hasKey = 'false';
            }
        }
    """)
    
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
    # 当前页面会把明文 key 掩码显示，真实值挂在 data-real-key 上。
    expect(mock_page.locator("#apiKeyInput")).to_have_attribute("data-real-key", test_key, timeout=5000)
    expect(mock_page.locator("#coreApiSelect")).to_have_value("qwen", timeout=5000)


@pytest.mark.frontend
def test_tts_voice_id_not_rewritten_when_gptsovits_disabled(mock_page: Page, running_server: str):
    """普通 HTTP TTS 配置在 GPT-SoVITS 关闭时不应被编码成占位串。"""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    url = f"{running_server}/api_key"
    mock_page.goto(url)

    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=10000)

    mock_page.evaluate("""
        () => {
            const enableCustomApi = document.getElementById('enableCustomApi');
            enableCustomApi.checked = true;
            toggleCustomApi();

            const ttsContent = document.getElementById('tts-model-content');
            if (ttsContent && !ttsContent.classList.contains('expanded')) {
                toggleModelConfig('tts');
            }

            const provider = document.getElementById('ttsModelProvider');
            provider.value = 'custom';
            provider.dispatchEvent(new Event('change', { bubbles: true }));

            document.getElementById('ttsModelUrl').value = 'https://example.com/v1/audio/speech';
            document.getElementById('ttsModelId').value = 'tts-1';
            document.getElementById('ttsVoiceId').value = 'alloy';
        }
    """)

    assert mock_page.evaluate("document.getElementById('gptsovitsEnabled').checked") is False

    payload = mock_page.evaluate("""
        async () => {
            window.__capturedSavePayload = null;
            window.saveApiKey = async (params) => {
                window.__capturedSavePayload = JSON.parse(JSON.stringify(params));
            };

            const currentApiKeyDiv = document.getElementById('current-api-key');
            if (currentApiKeyDiv) {
                currentApiKeyDiv.dataset.hasKey = 'false';
            }

            await save_button_down({ preventDefault() {} });
            return window.__capturedSavePayload;
        }
    """)

    assert payload["enableCustomApi"] is True
    assert payload["gptsovitsEnabled"] is False
    assert payload["ttsModelUrl"] == "https://example.com/v1/audio/speech"
    assert payload["ttsModelId"] == "tts-1"
    assert payload["ttsVoiceId"] == "alloy"
    assert not payload["ttsVoiceId"].startswith("__gptsovits_disabled__|")
