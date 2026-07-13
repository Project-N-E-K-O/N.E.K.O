"""
Phase 5 — 回归与边界验证：静态契约测试

验证核心约束：本功能只做"加自动触发条件 + 换表现"，不改写老链路。
依据：implementation-flow.md Phase 5 重点验证 1-6 + design.md 6.2 不应改坏 1-6。
"""

from pathlib import Path
from tests.static_app_parts import read_path_or_parts


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_AUTO_GOODBYE_PATH = PROJECT_ROOT / "static" / "app" / "app-auto-goodbye.js"
APP_UI_PATH = PROJECT_ROOT / "static" / "app" / "app-ui"
APP_BUTTONS_PATH = PROJECT_ROOT / "static" / "app" / "app-buttons.js"
AVATAR_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "avatar" / "avatar-ui-buttons"
APP_REACT_CHAT_PATH = PROJECT_ROOT / "static" / "app" / "app-react-chat-window"
APP_INTERPAGE_PATH = PROJECT_ROOT / "static" / "app" / "app-interpage"


def _read(path: Path) -> str:
    return read_path_or_parts(path)


# ── 5.1 手动 goodbye 链路未被改写 ──────────────────────────


def test_app_buttons_preserve_goodbye_backend_silence_contract():
    """请她离开链路必须把猫态静默同步给后端。"""
    source = _read(APP_BUTTONS_PATH)

    assert "window.isNekoGoodbyeModeActive()" in source
    assert "action: 'end_session'" in source
    assert "goodbye_active: !!isGoodbyeMode" in source
    assert "action: 'goodbye_state'" in source
    assert "reason: 'return-session'" in source


def test_app_ui_changes_are_limited_to_return_ball_desktop_bridge_contract():
    """Keep the return-ball desktop bridge without changing return semantics."""
    source = _read(APP_UI_PATH)

    assert "action: 'idle_return_ball_state'" in source
    assert "function canPostIdleReturnBallDesktopState()" in source
    assert "function isIdleCat1PlaygroundActiveForReturnBallDesktopBridge()" in source
    assert "__nekoIdleCat1PlaygroundDropState" in source
    assert "__nekoIdleCat1PlaygroundPendingEntry" in source
    bridge_start = source.index("function isIdleCat1PlaygroundActiveForReturnBallDesktopBridge()")
    bridge_end = source.index("function canPostIdleReturnBallDesktopState()", bridge_start)
    bridge_block = source[bridge_start:bridge_end]
    assert "buttons[i].__nekoIdleCat1PlaygroundPendingEntry" in bridge_block
    assert "if (isIdleCat1PlaygroundActiveForReturnBallDesktopBridge()) return;" in source
    assert "electron-chat-window" in source
    assert "function getReturnBallDragScreenRect(" in source
    assert "'return-ball-dragging'" in source
    assert "window.dispatchEvent(new CustomEvent(`${match[1]}-return-click`" in source
    assert "returnSessionButton.click()" not in source
    assert "start_session" not in source


# ── 5.2 auto-goodbye 不直接执行离开副作用 ──────────


def test_auto_goodbye_only_syncs_silence_without_running_goodbye_side_effects():
    source = _read(APP_AUTO_GOODBYE_PATH)

    assert "window.dispatchEvent(new CustomEvent('live2d-goodbye-click'" in source
    assert "action: 'goodbye_state'" in source
    assert "window.__nekoGoodbyeSilentState" in source
    assert "pending: !!pending" in source
    assert "end_session" not in source
    assert "stopProactiveChatSchedule" not in source
    assert "syncVoiceChatComposerHidden" not in source
    assert "stopProactiveVisionDuringSpeech" not in source
    assert "resetSessionButton.click" not in source
    assert "resetSessionButton.addEventListener" not in source


# ── 5.3 return-ball 回来仍走 handleReturnClick ─────────────


def test_avatar_return_button_dispatches_prefix_return_click_not_start_session():
    source = _read(AVATAR_UI_BUTTONS_PATH)

    assert "window.dispatchEvent(event);" in source
    assert "${prefix}-return-click" in source or "prefix}-return-click" in source
    assert "returnSessionButton.click()" not in source
    assert "start_session" not in source


# ── 5.4 start_session 不被误触发 ──────────────────────────


def test_no_new_code_references_start_session_or_return_session_button():
    for path in [
        APP_AUTO_GOODBYE_PATH,
        AVATAR_UI_BUTTONS_PATH,
        APP_INTERPAGE_PATH,
        APP_REACT_CHAT_PATH,
    ]:
        source = _read(path)
        assert "start_session" not in source, f"{path.name} references start_session"
        assert "returnSessionButton" not in source, (
            f"{path.name} references returnSessionButton"
        )


# ── 5.5 queued/running 任务阻断 auto-goodbye ──────────────


def test_has_blocking_active_work_treats_queued_and_running_as_blockers():
    source = _read(APP_AUTO_GOODBYE_PATH)

    assert "queued" in source
    assert "running" in source
    assert "completed" not in source or "'completed'" not in source
    assert "failed" not in source or "'failed'" not in source
    assert "cancelled" not in source or "'cancelled'" not in source


def test_try_auto_goodbye_has_autoGoodbyeTriggered_guard():
    """防止 goodbye manager 没设上 _goodbyeClicked 时重复派发事件。"""
    source = _read(APP_AUTO_GOODBYE_PATH)

    assert "state.autoGoodbyeTriggered" in source
    assert source.index("state.autoGoodbyeTriggered") < source.index(
        "window.dispatchEvent(new CustomEvent('live2d-goodbye-click'"
    )


# ── 5.6 deferred reminder (保持 running) 被正确阻断 ──────


def test_deferred_reminder_running_state_is_blocked_without_exemption():
    source = _read(APP_AUTO_GOODBYE_PATH)
    compact_source = "".join(source.split()).lower()

    # No special exemption for reminder / deferred task types
    assert "deferred" not in source
    assert "reminder" not in source.lower() or "deferred reminder" not in source.lower()
    assert "if(task.type==='reminder')" not in compact_source
    assert 'if(task.type==="reminder")' not in compact_source
    assert "if(task.type=='reminder')" not in compact_source
    assert 'if(task.type=="reminder")' not in compact_source
    assert "case'reminder'" not in compact_source
    assert 'case"reminder"' not in compact_source


# ── design.md 6.2: 不应改坏 ───────────────────────────────


def test_old_chat_container_not_referenced_in_new_code():
    for path in [
        APP_AUTO_GOODBYE_PATH,
        AVATAR_UI_BUTTONS_PATH,
        APP_INTERPAGE_PATH,
        APP_REACT_CHAT_PATH,
    ]:
        source = _read(path)
        lines_with_chat_container = [
            line for line in source.splitlines()
            if "chat-container" in line
            and "react-chat-window" not in line.lower()
            and not line.strip().startswith("//")
        ]
        assert len(lines_with_chat_container) == 0, (
            f"{path.name} references #chat-container: {lines_with_chat_container}"
        )


def test_tutorial_guards_present_in_auto_goodbye():
    source = _read(APP_AUTO_GOODBYE_PATH)

    assert "isNekoHomeTutorialInteractionLocked" in source
    assert "yui-taking-over" in source


def test_chat_electron_window_excluded():
    source = _read(APP_AUTO_GOODBYE_PATH)

    # auto-goodbye only starts on home page, not /chat
    assert "isEligiblePage" in source or "pathname" in source


def test_drag_guard_present_in_auto_goodbye():
    source = _read(APP_AUTO_GOODBYE_PATH)

    assert "dragging" in source.lower()
