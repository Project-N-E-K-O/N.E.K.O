from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_AGENT_JS = PROJECT_ROOT / "static" / "app-agent.js"
COMMON_UI_HUD_JS = PROJECT_ROOT / "static" / "common-ui-hud.js"
AGENT_UI_V2_JS = PROJECT_ROOT / "static" / "js" / "agent_ui_v2.js"
APP_REACT_CHAT_WINDOW_JS = PROJECT_ROOT / "static" / "app-react-chat-window.js"
APP_WEBSOCKET_JS = PROJECT_ROOT / "static" / "app-websocket.js"
PNGTUBER_CORE_JS = PROJECT_ROOT / "static" / "pngtuber-core.js"
PNGTUBER_PROTOCOL_JS = PROJECT_ROOT / "static" / "neko-pngtuber-protocol.js"


def test_app_agent_includes_pngtuber_agent_prefix():
    script = APP_AGENT_JS.read_text(encoding="utf-8")

    assert "const AGENT_AVATAR_PREFIXES = ['live2d', 'vrm', 'mmd', 'pngtuber'];" in script
    assert "function getAgentIds(suffix)" in script
    assert "AGENT_AVATAR_PREFIXES.map(prefix => `${prefix}-agent-${suffix}`)" in script
    assert "AGENT_AVATAR_PREFIXES.map(prefix => `${prefix}-agent-status`)" in script
    assert "window.addEventListener(`${prefix}-floating-buttons-ready`, handler);" in script
    assert "/^(live2d|vrm|mmd|pngtuber)-agent-/" in script
    assert '[id="pngtuber-popup-agent"]' in script


def test_agent_hud_uses_current_avatar_prefix_for_status_id():
    script = COMMON_UI_HUD_JS.read_text(encoding="utf-8")
    start = script.index("window.AgentHUD._createAgentPopupContent = function (popup)")
    end = script.index("const agentToggles = [", start)
    block = script[start:end]

    assert "const avatarPrefix = this._avatarPrefix || 'live2d';" in block
    assert "statusDiv.id = `${avatarPrefix}-agent-status`;" in block
    assert "statusDiv.id = 'live2d-agent-status';" not in block


def test_agent_ui_v2_includes_pngtuber_controls():
    script = AGENT_UI_V2_JS.read_text(encoding="utf-8")

    assert "const AVATAR_PREFIXES = ['live2d', 'vrm', 'mmd', 'pngtuber'];" in script
    assert "const prefixedIds = (suffix) => AVATAR_PREFIXES.map(prefix => `${prefix}-agent-${suffix}`);" in script
    assert "master: getEls(...prefixedIds('master'))" in script
    assert "status: getEls(...prefixedIds('status'))" in script
    assert "window.addEventListener(`${prefix}-floating-buttons-ready`, () => {" in script
    assert "window.getAgentUiV2DebugState = function getAgentUiV2DebugState()" in script
    assert "'mmd-agent-master')" not in script


def test_agent_pngtuber_scripts_bust_static_asset_cache():
    from main_routers import pages_router

    tracked = {
        APP_AGENT_JS,
        AGENT_UI_V2_JS,
        COMMON_UI_HUD_JS,
        PNGTUBER_PROTOCOL_JS,
        PNGTUBER_CORE_JS,
        APP_REACT_CHAT_WINDOW_JS,
        APP_WEBSOCKET_JS,
    }

    assert tracked.issubset(set(pages_router._YUI_GUIDE_ASSET_VERSION_PATHS))


def test_react_chat_host_listens_for_pngtuber_avatar_ready():
    script = APP_REACT_CHAT_WINDOW_JS.read_text(encoding="utf-8")

    assert "'pngtuber-floating-buttons-ready'" in script


def test_websocket_model_ready_listens_for_pngtuber_avatar_ready():
    script = APP_WEBSOCKET_JS.read_text(encoding="utf-8")

    assert "window.addEventListener('pngtuber-model-loaded', _onModelReady);" in script
