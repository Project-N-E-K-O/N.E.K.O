const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const A = 'a'.repeat(32);
const B = 'b'.repeat(32);
const limits = { source_max_bytes: 10485760, source_max_pixels: 20000000, normalized_size: 320, normalized_max_bytes: 1048576 };
const row = (uid, revision = '0', image = null, op = null) => ({
    schema_version: 1, character_uid: uid, revision, data_url: image, last_operation_id: op, limits
});
const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };

function harness() {
    const pending = [];
    const events = [];
    const timers = new Map();
    const listeners = new Map();
    let serial = 0;
    const window = {
        crypto: { randomUUID: () => 'operation-' + (++serial) },
        setTimeout(fn) { const id = ++serial; timers.set(id, fn); return id; },
        clearTimeout(id) { timers.delete(id); },
        addEventListener(type, fn) { listeners.set(type, fn); },
        dispatchEvent(event) { events.push(event); },
        nekoLocalMutationSecurity: { getMutationHeaders: async () => ({ 'X-CSRF-Token': 'test' }) },
        lanlan_config: { lanlan_name: 'Alice' }
    };
    const context = vm.createContext({
        window, document: { addEventListener() {}, visibilityState: 'visible' },
        CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
        AbortController, FormData, Blob,
        fetch(url, options) { const task = deferred(); pending.push({ url, options, ...task }); return task.promise; }
    });
    const sourcePath = path.join(__dirname, '../../static/app/app-chat-avatar-state.js');
    vm.runInContext(fs.readFileSync(sourcePath, 'utf8'), context, { filename: sourcePath });
    const api = window.appChatAvatarState;
    async function settle() { for (let i = 0; i < 8; ++i) await Promise.resolve(); }
    async function identify(uid = A) {
        const task = api.setIdentity({ uid, name: 'Alice' });
        pending.at(-1).resolve(response(row(uid)));
        await task;
    }
    return { api, window, pending, events, timers, listeners, settle, identify };
}

test('unset is an explicit successful record and limits come from backend', async () => {
    const h = harness(); await h.identify();
    assert.equal(h.api.getDataUrl(), '');
    assert.equal(h.api.getRecord().revision, '0');
    assert.equal(h.api.getLimits().normalized_size, 320);
    assert.ok(h.events.every(e => e.type === 'chat-avatar-display-updated'));
});

test('identity fence rejects old GET when switching to another UID sharing a model', async () => {
    const h = harness();
    const a = h.api.setIdentity({ uid: A });
    const b = h.api.setIdentity({ uid: B });
    h.pending[1].resolve(response(row(B, 'b1', 'data:image/png;base64,B'))); await b;
    h.pending[0].resolve(response(row(A, 'a1', 'data:image/png;base64,A'))); await a;
    assert.equal(h.api.getIdentity().uid, B);
    assert.equal(h.api.getDataUrl(), 'data:image/png;base64,B');
});

test('read generation ignores earlier GET completion and notification revisions remain opaque', async () => {
    const h = harness(); await h.identify();
    const first = h.api.refresh(); const second = h.api.refresh();
    h.pending[2].resolve(response(row(A, 'new', 'new'))); await second;
    h.pending[1].resolve(response(row(A, 'old', 'old'))); await first;
    assert.equal(h.api.getDataUrl(), 'new');
});

test('switch rollback restores last committed UID and ignores stale attempt', async () => {
    const h = harness(); await h.identify();
    h.api.beginCharacterSwitch(10);
    assert.equal(h.api.getIdentity(), null);
    h.api.beginCharacterSwitch(11);
    h.api.rollbackCharacterSwitch(10);
    assert.equal(h.api.getIdentity(), null);
    h.api.rollbackCharacterSwitch(11);
    assert.equal(h.api.getIdentity().uid, A);
    h.pending.at(-1).resolve(response(row(A))); await h.settle();
});

test('successful switch commits supplied UID independent of model loading', async () => {
    const h = harness(); await h.identify();
    h.api.beginCharacterSwitch(1);
    const done = h.api.commitCharacterSwitch(1, { uid: B, name: 'Bob' });
    h.pending.at(-1).resolve(response(row(B, 'b', 'B'))); await done;
    assert.equal(h.api.getIdentity().uid, B);
    assert.equal(h.api.getDataUrl(), 'B');
});

test('404 unsupported route and missing role do not become unset or destroy confirmed image', async () => {
    const h = harness(); await h.identify();
    const read = h.api.refresh(); h.pending.at(-1).resolve(response(row(A, '1', 'kept'))); await read;
    const unavailable = h.api.refresh(); h.pending.at(-1).resolve(response({ detail: 'Not Found' }, 404));
    await assert.rejects(unavailable, { code: 'chat_avatar_unavailable' });
    assert.equal(h.api.getDataUrl(), 'kept');
    const missing = h.api.refresh(); h.pending.at(-1).resolve(response({ code: 'chat_avatar_character_not_found' }, 404));
    await assert.rejects(missing, { code: 'chat_avatar_character_not_found' });
});

