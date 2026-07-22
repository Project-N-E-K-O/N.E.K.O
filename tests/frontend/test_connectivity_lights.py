"""
前端集成测试：连通性指示灯、错误信息展示、级联重置、服务商切换、自定义 API 测试按钮。

使用 mock_page.route() 拦截后端 /api/config/test_connectivity 请求，
返回受控响应，避免依赖外部 API 可用性。

Requirements: 2.6, 3.6, 5.4, 5.6, 6.1-6.6, 8.1-8.6
"""

import json
import pytest
from playwright.sync_api import Page, expect


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _goto_and_wait(mock_page: Page, running_server: str):
    """Navigate to /api_key, skip tutorial, and wait for loading overlay to disappear."""
    # Skip the tutorial overlay that blocks interactions
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    url = f"{running_server}/api_key"
    mock_page.goto(url)
    expect(mock_page.locator("#loading-overlay")).to_be_hidden(timeout=15000)


def _install_connectivity_route(mock_page: Page, *, success: bool = True,
                                 error: str = "", error_code: str = ""):
    """Install a route handler that intercepts connectivity test requests."""
    body = json.dumps({
        "success": success,
        "error": error if not success else None,
        "error_code": error_code if not success else None,
    })

    def handler(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=body,
        )

    mock_page.route("**/api/config/test_connectivity", handler)


def _wait_for_lights_settled(mock_page: Page, timeout: int = 10000):
    """Wait until no lights have data-status='testing' (all tests finished)."""
    mock_page.wait_for_function(
        """() => {
            const lights = document.querySelectorAll('.connectivity-light');
            return lights.length > 0 &&
                   Array.from(lights).every(l => l.dataset.status !== 'testing');
        }""",
        timeout=timeout,
    )


def _get_core_light_status(mock_page: Page) -> str:
    """Get the data-status of the core API indicator light."""
    return mock_page.evaluate("""() => {
        const input = document.getElementById('apiKeyInput');
        if (!input) return 'missing_input';
        const row = input.closest('.connectivity-input-row');
        if (!row) return 'missing_row';
        const light = row.querySelector('.connectivity-light');
        if (!light) return 'missing_light';
        return light.dataset.status || 'no_status';
    }""")


def _get_core_error_visible(mock_page: Page) -> bool:
    """Check if the core API error message is visible and non-empty."""
    return mock_page.evaluate("""() => {
        const input = document.getElementById('apiKeyInput');
        if (!input) return false;
        const row = input.closest('.connectivity-input-row');
        if (!row) return false;
        const parent = row.parentNode;
        const errorEl = parent ? parent.querySelector('.connectivity-error-msg') : null;
        if (!errorEl) return false;
        return errorEl.style.display !== 'none' && errorEl.textContent.length > 0;
    }""")


def _get_core_error_hidden(mock_page: Page) -> bool:
    """Check if the core API error message is hidden or empty."""
    return mock_page.evaluate("""() => {
        const input = document.getElementById('apiKeyInput');
        if (!input) return true;
        const row = input.closest('.connectivity-input-row');
        if (!row) return true;
        const parent = row.parentNode;
        const errorEl = parent ? parent.querySelector('.connectivity-error-msg') : null;
        if (!errorEl) return true;
        return errorEl.style.display === 'none' || errorEl.textContent === '';
    }""")


# ---------------------------------------------------------------------------
# Test 1: Page load auto-test — lights transition from testing to final state
# Requirements: 2.6, 3.6, 6.1, 6.3
# ---------------------------------------------------------------------------

@pytest.mark.frontend
def test_page_load_auto_test(mock_page: Page, running_server: str):
    """After page load, indicator lights should auto-test and reach a final state."""
    _install_connectivity_route(mock_page, success=True)
    _goto_and_wait(mock_page, running_server)

    # Wait for auto-test to complete (lights should not stay in 'testing')
    _wait_for_lights_settled(mock_page, timeout=15000)

    # The core API light should exist and be in a final state
    status = _get_core_light_status(mock_page)
    assert status in ("connected", "failed", "untested", "not_configured"), \
        f"Core light should be in a final state, got '{status}'"


