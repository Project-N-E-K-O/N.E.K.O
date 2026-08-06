from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHAT_EXPORT_JS = PROJECT_ROOT / "static" / "app" / "app-chat-export.js"
STATIC_INDEX_CSS = PROJECT_ROOT / "static" / "css" / "index.css"
REACT_CHAT_CSS = PROJECT_ROOT / "frontend" / "react-neko-chat" / "src" / "styles.css"


def css_rule(styles: str, selector: str) -> str:
    start = styles.index(f"{selector} {{")
    return styles[start : styles.index("}", start)]


def test_export_preview_waits_for_shell_before_rewriting_document():
    script = CHAT_EXPORT_JS.read_text(encoding="utf-8")

    assert "function waitForExportPreviewShell(previewWindow, targetUrl, timeoutMs)" in script
    assert "function waitForExportPreviewRewriteGate(previewWindow, targetUrl)" in script
    assert "function hasExportPreviewWindowControlApi(previewWindow)" in script
    assert "function isExportPreviewShellReady(previewWindow, targetUrl)" in script
    assert "href === 'about:blank'" in script
    assert "previewWindow.addEventListener('load', checkReady)" in script
    assert "waitForExportPreviewShell(previewWindow, targetUrl, 6500)" in script
    assert "shellReady || hasExportPreviewWindowControlApi(previewWindow)" in script

    gate_index = script.index("await waitForExportPreviewRewriteGate(previewWindow, getExportPreviewShellUrl());")
    guard_index = script.index("if (!canRewritePreview) {", gate_index)
    stop_index = script.index("if (typeof previewWindow.stop === 'function') previewWindow.stop();", gate_index)
    doc_open_index = script.index("var doc = previewWindow.document;", gate_index)
    assert gate_index < guard_index < stop_index < doc_open_index


def test_neko_export_group_time_uses_single_send_time():
    script = CHAT_EXPORT_JS.read_text(encoding="utf-8")

    function_start = script.index("function getGroupTime(group)")
    function_end = script.index("function fitMetaText", function_start)
    get_group_time = script[function_start:function_end]

    assert "return times[0];" in get_group_time
    assert "times[0] + ' - ' + times[times.length - 1]" not in get_group_time


def test_export_preview_reuses_only_shell_window_handles():
    script = CHAT_EXPORT_JS.read_text(encoding="utf-8")

    assert "function isReusableExportPreviewWindow(win)" in script
    assert "function isExportPreviewDocumentWindow(win)" in script
    assert "win.__nekoChatExportPreviewWindow === true" in script
    assert "classList.contains('chat-export-window')" in script
    assert "isExportPreviewShellUrl(getWindowHref(win)) || isExportPreviewDocumentWindow(win)" in script

    function_start = script.index("async function openExportPreviewWindow()")
    function_end = script.index("async function openPreviewModal", function_start)
    open_export = script[function_start:function_end]

    assert "var existingPreviewWindow = isReusableExportPreviewWindow(state.previewWindow)" in open_export
    assert "state.previewWindow = null;" in open_export
    assert "function isCurrentChatWindowHandle(win)" in script
    assert "win.document === document" in script
    assert "window.open('', getExportPreviewWindowName('main'), buildExportWindowFeatures())" in open_export
    assert "if (isCurrentChatWindowHandle(previewWindow))" in open_export
    assert "var returnedHref = getWindowHref(previewWindow);" in open_export
    assert "returnedHref !== 'about:blank' && !isExportPreviewShellUrl(returnedHref)" in open_export
    assert "var openedShellWindow = isExportPreviewShellUrl(returnedHref);" in open_export
    assert "previewWindow.__nekoChatExportPreviewWindow = true;" in open_export


def test_export_success_waits_for_a_real_save_result():
    script = CHAT_EXPORT_JS.read_text(encoding="utf-8")

    assert "function prepareExportSave(suggestion, preferredHostWindow)" in script
    assert "function deliverExportFile(preparedSavePromise, data)" in script
    assert "function waitForDesktopDownloadResult(hostWindow, fileName, startDownload)" in script
    assert "hostWindow.addEventListener('neko:file-download-result', handleResult)" in script
    assert "await writable.close();" in script
    assert "return { status: 'saved' };" in script
    assert "return { status: 'cancelled' };" in script
    assert "return { status: 'started' };" in script

    compact_start = script.index("async function downloadCompactInlineSelection(options)")
    compact_end = script.index("// ======================== Action handlers", compact_start)
    compact_download = script[compact_start:compact_end]
    assert compact_download.index("var preparedSave = prepareExportSave(") < compact_download.index(
        "await buildExportDocument(entries, state.exportFormat, exportDate)"
    )
    assert "showPreviewSaveResult(result);" not in compact_download
    assert "showToast('chat.exportSuccess'" not in compact_download
    assert "showToast('chat.exportCancelled'" not in compact_download
    assert "showToast('chat.exportInProgress'" not in compact_download
    assert "showToast('chat.exportActionFailed'" not in compact_download
    assert "showToastMessage(" not in compact_download

    click_start = script.index("async function handleDownloadClick()")
    click_end = script.index("async function handleCopyClick()", click_start)
    click_handler = script[click_start:click_end]
    assert click_handler.index("var preparedSave = prepareExportSave(") < click_handler.index(
        "await getOrBuildPreviewPayload(entries, state.exportFormat)"
    )
    assert "showPreviewSaveResult(result);" in click_handler
    assert "showToast('chat.exportSuccess'" not in click_handler
    assert "showToast('chat.exportCancelled'" not in click_handler
    assert "showToast('chat.exportInProgress'" not in click_handler
    assert "showToast('chat.exportActionFailed'" not in click_handler
    assert "showToastMessage(" not in click_handler

    result_reporter = script.split("function showPreviewSaveResult(result)", 1)[1].split(
        "function logExportError", 1
    )[0]
    assert "showToast(" not in result_reporter
    assert "showToastMessage(" not in result_reporter


def test_export_preview_feedback_does_not_participate_in_layout():
    static_styles = STATIC_INDEX_CSS.read_text(encoding="utf-8")
    react_styles = REACT_CHAT_CSS.read_text(encoding="utf-8")

    static_footer = css_rule(static_styles, ".chat-export-preview-footer")
    static_feedback = css_rule(static_styles, ".chat-export-preview-download-status")
    compact_region = css_rule(
        react_styles.split(".compact-export-preview-region[hidden]", 1)[1],
        ".compact-export-preview-region",
    )
    compact_feedback = css_rule(react_styles, ".compact-export-preview-feedback")

    assert "position: relative;" in static_footer
    assert "position: absolute;" in static_feedback
    assert "bottom: calc(100% + 8px);" in static_feedback
    assert "position: relative;" in compact_region
    assert "position: absolute;" in compact_feedback
    assert "bottom: 43px;" in compact_feedback
