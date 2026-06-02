from pathlib import Path


APP_REACT_CHAT_WINDOW_PATH = Path(__file__).resolve().parents[2] / "static" / "app-react-chat-window.js"
APP_BUTTONS_PATH = Path(__file__).resolve().parents[2] / "static" / "app-buttons.js"
APP_CHAT_EXPORT_PATH = Path(__file__).resolve().parents[2] / "static" / "app-chat-export.js"
STATIC_INDEX_CSS_PATH = Path(__file__).resolve().parents[2] / "static" / "css" / "index.css"
REACT_CHAT_STYLES_PATH = Path(__file__).resolve().parents[2] / "frontend" / "react-neko-chat" / "src" / "styles.css"
REACT_CHAT_APP_PATH = Path(__file__).resolve().parents[2] / "frontend" / "react-neko-chat" / "src" / "App.tsx"
CHAT_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "chat.html"
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
    assert "if (mode !== 'compact') return;" in persist_block
    assert "localStorage.setItem(CHAT_SURFACE_MODE_STORAGE_KEY, mode)" in persist_block


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


def test_compact_tool_fan_uses_shell_local_anchor_not_fixed_viewport_position():
    styles = REACT_CHAT_STYLES_PATH.read_text(encoding="utf-8")
    script = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")

    fan_block = css_block(styles, ".compact-input-tool-fan {", ".compact-chat-surface-shell *")
    wheel_block = styles.split(".compact-input-tool-fan .compact-input-tool-item {", 1)[1].split(
        ".compact-input-tool-fan .composer-tool-btn img",
        1,
    )[0]
    collector_block = script.split("function collectCompactToolFanGeometryItems(element)", 1)[1].split(
        "function collectCompactCompositeGeometryItems(element, kind)",
        1,
    )[0]
    native_hit_block = collector_block.split("nativeRects.forEach", 1)[1].split("return items.concat", 1)[0]

    assert "position: absolute;" in fan_block
    assert "--compact-tool-wheel-hover-radius: 116px;" in fan_block
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
    assert 'padding: 5px 62px 5px 20px;' in styles
    assert '.compact-chat-surface-frame[data-compact-tool-toggle-visible="true"]:not([data-compact-chat-state="input"])' in styles
    assert 'padding-right: 62px;' in styles
    assert 'right: 9px;' in styles
    assert "transform: none;" in styles
    assert '.compact-input-tool-item[data-compact-tool-wheel-slot="hidden"]' in styles
    assert '.compact-input-tool-item[data-compact-tool-wheel-slot="hidden-forward"]' in styles
    assert '.compact-input-tool-item[data-compact-tool-wheel-slot="hidden-backward"]' in styles
    assert "rotate(107.35deg) translateX(91.92px) rotate(-107.35deg)" in styles
    assert "rotate(-17.35deg) translateX(91.92px) rotate(17.35deg)" in styles
    assert "rotate(-48.51deg) translateX(91.92px) rotate(48.51deg)" in styles
    assert "rotate(138.51deg) translateX(91.92px) rotate(-138.51deg)" in styles
    assert "translateX(83.82px)" not in wheel_block
    assert "translateX(89.74px)" not in wheel_block
    assert "translateX(92.06px)" not in wheel_block
    assert "scale(0.56)" in wheel_block
    assert "scale(0.86)" in wheel_block
    assert "scale(0.98)" in wheel_block
    assert "scale(1.04)" in wheel_block
    assert '.compact-input-tool-fan[data-compact-tool-wheel-fast-animation="true"]' in styles
    assert "--compact-tool-wheel-transform-duration: 0.07s;" in styles
    assert "pointer-events: none;" in styles
    assert ".composer-icon-popover .composer-icon-button" in collector_block
    assert "toolFan:avatarToolChoice:" in collector_block
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

    assert "var clippedRect = parentRect ? intersectCompactRects(rect, parentRect) : rect;" in composite_block
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
    assert "if (kind === 'dragHandle') return 'baseHit';" in script
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


