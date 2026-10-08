const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const A = 'a'.repeat(32), B = 'b'.repeat(32);
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };

function fixture() {
    const elements = new Map(); const listeners = new Map(); const releases = [];
    for (const id of ['chat-avatar-upload', 'chat-avatar-file-input', 'chat-avatar-save', 'chat-avatar-cancel-edit', 'chat-avatar-restore', 'chat-avatar-custom-status', 'chatAvatarPreviewRefreshButton']) {
        elements.set(id, { hidden: false, disabled: false, focus() {}, click() {}, addEventListener(type, fn) { this[type] = fn; } });
    }
    let identity = { uid: A, identityEpoch: 1 };
    let record = { revision: '0', data_url: null, last_operation_id: null };
    const decodes = [], crops = [], encodes = [], writes = [], reads = [], restores = [];
    const state = {
        getIdentity: () => identity,
        getRecord: () => record,
        getDataUrl: () => record && record.data_url || '',
        getError: () => null,
        getLimits: () => ({ normalized_size: 320, normalized_max_bytes: 1048576 }),
        isCurrent(binding) { return !!identity && binding.uid === identity.uid && binding.identityEpoch === identity.identityEpoch; },
        cancelEdit() {},
        captureEdit() { return { ...identity, baseRevision: record.revision, operationId: 'operation' }; },
        refresh() { const task = deferred(); reads.push(task); return task.promise.then(value => { record = value; return value; }); },
        save(blob, binding) { const task = deferred(); writes.push({ blob, binding, ...task }); return task.promise; },
        restore(binding) { const task = deferred(); restores.push({ binding, ...task }); return task.promise; }
    };
    const window = {
        appChatAvatarState: state,
        appChatAvatar: {
            refreshDisplayedAvatar() {}, cancelModelPreviewCapture() {}, closeUploadCropper() {},
            openUploadCropper(source) { const task = deferred(); crops.push({ source, ...task }); return task.promise; },
            normalizeUploadCrop() { const task = deferred(); encodes.push(task); return task.promise; }
        },
        appChatAvatarImage: {
            decodeFile(file) { const task = deferred(); decodes.push({ file, ...task }); return task.promise; },
            pngBlob(dataUrl) { return new Blob([dataUrl], { type: 'image/png' }); }
        },
        safeT: key => key,
        addEventListener(type, fn) { listeners.set(type, fn); }
    };
    const sourcePath = path.join(__dirname, '../../static/app/app-chat-avatar-editor.js');
    vm.runInNewContext(fs.readFileSync(sourcePath, 'utf8'), {
        window, document: { getElementById: id => elements.get(id) }, Blob
    }, { filename: sourcePath });
    const api = window.appChatAvatarEditor;
    api.initialize();
    async function tick() { for (let i = 0; i < 8; ++i) await Promise.resolve(); }
    function source(id = 1) { return { url: 'blob:' + id, width: 32, height: 32, release() { releases.push(id); } }; }
    async function candidate() {
        const pending = api.chooseFile(new Blob(['png']));
        await tick(); decodes.at(-1).resolve(source()); await tick();
        crops.at(-1).resolve({ cropRect: { x: 0, y: 0, size: 32 } }); await tick();
        encodes.at(-1).resolve('data:image/png;base64,image'); await pending;
    }
    function switchToB() { identity = { uid: B, identityEpoch: 2 }; record = { revision: 'b', data_url: null }; listeners.get('chat-avatar-display-updated')(); }
    return { api, state, window, elements, listeners, releases, decodes, crops, encodes, writes, reads, restores, tick, source, candidate, switchToB, setRecord(value) { record = value; } };
}

test('selection is fenced before first refresh await, so A file cannot become B candidate', async () => {
    const h = fixture(); h.setRecord(null);
    const choose = h.api.chooseFile(new Blob(['A'])); assert.equal(h.reads.length, 1);
    h.switchToB(); h.reads[0].resolve({ revision: 'b', data_url: null }); await choose;
    assert.equal(h.decodes.length, 0); assert.equal(h.api.getState().editing, false);
});

test('cancel during decode retires its result and releases URL after decode finishes', async () => {
    const h = fixture(); const choose = h.api.chooseFile(new Blob(['png'])); await h.tick();
    h.api.cancel(); assert.equal(h.releases.length, 0);
    h.decodes[0].resolve(h.source()); await choose;
    assert.equal(h.crops.length, 0); assert.deepEqual(h.releases, [1]);
});

test('cancel during encoding waits for encoding completion before revoking source URL', async () => {
    const h = fixture(); const choose = h.api.chooseFile(new Blob(['png'])); await h.tick();
    h.decodes[0].resolve(h.source()); await h.tick(); h.crops[0].resolve({ cropRect: { size: 32 } }); await h.tick();
    h.api.cancel(); assert.equal(h.releases.length, 0);
    h.encodes[0].resolve('png'); await choose;
    assert.deepEqual(h.releases, [1]); assert.equal(h.api.getState().ready, false);
});

test('overlapping selections ignore old decode and keep only current crop session', async () => {
    const h = fixture(); const first = h.api.chooseFile(new Blob(['one'])); await h.tick();
    const second = h.api.chooseFile(new Blob(['two'])); await h.tick();
    h.decodes[0].resolve(h.source(1)); await first; assert.equal(h.crops.length, 0);
    h.decodes[1].resolve(h.source(2)); await h.tick(); assert.equal(h.crops.length, 1);
    h.crops[0].resolve(null); await second; assert.deepEqual(h.releases, [1, 2]);
});

