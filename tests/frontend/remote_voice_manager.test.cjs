'use strict';
const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { RemoteVoiceOperation } = require('../../static/js/remote_voice_manager.js');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/remote_voice_manager.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
};

function previewHarness(storage = new Map()) {
    const previewSource = fs.readFileSync(path.join(__dirname, '../../static/js/voice_clone.js'), 'utf8');
    const method = previewSource.slice(previewSource.indexOf('function finishVoicePreviewSession('), previewSource.indexOf('// 加载音色列表', previewSource.indexOf('async function playPreview(')));
    const sessions = new Map(), requests = [], audios = [], audioInstances = [], deadlines = [], errors = [];
    const context = vm.createContext({
        activeVoicePreviewSessions: sessions,
        attachVoicePreviewButton() {},
        setVoicePreviewButtonState() {},
        updateVoicePreviewSessionState() {},
        getVoicePreviewLanguage: () => 'zh-CN',
        localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
        AbortController, setTimeout: (_callback, ms) => { deadlines.push(ms); return ms; }, clearTimeout() {},
        fetch: (url, options) => { const request = { ...deferred(), url, options }; requests.push(request); return request.promise.then(response => ({
            ...response, headers: new Headers({ 'content-type': 'application/json' }), text: async () => JSON.stringify(response.data)
        })); },
        safeReadResponse: async response => ({ data: response.data }),
        sleepVoiceCloneLoaderRetry: async () => {}, VOICE_CLONE_LOADER_FETCH_BACKOFF_MS: 1,
        window: {}, console: { warn() {}, error() {} },
        showVoicePreviewErrorNotice: value => errors.push(value),
        Audio: class {
            constructor(src) { audios.push(src); audioInstances.push(this); this.src = src; this.paused = false; this.released = false; }
            addEventListener() {}
            async play() { if (this.pendingPlay) await this.pendingPlay.promise; }
            pause() { this.paused = true; }
            removeAttribute(key) { assert.equal(key, 'src'); this.src = ''; }
            load() { this.released = true; }
        },
        encodeURIComponent, Map, Set, JSON
    });
    vm.runInContext(method, context, { filename: path.join(__dirname, 'voice_preview_runtime.cjs') });
    return { context, sessions, requests, audios, audioInstances, deadlines, errors, storage,
        play: options => context.playPreview('voice_1234567890abcdef1234567890abcdef', { disabled: false }, options),
        finish: () => { for (const session of sessions.values()) context.finishVoicePreviewSession(session); },
        resolve: (index, audio = 'NEW') => requests[index].resolve({ ok: true, status: 200, data: { success: true, audio } }) };
}

const importedPreview = { source: 'clone', origin: 'import', provider: 'cosyvoice' };

function overwriteSnapshot(status, actions, revision = 1) {
    return { local_ref: 'voice-local', operation_id: 'controlled-operation', record_revision: revision,
        overwrite_status: status, actions };
}

async function startControlledOverwrite(h) {
    h.window.RemoteVoiceManager.openOverwrite('voice-local', { provider: 'cosyvoice', remote_voice_id: 'remote' });
    const input = h.panel().querySelectorAll('input')[0];
    input.files = [new Blob(['audio'])]; input.dispatch('change');
    h.button('overwrite').dispatch('click');
    h.resolve(0, { ...h.ctx, provider: 'cosyvoice', capabilities: { ...h.ctx.capabilities, overwrite: true } });
    await tick();
    return input;
}

test('failed context fetch keeps an in-dialog query exit and releases busy controls', async () => {
    const h = harness();
    h.window.RemoteVoiceManager.openOverwrite('voice-local', { provider: 'cosyvoice', remote_voice_id: 'remote' });
    const input = h.panel().querySelectorAll('input')[0];
    input.files = [new Blob(['audio'])]; input.dispatch('change'); h.button('overwrite').dispatch('click');
    h.requests[0].reject(new TypeError('isolated connection failure')); await tick();
    assert.equal(h.button('overwrite').hidden, true);
    assert.equal(h.button('refreshStatus').hidden, false);
    assert.equal(h.button('refreshStatus').disabled, false);
    assert.equal(h.panel().attributes['aria-busy'], 'false');
    assert.equal(h.requests.filter(request => request.options.method === 'POST').length, 0);
    h.button('refreshStatus').dispatch('click');
    h.resolve(1, { ...h.ctx, capabilities: { ...h.ctx.capabilities, overwrite: true } }); await tick();
    h.resolve(2, { success: true, status: 'failed', details: { voice_state: overwriteSnapshot('failed', ['refresh', 'overwrite']) } });
    await tick();
    assert.equal(h.button('overwrite').hidden, false);
    assert.equal(input.files.length, 1);
    h.window.RemoteVoiceManager.close();
});

test('HTTP409 unknown from a stale list offers query without another submission', async () => {
    const h = harness();
    await startControlledOverwrite(h);
    h.resolve(1, { success: false, code: 'UPDATE_OUTCOME_UNKNOWN', details: {
        attempt_outcome: 'not_submitted', state_sync: 'unchanged',
        voice_state: overwriteSnapshot('unknown', ['refresh'], 7)
    } }, 409);
    await tick();
    assert.equal(h.button('overwrite').hidden, true);
    assert.equal(h.button('refreshStatus').hidden, false);
    assert.equal(h.button('refreshStatus').disabled, false);
    assert.equal(h.requests.filter(request => request.options.method === 'POST').length, 1);
    h.window.RemoteVoiceManager.close();
});