test('save uses CSRF headers and multipart CAS fields, accepted image only after response', async () => {
    const h = harness(); await h.identify(); const binding = h.api.captureEdit();
    const save = h.api.save(new Blob(['png'], { type: 'image/png' }), binding); await h.settle();
    assert.equal(h.api.getDataUrl(), '');
    const request = h.pending.at(-1);
    assert.equal(request.options.headers['X-CSRF-Token'], 'test');
    assert.equal(request.options.headers['Content-Type'], undefined);
    assert.equal(request.options.body.get('base_revision'), '0');
    assert.equal(request.options.body.get('operation_id'), binding.operationId);
    request.resolve(response(row(A, 'saved', 'new', binding.operationId))); await save;
    assert.equal(h.api.getDataUrl(), 'new');
});

test('late save result cannot display on another UID', async () => {
    const h = harness(); await h.identify(); const binding = h.api.captureEdit();
    const save = h.api.save(new Blob(['png']), binding); await h.settle(); const write = h.pending.at(-1);
    await h.identify(B);
    write.resolve(response(row(A, 'saved', 'A', binding.operationId))); await save;
    assert.equal(h.api.getIdentity().uid, B);
    assert.equal(h.api.getDataUrl(), '');
});

test('header await is fenced when role changes before write', async () => {
    const h = harness(); await h.identify(); const binding = h.api.captureEdit(); const headers = deferred();
    h.window.nekoLocalMutationSecurity.getMutationHeaders = () => headers.promise;
    const save = h.api.save(new Blob(['png']), binding);
    await h.identify(B); const count = h.pending.length;
    headers.resolve({});
    await assert.rejects(save, { code: 'chat_avatar_stale_edit' });
    assert.equal(h.pending.length, count);
});

test('cancel during credential preparation prevents a not-yet-submitted write', async () => {
    const h = harness(); await h.identify(); const binding = h.api.captureEdit(); const headers = deferred();
    h.window.nekoLocalMutationSecurity.getMutationHeaders = () => headers.promise;
    const saving = h.api.save(new Blob(['png']), binding);
    h.api.cancelEdit(binding);
    headers.resolve({});
    await assert.rejects(saving, { code: 'chat_avatar_stale_edit' });
    assert.equal(h.pending.filter(p => p.options.method).length, 0);
    await assert.rejects(h.api.save(new Blob(['png']), binding), { code: 'chat_avatar_stale_edit' });
});

test('cancel after transport submission still confirms the authoritative outcome', async () => {
    const h = harness(); await h.identify(); const binding = h.api.captureEdit();
    const saving = h.api.save(new Blob(['png']), binding); await h.settle();
    h.api.cancelEdit(binding);
    h.pending.at(-1).resolve(response(row(A, 'saved', 'confirmed', binding.operationId)));
    await saving;
    assert.equal(h.api.getDataUrl(), 'confirmed');
});

test('restore uses JSON CAS and keeps tombstone revision', async () => {
    const h = harness(); await h.identify(); const binding = h.api.captureEdit();
    const restore = h.api.restore(binding); await h.settle(); const request = h.pending.at(-1);
    assert.equal(request.options.method, 'DELETE');
    assert.equal(request.options.headers['Content-Type'], 'application/json');
    assert.equal(JSON.parse(request.options.body).base_revision, '0');
    request.resolve(response(row(A, 'tombstone', null, binding.operationId))); await restore;
    assert.equal(h.api.getRecord().revision, 'tombstone');
});

test('save response overtaken by a newer read is confirmed with another GET', async () => {
    const h = harness(); await h.identify(); const binding = h.api.captureEdit();
    const save = h.api.save(new Blob(['png']), binding); await h.settle(); const write = h.pending.at(-1);
    const read = h.api.refresh(); h.pending.at(-1).resolve(response(row(A, 'newer', 'newer', 'another'))); await read;
    write.resolve(response(row(A, 'saved', 'stale', binding.operationId))); await h.settle();
    assert.equal(h.pending.at(-1).options.method, undefined);
    h.pending.at(-1).resolve(response(row(A, 'newer', 'newer', 'another')));
    await assert.rejects(save, { code: 'chat_avatar_conflict' });
    assert.equal(h.api.getDataUrl(), 'newer');
});

