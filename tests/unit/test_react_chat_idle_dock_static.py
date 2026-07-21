from pathlib import Path
from tests.static_app_parts import read_path_or_parts


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_REACT_CHAT_WINDOW_PATH = PROJECT_ROOT / "static" / "app" / "app-react-chat-window"
APP_UI_PATH = PROJECT_ROOT / "static" / "app" / "app-ui"
AVATAR_UI_BUTTONS_PATH = PROJECT_ROOT / "static" / "avatar" / "avatar-ui-buttons"
CHAT_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "chat.html"


def _read(path: Path) -> str:
    return read_path_or_parts(path)


def _between(source: str, start: str, end: str) -> str:
    if start not in source:
        raise ValueError(f"missing start delimiter: {start!r}")
    remainder = source.split(start, 1)[1]
    if end not in remainder:
        raise ValueError(f"missing end delimiter after {start!r}: {end!r}")
    return remainder.split(end, 1)[0]


def test_idle_dock_is_limited_to_cat2_and_cat3_tiers():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)

    assert "IDLE_DOCK_TIER_CAT2 = 'cat2';" in source
    assert "IDLE_DOCK_TIER_CAT3 = 'cat3';" in source
    assert "function isIdleDockTierActive()" in source
    assert "detail.tier === IDLE_DOCK_TIER_CAT2 || detail.tier === IDLE_DOCK_TIER_CAT3" in source
    goodbye_click_block = _between(
        source,
        "window.addEventListener('live2d-goodbye-click'",
        "window.addEventListener('live2d-return-click'",
    )
    assert "setGoodbyeComposerHidden(true, 'live2d-goodbye-click')" in goodbye_click_block
    assert "enterIdleDock" not in goodbye_click_block
    assert "setChatSurfaceMode('minimized')" not in goodbye_click_block


def test_idle_dock_does_not_pollute_normal_minimize_export_or_app_ui():
    react_source = _read(APP_REACT_CHAT_WINDOW_PATH)
    ui_source = _read(APP_UI_PATH)

    export_block = _between(
        react_source,
        "Object.assign(window.reactChatWindowHost, {",
        "\n    });",
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
        "function toggleMinimized()",
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
        "function cycleChatSurfaceMode()",
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
    assert "setChatSurfaceMode(coerceChatSurfaceModeForHost(lastRestorableChatSurfaceMode));" in source
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
    assert "await bridge.idleDockCommitCollapsedBounds(nextBounds)" in source
    assert "clampElectronDockBounds(preserveBounds, workArea)" in source
    assert "HOME_IDLE_DOCK_GAP" in source

    handler_block = _between(
        source,
        "handleElectronIdleReturnBallState = function handleElectronIdleReturnBallState(detail) {",
        "// Enter idle-dock: minimize if needed, then position next to return-ball.",
    )
    assert "shouldIgnoreElectronIdleDockTransientDragHide(detail)" in handler_block
    assert "var shouldPreserveCurrentPosition = detail && !!detail.screenRect && (" in handler_block
    assert "var shouldPreserveCurrentPosition = activeTier && detail" not in handler_block

    transient_drag_block = _between(
        source,
        "function shouldIgnoreElectronIdleDockTransientDragHide(detail) {",
        "function waitElectronIdleDockCommitRetry(delayMs) {",
    )
    assert "detail.reason === 'return-ball-drag-start'" in transient_drag_block
    assert "detail.reason === 'return-ball-drag-active'" in transient_drag_block
    assert "detail.reason === 'return-ball-dragging'" in transient_drag_block


def test_full_chat_min_height_guard_recognizes_native_88px_collapsed_window():
    source = CHAT_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "var ELECTRON_COLLAPSED_MAX_SIZE = 90;" in source
    assert "b.width <= ELECTRON_COLLAPSED_MAX_SIZE" in source
    assert "b.height <= ELECTRON_COLLAPSED_MAX_SIZE" in source
    assert "b.width <= BALL + 4 && b.height <= BALL + 4" not in source


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
    assert "function getElectronChatMinimizedScreenRect(windowRect)" in source
    assert "window.__nekoMinimizedChatBallScreenRect" in source
    assert "width: MINIMIZED_SIZE" in source
    assert "height: MINIMIZED_SIZE" in source
    assert "Math.round(windowRect.left + Math.max(0, (windowRect.width - MINIMIZED_SIZE) / 2))" in source
    assert "Math.round(windowRect.top + Math.max(0, (windowRect.height - MINIMIZED_SIZE) / 2))" in source
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

    dispatch_block = source.split("function dispatchElectronChatMinimizedState(reason)", 1)[1].split(
        "function scheduleElectronChatMinimizedState(reason)",
        1,
    )[0]
    assert "getElectronChatMinimizedScreenRect(windowRect)" in dispatch_block
    assert "classList.contains('is-minimized')" not in dispatch_block
    assert "querySelector('.react-chat-minimized-icon')" not in dispatch_block


def test_electron_chat_minimized_heartbeat_prefers_rendered_ball_over_carrier_bounds():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)
    rect_block = _between(
        source,
        "function getElectronChatMinimizedScreenRect(windowRect) {",
        "function dispatchElectronChatMinimizedState(reason)",
    )

    rendered_rect = "normalizeElectronWindowBoundsRect(\n            window.__nekoMinimizedChatBallScreenRect"
    fallback_left = "Math.round(windowRect.left + Math.max(0, (windowRect.width - MINIMIZED_SIZE) / 2))"
    assert rendered_rect in rect_block
    assert "if (renderedBallRect) return renderedBallRect;" in rect_block
    assert fallback_left in rect_block
    assert rect_block.index("if (renderedBallRect) return renderedBallRect;") < rect_block.index(fallback_left)


