"""回归：自定义 API 开关 / 加载竞态不能把免费版悄悄切成付费阿里。

历史 bug：loadCurrentApiKey 起手把服务商下拉清成 ''，再异步回填。在这个窗口内
开着自定义 API 点保存时，空 coreApi 绕过了空 Key 校验被写盘，后端把空值兜底成
付费的 qwen(阿里 dashscope)，残留的 free-access 被发给阿里 → 「API key 无效」。

修复两层：
  A1 前端 save_button_down：coreApi 为空时中止保存（提示稍候重试）。
  A2 后端 update_core_config：空 coreApi/assistApi 不覆盖已存的有效值。
全局解析兜底仍是默认 qwen；这里保护的是已存 free 配置不能被空提交污染。
"""

import pytest
from playwright.sync_api import Page, expect


def _establish_free_config(page: Page):
    """直接 POST 一个干净的免费版配置作为基线。"""
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
    """场景：纯免费版老老实实开自定义API再关掉 → coreApi/assistApi 仍是 free。"""
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
    """A1 回归：下拉为空（加载竞态）+ 开着自定义API 点保存 → 必须被前端拦截，
    不产生任何写盘 payload。"""
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

    # 基线未被污染：后端仍是 free
    readback = mock_page.evaluate("async () => await (await fetch('/api/config/core_api')).json()")
    assert readback["coreApi"] == "free"
    assert readback["assistApi"] == "free"


@pytest.mark.frontend
def test_backend_ignores_empty_core_api_on_save(mock_page: Page, running_server: str):
    """A2 回归：即使前端被绕过、直接 POST 空 coreApi，后端也不能用空值覆盖已存的 free，
    重新读取必须仍是 free（绝不变成 qwen）。"""
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
