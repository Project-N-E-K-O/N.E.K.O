import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_AGENT_PATH = REPO_ROOT / "static" / "app" / "app-agent.js"
AVATAR_POPUP_PATH = REPO_ROOT / "static" / "avatar" / "avatar-ui-popup.js"
COMMON_UI_HUD_PATH = REPO_ROOT / "static" / "common-ui-hud.js"
AVATAR_ROUNDS_PATH = (
    REPO_ROOT
    / "static"
    / "tutorial"
    / "yui-guide"
    / "director"
    / "avatar-rounds.js"
)
AGENT_HUD_TEMPLATE_PATH = REPO_ROOT / "templates" / "agenthud.html"
LOCALES_PATH = REPO_ROOT / "static" / "locales"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.unit
def test_floating_window_slider_sits_below_agent_master_before_separator():
    hud_source = _read(COMMON_UI_HUD_PATH)
    popup_start = hud_source.index("window.AgentHUD._createAgentPopupContent")
    popup_end = hud_source.index("// 创建 Agent 任务 HUD", popup_start)
    popup_block = hud_source[popup_start:popup_end]

    master_position = popup_block.index("id: 'agent-master'")
    floating_window_position = popup_block.index("id: 'agent-taskhud'")
    keyboard_position = popup_block.index("id: 'agent-keyboard'")

    assert master_position < floating_window_position < keyboard_position
    assert popup_block.count("controlStyle: 'slider'") == 7
    assert "separatorAfter: true" in popup_block
    assert "const toggleItem = this._createToggleItem(toggle, popup);" in popup_block
    assert "separator.className = `${avatarPrefix}-settings-separator`;" in popup_block


@pytest.mark.unit
def test_agent_slider_reuses_main_settings_slider_layout():
    source = _read(AVATAR_POPUP_PATH)
    agent_toggle_start = source.index("function createToggleItem(")
    settings_toggle_start = source.index(
        "function createSettingsToggleItem(", agent_toggle_start
    )
    agent_toggle_block = source[agent_toggle_start:settings_toggle_start]

    assert "toggle.controlStyle === 'slider'" in source
    assert "${prefix}-toggle-item-slider" in source
    assert "${prefix}-toggle-slider" in source
    assert "${prefix}-toggle-thumb" in source
    assert (
        """if (usesSliderControl) {
        toggleItem.appendChild(label);
        toggleItem.appendChild(indicator);
    } else {"""
        in agent_toggle_block
    )
    assert "toggle.initialDisabled ? '-1' : '0'" in agent_toggle_block
    assert "checkbox.disabled = true;" in agent_toggle_block
    assert "checkbox._updateStyle = () => {" in agent_toggle_block
    assert "const activeColor = 'var(--neko-popup-accent, #44b7fe)';" in agent_toggle_block
    assert "indicator.style.backgroundColor = checked" in agent_toggle_block
    assert "indicator.style.borderColor = checked" in agent_toggle_block
    assert "createTaskHudSettingsSidePanel" not in source
    assert "task-hud-settings" not in source
    assert "advanced-settings-entry" not in source


@pytest.mark.unit
def test_floating_window_label_is_localized_in_all_supported_locales():
    expected = {
        "en": "Show floating window",
        "es": "Mostrar ventana flotante",
        "ja": "フローティングウィンドウを表示",
        "ko": "플로팅 창 표시",
        "pt": "Mostrar janela flutuante",
        "ru": "Показать плавающее окно",
        "zh-CN": "显示悬浮窗",
        "zh-TW": "顯示懸浮視窗",
    }

    for locale, label in expected.items():
        data = json.loads((LOCALES_PATH / f"{locale}.json").read_text(encoding="utf-8"))
        assert data["settings"]["toggles"]["showTaskHud"] == label


@pytest.mark.unit
def test_status_refresh_does_not_overwrite_cross_window_hud_preference():
    source = _read(APP_AGENT_PATH)
    check_start = source.index("function checkAndToggleTaskHUD(event)")
    check_end = source.index("window.checkAndToggleTaskHUD =", check_start)
    check_block = source[check_start:check_end]

    assert "if (changedCheckbox) {" in check_block
    assert "taskhudOn = changedCheckbox.checked;" in check_block
    assert (
        "} else if (taskhudCheckbox && taskhudCheckbox.checked !== taskhudOn) {"
        in check_block
    )
    assert "taskhudCheckbox.checked = taskhudOn;" in check_block
    assert (
        "if (taskhudCheckbox) {\n            taskhudOn = taskhudCheckbox.checked;"
        not in check_block
    )


@pytest.mark.unit
def test_day6_tutorial_can_temporarily_show_a_user_hidden_task_hud():
    hud_source = _read(COMMON_UI_HUD_PATH)
    director_source = _read(AVATAR_ROUNDS_PATH)
    show_start = hud_source.index(
        "window.AgentHUD.showAgentTaskHUD = function (options = {})"
    )
    show_end = hud_source.index(
        "window.AgentHUD.hideAgentTaskHUD = function", show_start
    )
    show_block = hud_source[show_start:show_end]

    assert (
        "const ignoreVisibilityPreference = options.ignoreVisibilityPreference === true;"
        in show_block
    )
    assert (
        "(!ignoreVisibilityPreference && !isAgentTaskHudVisiblePreferenceEnabled())"
        in show_block
    )
    assert (
        "window.AgentHUD.showAgentTaskHUD({ ignoreVisibilityPreference: true });"
        in director_source
    )


@pytest.mark.unit
def test_standalone_hud_uses_window_ownership_instead_of_floating_preference():
    template_source = _read(AGENT_HUD_TEMPLATE_PATH)

    assert (
        "window.AgentHUD.showAgentTaskHUD({ ignoreVisibilityPreference: true });"
        in template_source
    )


@pytest.mark.unit
def test_user_plugin_row_toggles_only_via_slider_and_management_always_opens():
    """用户插件：点文字不开关，只点滑块；管理面板不依赖开关状态。"""
    popup_source = _read(AVATAR_POPUP_PATH)
    hud_source = _read(COMMON_UI_HUD_PATH)

    agent_toggle_start = popup_source.index("function createToggleItem(")
    settings_toggle_start = popup_source.index(
        "function createSettingsToggleItem(", agent_toggle_start
    )
    agent_toggle_block = popup_source[agent_toggle_start:settings_toggle_start]

    assert "agent-user-plugin" in agent_toggle_block
    assert "controlOnlyToggle" in agent_toggle_block
    assert "只有滑块按钮切换开/关" in agent_toggle_block
    assert "indicator.addEventListener('click'" in agent_toggle_block
    assert "有侧栏入口时" in agent_toggle_block

    side_panel_start = hud_source.index("侧边快捷入口（用户插件管理面板")
    side_panel_end = hud_source.index("// 创建 Agent 任务 HUD", side_panel_start)
    side_panel_block = hud_source[side_panel_start:side_panel_end]
    assert "管理面板不依赖「用户插件」开关状态" in side_panel_block
    assert "openActionTarget" in side_panel_block
    assert "sidePanel.style.pointerEvents = 'auto'" in side_panel_block
    assert "toggleItem.addEventListener('click'" in side_panel_block
    assert "closest(`.${avatarPrefix}-toggle-indicator`)" in side_panel_block

    assert "return 40;" in popup_source
    assert "agent-user-plugin-actions" in popup_source