def test_cat1_minimized_ball_target_wins_over_stale_compact_surface():
    source = _read(AVATAR_UI_BUTTONS_PATH)
    target_block = _between(
        source,
        "function _getNekoIdleCat1Target(container, chatRect, options = {}) {",
        "function _setNekoIdleCat1ContainerPosition",
    )

    side_index = target_block.index("const minimizedSideTarget = _getNekoIdleCat1SideTarget(container, chatRect);")
    return_side_index = target_block.index("return minimizedSideTarget;")
    compact_index = target_block.index("const compactSurfaceRect = _getNekoIdleChatCompactSurfaceRect();")
    assert side_index < return_side_index < compact_index
    assert "return null;" in target_block


def test_desktop_cat1_minimized_and_compact_surface_state_are_timestamp_ordered():
    source = _read(AVATAR_UI_BUTTONS_PATH)

    state_init_block = _between(
        source,
        "let _nekoIdleDesktopChatMinimizedState = {",
        "function _getNekoIdleDesktopStateSourceUpdatedAt(detail, fallbackUpdatedAt) {",
    )
    assert "sourceUpdatedAt: 0" in state_init_block
    assert "expandedRecent: false" in state_init_block
    assert "function _getNekoIdleDesktopStateSourceUpdatedAt(detail, fallbackUpdatedAt)" in source
    assert "function _isNekoIdleDesktopStateStaleAgainst(sourceUpdatedAt, state)" in source
    assert "function _isNekoIdleDesktopStateNewerThan(sourceUpdatedAt, state)" in source
    assert "function _makeNekoIdleDesktopChatMinimizedState(minimized, screenRect, updatedAt, sourceUpdatedAt, expandedRecent)" in source
    assert "function _makeNekoIdleDesktopCompactSurfaceState(visible, screenRect, updatedAt, sourceUpdatedAt)" in source
    assert "if (state.expandedRecent === false) return false;" in source

    pair_move_block = _between(
        source,
        "function _rememberNekoIdleDesktopChatPairMoveRect(screenRect) {",
        "function _getNekoIdleDesktopChatPairMoveSignature(screenRect) {",
    )
    assert "_nekoIdleDesktopChatMinimizedState = _makeNekoIdleDesktopChatMinimizedState(" in pair_move_block
    assert "_nekoIdleDesktopCompactSurfaceState = _makeNekoIdleDesktopCompactSurfaceState(" in pair_move_block

    desktop_compact_rect = _between(
        source,
        "function _getNekoIdleDesktopCompactSurfaceRect() {",
        "function _getNekoIdleChatCompactSurfaceRect() {",
    )
    assert "_nekoIdleDesktopChatMinimizedState.minimized" in desktop_compact_rect
    assert "_isNekoIdleDesktopStateNewerThan(_nekoIdleDesktopChatMinimizedState.sourceUpdatedAt, state)" in desktop_compact_rect

    desktop_minimized_rect = _between(
        source,
        "function _getNekoIdleDesktopChatMinimizedRect() {",
        "function _isNekoIdleDesktopChatExpandedRecent() {",
    )
    assert "_nekoIdleDesktopCompactSurfaceState.visible" in desktop_minimized_rect
    assert "_isNekoIdleDesktopStateNewerThan(_nekoIdleDesktopCompactSurfaceState.sourceUpdatedAt, state)" in desktop_minimized_rect

    minimized_listener = _between(
        source,
        "window.addEventListener('neko:idle-chat-minimized-state', (event) => {",
        "window.addEventListener('neko:idle-chat-compact-surface-state', (event) => {",
    )
    assert "const sourceUpdatedAt = _getNekoIdleDesktopStateSourceUpdatedAt(detail, receivedAt);" in minimized_listener
    assert "if (_isNekoIdleDesktopStateStaleAgainst(sourceUpdatedAt, _nekoIdleDesktopChatMinimizedState)) return;" in minimized_listener
    assert "const compactSurfaceCurrentlyVisible = !!_getNekoIdleDesktopCompactSurfaceRect();" in minimized_listener
    assert "_nekoIdleDesktopCompactSurfaceState.visible" in minimized_listener
    assert "_isNekoIdleDesktopStateStaleAgainst(sourceUpdatedAt, _nekoIdleDesktopCompactSurfaceState)" in minimized_listener
    assert "_nekoIdleDesktopCompactSurfaceState = _makeNekoIdleDesktopCompactSurfaceState(" in minimized_listener
    assert "!!(detail && !detail.minimized && !compactSurfaceCurrentlyVisible)" in minimized_listener

    compact_listener = _between(
        source,
        "window.addEventListener('neko:idle-chat-compact-surface-state', (event) => {",
        "const currentTier = _readNekoAutoGoodbyeVisualTier();",
    )
    assert "const sourceUpdatedAt = _getNekoIdleDesktopStateSourceUpdatedAt(detail, receivedAt);" in compact_listener
    assert "if (_isNekoIdleDesktopStateStaleAgainst(sourceUpdatedAt, _nekoIdleDesktopCompactSurfaceState)) return;" in compact_listener
    assert "_nekoIdleDesktopChatMinimizedState.minimized" in compact_listener
    assert "_isNekoIdleDesktopStateStaleAgainst(sourceUpdatedAt, _nekoIdleDesktopChatMinimizedState)" in compact_listener
    assert "const heartbeat = !!(detail && detail.heartbeat);" in compact_listener
    assert "if (!heartbeat) {" in compact_listener
    assert "_nekoIdleDesktopChatMinimizedState = _makeNekoIdleDesktopChatMinimizedState(" in compact_listener
    # heartbeat 分支必须保留原 sourceUpdatedAt，避免心跳新鲜时间戳扰乱跨状态排序
    assert "prevCompactSourceUpdatedAt" in compact_listener
    # 还原后来心跳 catch-up：minimized 确认 false 但 compact 缓存尚未恢复时，
    # heartbeat 应能恢复 compact 可见性（Electron setMinimized 早退不发布 compact 事件）
    assert "!_nekoIdleDesktopChatMinimizedState.minimized" in compact_listener
    # minimized state 重赋值仅在 !heartbeat 分支内
    minimized_reassign_line = compact_listener[compact_listener.index("_nekoIdleDesktopChatMinimizedState = _makeNekoIdleDesktopChatMinimizedState("):]
    assert minimized_reassign_line.index("false") < minimized_reassign_line.index("null")
    assert minimized_reassign_line.index("null") < minimized_reassign_line.index("receivedAt")
    assert minimized_reassign_line.index("receivedAt") < minimized_reassign_line.index("sourceUpdatedAt")