test('persisted rejection restores overwrite inside the same dialog; save failure keeps query only', async () => {
    for (const failedSave of [false, true]) {
        const h = harness();
        const input = await startControlledOverwrite(h);
        h.resolve(1, { success: false, code: failedSave ? 'LOCAL_SAVE_FAILED_AFTER_UPDATE' : 'UPSTREAM_REJECTED', details: {
            attempt_outcome: 'rejected', state_sync: failedSave ? 'failed' : 'saved',
            voice_state: overwriteSnapshot(failedSave ? 'processing' : 'failed', failedSave ? ['refresh'] : ['refresh', 'overwrite'])
        } }, failedSave ? 500 : 400);
        await tick();
        assert.equal(h.button('overwrite').hidden, failedSave);
        assert.equal(h.button('refreshStatus').hidden, false);
        assert.equal(h.button('overwrite').disabled, failedSave);
        assert.equal(input.files.length, 1);
        h.window.RemoteVoiceManager.close();
    }
});

test('query terminal snapshot updates modal actions and rejects a lower local revision', async () => {
    const h = harness();
    await startControlledOverwrite(h);
    h.resolve(1, { success: false, code: 'UPDATE_OUTCOME_UNKNOWN', details: {
        voice_state: overwriteSnapshot('unknown', ['refresh'], 7)
    } }, 409); await tick();
    h.button('refreshStatus').dispatch('click');
    h.resolve(2, { success: true, status: 'failed', details: {
        voice_state: overwriteSnapshot('failed', ['refresh', 'overwrite'], 8)
    } }); await tick();
    assert.equal(h.button('overwrite').hidden, false);
    assert.equal(h.button('overwrite').disabled, false);
    h.button('refreshStatus').dispatch('click');
    h.resolve(3, { success: true, status: 'unknown', details: {
        voice_state: overwriteSnapshot('unknown', ['refresh'], 7)
    } }); await tick();
    assert.equal(h.button('overwrite').hidden, false);
    assert.ok(h.panel().textContent.includes('voice.remote.failed'));
    h.window.RemoteVoiceManager.close();
});

test('legacy missing overwrite details retains a query exit without trusting a refusal code', async () => {
    const h = harness();
    await startControlledOverwrite(h);
    h.resolve(1, { success: false, code: 'UPSTREAM_REJECTED' }, 400); await tick();
    assert.equal(h.button('overwrite').hidden, true);
    assert.equal(h.button('refreshStatus').hidden, false);
    h.window.RemoteVoiceManager.close();
});

test('server selected Doubao resource is displayed readonly and sent unchanged', async () => {
    const h = harness();
    h.window.RemoteVoiceManager.openImport();
    h.resolve(0, { ...h.ctx, required_fields: [{ key: 'doubao_resource_id', required: true,
        readonly: true, default_value: 'server-resource' }] }); await tick();
    h.resolve(1, { success: true, voices: [] }); await tick();
    h.button('manualEntry').dispatch('click');
    const inputs = h.panel().querySelectorAll('input');
    const resource = inputs.find(input => input.name === 'doubao_resource_id');
    assert.equal(resource.readOnly, true);
    assert.equal(resource.value, 'server-resource');
    inputs[0].value = 'S_isolated'; inputs[0].dispatch('input');
    h.button('import').dispatch('click');
    assert.equal(JSON.parse(h.requests.at(-1).options.body).metadata.doubao_resource_id, 'server-resource');
    h.resolve(2, { success: true, verification: 'verified' }); await tick();
    h.window.RemoteVoiceManager.close();
});

test('imported preview ignores legacy audio and uses the clone synthesis deadline', async () => {
    const key = 'voice_preview_voice_1234567890abcdef1234567890abcdef';
    const h = previewHarness(new Map([[key, JSON.stringify({ version: 2, language: 'zh-CN', audioSrc: 'OLD' })]]));
    const pending = h.play(importedPreview); await tick();
    assert.equal(h.requests.length, 1); assert.equal(h.deadlines[0], 30000);
    h.resolve(0); await pending;
    assert.deepEqual(h.audios, ['data:audio/mpeg;base64,NEW']);
});

test('overwrite operations and completion invalidate cached imported preview', async () => {
    const h = previewHarness();
    const first = h.play(importedPreview); h.resolve(0, 'BEFORE'); await first; h.finish();
    const reused = h.play(importedPreview); await reused; h.finish();
    assert.equal(h.requests.length, 1);
    const processing = { ...importedPreview, overwrite_operation_id: 'operation-A', overwrite_status: 'processing' };
    const updating = h.play(processing); await tick(); assert.equal(h.requests.length, 2);
    h.resolve(1, 'PROCESSING'); await updating; h.finish();
    const completed = h.play({ ...processing, overwrite_status: 'completed' }); await tick();
    assert.equal(h.requests.length, 3); h.resolve(2, 'AFTER'); await completed;
    assert.equal(h.audios.at(-1), 'data:audio/mpeg;base64,AFTER');
});

test('late preview from before overwrite cannot play or replace the current preview', async () => {
    const h = previewHarness(); const before = h.play(importedPreview);
    const after = h.play({ ...importedPreview, overwrite_operation_id: 'new-operation', overwrite_status: 'completed' });
    await tick(); assert.equal(h.requests.length, 2);
    h.resolve(1, 'AFTER'); await after; h.resolve(0, 'BEFORE'); await before;
    assert.deepEqual(h.audios, ['data:audio/mpeg;base64,AFTER']);
    assert.match([...h.storage.values()][0], /AFTER/);
});

