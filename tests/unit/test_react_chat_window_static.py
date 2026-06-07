from pathlib import Path


APP_REACT_CHAT_WINDOW_PATH = Path(__file__).resolve().parents[2] / "static" / "app-react-chat-window.js"
APP_BUTTONS_PATH = Path(__file__).resolve().parents[2] / "static" / "app-buttons.js"
APP_CHAT_EXPORT_PATH = Path(__file__).resolve().parents[2] / "static" / "app-chat-export.js"
MUSIC_UI_PATH = Path(__file__).resolve().parents[2] / "static" / "music_ui.js"
MUSIC_UI_CSS_PATH = Path(__file__).resolve().parents[2] / "static" / "css" / "music_ui.css"
STATIC_INDEX_CSS_PATH = Path(__file__).resolve().parents[2] / "static" / "css" / "index.css"
STATIC_DARK_MODE_CSS_PATH = Path(__file__).resolve().parents[2] / "static" / "css" / "dark-mode.css"
STATIC_INDEX_JS_PATH = Path(__file__).resolve().parents[2] / "static" / "js" / "index.js"
REACT_CHAT_STYLES_PATH = Path(__file__).resolve().parents[2] / "frontend" / "react-neko-chat" / "src" / "styles.css"
REACT_CHAT_APP_PATH = Path(__file__).resolve().parents[2] / "frontend" / "react-neko-chat" / "src" / "App.tsx"
CHAT_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "chat.html"
SUBTITLE_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "subtitle.html"
PAGES_ROUTER_PATH = Path(__file__).resolve().parents[2] / "main_routers" / "pages_router.py"
COMPACT_EXPORT_HISTORY_PANEL_PATH = (
    Path(__file__).resolve().parents[2] / "frontend" / "react-neko-chat" / "src" / "CompactExportHistoryPanel.tsx"
)


def css_block(styles: str, selector: str, next_selector: str) -> str:
    start = styles.find(selector)
    if start < 0:
        snippet = styles[:240].replace("\n", "\\n")
        raise AssertionError(
            f"css_block could not find selector {selector!r}; "
            f"next_selector={next_selector!r}; styles snippet={snippet!r}"
        )
    content_start = start + len(selector)
    end = styles.find(next_selector, content_start)
    if end < 0:
        snippet = styles[content_start : content_start + 240].replace("\n", "\\n")
        raise AssertionError(
            f"css_block could not find next_selector {next_selector!r} "
            f"after selector {selector!r}; styles snippet={snippet!r}"
        )
    return styles[content_start:end]


def assert_no_layout_transition(block: str) -> None:
    transition_section = block.split("transition:", 1)[1].split(";", 1)[0] if "transition:" in block else ""
    for prop in ("width", "height", "max-height", "min-height", "padding", "margin", "top", "right", "bottom", "left"):
        assert prop not in transition_section


def test_subtitle_window_dark_mode_keeps_transparent_background():
    source = STATIC_DARK_MODE_CSS_PATH.read_text(encoding="utf-8")
    selector = (
        'html[data-theme="dark"].subtitle-window-host,\n'
        'html[data-theme="dark"].subtitle-window-host body.subtitle-window-host {'
    )
    assert selector in source
    block = source.split(selector, 1)[1].split("}", 1)[0]

    assert "background: transparent !important;" in block
    assert source.index(selector) > source.index("background: #000 !important;")


def test_standalone_subtitle_page_initializes_theme_before_subtitle_scripts():
    source = SUBTITLE_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert '<script src="/static/theme-manager.js"></script>' in source
    assert source.index('/static/theme-manager.js') < source.index('/static/subtitle-shared.js')


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
    # Restorable surfaces (compact + the revived legacy full) persist; minimized
    # never does — it restores via lastRestorableChatSurfaceMode.
    assert "if (mode !== 'compact' && mode !== 'full') return;" in persist_block
    assert "localStorage.setItem(CHAT_SURFACE_MODE_STORAGE_KEY, mode)" in persist_block


def test_chat_full_endpoint_uses_chat_template_with_initial_full_surface():
    router_source = PAGES_ROUTER_PATH.read_text(encoding="utf-8")
    template_source = CHAT_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert '@router.get("/chat_full", response_class=HTMLResponse)' in router_source
    assert '"initial_chat_surface_mode": "full"' in router_source
    assert '"initial_chat_surface_mode": "compact"' in router_source
    assert 'data-initial-chat-surface-mode="{{ initial_chat_surface_mode|default(\'compact\', true) }}"' in template_source

    chat_route_block = router_source.split('async def get_chat_page', 1)[1].split(
        '@router.get("/chat_full"',
        1,
    )[0]
    assert '"initial_chat_surface_mode": "compact"' in chat_route_block
    assert '"initial_chat_surface_mode": "full"' not in chat_route_block


def test_chat_templates_version_react_chat_bundle_from_react_assets():
    chat_template = CHAT_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert 'neko-chat-window.css?v={{ react_chat_asset_version }}' in chat_template
    assert 'neko-chat-window.iife.js?v={{ react_chat_asset_version }}' in chat_template


def test_chat_full_is_reserved_from_character_page_config_routing():
    source = STATIC_INDEX_JS_PATH.read_text(encoding="utf-8")

    assert "const RESERVED_PAGE_PATHS = new Set([" in source
    assert "'chat'" in source
    assert "'chat_full'" in source
    assert "RESERVED_PAGE_PATHS.has(pathParts[0])" in source
    assert "isReservedPagePath(window.location.pathname)" in source


def test_chat_host_initial_surface_mode_prefers_template_override_before_storage():
    source = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")

    assert "function readInitialChatSurfaceMode()" in source
    initial_reader_block = source.split("function readInitialChatSurfaceMode()", 1)[1].split(
        "function persistChatSurfaceModePreference",
        1,
    )[0]
    assert "var declaredModeAttr = body" in initial_reader_block
    assert "body.getAttribute('data-initial-chat-surface-mode')" in initial_reader_block
    assert "declaredModeAttr ? normalizeChatSurfaceMode(declaredModeAttr) : ''" in initial_reader_block
    assert "normalizeChatSurfaceMode(body.getAttribute('data-initial-chat-surface-mode'))" not in initial_reader_block
    assert "if (declaredMode === 'compact' || declaredMode === 'full')" in initial_reader_block
    assert "return declaredMode;" in initial_reader_block
    assert initial_reader_block.index("return declaredMode;") < initial_reader_block.index("return readChatSurfaceModePreference();")

    init_block = source.split("function init()", 1)[1].split(
        "function initAfterStorageBarrier()",
        1,
    )[0]
    initial_assignment = "state.chatSurfaceMode = readInitialChatSurfaceMode();"
    storage_read = "readChatSurfaceModePreference()"

    assert initial_assignment in init_block
    assert init_block.index(initial_assignment) < init_block.index("lastRestorableChatSurfaceMode = state.chatSurfaceMode;")
    assert storage_read not in init_block.split(initial_assignment, 1)[0]


def test_close_from_minimized_preserves_compact_surface_mode():
    source = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")

    close_block = source.split("function closeWindow()", 1)[1].split(
        "window.addEventListener('neko:main-ui-hidden-by-model-manager-changed'",
        1,
    )[0]
    minimized_block = close_block.split("if (minimized)", 1)[1].split("overlay.hidden = true;", 1)[0]

    assert "state.chatSurfaceMode = 'full'" not in minimized_block
    assert "minimized = false;" in minimized_block
    # closeWindow clears the minimized shell without routing through
    # setChatSurfaceMode, so it must restore the logical surface to the last
    # restorable mode. Otherwise the next openWindow() rebuilds the React props
    # with chatSurfaceMode:'minimized' and renders a blank body.
    assert (
        "state.chatSurfaceMode = normalizeChatSurfaceMode(lastRestorableChatSurfaceMode);"
        in minimized_block
    )
    assert "syncChatSurfaceModeUI();" in minimized_block
    assert "overlay.hidden = true;" in close_block
    assert "resetCompactChatState();" in close_block


def test_open_from_minimized_restores_surface_mode_before_mounting():
    source = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")

    open_block = source.split("function openWindow()", 1)[1].split(
        "function closeWindow()",
        1,
    )[0]
    # The minimized restore branch must reset the logical surface BEFORE
    # mountWindow() so React rebuilds the compact body instead of the blank
    # minimized surface — symmetric with closeWindow's reset.
    restore_assignment = (
        "state.chatSurfaceMode = normalizeChatSurfaceMode(lastRestorableChatSurfaceMode);"
    )
    assert restore_assignment in open_block
    assert open_block.index(restore_assignment) < open_block.index("if (!mountWindow())")