# ---------------------------------------------------------------------------
# Test 2: Key modification cascade reset
# Requirements: 5.4, 6.1, 6.3, 6.4, 8.4
# ---------------------------------------------------------------------------

@pytest.mark.frontend
def test_key_modification_cascade_reset(mock_page: Page, running_server: str):
    """Modifying a key should reset its light to 'untested' and clear error messages."""
    _install_connectivity_route(mock_page, success=True)
    _goto_and_wait(mock_page, running_server)

    # Select a non-free provider so we can type a key
    mock_page.wait_for_selector("#coreApiSelect option[value='qwen']", state="attached", timeout=10000)
    mock_page.select_option("#coreApiSelect", "qwen")

    # Fill a key and wait for debounce + auto-test to settle
    mock_page.fill("#apiKeyInput", "sk-test-key-12345")
    mock_page.wait_for_timeout(500)
    _wait_for_lights_settled(mock_page, timeout=15000)

    # Modify the key — this should trigger cascade reset to 'untested'
    mock_page.fill("#apiKeyInput", "sk-modified-key-99999")
    mock_page.wait_for_timeout(500)  # debounce delay

    status = _get_core_light_status(mock_page)
    assert status == "untested", \
        f"After key modification, light should be 'untested', got '{status}'"

    # Error message should be cleared/hidden
    assert _get_core_error_hidden(mock_page), \
        "Error message should be cleared after key modification"


# ---------------------------------------------------------------------------
# Test 3: Provider switch triggers key re-resolution
# Requirements: 5.6, 6.2, 6.5
# ---------------------------------------------------------------------------

@pytest.mark.frontend
def test_provider_switch_key_reresolution(mock_page: Page, running_server: str):
    """Switching provider should re-resolve the key and update the light."""
    _install_connectivity_route(mock_page, success=True)
    _goto_and_wait(mock_page, running_server)
    _wait_for_lights_settled(mock_page, timeout=15000)

    game_defaults = mock_page.evaluate("""() => ({
        gameMain: getDefaultProviderForModelType('gameMain'),
        gameSummary: getDefaultProviderForModelType('gameSummary'),
    })""")
    assert game_defaults == {
        "gameMain": "follow_conversation",
        "gameSummary": "follow_summary",
    }

    game_fold_state = mock_page.evaluate("""() => {
        if (typeof toggleModelConfig === 'function') toggleModelConfig('game');
        const gameContent = document.getElementById('game-model-content');
        const gameMainContent = document.getElementById('game-main-model-content');
        const gameSummaryContent = document.getElementById('game-summary-model-content');
        const gameHeader = document.querySelector('[aria-controls="game-model-content"]');
        const gameMainHeader = document.querySelector('[aria-controls="game-main-model-content"]');
        const gameSummaryHeader = document.querySelector('[aria-controls="game-summary-model-content"]');
        return {
            gameHeaderTag: gameHeader?.tagName,
            gameHeaderExpanded: gameHeader?.getAttribute('aria-expanded'),
            gameMainHeaderTag: gameMainHeader?.tagName,
            gameMainHeaderExpanded: gameMainHeader?.getAttribute('aria-expanded'),
            gameSummaryHeaderTag: gameSummaryHeader?.tagName,
            gameSummaryHeaderExpanded: gameSummaryHeader?.getAttribute('aria-expanded'),
            gameExpanded: gameContent?.classList.contains('expanded') || false,
            gameMainCollapsed: !gameMainContent?.classList.contains('expanded'),
            gameSummaryCollapsed: !gameSummaryContent?.classList.contains('expanded'),
            gameMainHidden: gameMainContent?.getAttribute('aria-hidden') === 'true',
            gameSummaryHidden: gameSummaryContent?.getAttribute('aria-hidden') === 'true',
        };
    }""")
    assert game_fold_state == {
        "gameHeaderTag": "BUTTON",
        "gameHeaderExpanded": "true",
        "gameMainHeaderTag": "BUTTON",
        "gameMainHeaderExpanded": "false",
        "gameSummaryHeaderTag": "BUTTON",
        "gameSummaryHeaderExpanded": "false",
        "gameExpanded": True,
        "gameMainCollapsed": True,
        "gameSummaryCollapsed": True,
        "gameMainHidden": True,
        "gameSummaryHidden": True,
    }

    initial_status = _get_core_light_status(mock_page)

    # Switch to a different provider (e.g., qwen)
    mock_page.wait_for_selector("#coreApiSelect option[value='qwen']", state="attached", timeout=10000)
    mock_page.select_option("#coreApiSelect", "qwen")
    mock_page.wait_for_timeout(500)

    # The light should have updated (either to a cached state or untested/not_configured)
    new_status = _get_core_light_status(mock_page)
    assert new_status in ("connected", "failed", "untested", "not_configured"), \
        f"After provider switch, light should be in a valid state, got '{new_status}'"