test('blocked browser storage does not prevent preview synthesis', async () => {
    const storage = { get() { throw new Error('SecurityError'); }, set() { throw new Error('SecurityError'); } };
    const h = previewHarness(storage); const pending = h.play(importedPreview);
    await tick(); assert.equal(h.requests.length, 1); h.resolve(0); await pending;
    assert.equal(h.audios.length, 1); assert.deepEqual(h.errors, []);
});

test('legacy preset preview retains its language cache', async () => {
    const key = 'voice_preview_voice_1234567890abcdef1234567890abcdef';
    const h = previewHarness(new Map([[key, JSON.stringify({ version: 2, language: 'zh-CN', audioSrc: 'PRESET' })]]));
    await h.play({ source: 'preset' });
    assert.equal(h.requests.length, 0); assert.deepEqual(h.audios, ['PRESET']);
});

class Element {
    constructor(tag, document) {
        this.tagName = tag; this.document = document; this.children = []; this.listeners = {};
        this.dataset = {}; this.style = {}; this.attributes = {}; this.value = ''; this.hidden = false;
        this.disabled = false; this.inert = false; this.className = ''; this._text = '';
        this.classList = {
            add: name => { this.className += ' ' + name; },
            remove: name => { this.className = this.className.split(' ').filter(value => value !== name).join(' '); }
        };
    }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    get firstChild() { return this.children[0] || { set textContent(value) {} }; }
    get isConnected() { return this === this.document.body || !!this.parentElement?.isConnected; }
    append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } }
    appendChild(child) { this.append(child); return child; }
    replaceChildren(...children) { this._text = ''; this.children = []; this.append(...children); }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(key, handler) { (this.listeners[key] ||= []).push(handler); }
    remove() {
        const parent = this.parentElement;
        if (parent) parent.children = parent.children.filter(child => child !== this);
        this.parentElement = null;
    }
    focus() { this.document.activeElement = this; }
    reportValidity() { return !this.required || !!this.value.trim(); }
    getClientRects() { return this.hidden ? [] : [{}]; }
    querySelectorAll(selector) {
        const values = [];
        const matches = element => selector.startsWith('.') ? element.className.split(' ').includes(selector.slice(1)) :
            selector.split(',').some(part => part.trim().split('[')[0] === element.tagName);
        function walk(element) { for (const child of element.children) { if (matches(child)) values.push(child); walk(child); } }
        walk(this); return values;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    dispatch(type, data = {}) {
        const event = { type, target: this, preventDefault() {}, stopPropagation() {}, ...data };
        for (const handler of this.listeners[type] || []) handler(event);
    }
}

function harness(timers = { setTimeout, clearTimeout }) {
    const document = {
        listeners: {}, createElement: tag => new Element(tag, document),
        addEventListener(key, handler) { (this.listeners[key] ||= []).push(handler); },
        removeEventListener(key, handler) { this.listeners[key] = (this.listeners[key] || []).filter(value => value !== handler); },
        getElementById(id) {
            const walk = item => item.id === id ? item : item.children.map(walk).find(Boolean);
            return walk(this.body);
        },
        querySelector(selector) { return this.body.querySelector(selector); },
        dispatch(key, event = {}) { for (const handler of [...this.listeners[key] || []]) handler(event); }
    };
    document.body = new Element('body', document);
    const container = new Element('div', document); container.className = 'container';
    const provider = new Element('select', document); provider.id = 'voiceProvider'; provider.value = 'minimax';
    provider.options = [{ value: 'minimax', textContent: 'MiniMax' }, { value: 'cosyvoice_intl', textContent: 'CosyVoice international' }];
    const entry = new Element('button', document); entry.id = 'importExistingVoice';
    container.append(provider, entry); document.body.append(container); document.activeElement = entry;
    const requests = [];
    let refreshes = 0;
    const window = {
        document, t: key => key, addEventListener() {},
        fetch(url, options) { const pending = deferred(); requests.push({ url, options, ...pending }); return pending.promise; },
        loadVoices: async () => { refreshes++; },
        loadVoiceCloneProviderRestrictionState: async () => ({ ttsProviders: {
            minimax: { voice_management: { manual_import: true } }, cosyvoice: { voice_management: { manual_import: true } }
        } })
    };
    const context = vm.createContext({ window, document, AbortController, DOMException, URLSearchParams, FormData,
        setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
        loadVoiceCloneProviderRestrictionState: window.loadVoiceCloneProviderRestrictionState });
    vm.runInContext(source, context, { filename: path.join(__dirname, '../../static/js/remote_voice_manager.js') });
    document.dispatch('DOMContentLoaded');
    const resolve = (index, body, status = 200) => requests[index].resolve({ ok: status < 400, status, json: async () => body });
    const ctx = { success: true, provider: 'minimax', context_token: 'snapshot', configured: true,
        capabilities: { list: true, overwrite: false, manual_import: true }, required_fields: [] };
    const panel = () => document.querySelector('.remote-voice-dialog');
    const button = key => panel().querySelectorAll('button').find(item => item.textContent === 'voice.remote.' + key);
    return { window, document, container, provider, entry, requests, resolve, ctx, panel, button, refreshes: () => refreshes };
}

function searchClock() {
    const timers = new Map();
    return {
        setTimeout(callback, delay) { const id = {}; timers.set(id, { callback, delay }); return id; },
        clearTimeout(id) { timers.delete(id); },
        fire(delay) { for (const [id, timer] of [...timers]) if (timer.delay === delay) { timers.delete(id); timer.callback(); } },
        size: () => timers.size
    };
}

