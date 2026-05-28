from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHAT_EXPORT_JS = PROJECT_ROOT / "static" / "app-chat-export.js"


def test_export_preview_waits_for_shell_before_rewriting_document():
    script = CHAT_EXPORT_JS.read_text(encoding="utf-8")

    assert "function waitForExportPreviewShell(previewWindow, targetUrl)" in script
    assert "function isExportPreviewShellReady(previewWindow, targetUrl)" in script
    assert "href === 'about:blank'" in script
    assert "previewWindow.addEventListener('load', checkReady)" in script

    wait_index = script.index("await waitForExportPreviewShell(previewWindow, getExportPreviewShellUrl());")
    stop_index = script.index("if (typeof previewWindow.stop === 'function') previewWindow.stop();", wait_index)
    doc_open_index = script.index("var doc = previewWindow.document;", wait_index)
    assert wait_index < doc_open_index
    assert wait_index < stop_index < doc_open_index