def test_minimized_restore_uses_previous_real_surface_mode():
    source = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")

    assert "var lastRestorableChatSurfaceMode = 'compact';" in source
    assert "var CHAT_SURFACE_MODE_SEQUENCE = ['compact', 'minimized'];" in source
    assert "var COMPACT_CHAT_STATES = ['default', 'options', 'input'];" in source
    assert "compactChatState: 'default'," in source
    assert "return COMPACT_CHAT_STATES.indexOf(mode) >= 0 ? mode : 'default';" in source
    assert "state.compactChatState = 'default';" in source

    next_mode_block = source.split("function getNextChatSurfaceMode(mode)", 1)[1].split(
        "function resetCompactChatState()",
        1,
    )[0]
    set_mode_block = source.split("function setChatSurfaceMode(nextMode)", 1)[1].split(
        "function cycleChatSurfaceMode()",
        1,
    )[0]
    set_view_props_block = source.split("function setViewProps(nextViewProps)", 1)[1].split(
        "function ensureBundleLoaded()",
        1,
    )[0]
    init_block = source.split("function init()", 1)[1].split(
        "function initAfterStorageBarrier()",
        1,
    )[0]

    assert "if (normalized === 'minimized')" in next_mode_block
    assert "lastRestorableChatSurfaceMode" in next_mode_block
    assert "return normalizeChatSurfaceMode(lastRestorableChatSurfaceMode);" in next_mode_block
    assert "lastRestorableChatSurfaceMode = normalized;" in set_mode_block
    assert "lastRestorableChatSurfaceMode = previousMode;" in set_mode_block
    assert "lastRestorableChatSurfaceMode = normalizedChatSurfaceMode;" in set_view_props_block
    assert "lastRestorableChatSurfaceMode = previousChatSurfaceMode;" in set_view_props_block
    assert "lastRestorableChatSurfaceMode = state.chatSurfaceMode;" in init_block


def test_idle_cat1_compact_mirror_ignores_pet_window_local_events():
    source = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")

    assert "function shouldIgnoreIdleCat1CompactMirrorState(detail)" in source
    ignore_block = source.split("function shouldIgnoreIdleCat1CompactMirrorState(detail)", 1)[1].split(
        "function handleIdleCat1CompactMirrorState(event)",
        1,
    )[0]
    assert "window.__LANLAN_IS_ELECTRON_PET__" in ignore_block
    assert "detail.via === 'local'" in ignore_block
    assert "detail.source === 'pet-window'" in ignore_block

    handler_block = source.split("function handleIdleCat1CompactMirrorState(event)", 1)[1].split(
        "function dispatchCompactSurfaceLayoutChange(rect)",
        1,
    )[0]
    ignore_call = "if (shouldIgnoreIdleCat1CompactMirrorState(detail)) return;"
    inactive_branch = "if (!detail || !detail.active)"
    assert ignore_call in handler_block
    assert handler_block.index(ignore_call) < handler_block.index(inactive_branch)


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


def test_compact_history_size_tokens_are_ratio_based_for_ui_optimization():
    styles = REACT_CHAT_STYLES_PATH.read_text(encoding="utf-8")

    anchor_block = css_block(
        styles,
        ".compact-export-history-anchor {",
        "body.electron-chat-window.subtitle-web-host .compact-export-history-anchor",
    )
    desktop_history_block = styles.split(
        "body.electron-chat-window.subtitle-web-host .compact-export-history-anchor",
        1,
    )[1].split(".compact-export-history-panel", 1)[0]
    bubble_block = css_block(styles, ".compact-export-history-bubble {", ".compact-export-history-message.is-disabled")
    system_bubble_block = css_block(
        styles,
        ".compact-export-history-message.is-system .compact-export-history-bubble {",
        ".compact-export-history-message.is-selected",
    )
    preview_bubble_block = css_block(
        styles,
        ".compact-export-preview-bubble {",
        ".compact-export-preview-message.is-user",
    )
    preview_system_bubble_block = css_block(
        styles,
        ".compact-export-preview-message.is-system .compact-export-preview-bubble {",
        ".compact-export-preview-meta",
    )

    assert "--compact-export-history-width-ratio:" in anchor_block
    assert "--compact-export-surface-width: var(--compact-surface-resize-width, var(--desktop-compact-surface-width, var(--compact-surface-width, 430px)));" in anchor_block
    assert "--compact-export-history-inline-size: min(" in anchor_block
    assert "calc(var(--compact-export-surface-width) * var(--compact-export-history-width-ratio))" in anchor_block
    assert "width: var(--compact-export-history-inline-size);" in anchor_block
    assert "--compact-export-history-max-inline-size: calc(100vw - var(--compact-export-history-viewport-gutter));" in anchor_block
    assert "--compact-export-history-max-inline-size: calc(" in desktop_history_block
    assert "var(--compact-desktop-workarea-width, 1440px) - var(--compact-export-history-viewport-gutter)" in desktop_history_block
    assert "max-width: var(--compact-history-bubble-max-ratio, var(--compact-export-history-bubble-max-ratio));" in bubble_block
    assert "max-width: var(--compact-history-bubble-max-ratio, var(--compact-export-history-system-bubble-max-ratio));" in system_bubble_block
    assert "max-width: var(--compact-export-preview-bubble-max-ratio);" in preview_bubble_block
    assert "max-width: var(--compact-export-preview-system-bubble-max-ratio);" in preview_system_bubble_block


def test_compact_surface_resize_handles_keep_width_in_dom_geometry_contract():
    script = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")
    styles = REACT_CHAT_STYLES_PATH.read_text(encoding="utf-8")
    app_source = REACT_CHAT_APP_PATH.read_text(encoding="utf-8")

    metrics_block = script.split("function getCompactSurfaceMetrics()", 1)[1].split(
        "function clampCompactSurfacePosition(left, top, metrics)",
        1,
    )[0]
    include_block = script.split("function shouldIncludeCompactGeometryElement(element)", 1)[1].split(
        "function getCompactGeometryElementRect(element)",
        1,
    )[0]
    resize_shell_block = css_block(styles, ".compact-chat-surface-shell {", ".compact-chat-surface-frame")
    frame_block = css_block(styles, ".compact-chat-surface-frame {", ".compact-chat-surface-frame[data-compact-chat-state")
    handle_block = css_block(styles, ".compact-chat-resize-handle {", ".compact-chat-resize-handle-left")
    resize_clamp_block = script.split("function clampCompactSurfaceResizeWidthForSide", 1)[1].split(
        "function applyCompactSurfaceResizeRequest",
        1,
    )[0]

    assert "var measuredWidth = rect && rect.width > 0 ? rect.width : 0;" in metrics_block
    assert "var storedWidth = loadCompactSurfaceStoredWidth();" in metrics_block
    assert "getCompactSurfaceResizeMaxWidth()" in metrics_block
    assert "item !== 'resizeHandle'" in include_block
    assert "function applyCompactSurfaceResizeRequest(detail)" in script
    assert "function isDesktopHomeCompactSurfaceRoute()" in script
    assert "if (!isHomeCompactSurfaceRoute() && !isDesktopHomeCompactSurfaceRoute()) return;" in script
    assert "var compactSurfaceDesktopResizeActive = false;" in script
    assert "if (isElectronChatWindow() && detail && detail.screenRect)" in script
    assert "compactSurfaceDesktopResizeActive = phase !== 'end';" in script
    assert "if (compactSurfaceDesktopResizeActive && isElectronChatWindow())" in script
    assert "function handleDesktopCompactLayoutChange(layout)" in script
    assert "if (baseAnchorChanged && !compactSurfaceDesktopResizeActive)" in script
    assert "var compactSurfaceResizeSession = null;" in script
    assert "surfaceScreenRect: surfaceScreenRect" in script
    assert "function getCompactSurfaceDesktopWindowX()" in script
    assert "function getCompactSurfaceDesktopScreenRect()" in script
    assert "function getCompactDesktopWorkAreaEdge(workArea, edge)" in script
    assert "if (edge === 'right' && Number.isFinite(x) && Number.isFinite(width)) return x + width;" in script
    assert ": currentRect.left + windowX" in script
    assert ": currentRect.left + currentRect.width + windowX" in script
    assert "var desktopSurfaceRect = getCompactSurfaceDesktopScreenRect();" in script
    assert "anchorTopScreen: desktopSurfaceRect" in script
    assert "var workAreaLeft = getCompactDesktopWorkAreaEdge(workArea, 'left');" in script
    assert "var workAreaRight = getCompactDesktopWorkAreaEdge(workArea, 'right');" in script
    assert "workArea.left + COMPACT_SURFACE_VIEWPORT_PAD_X" not in resize_clamp_block
    assert "workArea.right - COMPACT_SURFACE_VIEWPORT_PAD_X" not in resize_clamp_block
    assert "if (!Number.isFinite(sideMax) || sideMax <= 0)" in resize_clamp_block
    assert "? compactSurfaceResizeSession.anchorRightScreen - windowX - width" in script
    assert ": compactSurfaceResizeSession.anchorLeftScreen - windowX;" in script
    assert "persist: phase === 'end'" in script
    assert "phase === 'end' && isElectronChatWindow()" in script
    assert "compactSurfaceResizeSession = null;" in script
    assert "neko:compact-surface-resize-request" in script
    assert "neko:compact-surface-layout-change" in script
    assert "neko:compact-surface-resize-width-change" in script
    assert "function isDesktopCompactSurfaceLayoutActive()" in app_source
    assert "document.documentElement.style.removeProperty('--compact-surface-resize-width');" in app_source
    assert "&& !isDesktopCompactSurfaceLayoutActive()" in app_source
    assert "const getClampedCompactSurfaceResizeWidthForSide = useCallback" in app_source
    assert "resizeState.anchorRightScreen - areaLeft" in app_source
    assert "areaRight - resizeState.anchorLeftScreen" in app_source
    assert "if (!isDesktopCompactSurfaceLayoutActive()) {\n      setCompactSurfaceResizeWidth(startWidth);" in app_source
    assert "if (!isDesktopCompactSurfaceLayoutActive()) {\n      setCompactSurfaceResizeWidth(nextWidth);" in app_source
    assert "if (isDesktopCompactSurfaceLayoutActive()) {\n        setCompactSurfaceResizeWidth(null);" in app_source
    assert "applyCompactSurfaceResizeWidthVar(null);\n        return;\n      }\n      const resizeState = compactSurfaceResizeStateRef.current;" in app_source

    assert "--compact-surface-active-width: var(--compact-surface-resize-width, var(--compact-surface-width, 430px));" in resize_shell_block
    assert "width: var(--compact-surface-active-width);" in resize_shell_block
    assert "width: 100%;" in frame_block
    assert "width: 12px;" in handle_block
    assert "cursor: ew-resize;" in handle_block
    assert "pointer-events: auto;" in handle_block
    assert "touch-action: none;" in handle_block
    assert "z-index: 100004;" in handle_block
    assert ".compact-chat-resize-handle::before" not in styles
    assert "left: -4px;" in styles
    assert "right: -4px;" in styles


