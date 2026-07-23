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
    mock_page.evaluate("""
        () => {
            const input = document.getElementById('apiKeyInput');
            if (input) {
                input.value = '';
                input.removeAttribute('data-real-key');
            }
        }
    """)
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
def test_custom_model_headers_own_their_capsule_shape(mock_page: Page, running_server: str):
    """Collapsed custom-model headers must not borrow rounded corners from a wrapper."""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")

    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=10000)

    styles = mock_page.evaluate("""
        () => {
            document.getElementById('custom-api-options').style.display = 'block';
            document.getElementById('custom-api-container').style.display = 'block';
            const container = document.querySelector('.model-config-container');
            const header = container.querySelector(':scope > .model-header');

            return {
                containerBorderWidth: getComputedStyle(container).borderTopWidth,
                containerOverflow: getComputedStyle(container).overflow,
                headerBorderRadius: getComputedStyle(header).borderTopLeftRadius,
                headerOverflow: getComputedStyle(header).overflow,
                headerBoxSizing: getComputedStyle(header).boxSizing,
                containerWidth: Math.round(container.getBoundingClientRect().width),
                headerWidth: Math.round(header.getBoundingClientRect().width),
            };
        }
    """)

    assert styles == {
        "containerBorderWidth": "0px",
        "containerOverflow": "visible",
        "headerBorderRadius": "999px",
        "headerOverflow": "hidden",
        "headerBoxSizing": "border-box",
        "containerWidth": styles["headerWidth"],
        "headerWidth": styles["headerWidth"],
    }

    mock_page.evaluate("toggleModelConfig('conversation')")
    mock_page.wait_for_timeout(350)

    expanded_styles = mock_page.evaluate("""
        () => {
            const content = document.getElementById('conversation-model-content');
            const style = getComputedStyle(content);

            return {
                borderWidth: style.borderTopWidth,
                borderRadius: style.borderTopLeftRadius,
                marginTop: style.marginTop,
            };
        }
    """)

    assert expanded_styles == {
        "borderWidth": "1px",
        "borderRadius": "24px",
        "marginTop": "8px",
    }


@pytest.mark.frontend
def test_key_book_shortcut_centers_and_selects_provider_input(
    mock_page: Page, running_server: str
):
    """The shortcut should center, focus, and select the matching provider key field."""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")

    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=10000)
    mock_page.wait_for_selector(
        "#conversationModelProvider option[value='qwen']", state="attached", timeout=10000
    )
    mock_page.wait_for_selector("#keyBookInput_qwen", state="attached", timeout=10000)

    mock_page.evaluate("""() => {
        const enableCustomApi = document.getElementById('enableCustomApi');
        enableCustomApi.checked = true;
        toggleCustomApi();
        document.getElementById('custom-api-options').style.display = 'block';

        const provider = document.getElementById('conversationModelProvider');
        provider.value = 'qwen';
        onCustomModelProviderChange('conversation');
        toggleModelConfig('conversation');

        setMaskedInput(document.getElementById('keyBookInput_qwen'), 'sk-qwen-shortcut-test');
        window.__keyBookScrollTarget = '';
        window.__keyBookScrollOptions = null;
        Element.prototype.scrollIntoView = function (options) {
            window.__keyBookScrollTarget = this.querySelector('input')?.id || this.id;
            window.__keyBookScrollOptions = options;
        };
    }""")
    mock_page.wait_for_timeout(350)

    shortcut = mock_page.locator("#conversation-model-content .key-book-shortcut")
    expect(shortcut).to_have_count(1)
    expect(shortcut).to_have_attribute("data-provider-key", "qwen")
    shortcut.click()

    state = mock_page.evaluate("""() => {
        const input = document.getElementById('keyBookInput_qwen');
        return {
            optionsDisplay: document.getElementById('key-book-options').style.display,
            toggleExpanded: document.getElementById('key-book-toggle-btn').classList.contains('rotated'),
            scrollTarget: window.__keyBookScrollTarget,
            scrollBlock: window.__keyBookScrollOptions?.block,
            activeId: document.activeElement?.id,
            value: input.value,
            selectionStart: input.selectionStart,
            selectionEnd: input.selectionEnd,
        };
    }""")

    assert state == {
        "optionsDisplay": "block",
        "toggleExpanded": True,
        "scrollTarget": "keyBookInput_qwen",
        "scrollBlock": "center",
        "activeId": "keyBookInput_qwen",
        "value": "sk-qwen-shortcut-test",
        "selectionStart": 0,
        "selectionEnd": len("sk-qwen-shortcut-test"),
    }


