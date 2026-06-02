from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_REACT_CHAT_WINDOW_PATH = PROJECT_ROOT / "static" / "app-react-chat-window.js"
APP_UI_PATH = PROJECT_ROOT / "static" / "app-ui.js"
AVATAR_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "avatar-ui-buttons.js"
CHAT_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "chat.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    if start not in source:
        raise ValueError(f"missing start delimiter: {start!r}")
    remainder = source.split(start, 1)[1]
    if end not in remainder:
        raise ValueError(f"missing end delimiter after {start!r}: {end!r}")
    return remainder.split(end, 1)[0]


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


def test_idle_dock_enters_minimized_surface_mode_without_setminimized_options():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)
    set_surface_block = _between(
        source,
        "function setChatSurfaceMode(nextMode) {",
        "\n    function cycleChatSurfaceMode()",
    )

    # enterIdleDock goes through chatSurfaceMode so compact/full/minimized state
    # stays aligned with the minimized visual class after the upstream compact merge.
    assert "setChatSurfaceMode('minimized');" in source
    assert "var enteringMinimized = nextMinimized && !previousMinimized;" not in set_surface_block
    assert "renderWindow();" in set_surface_block
    assert "setMinimized(nextMinimized);" in set_surface_block
    assert set_surface_block.index("renderWindow();") < set_surface_block.index("setMinimized(nextMinimized);")
    assert "setMinimized(true, {" not in source

    # exitIdleDock restores the previous real surface mode without adding
    # idle-dock options or branches to setMinimized itself.
    assert "setChatSurfaceMode(normalizeChatSurfaceMode(lastRestorableChatSurfaceMode));" in source
    assert "setMinimized(false, {" not in source


def test_electron_idle_dock_uses_desktop_return_ball_bridge():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)

    assert "neko:idle-return-ball-state" in source
    assert "function handleElectronIdleReturnBallState(detail)" in source
    assert "bridge.idleDockCollapse" in source
    assert "bridge.idleDockExpand" in source
    assert "electronIdleDockEntering" in source
    assert "electronIdleDockDesired" in source
    assert "electronIdleDockGeneration" in source
    assert "isElectronIdleDockCurrent(generation)" in source
    assert "hasElectronIdleDockPendingOrActive()" in source
    assert "entrySavedBounds" in source
    assert "clearElectronIdleDockPositionFrame()" in source
    assert "electronIdleDockPositionSeq" in source
    assert "electronIdleDockCurrentBounds" in source
    assert "electronIdleDockWorkArea" in source
    assert "rememberElectronIdleDockBounds" in source
    assert "scheduleElectronIdleDockPosition()" in source
    assert "scheduleElectronIdleDockRetry(generation)" in source
    assert "detail.screenRect" in source
    assert "detail.reason === 'return-ball-drag-demotion'" in source
    assert "detail.reason === 'return-ball-drag-end'" in source
    assert "idle-dock-exit-preserve" in source
    assert "preserveScreenRect" in source
    assert "idleDockCommitCollapsedBounds" in source
    assert "clampElectronDockBounds(preserveBounds, workArea)" in source
    assert "HOME_IDLE_DOCK_GAP" in source


def test_app_ui_broadcasts_return_ball_screen_rect_for_desktop_idle_dock():
    source = _read(APP_UI_PATH)

    assert "action: 'idle_return_ball_state'" in source
    assert "function canPostIdleReturnBallDesktopState()" in source
    assert "electron-chat-window" in source
    assert "function getIdleReturnBallScreenRect(container)" in source
    assert "window.screenX" in source
    assert "window.appInterpage && window.appInterpage.nekoBroadcastChannel" in source
    assert "detail.source === 'return-ball-drag-demotion' ? 'return-ball-drag-demotion' : 'visual-tier'" in source
    assert "'return-ball-dragging'" in source
    assert "scheduleIdleReturnBallDesktopDragState" in source
    assert "clearIdleReturnBallDesktopDragStateFrame" in source
    assert "getReturnBallDragScreenRect(" in source