def test_mobile_web_compact_surface_respects_width_bounds_and_position_vars():
    script = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")
    styles = STATIC_INDEX_CSS_PATH.read_text(encoding="utf-8")
    app_source = REACT_CHAT_APP_PATH.read_text(encoding="utf-8")

    mobile_compact_block = css_block(
        styles,
        'body:not(.electron-chat-window):where(:not(.lanlan-pet-mode)) #react-chat-window-shell[data-chat-surface-mode="compact"]:not(.is-minimized):not(.is-collapsing):not(.is-expanding) {',
        'body:not(.electron-chat-window):where(:not(.lanlan-pet-mode)) #react-chat-window-shell #react-chat-window-root',
    )
    mobile_compact_overflow_block = css_block(
        styles,
        'body:not(.electron-chat-window):where(:not(.lanlan-pet-mode)) #react-chat-window-shell[data-chat-surface-mode="compact"]:not(.is-minimized):not(.is-collapsing):not(.is-expanding) #react-chat-window-root,',
        'body:not(.electron-chat-window):where(:not(.lanlan-pet-mode)) #react-chat-window-shell .app-shell',
    )
    metrics_block = script.split("function getCompactSurfaceMetrics()", 1)[1].split(
        "function clampCompactSurfacePosition(left, top, metrics)",
        1,
    )[0]
    stored_width_block = script.split("function loadCompactSurfaceStoredWidth()", 1)[1].split(
        "function saveCompactSurfacePosition",
        1,
    )[0]
    resize_clamp_block = script.split("function clampCompactSurfaceResizeWidthForSide", 1)[1].split(
        "function applyCompactSurfaceResizeRequest",
        1,
    )[0]
    mobile_width_bounds_block = script.split("function getCompactSurfaceMobileWidthBounds()", 1)[1].split(
        "function getCompactSurfaceResizeMaxWidth()",
        1,
    )[0]

    assert "COMPACT_SURFACE_RESIZE_MOBILE_MIN_WIDTH = 280" in app_source
    assert "COMPACT_SURFACE_RESIZE_MOBILE_VIEWPORT_GUTTER = 16" in app_source
    assert "getCompactSurfaceResizeMinAvailableWidth" in app_source
    assert "getCompactSurfaceResizeViewportGutter" in app_source
    assert "maxAvailableWidth - viewportGutter" in app_source
    assert "window.innerWidth <= 768" in app_source
    assert "lanlan-pet-mode" in app_source
    assert "__LANLAN_IS_ELECTRON_PET__" in app_source
    assert "COMPACT_SURFACE_MOBILE_MIN_WIDTH = 280" in script
    assert "COMPACT_SURFACE_MOBILE_VIEWPORT_GUTTER" in script
    assert "COMPACT_SURFACE_RESIZE_MAX_WIDTH" in mobile_width_bounds_block
    assert "viewportWidth - COMPACT_SURFACE_MOBILE_VIEWPORT_GUTTER" in mobile_width_bounds_block
    assert "isMobileWidth() && storedWidth" in metrics_block
    assert "getCompactSurfaceMobileWidthBounds().minWidth" in stored_width_block
    assert "getCompactSurfaceMobileWidthBounds().minWidth" in resize_clamp_block
    assert "width: var(--compact-surface-width, calc(100vw - 16px)) !important;" in mobile_compact_block
    assert "left: var(--compact-surface-left, 8px) !important;" in mobile_compact_block
    assert "right: auto;" in mobile_compact_block
    assert ".chat-window.chat-surface-mode-compact" in mobile_compact_overflow_block
    assert "overflow: visible;" in mobile_compact_overflow_block


def test_compact_tool_fan_uses_shell_local_anchor_not_fixed_viewport_position():
    styles = REACT_CHAT_STYLES_PATH.read_text(encoding="utf-8")
    script = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")
    app_source = REACT_CHAT_APP_PATH.read_text(encoding="utf-8")

    fan_block = css_block(styles, ".compact-input-tool-fan {", ".compact-chat-surface-shell *")
    compact_input_block = css_block(
        styles,
        '.compact-chat-surface-frame[data-compact-chat-state="input"] .composer-input {',
        '.compact-chat-surface-frame[data-compact-chat-state="input"] .composer-input::placeholder',
    )
    compact_mobile_block = css_block(
        styles,
        "@media (max-width: 820px) {",
        "/* ===================================================================",
    )
    wheel_block = styles.split(".compact-input-tool-fan .compact-input-tool-item {", 1)[1].split(
        ".compact-input-tool-fan .composer-tool-btn img",
        1,
    )[0]
    collector_block = script.split("function collectCompactToolFanGeometryItems(element)", 1)[1].split(
        "function collectCompactCompositeGeometryItems(element, kind)",
        1,
    )[0]
    native_hit_block = collector_block.split("nativeRects.forEach", 1)[1].split("return items.concat", 1)[0]
    geometry_sync_block = app_source.split(
        "window.dispatchEvent(new CustomEvent('neko:compact-interaction-geometry-change'));",
        1,
    )[1].split(
        "const openCompactInputToolFan",
        1,
    )[0]

    assert "position: absolute;" in fan_block
    assert "--compact-tool-wheel-hover-radius: 116px;" in fan_block
    assert "--compact-tool-wheel-orbit-radius: 80px;" in fan_block
    assert "--compact-tool-fan-focus-x: var(--compact-tool-wheel-hover-radius);" in fan_block
    assert "--compact-tool-fan-focus-y: var(--compact-tool-wheel-hover-radius);" in fan_block
    assert "--compact-tool-wheel-center-x: var(--compact-tool-wheel-hover-radius);" in fan_block
    assert "--compact-tool-wheel-center-y: var(--compact-tool-wheel-hover-radius);" in fan_block
    assert "--compact-tool-wheel-transform-duration: 0.22s;" in fan_block
    assert "--compact-tool-wheel-transform-easing: cubic-bezier(0.2, 0.9, 0.22, 1.12);" in fan_block
    assert "--compact-tool-wheel-charge-first-angle: 0deg;" in fan_block
    assert "--compact-tool-wheel-charge-second-angle: 0deg;" in fan_block
    assert "--compact-tool-toggle-center-x: calc(100% - 31px);" in fan_block
    assert "--compact-tool-toggle-center-y: 31px;" in fan_block
    assert "left: calc(var(--compact-tool-toggle-center-x) - var(--compact-tool-fan-focus-x));" in fan_block
    assert "top: calc(var(--compact-tool-toggle-center-y) - var(--compact-tool-fan-focus-y));" in fan_block
    assert "width: calc(var(--compact-tool-wheel-hover-radius) * 2);" in fan_block
    assert "height: calc(var(--compact-tool-wheel-hover-radius) * 2);" in fan_block
    assert "--compact-tool-wheel-orbit-radius: 80px;" in fan_block
    assert "touch-action: none;" in fan_block
    assert "position: fixed;" not in fan_block
    assert "--compact-input-tool-fan-origin-left" not in fan_block
    assert ".compact-input-tool-fan-hit-region" in styles
    assert ".compact-input-tool-wheel-charge" in styles
    assert "width: calc(var(--compact-tool-wheel-hover-radius) * 2);" in styles
    assert '.compact-input-tool-fan[data-compact-input-tool-fan-open="true"] .compact-input-tool-fan-hit-region' in styles
    assert '.compact-input-tool-fan[data-compact-tool-wheel-charge-active="true"] .compact-input-tool-wheel-charge' in styles
    assert "conic-gradient(" in styles
    assert "--compact-tool-wheel-charge-first-angle" in styles
    assert "--compact-tool-wheel-charge-second-angle" in styles
    assert ".compact-input-tool-wheel-charge::after" in styles
    assert '.compact-input-tool-fan[data-compact-tool-wheel-charge-direction="backward"] .compact-input-tool-wheel-charge' in styles
    assert "calc(360deg - var(--compact-tool-wheel-charge-first-angle))" in styles
    assert "calc(360deg - var(--compact-tool-wheel-charge-second-angle))" in styles
    assert '.compact-input-tool-fan[data-compact-input-tool-fan-open="false"]' in styles
    assert "visibility: hidden;" in styles
    assert "pointer-events: none !important;" in styles
    assert '.compact-input-tool-fan[data-compact-input-tool-fan-open="true"]' in styles
    assert "visibility: visible;" in styles
    assert (
        '.compact-chat-surface-frame[data-compact-tool-toggle-visible="true"] '
        '.compact-input-tool-toggle:hover'
    ) in styles
    assert "width: auto;" in compact_input_block
    assert "min-width: 0;" in compact_input_block
    assert "padding: 10px 8px 10px 0;" in compact_input_block
    assert 'padding: 5px 62px 5px 2px;' in styles
    assert '.compact-chat-surface-frame[data-compact-chat-state="input"]' in compact_mobile_block
    assert 'padding: 5px 62px 5px 18px;' in compact_mobile_block
    assert 'padding: 5px 8px 5px 18px;' not in compact_mobile_block
    assert '.compact-chat-surface-frame[data-compact-tool-toggle-visible="true"]:not([data-compact-chat-state="input"])' in styles
    assert 'padding-right: 62px;' in styles
    assert 'right: 9px;' in styles
    assert "transform: none;" in styles
    assert '.compact-input-tool-item[data-compact-tool-wheel-slot="hidden"]' in styles
    assert '.compact-input-tool-item[data-compact-tool-wheel-slot="hidden-forward"]' in styles
    assert '.compact-input-tool-item[data-compact-tool-wheel-slot="hidden-backward"]' in styles
    assert "rotate(107.35deg) translateX(var(--compact-tool-wheel-orbit-radius)) rotate(-107.35deg)" in styles
    assert "rotate(-17.35deg) translateX(var(--compact-tool-wheel-orbit-radius)) rotate(17.35deg)" in styles
    assert "rotate(-48.51deg) translateX(var(--compact-tool-wheel-orbit-radius)) rotate(48.51deg)" in styles
    assert "rotate(138.51deg) translateX(var(--compact-tool-wheel-orbit-radius)) rotate(-138.51deg)" in styles
    assert "translateX(83.82px)" not in wheel_block
    assert "translateX(89.74px)" not in wheel_block
    assert "translateX(92.06px)" not in wheel_block
    assert "scale(0.56)" in wheel_block
    assert "scale(0.86)" in wheel_block
    assert "scale(0.98)" in wheel_block
    assert "scale(1.04)" in wheel_block
    assert "bubbleFloat 3.2s ease-in-out infinite" in styles
    assert '.compact-input-tool-fan[data-compact-tool-wheel-fast-animation="true"]' in styles
    assert "--compact-tool-wheel-transform-duration: 0.07s;" in styles
    assert "pointer-events: none;" in styles
    assert "activeCursorToolId" in geometry_sync_block
    assert "toolMenuOpen" in geometry_sync_block
    assert "var COMPACT_TOOL_AVATAR_CHOICE_FLOAT_PADDING_X = 6;" in script
    assert "var COMPACT_TOOL_AVATAR_CHOICE_FLOAT_PADDING_Y = 12;" in script
    assert "function buildCompactAvatarToolChoiceHitRect(rect)" in script
    assert ".composer-icon-popover .composer-icon-button" in collector_block
    assert "toolFan:avatarToolChoice:" in collector_block
    assert "var hitRect = isAvatarToolChoice ? buildCompactAvatarToolChoiceHitRect(rect) : rect;" in collector_block
    assert "nativeRect: hitRect" in collector_block
    assert "slot.indexOf('hidden') === 0" in collector_block
    assert "style.pointerEvents !== 'none'" not in collector_block
    assert "hitRect: nativeRect" in native_hit_block
    assert "interactive: true" in native_hit_block
    assert "hitRect: null" not in native_hit_block
    assert "var COMPACT_TOOL_FAN_CIRCLE_SLICE_COUNT = 18;" in script
    assert "function buildCompactToolFanCircleSliceRects(rect, element)" in script
    assert "readCompactToolFanPixelVar(style, '--compact-tool-wheel-center-x', 116)" in script
    assert "readCompactToolFanPixelVar(style, '--compact-tool-wheel-hover-radius', 116)" in script
    assert "Math.sqrt(Math.max(0" in script
    assert "id: index === 0 ? 'toolFan:native' : 'toolFan:native:' + index" in script