def test_electron_chat_loads_interpage_before_react_chat_for_desktop_cat1_sync():
    source = _read(CHAT_TEMPLATE_PATH)

    assert 'class="electron-chat-window subtitle-web-host"' in source
    assert source.index('/static/app/app-interpage') < source.index('/static/app/app-react-chat-window')
    assert '/static/app/app-interpage/bootstrap-resources-and-model-reload.js?v={{ static_asset_version }}' in source


def test_react_chat_applies_desktop_cat1_pair_move_bounds_when_collapsed():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)

    assert "function isElectronLinuxRuntime()" in source
    assert "runtime.isLinux" in source
    assert "runtime.isLinuxX11" in source
    assert "runtime.platform === 'linux'" in source
    assert "electronCat1PairMoveBoundsFrame" in source
    assert "electronCat1PairMovePendingForce" in source
    assert "electronCat1PairMovePendingReason" in source
    assert "function scheduleElectronCat1PairMoveBounds(bounds, options)" in source
    assert "async function applyElectronCat1PairMoveBounds(bounds, options)" in source
    assert "function electronVisibleYarnRectToWindowBounds(rect, carrierRect)" in source
    assert "ELECTRON_CHAT_MINIMIZED_FALLBACK_WINDOW_SIZE = 83" in source
    assert "window.addEventListener('neko:idle-chat-pair-move-bounds'" in source
    assert "scheduleElectronCat1PairMoveBounds(detail.screenRect || detail.bounds, {" in source
    assert "reason: detail.reason || detail.source || 'cat1-pair-move'" in source
    assert "if (!bridge || !isElectronChatWindowCollapsed(bridge)) return;" in source
    assert "if (hasElectronIdleDockPendingOrActive()) return;" in source
    assert "bridge.idleDockCommitCollapsedBounds(targetBounds)" in source
    assert "scheduleElectronChatMinimizedState(reason)" in source

    apply_block = _between(
        source,
        "async function applyElectronCat1PairMoveBounds(bounds, options) {",
        "function scheduleElectronCat1PairMoveBounds(bounds, options) {",
    )
    assert "var force = !!(options && options.force);" in apply_block
    assert "var reason = options && typeof options.reason === 'string' && options.reason" in apply_block
    assert ": 'cat1-pair-move';" in apply_block
    assert "if (isElectronLinuxRuntime() && !force) return;" in apply_block
    assert "if (isElectronLinuxRuntime()) return;" not in apply_block
    assert "carrierBounds = await bridge.getBounds();" in apply_block
    assert "var targetBounds = electronVisibleYarnRectToWindowBounds(bounds, carrierBounds);" in apply_block
    assert "electronRectToBounds(bounds)" not in apply_block

    visible_to_window_block = _between(
        source,
        "function electronVisibleYarnRectToWindowBounds(rect, carrierRect) {",
        "async function applyElectronCat1PairMoveBounds(bounds, options) {",
    )
    assert "var carrier = normalizeElectronWindowBoundsRect(carrierRect);" in visible_to_window_block
    assert "Math.max(ELECTRON_CHAT_MINIMIZED_FALLBACK_WINDOW_SIZE, Math.round(normalized.width))" in visible_to_window_block
    assert "var insetX = Math.max(0, (carrierWidth - normalized.width) / 2);" in visible_to_window_block
    assert "var insetY = Math.max(0, (carrierHeight - normalized.height) / 2);" in visible_to_window_block
    assert "x: Math.round(normalized.left - insetX)" in visible_to_window_block
    assert "y: Math.round(normalized.top - insetY)" in visible_to_window_block
    assert "width: Math.round(carrierWidth)" in visible_to_window_block
    assert "height: Math.round(carrierHeight)" in visible_to_window_block

    schedule_block = _between(
        source,
        "function scheduleElectronCat1PairMoveBounds(bounds, options) {",
        "function isElectronIdleDockCurrent(generation) {",
    )
    assert "var force = !!(options && options.force);" in schedule_block
    assert "var reason = options && typeof options.reason === 'string' && options.reason" in schedule_block
    assert "if (isElectronLinuxRuntime() && !force) return;" in schedule_block
    assert "if (isElectronLinuxRuntime()) return;" not in schedule_block
    assert "electronCat1PairMovePendingForce = electronCat1PairMovePendingForce || force;" in schedule_block
    assert "electronCat1PairMovePendingReason = reason;" in schedule_block
    assert "var pendingForce = electronCat1PairMovePendingForce;" in schedule_block
    assert "electronCat1PairMovePendingBounds = normalizeElectronRect({" in schedule_block
    assert "var pendingReason = electronCat1PairMovePendingReason || 'cat1-pair-move';" in schedule_block
    assert "electronCat1PairMovePendingForce = false;" in schedule_block
    assert "electronCat1PairMovePendingReason = '';" in schedule_block
    assert "reason: pendingReason" in schedule_block