@pytest.mark.frontend
def test_key_book_shortcut_targets_active_mimo_token_plan_key(
    mock_page: Page, running_server: str
):
    """Follow-assist shortcuts must target the Token Plan key while that mode is active."""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")

    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=10000)
    mock_page.wait_for_selector("#assistApiSelect option[value='mimo']", state="attached")
    mock_page.wait_for_selector(
        "#conversationModelProvider option[value='follow_assist']", state="attached"
    )

    mock_page.evaluate("""() => {
        const assistProvider = document.getElementById('assistApiSelect');
        assistProvider.value = 'mimo';
        assistProvider.dispatchEvent(new Event('change', { bubbles: true }));

        const tokenPlanToggle = document.getElementById('useMimoTokenPlan');
        tokenPlanToggle.checked = true;
        tokenPlanToggle.dispatchEvent(new Event('change', { bubbles: true }));
        setMaskedInput(
            document.getElementById('mimoTokenPlanKeyInput'),
            'tp-shortcut-test'
        );

        const enableCustomApi = document.getElementById('enableCustomApi');
        enableCustomApi.checked = true;
        toggleCustomApi();
        document.getElementById('custom-api-options').style.display = 'block';

        const provider = document.getElementById('conversationModelProvider');
        provider.value = 'follow_assist';
        onCustomModelProviderChange('conversation');
        toggleModelConfig('conversation');

        window.__keyBookScrollTarget = '';
        window.__keyBookScrollOptions = null;
        Element.prototype.scrollIntoView = function (options) {
            window.__keyBookScrollTarget = this.querySelector('input')?.id || this.id;
            window.__keyBookScrollOptions = options;
        };
    }""")
    mock_page.wait_for_timeout(350)

    shortcut = mock_page.locator("#conversation-model-content .key-book-shortcut")
    expect(shortcut).to_have_count(1)
    expect(shortcut).to_have_attribute("data-provider-key", "mimo_token_plan")
    shortcut.click()

    state = mock_page.evaluate("""() => {
        const input = document.getElementById('mimoTokenPlanKeyInput');
        return {
            scrollTarget: window.__keyBookScrollTarget,
            scrollBlock: window.__keyBookScrollOptions?.block,
            activeId: document.activeElement?.id,
            value: input.value,
            selectionStart: input.selectionStart,
            selectionEnd: input.selectionEnd,
        };
    }""")

    assert state == {
        "scrollTarget": "mimoTokenPlanKeyInput",
        "scrollBlock": "center",
        "activeId": "mimoTokenPlanKeyInput",
        "value": "tp-shortcut-test",
        "selectionStart": 0,
        "selectionEnd": len("tp-shortcut-test"),
    }


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

    # GSV「是否启用」迁到 ttsModelProvider 下拉后，启用状态 = 下拉是否选中 gptsovits；
    # 这里选的是 custom，故未启用（旧的独立 #gptsovitsEnabled 开关已移除）。
    assert mock_page.evaluate("document.getElementById('ttsModelProvider').value") == "custom"

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
    # gptsovitsEnabled 已退役，保存不再外发；启用状态由 ttsModelProvider 表达（这里 custom，未选 GSV）。
    assert "gptsovitsEnabled" not in payload
    assert payload["ttsModelProvider"] == "custom"
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


@pytest.mark.frontend
def test_mimo_token_plan_locks_regular_mimo_key(mock_page: Page, running_server: str):
    """MiMo Token Plan is a MiMo-only mode and must not overwrite the normal MiMo key."""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector("#assistApiSelect option[value='mimo']", state="attached", timeout=10000)

    result = mock_page.evaluate("""
        async () => {
            const core = document.getElementById('coreApiSelect');
            core.value = 'qwen';
            core.dispatchEvent(new Event('change', { bubbles: true }));
            setMaskedInput(document.getElementById('apiKeyInput'), 'sk-core-test');

            syncKeyToBook('mimo', 'sk-regular-mimo');
            const assist = document.getElementById('assistApiSelect');
            assist.value = 'mimo';
            assist.dispatchEvent(new Event('change', { bubbles: true }));

            const tokenToggle = document.getElementById('useMimoTokenPlan');
            tokenToggle.checked = true;
            tokenToggle.dispatchEvent(new Event('change', { bubbles: true }));
            const tokenInput = document.getElementById('mimoTokenPlanKeyInput');
            setMaskedInput(tokenInput, 'tp-token-plan-key');

            window.__captured = null;
            window.saveApiKey = async (p) => { window.__captured = JSON.parse(JSON.stringify(p)); };
            const div = document.getElementById('current-api-key');
            if (div) div.dataset.hasKey = 'false';
            await save_button_down({ preventDefault() {} });
            const payload = window.__captured;

            const resolved = ConnectivityManager.resolveEffectiveKey({ type: 'assist' });
            return {
                payload,
                assistDisabled: document.getElementById('assistApiKeyInput').disabled,
                tokenRowVisible: document.getElementById('mimoTokenPlanKeyRow').style.display !== 'none',
                resolved,
            };
        }
    """)

    payload = result["payload"]
    assert payload["assistApi"] == "mimo"
    assert payload["useMimoTokenPlan"] is True
    assert payload["assistApiKeyMimo"] == "sk-regular-mimo"
    assert payload["assistApiKeyMimoTokenPlan"] == "tp-token-plan-key"
    assert result["assistDisabled"] is True
    assert result["tokenRowVisible"] is True
    assert result["resolved"]["providerKey"] == "mimo"
    assert result["resolved"]["key"] == "tp-token-plan-key"
    assert "token-plan-cn.xiaomimimo.com" in result["resolved"]["url"]