def test_compact_choice_hit_contract_uses_real_options_only():
    styles = REACT_CHAT_STYLES_PATH.read_text(encoding="utf-8")
    host_styles = STATIC_INDEX_CSS_PATH.read_text(encoding="utf-8")
    script = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")

    choice_selector = 'body > .compact-chat-choice-anchor[data-chat-surface-mode="compact"] {'
    slot_selector = (
        'body > .compact-chat-choice-anchor[data-chat-surface-mode="compact"] .composer-galgame-slot,\n'
        'body > .compact-chat-choice-anchor[data-chat-surface-mode="compact"] .composer-galgame-options,\n'
        'body > .compact-chat-choice-anchor[data-chat-surface-mode="compact"] .composer-galgame-option {'
    )
    choice_block = css_block(styles, choice_selector, 'body > .compact-chat-choice-anchor[data-chat-surface-mode="compact"]::-webkit-scrollbar')
    host_choice_block = css_block(host_styles, choice_selector, 'body > .compact-chat-choice-anchor[data-chat-surface-mode="compact"]::-webkit-scrollbar')
    slot_block = css_block(styles, slot_selector, 'body > .compact-chat-choice-anchor[data-chat-surface-mode="compact"][data-choice-layer-open="false"]')
    host_slot_block = css_block(host_styles, slot_selector, 'body > .compact-chat-choice-anchor[data-chat-surface-mode="compact"][data-choice-layer-open="false"]')
    geometry_block = script.split("function getCompactGeometryElementRect(element)", 1)[1].split(
        "function getCompactHistoryScrollbarRect(element, parentRect)",
        1,
    )[0]

    assert "pointer-events: none;" not in choice_block
    assert "pointer-events: none;" not in host_choice_block
    assert "pointer-events: auto;" in slot_block
    assert "pointer-events: auto;" in host_slot_block
    assert "The compact choice portal itself is transparent" in styles
    assert "The compact choice portal itself is transparent" in host_styles
    assert "if (item === 'choice')" in geometry_block
    assert "querySelectorAll('.composer-galgame-option')" in geometry_block
    assert "var ownRect = normalizeCompactDomRect(element.getBoundingClientRect());" in geometry_block
    assert geometry_block.index("if (item === 'choice')") < geometry_block.index("var ownRect = normalizeCompactDomRect")


def test_desktop_compact_choice_placement_uses_surface_anchor_without_frame_polling():
    app_source = REACT_CHAT_APP_PATH.read_text(encoding="utf-8")

    placement_effect = app_source.split("if (!compactChoiceLayerOpen) return;", 1)[1].split(
        "const requestCompactChatState = useCallback",
        1,
    )[0]

    assert "const shellNode = compactInputShellRef.current;" in placement_effect
    assert "const nextShellNode = compactInputShellRef.current;" in placement_effect
    assert "appShellRef.current" not in placement_effect
    assert "window.addEventListener('neko:desktop-compact-layout-change', schedulePlacementUpdate);" in placement_effect
    assert "requestAnimationFrame(trackPlacement)" not in placement_effect
    assert "const trackPlacement = () =>" not in placement_effect


def test_desktop_compact_history_hit_regions_are_clipped_to_visible_parent():
    script = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")

    assert "function intersectCompactRects(a, b)" in script

    composite_block = script.split("function collectCompactCompositeGeometryItems(element, kind)", 1)[1].split(
        "function collectCompactSurfaceGeometryItems()",
        1,
    )[0]

    assert "var clippedRect = kind === 'musicPlayer'" in composite_block
    assert "? rect" in composite_block
    assert ": (parentRect ? intersectCompactRects(rect, parentRect) : rect);" in composite_block
    assert "if (!clippedRect) return null;" in composite_block
    assert "visualRect: clippedRect" in composite_block
    assert "hitRect: clippedRect" in composite_block
    assert "nativeRect: kind === 'history' ? null : clippedRect" in composite_block
    assert "id: 'history:scrollbar'" in composite_block
    assert "nativeRect: null" in composite_block


def test_compact_geometry_snapshot_separates_base_surface_from_extra_islands():
    script = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")

    assert "function isCompactSurfaceBaseAnchorKind(kind)" in script
    assert "return kind === 'surfaceShell' || kind === 'capsule' || kind === 'input';" in script
    assert "function getCompactSurfaceGeometryRole(kind)" in script
    assert "if (kind === 'dragHandle') return 'baseHit';" not in script
    assert "return 'extraIsland';" in script
    assert "item.geometryRole = getCompactSurfaceGeometryRole(item.kind);" in script

    snapshot_block = script.split("function getCompactInteractionGeometrySnapshot()", 1)[1].split(
        "function syncCompactInteractionGeometry()",
        1,
    )[0]

    assert "var baseSurfaceItems = surfaceItems.filter(function (item) {" in snapshot_block
    assert "return item && item.geometryRole === 'baseAnchor';" in snapshot_block
    assert "var extraIslandItems = surfaceItems.filter(function (item) {" in snapshot_block
    assert "return item && item.geometryRole === 'extraIsland';" in snapshot_block
    assert "baseSurfaceItems: baseSurfaceItems" in snapshot_block
    assert "baseSurfaceRect: unionCompactRects(baseSurfaceRects)" in snapshot_block
    assert "baseSurfaceNativeRects:" in snapshot_block
    assert "baseSurfaceHitRects:" in snapshot_block
    assert "extraIslandItems: extraIslandItems" in snapshot_block
    assert "extraIslandNativeRects:" in snapshot_block
    assert "extraIslandHitRects:" in snapshot_block


