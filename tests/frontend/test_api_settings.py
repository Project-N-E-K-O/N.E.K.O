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


@pytest.mark.frontend
def test_assist_free_disables_assist_api_key_input(mock_page: Page, running_server: str):
    """辅助 API 选择免费版时应禁用辅助 API Key 输入框。"""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    url = f"{running_server}/api_key"
    mock_page.goto(url)

    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=10000)
    mock_page.wait_for_selector("#coreApiSelect option[value='free']", state="attached", timeout=10000)
    mock_page.wait_for_selector("#assistApiSelect option[value='free']", state="attached", timeout=10000)
    mock_page.wait_for_selector("#assistApiSelect option[value='qwen']", state="attached", timeout=10000)

    mock_page.select_option("#coreApiSelect", "free")
    mock_page.select_option("#assistApiSelect", "free")

    expect(mock_page.locator("#assistApiKeyInput")).to_be_disabled(timeout=5000)
    assert mock_page.evaluate(
        "isFreeVersionText(getRealKey(document.getElementById('assistApiKeyInput')))"
    ) is True

    mock_page.select_option("#assistApiSelect", "qwen")

    expect(mock_page.locator("#assistApiKeyInput")).to_be_enabled(timeout=5000)
    assert mock_page.evaluate(
        "isFreeVersionText(getRealKey(document.getElementById('assistApiKeyInput')))"
    ) is False


@pytest.mark.frontend
def test_custom_api_close_preserves_assist_provider(mock_page: Page, running_server: str):
    """Toggling custom API on/off must not rewrite the user's chosen (non-free) assist provider."""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    url = f"{running_server}/api_key"
    mock_page.goto(url)

    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=10000)
    mock_page.wait_for_selector("#coreApiSelect option[value='qwen']", state="attached", timeout=10000)
    mock_page.wait_for_selector("#assistApiSelect option[value='free']", state="attached", timeout=10000)
    mock_page.wait_for_selector("#assistApiSelect option[value='qwen']", state="attached", timeout=10000)

    alternate_assist = mock_page.evaluate("""
        () => {
            const options = Array.from(document.querySelectorAll('#assistApiSelect option'));
            const option = options.find(opt => opt.value && opt.value !== 'free' && opt.value !== 'qwen' && !opt.disabled);
            return option ? option.value : '';
        }
    """)
    if not alternate_assist:
        pytest.skip("No alternate non-free assist provider is available")

    result = mock_page.evaluate("""
        (alternateAssist) => {
            const core = document.getElementById('coreApiSelect');
            const assist = document.getElementById('assistApiSelect');
            const enableCustomApi = document.getElementById('enableCustomApi');

            core.value = 'qwen';
            assist.value = alternateAssist;
            updateAssistApiRecommendation();

            enableCustomApi.checked = true;
            toggleCustomApi();
            const afterOpen = assist.value;

            enableCustomApi.checked = false;
            toggleCustomApi();
            const afterClose = assist.value;

            return { afterOpen, afterClose };
        }
    """, alternate_assist)

    assert result["afterOpen"] == alternate_assist
    assert result["afterClose"] == alternate_assist