def test_electron_compact_chat_root_mask_does_not_clip_history_layer():
    template = CHAT_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert "#react-chat-window-root {" in template
    assert (
        'body.electron-chat-window.subtitle-web-host '
        '#react-chat-window-shell[data-chat-surface-mode="compact"] #react-chat-window-root'
    ) in template

    compact_override_block = template.split(
        'body.electron-chat-window.subtitle-web-host '
        '#react-chat-window-shell[data-chat-surface-mode="compact"] #react-chat-window-root',
        1,
    )[1].split("@keyframes liquidFlow", 1)[0]

    assert "-webkit-mask-image: none !important;" in compact_override_block
    assert "mask-image: none !important;" in compact_override_block


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
        ".compact-export-history-controls.is-collapsed",
    )
    collapsed_block = css_block(
        styles,
        ".compact-export-history-controls.is-collapsed {",
        ".compact-export-history-controls-content",
    )
    collapsed_toggle_block = css_block(
        styles,
        ".compact-export-history-controls.is-collapsed .compact-export-history-controls-toggle {",
        ".compact-export-history-controls.is-collapsed .compact-export-history-controls-toggle::before",
    )
    control_button_block = css_block(styles, ".compact-export-history-control {", ".compact-export-history-control:hover")

    assert "Geometry-critical" in anchor_block
    assert "--compact-export-controls-padding-y:" in anchor_block
    assert "--compact-export-controls-action-height:" in anchor_block
    assert "--compact-export-controls-collapsed-toggle-height:" in anchor_block
    assert "--compact-export-controls-expanded-block-size: calc(" in anchor_block
    assert "--compact-export-controls-collapsed-block-size: calc(" in anchor_block
    assert "--compact-export-controls-collapse-delta: 0px;" in anchor_block
    assert (
        "--compact-export-controls-collapse-delta: calc(\n"
        "    var(--compact-export-controls-expanded-block-size) - "
        "var(--compact-export-controls-collapsed-block-size)\n"
        "  );"
    ) in collapsed_anchor_block
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
    assert "padding: 0;" in collapsed_block
    assert "width: var(--compact-export-controls-collapsed-width);" in collapsed_block
    assert "height: var(--compact-export-controls-collapsed-toggle-height);" in collapsed_toggle_block
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
        ".compact-export-history-controls.is-collapsed",
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


def test_compact_history_hit_contract_keeps_transparent_wrappers_out_of_hit_regions():
    styles = REACT_CHAT_STYLES_PATH.read_text(encoding="utf-8")
    panel_source = COMPACT_EXPORT_HISTORY_PANEL_PATH.read_text(encoding="utf-8")
    script = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")

    anchor_block = css_block(
        styles,
        ".compact-export-history-anchor {",
        "body.electron-chat-window.subtitle-web-host .compact-export-history-anchor",
    )
    panel_block = css_block(styles, ".compact-export-history-panel {", ".compact-export-history-anchor.under-choice-prompt")
    scroll_block = css_block(styles, ".compact-export-history-scroll {", ".compact-export-history-scroll-content")
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

    assert "pointer-events: none;" in anchor_block
    assert "pointer-events: none;" in panel_block
    assert "overflow-y: auto;" in scroll_block
    assert "pointer-events: none;" in scroll_block
    assert "pointer-events: none;" in content_block
    assert "pointer-events: none;" in message_block
    assert "pointer-events: auto;" in bubble_block
    assert ".compact-export-history-controls,\n.compact-export-preview-region {" in styles
    assert "pointer-events: auto;" in shared_hit_block
    assert "function getCompactHistoryScrollbarRect(element, parentRect)" in script
    assert "id: 'history:scrollbar'" in script
    assert "data-compact-hit-region" not in scroll_jsx_block
    assert 'data-compact-hit-region-id={`history:message:${message.id}`}' in panel_source
    assert 'data-compact-hit-region-id="history:controls"' in panel_source
    assert 'data-compact-hit-region-id="history:preview"' in panel_source


def test_subtitle_web_host_keeps_compact_history_transparent_wrappers_click_through():
    styles = STATIC_INDEX_CSS_PATH.read_text(encoding="utf-8")

    broad_surface_selector = (
        'body.subtitle-web-host #react-chat-window-shell[data-chat-surface-mode="compact"]:not(.is-minimized):'
        'not(.is-collapsing):not(.is-expanding) [data-compact-geometry-owner="surface"] *'
    )
    history_anchor_selector = (
        'body.subtitle-web-host #react-chat-window-shell[data-chat-surface-mode="compact"]:not(.is-minimized):'
        'not(.is-collapsing):not(.is-expanding) .compact-export-history-anchor'
    )
    history_bubble_selector = (
        'body.subtitle-web-host #react-chat-window-shell[data-chat-surface-mode="compact"]:not(.is-minimized):'
        'not(.is-collapsing):not(.is-expanding) .compact-export-history-bubble'
    )

    assert broad_surface_selector in styles
    assert history_anchor_selector in styles
    assert history_bubble_selector in styles
    assert styles.index(broad_surface_selector) < styles.index(history_anchor_selector)

    passthrough_block = styles.split(history_anchor_selector, 1)[1].split(history_bubble_selector, 1)[0]
    interactive_block = styles.split(history_bubble_selector, 1)[1].split(
        'body.subtitle-web-host #react-chat-window-shell[data-chat-surface-mode="compact"]:not(.is-minimized):not(.is-collapsing):not(.is-expanding) #reactChatWindowMinimizeButton',
        1,
    )[0]

    for selector in (
        ".compact-export-history-panel",
        ".compact-export-history-scroll",
        ".compact-export-history-scroll-content",
        ".compact-export-history-message",
    ):
        assert selector in passthrough_block
    assert "pointer-events: none;" in passthrough_block
    assert ".compact-export-history-controls" in interactive_block
    assert ".compact-export-preview-region" in interactive_block
    assert "pointer-events: auto;" in interactive_block


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
