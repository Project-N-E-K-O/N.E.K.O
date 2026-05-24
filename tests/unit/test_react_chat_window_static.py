from pathlib import Path


APP_REACT_CHAT_WINDOW_PATH = Path(__file__).resolve().parents[2] / "static" / "app-react-chat-window.js"
APP_CHAT_EXPORT_PATH = Path(__file__).resolve().parents[2] / "static" / "app-chat-export.js"
REACT_CHAT_STYLES_PATH = Path(__file__).resolve().parents[2] / "frontend" / "react-neko-chat" / "src" / "styles.css"
CHAT_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "chat.html"
COMPACT_EXPORT_HISTORY_PANEL_PATH = (
    Path(__file__).resolve().parents[2] / "frontend" / "react-neko-chat" / "src" / "CompactExportHistoryPanel.tsx"
)


def css_block(styles: str, selector: str, next_selector: str) -> str:
    return styles.split(selector, 1)[1].split(next_selector, 1)[0]


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
    assert "max-width: var(--compact-export-history-bubble-max-ratio);" in bubble_block
    assert "max-width: var(--compact-export-history-system-bubble-max-ratio);" in system_bubble_block
    assert "max-width: var(--compact-export-preview-bubble-max-ratio);" in preview_bubble_block
    assert "max-width: var(--compact-export-preview-system-bubble-max-ratio);" in preview_system_bubble_block


def test_compact_surface_resize_handles_keep_width_in_dom_geometry_contract():
    script = APP_REACT_CHAT_WINDOW_PATH.read_text(encoding="utf-8")
    styles = REACT_CHAT_STYLES_PATH.read_text(encoding="utf-8")

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

    fan_block = css_block(styles, ".compact-input-tool-fan {", ".compact-chat-surface-shell *")

    assert "position: absolute;" in fan_block
    assert "left: calc(100% - 30px);" in fan_block
    assert "top: 31px;" in fan_block
    assert "position: fixed;" not in fan_block
    assert "--compact-input-tool-fan-origin-left" not in fan_block


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
    assert "nativeRect: clippedRect" in composite_block


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
    assert "pointer-events: none;" in scroll_block
    assert "pointer-events: none;" in content_block
    assert "pointer-events: none;" in message_block
    assert "pointer-events: auto;" in bubble_block
    assert ".compact-export-history-controls,\n.compact-export-preview-region {" in styles
    assert "pointer-events: auto;" in shared_hit_block
    assert "data-compact-hit-region" not in scroll_jsx_block
    assert 'data-compact-hit-region-id={`history:message:${message.id}`}' in panel_source
    assert 'data-compact-hit-region-id="history:controls"' in panel_source
    assert 'data-compact-hit-region-id="history:preview"' in panel_source


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
