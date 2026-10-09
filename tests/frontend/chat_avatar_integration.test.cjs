const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const root = path.resolve(__dirname, '../..');
const UID_A = 'a'.repeat(32);
const UID_B = 'b'.repeat(32);

function deferred() {
    let resolve, reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
}

function harness() {
    const listeners = new Map();
    const timers = new Map();
    let timerId = 0;
    const window = {
        appState: { incomingAudioEpoch: 0 },
        lanlan_config: { lanlan_name: 'A', model_type: 'live2d' },
        location: { pathname: '/chat' },
        invalidatePendingMusicSearch() {},
        connectWebSocket() {},
        showStatusToast() {},
        addEventListener(type, listener) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(listener);
        },
        removeEventListener(type, listener) {
            listeners.set(type, (listeners.get(type) || []).filter(value => value !== listener));
        },
        dispatchEvent(event) {
            for (const listener of listeners.get(event.type) || []) listener(event);
        }
    };
    const document = {
        readyState: 'loading',
        body: { classList: { add() {} } },
        getElementById() { return null; },
        querySelectorAll() { return []; },
        addEventListener() {}
    };
    const context = vm.createContext({
        window, document, console: { log() {}, info() {}, warn() {}, error() {} },
        Event: class { constructor(type) { this.type = type; } },
        CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
        WebSocket: { OPEN: 1, CLOSING: 2, CLOSED: 3 },
        setTimeout(callback, delay) {
            const id = ++timerId;
            if (delay === 100) queueMicrotask(callback);
            else timers.set(id, { callback, delay });
            return id;
        },
        clearTimeout(id) { timers.delete(id); },
        clearInterval() {},
        cancelAnimationFrame() {},
        fetch: async () => ({ ok: true, json: async () => ({ '猫娘': {
            A: { model_type: 'live2d', _reserved: { character_uid: UID_A } },
            B: { model_type: 'live2d', _reserved: { character_uid: UID_B } }
        } }) })
    });
    return {
        window, context, listeners, timers,
        load(relative) { vm.runInContext(fs.readFileSync(path.join(root, relative), 'utf8'), context, { filename: relative }); }
    };
}

function avatarCalls(h) {
    const calls = [];
    h.window.appChatAvatarState = {
        beginCharacterSwitch(id) { calls.push(['begin', id]); },
        commitCharacterSwitch(id, identity) { calls.push(['commit', id, { ...identity }]); },
        rollbackCharacterSwitch(id) { calls.push(['rollback', id]); }
    };
    return calls;
}

for (const action of ['cancel', 'replace']) {
    test('real editor ' + action + ' retires a save awaiting credentials', async () => {
        const h = harness(); const headers = deferred(); const writes = [];
        h.context.AbortController = AbortController;
        h.context.FormData = FormData;
        h.context.Blob = Blob;
        h.window.crypto = { randomUUID: () => 'review-operation' };
        h.window.setTimeout = setTimeout; h.window.clearTimeout = clearTimeout;
        h.window.nekoLocalMutationSecurity = { getMutationHeaders: () => headers.promise };
        h.window.appChatAvatar = {
            refreshDisplayedAvatar() {}, cancelModelPreviewCapture() {}, closeUploadCropper() {},
            openUploadCropper: async () => ({ cropRect: { size: 1 } }),
            normalizeUploadCrop: async () => 'data:image/png;base64,YQ=='
        };
        h.window.appChatAvatarImage = {
            decodeFile: async () => ({ url: 'blob:review', width: 1, height: 1, release() {} }),
            pngBlob: () => new Blob(['png'])
        };
        h.context.fetch = async (_url, options) => {
            if (options.method) writes.push(options.method);
            return { ok: true, json: async () => ({
                character_uid: UID_A, revision: '0', data_url: null,
                limits: { normalized_size: 320, normalized_max_bytes: 1048576 }
            }) };
        };
        h.load('static/app/app-chat-avatar-state.js');
        h.load('static/app/app-chat-avatar-editor.js');
        await h.window.appChatAvatarState.setIdentity({ uid: UID_A });
        const editor = h.window.appChatAvatarEditor;
        await editor.chooseFile(new Blob(['first']));
        const saving = editor.save();
        if (action === 'cancel') editor.cancel();
        else await editor.chooseFile(new Blob(['replacement']));
        headers.resolve({}); await saving;
        assert.deepEqual(writes, []);
        assert.equal(editor.getState().ready, action === 'replace');
        assert.equal(h.window.appChatAvatarState.getRecord().revision, '0');
    });
}