@pytest.mark.frontend
def test_mimo_token_plan_toggle_wraps_below_assist_provider(mock_page: Page, running_server: str):
    """The Assist API provider dropdown should keep the Core API width when MiMo controls appear."""
    mock_page.set_viewport_size({"width": 1280, "height": 900})
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector("#assistApiSelect option[value='mimo']", state="attached", timeout=10000)

    mock_page.select_option("#assistApiSelect", "mimo")
    expect(mock_page.locator("#mimoTokenPlanToggleRow")).to_be_visible(timeout=5000)

    metrics = mock_page.evaluate("""
        () => {
            const getRect = (selector) => {
                const el = document.querySelector(selector);
                const rect = el.getBoundingClientRect();
                return {
                    top: rect.top,
                    bottom: rect.bottom,
                    width: rect.width,
                };
            };

            return {
                core: getRect("#coreApiSelect-dropdown-trigger"),
                assist: getRect("#assistApiSelect-dropdown-trigger"),
                row: getRect(".mimo-assist-select-row"),
                toggle: getRect("#mimoTokenPlanToggleRow"),
            };
        }
    """)

    assert abs(metrics["assist"]["width"] - metrics["core"]["width"]) <= 1
    assert metrics["assist"]["width"] <= 600
    assert metrics["row"]["width"] <= metrics["core"]["width"] + 1
    assert metrics["toggle"]["top"] >= metrics["assist"]["bottom"] - 1


@pytest.mark.frontend
def test_mimo_token_plan_keeps_settings_scroll_container_stable(mock_page: Page, running_server: str):
    """Expanding Token Plan at the bottom should not leave the page at the old scroll limit."""
    mock_page.set_viewport_size({"width": 1816, "height": 1376})
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector("#assistApiSelect option[value='mimo']", state="attached", timeout=10000)

    mock_page.select_option("#assistApiSelect", "mimo")
    expect(mock_page.locator("#mimoTokenPlanToggleRow")).to_be_visible(timeout=5000)

    metrics = mock_page.evaluate("""
        async () => {
            const content = document.querySelector('.container-content');
            content.scrollTop = content.scrollHeight;
            await new Promise(resolve => requestAnimationFrame(resolve));

            const toggle = document.getElementById('useMimoTokenPlan');
            const beforeMaxScroll = content.scrollHeight - content.clientHeight;
            const beforeScrollTop = content.scrollTop;
            toggle.checked = true;
            toggle.dispatchEvent(new Event('change', { bubbles: true }));
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));

            const rect = content.getBoundingClientRect();
            const customRect = document.getElementById('custom-api-section').getBoundingClientRect();
            const afterMaxScroll = content.scrollHeight - content.clientHeight;
            return {
                beforeMaxScroll,
                beforeScrollTop,
                afterMaxScroll,
                afterScrollTop: content.scrollTop,
                contentBottom: rect.bottom,
                documentScrollHeight: document.documentElement.scrollHeight,
                viewportHeight: window.innerHeight,
                customTop: customRect.top,
                customDisplay: getComputedStyle(document.getElementById('custom-api-section')).display,
            };
        }
    """)

    # 前提：该视口下内容本就需要滚动，否则 before/after 都为 0，断言失去意义。
    assert metrics["beforeMaxScroll"] > 0
    assert abs(metrics["beforeScrollTop"] - metrics["beforeMaxScroll"]) <= 2
    assert metrics["afterMaxScroll"] > metrics["beforeMaxScroll"]
    assert abs(metrics["afterScrollTop"] - metrics["afterMaxScroll"]) <= 2
    assert abs(metrics["contentBottom"] - metrics["viewportHeight"]) <= 2
    assert metrics["documentScrollHeight"] <= metrics["viewportHeight"] + 2
    assert metrics["customDisplay"] != "none"
    assert 0 <= metrics["customTop"] < metrics["viewportHeight"]