test('search queries unseen pages, skips empty pages and retains the query when loading more', async () => {
    const clock = searchClock(), h = harness(clock);
    h.window.RemoteVoiceManager.openImport(); h.resolve(0, h.ctx); await tick();
    h.resolve(1, { success: true, voices: [{ voice_id: 'unrelated', name: 'Unrelated' }], next_cursor: 'page2' }); await tick();
    const search = h.panel().querySelectorAll('input').find(input => input.type === 'search');
    search.value = 'Target'; search.dispatch('input'); clock.fire(250);
    h.resolve(2, h.ctx); await tick();
    assert.equal(new URL(h.requests[3].url, 'http://test').searchParams.get('query'), 'Target');
    assert.equal(new URL(h.requests[3].url, 'http://test').searchParams.has('cursor'), false);
    h.resolve(3, { success: true, voices: [], next_cursor: 'page2' }); await tick();
    assert.equal(new URL(h.requests[4].url, 'http://test').searchParams.get('cursor'), 'page2');
    assert.equal(h.panel().querySelector('.remote-voice-status').textContent, 'voice.remote.loading');
    h.resolve(4, { success: true, voices: [{ voice_id: 'target2', name: 'Target two' }], next_cursor: 'page3' }); await tick();
    assert.ok(h.panel().textContent.includes('Target two'));
    h.button('loadMore').dispatch('click');
    assert.equal(new URL(h.requests[5].url, 'http://test').searchParams.get('query'), 'Target');
    h.resolve(5, { success: true, voices: [], next_cursor: 'page4' }); await tick();
    h.resolve(6, { success: true, voices: [{ voice_id: 'target4', name: 'Target four' }] }); await tick();
    assert.equal(h.panel().querySelectorAll('input').filter(input => input.type === 'radio').length, 2);
    assert.equal(h.requests.some(request => request.options.method === 'POST'), false);
    h.window.RemoteVoiceManager.close(); assert.equal(clock.size(), 0);
});

test('changing a search immediately retires a late result and closing releases the debounce timer', async () => {
    const clock = searchClock(), h = harness(clock);
    h.window.RemoteVoiceManager.openImport(); h.resolve(0, h.ctx); await tick();
    h.resolve(1, { success: true, voices: [] }); await tick();
    const search = h.panel().querySelectorAll('input').find(input => input.type === 'search');
    search.value = 'Old'; search.dispatch('input'); clock.fire(250);
    h.resolve(2, h.ctx); await tick();
    search.value = 'New'; search.dispatch('input');
    assert.equal(h.requests[3].options.signal.aborted, true);
    h.resolve(3, { success: true, voices: [{ voice_id: 'old', name: 'Old' }] }); await tick();
    assert.ok(!h.panel().textContent.includes('Old'));
    clock.fire(250); h.resolve(4, h.ctx); await tick();
    assert.equal(new URL(h.requests[5].url, 'http://test').searchParams.get('query'), 'New');
    h.resolve(5, { success: true, voices: [{ voice_id: 'new', name: 'New' }] }); await tick();
    assert.ok(h.panel().textContent.includes('New'));
    search.value = 'Cancelled'; search.dispatch('input');
    h.window.RemoteVoiceManager.close(); assert.equal(clock.size(), 0);
    clock.fire(250); assert.equal(h.requests.length, 6);
});

test('search rejects a cyclic cursor and its overall deadline aborts further traversal', async () => {
    const clock = searchClock(), h = harness(clock);
    h.window.RemoteVoiceManager.openImport(); h.resolve(0, h.ctx); await tick();
    h.resolve(1, { success: true, voices: [] }); await tick();
    const search = h.panel().querySelectorAll('input').find(input => input.type === 'search');
    search.value = 'Target'; search.dispatch('input'); clock.fire(250);
    h.resolve(2, h.ctx); await tick();
    h.resolve(3, { success: true, voices: [], next_cursor: 'repeat' }); await tick();
    h.resolve(4, { success: true, voices: [], next_cursor: 'repeat' }); await tick();
    assert.ok(h.panel().textContent.includes('voice.remote.requestFailed'));
    assert.equal(h.requests.length, 5); assert.equal(clock.size(), 0);
    h.button('refresh').dispatch('click'); h.resolve(5, h.ctx); await tick();
    clock.fire(35000); assert.equal(h.requests[6].options.signal.aborted, true);
    h.resolve(6, { success: true, voices: [], next_cursor: 'further' }); await tick();
    assert.equal(h.requests.length, 7); assert.equal(clock.size(), 0);
    h.window.RemoteVoiceManager.close();
});

test('overwrite and status contexts identify their local record while import context stays provider-only', async () => {
    const h = harness();
    h.window.RemoteVoiceManager.openImport();
    assert.equal(new URL(h.requests[0].url, 'http://test').searchParams.has('local_ref'), false);
    h.resolve(0, h.ctx); await tick(); h.resolve(1, { success: true, voices: [] }); await tick();
    h.window.RemoteVoiceManager.openOverwrite('voice-target', { provider: 'cosyvoice', remote_voice_id: 'remote' });
    const audio = h.panel().querySelectorAll('input')[0]; audio.files = [new Blob(['audio'])]; audio.dispatch('change');
    h.button('overwrite').dispatch('click');
    assert.equal(new URL(h.requests[2].url, 'http://test').searchParams.get('local_ref'), 'voice-target');
    h.window.RemoteVoiceManager.close(); h.resolve(2, h.ctx); await tick();
    h.window.RemoteVoiceManager.openStatus('voice-status', { provider: 'cosyvoice', remote_voice_id: 'remote' });
    assert.equal(new URL(h.requests[3].url, 'http://test').searchParams.get('local_ref'), 'voice-status');
    h.window.RemoteVoiceManager.close(); h.resolve(3, h.ctx); await tick();
});