def test_react_chat_broadcasts_minimized_screen_rect_for_cat1_follow():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)
    avatar_source = _read(AVATAR_UI_BUTTONS_PATH)

    assert "function dispatchElectronChatMinimizedState(reason)" in source
    assert "action: 'idle_chat_minimized_state'" in source
    assert "new CustomEvent('neko:idle-chat-minimized-state'" in source
    assert "bridge.getBounds().then(function (bounds)" in source
    assert "isElectronChatWindowCollapsed(bridge)" in source
    assert "ensureElectronChatMinimizedStateBridge()" in source
    assert "ELECTRON_CHAT_MINIMIZED_STATE_HEARTBEAT_MS = 1000" in source
    assert "setInterval(function ()" in source
    assert "}, 500);" in source
    assert "electronChatMinimizedStatePublishedAt" in source
    assert "_NEKO_IDLE_DESKTOP_CHAT_RECT_STALE_MS = 2500" in avatar_source


def test_electron_chat_loads_interpage_before_react_chat_for_desktop_cat1_sync():
    source = _read(CHAT_TEMPLATE_PATH)

    assert 'class="electron-chat-window subtitle-web-host"' in source
    assert source.index('/static/app-interpage.js') < source.index('/static/app-react-chat-window.js')


def test_react_chat_applies_desktop_cat1_pair_move_bounds_when_collapsed():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)

    assert "electronCat1PairMoveBoundsFrame" in source
    assert "function scheduleElectronCat1PairMoveBounds(bounds)" in source
    assert "async function applyElectronCat1PairMoveBounds(bounds)" in source
    assert "window.addEventListener('neko:idle-chat-pair-move-bounds'" in source
    assert "scheduleElectronCat1PairMoveBounds(detail.screenRect || detail.bounds)" in source
    assert "if (!bridge || !isElectronChatWindowCollapsed(bridge)) return;" in source
    assert "if (hasElectronIdleDockPendingOrActive()) return;" in source
    assert "bridge.idleDockCommitCollapsedBounds(targetBounds)" in source
    assert "scheduleElectronChatMinimizedState('cat1-pair-move')" in source


def test_idle_dock_uses_mutation_observer_to_detect_minimize_completion():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)

    # enterIdleDock sets up a MutationObserver on the shell to detect
    # when the minimize animation finishes before applying dock position
    assert "idleDockMinimizeObserver" in source
    assert "is-minimized" in source
    assert "stopIdleDockMinimizeObserver" in source
    assert "function finishIdleDockMinimize(shell)" in source
    assert "function scheduleIdleDockMinimizeFallback(shell)" in source
    assert "scheduleIdleDockMinimizeFallback(shell)" in source
    assert "function hasIdleDockPendingOrActive()" in source
    assert "idleDockActive || idleDockTriggeredMinimize || idleDockMinimizeObserver" in source
    assert "triggered && !wasActive && wasTransitioning" in source
    assert "cancelActiveAnimation()" in source
    assert "shell.classList.remove('is-minimized', 'is-collapsing', 'is-idle-docked')" in source


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


def test_idle_dock_exit_preserves_drag_demotion_position():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)

    assert "function exitIdleDock(options)" in source
    assert "function exitElectronIdleDock(options)" in source
    assert "preserveCurrentPosition" in source
    assert "detail.source === 'return-ball-drag-demotion'" in source
    assert "detail.reason === 'return-ball-drag-demotion'" in source
    assert "detail.reason === 'return-ball-drag-end'" in source
    assert "detail.reason === 'viewport-resize'" in source
    assert "function shouldIgnoreElectronIdleDockInactiveViewportResize(detail, activeTier)" in source
    assert "if (shouldIgnoreElectronIdleDockInactiveViewportResize(detail, activeTier))" in source
    assert "async function commitElectronIdleDockCollapsedBounds(bridge, bounds, generation)" in source
    assert "result !== false && result !== null && result !== undefined" in source
    assert "await waitElectronIdleDockCommitRetry(80)" in source
    assert "preserveScreenRect: shouldPreserveCurrentPosition ? detail.screenRect : null" in source
    assert "await commitElectronIdleDockCollapsedBounds(bridge, preserveBounds, exitGeneration)" in source
    assert "wasActive && saved && !preserveCurrentPosition" in source
    assert "wasActive && triggered && minimized && preserveCurrentPosition" in source
    assert "setChatSurfaceMode(normalizeChatSurfaceMode(lastRestorableChatSurfaceMode));" in source
