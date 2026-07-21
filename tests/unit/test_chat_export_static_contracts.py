from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHAT_EXPORT_JS = PROJECT_ROOT / "static" / "app" / "app-chat-export.js"


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
    assert "var EXPORT_PREVIEW_WINDOW_NAME_PREFIX = 'neko_chat_export_preview_';" in script
    assert "function buildExportPreviewWindowName(kind)" in script
    assert "function hasExportPreviewWindowControlApi(previewWindow)" in script
    assert "previewWindow.nekoWindowControl" in script
    assert "typeof api.getAlwaysOnTopState === 'function'" in script
    assert "typeof api.toggleAlwaysOnTop === 'function'" in script
    assert "window.open('', buildExportPreviewWindowName('main'), buildExportWindowFeatures())" in script
    assert "function openExportDocumentWindow()" in script
    assert "var ownerWindow = state.previewWindow && !state.previewWindow.closed" in script
    assert "ownerWindow.open('', buildExportPreviewWindowName('document'))" in script
    assert "var imgWin = openExportDocumentWindow();" in script
    assert "var win = openExportDocumentWindow();" in script
    assert "'</title>' + buildWindowControlAssetsHtml()" in script
    assert "doc.replace(/<\\/head>/i, buildWindowControlAssetsHtml() + '</head>')" in script

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
    assert "window.open('', buildExportPreviewWindowName('main'), buildExportWindowFeatures())" in open_export
    assert "if (isCurrentChatWindowHandle(previewWindow))" in open_export
    assert "var returnedHref = getWindowHref(previewWindow);" in open_export
    assert "returnedHref !== 'about:blank' && !isExportPreviewShellUrl(returnedHref)" in open_export
    assert "var openedShellWindow = isExportPreviewShellUrl(returnedHref);" in open_export
    assert "var canRewritePreview = await waitForExportPreviewRewriteGate(" in open_export
    assert "previewWindow.__nekoChatExportPreviewWindow = true;" in open_export


def test_export_preview_requeues_changes_that_arrive_during_render():
    script = CHAT_EXPORT_JS.read_text(encoding="utf-8")

    assert "previewRenderQueued: false" in script
    assert "if (state.isPreviewRendering) {" in script
    assert "state.previewRenderQueued = true;" in script
    assert "if (state.previewRenderQueued && state.previewModal)" in script
    assert "schedulePreviewRender();" in script


def test_export_document_window_chrome_has_the_full_control_group():
    script = CHAT_EXPORT_JS.read_text(encoding="utf-8")
    start = script.index("function buildWindowChromeHtml(title)")
    end = script.index("function openExportDocumentWindow()", start)
    chrome = script[start:end]

    pin_index = chrome.index('data-neko-window-control="pin"')
    minimize_index = chrome.index('data-neko-window-control="minimize"')
    maximize_index = chrome.index('data-neko-window-control="maximize"')
    close_index = chrome.index('onclick="window.close()"')
    assert pin_index < minimize_index < maximize_index < close_index
    assert 'aria-pressed="false" hidden' in chrome