def test_cat1_desktop_pair_move_skips_linux_runtime_native_bounds_sync():
    source = _read(AVATAR_UI_BUTTONS_PATH)

    assert "function _isNekoDesktopLinuxRuntime()" in source
    assert "runtime.isLinux" in source
    assert "runtime.isLinuxX11" in source
    assert "runtime.platform === 'linux'" in source
    assert "_NEKO_IDLE_CAT1_DESKTOP_PAIR_MOVE_SYNC_MIN_MS = 50" in source
    assert "let _nekoIdleDesktopChatPairMoveLastDispatchAt = 0;" in source
    assert "let _nekoIdleDesktopChatPairMoveLastDispatchSignature = '';" in source
    assert "function _getNekoIdleDesktopChatPairMoveSignature(screenRect)" in source

    dispatch_block = _between(
        source,
        "function _dispatchNekoIdleDesktopChatPairMoveBounds(screenRect, options = {}) {",
        "function _getNekoIdleCat1PairMoveChatTarget() {",
    )
    assert "const force = !!(options && options.force);" in dispatch_block
    assert "if (_isNekoDesktopLinuxRuntime() && !force) return false;" in dispatch_block
    assert "_rememberNekoIdleDesktopChatPairMoveRect(screenRect)" in dispatch_block
    assert "if (!force) {" in dispatch_block
    assert "signature === _nekoIdleDesktopChatPairMoveLastDispatchSignature" in dispatch_block
    assert "now - _nekoIdleDesktopChatPairMoveLastDispatchAt < _NEKO_IDLE_CAT1_DESKTOP_PAIR_MOVE_SYNC_MIN_MS" in dispatch_block
    assert "_nekoIdleDesktopChatPairMoveLastDispatchAt = now;" in dispatch_block
    assert "_nekoIdleDesktopChatPairMoveLastDispatchSignature = signature;" in dispatch_block
    assert "timestamp: now" in dispatch_block

    plan_block = _between(
        source,
        "function _getNekoIdleCat1PairMovePlan(button) {",
        "function _easeNekoIdleCat1PairMove(progress) {",
    )
    assert "chatTarget && chatTarget.mode === 'desktop' && _isNekoDesktopLinuxRuntime()" in plan_block

    schedule_guard_block = _between(
        source,
        "function _canScheduleNekoIdleCat1PairMove(button, state) {",
        "function _finishNekoIdleCat1PairMove(button) {",
    )
    assert "chatTarget && chatTarget.mode === 'desktop' && _isNekoDesktopLinuxRuntime()" in schedule_guard_block

    pair_move_block = _between(
        source,
        "function _applyNekoIdleCat1PairMovePlan(plan, progress) {",
        "function _setNekoIdleCat1Substate(button, substate, options = {}) {",
    )
    assert "force: progress >= 1" in pair_move_block