@pytest.mark.frontend
def test_free_assist_with_paid_core_is_preserved(mock_page: Page, running_server: str):
    """assist=free with a paid core is a valid combination: neither the recommendation
    logic nor the custom API toggle may move it away; the free option must stay enabled
    and the assist key input must be locked with the free-version text."""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    url = f"{running_server}/api_key"
    mock_page.goto(url)

    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=10000)
    mock_page.wait_for_selector("#coreApiSelect option[value='qwen']", state="attached", timeout=10000)
    mock_page.wait_for_selector("#assistApiSelect option[value='free']", state="attached", timeout=10000)
    mock_page.wait_for_selector("#assistApiSelect option[value='qwen']", state="attached", timeout=10000)

    result = mock_page.evaluate("""
        () => {
            const core = document.getElementById('coreApiSelect');
            const assist = document.getElementById('assistApiSelect');
            const enableCustomApi = document.getElementById('enableCustomApi');

            core.value = 'qwen';
            core.dispatchEvent(new Event('change', { bubbles: true }));
            assist.value = 'free';
            assist.dispatchEvent(new Event('change', { bubbles: true }));
            const afterExplicitSelect = assist.value;
            const freeOption = assist.querySelector('option[value="free"]');
            const freeOptionDisabled = freeOption ? freeOption.disabled : null;

            enableCustomApi.checked = true;
            toggleCustomApi();
            const afterOpen = assist.value;

            enableCustomApi.checked = false;
            toggleCustomApi();
            const afterClose = assist.value;

            const assistKeyInput = document.getElementById('assistApiKeyInput');
            return {
                afterExplicitSelect, afterOpen, afterClose, freeOptionDisabled,
                assistKeyDisabled: assistKeyInput.disabled,
                assistKeyIsFreeText: isFreeVersionText(getRealKey(assistKeyInput)),
            };
        }
    """)

    assert result["afterExplicitSelect"] == "free"
    assert result["afterOpen"] == "free"
    assert result["afterClose"] == "free"
    assert result["freeOptionDisabled"] is False
    assert result["assistKeyDisabled"] is True
    assert result["assistKeyIsFreeText"] is True


@pytest.mark.frontend
def test_paid_core_key_not_overwritten_by_free_assist_on_save(mock_page: Page, running_server: str):
    """Saving core=qwen + assist=free: the paid core still requires a key (assist=free
    must not waive the check), and coreApiKey persists the real key, not free-access."""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    url = f"{running_server}/api_key"
    mock_page.goto(url)
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)

    # 建立干净基线：免费版配置 + 清空 qwen 辅助 Key，隔离 session 级 server 的残留状态。
    baseline = mock_page.evaluate("""
        async () => {
            const r = await fetch('/api/config/core_api', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    coreApiKey: 'free-access', coreApi: 'free',
                    assistApi: 'free', assistApiKeyQwen: '', enableCustomApi: false,
                }),
            });
            return await r.json();
        }
    """)
    assert baseline.get("success"), f"建立免费版基线失败: {baseline}"

    mock_page.reload()
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector("#coreApiSelect option[value='qwen']", state="attached", timeout=10000)
    expect(mock_page.locator("#coreApiSelect")).to_have_value("free", timeout=5000)

    # 切到付费 core，assist 保持 free
    mock_page.evaluate("""
        () => {
            const core = document.getElementById('coreApiSelect');
            core.value = 'qwen';
            core.dispatchEvent(new Event('change', { bubbles: true }));
        }
    """)
    expect(mock_page.locator("#assistApiSelect")).to_have_value("free", timeout=5000)

    # 空 Key 保存必须被拦截：assist=free 不豁免付费 core 的 Key 要求
    blocked = mock_page.evaluate("""
        async () => {
            window.__captured = null;
            window.saveApiKey = async (p) => { window.__captured = JSON.parse(JSON.stringify(p)); };
            const div = document.getElementById('current-api-key');
            if (div) div.dataset.hasKey = 'false';
            await save_button_down({ preventDefault() {} });
            return window.__captured === null;
        }
    """)
    assert blocked, "付费 core 空 Key 被 assist=free 放行保存了"

    # 填入真实 Key 后保存：coreApiKey 必须是真实 Key，不得被 free-access 覆盖
    test_key = "sk-test-paid-core-with-free-assist"
    mock_page.fill("#apiKeyInput", test_key)
    payload = mock_page.evaluate("""
        async () => {
            window.__captured = null;
            window.saveApiKey = async (p) => { window.__captured = JSON.parse(JSON.stringify(p)); };
            const div = document.getElementById('current-api-key');
            if (div) div.dataset.hasKey = 'false';
            await save_button_down({ preventDefault() {} });
            return window.__captured;
        }
    """)
    assert payload is not None, "填好 Key 的正常保存不应被拦截"
    assert payload["coreApi"] == "qwen"
    assert payload["assistApi"] == "free"
    assert payload["apiKey"] == test_key, f"付费 core Key 被改写: {payload['apiKey']!r}"