@pytest.mark.frontend
def test_mimo_token_plan_does_not_force_scroll_when_not_scrollable(mock_page: Page, running_server: str):
    """When the settings fit without scrolling, enabling Token Plan must not yank the user to the bottom."""
    mock_page.set_viewport_size({"width": 1816, "height": 1376})
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector("#assistApiSelect option[value='mimo']", state="attached", timeout=10000)

    mock_page.select_option("#assistApiSelect", "mimo")
    expect(mock_page.locator("#mimoTokenPlanToggleRow")).to_be_visible(timeout=5000)

    # Grow the viewport past the content height so the scroll container is no longer
    # scrollable, exercising the "not scrollable before expansion" boundary.
    content_height = mock_page.evaluate(
        "() => document.querySelector('.container-content').scrollHeight"
    )
    mock_page.set_viewport_size({"width": 1816, "height": int(content_height) + 400})

    metrics = mock_page.evaluate("""
        async () => {
            const content = document.querySelector('.container-content');
            content.scrollTop = 0;
            await new Promise(resolve => requestAnimationFrame(resolve));

            const toggle = document.getElementById('useMimoTokenPlan');
            const beforeMaxScroll = content.scrollHeight - content.clientHeight;
            toggle.checked = true;
            toggle.dispatchEvent(new Event('change', { bubbles: true }));
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));

            return {
                beforeMaxScroll,
                afterMaxScroll: content.scrollHeight - content.clientHeight,
                afterScrollTop: content.scrollTop,
                customDisplay: getComputedStyle(document.getElementById('custom-api-section')).display,
            };
        }
    """)

    # 前提：展开前容器不可滚动（maxScroll≈0），覆盖 isApiSettingsScrolledToBottom 的吸底守卫。
    assert metrics["beforeMaxScroll"] <= 4
    # 修复点：用户原本在顶部且不可滚动，启用 Token Plan 后不得被强制吸到底部。
    assert metrics["afterScrollTop"] <= 2
    assert metrics["customDisplay"] != "none"


@pytest.mark.frontend
def test_mimo_token_plan_connectivity_tries_endpoint_candidates(mock_page: Page, running_server: str):
    """Token Plan connectivity should try regional MiMo endpoints until one succeeds."""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector("#assistApiSelect option[value='mimo']", state="attached", timeout=10000)

    result = mock_page.evaluate("""
        async () => {
            const cnUrl = 'https://token-plan-cn.xiaomimimo.com/v1';
            const sgpUrl = 'https://token-plan-sgp.xiaomimimo.com/v1';
            const originalFetch = window.fetch.bind(window);
            const calls = [];

            _resolvedProviderUrls = {};
            _assistApiProviders.mimo.token_plan_openrouter_url = '';
            _assistApiProviders.mimo.token_plan_openrouter_urls = [cnUrl, sgpUrl];

            const assist = document.getElementById('assistApiSelect');
            assist.value = 'mimo';
            assist.dispatchEvent(new Event('change', { bubbles: true }));

            const tokenToggle = document.getElementById('useMimoTokenPlan');
            tokenToggle.checked = true;
            tokenToggle.dispatchEvent(new Event('change', { bubbles: true }));
            setMaskedInput(document.getElementById('mimoTokenPlanKeyInput'), 'tp-token-plan-key');

            window.fetch = async (input, init = {}) => {
                const requestUrl = typeof input === 'string' ? input : input.url;
                if (requestUrl.endsWith('/api/config/test_connectivity')) {
                    const body = JSON.parse(init.body || '{}');
                    calls.push(body.url || '');
                    if (body.url === cnUrl) {
                        return new Response(JSON.stringify({
                            success: false,
                            error: 'cn failed',
                            error_code: 'upstream_error'
                        }), {
                            status: 200,
                            headers: { 'Content-Type': 'application/json' }
                        });
                    }
                    if (body.url === sgpUrl) {
                        return new Response(JSON.stringify({
                            success: true,
                            resolved_url: sgpUrl
                        }), {
                            status: 200,
                            headers: { 'Content-Type': 'application/json' }
                        });
                    }
                    return new Response(JSON.stringify({
                        success: false,
                        error: 'unexpected endpoint',
                        error_code: 'unexpected_endpoint'
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' }
                    });
                }
                return originalFetch(input, init);
            };

            try {
                const resolved = ConnectivityManager.resolveEffectiveKey({ type: 'assist' });
                const connectivity = await ConnectivityManager.testKey({
                    provider_key: resolved.providerKey,
                    provider_scope: resolved.providerScope,
                    url: resolved.url,
                    api_key: resolved.key || '',
                    provider_type: resolved.providerType,
                    cache_id: resolved.cacheId
                });
                return {
                    calls,
                    connectivity,
                    remembered: _resolvedProviderUrls['assist:mimo_token_plan'] || ''
                };
            } finally {
                window.fetch = originalFetch;
            }
        }
    """)

    assert result["calls"] == [
        "https://token-plan-cn.xiaomimimo.com/v1",
        "https://token-plan-sgp.xiaomimimo.com/v1",
    ]
    assert result["connectivity"]["success"] is True
    assert result["connectivity"]["resolved_url"] == "https://token-plan-sgp.xiaomimimo.com/v1"
    assert result["remembered"] == "https://token-plan-sgp.xiaomimimo.com/v1"