test('same-model character switch commits authoritative UID after switch succeeds', async () => {
    const h = harness(); const calls = avatarCalls(h);
    h.load('static/app/app-character.js');
    await h.window.handleCatgirlSwitch('B', 'A');
    assert.deepEqual(calls, [['begin', 1], ['commit', 1, { uid: UID_B, name: 'B' }]]);
    assert.equal(h.window.lanlan_config.lanlan_name, 'B');
});

test('failed role fetch restores previous avatar ownership', async () => {
    const h = harness(); const calls = avatarCalls(h);
    h.context.fetch = async () => { throw new Error('offline'); };
    h.load('static/app/app-character.js');
    await h.window.handleCatgirlSwitch('B', 'A');
    assert.deepEqual(calls, [['begin', 1], ['rollback', 1]]);
    assert.equal(h.window.lanlan_config.lanlan_name, 'A');
});

test('watchdog retires pending switch and late response cannot reclaim avatar identity', async () => {
    const h = harness(); const calls = avatarCalls(h); const pending = deferred();
    h.context.fetch = () => pending.promise;
    h.load('static/app/app-character.js');
    const first = h.window.handleCatgirlSwitch('B', 'A');
    const watchdog = [...h.timers.values()].find(timer => timer.delay === 45000);
    assert.ok(watchdog); watchdog.callback();
    assert.deepEqual(calls, [['begin', 1], ['rollback', 1]]);
    h.context.fetch = async () => ({ ok: true, json: async () => ({ '猫娘': {
        B: { model_type: 'live2d', _reserved: { character_uid: UID_B } }
    } }) });
    await h.window.handleCatgirlSwitch('B', 'A');
    pending.resolve({ ok: true, json: async () => { throw new Error('stale body must not be consumed'); } });
    await first;
    assert.deepEqual(calls, [['begin', 1], ['rollback', 1], ['begin', 2], ['commit', 2, { uid: UID_B, name: 'B' }]]);
});

test('display update refreshes existing assistant avatars without changing user messages', () => {
    const h = harness(); const updates = [];
    let current = 'data:image/png;base64,CUSTOM';
    h.window.appChatAvatar = { getCurrentAvatarDataUrl: () => current };
    h.window.reactChatWindowHost = {
        getState: () => ({ messages: [{ id: 'assistant', role: 'assistant' }, { id: 'user', role: 'user' }] }),
        updateMessage: (id, patch) => updates.push([id, { ...patch }])
    };
    h.load('static/app/app-chat-adapter.js');
    h.window.dispatchEvent({ type: 'chat-avatar-display-updated' });
    assert.equal(updates.length, 1);
    assert.equal(updates[0][0], 'assistant');
    assert.equal(updates[0][1].avatarUrl, current);
    current = '';
    h.window.dispatchEvent({ type: 'chat-avatar-display-updated' });
    assert.equal(updates[1][1].avatarUrl, undefined);
});

test('restore leaves no custom image behind as an assistant message fallback', () => {
    const custom = 'data:image/png;base64,CUSTOM';
    let display = custom; let model = '';
    const I = { _sortKeySeq: 0, state: { messages: [] }, renderWindow() {} };
    const window = {
        __appReactChatWindowParts: I,
        appState: {},
        appChatAvatarState: { isCustomDataUrl: url => url === custom },
        appChatAvatar: { getCurrentAvatarDataUrl: () => display, getModelAvatarDataUrl: () => model }
    };
    vm.runInContext(fs.readFileSync(path.join(root, 'static/app/app-react-chat-window/geometry-and-messages.js'), 'utf8'),
        vm.createContext({ window, console, Date, setTimeout() {}, clearTimeout() {} }));
    // Created while the custom avatar shows and no model capture exists yet.
    const created = I.normalizeMessage({ id: 'assistant', role: 'assistant', avatarUrl: custom }, 1);
    I.state.messages = [created, I.normalizeMessage({ id: 'user', role: 'user', avatarUrl: custom }, 2)];
    assert.equal(I.state.messages[0].avatarUrl, custom);

    display = '';
    I.refreshAssistantAvatarUrls();
    assert.equal(I.state.messages[0].avatarUrl, undefined);
    assert.equal(I.state.messages[1].avatarUrl, custom, 'user messages are left alone');
    // The adapter's own refresh patches avatarUrl through updateMessage's normalizer.
    assert.equal(I.normalizeMessage(Object.assign({}, created, { avatarUrl: undefined }), 1).avatarUrl, undefined);

    // A model capture that existed at creation is what the message falls back to instead.
    model = 'data:image/png;base64,MODEL'; display = custom;
    const withModel = I.normalizeMessage({ id: 'later', role: 'assistant', avatarUrl: custom }, 3);
    assert.equal(withModel.baseAvatarUrl, model);
    display = '';
    I.state.messages = [withModel];
    I.refreshAssistantAvatarUrls();
    assert.equal(I.state.messages[0].avatarUrl, model);
});