# ---------------------------------------------------------------------------
# Test 4: Custom API batch test button
# Requirements: 4.1, 4.2, 4.3, 4.6, 5.1, 5.2
# ---------------------------------------------------------------------------

@pytest.mark.frontend
def test_custom_api_test_button(mock_page: Page, running_server: str):
    """Enable custom API, click test button, lights should animate and settle."""
    _install_connectivity_route(mock_page, success=True)
    _goto_and_wait(mock_page, running_server)
    _wait_for_lights_settled(mock_page, timeout=15000)

    # Enable custom API and set up a custom model via JS (avoids visibility issues)
    mock_page.evaluate("""() => {
        const enableCustomApi = document.getElementById('enableCustomApi');
        enableCustomApi.checked = true;
        if (typeof toggleCustomApi === 'function') toggleCustomApi();
        enableCustomApi.dispatchEvent(new Event('change', { bubbles: true }));
        const customApiOptions = document.getElementById('custom-api-options');
        if (customApiOptions) customApiOptions.style.display = 'block';

        // Expand conversation model section if collapsed
        const convContent = document.getElementById('conversation-model-content');
        if (convContent && !convContent.classList.contains('expanded')) {
            if (typeof toggleModelConfig === 'function') toggleModelConfig('conversation');
        }

        // Set a custom provider with a key
        const provider = document.getElementById('conversationModelProvider');
        if (provider) {
            provider.value = 'custom';
            provider.dispatchEvent(new Event('change', { bubbles: true }));
        }

        const urlInput = document.getElementById('conversationModelUrl');
        if (urlInput) urlInput.value = 'https://api.example.com/v1';

        const modelInput = document.getElementById('conversationModelId');
        if (modelInput) {
            modelInput.value = 'neko-conversation-test-model';
            modelInput.dispatchEvent(new Event('input', { bubbles: true }));
        }

        const keyInput = document.getElementById('conversationModelApiKey');
        if (keyInput) {
            keyInput.value = 'sk-custom-test-key';
            keyInput.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }""")
    mock_page.wait_for_timeout(500)

    # The test button should now be visible
    btn_visible = mock_page.evaluate("""() => {
        const btn = document.querySelector('.connectivity-test-btn');
        if (!btn) return false;
        const wrapper = btn.closest('.connectivity-test-btn-wrapper');
        if (!wrapper) return false;
        return wrapper.style.display !== 'none' && getComputedStyle(wrapper).display !== 'none';
    }""")
    assert btn_visible, "Test button should be visible when custom API is enabled"

    # Click the test button via JS
    mock_page.evaluate("""() => {
        const btn = document.querySelector('.connectivity-test-btn');
        if (btn) btn.click();
    }""")

    # Wait for lights to settle after the test
    _wait_for_lights_settled(mock_page, timeout=15000)

    # Summary lights should exist
    summary_count = mock_page.evaluate("""() => {
        return document.querySelectorAll('.connectivity-summary-light').length;
    }""")
    assert summary_count == 10, "Summary lights should cover all custom model slots"

    tooltip_state = mock_page.evaluate("""() => {
        const summaryLight = document.querySelector('.connectivity-summary-light[data-model-type="conversation"]');
        const coreInput = document.getElementById('apiKeyInput');
        const coreLight = coreInput?.closest('.connectivity-input-row')?.querySelector('.connectivity-light');
        const label = window.t ? window.t('api.conversationModelConfig', '文本对话模型配置') : '文本对话模型配置';
        const status = window.t ? window.t('connectivity.status.connected', '已连通') : '已连通';
        const modelIdLabel = window.t ? window.t('api.modelId', '模型ID') : '模型ID';
        return {
            summaryTitle: summaryLight?.getAttribute('title') || '',
            coreTitle: coreLight?.getAttribute('title') || '',
            expectedTitle: `${label} ${status}\\n${modelIdLabel}: neko-conversation-test-model`,
        };
    }""")
    assert tooltip_state["summaryTitle"] == tooltip_state["expectedTitle"]
    assert "\n" not in tooltip_state["coreTitle"]
    assert "neko-conversation-test-model" not in tooltip_state["coreTitle"]