@pytest.mark.frontend
def test_mimo_token_plan_hidden_when_assist_api_is_not_mimo(mock_page: Page, running_server: str):
    """Leaving MiMo must hide Token Plan controls and use the selected assist provider normally."""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector("#assistApiSelect option[value='qwen']", state="attached", timeout=10000)

    result = mock_page.evaluate("""
        () => {
            syncKeyToBook('qwen', 'sk-qwen-assist');
            const assist = document.getElementById('assistApiSelect');
            assist.value = 'qwen';
            assist.dispatchEvent(new Event('change', { bubbles: true }));
            const toggle = document.getElementById('useMimoTokenPlan');
            toggle.checked = true;
            toggle.dispatchEvent(new Event('change', { bubbles: true }));
            const resolved = ConnectivityManager.resolveEffectiveKey({ type: 'assist' });
            return {
                toggleVisible: document.getElementById('mimoTokenPlanToggleRow').style.display !== 'none',
                tokenRowVisible: document.getElementById('mimoTokenPlanKeyRow').style.display !== 'none',
                resolved,
            };
        }
    """)

    assert result["toggleVisible"] is False
    assert result["tokenRowVisible"] is False
    assert result["resolved"]["providerKey"] == "qwen"
    assert result["resolved"]["key"] == "sk-qwen-assist"
    assert "dashscope.aliyuncs.com" in result["resolved"]["url"]


@pytest.mark.frontend
def test_explicit_mimo_provider_ignores_assist_token_plan(mock_page: Page, running_server: str):
    """Explicit MiMo model providers should keep normal MiMo even when assist follows Token Plan."""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector("#assistApiSelect option[value='mimo']", state="attached", timeout=10000)

    result = mock_page.evaluate("""
        () => {
            syncKeyToBook('mimo', 'sk-regular-mimo');
            const assist = document.getElementById('assistApiSelect');
            assist.value = 'mimo';
            assist.dispatchEvent(new Event('change', { bubbles: true }));

            const tokenToggle = document.getElementById('useMimoTokenPlan');
            tokenToggle.checked = true;
            tokenToggle.dispatchEvent(new Event('change', { bubbles: true }));
            setMaskedInput(document.getElementById('mimoTokenPlanKeyInput'), 'tp-token-plan-key');

            const provider = document.getElementById('conversationModelProvider');
            provider.value = 'mimo';
            provider.dispatchEvent(new Event('change', { bubbles: true }));

            const explicit = ConnectivityManager.resolveEffectiveKey({
                type: 'custom',
                modelType: 'conversation'
            });
            const followAssist = (() => {
                provider.value = 'follow_assist';
                provider.dispatchEvent(new Event('change', { bubbles: true }));
                return ConnectivityManager.resolveEffectiveKey({
                    type: 'custom',
                    modelType: 'conversation'
                });
            })();
            return { explicit, followAssist };
        }
    """)

    assert result["explicit"]["providerKey"] == "mimo"
    assert result["explicit"]["key"] == "sk-regular-mimo"
    assert "api.xiaomimimo.com" in result["explicit"]["url"]
    assert "token-plan" not in result["explicit"]["url"]
    assert result["followAssist"]["providerKey"] == "mimo"
    assert result["followAssist"]["key"] == "tp-token-plan-key"
    assert "token-plan-cn.xiaomimimo.com" in result["followAssist"]["url"]