def test_compact_surface_drag_uses_declared_surface_and_no_drag_exclusions():
    script = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")

    assert "function isCompactDragSurfaceTarget(target)" in script
    assert "target.closest('[data-compact-no-drag=\"true\"]')" in script
    assert "target.closest('[data-compact-drag-surface=\"true\"]')" in script
    assert "function isCompactDragHandleTarget(target)" not in script
    assert "target.closest('[data-compact-drag-handle=\"true\"]')" not in script
    assert "if (dragState.compactSurface && !dragState.moved) return;" in script

    drag_block = script.split("document.addEventListener('mousedown', function (event)", 1)[1].split(
        "document.addEventListener('touchstart', function (event)",
        1,
    )[0]
    assert "if (!isCompactDragSurfaceTarget(event.target)) return;" in drag_block
    assert "compactSurface: true" in drag_block

    touch_block = script.split("document.addEventListener('touchstart', function (event)", 1)[1].split(
        "document.addEventListener('mousemove', function (event)",
        1,
    )[0]
    assert "if (!isCompactDragSurfaceTarget(event.target)) return;" in touch_block
    assert "compactSurface: true" in touch_block
    assert "compact capsule must still synthesize click" in touch_block
    assert "event.preventDefault();" not in touch_block
    assert "event.stopPropagation();" not in touch_block
    assert "}, { capture: true, passive: true });" in touch_block


def test_moved_drag_suppresses_trailing_release_click():
    # 移动过的拖拽（含 compact surface 本体拖拽）松开后，浏览器会在 mouseup 落点
    # 补发一次 click；落点若是胶囊按钮会被误判为点击而展开输入框。守卫在 moved
    # 时 arm，并在 document capture 阶段吞掉紧随的那一次 click。
    script = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")

    assert "var suppressDragReleaseClick = false;" in script

    stop_block = script.split("function stopDrag(options)", 1)[1].split(
        "function bindDragging()",
        1,
    )[0]
    assert "if (wasMoved) {" in stop_block
    assert "armDragReleaseClickGuard();" in stop_block

    arm_block = script.split("function armDragReleaseClickGuard()", 1)[1].split(
        "function consumeDragReleaseClickGuard",
        1,
    )[0]
    assert "suppressDragReleaseClick = true;" in arm_block
    # setTimeout(…, 0) 兜底清旗：click 同任务先消费，无 click 时也不残留误吞后续点击。
    assert "window.setTimeout(function () {" in arm_block
    assert "suppressDragReleaseClick = false;" in arm_block

    consume_block = script.split("function consumeDragReleaseClickGuard(event)", 1)[1].split(
        "function getOverlay()",
        1,
    )[0]
    assert "if (!suppressDragReleaseClick) return;" in consume_block
    assert "suppressDragReleaseClick = false;" in consume_block
    assert "event.preventDefault();" in consume_block
    assert "event.stopPropagation();" in consume_block

    # capture 阶段挂载，且排在 mobile 展开守卫之后（保留既有行为优先级）。
    assert "document.addEventListener('click', consumeDragReleaseClickGuard, true);" in script
    listeners_block = script.split(
        "document.addEventListener('click', blockMobileExpandSyntheticPointerEvent, true);",
        1,
    )[1]
    assert "document.addEventListener('click', consumeDragReleaseClickGuard, true);" in listeners_block


def test_desktop_compact_layout_change_resets_anchor_only_when_base_surface_changes():
    script = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")

    assert "var compactDesktopSurfaceAnchorSnapshot = '';" in script
    assert "function serializeCompactSurfaceRectSnapshot(rect)" in script
    assert "function getCompactDesktopLayoutAnchorSnapshot(layout)" in script
    assert "return 'version:' + Math.round(anchorVersion);" in script
    assert "var screenSnapshot = serializeCompactSurfaceRectSnapshot(layout.surfaceScreenRect);" in script
    assert "if (screenSnapshot) return 'screen:' + screenSnapshot;" in script

    handler_block = script.split("function handleDesktopCompactLayoutChange(layout)", 1)[1].split(
        "function normalizeCompactDesktopWorkArea(raw)",
        1,
    )[0]
    listener_block = script.split("window.addEventListener('neko:desktop-compact-layout-change'", 1)[1].split(
        "window.addEventListener('neko:desktop-avatar-bounds-change'",
        1,
    )[0]

    assert "var nextAnchorSnapshot = getCompactDesktopLayoutAnchorSnapshot(layout);" in handler_block
    assert "nextAnchorSnapshot !== compactDesktopSurfaceAnchorSnapshot" in handler_block
    assert "compactDesktopSurfaceAnchorSnapshot = nextAnchorSnapshot;" in handler_block
    assert "if (baseAnchorChanged && !compactSurfaceDesktopResizeActive)" in handler_block
    assert "compactSurfaceAnchorLocked = false;" in handler_block
    assert "compactSurfaceAnchorSnapshot = '';" in handler_block
    assert "scheduleCompactMinimizeBallTracking();" in handler_block
    assert "var layout = event && event.detail ? event.detail : window.__nekoDesktopCompactLayout;" in listener_block
    assert "handleDesktopCompactLayoutChange(layout || null);" in listener_block
    assert "compactSurfaceAnchorSnapshot = '';" not in listener_block


def test_electron_compact_chat_retires_full_surface_chrome():
    template = CHAT_TEMPLATE_PATH.read_text(encoding="utf-8")

    # The standalone /chat window renders the shared compact capsule (driven by
    # static/css/index.css's `body.subtitle-web-host #react-chat-window-shell
    # [data-chat-surface-mode="compact"]` rule set, which chat.html's
    # subtitle-web-host body opts into) instead of a bespoke full-window glass
    # panel. The retired full form used to paint an empty glass panel for one
    # frame before React mounted and collapsed it to compact ("先 full 再
    # compact" 的那一帧). chat.html must NOT re-introduce that full surface.
    #
    # Retired full-surface artifacts that must stay gone:
    assert "@keyframes liquidFlow" not in template            # 玻璃流光关键帧
    assert "@keyframes lightSweep" not in template
    assert "-webkit-mask-image" not in template               # 全窗口 root 四边渐隐 mask
    assert "#react-chat-window-shell:not(.is-minimized)::before" not in template
    assert "#react-chat-window-shell:not(.is-minimized)::after" not in template
    assert "inset: 40px 25px 25px 20px" not in template       # 全窗口 shell 几何

    # The shell font tweak survives (compact still uses #react-chat-window-root).
    assert "#react-chat-window-root {" in template
    # The overlay must start hidden so nothing paints before React mounts the
    # compact surface — parity with templates/index.html, which is what kills the
    # pre-mount full-form flash.
    assert 'id="react-chat-window-overlay" hidden' in template


def test_compact_history_resize_bar_is_draggable_and_persisted():
    styles = REACT_CHAT_STYLES_PATH.read_text(encoding="utf-8")
    panel_source = COMPACT_EXPORT_HISTORY_PANEL_PATH.read_text(encoding="utf-8")
    app_source = REACT_CHAT_APP_PATH.read_text(encoding="utf-8")

    # 顶部 resize bar 的样式：可纵向拖、可接收 pointer、平时透明、不触发原生窗口拖动。
    bar_block = css_block(
        styles,
        ".compact-export-history-resize-bar {",
        ".compact-export-history-resize-bar::after",
    )
    assert "cursor: ns-resize;" in bar_block
    assert "pointer-events: auto;" in bar_block
    assert "-webkit-app-region: no-drag;" in bar_block
    # bar 本体不能透明：宿主几何收集器按 opacity<=0.01 丢弃 hit-region，透明会让 Electron 下鼠标穿透、点不到。
    assert "opacity: 0;" not in bar_block
    # 只让视觉抓手（伪元素）平时透明、hover/拖动显现。
    after_block = css_block(
        styles,
        ".compact-export-history-resize-bar::after {",
        ".compact-export-history-anchor:hover .compact-export-history-resize-bar::after",
    )
    assert "opacity: 0;" in after_block
    assert ".compact-export-history-resize-bar.is-active::after {" in styles
    # hover 整个历史区即浮现顶部高度 bar（回滚 fd138cd 的「调尺寸热区联动浮现」收窄，
    # 让整个历史记录气泡区域都能唤出 resize bar）。
    assert ".compact-export-history-anchor:hover .compact-export-history-resize-bar::after" in styles

    # bar 的 DOM：带可命中且不穿透的 hit-region，拖拽不触发面板 / 整窗拖动。
    assert "className={clsx('compact-export-history-resize-bar'" in panel_source
    # hit-region 受 historyInteractive && !choiceLayerAbove 双重 gate：choice prompt 压在历史上方时，
    # bar 不再上报命中区（与 scroll/controls 一起惰性，Electron 下不留可拖 / 不穿透的活条）。
    assert "data-compact-hit-region-id={historyInteractive && !choiceLayerAbove ? 'history:resize' : undefined}" in panel_source
    assert "data-compact-hit-region-kind={historyInteractive && !choiceLayerAbove ? 'resize' : undefined}" in panel_source
    resize_bar_jsx = panel_source.split("compact-export-history-resize-bar", 1)[1].split("/>", 1)[0]
    assert 'data-compact-no-drag="true"' in resize_bar_jsx
    # under-choice 态把 bar 一并纳入 pointer-events:none / 淡化规则。
    assert ".compact-export-history-anchor.under-choice-prompt .compact-export-history-resize-bar," in styles
    # 纯点击不落库：finish 仅在 moved 时 persist。
    assert "if (resizeState.moved) {" in app_source

    # 高度上限写预留覆盖变量 + 独立 localStorage key；变更后触发宿主几何重算（Electron 窗口联动）。
    assert "COMPACT_HISTORY_HEIGHT_STORAGE_KEY = 'neko.reactChatWindow.compactHistorySlotHeight'" in app_source
    assert "'--compact-history-slot-height'" in app_source
    assert "new CustomEvent('neko:compact-interaction-geometry-refresh')" in app_source


