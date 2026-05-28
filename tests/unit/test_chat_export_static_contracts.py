from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHAT_EXPORT_JS = PROJECT_ROOT / "static" / "app-chat-export.js"


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