@pytest.mark.frontend
def test_custom_api_summary_lights_navigate_to_model_config(mock_page: Page, running_server: str):
    """Summary lights should expand and scroll to their corresponding model config."""
    _install_connectivity_route(mock_page, success=True)
    _goto_and_wait(mock_page, running_server)
    _wait_for_lights_settled(mock_page, timeout=15000)

    mock_page.evaluate("""() => {
        const enableCustomApi = document.getElementById('enableCustomApi');
        enableCustomApi.checked = true;
        enableCustomApi.dispatchEvent(new Event('change', { bubbles: true }));

        const customApiOptions = document.getElementById('custom-api-options');
        customApiOptions.style.display = 'block';
        document.getElementById('custom-api-toggle-btn').classList.add('rotated');

        window.__customConfigScrollTarget = '';
        window.__customConfigScrollOptions = null;
        Element.prototype.scrollIntoView = function (options) {
            const content = this.querySelector(':scope > .model-content');
            window.__customConfigScrollTarget = content?.id || '';
            window.__customConfigScrollOptions = options;
        };
    }""")

    target_ids = mock_page.evaluate("""() => Object.fromEntries(
        Array.from(document.querySelectorAll('.connectivity-summary-light')).map(light => [
            light.dataset.modelType,
            light.dataset.targetContentId,
        ])
    )""")
    assert target_ids == {
        "conversation": "conversation-model-content",
        "summary": "summary-model-content",
        "gameMain": "game-main-model-content",
        "gameSummary": "game-summary-model-content",
        "correction": "correction-model-content",
        "emotion": "emotion-model-content",
        "vision": "vision-model-content",
        "agent": "agent-model-content",
        "omni": "omni-model-content",
        "tts": "tts-model-content",
    }

    conversation_light = mock_page.locator(
        '.connectivity-summary-light[data-model-type="conversation"]'
    )
    expect(conversation_light).to_have_attribute(
        "data-target-content-id", "conversation-model-content"
    )
    assert conversation_light.get_attribute("aria-label")
    conversation_light.hover()
    assert conversation_light.evaluate("light => getComputedStyle(light).transform") == "none"
    conversation_light.click()
    mock_page.wait_for_timeout(400)

    conversation_state = mock_page.evaluate("""() => ({
        expanded: document.getElementById('conversation-model-content').classList.contains('expanded'),
        ariaHidden: document.getElementById('conversation-model-content').getAttribute('aria-hidden'),
        scrollTarget: window.__customConfigScrollTarget,
        scrollBlock: window.__customConfigScrollOptions?.block,
    })""")
    assert conversation_state == {
        "expanded": True,
        "ariaHidden": "false",
        "scrollTarget": "conversation-model-content",
        "scrollBlock": "center",
    }

    game_summary_light = mock_page.locator(
        '.connectivity-summary-light[data-model-type="gameSummary"]'
    )
    expect(game_summary_light).to_have_attribute(
        "data-target-content-id", "game-summary-model-content"
    )
    game_summary_light.click()
    mock_page.wait_for_timeout(400)

    game_state = mock_page.evaluate("""() => ({
        gameExpanded: document.getElementById('game-model-content').classList.contains('expanded'),
        summaryExpanded: document.getElementById('game-summary-model-content').classList.contains('expanded'),
        mainExpanded: document.getElementById('game-main-model-content').classList.contains('expanded'),
        scrollTarget: window.__customConfigScrollTarget,
        scrollBlock: window.__customConfigScrollOptions?.block,
    })""")
    assert game_state == {
        "gameExpanded": True,
        "summaryExpanded": True,
        "mainExpanded": False,
        "scrollTarget": "game-summary-model-content",
        "scrollBlock": "center",
    }


