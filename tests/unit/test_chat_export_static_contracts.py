import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


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


def test_markdown_preview_replaces_hidden_frame_and_waits_for_latest_document_load():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the export preview lifecycle harness")

    source = CHAT_EXPORT_JS.read_text(encoding="utf-8")
    frame_start = source.index("function createPreviewFrame(doc)")
    frame_end = source.index("function createPreviewModal(targetDocument)", frame_start)
    frame_functions = source[frame_start:frame_end]
    render_start = source.index("function schedulePreviewRender()")
    render_end = source.index("function buildExportWindowFeatures()", render_start)
    render_functions = source[render_start:render_end]

    harness = (
        """
const assert = require('node:assert/strict');

const animationFrames = [];
function requestAnimationFrame(callback) {
    animationFrames.push(callback);
}
function runAnimationFrame() {
    const callback = animationFrames.shift();
    assert.ok(callback, 'expected a queued animation frame');
    callback();
}

let doc;
function makeFrame() {
    const listeners = new Map();
    return {
        ownerDocument: doc,
        parentNode: null,
        className: '',
        hidden: false,
        srcdoc: '',
        attributes: {},
        setAttribute(name, value) {
            this.attributes[name] = value;
        },
        addEventListener(type, callback, options) {
            listeners.set(type, { callback, once: !!(options && options.once) });
        },
        dispatch(type) {
            const listener = listeners.get(type);
            assert.ok(listener, `expected a ${type} listener`);
            if (listener.once) listeners.delete(type);
            listener.callback();
        }
    };
}
doc = {
    createElement(tagName) {
        assert.equal(tagName, 'iframe');
        return makeFrame();
    }
};

const previewBody = {
    child: null,
    replaceChild(nextFrame, currentFrame) {
        assert.equal(this.child, currentFrame);
        assert.notEqual(
            nextFrame.srcdoc,
            '',
            'srcdoc must be assigned before the replacement iframe is inserted'
        );
        currentFrame.parentNode = null;
        nextFrame.parentNode = this;
        this.child = nextFrame;
    }
};
const initialFrame = makeFrame();
initialFrame.hidden = true;
initialFrame.parentNode = previewBody;
previewBody.child = initialFrame;

const modal = {
    panel: { hidden: false },
    previewBody,
    frame: initialFrame,
    previewImage: { src: '' },
    previewImageWrap: { hidden: true },
    placeholder: { hidden: true, textContent: '' },
    downloadButton: { disabled: false },
    openWindowButton: { disabled: false }
};
const state = {
    previewModal: modal,
    previewRenderToken: 0,
    previewRenderPending: false,
    previewCurrentCacheKey: '',
    isPreviewRendering: false,
    exportFormat: 'image'
};
let selectedEntries = [{ id: 'message-1' }];
const requestedFormats = [];
let resolveFirstPreview;

function ensurePreviewModal() { return modal; }
function getSelectedEntries() { return selectedEntries; }
function renderControls() {}
function updateSummary() {}
function translateLabel(_key, fallback) { return fallback; }
function logExportError() {}
function getErrorMessage(error) { return String(error); }
function getOrBuildPreviewPayload(_entries, formatId) {
    requestedFormats.push(formatId);
    if (requestedFormats.length === 1) {
        return new Promise(function (resolve) {
            resolveFirstPreview = resolve;
        });
    }
    return Promise.resolve({
        cacheKey: `markdown-${requestedFormats.length}`,
        previewKind: 'document',
        previewDocument: `<p>Markdown preview ${requestedFormats.length}</p>`
    });
}
"""
        + frame_functions
        + render_functions
        + """
(async function () {
    // Start an image render, then request Markdown while it still owns the
    // render lock. The latest request must be replayed after the image becomes
    // stale instead of leaving the frame blank.
    schedulePreviewRender();
    runAnimationFrame();
    assert.equal(state.isPreviewRendering, true);

    state.exportFormat = 'markdown';
    schedulePreviewRender();
    runAnimationFrame();
    assert.equal(state.previewRenderPending, true);

    resolveFirstPreview({
        cacheKey: 'stale-image',
        previewKind: 'image',
        previewUrl: 'blob:stale-image'
    });
    await Promise.resolve();

    assert.equal(animationFrames.length, 1);
    runAnimationFrame();
    await Promise.resolve();

    const firstMarkdownFrame = modal.frame;
    assert.notEqual(firstMarkdownFrame, initialFrame);
    assert.equal(firstMarkdownFrame.hidden, false);
    assert.equal(firstMarkdownFrame.srcdoc, '<p>Markdown preview 2</p>');
    assert.equal(modal.placeholder.hidden, false);
    firstMarkdownFrame.dispatch('load');
    assert.equal(modal.placeholder.hidden, true);

    // Exercise the other reported path: selected -> empty -> selected. A new
    // visible frame must be mounted and its own load event must reveal it.
    selectedEntries = [];
    schedulePreviewRender();
    runAnimationFrame();
    assert.equal(modal.frame.hidden, true);
    assert.equal(modal.placeholder.hidden, false);

    selectedEntries = [{ id: 'message-1' }];
    schedulePreviewRender();
    runAnimationFrame();
    await Promise.resolve();

    const secondMarkdownFrame = modal.frame;
    assert.notEqual(secondMarkdownFrame, firstMarkdownFrame);
    assert.equal(secondMarkdownFrame.hidden, false);
    assert.equal(secondMarkdownFrame.srcdoc, '<p>Markdown preview 3</p>');
    assert.equal(modal.placeholder.hidden, false);
    secondMarkdownFrame.dispatch('load');
    assert.equal(modal.placeholder.hidden, true);
    assert.deepEqual(requestedFormats, ['image', 'markdown', 'markdown']);
})().catch(function (error) {
    console.error(error);
    process.exitCode = 1;
});
"""
    )

    run_node_script(node, harness, check=True, cwd=PROJECT_ROOT)


def test_export_preview_locale_change_updates_the_current_replaced_frame():
    source = CHAT_EXPORT_JS.read_text(encoding="utf-8")
    modal_start = source.index("function createPreviewModal(targetDocument)")
    modal_end = source.index("function getPreviewModalDocument(modal)", modal_start)
    locale_handler = source[modal_start:modal_end].split(
        "var localeHandler = function () {", 1
    )[1].split("window.addEventListener('localechange', localeHandler);", 1)[0]

    assert "modal.frame.setAttribute('title'" in locale_handler
    assert "\n            frame.setAttribute('title'" not in locale_handler