@pytest.mark.frontend
def test_explicit_mimo_tts_provider_is_saved_for_runtime_routing(mock_page: Page, running_server: str):
    """Saving explicit MiMo TTS must preserve ttsProvider so runtime dispatch selects MiMo."""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector("#assistApiSelect option[value='qwen']", state="attached", timeout=10000)
    mock_page.wait_for_selector("#ttsModelProvider option[value='mimo']", state="attached", timeout=10000)

    payload = mock_page.evaluate("""
        async () => {
            document.getElementById('enableCustomApi').checked = true;
            toggleCustomApi();

            const assist = document.getElementById('assistApiSelect');
            assist.value = 'qwen';
            assist.dispatchEvent(new Event('change', { bubbles: true }));

            const provider = document.getElementById('ttsModelProvider');
            provider.value = 'mimo';
            provider.dispatchEvent(new Event('change', { bubbles: true }));

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

    assert payload["assistApi"] == "qwen"
    assert payload["ttsModelProvider"] == "mimo"
    assert payload["ttsProvider"] == "mimo"


@pytest.mark.frontend
def test_doubao_tts_is_keybook_only_and_hidden_from_tts_config(mock_page: Page, running_server: str):
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector("#keyBookInput_doubao_tts", state="attached", timeout=10000)

    assert mock_page.locator("#assistApiSelect option[value='doubao_tts']").count() == 0
    assert mock_page.locator("#ttsModelProvider option[value='doubao_tts']").count() == 0
    expect(mock_page.locator("#assistApiSelect option[value='doubao']")).to_be_attached()


@pytest.mark.frontend
def test_doubao_tts_keybook_saves_independent_speech_key(mock_page: Page, running_server: str):
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector("#keyBookInput_doubao_tts", state="attached", timeout=10000)
    expect(mock_page.locator("label[data-i18n='api.keyBook.doubao_tts']")).not_to_have_text("api.keyBook.doubao_tts")
    assert mock_page.locator("#assistApiSelect option[value='doubao_tts']").count() == 0
    expect(mock_page.locator("#assistApiSelect option[value='doubao']")).to_be_attached()

    payload = mock_page.evaluate("""
        async () => {
            setMaskedInput(document.getElementById('keyBookInput_vllm_omni'), '');
            setMaskedInput(document.getElementById('keyBookInput_doubao_tts'), 'doubao-speech-key');

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

    assert payload["assistApiKeyDoubaoTts"] == "doubao-speech-key"


@pytest.mark.frontend
def test_gptsovits_dropdown_shows_gsv_fields_and_saves_enabled(mock_page: Page, running_server: str):
    """GPT-SoVITS moved to the ttsModelProvider dropdown:

    - the registry-only provider 'gptsovits' shows up in the TTS dropdown (Codex #3);
    - selecting it shows the GSV-specific fields (URL + voice grid) and hides the
      standard url/model/key/voice fields;
    - on save ttsModelProvider/ttsProvider=='gptsovits' and gptsovitsEnabled is no
      longer emitted (retired single source of truth — backend derives
      GPTSOVITS_ENABLED from ttsModelProvider), ttsModelUrl is the GSV URL,
      ttsVoiceId is the GSV voice, and no __gptsovits_disabled__| placeholder is written.
    """
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    # registry-only provider 必须进 TTS 下拉
    mock_page.wait_for_selector("#ttsModelProvider option[value='gptsovits']", state="attached", timeout=10000)

    visibility = mock_page.evaluate("""
        () => {
            document.getElementById('enableCustomApi').checked = true;
            toggleCustomApi();

            const ttsContent = document.getElementById('tts-model-content');
            if (ttsContent && !ttsContent.classList.contains('expanded')) {
                toggleModelConfig('tts');
            }

            const provider = document.getElementById('ttsModelProvider');
            provider.value = 'gptsovits';
            provider.dispatchEvent(new Event('change', { bubbles: true }));

            document.getElementById('gptsovitsApiUrl').value = 'http://127.0.0.1:9881';
            document.getElementById('gptsovitsVoiceId').value = 'my_voice';

            const std = document.getElementById('tts-standard-fields');
            const gsv = document.getElementById('gptsovits-config-fields');
            return {
                stdHidden: std ? getComputedStyle(std).display === 'none' : null,
                gsvShown: gsv ? getComputedStyle(gsv).display !== 'none' : null,
            };
        }
    """)
    assert visibility["stdHidden"] is True
    assert visibility["gsvShown"] is True

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

    assert payload["ttsModelProvider"] == "gptsovits"
    assert payload["ttsProvider"] == "gptsovits"
    # gptsovitsEnabled 已退役，保存不再外发；启用收口到 ttsModelProvider=='gptsovits' 单一真相，
    # 后端 snapshot 据此派生 GPTSOVITS_ENABLED（见 utils/config_manager.py）。
    assert "gptsovitsEnabled" not in payload
    assert payload["ttsModelUrl"] == "http://127.0.0.1:9881"
    assert payload["ttsVoiceId"] == "my_voice"
    assert not payload["ttsVoiceId"].startswith("__gptsovits_disabled__|")


@pytest.mark.frontend
def test_load_gptsovits_enabled_by_dropdown_with_remote_url(mock_page: Page, running_server: str):
    """Reload path after gptsovitsEnabled was retired: a dropdown-only user with a
    REMOTE GSV URL (which the localhost legacy heuristic does not recognize) and no
    stored gptsovitsEnabled (response returns null) must still load as enabled — the
    GSV URL/voice fields are repopulated, mirroring the backend snapshot derivation.
    The negative case (provider switched away while a stale URL lingers) must NOT
    re-enable GSV.
    """
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector("#ttsModelProvider option[value='gptsovits']", state="attached", timeout=10000)

    result = mock_page.evaluate("""
        () => {
            const remoteUrl = 'http://192.168.1.50:9881';
            // 下拉为准：provider=gptsovits + gptsovitsEnabled=null（未存）+ 远程 URL
            document.getElementById('gptsovitsApiUrl').value = '';
            loadGptSovitsConfig(remoteUrl, 'gsv:remote_voice', '', '', null, 'gptsovits');
            const enabled = {
                state: _loadedGptSovitsState,
                url: document.getElementById('gptsovitsApiUrl').value,
            };
            // 切走下拉：provider=vllm_omni + 残留 gptsovitsEnabled=true 不得把 GSV 兜回来
            document.getElementById('gptsovitsApiUrl').value = '';
            loadGptSovitsConfig(remoteUrl, 'gsv:remote_voice', '', '', true, 'vllm_omni');
            const switchedAway = {
                state: _loadedGptSovitsState,
                url: document.getElementById('gptsovitsApiUrl').value,
            };
            return { enabled, switchedAway };
        }
    """)

    assert result["enabled"]["state"] == "enabled"
    assert result["enabled"]["url"] == "http://192.168.1.50:9881"
    assert result["switchedAway"]["state"] == "none"
    assert result["switchedAway"]["url"] == ""


@pytest.mark.frontend
def test_load_gptsovits_legacy_follow_default_and_sentinel(mock_page: Page, running_server: str):
    """⚠️ Codex/CodeRabbit PR#1850 regressions on the load path:

    1. A pre-#1830 GSV user has gptsovitsEnabled=true with the TTS dropdown left at its
       default 'follow_assist' (the old save path submitted every provider dropdown).
       follow_* is a 'follow assist/core' sentinel — NOT an explicit provider — so the
       frontend must fall back to the legacy flag and load GSV as enabled, matching the
       backend snapshot (otherwise the frontend mirror loads it off and diverges).
    2. The legacy `__gptsovits_disabled__|` sentinel must NOT force 'disabled' when the
       dropdown explicitly selects gptsovits — the explicit provider wins, and the
       URL/voice are recovered from the sentinel for migration.
    """
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector("#ttsModelProvider option[value='gptsovits']", state="attached", timeout=10000)

    result = mock_page.evaluate("""
        () => {
            // 1) 存量：gptsovitsEnabled=true + 默认 follow_assist 哨兵 + 远程 URL → 回落旧 flag 启用
            document.getElementById('gptsovitsApiUrl').value = '';
            document.getElementById('gptsovitsVoiceId').value = '';
            loadGptSovitsConfig('http://192.168.1.50:9881', 'gsv:legacy_voice', '', '', true, 'follow_assist');
            const legacyFollow = {
                state: _loadedGptSovitsState,
                url: document.getElementById('gptsovitsApiUrl').value,
                voice: document.getElementById('gptsovitsVoiceId').value,
            };
            // 2) disabled sentinel（在 ttsVoiceId 位）+ 显式 provider=gptsovits → 显式胜出，
            //    从 sentinel 解 URL/voice
            document.getElementById('gptsovitsApiUrl').value = '';
            document.getElementById('gptsovitsVoiceId').value = '';
            loadGptSovitsConfig('', '__gptsovits_disabled__|http://10.0.0.9:9881|gsv:kept', '', '', null, 'gptsovits');
            const sentinelButSelected = {
                state: _loadedGptSovitsState,
                url: document.getElementById('gptsovitsApiUrl').value,
                voice: document.getElementById('gptsovitsVoiceId').value,
            };
            return { legacyFollow, sentinelButSelected };
        }
    """)

    assert result["legacyFollow"]["state"] == "enabled"
    assert result["legacyFollow"]["url"] == "http://192.168.1.50:9881"
    assert result["legacyFollow"]["voice"] == "gsv:legacy_voice"
    assert result["sentinelButSelected"]["state"] == "enabled"
    assert result["sentinelButSelected"]["url"] == "http://10.0.0.9:9881"
    assert result["sentinelButSelected"]["voice"] == "gsv:kept"


@pytest.mark.frontend
def test_switching_tts_provider_to_vllm_resets_stale_model(mock_page: Page, running_server: str):
    """Switching from another TTS provider to vLLM should not keep stale model IDs."""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector("#ttsModelProvider option[value='vllm_omni']", state="attached", timeout=10000)

    values = mock_page.evaluate("""
        () => {
            const provider = document.getElementById('ttsModelProvider');
            const model = document.getElementById('ttsModelId');
            const voice = document.getElementById('ttsVoiceId');

            model.value = 'tts-1-hd';
            voice.value = 'alloy';
            provider.value = 'vllm_omni';
            provider.dispatchEvent(new Event('change', { bubbles: true }));

            return {
                model: model.value,
                voice: voice.value,
            };
        }
    """)

    assert values == {"model": "Qwen3-TTS", "voice": "default"}


@pytest.mark.frontend
def test_switching_tts_provider_to_vllm_replaces_readonly_url(mock_page: Page, running_server: str):
    """Provider-derived readonly URLs must not be carried into vLLM TTS."""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector("#ttsModelProvider option[value='vllm_omni']", state="attached", timeout=10000)

    value = mock_page.evaluate("""
        () => {
            const provider = document.getElementById('ttsModelProvider');
            const url = document.getElementById('ttsModelUrl');

            url.value = 'wss://old-provider.example.com/v1';
            url.setAttribute('readonly', 'readonly');
            provider.value = 'vllm_omni';
            provider.dispatchEvent(new Event('change', { bubbles: true }));

            return {
                url: url.value,
                readonly: url.hasAttribute('readonly'),
            };
        }
    """)

    assert value == {"url": "ws://localhost:8091/v1", "readonly": False}


@pytest.mark.frontend
@pytest.mark.parametrize(
    ("model_type", "follow_provider"),
    [
        ("gameMain", "follow_conversation"),
        ("gameSummary", "follow_summary"),
    ],
)
def test_switching_game_model_away_from_follow_clears_model_readonly(
    mock_page: Page,
    running_server: str,
    model_type: str,
    follow_provider: str,
):
    """Game model IDs must be editable again after leaving follow-conversation/summary modes."""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector(f"#{model_type}ModelProvider option[value='{follow_provider}']", state="attached", timeout=10000)
    mock_page.wait_for_selector(f"#{model_type}ModelProvider option[value='custom']", state="attached", timeout=10000)
    mock_page.wait_for_selector(f"#{model_type}ModelProvider option[value='qwen']", state="attached", timeout=10000)

    value = mock_page.evaluate("""
        ({ modelType, followProvider }) => {
            const provider = document.getElementById(`${modelType}ModelProvider`);
            const model = document.getElementById(`${modelType}ModelId`);

            provider.value = followProvider;
            provider.dispatchEvent(new Event('change', { bubbles: true }));
            const followReadonly = model.hasAttribute('readonly');

            provider.value = 'custom';
            provider.dispatchEvent(new Event('change', { bubbles: true }));
            const customReadonly = model.hasAttribute('readonly');

            model.setAttribute('readonly', 'readonly');
            provider.value = 'qwen';
            provider.dispatchEvent(new Event('change', { bubbles: true }));
            const namedReadonly = model.hasAttribute('readonly');

            return { followReadonly, customReadonly, namedReadonly };
        }
    """, {"modelType": model_type, "followProvider": follow_provider})

    assert value == {
        "followReadonly": True,
        "customReadonly": False,
        "namedReadonly": False,
    }


@pytest.mark.frontend
def test_switching_tts_provider_away_from_vllm_clears_fallback_voice(mock_page: Page, running_server: str):
    """The vLLM fallback voice is provider-specific and must not leak into follow_* TTS."""
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/api_key")
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    mock_page.wait_for_selector("#ttsModelProvider option[value='vllm_omni']", state="attached", timeout=10000)

    values = mock_page.evaluate("""
        () => {
            const enableCustomApi = document.getElementById('enableCustomApi');
            const provider = document.getElementById('ttsModelProvider');
            const voice = document.getElementById('ttsVoiceId');

            if (enableCustomApi && !enableCustomApi.checked) {
                enableCustomApi.checked = true;
                toggleCustomApi();
            }

            provider.value = 'vllm_omni';
            provider.dispatchEvent(new Event('change', { bubbles: true }));
            const vllmVoice = voice.value;

            provider.value = 'follow_assist';
            provider.dispatchEvent(new Event('change', { bubbles: true }));

            return {
                vllmVoice,
                followVoice: voice.value,
            };
        }
    """)

    assert values == {"vllmVoice": "default", "followVoice": ""}