def test_compact_history_controls_collapse_gives_height_back_to_history_scroll():
    styles = REACT_CHAT_STYLES_PATH.read_text(encoding="utf-8")

    anchor_block = css_block(
        styles,
        ".compact-export-history-anchor {",
        "body.electron-chat-window.subtitle-web-host .compact-export-history-anchor",
    )
    collapsed_anchor_block = css_block(
        styles,
        ".compact-export-history-anchor.controls-collapsed {",
        "body.electron-chat-window.subtitle-web-host .compact-export-history-anchor",
    )
    scroll_block = css_block(styles, ".compact-export-history-scroll {", ".compact-export-history-scroll-content")
    controls_block = css_block(
        styles,
        ".compact-export-history-controls {",
        ".compact-export-history-controls-content",
    )
    history_handle_block = css_block(
        styles,
        ".compact-history-visibility-handle {",
        ".compact-history-visibility-handle::before",
    )
    control_button_block = css_block(styles, ".compact-export-history-control {", ".compact-export-history-control:hover")

    assert "Geometry-critical" in anchor_block
    assert "--compact-export-controls-padding-y:" in anchor_block
    assert "--compact-export-controls-action-height:" in anchor_block
    assert "--compact-export-controls-collapsed-toggle-height:" in anchor_block
    assert "--compact-export-controls-expanded-block-size: calc(" in anchor_block
    assert "--compact-export-controls-collapsed-block-size: calc(" in anchor_block
    assert "--compact-export-controls-collapse-delta: 0px;" in anchor_block
    assert "--compact-export-controls-collapse-delta: var(--compact-export-controls-expanded-block-size);" in collapsed_anchor_block
    assert "24px" not in collapsed_anchor_block
    assert (
        "height: calc(var(--compact-export-history-region-height) + "
        "var(--compact-export-controls-collapse-delta));"
    ) in scroll_block
    assert (
        "max-height: calc(var(--compact-export-history-region-height) + "
        "var(--compact-export-controls-collapse-delta));"
    ) in scroll_block
    assert "height: 40px;" not in controls_block
    assert "min-height: 40px;" not in controls_block
    assert "padding: var(--compact-export-controls-padding-y) 6px;" in controls_block
    assert "padding 0.16s ease" not in controls_block
    assert ".compact-export-history-controls.is-collapsed" not in styles
    assert ".compact-export-history-controls-toggle" not in styles
    assert "position: fixed;" in history_handle_block
    assert "--compact-history-handle-line-width: clamp(38px, calc(var(--compact-history-handle-surface-width) * 0.102), 50px);" in history_handle_block
    assert ".compact-history-visibility-handle.is-open {" in history_handle_block
    assert "--compact-history-handle-line-width: 100%;" in history_handle_block
    assert "top: calc(var(--desktop-compact-surface-top, var(--compact-surface-top, 68vh)) + 8px);" in history_handle_block
    assert "bottom: auto;" in history_handle_block
    assert "bottom: calc(100vh - var(--desktop-compact-surface-top" not in history_handle_block
    assert "transform: translate(-50%, -50%);" in history_handle_block
    assert "z-index: 100002;" in history_handle_block
    assert "pointer-events: auto;" in history_handle_block
    assert "-webkit-app-region: no-drag;" in history_handle_block
    assert ".compact-history-visibility-handle-triangle {\n  display: none;\n}" in styles
    assert "height: var(--compact-export-controls-action-height);" in control_button_block


def test_compact_history_layout_contract_avoids_jitter_feedback():
    styles = REACT_CHAT_STYLES_PATH.read_text(encoding="utf-8")

    anchor_block = css_block(
        styles,
        ".compact-export-history-anchor {",
        "body.electron-chat-window.subtitle-web-host .compact-export-history-anchor",
    )
    panel_block = css_block(styles, ".compact-export-history-panel {", ".compact-export-history-anchor.under-choice-prompt")
    scroll_block = css_block(styles, ".compact-export-history-scroll {", ".compact-export-history-scroll-content")
    controls_block = css_block(
        styles,
        ".compact-export-history-controls {",
        ".compact-export-history-controls-content",
    )
    preview_block = css_block(
        styles.split(".compact-export-preview-region[hidden]", 1)[1],
        ".compact-export-preview-region {",
        ".compact-export-preview-header",
    )

    assert "transition:" not in anchor_block
    assert "transition:" not in panel_block
    assert "transition:" not in scroll_block
    assert_no_layout_transition(controls_block)
    assert "transition:" not in preview_block

    assert "height: calc(var(--compact-export-history-region-height)" in scroll_block
    assert "max-height: calc(var(--compact-export-history-region-height)" in scroll_block
    assert "max-height: inherit;" in panel_block


def test_compact_history_reduced_motion_closing_hides_immediately():
    styles = REACT_CHAT_STYLES_PATH.read_text(encoding="utf-8")

    reduced_motion_block = styles.rsplit("@media (prefers-reduced-motion: reduce)", 1)[1]
    closing_block = css_block(
        reduced_motion_block,
        '.compact-export-history-anchor[data-compact-export-history-visibility="closing"] {',
        ".avatar-cursor-overlay-stage",
    )

    assert "opacity: 0 !important;" in closing_block
    assert "visibility: hidden !important;" in closing_block


def test_compact_history_closing_bubbles_disable_pointer_events():
    styles = REACT_CHAT_STYLES_PATH.read_text(encoding="utf-8")

    closing_bubble_block = css_block(
        styles,
        '.compact-export-history-anchor[data-compact-export-history-visibility="closing"] .compact-export-history-bubble {',
        ".compact-export-history-message.is-disabled",
    )

    assert "animation: compact-history-message-exit" in closing_bubble_block
    assert "pointer-events: none;" in closing_bubble_block


def test_compact_history_bubbles_are_text_selectable_and_drag_free():
    """拖到 avatar 子系统已删除：气泡不再捕获指针，文本可被鼠标选中复制。

    断言气泡 CSS 显式声明 user-select:text / cursor:text（覆盖 [role=\"button\"]
    的 user-select:none），且气泡 JSX 不再挂任何指针拖拽 handler（只留 onClick /
    onKeyDown 驱动导出勾选）。"""
    styles = REACT_CHAT_STYLES_PATH.read_text(encoding="utf-8")
    panel_source = COMPACT_EXPORT_HISTORY_PANEL_PATH.read_text(encoding="utf-8")

    bubble_block = css_block(
        styles,
        ".compact-export-history-bubble {",
        ".compact-export-history-anchor[data-compact-export-history-visibility=\"open\"]",
    )
    assert "user-select: text;" in bubble_block
    assert "-webkit-user-select: text;" in bubble_block
    assert "cursor: text;" in bubble_block
    assert "cursor: pointer;" not in bubble_block

    # 拖拽 CSS 全部移除。
    assert "compact-history-drag-layer" not in styles
    assert "data-compact-history-drag" not in styles

    # 气泡 JSX 只保留 onClick / onKeyDown，不再有拖拽指针 handler。
    bubble_jsx = panel_source.split('className="compact-export-history-bubble"', 1)[1].split(
        "compact-export-history-check",
        1,
    )[0]
    assert "onClick={(event) => handleClick(event, message, selectable)}" in bubble_jsx
    assert "onKeyDown={(event) => handleKeyDown(event, message, selectable)}" in bubble_jsx
    assert "onPointerDown" not in bubble_jsx
    assert "onPointerMove" not in bubble_jsx
    assert "onPointerUp" not in bubble_jsx
    assert "handlePointerDown" not in panel_source
    assert "startCompactHistoryDrag" not in panel_source