test('pending deletion displays the translated conflict and restores the local list without clearing preview', async () => {
    const product = fs.readFileSync(path.join(__dirname, '../../static/js/voice_clone.js'), 'utf8');
    const start = product.indexOf('async function deleteVoice(');
    const end = product.indexOf('// 页面加载时自动加载音色列表', start);
    assert.ok(start >= 0 && end > start, 'Product delete method boundaries must exist');
    const h = harness(), alerts = [];
    const list = new Element('div', h.document); list.id = 'voice-list-container';
    const refresh = new Element('button', h.document); refresh.id = 'refresh-voices-btn';
    h.container.append(list, refresh);
    const context = vm.createContext({
        document: h.document, window: h.window, console, setTimeout, confirm: () => true,
        fetch: async () => ({ ok: false, status: 409 }),
        safeReadResponse: async () => ({ data: { success: false, code: 'OPERATION_IN_PROGRESS', error: 'OPERATION_IN_PROGRESS' } }),
        alert: text => alerts.push(text), loadVoices: h.window.loadVoices,
        localStorage: { removeItem: () => assert.fail('A denied delete must retain preview cache') }
    });
    vm.runInContext(product.slice(start, end), context);
    await vm.runInContext("deleteVoice('voice-pending', 'Voice')", context);
    assert.deepEqual(alerts, ['voice.remote.operationInProgress']);
    assert.equal(h.refreshes(), 1); assert.equal(refresh.disabled, false);
});

test('superseded transport and late JSON cannot publish into a new operation', async () => {
    const pending = deferred();
    const operation = new RemoteVoiceOperation(async () => ({ ok: true, json: () => pending.promise }));
    const old = operation.begin('minimax');
    const result = operation.request(old, '/list');
    await tick();
    const next = operation.begin('elevenlabs');
    pending.resolve({ success: true });
    await assert.rejects(result, { name: 'AbortError' });
    assert.equal(operation.owns(next), true);
    assert.equal(old.controller.signal.aborted, true);
});

test('timeout aborts transport and retires request ownership', async () => {
    const operation = new RemoteVoiceOperation((url, options) => new Promise((resolve, reject) => {
        options.signal.addEventListener('abort', () => reject(new DOMException('timeout', 'AbortError')));
    }), 5);
    const current = operation.begin('minimax');
    await assert.rejects(operation.request(current, '/list'), { name: 'AbortError' });
    assert.equal(operation.owns(current), false);
});

test('closing a loading dialog aborts it and restores background and focus', async () => {
    const h = harness(); h.window.RemoteVoiceManager.openImport();
    assert.equal(h.container.inert, true);
    h.window.RemoteVoiceManager.close();
    assert.equal(h.requests[0].options.signal.aborted, true);
    h.resolve(0, h.ctx); await tick();
    assert.equal(h.panel(), null);
    assert.equal(h.container.inert, false);
    assert.equal(h.document.activeElement, h.entry);
    assert.equal(h.requests.length, 1);
});

test('refresh supersedes an old list and never saves remote results automatically', async () => {
    const h = harness(); h.window.RemoteVoiceManager.openImport(); h.resolve(0, h.ctx); await tick();
    h.button('refresh').dispatch('click');
    h.resolve(2, h.ctx); await tick();
    h.resolve(3, { success: true, voices: [{ voice_id: 'new', name: 'New', status: 'ready' }] }); await tick();
    h.resolve(1, { success: true, voices: [{ voice_id: 'old', name: 'Old' }] }); await tick();
    assert.ok(h.panel().textContent.includes('New'));
    assert.ok(!h.panel().textContent.includes('Old'));
    assert.equal(h.requests.filter(request => request.options.method === 'POST').length, 0);
});

test('manual mode replaces a pending list and import only saves the chosen ID', async () => {
    const h = harness(); h.window.RemoteVoiceManager.openImport(); h.resolve(0, h.ctx); await tick();
    h.button('manualEntry').dispatch('click');
    const inputs = h.panel().querySelectorAll('input');
    inputs[0].value = 'Existing-voice'; inputs[0].dispatch('input'); inputs[1].value = 'My voice';
    h.button('import').dispatch('click');
    const payload = JSON.parse(h.requests[2].options.body);
    assert.equal(payload.remote_voice_id, 'Existing-voice');
    assert.equal(payload.display_name, 'My voice');
    assert.equal(payload.context_token, 'snapshot');
    assert.deepEqual(Object.keys(payload).sort(), ['context_token', 'display_name', 'metadata', 'provider', 'remote_voice_id']);
    h.resolve(1, { success: true, voices: [{ voice_id: 'late' }] }); await tick();
    h.resolve(2, { success: true, verification: 'unverified' }); await tick();
    assert.equal(h.refreshes(), 1);
    assert.ok(h.panel().textContent.includes('voice.remote.importedUnverified'));
    assert.ok(!h.panel().textContent.includes('late'));
    assert.equal(h.requests.some(request => request.url.includes('clone') || request.url.includes('voice_id')), false);
});