def test_cat1_compact_mirror_uses_stable_native_reserve_rect():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)

    assert "function getIdleCat1CompactMirrorNativeReserveRect(mirrorRect, surfaceRect)" in source
    reserve_block = _between(
        source,
        "function getIdleCat1CompactMirrorNativeReserveRect(mirrorRect, surfaceRect) {",
        "function intersectCompactRects(a, b) {",
    )
    assert "var horizontalPad = Math.ceil(mirror.width / 2);" in reserve_block
    assert "surface.left - horizontalPad" in reserve_block
    assert "surface.right + horizontalPad" in reserve_block
    assert "Math.min(mirror.top, surface.top)" in reserve_block

    collect_block = _between(
        source,
        "function collectCompactSurfaceGeometryItems() {",
        "function getCompactInteractionGeometrySnapshot() {",
    )
    assert "var mirrorNativeRect = getIdleCat1CompactMirrorNativeReserveRect(mirrorRect, shellRect);" in collect_block
    assert "visualRect: mirrorRect" in collect_block
    assert "nativeRect: mirrorNativeRect || mirrorRect" in collect_block


def test_desktop_compact_resize_broadcasts_surface_state_for_cat1_follow():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)

    resize_block = _between(
        source,
        "function applyCompactSurfaceResizeRequest(detail) {",
        "function getCompactSurfaceTarget(layoutOverride) {",
    )
    assert "isElectronChatWindow() && detail && detail.screenRect" in resize_block
    assert "new CustomEvent('neko:compact-surface-layout-change'" in resize_block
    assert "screenRect: detail.screenRect" in resize_block
    assert "resizeActive: phase !== 'end'" in resize_block
    assert "reason: phase === 'end' ? 'resize-end' : 'resize'" in resize_block