test('candidate is separate from confirmed image and failure preserves retryable candidate', async () => {
    const h = fixture(); await h.candidate();
    assert.equal(h.state.getDataUrl(), ''); assert.ok(h.api.getCandidateDataUrl());
    const saving = h.api.save(); assert.equal(h.writes.length, 1);
    const error = new Error('disk full'); error.code = 'chat_avatar_write_failed'; h.writes[0].reject(error); await saving;
    assert.equal(h.api.getState().ready, true); assert.equal(h.api.getState().status, 'saveFailed');
    const retry = h.api.save(); assert.equal(h.writes[1].binding.operationId, h.writes[0].binding.operationId);
    h.writes[1].resolve({}); await retry; assert.equal(h.api.getState().ready, false);
});

test('CAS conflict keeps candidate but requires new editing before overwriting remote version', async () => {
    const h = fixture(); await h.candidate(); const save = h.api.save();
    const error = new Error('conflict'); error.code = 'chat_avatar_conflict'; h.writes[0].reject(error); await save;
    assert.equal(h.api.getState().ready, true); assert.equal(h.elements.get('chat-avatar-save').disabled, true);
    await h.api.save(); assert.equal(h.writes.length, 1);
});

test('unknown outcome requires GET on explicit retry and retains identical operation/base', async () => {
    const h = fixture(); await h.candidate(); const first = h.api.save();
    const error = new Error('uncertain'); error.code = 'chat_avatar_unknown_outcome'; h.writes[0].reject(error); await first;
    const retry = h.api.save(); assert.equal(h.reads.length, 1); assert.equal(h.writes.length, 1);
    h.reads[0].resolve({ revision: '0', data_url: null }); await h.tick();
    assert.equal(h.writes[1].binding.operationId, 'operation'); assert.equal(h.writes[1].binding.baseRevision, '0');
    h.writes[1].resolve({}); await retry;
});

test('late committed operation is confirmed on explicit retry without second PUT', async () => {
    const h = fixture(); await h.candidate(); const first = h.api.save();
    const error = new Error('uncertain'); error.code = 'chat_avatar_unknown_outcome'; h.writes[0].reject(error); await first;
    const retry = h.api.save(); h.reads[0].resolve({ revision: 'saved', data_url: 'png', last_operation_id: 'operation' }); await retry;
    assert.equal(h.writes.length, 1); assert.equal(h.api.getState().editing, false);
});

test('role switch during restore clears busy ownership and late completion cannot affect new editor', async () => {
    const h = fixture(); const restore = h.api.restore(); assert.equal(h.api.getState().busy, true);
    h.switchToB(); assert.equal(h.api.getState().busy, false);
    h.restores[0].resolve({}); await restore; assert.equal(h.api.getState().status, '');
});

test('decoding errors produce local status and no confirmed changes', async () => {
    const h = fixture(); const choose = h.api.chooseFile(new Blob(['corrupt'])); await h.tick();
    const error = new Error('bad image'); error.code = 'chat_avatar_invalid_image'; h.decodes[0].reject(error); await choose;
    assert.equal(h.api.getState().status, 'invalidImage'); assert.equal(h.state.getDataUrl(), '');
});

test('file input resets value even for selecting same file repeatedly', async () => {
    const h = fixture(); const input = h.elements.get('chat-avatar-file-input'); input.files = [new Blob(['png'])]; input.value = 'same.png';
    input.change(); assert.equal(input.value, ''); await h.tick(); h.api.cancel(); h.decodes[0].resolve(h.source()); await h.tick();
    input.value = 'same.png'; input.change(); assert.equal(input.value, ''); await h.tick(); assert.equal(h.decodes.length, 2);
    h.api.cancel(); h.decodes[1].resolve(h.source(2)); await h.tick();
});

test('maintenance and data-root changes remain distinct from version conflict', async () => {
    for (const [code, status, disabled] of [['CLOUDSAVE_WRITE_FENCE_ACTIVE', 'maintenance', false], ['chat_avatar_storage_changed', 'storageChanged', true]]) {
        const h = fixture(); await h.candidate(); const save = h.api.save();
        const error = new Error(code); error.code = code; error.status = 409; h.writes[0].reject(error); await save;
        assert.equal(h.api.getState().status, status); assert.equal(h.elements.get('chat-avatar-save').disabled, disabled);
    }
});

test('uncertain restore is confirmed with GET before retrying and reuses the original operation', async () => {
    const h = fixture(); const first = h.api.restore();
    const error = new Error('uncertain'); error.code = 'chat_avatar_unknown_outcome'; h.restores[0].reject(error); await first;
    const retry = h.api.restore(); assert.equal(h.reads.length, 1); assert.equal(h.restores.length, 1);
    h.reads[0].resolve({ revision: 'cleared', data_url: null, last_operation_id: 'operation' }); await retry;
    assert.equal(h.restores.length, 1); assert.equal(h.api.getState().status, '');
});

test('failed uncertain-save GET does not lose uncertainty or permit a blind later PUT', async () => {
    const h = fixture(); await h.candidate(); const first = h.api.save();
    const error = new Error('uncertain'); error.code = 'chat_avatar_unknown_outcome'; h.writes[0].reject(error); await first;
    const retry = h.api.save(); h.reads[0].reject(new TypeError('offline')); await retry;
    assert.equal(h.api.getState().status, 'unknownOutcome'); assert.equal(h.writes.length, 1);
    const later = h.api.save(); assert.equal(h.reads.length, 2); assert.equal(h.writes.length, 1);
    h.reads[1].resolve({ revision: 'saved', data_url: 'png', last_operation_id: 'operation' }); await later;
});

test('unsupported API is displayed as unavailable rather than as an unset avatar', () => {
    const h = fixture(); h.state.getError = () => ({ code: 'chat_avatar_unavailable' }); h.api.update();
    assert.equal(h.elements.get('chat-avatar-custom-status').textContent, 'chatAvatar.custom.unavailable');
});