test('direct model capture fallback broadcasts only model preview cache', () => {
    const h = harness(); const events = [];
    h.window.__NEKO_MULTI_WINDOW__ = true;
    h.window.__nekoRequestAvatarPreview = () => { throw new Error('cache should avoid IPC'); };
    h.window.appChatAvatar = {
        getCurrentAvatarDataUrl: () => 'data:image/png;base64,CUSTOM',
        getCachedPreview: () => ({ dataUrl: 'data:image/png;base64,MODEL', modelType: 'live2d' })
    };
    const parts = h.window.__appReactChatWindowParts = {
        showToast() {}, getI18nText: (_, fallback) => fallback, dispatchHostEvent() {}
    };
    h.window.addEventListener('chat-avatar-preview-updated', event => events.push(event.detail));
    h.load('static/app/app-react-chat-window/message-bundle-actions-and-prompts.js');
    parts.handleAvatarGeneratorClick();
    assert.equal(events.length, 1);
    assert.equal(events[0].dataUrl, 'data:image/png;base64,MODEL');
});


test('raw socket invalidation is independent of assistant stream gating and ignores retired sockets', () => {
    const h = harness(); const changes = [];
    h.window.t = key => key;
    h.window.location.protocol = 'http:'; h.window.location.host = 'fixture.test';
    h.window.appConst = { HEARTBEAT_INTERVAL: 30000 };
    h.window.appChatAvatarState = { onBackendChanged: message => changes.push({ ...message }) };
    h.window.appState.suppressAssistantStreamUntilNextSession = true;
    h.context.Blob = Blob;
    h.context.WebSocket = class {
        static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        constructor(url) { this.url = url; this.readyState = 0; }
        send() {}
        close() { this.readyState = 3; }
    };
    h.load('static/app/app-websocket.js');
    h.window.connectWebSocket();
    const original = h.window.appState.socket;
    const message = { type: 'chat_avatar_changed', character_uid: UID_A, revision: 'rev1' };
    original.onmessage({ data: JSON.stringify(message) });
    assert.deepEqual(changes, [message]);
    h.window.connectWebSocket();
    original.onmessage({ data: JSON.stringify({ ...message, revision: 'stale' }) });
    assert.equal(changes.length, 1);
    h.window.appState.socket.onmessage({ data: JSON.stringify({ ...message, revision: 'rev2' }) });
    assert.equal(changes[1].revision, 'rev2');
});


test('socket reopen re-reads persistent avatars after missed notifications', async () => {
    const h = harness(); const reasons = [];
    h.window.t = key => key;
    h.window.location.protocol = 'http:'; h.window.location.host = 'fixture.test';
    h.window.appConst = { HEARTBEAT_INTERVAL: 30000 };
    h.window.appChatAvatarState = { refresh: reason => {
        reasons.push(reason);
        return reasons.length === 1 ? Promise.reject(new Error('read unavailable')) : Promise.resolve();
    } };
    h.context.setInterval = () => 42;
    h.context.navigator = {};
    h.context.Blob = Blob;
    h.context.WebSocket = class {
        static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        constructor(url) { this.url = url; this.readyState = 0; }
        send() {}
        close() { this.readyState = 3; }
    };
    h.load('static/app/app-websocket.js');
    h.window.connectWebSocket();
    const original = h.window.appState.socket;
    original.readyState = 1;
    original.onopen();
    original.readyState = 3;
    h.window.connectWebSocket();
    const reopened = h.window.appState.socket;
    reopened.readyState = 1;
    reopened.onopen();
    original.onopen();
    await new Promise(setImmediate);
    assert.deepEqual(reasons, ['reconnect', 'reconnect']);
});


test('postcommit UI failure cannot roll back character and avatar ownership', async () => {
    const h = harness(); const calls = avatarCalls(h);
    h.window.showStatusToast = message => { if (message.startsWith('已切换到')) throw new Error('toast unavailable'); };
    h.load('static/app/app-character.js');
    await h.window.handleCatgirlSwitch('B', 'A');
    assert.deepEqual(calls, [['begin', 1], ['commit', 1, { uid: UID_B, name: 'B' }]]);
    assert.equal(h.window.lanlan_config.lanlan_name, 'B');
});