def test_compact_history_hit_contract_keeps_transparent_wrappers_out_of_hit_regions():
    styles = REACT_CHAT_STYLES_PATH.read_text(encoding="utf-8")
    music_ui_styles = MUSIC_UI_CSS_PATH.read_text(encoding="utf-8")
    panel_source = COMPACT_EXPORT_HISTORY_PANEL_PATH.read_text(encoding="utf-8")
    app_source = REACT_CHAT_APP_PATH.read_text(encoding="utf-8")
    script = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")
    music_ui_source = MUSIC_UI_PATH.read_text(encoding="utf-8")

    anchor_block = css_block(
        styles,
        ".compact-export-history-anchor {",
        "body.electron-chat-window.subtitle-web-host .compact-export-history-anchor",
    )
    panel_block = css_block(styles, ".compact-export-history-panel {", ".compact-export-history-anchor.under-choice-prompt")
    scroll_block = css_block(styles, ".compact-export-history-scroll {", ".compact-export-history-scroll-content")
    scrollbar_hit_block = css_block(styles, ".compact-export-history-scrollbar-hit {", ".compact-export-history-message")
    content_block = css_block(
        styles,
        ".compact-export-history-scroll-content {",
        ".compact-export-history-scroll-content > .compact-export-history-message:first-child",
    )
    message_block = css_block(styles, ".compact-export-history-message {", ".compact-export-history-message.is-user")
    bubble_block = css_block(styles, ".compact-export-history-bubble {", ".compact-export-history-message.is-disabled")
    shared_hit_block = css_block(
        styles,
        ".compact-export-history-controls,\n.compact-export-preview-region {",
        ".compact-export-history-controls {",
    )
    scroll_jsx_block = panel_source.split('className="compact-export-history-scroll"', 1)[1].split(
        'className="compact-export-history-scroll-content"',
        1,
    )[0]
    message_hit_block = panel_source.split('className="compact-export-history-bubble"', 1)[1].split(
        'compact-export-history-check',
        1,
    )[0]
    controls_hit_block = panel_source.split('className="compact-export-history-controls"', 1)[1].split(
        'compact-export-history-controls-content',
        1,
    )[0]
    assert "pointer-events: none;" in anchor_block
    assert "pointer-events: none;" in panel_block
    assert "overflow-y: auto;" in scroll_block
    assert "pointer-events: none;" in scroll_block
    assert "position: absolute;" in scrollbar_hit_block
    assert "width: 20px;" in scrollbar_hit_block
    assert "pointer-events: auto;" in scrollbar_hit_block
    assert "pointer-events: none;" in content_block
    assert "pointer-events: none;" in message_block
    assert "pointer-events: auto;" in bubble_block
    assert ".compact-export-history-controls,\n.compact-export-preview-region {" in styles
    assert "pointer-events: auto;" in shared_hit_block
    assert "function getCompactHistoryScrollbarRect(element, parentRect)" in script
    assert "id: 'history:scrollbar'" in script
    assert "scrollNode.getAttribute('data-compact-scrollbar-visible') !== 'true'" in script
    assert "element.querySelector('.compact-export-history-scrollbar-hit')" in script
    scrollbar_block = script.split("function getCompactHistoryScrollbarRect(element, parentRect)", 1)[1].split(
        "var COMPACT_TOOL_FAN_CIRCLE_SLICE_COUNT",
        1,
    )[0]
    assert "if (hitStyle && hitStyle.pointerEvents === 'none') return null;" in scrollbar_block
    assert "style.pointerEvents === 'none'" not in scrollbar_block
    assert "data-compact-hit-region" not in scroll_jsx_block
    assert 'className="compact-export-history-scrollbar-hit"' in panel_source
    assert 'data-compact-scrollbar-hit="true"' in panel_source
    assert "hasPointerCapture?.(event.pointerId)" in panel_source
    assert 'data-compact-hit-region-id={historyInteractive ? `history:message:${message.id}` : undefined}' in panel_source
    assert "data-compact-hit-region={historyInteractive ? 'true' : undefined}" in message_hit_block
    assert "data-compact-hit-region-kind={historyInteractive ? 'message' : undefined}" in message_hit_block
    assert "data-compact-hit-region-id={historyInteractive ? 'history:controls' : undefined}" in panel_source
    assert "data-compact-hit-region={historyInteractive ? 'true' : undefined}" in controls_hit_block
    assert "data-compact-hit-region-kind={historyInteractive ? 'controls' : undefined}" in controls_hit_block
    assert "compact-export-history-music-mount" not in panel_source
    assert 'className="compact-music-player-mount"' in app_source
    assert 'data-music-player-mount="compact-surface"' in app_source
    assert 'data-compact-music-player-visibility={compactMusicPlayerVisibility}' in app_source
    assert "aria-hidden={compactMusicPlayerVisibility === 'open' ? undefined : true}" in app_source
    assert 'data-compact-geometry-item="musicPlayer"' in app_source
    assert 'data-compact-geometry-hit-scope="children"' in app_source
    assert "function getPreferredMusicMountTarget()" in music_ui_source
    assert "function getCompactMusicMountTarget()" in music_ui_source
    assert "document.querySelector('[data-music-player-mount=\"compact-surface\"]')" in music_ui_source
    assert "document.getElementById('music-player-mount')" in music_ui_source
    assert "document.getElementById(MUSIC_CONFIG.dom.containerId)" in music_ui_source
    assert "mutation.type === 'attributes' && isMusicMountMutationTarget(mutation.target)" in music_ui_source
    assert "function isCompactMusicGeometryMutationTarget(node)" in music_ui_source
    assert "node.closest('[data-music-player-mount=\"compact-surface\"]')" in music_ui_source
    assert "node.classList.contains('music-bar-volume-container')" in music_ui_source
    assert "node.classList.contains('music-bar-volume-slider-wrapper')" in music_ui_source
    assert "mutation.type === 'attributes' && isCompactMusicGeometryMutationTarget(mutation.target)" in music_ui_source
    assert "attributes: true" in music_ui_source
    assert "'data-music-player-mount'" in music_ui_source
    assert "mountMusicBar(musicBar)" in music_ui_source
    assert "musicBar.setAttribute('data-compact-hit-region-id', 'music-player')" in music_ui_source
    assert "neko:compact-interaction-geometry-refresh" in music_ui_source
    volume_toggle_segments = music_ui_source.split("volumeContainer.classList.toggle('expanded');")
    assert len(volume_toggle_segments) == 3
    for segment in volume_toggle_segments[1:]:
        assert "requestCompactMusicGeometrySync();" in segment[:96]
    volume_close_segments = music_ui_source.split("volumeContainer.classList.remove('expanded');")
    assert len(volume_close_segments) == 3
    for segment in volume_close_segments[1:]:
        assert "requestCompactMusicGeometrySync();" in segment[:96]
    assert "window.addEventListener('neko:compact-interaction-geometry-refresh'" in script
    assert "kind === 'musicPlayer'" in script
    assert music_ui_source.count('data-compact-hit-region-id="music-player:volume"') == 2
    assert music_ui_source.count('data-compact-hit-region-kind="music-volume"') == 2
    assert "#music-player-mount.compact-music-player-mount {" in styles
    assert "#music-player-mount.compact-music-player-mount {" in music_ui_styles
    assert '#music-player-mount.compact-music-player-mount[data-compact-music-player-visibility="closing"]' in styles
    assert '#music-player-mount.compact-music-player-mount[data-compact-music-player-visibility="closed"]' in styles
    assert "visibility: hidden;" in css_block(
        styles,
        '#music-player-mount.compact-music-player-mount[data-compact-music-player-visibility="closed"] {',
        '#music-player-mount.compact-music-player-mount[data-compact-music-player-visibility="closing"] *',
    )
    assert "width: min(calc(var(--compact-music-player-surface-width) - 16px), 360px, calc(100vw - 24px));" in styles
    assert "width: min(calc(var(--compact-music-player-surface-width) - 16px), 360px, calc(100vw - 24px));" in music_ui_styles
    assert "#music-player-mount.compact-music-player-mount > .music-player-bar" in styles
    assert "#music-player-mount.compact-music-player-mount > .music-player-bar" in music_ui_styles
    assert "max-inline-size: 100%;" in styles
    assert "max-inline-size: 100%;" in music_ui_styles
    assert "#music-player-mount.compact-music-player-mount .music-bar-info" in styles
    assert "#music-player-mount.compact-music-player-mount .music-bar-info" in music_ui_styles
    assert "--compact-music-player-reserved-block-size: 88px;" in styles
    assert "--compact-music-player-history-gap: 14px;" in styles
    assert "+ var(--compact-music-player-reserved-block-size, 88px)" in styles
    assert "+ var(--compact-music-player-history-gap, 14px)" in styles
    assert ".app-shell:has(> .compact-music-player-mount:not(:empty)) .compact-export-history-anchor" in styles
    # 音量弹层展开时滑块向上伸进 history 区域，播放器整体提到 history(100001) 之上，
    # 但仍低于工具轮盘态 surface-shell(100003)，避免抢点击又不破坏工具轮盘对表情按钮的让位。
    assert "z-index: 100002;" in css_block(
        styles,
        "#music-player-mount.compact-music-player-mount:has(.music-bar-volume-container.expanded) {",
        "#music-player-mount.compact-music-player-mount:empty",
    )
    assert 'data-compact-hit-region-id="history:preview"' in panel_source


