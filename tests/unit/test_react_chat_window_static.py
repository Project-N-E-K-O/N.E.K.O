from pathlib import Path


APP_REACT_CHAT_WINDOW_PATH = Path(__file__).resolve().parents[2] / "static" / "app-react-chat-window.js"
REACT_CHAT_STYLES_PATH = Path(__file__).resolve().parents[2] / "frontend" / "react-neko-chat" / "src" / "styles.css"


def test_chat_surface_mode_preference_is_shared_with_electron():
    source = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")

    gate_block = source.split("function shouldPersistChatSurfaceModePreference()", 1)[1].split(
        "function readChatSurfaceModePreference()",
        1,
    )[0]
    read_block = source.split("function readChatSurfaceModePreference()", 1)[1].split(
        "function persistChatSurfaceModePreference(mode)",
        1,
    )[0]
    persist_block = source.split("function persistChatSurfaceModePreference(mode)", 1)[1].split(
        "function readGalgameModePreference()",
        1,
    )[0]

    assert "electron-chat-window" not in gate_block
    assert "return true;" in gate_block
    assert "localStorage.getItem(CHAT_SURFACE_MODE_STORAGE_KEY)" in read_block
    assert "if (mode !== 'full' && mode !== 'compact') return;" in persist_block
    assert "localStorage.setItem(CHAT_SURFACE_MODE_STORAGE_KEY, mode)" in persist_block


def test_desktop_compact_history_uses_workarea_not_browserwindow_viewport():
    script = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")
    styles = REACT_CHAT_STYLES_PATH.read_text(encoding="utf-8")

    assert "normalizeCompactDesktopWorkArea" in script
    assert "--compact-desktop-workarea-width" in script
    assert "--compact-desktop-workarea-height" in script

    desktop_history_block = styles.split(
        "body.electron-chat-window.subtitle-web-host .compact-export-history-anchor",
        1,
    )[1].split(".compact-export-history-panel", 1)[0]

    assert "--compact-desktop-workarea-width" in desktop_history_block
    assert "--compact-desktop-workarea-height" in desktop_history_block
    assert "vh" not in desktop_history_block
    assert "vw" not in desktop_history_block
