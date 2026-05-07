from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_REACT_CHAT_WINDOW_PATH = PROJECT_ROOT / "static" / "app-react-chat-window.js"
APP_UI_PATH = PROJECT_ROOT / "static" / "app-ui.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_idle_dock_is_limited_to_cat2_and_cat3_tiers():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)

    assert "var IDLE_DOCK_TIER_CAT2 = 'cat2';" in source
    assert "var IDLE_DOCK_TIER_CAT3 = 'cat3';" in source
    assert "function isIdleDockTierActive()" in source
    assert "detail.tier === IDLE_DOCK_TIER_CAT2 || detail.tier === IDLE_DOCK_TIER_CAT3" in source
    assert "window.addEventListener('live2d-goodbye-click'" not in source


def test_idle_dock_does_not_pollute_normal_minimize_export_or_app_ui():
    react_source = _read(APP_REACT_CHAT_WINDOW_PATH)
    ui_source = _read(APP_UI_PATH)

    export_block = _between(
        react_source,
        "window.reactChatWindowHost = {",
        "\n    };\n\n})();",
    )
    assert "setMinimized:" not in export_block
    assert "setIdlePresentation" not in export_block
    assert "clearIdlePresentation" not in export_block
    assert "syncReactChatWindowGoodbyeMinimized" not in ui_source


def test_setMinimized_has_no_options_parameter_and_no_idle_dock_branches():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)

    # setMinimized must have the original single-parameter signature
    assert "function setMinimized(nextMinimized) {" in source
    assert "function setMinimized(nextMinimized, options)" not in source

    # No idle-dock variables/branches inside setMinimized body
    set_minimized_block = _between(
        source,
        "function setMinimized(nextMinimized) {",
        "\n    function toggleMinimized()",
    )
    assert "idleDock" not in set_minimized_block
    assert "idleDockRequested" not in set_minimized_block
    assert "idleDockPendingAfterCollapse" not in set_minimized_block
    assert "restoreSavedPosition" not in set_minimized_block
    assert "clearIdleDockContext" not in set_minimized_block
    assert "clearIdleDockState" not in set_minimized_block
    assert "opts.idleDock" not in set_minimized_block


def test_idle_dock_calls_setMinimized_externally_without_options():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)

    # enterIdleDock calls setMinimized(true) with no second argument
    assert "setMinimized(true);" in source
    assert "setMinimized(true, {" not in source

    # exitIdleDock calls setMinimized(false) with no second argument
    assert "setMinimized(false);" in source
    assert "setMinimized(false, {" not in source


def test_idle_dock_uses_mutation_observer_to_detect_minimize_completion():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)

    # enterIdleDock sets up a MutationObserver on the shell to detect
    # when the minimize animation finishes before applying dock position
    assert "idleDockMinimizeObserver" in source
    assert "is-minimized" in source
    assert "stopIdleDockMinimizeObserver" in source


def test_toggle_minimized_restores_position_before_expand_when_idle_docked():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)

    toggle_block = _between(
        source,
        "function toggleMinimized() {",
        "function prewarmUserDisplayName()",
    )
    assert "minimized && idleDockActive && idleDockSavedPosition" in toggle_block
    assert "idleDockSavedPosition.left" in toggle_block
    assert "idleDockSavedPosition.top" in toggle_block
    assert "is-idle-docked" in toggle_block
