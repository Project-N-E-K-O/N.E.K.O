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

function harness() {
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
    const context = vm.createContext({ window, document, AbortController, DOMException, URLSearchParams, FormData, setTimeout, clearTimeout,
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

test('uncertain overwrite disables resubmission and offers explicit status refresh', async () => {
    const h = harness(); h.window.RemoteVoiceManager.openOverwrite('voice-local', { provider: 'cosyvoice', remote_voice_id: 'remote' });
    const file = h.panel().querySelectorAll('input')[0]; file.files = [new Blob(['audio'])]; file.dispatch('change');
    h.button('overwrite').dispatch('click'); h.resolve(0, { ...h.ctx, provider: 'cosyvoice', capabilities: { ...h.ctx.capabilities, overwrite: true } }); await tick();
    assert.ok(h.requests[1].url.includes('/voice-local/overwrite'));
    assert.equal(h.requests[1].options.body.get('context_token'), 'snapshot');
    h.resolve(1, { success: false, code: 'UPDATE_OUTCOME_UNKNOWN' }, 504); await tick();
    assert.equal(h.button('overwrite').hidden, true);
    assert.equal(h.button('refreshStatus').hidden, false);
    h.button('refreshStatus').dispatch('click');
    assert.ok(h.requests[2].url.includes('/overwrite_status?'));
    h.resolve(2, { success: true, status: 'completed' }); await tick();
    assert.equal(h.refreshes(), 1);
    assert.ok(h.panel().textContent.includes('voice.remote.completed'));
    assert.equal(h.requests.filter(request => request.options.method === 'POST').length, 1);
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
    const method = tutorial.slice(tutorial.indexOf('        deferUntilModalCloses() {'), tutorial.indexOf('        handleStepHighlighted() {'));
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