@pytest.mark.frontend
def test_custom_api_deduplicates_same_custom_endpoint_and_model(mock_page: Page, running_server: str):
    """Custom API slots with the same effective URL/key/model should share one backend probe."""
    matching_requests = []
    duplicate_url = "https://duplicate.example.com/v1"
    duplicate_model = "shared-mini-game-model"

    def handler(route):
        body = json.loads(route.request.post_data or "{}")
        if body.get("url") == duplicate_url and body.get("model") == duplicate_model:
            matching_requests.append(body)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"success": True, "error": None, "error_code": None}),
        )

    mock_page.route("**/api/config/test_connectivity", handler)
    _goto_and_wait(mock_page, running_server)
    _wait_for_lights_settled(mock_page, timeout=15000)

    cache_state = mock_page.evaluate("""({ duplicateUrl, duplicateModel }) => {
        const enableCustomApi = document.getElementById('enableCustomApi');
        enableCustomApi.checked = true;
        if (typeof toggleCustomApi === 'function') toggleCustomApi();
        enableCustomApi.dispatchEvent(new Event('change', { bubbles: true }));
        const customApiOptions = document.getElementById('custom-api-options');
        if (customApiOptions) customApiOptions.style.display = 'block';

        ['gameMain', 'gameSummary'].forEach(mt => {
            const provider = document.getElementById(`${mt}ModelProvider`);
            provider.value = 'custom';
            provider.dispatchEvent(new Event('change', { bubbles: true }));

            const urlInput = document.getElementById(`${mt}ModelUrl`);
            urlInput.value = duplicateUrl;
            urlInput.dispatchEvent(new Event('input', { bubbles: true }));

            const modelInput = document.getElementById(`${mt}ModelId`);
            modelInput.value = duplicateModel;
            modelInput.dispatchEvent(new Event('input', { bubbles: true }));

            const keyInput = document.getElementById(`${mt}ModelApiKey`);
            keyInput.value = 'sk-shared-custom-key';
            keyInput.dispatchEvent(new Event('input', { bubbles: true }));
        });

        const main = ConnectivityManager.resolveEffectiveKey({ type: 'custom', modelType: 'gameMain' });
        const summary = ConnectivityManager.resolveEffectiveKey({ type: 'custom', modelType: 'gameSummary' });
        return {
            mainCacheId: main.cacheId,
            summaryCacheId: summary.cacheId,
            mainModel: main.model,
            summaryModel: summary.model,
        };
    }""", {"duplicateUrl": duplicate_url, "duplicateModel": duplicate_model})

    assert cache_state["mainCacheId"] == cache_state["summaryCacheId"]
    assert "gameMain" not in cache_state["mainCacheId"]
    assert "gameSummary" not in cache_state["summaryCacheId"]
    assert cache_state["mainModel"] == duplicate_model
    assert cache_state["summaryModel"] == duplicate_model

    mock_page.evaluate("""() => {
        const btn = document.querySelector('.connectivity-test-btn');
        if (btn) btn.click();
    }""")
    _wait_for_lights_settled(mock_page, timeout=15000)

    assert len(matching_requests) == 1
    assert matching_requests[0]["provider_type"] == "openai_compatible"


# ---------------------------------------------------------------------------
# Test 5: Error display and clearing
# Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6
# ---------------------------------------------------------------------------