for (const action of ['search', 'refresh', 'manualEntry', 'backToList']) {
    test(`pending import owns its acknowledgement despite ${action}`, async () => {
        const clock = searchClock(), h = harness(clock);
        h.window.RemoteVoiceManager.openImport(); h.resolve(0, h.ctx); await tick();
        h.resolve(1, { success: true, voices: [{ voice_id: 'Existing', name: 'Voice' }] }); await tick();
        if (action === 'backToList') {
            h.button('manualEntry').dispatch('click');
            const id = h.panel().querySelectorAll('input')[0]; id.value = 'Existing'; id.dispatch('input');
            h.button('import').dispatch('click');
        } else {
            h.panel().querySelectorAll('input').find(input => input.type === 'radio').dispatch('change');
            h.button('importSelected').dispatch('click');
        }
        const control = action === 'search' ? h.panel().querySelectorAll('input').find(input => input.type === 'search') : h.button(action);
        assert.equal(control.disabled, true);
        // Force dispatch too: disabled DOM controls alone cannot enforce ownership.
        if (action === 'search') { control.value = 'Other'; control.dispatch('input'); }
        else control.dispatch('click');
        clock.fire(250); await tick();
        assert.equal(h.requests.length, 3);
        assert.equal(h.requests[2].options.signal.aborted, false);
        h.resolve(2, { success: true, verification: 'verified' }); await tick();
        assert.equal(h.refreshes(), 1);
        assert.ok(h.panel().textContent.includes('voice.remote.imported'));
        assert.ok(h.panel().textContent.includes('voice.remote.bindHint'));
        h.window.RemoteVoiceManager.close(); assert.equal(clock.size(), 0);
    });
}

test('failed import releases query controls and manual values for a deliberate retry', async () => {
    const h = harness(searchClock());
    h.window.RemoteVoiceManager.openImport(); h.resolve(0, h.ctx); await tick();
    h.resolve(1, { success: true, voices: [] }); await tick();
    h.button('manualEntry').dispatch('click');
    const id = h.panel().querySelectorAll('input')[0]; id.value = 'Existing'; id.dispatch('input');
    h.button('import').dispatch('click');
    assert.equal(id.disabled, true);
    h.resolve(2, { success: false, code: 'CONTEXT_CHANGED' }, 409); await tick();
    assert.equal(id.disabled, false); assert.equal(id.value, 'Existing');
    assert.equal(h.button('backToList').disabled, false);
    assert.equal(h.button('import').disabled, false); assert.equal(h.refreshes(), 0);
    h.button('backToList').dispatch('click');
    h.resolve(3, h.ctx); await tick(); h.resolve(4, { success: true, voices: [] }); await tick();
    assert.equal(h.panel().querySelectorAll('input').find(input => input.type === 'search').disabled, false);
    h.window.RemoteVoiceManager.close();
});

for (const phase of ['fetching', 'playing', 'play-rejection']) {
    test(`confirmed deletion retires ${phase} preview and ignores late delivery`, async () => {
        const h = previewHarness(), dom = harness(), ref = 'voice_1234567890abcdef1234567890abcdef';
        const product = fs.readFileSync(path.join(__dirname, '../../static/js/voice_clone.js'), 'utf8');
        const start = product.indexOf('async function deleteVoice(');
        const list = new Element('div', dom.document); list.id = 'voice-list-container'; dom.container.append(list);
        Object.assign(h.context, { document: dom.document, confirm: () => true, alert: assert.fail,
            loadVoices: dom.window.loadVoices, window: dom.window });
        vm.runInContext(product.slice(start, product.indexOf('// 页面加载时自动加载音色列表', start)), h.context);
        if (phase === 'play-rejection') {
            const Audio = h.context.Audio, play = deferred();
            h.context.Audio = class extends Audio { constructor(src) { super(src); this.pendingPlay = play; } };
        }
        const pending = h.play(importedPreview);
        if (phase !== 'fetching') { h.resolve(0); await tick(); }
        const deleted = h.context.deleteVoice(ref, 'Voice'); await tick();
        h.requests[1].resolve({ ok: true, status: 200, data: { success: true } }); await deleted;
        assert.equal(h.sessions.size, 0); assert.equal(dom.refreshes(), 1);
        if (phase === 'fetching') {
            assert.equal(h.requests[0].options.signal.aborted, true);
            h.resolve(0, 'DELETED');
        } else {
            assert.equal(h.audioInstances[0].paused, true); assert.equal(h.audioInstances[0].released, true);
            if (phase === 'play-rejection') h.audioInstances[0].pendingPlay.reject(new Error('retired play'));
        }
        await pending;
        assert.equal(h.storage.size, 0); assert.deepEqual(h.errors, []);
        assert.equal(h.audios.length, phase === 'fetching' ? 0 : 1);
    });
}

test('a successful empty library refresh retires only missing imported preview sessions', async () => {
    const h = previewHarness(), dom = harness();
    const product = fs.readFileSync(path.join(__dirname, '../../static/js/voice_clone.js'), 'utf8');
    const list = new Element('div', dom.document); list.id = 'voice-list-container'; dom.container.append(list);
    Object.assign(h.context, { document: dom.document, window: dom.window,
        getCurrentCharacterVoiceId: async () => '',
        fetchVoiceCloneLoaderResponse: async () => ({ ok: true, status: 200, data: { voices: {} } }) });
    vm.runInContext(product.slice(product.indexOf('let voiceListLoadGeneration ='), product.indexOf('// 删除音色')), h.context);
    const pending = h.play(importedPreview);
    const legacy = { voiceId: 'legacy', imported: false, buttons: new Set() }; h.sessions.set('legacy', legacy);
    await h.context.loadVoices();
    assert.equal(h.requests[0].options.signal.aborted, true);
    assert.equal(h.sessions.get('legacy'), legacy);
    h.resolve(0, 'DELETED'); await pending;
    assert.equal(h.audios.length, 0); assert.equal(h.storage.size, 0);
});