test('AbortError with DOM numeric code reconciles committed result instead of replay', async () => {
    const h = harness(); await h.identify(); const binding = h.api.captureEdit();
    const save = h.api.save(new Blob(['png']), binding); await h.settle();
    h.pending.at(-1).reject(new DOMException('aborted', 'AbortError')); await h.settle();
    const reconcile = h.pending.at(-1); assert.equal(reconcile.options.method, undefined);
    reconcile.resolve(response(row(A, 'committed', 'image', binding.operationId))); await save;
    assert.equal(h.api.getDataUrl(), 'image');
    assert.equal(h.pending.filter(p => p.options.method === 'PUT').length, 1);
});

test('uncertain network failure reads once and never automatically resubmits', async () => {
    const h = harness(); await h.identify(); const binding = h.api.captureEdit();
    const save = h.api.save(new Blob(['png']), binding); await h.settle();
    h.pending.at(-1).reject(new TypeError('offline')); await h.settle();
    h.pending.at(-1).resolve(response(row(A)));
    await assert.rejects(save, { code: 'chat_avatar_unknown_outcome' });
    assert.equal(h.pending.filter(p => p.options.method === 'PUT').length, 1);
});

test('conflict rereads authoritative record and storage failure leaves confirmed state untouched', async () => {
    const h = harness(); await h.identify(); const binding = h.api.captureEdit();
    const save = h.api.save(new Blob(['png']), binding); await h.settle();
    h.pending.at(-1).resolve(response({ code: 'chat_avatar_conflict' }, 409)); await h.settle();
    h.pending.at(-1).resolve(response(row(A, 'other', 'other')));
    await assert.rejects(save, { code: 'chat_avatar_conflict' });
    const fail = h.api.save(new Blob(['png']), h.api.captureEdit()); await h.settle();
    h.pending.at(-1).resolve(response({ code: 'chat_avatar_write_failed' }, 503));
    await assert.rejects(fail, { code: 'chat_avatar_write_failed' });
    assert.equal(h.api.getDataUrl(), 'other');
});

test('notification for another role is ignored and current revision avoids redundant GET', async () => {
    const h = harness(); await h.identify(); const count = h.pending.length;
    h.api.onBackendChanged({ character_uid: B, revision: '1' });
    h.api.onBackendChanged({ character_uid: A, revision: '0' });
    assert.equal(h.pending.length, count);
    h.api.onBackendChanged({ character_uid: A, revision: 'changed' });
    assert.equal(h.pending.length, count + 1);
    h.pending.at(-1).resolve(response(row(A, 'changed'))); await h.settle();
});

test('initialize waits for config, resolves stable UID, and focus/pageshow supplement notifications', async () => {
    const h = harness(); const configReady = deferred(); h.window.pageConfigReady = configReady.promise;
    h.api.initialize(); h.api.initialize(); await h.settle(); assert.equal(h.pending.length, 0);
    configReady.resolve({}); await h.settle(); assert.equal(h.pending[0].url, '/api/characters');
    h.pending[0].resolve(response({ '猫娘': { Alice: { _reserved: { character_uid: A } } } })); await h.settle();
    h.pending[1].resolve(response(row(A, 'saved', 'custom'))); await h.settle();
    assert.equal(h.api.getIdentity().uid, A); assert.equal(h.api.getDataUrl(), 'custom');
    h.listeners.get('focus')(); h.pending.at(-1).resolve(response(row(A, 'saved', 'custom'))); await h.settle();
    h.listeners.get('pageshow')(); h.pending.at(-1).resolve(response(row(A, 'saved', 'custom'))); await h.settle();
    assert.equal(h.pending.filter(p => p.url === '/api/characters').length, 1);
});

test('failed character bootstrap is unavailable and focus retries without deleting any avatar', async () => {
    const h = harness(); h.api.initialize(); await h.settle();
    h.pending[0].resolve(response({ error: 'denied' }, 403)); await h.settle();
    assert.equal(h.api.getIdentity(), null); assert.equal(h.api.getError().code, 'chat_avatar_read_failed');
    assert.throws(() => h.api.captureEdit(), { code: 'chat_avatar_unavailable' });
    h.listeners.get('focus')(); await h.settle();
    h.pending[1].resolve(response({ '猫娘': { Alice: { _reserved: { character_uid: A } } } })); await h.settle();
    h.pending[2].resolve(response(row(A))); await h.settle(); assert.equal(h.api.getIdentity().uid, A);
});

test('bootstrap role name missing never reuses another role UID', async () => {
    const h = harness(); h.api.initialize(); await h.settle();
    h.pending[0].resolve(response({ '猫娘': { Bob: { _reserved: { character_uid: B } } } })); await h.settle();
    assert.equal(h.api.getIdentity(), null); assert.equal(h.api.getError().code, 'chat_avatar_character_not_found');
});