def test_cat1_compact_follow_treats_resize_as_active_surface_adjustment():
    source = _read(AVATAR_UI_BUTTONS_PATH)

    move_state_block = _between(
        source,
        "function _handleNekoIdleCompactSurfaceMoveState(detail) {",
        "function _shouldRecheckNekoIdleCat1AfterManualMove(detail) {",
    )
    assert "const resizeActive = !!(detail && detail.resizeActive);" in move_state_block
    assert "const activeSurfaceAdjustment = dragging || resizeActive;" in move_state_block
    assert "_nekoIdleCompactSurfaceDragging = activeSurfaceAdjustment;" in move_state_block
    assert "_interruptNekoIdleCat1PairMovesForRetarget({ scheduleSync: !activeSurfaceAdjustment });" in move_state_block

    follow_block = _between(
        source,
        "function _syncNekoIdleCat1CompactTopEdgeSurfaceFollow(detail) {",
        "function _isNekoIdleCat1Walking(button) {",
    )
    assert "const resizeActive = !!(detail && detail.resizeActive);" in follow_block
    assert "const fastMove = !resizeActive && motion.hasPrevious" in follow_block
    assert "reason: resizeActive ? 'compact-surface-resize' : 'compact-surface-drag'" in follow_block


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


def test_minimize_collapse_deferred_scale_write_is_cancellable():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)
    minimize_block = _between(
        source,
        "if (willMinimize) {",
        "        } else {",
    )

    assert "var collapseScaleFrame = 0;" in minimize_block
    assert "var collapseScaleInnerFrame = 0;" in minimize_block
    assert "function cancelCollapseScaleFrames()" in minimize_block
    assert "window.cancelAnimationFrame(collapseScaleFrame)" in minimize_block
    assert "window.cancelAnimationFrame(collapseScaleInnerFrame)" in minimize_block
    assert minimize_block.count("cancelCollapseScaleFrames();") >= 2
    assert (
        "if (handled || !shell.classList.contains('is-collapsing') || "
        "shell.classList.contains('is-minimized')) return;"
    ) in minimize_block


