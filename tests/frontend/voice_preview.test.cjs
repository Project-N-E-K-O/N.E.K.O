'use strict';
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { createVoicePreviewServer } = require('./voice_preview_server.cjs');
const { verifyPreviewBodyRaces } = require('./voice_preview_races.cjs');
test('preview cancellation and timeout cover the actual HTTP response body', async () => {
    const transport = createVoicePreviewServer();
    await new Promise(resolve => transport.server.listen(0, '127.0.0.1', resolve));
    const origin = 'http://127.0.0.1:' + transport.server.address().port;
    const storage = new Map();
    const context = vm.createContext({
        activeVoicePreviewSessions: new Map(), AbortController, setTimeout, clearTimeout, console,
        fetch: (url, options) => fetch(new URL(url, origin), options),
        localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), clear: () => storage.clear() },
        attachVoicePreviewButton() {}, setVoicePreviewButtonState() {}, updateVoicePreviewSessionState() {},
        getVoicePreviewLanguage: () => 'zh-CN',
        sleepVoiceCloneLoaderRetry: async () => {}, VOICE_CLONE_LOADER_FETCH_BACKOFF_MS: 1,
        document: { createElement: () => ({ disabled: false }), body: { appendChild() {} } },
        buildNonJsonError: (response, text) => 'HTTP ' + response.status + ': ' + text,
        resolveBackendErrorMsg: data => data.error,
    });
    context.window = context;
    const source = fs.readFileSync(path.join(__dirname, '../../static/js/voice_clone.js'), 'utf8');
    vm.runInContext(source.slice(source.indexOf('async function safeReadResponse('), source.indexOf('function buildNonJsonError(')), context);
    vm.runInContext(source.slice(source.indexOf('function finishVoicePreviewSession('), source.indexOf('// 加载音色列表', source.indexOf('async function playPreview('))), context, { filename: path.join(__dirname, 'voice_preview_runtime.cjs') });
    const run = async code => {
        const value = await vm.runInContext(code, context);
        return value === undefined ? value : JSON.parse(JSON.stringify(value));
    };
    try { await verifyPreviewBodyRaces({ run, transport }); }
    finally { await transport.close(); }
});

test('preview reader keeps diagnostics and propagates body aborts without a second read', async () => {
    const source = fs.readFileSync(path.join(__dirname, '../../static/js/voice_clone.js'), 'utf8');
    const context = vm.createContext({});
    vm.runInContext(source.slice(source.indexOf('function finishVoicePreviewSession('), source.indexOf('// 加载音色列表', source.indexOf('async function playPreview('))), context, { filename: path.join(__dirname, 'voice_preview_runtime.cjs') });
    for (const contentType of ['application/json', 'application/problem+json; charset=utf-8']) {
        const result = await context.readVoicePreviewResponse(new Response('{"success":true}', { headers: { 'content-type': contentType } }));
        assert.equal(result.data.success, true);
        assert.equal(result.nonJson, false);
    }
    for (const [contentType, text] of [['application/json', '{broken'], ['text/html', '<html>Gateway failure</html>']]) {
        const result = await context.readVoicePreviewResponse(new Response(text, { headers: { 'content-type': contentType } }));
        assert.equal(result.data, null);
        assert.equal(result.nonJson, true);
        assert.equal(result.text, text);
    }
    const error = new DOMException('Download interrupted', 'AbortError');
    let reads = 0;
    await assert.rejects(context.readVoicePreviewResponse({ text: async () => { reads++; throw error; } }), failure => failure === error);
    assert.equal(reads, 1);
});