test('late switch failure restores character name before publishing rollback avatar', async () => {
    const h = harness(); const calls = avatarCalls(h);
    const rollback = h.window.appChatAvatarState.rollbackCharacterSwitch;
    h.window.appChatAvatarState.rollbackCharacterSwitch = id => {
        assert.equal(h.window.lanlan_config.lanlan_name, 'A');
        rollback(id);
    };
    h.window.connectWebSocket = () => {
        assert.equal(h.window.lanlan_config.lanlan_name, 'B');
        throw new Error('reconnect failed');
    };
    h.load('static/app/app-character.js');
    await h.window.handleCatgirlSwitch('B', 'A');
    assert.deepEqual(calls, [['begin', 1], ['rollback', 1]]);
    assert.equal(h.window.lanlan_config.lanlan_name, 'A');
});

function popupHarness() {
    const h = harness();
    const elements = new Map();
    for (const id of ['chat-avatar-upload', 'chat-avatar-file-input', 'chat-avatar-save', 'chat-avatar-cancel-edit',
        'chat-avatar-restore', 'chat-avatar-custom-status', 'chatAvatarPreviewRefreshButton']) {
        elements.set(id, { hidden: false, disabled: false, textContent: '', focus() {}, click() {}, addEventListener() {} });
    }
    h.window.appState.dom = {
        chatAvatarPreviewImage: { hidden: true, removeAttribute(key) { delete this[key]; } },
        chatAvatarPreviewImageShell: { classList: { add() {}, remove() {} } },
        chatAvatarPreviewPlaceholder: { hidden: false },
        chatAvatarPreviewNote: { textContent: '', hidden: false },
        chatAvatarPreviewStatus: { textContent: '' }
    };
    h.context.document.getElementById = id => elements.get(id);
    h.context.Blob = Blob;
    h.window.safeT = key => key;
    h.window.avatarPortrait = { capture: async () => { throw new Error('model render unavailable'); } };
    let custom = '';
    const identity = { uid: UID_A, identityEpoch: 1 };
    h.window.appChatAvatarState = {
        getDataUrl: () => custom,
        getIdentity: () => identity,
        getRecord: () => ({ revision: '0', data_url: custom || null }),
        getError: () => null,
        getLimits: () => ({ normalized_size: 320, normalized_max_bytes: 1048576 }),
        isCurrent: () => true,
        cancelEdit() {},
        captureEdit: () => ({ ...identity, operationId: 'edit', baseRevision: '0' }),
        save: async () => { throw new Error('disk full'); }
    };
    h.load('static/app/app-chat-avatar.js');
    return { ...h, elements, dom: h.window.appState.dom, setCustom(value) { custom = value; } };
}

test('custom popup hides model errors and restores their latest status after clearing the override', async () => {
    const h = popupHarness(); const core = h.window.appChatAvatar;
    await core.showPopup();
    assert.equal(h.dom.chatAvatarPreviewNote.textContent, 'model render unavailable');
    assert.equal(h.dom.chatAvatarPreviewStatus.textContent, 'chat.avatarPreviewFailed');
    h.setCustom('data:image/png;base64,CUSTOM'); core.refreshDisplayedAvatar();
    assert.equal(h.dom.chatAvatarPreviewImage.src, 'data:image/png;base64,CUSTOM');
    assert.equal(h.dom.chatAvatarPreviewNote.hidden, true);
    assert.equal(h.dom.chatAvatarPreviewStatus.textContent, 'chatAvatar.custom.custom');
    await core.showPopup(null, { forceRefresh: true });
    assert.equal(h.dom.chatAvatarPreviewNote.hidden, true);
    assert.equal(h.dom.chatAvatarPreviewStatus.textContent, 'chatAvatar.custom.custom');
    h.setCustom(''); core.refreshDisplayedAvatar();
    assert.equal(h.dom.chatAvatarPreviewNote.hidden, false);
    assert.equal(h.dom.chatAvatarPreviewNote.textContent, 'model render unavailable');
    assert.equal(h.dom.chatAvatarPreviewStatus.textContent, 'chat.avatarPreviewFailed');
});