test('already imported rows cannot be selected and pagination deduplicates results', async () => {
    const h = harness(); h.window.RemoteVoiceManager.openImport(); h.resolve(0, h.ctx); await tick();
    h.resolve(1, { success: true, voices: [{ voice_id: 'saved', imported: true }], next_cursor: 'page2' }); await tick();
    assert.equal(h.panel().querySelectorAll('input').find(input => input.type === 'radio').disabled, true);
    h.button('loadMore').dispatch('click');
    assert.ok(h.requests[2].url.includes('cursor=page2'));
    h.resolve(2, { success: true, voices: [{ voice_id: 'saved', imported: true }, { voice_id: 'next' }] }); await tick();
    assert.equal(h.panel().querySelectorAll('input').filter(input => input.type === 'radio').length, 2);
    assert.equal(h.button('loadMore').hidden, true);
});

test('provider switch retires old dialog and international CosyVoice keeps the entry', async () => {
    const h = harness(); h.window.RemoteVoiceManager.openImport();
    h.provider.value = 'cosyvoice_intl'; h.provider.dispatch('change'); await tick();
    h.resolve(0, h.ctx); await tick();
    assert.equal(h.panel(), null);
    assert.equal(h.entry.hidden, false);
    h.provider.value = 'mimo'; h.provider.dispatch('change'); await tick();
    assert.equal(h.entry.hidden, true);
});

test('a rejected overwrite claim retains audio and allows only an explicit retry', async () => {
    const h = harness();
    h.window.RemoteVoiceManager.openOverwrite('voice-local', { provider: 'cosyvoice', remote_voice_id: 'remote' });
    const file = h.panel().querySelectorAll('input')[0], audio = new Blob(['audio']);
    file.files = [audio]; file.dispatch('change');
    h.button('overwrite').dispatch('click');
    h.resolve(0, { ...h.ctx, provider: 'cosyvoice', capabilities: { ...h.ctx.capabilities, overwrite: true } }); await tick();
    h.resolve(1, { success: false, code: 'VOICE_STATE_CHANGED', details: {
        attempt_outcome: 'not_submitted', state_sync: 'unchanged', voice_state: overwriteSnapshot('failed', ['overwrite'])
    } }, 409); await tick();
    assert.ok(h.panel().textContent.includes('voice.remote.voiceStateChanged'));
    assert.equal(h.button('overwrite').hidden, false);
    assert.equal(h.button('overwrite').disabled, false);
    assert.equal(h.button('refreshStatus').hidden, true);
    assert.equal(file.files[0], audio);
    assert.equal(h.refreshes(), 1);
    assert.equal(h.requests.length, 2);
    h.button('overwrite').dispatch('click');
    h.resolve(2, { ...h.ctx, capabilities: { ...h.ctx.capabilities, overwrite: true } }); await tick();
    assert.equal(h.requests[3].options.body.get('audio').size, audio.size);
    h.resolve(3, { success: true, status: 'completed' }); await tick();
    assert.equal(h.requests.filter(request => request.options.method === 'POST').length, 2);
    assert.equal(h.requests.some(request => request.url.includes('overwrite_status')), false);
    assert.ok(h.panel().textContent.includes('voice.remote.completed'));
});

test('uncertain overwrite disables resubmission and offers explicit status refresh', async () => {
    const h = harness(); h.window.RemoteVoiceManager.openOverwrite('voice-local', { provider: 'cosyvoice', remote_voice_id: 'remote' });
    const file = h.panel().querySelectorAll('input')[0]; file.files = [new Blob(['audio'])]; file.dispatch('change');
    h.button('overwrite').dispatch('click'); h.resolve(0, { ...h.ctx, provider: 'cosyvoice', capabilities: { ...h.ctx.capabilities, overwrite: true } }); await tick();
    assert.ok(h.requests[1].url.includes('/voice-local/overwrite'));
    assert.equal(h.requests[1].options.body.get('context_token'), 'snapshot');
    h.resolve(1, { success: false, code: 'UPDATE_OUTCOME_UNKNOWN' }, 504); await tick();
    assert.equal(h.button('overwrite').hidden, true);
    assert.equal(h.button('refreshStatus').hidden, false);
    assert.equal(h.refreshes(), 1);
    h.button('refreshStatus').dispatch('click');
    assert.ok(h.requests[2].url.includes('/overwrite_status?'));
    h.resolve(2, { success: true, status: 'completed' }); await tick();
    assert.equal(h.refreshes(), 2);
    assert.ok(h.panel().textContent.includes('voice.remote.completed'));
    assert.equal(h.requests.filter(request => request.options.method === 'POST').length, 1);
});

test('uncertain overwrite keeps status refresh disabled until library refresh finishes', async () => {
    const h = harness(), library = deferred();
    h.window.loadVoices = () => library.promise;
    h.window.RemoteVoiceManager.openOverwrite('voice-local', { provider: 'cosyvoice', remote_voice_id: 'remote' });
    const file = h.panel().querySelectorAll('input')[0]; file.files = [new Blob(['audio'])]; file.dispatch('change');
    h.button('overwrite').dispatch('click');
    h.resolve(0, { ...h.ctx, provider: 'cosyvoice', capabilities: { ...h.ctx.capabilities, overwrite: true } }); await tick();
    h.resolve(1, { success: false, code: 'UPDATE_OUTCOME_UNKNOWN' }, 504); await tick();
    assert.equal(h.button('refreshStatus').disabled, true);
    library.resolve(); await tick();
    assert.equal(h.button('refreshStatus').disabled, false);
});

test('Escape during IME composition keeps modal, regular Escape cleans listeners', async () => {
    const h = harness(); h.window.RemoteVoiceManager.openImport();
    const event = { key: 'Escape', isComposing: true, preventDefault() {}, stopPropagation() {} };
    h.document.dispatch('keydown', event); assert.ok(h.panel());
    h.document.dispatch('keydown', { ...event, isComposing: false }); assert.equal(h.panel(), null);
    assert.equal(h.document.listeners.keydown.length, 0);
    h.resolve(0, h.ctx); await tick();
});