def test_idle_dock_minimize_fallback_preserves_pending_surface_mode():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)
    fallback_block = _between(
        source,
        "function scheduleIdleDockMinimizeFallback(shell) {",
        "function getElectronIdleDockBridge() {",
    )

    assert "var pendingSurfaceMode = pendingChatSurfaceMode;" in fallback_block
    assert "var pendingSurfaceCommit = pendingMinimizedSurfaceCommit;" in fallback_block
    assert "pendingChatSurfaceMode = pendingSurfaceMode;" in fallback_block
    assert "pendingMinimizedSurfaceCommit = pendingSurfaceCommit;" in fallback_block
    assert "commitPendingMinimizedSurfaceMode();" in fallback_block
    assert "flushPendingChatSurfaceModeIfNeeded();" in fallback_block
    assert (
        "if (!minimized || getCurrentChatSurfaceMode() !== 'minimized') {"
    ) in fallback_block

    flush_index = fallback_block.index("flushPendingChatSurfaceModeIfNeeded();")
    finish_index = fallback_block.index("finishIdleDockMinimize(latestShell);")
    assert flush_index < finish_index


def test_idle_dock_does_not_follow_return_ball_after_initial_dock():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)
    electron_return_block = _between(
        source,
        "function handleElectronIdleReturnBallState(detail) {",
        "    // Enter idle-dock:",
    )

    assert "idleDockContainerObserver" not in source
    assert "refreshIdleDockContainerObserver" not in source
    assert "scheduleIdleDockSync" not in source
    assert "dockTarget = getIdleDockTarget()" not in source
    guard_index = electron_return_block.index("!hasElectronIdleDockPendingOrActive()")
    enter_index = electron_return_block.index("enterElectronIdleDock(detail.screenRect)")
    assert guard_index < enter_index


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


def test_idle_dock_exit_clears_cat2_to_cat1_drag_binding():
    source = _read(APP_REACT_CHAT_WINDOW_PATH)

    assert "function exitIdleDock(options)" in source
    assert "function exitElectronIdleDock(options)" in source
    assert "preserveCurrentPosition" in source
    assert "idleDockActive && detail.source === 'return-ball-drag-demotion'" in source
    assert "detail.reason === 'return-ball-drag-demotion'" in source
    assert "detail.reason === 'return-ball-drag-end'" in source
    assert "detail.reason === 'viewport-resize'" in source
    assert "function shouldIgnoreElectronIdleDockInactiveViewportResize(detail, activeTier)" in source
    assert "if (shouldIgnoreElectronIdleDockInactiveViewportResize(detail, activeTier))" in source
    assert "async function commitElectronIdleDockCollapsedBounds(bridge, bounds, generation)" in source
    assert "result !== false && result !== null && result !== undefined" in source
    assert "await waitElectronIdleDockCommitRetry(80)" in source
    assert "var shouldPreserveCurrentPosition = detail && !!detail.screenRect && (" in source
    assert "activeTier && detail && (" not in source
    assert "preserveScreenRect: shouldPreserveCurrentPosition ? detail.screenRect : null" in source
    assert "await commitElectronIdleDockCollapsedBounds(bridge, preserveBounds, exitGeneration)" in source
    assert "wasActive && saved && !preserveCurrentPosition" in source
    assert "wasActive && triggered && minimized && preserveCurrentPosition" in source
    assert "setChatSurfaceMode(coerceChatSurfaceModeForHost(lastRestorableChatSurfaceMode));" in source