def test_subtitle_web_host_keeps_compact_history_transparent_wrappers_click_through():
    styles = STATIC_INDEX_CSS_PATH.read_text(encoding="utf-8")

    compact_surface_prefix = (
        'body.subtitle-web-host #react-chat-window-shell[data-chat-surface-mode="compact"]:not(.is-minimized):'
        'not(.is-collapsing):not(.is-expanding)'
    )
    broad_surface_rule = (
        f'{compact_surface_prefix} [data-compact-geometry-owner="surface"],\n'
        f'{compact_surface_prefix} [data-compact-geometry-owner="surface"] *,\n'
        f'{compact_surface_prefix} #reactChatWindowMinimizeButton,\n'
        f'{compact_surface_prefix} #reactChatWindowMinimizeButton *,\n'
        f'{compact_surface_prefix} .compact-input-tool-fan[data-compact-input-tool-fan-open="true"],\n'
        f'{compact_surface_prefix} .compact-input-tool-fan[data-compact-input-tool-fan-open="true"] * {{\n'
        "    pointer-events: auto;\n"
        "}"
    )
    compact_music_interactive_rule = (
        f'{compact_surface_prefix} #music-player-mount,\n'
        f'{compact_surface_prefix} #music-player-mount * {{\n'
        "    pointer-events: auto;\n"
        "}"
    )
    compact_music_hidden_rule = (
        f'{compact_surface_prefix} #music-player-mount.compact-music-player-mount[data-compact-music-player-visibility="closing"],\n'
        f'{compact_surface_prefix} #music-player-mount.compact-music-player-mount[data-compact-music-player-visibility="closing"] *,\n'
        f'{compact_surface_prefix} #music-player-mount.compact-music-player-mount[data-compact-music-player-visibility="closed"],\n'
        f'{compact_surface_prefix} #music-player-mount.compact-music-player-mount[data-compact-music-player-visibility="closed"] * {{\n'
        "    pointer-events: none;\n"
        "}"
    )
    history_passthrough_rule = (
        f'{compact_surface_prefix} .compact-export-history-anchor,\n'
        f'{compact_surface_prefix} .compact-export-history-panel,\n'
        f'{compact_surface_prefix} .compact-export-history-scroll,\n'
        f'{compact_surface_prefix} .compact-export-history-scroll-content,\n'
        f'{compact_surface_prefix} .compact-export-history-message {{\n'
        "    pointer-events: none;\n"
        "}"
    )
    history_interactive_rule = (
        f'{compact_surface_prefix} .compact-export-history-bubble,\n'
        f'{compact_surface_prefix} .compact-export-history-controls,\n'
        f'{compact_surface_prefix} .compact-export-preview-region {{\n'
        "    pointer-events: auto;\n"
        "}"
    )

    assert broad_surface_rule in styles
    assert compact_music_interactive_rule in styles
    assert compact_music_hidden_rule in styles
    assert history_passthrough_rule in styles
    assert history_interactive_rule in styles
    assert styles.index(broad_surface_rule) < styles.index(compact_music_interactive_rule)
    assert styles.index(compact_music_interactive_rule) < styles.index(compact_music_hidden_rule)
    assert styles.index(compact_music_hidden_rule) < styles.index(history_passthrough_rule)
    assert styles.index(history_passthrough_rule) < styles.index(history_interactive_rule)
    assert ".compact-export-history-scroll,\n" in history_passthrough_rule


def test_compact_inline_export_uses_windowless_app_chat_export_api():
    script = APP_CHAT_EXPORT_PATH.read_text(encoding="utf-8")

    translate_block = script.split("function translateText(key, fallback, params)", 1)[1].split(
        "function translateLabel(key, fallback)",
        1,
    )[0]

    assert "typeof translated === 'string' || typeof translated === 'number'" in translate_block
    assert "return String(translated);" in translate_block

    assert "getCompactInlineOptions: getCompactInlineExportOptions" in script
    assert "buildCompactInlinePreview: buildCompactInlinePreview" in script
    assert "copyCompactInlineSelection: copyCompactInlineSelection" in script
    assert "downloadCompactInlineSelection: downloadCompactInlineSelection" in script

    compact_api_block = script.split("// ======================== Compact inline API ========================", 1)[1].split(
        "// ======================== Action handlers ========================",
        1,
    )[0]

    assert "state.allMessages = getReactMessages();" in compact_api_block
    assert "state.selectedIds = selectedIds;" in compact_api_block
    assert "selectedIds.size >= MAX_EXPORT_SELECTION" in compact_api_block
    assert "normalizeExportFormatId(opts.format)" in compact_api_block
    assert "normalizeImageExportStyleId(opts.imageStyle)" in compact_api_block
    assert "normalizeImageExportFormatId(opts.imageFormat)" in compact_api_block
    assert "function buildCompactInlinePreview(options)" in compact_api_block
    assert "buildExportDocument(entries, state.exportFormat)" in compact_api_block
    assert "URL.createObjectURL(exportData.previewBlob)" in compact_api_block
    assert "buildMarkdownPreviewDocument(exportData.content)" in compact_api_block
    assert "getOrBuildPreviewPayload" not in compact_api_block
    assert "clearPreviewCache()" not in compact_api_block
    assert "function runCompactInlineExportAction(options, action)" in compact_api_block
    assert "state.exportFormat = previous.exportFormat;" in compact_api_block
    assert "buildExportDocument(entries, 'image')" in compact_api_block
    assert "copyImageToClipboard(imgBlob)" in compact_api_block
    assert "buildExportDocument(entries, 'markdown')" in compact_api_block
    assert "copyTextToClipboard(mdData.content)" in compact_api_block
    assert "downloadExportFile(data.fileName, data.content, data.contentType, window)" in compact_api_block
    assert "handleCopyClick" not in compact_api_block
    assert "handleDownloadClick" not in compact_api_block
    assert "openExportPreviewWindow" not in compact_api_block
    assert "window.open" not in compact_api_block


def test_compact_history_drop_payload_suppresses_real_send_in_voice_mode_only_at_host_send_boundary():
    script = APP_BUTTONS_PATH.read_text(encoding="utf-8")
    host_script = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")

    assert "function shouldSuppressCompactHistoryDropSendForVoiceMode()" in script
    assert "window.shouldKeepVoiceComposerHidden()" in script
    assert "S.isRecording || S.voiceChatActive || S.voiceStartPending" in script
    assert "prepareCompactHistoryDropSubmit: prepareCompactHistoryDropSubmit" in host_script

    drop_block = script.split("async function sendCompactHistoryDropPayloadNow(payload)", 1)[1].split(
        "window.sendCompactHistoryDropPayload = mod.sendCompactHistoryDropPayload",
        1,
    )[0]
    guard = "if (shouldSuppressCompactHistoryDropSendForVoiceMode()) {\n            return true;\n        }"
    assert guard in drop_block
    assert drop_block.index(guard) < drop_block.index("var normalizedImages = [];")
    assert drop_block.index(guard) < drop_block.index("mod.sendTextPayload(text,")
    assert "compactHistoryDropPayloadQueue = Promise.resolve();" in script
    assert "sendCompactHistoryDropPayloadNow(payload)" in script
    assert "prepareCompactHistoryDropSubmit" in drop_block


def test_chat_image_file_drop_uses_import_pipeline_and_blocks_browser_navigation():
    script = APP_BUTTONS_PATH.read_text(encoding="utf-8")

    assert "function getImageFilesFromFileList(fileList)" in script
    assert "return file instanceof File && (file.type === '' || isLikelyImageFile(file));" in script
    assert "!file.type ||" not in script
    assert "return /^files$/i.test(String(type || ''));" in script
    assert "mod.importImageFilesToPendingList = function importImageFilesToPendingList(files, options)" in script
    assert "return Promise.resolve({ succeeded: 0, failed: inputFiles.length });" in script
    assert "window.t('app.importImagePartial', { success: succeeded, failed: failed })" in script

    change_anchor = "input.addEventListener('change', function (event) {"
    change_block = script.split(change_anchor, 1)[1].split(
        "mod._importImageInput = input;",
        1,
    )[0]
    assert script.index("function getImageFilesFromFileList(fileList)") < script.index(change_anchor)
    assert script.index(change_anchor) < script.index(
        "mod.importImageFilesToPendingList = function importImageFilesToPendingList(files, options)"
    )
    assert "mod.importImageFilesToPendingList(files, { logPrefix: '[导入图片]' })" in change_block

    drag_block = script.split("document.addEventListener('dragover'", 1)[1].split(
        "document.addEventListener('drop'",
        1,
    )[0]
    drop_block = script.split("document.addEventListener('drop'", 1)[1].split(
        "mod.ensureImportImageInput();",
        1,
    )[0]

    assert "shouldHandleChatFileDrop(e)" in drag_block
    assert "e.preventDefault();" in drag_block
    assert "e.stopPropagation();" in drag_block
    assert "e.dataTransfer.dropEffect = isHomeTutorialInteractionLocked() ? 'none' : 'copy';" in drag_block

    assert "shouldHandleChatFileDrop(e)" in drop_block
    assert "e.preventDefault();" in drop_block
    assert "e.stopPropagation();" in drop_block
    assert "showHomeTutorialLockedToast();" in drop_block
    assert "mod.importImageFilesToPendingList(files, { logPrefix: '[拖放图片]' });" in drop_block