test('failed timeout reconciliation stays uncertain so user retry requires another read', async () => {
    const h = harness(); await h.identify(); const binding = h.api.captureEdit();
    const save = h.api.save(new Blob(['png']), binding); await h.settle();
    h.pending.at(-1).reject(new DOMException('abort', 'AbortError')); await h.settle();
    h.pending.at(-1).reject(new TypeError('still offline'));
    await assert.rejects(save, { code: 'chat_avatar_unknown_outcome' });
    assert.equal(h.pending.filter(p => p.options.method === 'PUT').length, 1);
});

test('request deadline aborts signal and malformed successful body is rejected', async () => {
    const h = harness(); await h.identify(); const read = h.api.refresh();
    for (const timer of h.timers.values()) timer();
    assert.equal(h.pending.at(-1).options.signal.aborted, true);
    h.pending.at(-1).resolve(response({ character_uid: B, data_url: null, revision: '1' }));
    await assert.rejects(read, { code: 'chat_avatar_invalid_response' });
    assert.equal(h.api.getRecord().revision, '0');
});

test('remote HTTP crypto without secure-context randomUUID still creates strong operation identity', async () => {
    const h = harness(); await h.identify();
    h.window.crypto = { getRandomValues(bytes) { for (let i = 0; i < bytes.length; ++i) bytes[i] = i; return bytes; } };
    const binding = h.api.captureEdit(); assert.equal(binding.operationId, '000102030405060708090a0b0c0d0e0f');
    const save = h.api.save(new Blob(['png']), binding); await h.settle();
    h.pending.at(-1).resolve(response(row(A, 'new', 'png', binding.operationId))); await save;
    assert.equal(h.api.getDataUrl(), 'png');
});

test('successful headers followed by body timeout reconcile actual commit', async () => {
    const h = harness(); await h.identify(); const binding = h.api.captureEdit();
    const save = h.api.save(new Blob(['png']), binding); await h.settle();
    h.pending.at(-1).resolve({ ok: true, status: 200, json: async () => { throw new DOMException('body abort', 'AbortError'); } });
    await h.settle(); assert.equal(h.pending.at(-1).options.method, undefined);
    h.pending.at(-1).resolve(response(row(A, 'saved', 'confirmed', binding.operationId))); await save;
    assert.equal(h.api.getDataUrl(), 'confirmed');
});

test('successful headers followed by malformed body reconcile before reporting unknown result', async () => {
    const h = harness(); await h.identify(); const binding = h.api.captureEdit();
    const save = h.api.save(new Blob(['png']), binding); await h.settle();
    h.pending.at(-1).resolve({ ok: true, status: 200, json: async () => { throw new SyntaxError('bad json'); } });
    await h.settle(); assert.equal(h.pending.at(-1).options.method, undefined);
    h.pending.at(-1).resolve(response(row(A)));
    await assert.rejects(save, { code: 'chat_avatar_unknown_outcome' });
});

test('reconnect can recover a failed initial identity lookup without waiting for window activation', async () => {
    const h = harness(); h.api.initialize(); await h.settle(); h.pending[0].resolve(response({}, 503)); await h.settle();
    const recovery = h.api.refresh('reconnect'); await h.settle();
    h.pending[1].resolve(response({ '猫娘': { Alice: { _reserved: { character_uid: A } } } })); await h.settle();
    h.pending[2].resolve(response(row(A))); await recovery;
    assert.equal(h.api.getIdentity().uid, A);
});

test('invalid committed UID becomes unavailable rather than a permanent loading state', async () => {
    const h = harness(); await h.identify(); h.api.beginCharacterSwitch(5);
    await h.api.commitCharacterSwitch(5, { uid: '', name: 'Older backend role' });
    assert.equal(h.api.getIdentity(), null); assert.equal(h.api.getError().code, 'chat_avatar_unavailable');
    assert.throws(() => h.api.captureEdit(), { code: 'chat_avatar_unavailable' });
});

test('initial character lookup has a deadline and can recover on focus after timeout', async () => {
    const h = harness(); h.api.initialize(); await h.settle();
    const lookup = h.pending[0];
    assert.ok(lookup.options.signal, 'bootstrap lookup must have an abortable deadline');
    lookup.options.signal.addEventListener('abort', () => lookup.reject(new DOMException('abort', 'AbortError')), { once: true });
    for (const timeout of h.timers.values()) timeout();
    await h.settle();
    assert.equal(h.api.getIdentity(), null);
    assert.equal(h.api.getError().code, 'chat_avatar_timeout');
    assert.equal(h.timers.size, 0);
    h.listeners.get('focus')(); await h.settle();
    h.pending[1].resolve(response({ '猫娘': { Alice: { _reserved: { character_uid: A } } } })); await h.settle();
    h.pending[2].resolve(response(row(A))); await h.settle();
    assert.equal(h.api.getIdentity().uid, A);
    assert.equal(h.api.getError(), null);
    assert.equal(h.timers.size, 0);
});