test('manual entry before context arrives preserves typed input and renders required metadata', async () => {
    const h = harness(); h.window.RemoteVoiceManager.openImport();
    h.button('manualEntry').dispatch('click');
    const inputs = h.panel().querySelectorAll('input'); inputs[0].value = 'TypedEarly'; inputs[1].value = 'Name';
    h.resolve(0, h.ctx);
    h.resolve(1, { ...h.ctx, required_fields: [{ key: 'clone_model', required: true, default_value: 'cosyvoice-v3-plus' }] });
    await tick();
    const fields = h.panel().querySelectorAll('input');
    assert.equal(fields[0].value, 'TypedEarly'); assert.equal(fields[1].value, 'Name');
    assert.equal(fields[2].value, 'cosyvoice-v3-plus');
    h.button('import').dispatch('click');
    assert.equal(JSON.parse(h.requests[2].options.body).metadata.clone_model, 'cosyvoice-v3-plus');
    h.resolve(2, { success: true, verification: 'verified' }); await tick();
    assert.ok(h.panel().textContent.includes('voice.remote.imported'));
});

test('context conflict preserves manual input and does not silently resubmit', async () => {
    const h = harness(); h.window.RemoteVoiceManager.openImport(); h.resolve(0, h.ctx); await tick();
    h.button('manualEntry').dispatch('click');
    const input = h.panel().querySelectorAll('input')[0]; input.value = 'MyVoice'; input.dispatch('input');
    h.button('import').dispatch('click');
    h.resolve(2, { success: false, code: 'CONTEXT_CHANGED' }, 409); await tick();
    assert.equal(h.panel().querySelectorAll('input')[0].value, 'MyVoice');
    assert.ok(h.panel().textContent.includes('voice.remote.contextChanged'));
    assert.equal(h.requests.length, 3);
    assert.equal(h.refreshes(), 0);
    h.resolve(1, { success: true, voices: [] }); await tick();
});

test('list query failure leaves manual import available with a specific permission error', async () => {
    const h = harness(); h.window.RemoteVoiceManager.openImport(); h.resolve(0, h.ctx); await tick();
    h.resolve(1, { success: false, code: 'PERMISSION_DENIED' }, 403); await tick();
    assert.ok(h.panel().textContent.includes('voice.remote.permissionDenied'));
    assert.equal(h.button('manualEntry').disabled, false);
    h.button('manualEntry').dispatch('click');
    assert.ok(h.panel().querySelectorAll('input').some(input => input.name === 'remote_voice_id'));
    h.window.RemoteVoiceManager.close();
});

test('tutorial waits for visible modal removal once and releases observer on teardown', () => {
    const tutorial = fs.readFileSync(path.join(__dirname, '../../static/tutorial/core/page-tutorial-manager.js'), 'utf8');
    const start = tutorial.indexOf('        deferUntilModalCloses() {');
    const end = tutorial.indexOf('        handleStepHighlighted() {');
    assert.ok(start >= 0, 'Tutorial modal deferral method must exist');
    assert.ok(end > start, 'Tutorial method boundary must follow modal deferral');
    const method = tutorial.slice(start, end);
    let modalVisible = true, resumed = 0, observers = 0, callback;
    const listeners = {};
    const document = { visibilityState: 'visible', body: {}, querySelectorAll: () => modalVisible ? [{ getClientRects: () => [{}] }] : [],
        addEventListener: (key, fn) => { listeners[key] = fn; }, removeEventListener: key => { delete listeners[key]; } };
    const window = { addEventListener: (key, fn) => { listeners[key] = fn; }, removeEventListener: key => { delete listeners[key]; } };
    class Observer { constructor(fn) { callback = fn; observers++; } observe() {} disconnect() { observers--; } }
    const context = vm.createContext({ document, window, MutationObserver: Observer });
    const manager = vm.runInContext('({' + method + '})', context);
    manager.startTutorial = () => { resumed++; };
    assert.equal(manager.deferUntilModalCloses(), true);
    assert.equal(manager.deferUntilModalCloses(), true);
    assert.equal(observers, 1); callback(); assert.equal(resumed, 0);
    modalVisible = false; callback(); assert.equal(resumed, 1); assert.equal(observers, 0);
    callback(); assert.equal(resumed, 1);
    modalVisible = true; assert.equal(manager.deferUntilModalCloses(), true);
    listeners.pagehide(); assert.equal(observers, 0);
    modalVisible = false; callback(); assert.equal(resumed, 1);
    assert.equal(Object.keys(listeners).length, 0);
});

test('a confirmed failed overwrite is reported as failed and cannot be mistaken for pending', async () => {
    const h = harness(); h.window.RemoteVoiceManager.openOverwrite('voice-local', { provider: 'cosyvoice', remote_voice_id: 'remote' });
    const file = h.panel().querySelectorAll('input')[0]; file.files = [new Blob(['audio'])]; file.dispatch('change');
    h.button('overwrite').dispatch('click');
    h.resolve(0, { ...h.ctx, provider: 'cosyvoice', capabilities: { ...h.ctx.capabilities, overwrite: true } }); await tick();
    h.resolve(1, { success: true, status: 'failed' }); await tick();
    assert.ok(h.panel().textContent.includes('voice.remote.failed'));
    assert.equal(h.button('overwrite').hidden, true);
    assert.equal(h.button('refreshStatus').hidden, true);
    assert.equal(h.refreshes(), 1);
});