test('candidate popup suppresses model notes but keeps backend save errors and restores model status on cancel', async () => {
    const h = popupHarness(); const core = h.window.appChatAvatar;
    await core.showPopup();
    h.window.appChatAvatarImage = {
        decodeFile: async () => ({ url: 'blob:source', width: 32, height: 32, release() {} }),
        pngBlob: () => new Blob(['png'])
    };
    core.openUploadCropper = async () => ({ cropRect: { x: 0, y: 0, size: 32 } });
    core.normalizeUploadCrop = async () => 'data:image/png;base64,CANDIDATE';
    h.load('static/app/app-chat-avatar-editor.js');
    const editor = h.window.appChatAvatarEditor; editor.initialize();
    await editor.chooseFile(new Blob(['source']));
    assert.equal(h.dom.chatAvatarPreviewNote.hidden, true);
    assert.equal(h.dom.chatAvatarPreviewStatus.textContent, 'chatAvatar.custom.ready');
    await editor.save();
    assert.equal(h.elements.get('chat-avatar-custom-status').textContent, 'chatAvatar.custom.saveFailed');
    assert.equal(h.elements.get('chat-avatar-custom-status').hidden, false);
    assert.equal(h.dom.chatAvatarPreviewImage.src, 'data:image/png;base64,CANDIDATE');
    assert.equal(h.dom.chatAvatarPreviewNote.hidden, true);
    editor.cancel();
    assert.equal(h.dom.chatAvatarPreviewNote.hidden, false);
    assert.equal(h.dom.chatAvatarPreviewNote.textContent, 'model render unavailable');
    assert.equal(h.dom.chatAvatarPreviewStatus.textContent, 'chat.avatarPreviewFailed');
});

test('model IPC feedback preserves custom title and becomes visible after restore', async () => {
    const h = popupHarness(); const core = h.window.appChatAvatar;
    await core.showPopup();
    h.setCustom('data:image/png;base64,CUSTOM'); core.refreshDisplayedAvatar();
    delete h.window.avatarPortrait;
    h.dom.chatAvatarPreviewCard = { hidden: false };
    core.setExternalAvatar('data:image/png;base64,MODEL', 'vrm');
    assert.equal(h.dom.chatAvatarPreviewImage.src, 'data:image/png;base64,CUSTOM');
    assert.equal(h.dom.chatAvatarPreviewNote.hidden, true);
    assert.equal(h.dom.chatAvatarPreviewStatus.textContent, 'chatAvatar.custom.custom');
    h.setCustom(''); core.refreshDisplayedAvatar();
    assert.equal(h.dom.chatAvatarPreviewImage.src, 'data:image/png;base64,MODEL');
    assert.equal(h.dom.chatAvatarPreviewNote.hidden, false);
    assert.equal(h.dom.chatAvatarPreviewNote.textContent, 'chat.avatarPreviewReadyHint');
    assert.equal(h.dom.chatAvatarPreviewStatus.textContent, 'chat.avatarPreviewReady · VRM');
});

test('upload crop title remains temporary and cancel restores the model failure snapshot', async () => {
    const h = popupHarness(); const core = h.window.appChatAvatar;
    await core.showPopup();
    function element() {
        return {
            style: {}, offsetWidth: 200, offsetHeight: 40, hidden: false,
            classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
            addEventListener() {}, removeEventListener() {}, setAttribute() {}, removeAttribute() {}, contains: () => false
        };
    }
    const controls = element(); const popup = element(); popup.querySelector = () => controls;
    h.dom.chatAvatarPreviewCard = popup;
    for (const id of ['avatar-cropper-wrap', 'avatar-cropper-img', 'avatar-cropper-mask', 'avatar-cropper-box',
        'avatar-cropper-retake', 'avatar-cropper-cancel', 'avatar-cropper-save']) h.elements.set(id, element());
    h.context.document.removeEventListener = () => {};
    h.context.requestAnimationFrame = () => 1;
    h.window.innerWidth = 1024; h.window.innerHeight = 768;
    let editing = true;
    h.window.appChatAvatarEditor = { getState: () => ({ editing }), getCandidateDataUrl: () => '' };
    const cropping = core.openUploadCropper({ url: 'blob:upload', width: 32, height: 32 });
    assert.equal(h.dom.chatAvatarPreviewStatus.textContent, 'chat.avatarCropperTitle');
    core.closeUploadCropper();
    assert.equal(await cropping, null);
    editing = false; core.refreshDisplayedAvatar();
    assert.equal(h.dom.chatAvatarPreviewNote.hidden, false);
    assert.equal(h.dom.chatAvatarPreviewNote.textContent, 'model render unavailable');
    assert.equal(h.dom.chatAvatarPreviewStatus.textContent, 'chat.avatarPreviewFailed');
});