@pytest.mark.frontend
def test_error_display_and_clearing(mock_page: Page, running_server: str):
    """Invalid key should show error message; modifying key should clear it."""
    # Intercept connectivity requests → return failure
    _install_connectivity_route(
        mock_page,
        success=False,
        error="API Key无效或已过期",
        error_code="auth_failed",
    )
    _goto_and_wait(mock_page, running_server)

    # Select a non-free provider
    mock_page.wait_for_selector("#coreApiSelect option[value='qwen']", state="attached", timeout=10000)
    mock_page.select_option("#coreApiSelect", "qwen")

    # Fill an invalid key
    mock_page.fill("#apiKeyInput", "sk-invalid-key")
    mock_page.wait_for_timeout(500)

    # Trigger a connectivity test
    mock_page.evaluate("() => ConnectivityManager.autoTestOnLoad()")
    _wait_for_lights_settled(mock_page, timeout=15000)

    # The core light should be 'failed' (red)
    status = _get_core_light_status(mock_page)
    assert status == "failed", \
        f"After failed test, light should be 'failed', got '{status}'"

    # Error message should be visible
    assert _get_core_error_visible(mock_page), \
        "Error message should be visible after failed connectivity test"

    # Now modify the key — error should clear
    mock_page.unroute("**/api/config/test_connectivity")
    _install_connectivity_route(mock_page, success=True)

    mock_page.fill("#apiKeyInput", "sk-new-valid-key")
    mock_page.wait_for_timeout(500)  # debounce

    # After key modification, error should be cleared
    assert _get_core_error_hidden(mock_page), \
        "Error message should be cleared after key modification"

    # Light should be reset to 'untested'
    status = _get_core_light_status(mock_page)
    assert status == "untested", \
        f"After key modification, light should be 'untested', got '{status}'"


# ---------------------------------------------------------------------------
# Test 6: Custom API test button hidden when custom API disabled
# Requirements: 4.6
# ---------------------------------------------------------------------------

@pytest.mark.frontend
def test_custom_api_button_visibility(mock_page: Page, running_server: str):
    """Test button should be hidden when custom API is disabled, visible when enabled."""
    _install_connectivity_route(mock_page, success=True)
    _goto_and_wait(mock_page, running_server)
    _wait_for_lights_settled(mock_page, timeout=15000)

    # Ensure custom API is disabled first (use JS to avoid visibility issues)
    mock_page.evaluate("""() => {
        const cb = document.getElementById('enableCustomApi');
        if (cb && cb.checked) {
            cb.checked = false;
            cb.dispatchEvent(new Event('change', { bubbles: true }));
            if (typeof toggleCustomApi === 'function') toggleCustomApi();
        }
    }""")
    mock_page.wait_for_timeout(300)

    # The test button wrapper should be hidden
    btn_wrapper_hidden = mock_page.evaluate("""() => {
        const btn = document.querySelector('.connectivity-test-btn');
        if (!btn) return true;
        const wrapper = btn.closest('.connectivity-test-btn-wrapper');
        if (!wrapper) return true;
        return wrapper.style.display === 'none' || getComputedStyle(wrapper).display === 'none';
    }""")
    assert btn_wrapper_hidden, "Test button should be hidden when custom API is disabled"

    # Enable custom API via JS
    mock_page.evaluate("""() => {
        const cb = document.getElementById('enableCustomApi');
        if (cb && !cb.checked) {
            cb.checked = true;
            cb.dispatchEvent(new Event('change', { bubbles: true }));
            if (typeof toggleCustomApi === 'function') toggleCustomApi();
        }
    }""")
    mock_page.wait_for_timeout(300)

    # The test button wrapper should now be visible
    btn_wrapper_visible = mock_page.evaluate("""() => {
        const btn = document.querySelector('.connectivity-test-btn');
        if (!btn) return false;
        const wrapper = btn.closest('.connectivity-test-btn-wrapper');
        if (!wrapper) return false;
        return wrapper.style.display !== 'none' && getComputedStyle(wrapper).display !== 'none';
    }""")
    assert btn_wrapper_visible, "Test button should be visible when custom API is enabled"
