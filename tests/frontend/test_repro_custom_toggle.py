"""Regression: custom-API toggling/loading races must not silently switch free service to paid Alibaba.

Historical bug: loadCurrentApiKey initially cleared the provider selector to '' before asynchronous refill. Saving
with custom API enabled during that window let empty coreApi bypass empty-key validation and persist. The backend
fell back to paid qwen (Alibaba DashScope), sending residual free-access to Alibaba and producing an invalid-key error.

Two-layer fix:
  A1 Frontend save_button_down: abort saving when coreApi is empty, prompting retry later.
  A2 Backend update_core_config: empty coreApi/assistApi must not overwrite valid stored values.
Global resolution still defaults to qwen; this protects stored free configuration from empty submissions.
"""

import pytest
from playwright.sync_api import Page, expect


def _establish_free_config(page: Page):
    """POST a clean free-service configuration directly as the baseline."""
    result = page.evaluate(
        """async () => {
            const r = await fetch('/api/config/core_api', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    coreApiKey: 'free-access', coreApi: 'free',
                    assistApi: 'free', enableCustomApi: false,
                }),
            });
            return await r.json();
        }"""
    )
    assert result.get("success"), f"建立免费版基线失败: {result}"


def _open_free_settings(page: Page, server: str):
    page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    page.goto(f"{server}/api_key")
    expect(page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    _establish_free_config(page)
    page.reload()
    expect(page.locator("#loading-overlay")).to_be_hidden(timeout=15000)
    page.wait_for_selector("#coreApiSelect option[value='free']", state="attached", timeout=10000)
    expect(page.locator("#coreApiSelect")).to_have_value("free", timeout=5000)


@pytest.mark.frontend
def test_pure_free_toggle_preserves_free(mock_page: Page, running_server: str):
    """Scenario: enabling then disabling custom API in a free-service configuration leaves coreApi/assistApi as free."""
    _open_free_settings(mock_page, running_server)

    mock_page.evaluate(
        """() => {
            const cb = document.getElementById('enableCustomApi');
            cb.checked = true;  cb.dispatchEvent(new Event('change', {bubbles:true}));
            cb.checked = false; cb.dispatchEvent(new Event('change', {bubbles:true}));
        }"""
    )

    payload = mock_page.evaluate(
        """async () => {
            window.__captured = null;
            window.saveApiKey = async (p) => { window.__captured = JSON.parse(JSON.stringify(p)); };
            const div = document.getElementById('current-api-key');
            if (div) div.dataset.hasKey = 'false';
            await save_button_down({ preventDefault() {} });
            return window.__captured;
        }"""
    )
    assert payload is not None, "纯免费版正常开关不应被拦截"
    assert payload["coreApi"] == "free", f"core 漂移: {payload['coreApi']!r}"
    assert payload["assistApi"] == "free", f"assist 漂移: {payload['assistApi']!r}"


@pytest.mark.frontend
def test_empty_dropdown_save_is_blocked(mock_page: Page, running_server: str):
    """A1 regression: empty selector during loading + save with custom API enabled must be blocked by the frontend,
    producing no persistence payload."""
    _open_free_settings(mock_page, running_server)

    blocked = mock_page.evaluate(
        """async () => {
            const core = document.getElementById('coreApiSelect');
            const assist = document.getElementById('assistApiSelect');
            core.value = '';      // 模拟 loadCurrentApiKey 起手清空后的竞态窗口
            assist.value = '';
            const cb = document.getElementById('enableCustomApi');
            cb.checked = true; cb.dispatchEvent(new Event('change', {bubbles:true}));  // 开自定义绕过空Key校验

            window.__captured = null;
            window.saveApiKey = async (p) => { window.__captured = JSON.parse(JSON.stringify(p)); };
            const div = document.getElementById('current-api-key');
            if (div) div.dataset.hasKey = 'false';
            await save_button_down({ preventDefault() {} });
            return window.__captured === null;   // true = 保存被拦截，未写盘
        }"""
    )
    assert blocked, "空 coreApi 仍被写盘——A1 守卫失效，会被后端兜底成阿里"

    # The baseline is unmodified: backend remains free.
    readback = mock_page.evaluate("async () => await (await fetch('/api/config/core_api')).json()")
    assert readback["coreApi"] == "free"
    assert readback["assistApi"] == "free"


@pytest.mark.frontend
def test_backend_ignores_empty_core_api_on_save(mock_page: Page, running_server: str):
    """A2 regression: even if the frontend is bypassed and empty coreApi is POSTed directly, the backend must not overwrite stored free;
    rereading must still return free, never qwen."""
    _open_free_settings(mock_page, running_server)

    result = mock_page.evaluate(
        """async () => {
            const saveResp = await fetch('/api/config/core_api', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({coreApiKey:'', coreApi:'   ', assistApi:'   ', enableCustomApi:true})
            });
            const saveJson = await saveResp.json();
            const readback = await (await fetch('/api/config/core_api')).json();
            return {saveJson, readback};
        }"""
    )
    assert result["saveJson"].get("success") is True, f"保存请求未成功: {result['saveJson']}"
    readback = result["readback"]
    assert readback["coreApi"] == "free", f"空 coreApi 覆盖了已存值: {readback['coreApi']!r}"
    assert readback["assistApi"] == "free"


@pytest.mark.frontend
def test_backend_preserves_free_provider_before_key_validation(
    mock_page: Page,
    running_server: str,
):
    """Regression: blank submitted providers should inherit stored free providers
    before coreApiKey emptiness is validated."""
    _open_free_settings(mock_page, running_server)

    result = mock_page.evaluate(
        """async () => {
            const saveResp = await fetch('/api/config/core_api', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({coreApiKey:'', coreApi:'   ', assistApi:'   ', enableCustomApi:false})
            });
            const saveJson = await saveResp.json();
            const readback = await (await fetch('/api/config/core_api')).json();
            return {saveJson, readback};
        }"""
    )
    assert result["saveJson"].get("success") is True, result["saveJson"]
    readback = result["readback"]
    assert readback["coreApi"] == "free"
    assert readback["assistApi"] == "free"


@pytest.mark.frontend
def test_backend_requires_key_when_switching_free_core_to_qwen(
    mock_page: Page,
    running_server: str,
):
    """Regression: assistApi=free must not waive the key for a paid coreApi."""
    _open_free_settings(mock_page, running_server)

    result = mock_page.evaluate(
        """async () => {
            const saveResp = await fetch('/api/config/core_api', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({coreApiKey:'', coreApi:'qwen', enableCustomApi:false})
            });
            const saveJson = await saveResp.json();
            const readback = await (await fetch('/api/config/core_api')).json();
            return {saveJson, readback};
        }"""
    )
    assert result["saveJson"].get("success") is False, result["saveJson"]
    assert "API Key" in result["saveJson"].get("error", "")
    readback = result["readback"]
    assert readback["coreApi"] == "free"
    assert readback["assistApi"] == "free"
