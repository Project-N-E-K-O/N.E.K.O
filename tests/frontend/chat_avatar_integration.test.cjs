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
        WebSocket: { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 },
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



test('same-current VRM event recovers when the configured VRM instance is missing', async () => {
    const h = harness(); const calls = avatarCalls(h);
    h.window.location.pathname = '/';
    h.window.lanlan_config = {
        lanlan_name: 'A',
        model_type: 'live3d',
        live3d_sub_type: 'vrm'
    };
    h.window.vrmManager = { currentModel: null };
    h.context.fetch = async () => { throw new Error('stop after dedupe gate'); };
    h.load('static/app/app-character.js');

    await h.window.handleCatgirlSwitch('A', 'B');

    assert.deepEqual(calls, [
        ['begin', 1],
        ['rollback', 1]
    ], 'missing VRM must bypass same-current dedupe and enter recovery flow');
});

test('same-current receipt does not restart an active cold VRM load, but retries after it fails', async () => {
    const h = harness(), calls = avatarCalls(h);
    h.window.location.pathname = '/';
    h.window.lanlan_config = { lanlan_name: 'A', model_type: 'live3d', live3d_sub_type: 'vrm' };
    h.window.vrmManager = { currentModel: null, _isLoadingModel: true };
    h.context.fetch = async () => { throw new Error('stop after dedupe gate'); };
    h.load('static/app/app-character.js');
    await h.window.handleCatgirlSwitch('A', 'B', { switchRevision: 10 });
    assert.deepEqual(calls, []);
    h.window.vrmManager._isLoadingModel = false;
    await h.window.handleCatgirlSwitch('A', 'B', { switchRevision: 10 });
    assert.deepEqual(calls, [['begin', 1], ['rollback', 1]]);
});

for (const incoming of ['A', 'B']) {
    test('VRM startup awaiting metadata yields only to a different committed character: ' + incoming, async () => {
        const h = harness(), w = h.window, calls = avatarCalls(h), entered = deferred(), release = deferred();
        w.location.pathname = '/'; w.location.href = 'https://example.test/'; h.context.document.querySelector = () => null;
        h.context.requestAnimationFrame = () => 0; h.context.URL = URL; w.vrmModuleLoaded = true; w.VRMCore = {};
        w.lanlan_config = { lanlan_name: 'A', model_type: 'live3d', live3d_sub_type: 'vrm', switch_revision: 810 };
        w.vrmModel = '/static/vrm/A.vrm';
        const role = n => ({ model_type: 'live3d', live3d_sub_type: 'vrm', vrm: '/static/vrm/' + n + '.vrm',
            _reserved: { character_uid: n === 'A' ? UID_A : UID_B } });
        h.context.fetch = async url => {
            if (url.startsWith('/api/model/vrm/config')) { entered.resolve(); await release.promise; }
            return { ok: true, json: async () => url === '/api/characters' ? { '猫娘': { A: role('A'), B: role('B') } } : {} };
        };
        h.load('static/vrm/vrm-manager.js'); h.load('static/vrm/vrm-init.js');
        const manager = Object.create(w.VRMManager.prototype), loads = [];
        Object.assign(manager, { currentModel: null, _pendingModelLoadCount: 0, _activeLoadToken: 0,
            initThreeJS: async () => {}, pauseRendering() {}, resumeRendering() {}, cleanupUI() {},
            async _loadModelInternal(url, options, token) {
                loads.push(url.split('/').pop());
                if (this._isLoadTokenActive(token)) this.currentModel = { url, vrm: { name: loads.at(-1) } };
                return this.currentModel;
            } });
        w.vrmManager = manager; h.load('static/app/app-character.js');
        const boot = w.initVRMModel(); await entered.promise;
        await w.handleCatgirlSwitch(incoming, 'A', { switchRevision: incoming === 'A' ? 810 : 811 });
        release.resolve(); await boot;
        assert.deepEqual(loads, [incoming + '.vrm']);
        assert.equal(w.lanlan_config.lanlan_name, incoming);
        assert.equal(w.vrmManager.currentModel.vrm.name, incoming + '.vrm');
        assert.equal(calls.filter(c => c[0] === 'begin').length, incoming === 'A' ? 0 : 1);
    });
}

test('same-current VRM event remains deduped when the VRM instance is healthy', async () => {
    const h = harness(); const calls = avatarCalls(h);
    h.window.location.pathname = '/';
    h.window.lanlan_config = {
        lanlan_name: 'A',
        model_type: 'live3d',
        live3d_sub_type: 'vrm'
    };
    h.window.vrmManager = { currentModel: { vrm: {} } };
    h.load('static/app/app-character.js');

    await h.window.handleCatgirlSwitch('A', 'B');

    assert.deepEqual(calls, [], 'healthy VRM duplicate must keep BC/WS dedupe behavior');
});

test('rapid switch back queues latest target and force-reloads it after the in-flight switch fails', async () => {
    const h = harness(); const calls = avatarCalls(h); const firstFetch = deferred();
    let fetchCount = 0;
    h.context.fetch = async () => {
        fetchCount += 1;
        if (fetchCount === 1) return firstFetch.promise;
        return { ok: true, json: async () => ({ '猫娘': {
            A: { model_type: 'live2d', _reserved: { character_uid: UID_A } },
            B: { model_type: 'live2d', _reserved: { character_uid: UID_B } }
        } }) };
    };
    h.load('static/app/app-character.js');

    const first = h.window.handleCatgirlSwitch('B', 'A');
    await Promise.resolve();
    assert.equal(h.window.appState.isSwitchingCatgirl, true);

    await h.window.handleCatgirlSwitch('A', 'B');
    assert.equal(h.window.appState._pendingCatgirlSwitch?.newCatgirl, 'A');
    assert.equal(h.window.appState._pendingCatgirlSwitch?.oldCatgirl, 'B');

    firstFetch.reject(new Error('B model/config unavailable'));
    await first;
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));

    assert.ok(fetchCount >= 2, 'queued switch-back must replay instead of being dropped');
    assert.equal(h.window.lanlan_config.lanlan_name, 'A');
    assert.equal(h.window.appState.isSwitchingCatgirl, false);
    assert.equal(h.window.appState._pendingCatgirlSwitch, null);
    assert.deepEqual(calls, [
        ['begin', 1],
        ['rollback', 1],
        ['begin', 2],
        ['commit', 2, { uid: UID_A, name: 'A' }]
    ]);
});

test('older switch revision cannot overwrite a newer pending target while a switch is active', async () => {
    const h = harness(); const firstFetch = deferred();
    let fetchCount = 0;
    h.context.fetch = async () => {
        fetchCount += 1;
        if (fetchCount === 1) return firstFetch.promise;
        return { ok: true, json: async () => ({ ['\u732b\u5a18']: {
            A: { model_type: 'live2d', _reserved: { character_uid: UID_A } },
            B: { model_type: 'live2d', _reserved: { character_uid: UID_B } }
        } }) };
    };
    h.load('static/app/app-character.js');

    const first = h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 100 });
    await Promise.resolve();
    await h.window.handleCatgirlSwitch('A', 'B', { switchRevision: 101 });
    assert.equal(h.window.appState._pendingCatgirlSwitch?.newCatgirl, 'A');
    assert.equal(h.window.appState._pendingCatgirlSwitch?.switchRevision, 101);

    await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 100 });
    assert.equal(h.window.appState._pendingCatgirlSwitch?.newCatgirl, 'A',
        'late older server commit must not replace newer A intent');
    assert.equal(h.window.appState._pendingCatgirlSwitch?.switchRevision, 101);

    firstFetch.reject(new Error('B load failed'));
    await first;
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.window.lanlan_config.lanlan_name, 'A');
});

test('older switch revision stays ignored after a newer BC-only switch has completed', async () => {
    const h = harness(); const calls = avatarCalls(h);
    h.load('static/app/app-character.js');

    await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 200 });
    await h.window.handleCatgirlSwitch('A', 'B', { switchRevision: 201 });
    await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 200 });

    assert.equal(h.window.lanlan_config.lanlan_name, 'A');
    assert.deepEqual(calls, [
        ['begin', 1], ['commit', 1, { uid: UID_B, name: 'B' }],
        ['begin', 2], ['commit', 2, { uid: UID_A, name: 'A' }]
    ], 'late response-derived BC for revision 200 must not undo revision 201');
});

test('legacy receipt cannot replace a newer versioned pending target', async () => {
    const h = harness(); const calls = avatarCalls(h); const firstFetch = deferred();
    let fetchCount = 0;
    h.context.fetch = async () => {
        fetchCount += 1;
        if (fetchCount === 1) return firstFetch.promise;
        return { ok: true, json: async () => ({ ['\u732b\u5a18']: {
            A: { model_type: 'live2d', _reserved: { character_uid: UID_A } },
            B: { model_type: 'live2d', _reserved: { character_uid: UID_B } }
        } }) };
    };
    h.load('static/app/app-character.js');

    const first = h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 11 });
    await Promise.resolve();
    await h.window.handleCatgirlSwitch('A', 'B', { switchRevision: 12 });
    assert.deepEqual(
        [h.window.appState._pendingCatgirlSwitch?.newCatgirl, h.window.appState._pendingCatgirlSwitch?.switchRevision],
        ['A', 12]
    );

    await h.window.handleCatgirlSwitch('B', 'A');
    assert.deepEqual(
        [h.window.appState._pendingCatgirlSwitch?.newCatgirl, h.window.appState._pendingCatgirlSwitch?.switchRevision],
        ['A', 12],
        'an unversioned compatibility receipt must not downgrade the queued committed intent'
    );

    firstFetch.reject(new Error('B load failed'));
    await first;
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.window.lanlan_config.lanlan_name, 'A');
    assert.ok(calls.some(call => call[0] === 'commit' && call[2]?.name === 'A'),
        'the queued A/12 intent must actually replay and commit A');
});

test('newer same-name revision queued after commit still replays', async () => {
    const h = harness(); const calls = avatarCalls(h); const unlock = deferred(), unlockEntered = deferred();
    h.context.fetch = async () => ({ ok: true, json: async () => ({ ['猫娘']: {
        A: { model_type: 'live2d', _reserved: { character_uid: UID_A } },
        B: { model_type: 'live2d', _reserved: { character_uid: UID_B } }
    } }) });
    h.window.unlockAchievement = async () => {
        unlockEntered.resolve();
        await unlock.promise;
    };
    h.load('static/app/app-character.js');

    const first = h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 82 });
    await unlockEntered.promise;
    assert.deepEqual(calls.map(call => call[0]), ['begin', 'commit'], 'B/82 has crossed its real commit point');

    assert.equal(await h.window.handleCatgirlSwitch('B', 'B', { switchRevision: 83 }), false);
    assert.deepEqual(
        [h.window.appState._pendingCatgirlSwitch?.newCatgirl, h.window.appState._pendingCatgirlSwitch?.switchRevision],
        ['B', 83]
    );

    unlock.resolve();
    assert.equal(await first, false, 'B/82 committed but its caller is superseded by queued B/83');
    for (let turn = 0; turn < 12 && calls.filter(call => call[0] === 'commit').length < 2; turn++) {
        await new Promise(setImmediate);
    }

    assert.equal(h.window.lanlan_config.lanlan_name, 'B');
    assert.equal(h.window.appState._latestCatgirlSwitchRevision, 83);
    assert.deepEqual(calls.map(call => call[0]), ['begin', 'commit', 'begin', 'commit'],
        'B/83 must replay after B/82 commits instead of being deduped by name alone');
});

test('same revision duplicate queued during an active attempt becomes retry only if that attempt fails', async () => {
    const h = harness(); const calls = avatarCalls(h); const firstFetch = deferred();
    let fetchCount = 0;
    h.context.fetch = async () => {
        fetchCount += 1;
        if (fetchCount === 1) return firstFetch.promise;
        return { ok: true, json: async () => ({ ['\u732b\u5a18']: {
            A: { model_type: 'live2d', _reserved: { character_uid: UID_A } },
            B: { model_type: 'live2d', _reserved: { character_uid: UID_B } }
        } }) };
    };
    h.load('static/app/app-character.js');

    const first = h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 250 });
    await Promise.resolve();
    await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 250 });
    assert.equal(h.window.appState._pendingCatgirlSwitch?.newCatgirl, 'B');
    assert.equal(h.window.appState._pendingCatgirlSwitch?.switchRevision, 250);

    firstFetch.reject(new Error('first B attempt failed'));
    await first;
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));

    assert.equal(h.window.lanlan_config.lanlan_name, 'B');
    assert.deepEqual(calls, [
        ['begin', 1], ['rollback', 1],
        ['begin', 2], ['commit', 2, { uid: UID_B, name: 'B' }]
    ]);
});

test('same switch revision may retry after its previous frontend attempt failed', async () => {
    const h = harness(); const calls = avatarCalls(h);
    let failOnce = true;
    h.context.fetch = async () => {
        if (failOnce) {
            failOnce = false;
            throw new Error('temporary frontend config read failure');
        }
        return { ok: true, json: async () => ({ ['\u732b\u5a18']: {
            A: { model_type: 'live2d', _reserved: { character_uid: UID_A } },
            B: { model_type: 'live2d', _reserved: { character_uid: UID_B } }
        } }) };
    };
    h.load('static/app/app-character.js');

    await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 300 });
    assert.equal(h.window.lanlan_config.lanlan_name, 'A');
    await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 300 });

    assert.equal(h.window.lanlan_config.lanlan_name, 'B');
    assert.deepEqual(calls, [
        ['begin', 1], ['rollback', 1],
        ['begin', 2], ['commit', 2, { uid: UID_B, name: 'B' }]
    ], 'same authoritative revision remains a valid recovery after a failed frontend attempt');
});

test('latest intent matching a successful in-flight target cancels an older pending target without replay', async () => {
    const h = harness(); const calls = avatarCalls(h); const audioClear = deferred();
    h.window.clearAudioQueue = () => audioClear.promise;
    h.load('static/app/app-character.js');

    const first = h.window.handleCatgirlSwitch('B', 'A');
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(h.window.appState.isSwitchingCatgirl, true);

    await h.window.handleCatgirlSwitch('C', 'B');
    assert.equal(h.window.appState._pendingCatgirlSwitch?.newCatgirl, 'C');
    await h.window.handleCatgirlSwitch('B', 'C');
    assert.equal(h.window.appState._pendingCatgirlSwitch?.newCatgirl, 'B');

    audioClear.resolve();
    await first;
    await new Promise(resolve => setImmediate(resolve));

    assert.equal(h.window.lanlan_config.lanlan_name, 'B');
    assert.equal(h.window.appState._pendingCatgirlSwitch, null);
    assert.deepEqual(calls, [
        ['begin', 1],
        ['commit', 1, { uid: UID_B, name: 'B' }]
    ], 'successful in-flight B already satisfies the latest B intent without a second attempt');
});

function loadSwitchRelay(h) {
    h.load('static/app/app-interpage/bootstrap-resources-and-model-reload.js');
    Object.assign(h.window.__appInterpageParts, {
        isIcebreakerBridgeAction: () => false,
        shouldBypassYuiGuideMessageDedup: () => false,
        isYuiGuideLifecycleStartAction: () => false,
        isYuiGuideLifecycleScopedAction: () => false,
        isYuiGuideMessageForCurrentLifecycle: () => true,
        isYuiGuidePcOverlayRunEnded: () => false,
        clearYuiGuidePcOverlayBridgeState() {},
        isHighVolumeBroadcastChannelAction: () => false,
        drainPendingYuiGuideChatBridgeQueue() {}
    });
    h.context.BroadcastChannel = class {
        constructor(name) { this.name = name; }
        postMessage() {}
        close() {}
    };
    h.load('static/app/app-interpage/guide-message-relay.js');

    return h.window.__appInterpageParts.handleNekoBroadcastMessage;
}

test('BroadcastChannel forwards distinct commits even when they share a timestamp', async () => {
    const h = harness(); const forwarded = [];
    h.window.handleCatgirlSwitch = (next, previous, options) => {
        forwarded.push([next, previous, options?.switchRevision || 0]);
    };
    const deliver = loadSwitchRelay(h);
    const timestamp = 1700000000000;
    await deliver({ data: {
        action: 'catgirl_switched', new_catgirl: 'B', old_catgirl: 'A',
        switch_revision: 600, timestamp
    } });
    await deliver({ data: {
        action: 'catgirl_switched', new_catgirl: 'C', old_catgirl: 'B',
        switch_revision: 601, timestamp
    } });
    assert.deepEqual(forwarded, [
        ['B', 'A', 600],
        ['C', 'B', 601]
    ], 'distinct revisions sharing one millisecond must both pass');
});

test('page-config switch revision seeds central ordering before any live switch event', async () => {
    const h = harness(); const calls = avatarCalls(h);
    h.window.location.pathname = '/';
    h.window.lanlan_config = {
        lanlan_name: 'C', model_type: 'live2d', switch_revision: 701
    };
    h.load('static/app/app-character.js');

    await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 700 });

    assert.deepEqual(calls, [], 'new page snapshot must reject an older delayed switch before starting an attempt');
    assert.equal(h.window.lanlan_config.lanlan_name, 'C');
    assert.equal(h.window.appState._latestCatgirlSwitchRevision, 701);
});

test('page bootstrap retains the authoritative positive switch revision from page_config', async () => {
    const h = harness(); h.context.URLSearchParams = URLSearchParams;
    h.context.fetch = async () => ({ json: async () => ({
        success: true, lanlan_name: 'A', switch_revision: 701, model_type: 'live2d'
    }) });
    h.load('static/js/index.js');
    assert.equal(await h.window.startPageConfigLoad(), true);
    assert.equal(h.window.lanlan_config.switch_revision, 701);
});

test('BroadcastChannel catgirl_switched forwards same-name targets to the central switch handler', async () => {
    const h = harness(); const forwarded = [];
    h.window.handleCatgirlSwitch = (next, previous, options) => forwarded.push([next, previous, options?.switchRevision || 0]);
    h.window.__appInterpageParts = {
        isIcebreakerBridgeAction: () => false,
        shouldBypassYuiGuideMessageDedup: () => false,
        isDuplicateMessage: () => false,
        isYuiGuideLifecycleStartAction: () => false,
        isYuiGuideLifecycleScopedAction: () => false,
        isYuiGuideMessageForCurrentLifecycle: () => true,
        isYuiGuidePcOverlayRunEnded: () => false,
        clearYuiGuidePcOverlayBridgeState() {},
        isHighVolumeBroadcastChannelAction: () => false,
        drainPendingYuiGuideChatBridgeQueue() {}
    };
    h.context.BroadcastChannel = class {
        constructor(name) { this.name = name; }
        postMessage() {}
        close() {}
    };
    h.load('static/app/app-interpage/guide-message-relay.js');

    const parts = h.window.__appInterpageParts;
    await parts.handleNekoBroadcastMessage({ data: {
        action: 'catgirl_switched',
        new_catgirl: 'A',
        old_catgirl: 'B',
        switch_revision: 400,
        timestamp: Date.now()
    } });

    h.window.lanlan_config.lanlan_name = 'B';
    await parts.handleNekoBroadcastMessage({ data: {
        action: 'catgirl_switched', new_catgirl: 'C', old_catgirl: 'B', switch_revision: 401, timestamp: Date.now() + 1
    } });
    await parts.handleNekoBroadcastMessage({ data: {
        action: 'catgirl_switched', new_catgirl: 'B', old_catgirl: 'C', switch_revision: 402, timestamp: Date.now() + 2
    } });

    assert.deepEqual(forwarded, [['A', 'B', 400], ['C', 'B', 401], ['B', 'C', 402]],
        'BC must forward same-name recovery and latest-target cancellation events to central arbitration');
});

test('WebSocket catgirl_switched forwards authoritative switch revision', () => {
    const h = harness(); const forwarded = [];
    h.window.t = key => key;
    h.window.location.protocol = 'http:'; h.window.location.host = 'fixture.test';
    h.window.appConst = { HEARTBEAT_INTERVAL: 30000 };
    h.window.handleCatgirlSwitch = (next, previous, options) => {
        forwarded.push([next, previous, options?.switchRevision || 0]);
    };
    h.context.Blob = Blob;
    h.context.WebSocket = class {
        static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        constructor(url) { this.url = url; this.readyState = 0; }
        send() {}
        close() { this.readyState = 3; }
    };
    h.load('static/app/app-websocket.js');
    h.window.connectWebSocket();
    h.window.appState.socket.onmessage({ data: JSON.stringify({
        type: 'catgirl_switched', new_catgirl: 'B', old_catgirl: 'A', switch_revision: 500
    }) });
    assert.deepEqual(forwarded, [['B', 'A', 500]]);
});

test('WS-only newer B-to-B notification reaches a frontend still on A before delayed A-to-B', async () => {
    const h = harness(), w = h.window, calls = avatarCalls(h);
    w.location.protocol = 'http:'; w.location.host = 'fixture.test';
    w.t = key => key; w.appConst = { HEARTBEAT_INTERVAL: 30000 };
    h.context.Blob = Blob;
    h.context.WebSocket = class {
        static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        constructor(url) { this.url = url; this.readyState = 0; }
        send() {}
        close() { this.readyState = 3; }
    };
    h.load('static/app/app-character.js'); h.load('static/app/app-websocket.js');
    w.connectWebSocket();
    const original = w.appState.socket;
    const receipt = (socket, previous, revision) => socket.onmessage({ data: JSON.stringify({
        type: 'catgirl_switched', new_catgirl: 'B', old_catgirl: previous, switch_revision: revision,
    }) });
    receipt(original, 'B', 101); receipt(original, 'A', 100);
    await new Promise(setImmediate);
    receipt(w.appState.socket, 'B', 101);
    await new Promise(setImmediate);
    assert.deepEqual(calls.map(c => c[0]), ['begin', 'commit']);
    assert.equal(w.lanlan_config.lanlan_name, 'B');
    assert.equal(w.lanlan_config.switch_revision, 101);
});

for (const pathname of ['/chat', '/chat/', '/chat_full', '/chat_full/']) {
    test(`${pathname} repeated same-current VRM WebSocket notifications stay deduped without model-side effects`, async () => {
        const h = harness(); const calls = avatarCalls(h);
        h.window.location.pathname = pathname;
        h.window.location.protocol = 'http:';
        h.window.location.host = 'fixture.test';
        h.window.t = key => key;
        h.window.appConst = { HEARTBEAT_INTERVAL: 30000 };
        h.window.lanlan_config = {
            lanlan_name: 'A',
            model_type: 'live3d',
            live3d_sub_type: 'vrm'
        };
        h.window.vrmManager = undefined;
        let clearCount = 0;
        h.window.clearAudioQueue = async () => { clearCount += 1; };
        h.context.Blob = Blob;
        let socketCount = 0;
        h.context.WebSocket = class {
            static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
            constructor(url) { this.url = url; this.readyState = 0; socketCount += 1; }
            send() {}
            close() { this.readyState = 3; }
        };

        h.load('static/app/app-character.js');
        h.load('static/app/app-websocket.js');
        h.window.connectWebSocket();
        const socket = h.window.appState.socket;
        const initialSocketCount = socketCount;
        const message = JSON.stringify({ type: 'catgirl_switched', new_catgirl: 'A', old_catgirl: 'B' });
        socket.onmessage({ data: message });
        socket.onmessage({ data: message });
        await new Promise(resolve => setImmediate(resolve));

        assert.deepEqual(calls, [], 'chat-only surfaces must not interpret absent VRM runtime as model loss');
        assert.equal(clearCount, 0, 'same-current WS notifications must not clear chat audio on chat-only surfaces');
        assert.equal(socketCount, initialSocketCount, 'same-current WS notifications must not reconnect on chat-only surfaces');
    });
}

test('character-card switch producer republishes the backend switch revision to local and BC paths', () => {
    const source = fs.readFileSync(path.join(root,
        'static/js/character_card_manager/card-form-and-actions.js'), 'utf8');
    assert.match(source, /const switchRevision = Number\.isSafeInteger\(result\.switch_revision\)/);
    assert.match(source, /handleCatgirlSwitch\(targetName, oldCatgirl, \{ switchRevision \}\)/);
    assert.match(source, /_broadcastCatgirlSwitched\(targetName, oldCatgirl, switchRevision\)/);
    assert.match(source, /message\.switch_revision = switchRevision/);
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
    // The Electron chat window renders no model: its only model avatar is the one injected over IPC.
    h.window.appChatAvatar = {
        getCurrentAvatarDataUrl: () => 'data:image/png;base64,CUSTOM',
        getCachedPreview: () => null,
        getModelAvatarDataUrl: () => 'data:image/png;base64,MODEL'
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


test('session-end suppression still renders authenticated visit assistant messages', () => {
    const h = harness(); const appended = [];
    h.window.t = key => key;
    h.window.location.protocol = 'http:'; h.window.location.host = 'fixture.test';
    h.window.appConst = { HEARTBEAT_INTERVAL: 30000 };
    h.window.appState.suppressAssistantStreamUntilNextSession = true;
    h.window.appendMessage = (text, role, isNewMessage) => {
        appended.push({ text, role, isNewMessage });
        return true;
    };
    h.context.Blob = Blob;
    h.context.WebSocket = class {
        static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        constructor(url) { this.url = url; this.readyState = 0; }
        send() {}
        close() { this.readyState = 3; }
    };
    h.load('static/app/app-websocket.js');
    h.window.connectWebSocket();
    const socket = h.window.appState.socket;
    const send = response => socket.onmessage({ data: JSON.stringify(response) });
    const visitResponse = (kind, text) => ({
        type: 'gemini_response', text, isNewMessage: true,
        turn_id: `${kind}:visit-1`, request_id: `${kind}:visit-1`,
        metadata: {
            source: 'neko_visit', kind, session_id: 'visit-1',
            mirror: { kind, session_id: 'visit-1', event: { memory_enabled: false } }
        }
    });

    send({ type: 'gemini_response', text: '迟到的普通回复', isNewMessage: true });
    send(visitResponse('visit_line', '迟到的串门过程台词'));
    send({ ...visitResponse('visit_ritual', '伪造的不完整身份'), metadata: {
        source: 'neko_visit', kind: 'visit_ritual', session_id: 'visit-1',
        mirror: { kind: 'visit_ritual', session_id: 'another-visit' }
    } });
    assert.deepEqual(appended.map(item => item.text), ['迟到的串门过程台词']);

    send(visitResponse('visit_direct_invite_question', '要去串门吗？'));
    send(visitResponse('visit_ritual', '我回来啦。'));
    send(visitResponse('visit_debrief', '这次串门很开心。'));
    assert.deepEqual(appended.map(item => item.text), [
        '迟到的串门过程台词', '要去串门吗？', '我回来啦。', '这次串门很开心。'
    ]);
    assert.ok(appended.every(item => item.role === 'gemini' && item.isNewMessage === true));
    assert.equal(h.window.appState.suppressAssistantStreamUntilNextSession, true,
        'allowing homecoming messages must not reopen the ended voice session');
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
        refresh: async () => null,
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

const MODEL_AVATAR = 'data:image/png;base64,MODEL';

function captureHarness() {
    const h = popupHarness(); const capture = deferred();
    h.window.cubism4Model = 'model-a';
    h.window.avatarPortrait = { capture: () => capture.promise };
    // A silent background capture, as scheduled after a model loads.
    const running = h.window.appChatAvatar.showPopup(null, { showCard: false, silent: true, forceRefresh: true });
    async function finish() {
        capture.resolve({ dataUrl: MODEL_AVATAR, modelType: 'live2d' });
        await running;
    }
    return { ...h, core: h.window.appChatAvatar, finish };
}

test('closing the popup lets a running model capture finish and cache', async () => {
    const h = captureHarness();
    h.dom.chatAvatarPreviewCard = { hidden: true };
    h.core.hidePopup();
    await h.finish();
    assert.equal(h.core.getModelAvatarDataUrl(), MODEL_AVATAR);
});

test('identity changes close a cropper but keep the running model capture', async () => {
    const h = captureHarness();
    h.load('static/app/app-chat-avatar-editor.js'); h.window.appChatAvatarEditor.initialize();
    let closed = 0; const close = h.core.closeUploadCropper;
    h.core.closeUploadCropper = () => { ++closed; close(); };
    for (const reason of ['switch-start', 'identity', 'switch-rollback']) {
        h.window.dispatchEvent({ type: 'chat-avatar-display-updated', detail: { reason } });
    }
    assert.equal(closed, 3);
    await h.finish();
    assert.equal(h.core.getModelAvatarDataUrl(), MODEL_AVATAR);
});

test('a model capture retired for a custom upload is captured again silently', async () => {
    const h = captureHarness();
    const autoCaptures = () => [...h.timers.values()].filter(timer => timer.delay === 180).length;
    const before = autoCaptures();
    h.core.cancelModelPreviewCapture();
    assert.equal(autoCaptures(), before + 1);
    await h.finish();
    assert.equal(h.core.getModelAvatarDataUrl(), '', 'the retired result itself is never cached');
    h.core.cancelModelPreviewCapture();
    assert.equal(autoCaptures(), before + 1, 'nothing to recapture when no capture was running');
});

test('opening the popup re-reads the avatar record, whether or not the last read failed', async () => {
    const h = popupHarness(); const reads = [];
    let error = { code: 'chat_avatar_network_error' };
    Object.assign(h.window.appChatAvatarState, {
        getError: () => error,
        refresh: reason => { reads.push(reason); return Promise.reject(new Error('offline')); }
    });
    h.setCustom('data:image/png;base64,CUSTOM');
    await h.window.appChatAvatar.showPopup();
    assert.deepEqual(reads, ['popup-open']);
    // An uncertain save whose confirmation read succeeded with the old revision leaves no error.
    error = null;
    await h.window.appChatAvatar.showPopup();
    assert.deepEqual(reads, ['popup-open', 'popup-open']);
    await h.window.appChatAvatar.showPopup(null, { showCard: false, silent: true });
    assert.deepEqual(reads, ['popup-open', 'popup-open'], 'background captures do not read');
});

test('Escape closes the popup but only keeps a key aimed at it from other handlers', () => {
    const h = popupHarness(); const keydown = [];
    const inside = { id: 'inside' }; const outside = { id: 'outside' };
    const popup = { hidden: false, contains: node => node === inside, classList: { add() {}, remove() {}, contains() { return false; } },
        addEventListener() {}, removeEventListener() {} };
    const stub = () => ({ hidden: false, disabled: false, addEventListener() {}, classList: { add() {}, remove() {} } });
    const dom = new Map([['chat-avatar-preview-popup', popup], ['chatAvatarPreviewRefreshButton', stub()],
        ['chatAvatarPreviewCloseButton', stub()]]);
    h.context.document.getElementById = id => dom.get(id) || h.elements.get(id);
    h.context.document.addEventListener = (type, listener) => { if (type === 'keydown') keydown.push(listener); };
    h.context.document.activeElement = outside;
    h.window.appChatAvatarState.initialize = () => {};
    h.window.setTimeout = h.context.setTimeout; h.window.clearTimeout = h.context.clearTimeout;
    h.window.appChatAvatar.init();
    assert.equal(keydown.length, 1);
    const press = target => {
        const event = { key: 'Escape', target, prevented: false, stopped: false,
            preventDefault() { this.prevented = true; }, stopImmediatePropagation() { this.stopped = true; } };
        popup.hidden = false;
        keydown[0](event);
        return event;
    };
    const aimed = press(inside);
    assert.equal(aimed.prevented, true); assert.equal(aimed.stopped, true);
    const elsewhere = press(outside);
    assert.equal(elsewhere.prevented, true); assert.equal(elsewhere.stopped, false);
    h.context.document.activeElement = inside;
    assert.equal(press(outside).stopped, true, 'focus inside the popup also counts');
});

test('a background model capture keeps the upload cropper title and its status shows afterwards', async () => {
    const h = popupHarness(); const core = h.window.appChatAvatar; const capture = deferred();
    const node = () => ({
        hidden: false, disabled: false, style: {}, dataset: {},
        classList: { add() {}, remove() {}, contains() { return false; } },
        addEventListener() {}, removeEventListener() {}, setAttribute() {}, getAttribute() { return null; },
        querySelector() { return null; }, querySelectorAll() { return []; }, focus() {},
        getBoundingClientRect() { return { left: 0, top: 0, right: 100, bottom: 100, width: 100, height: 100 }; }
    });
    const cropperDom = new Map(['avatar-cropper-wrap', 'avatar-cropper-img', 'avatar-cropper-mask', 'avatar-cropper-box',
        'avatar-cropper-retake', 'avatar-cropper-cancel', 'avatar-cropper-save'].map(id => [id, node()]));
    h.context.document.getElementById = id => cropperDom.get(id) || h.elements.get(id);
    h.context.document.removeEventListener = () => {};
    h.dom.chatAvatarPreviewCard = node();
    h.window.requestAnimationFrame = h.context.requestAnimationFrame = () => 0;
    h.window.cubism4Model = 'model-a';
    h.window.avatarPortrait = { capture: () => capture.promise };

    const cropping = core.openUploadCropper({ url: 'blob:upload', width: 64, height: 64 });
    assert.equal(h.dom.chatAvatarPreviewStatus.textContent, 'chat.avatarCropperTitle');
    const running = core.showPopup(null, { showCard: false, silent: true, forceRefresh: true });
    assert.equal(h.dom.chatAvatarPreviewStatus.textContent, 'chat.avatarCropperTitle');
    capture.resolve({ dataUrl: MODEL_AVATAR, modelType: 'live2d' }); await running;
    assert.equal(h.dom.chatAvatarPreviewStatus.textContent, 'chat.avatarCropperTitle');
    assert.equal(core.getModelAvatarDataUrl(), MODEL_AVATAR);

    core.closeUploadCropper(); assert.equal(await cropping, null);
    core.refreshDisplayedAvatar();
    assert.match(h.dom.chatAvatarPreviewStatus.textContent, /^chat\.avatarPreviewReady/);
});

for (const lateResult of ['older', 'http-error', 'network-error']) {
    test('delayed page config cannot undo a completed character switch: ' + lateResult, async () => {
        const h = harness(); const calls = avatarCalls(h);
        h.context.URLSearchParams = URLSearchParams;
        h.load('static/js/index.js');
        h.load('static/app/app-character.js');
        const originalFetch = h.context.fetch;
        const getStarted = deferred(), reply = deferred();
        h.context.fetch = (url, ...args) => {
            if (String(url).startsWith('/api/config/page_config')) {
                getStarted.resolve();
                return reply.promise;
            }
            return originalFetch(url, ...args);
        };
        const loading = h.window.startPageConfigLoad();
        await getStarted.promise;
        await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 902 });
        assert.deepEqual(calls, [['begin', 1], ['commit', 1, { uid: UID_B, name: 'B' }]]);
        const snapshot = () => [h.window.lanlan_config.lanlan_name, h.window.lanlan_config.switch_revision,
            h.window.lanlan_config.model_type, h.window.cubism4Model, h.window.vrmModel];
        const committed = snapshot();
        assert.deepEqual(committed.slice(0, 2), ['B', 902]);
        if (lateResult === 'network-error') reply.reject(new Error('late read failed'));
        else if (lateResult === 'http-error') reply.resolve({ json: async () => ({ success: false }) });
        else reply.resolve({ json: async () => ({
            success: true, lanlan_name: 'A', switch_revision: 901,
            model_type: 'live2d', model_path: '/A/A.model3.json'
        }) });
        await loading;
        assert.deepEqual(snapshot(), committed);
        assert.equal(h.window.appState._latestCatgirlSwitchRevision, 902);
    });
}

test('page config still boots an explicit preview without a revision and refreshes an equal revision', async () => {
    const h = harness();
    h.context.URLSearchParams = URLSearchParams;
    h.window.location.pathname = '/preview-A';
    h.load('static/js/index.js');
    const fetchConfig = (name, revision, modelPath) => {
        h.context.fetch = async () => ({ json: async () => ({
            success: true, lanlan_name: name, switch_revision: revision, model_path: modelPath
        }) });
    };
    fetchConfig('preview-A', 0, '/preview.model3.json');
    assert.equal(await h.window.startPageConfigLoad(), true);
    assert.equal(h.window.lanlan_config.lanlan_name, 'preview-A');
    assert.equal(h.window.lanlan_config.switch_revision, 0);
    assert.equal(h.window.cubism4Model, '/preview.model3.json');
    h.window.location.pathname = '/';
    fetchConfig('B', 902, '/B.model3.json');
    assert.equal(await h.window.reloadPageConfig(), true);
    fetchConfig('B', 902, '/updated-B.model3.json');
    assert.equal(await h.window.reloadPageConfig(), true);
    assert.equal(h.window.lanlan_config.lanlan_name, 'B');
    assert.equal(h.window.cubism4Model, '/updated-B.model3.json');
});

test('explicit preview reload accepts revision 0 after current-character high-water advances', async () => {
    const h = harness();
    h.context.URLSearchParams = URLSearchParams;
    h.window.location.pathname = '/preview-A';
    h.load('static/js/index.js');
    h.context.fetch = async () => ({ json: async () => ({
        success: true, lanlan_name: 'preview-A', switch_revision: 0,
        model_type: 'live2d', model_path: '/preview-a.model3.json'
    }) });
    assert.equal(await h.window.startPageConfigLoad(), true);
    h.window.appState = { _latestCatgirlSwitchRevision: 1204 };
    h.context.fetch = async () => ({ json: async () => ({
        success: true, lanlan_name: 'preview-A', switch_revision: 0,
        model_type: 'live2d', model_path: '/preview-b.model3.json'
    }) });
    assert.equal(await h.window.reloadPageConfig(), true);
    assert.equal(h.window.lanlan_config.lanlan_name, 'preview-A');
    assert.equal(h.window.lanlan_config.switch_revision, 0);
    assert.equal(h.window.cubism4Model, '/preview-b.model3.json');
});

for (const versioned of [true, false]) {
    test('IPC current-character bootstrap rejects an older BC receipt (' + (versioned ? 'paired' : 'legacy') + ')', async () => {
        const h = harness(); const calls = avatarCalls(h);
        h.context.URLSearchParams = URLSearchParams;
        h.window.__NEKO_MULTI_WINDOW__ = true;
        h.window.__nekoInjectedConfig = {
            lanlan_name: 'A', model_type: 'live2d', ...(versioned ? { switch_revision: 923 } : {})
        };
        const originalFetch = h.context.fetch, lookups = [];
        h.context.fetch = (url, ...args) => {
            if (url !== '/api/characters/current_catgirl') return originalFetch(url, ...args);
            lookups.push(url);
            return Promise.resolve({ ok: true, json: async () => ({ current_catgirl: 'A', switch_revision: 923 }) });
        };
        h.load('static/js/index.js');
        h.load('static/app/app-character.js');
        assert.equal(await h.window.startPageConfigLoad(), true);
        await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 922 });
        assert.equal(h.window.lanlan_config.lanlan_name, 'A');
        assert.equal(h.window.lanlan_config.switch_revision, 923);
        assert.deepEqual(calls, []);
        assert.equal(lookups.length, versioned ? 0 : 1);
    });
}

for (const delay of ['headers', 'body']) {
    test('desktop legacy IPC remains usable when the identity lookup stalls at ' + delay, async () => {
        const h = harness(); const started = deferred(), slow = deferred();
        h.context.URLSearchParams = URLSearchParams;
        h.context.AbortController = AbortController;
        h.window.__NEKO_MULTI_WINDOW__ = true;
        h.window.__nekoInjectedConfig = { lanlan_name: 'A', model_type: 'live2d' };
        const originalFetch = h.context.fetch;
        h.context.fetch = async (url, ...args) => {
            if (url !== '/api/characters/current_catgirl') return originalFetch(url, ...args);
            if (delay === 'headers') { started.resolve(); await slow.promise; }
            return { ok: true, json: async () => {
                if (delay === 'body') { started.resolve(); await slow.promise; }
                return { current_catgirl: 'A', switch_revision: 999 };
            } };
        };
        h.load('static/js/index.js'); h.load('static/app/app-character.js');
        const loading = h.window.startPageConfigLoad();
        await started.promise;
        let ready = false;
        loading.then(() => { ready = true; });
        for (const timer of [...h.timers.values()]) if (timer.delay <= 5000) timer.callback();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(ready, true, 'IPC identity must not wait indefinitely for a stalled snapshot');
        assert.equal(h.window.lanlan_config.lanlan_name, 'A');
        await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 902 });
        slow.resolve(); await new Promise(resolve => setImmediate(resolve));
        assert.equal(h.window.lanlan_config.lanlan_name, 'B');
        assert.equal(h.window.lanlan_config.switch_revision, 902, 'abandoned reads must not mutate a later live switch');
    });
}

for (const explicit of [false, true]) for (const delay of ['headers', 'body']) {
    test('mismatched desktop IPC keeps verified identity and hydrates a slow ' + delay + ' (' + (explicit ? 'preview' : 'current') + ')', async () => {
        const h = harness(), started = deferred(), finish = deferred();
        const target = explicit ? 'Preview' : 'B', revision = explicit ? 0 : 82;
        h.context.URLSearchParams = URLSearchParams; h.window.__NEKO_MULTI_WINDOW__ = true;
        h.window.location.search = explicit ? '?lanlan_name=Preview' : '';
        h.window.__nekoInjectedConfig = { lanlan_name: 'A', lighting: { wrong: true }, avatarDataUrl: 'wrong-avatar' };
        h.context.fetch = async url => {
            if (url === '/api/characters/current_catgirl') return { json: async () => ({ current_catgirl: target, switch_revision: revision }) };
            if (delay === 'headers') { started.resolve(); await finish.promise; }
            return { json: async () => {
                if (delay === 'body') { started.resolve(); await finish.promise; }
                return { success: true, lanlan_name: target, switch_revision: revision, model_type: 'live2d', model_path: '/correct.model3.json' };
            } };
        };
        h.load('static/js/index.js'); h.load('static/app/app-character.js');
        const loading = h.window.startPageConfigLoad();
        await started.promise;
        let ready = 'pending';
        h.window.pageConfigReady.then(result => { ready = result; });
        for (const t of [...h.timers.values()]) if (t.delay === 1500) t.callback();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(ready, 'pending', 'paired identity is not verified page readiness');
        const cfg = h.window.lanlan_config;
        assert.deepEqual([cfg.lanlan_name, cfg.switch_revision], [target, revision]);
        assert.ok(!cfg.lighting && !h.window.__nekoPendingAvatar && !h.window.cubism4Model);
        finish.resolve();
        assert.equal(await loading, true);
        assert.equal(h.window.cubism4Model, '/correct.model3.json');
    });
}


function startDesktopAvatarBootstrap(h, injected, fetch, live2d = false, hooks = {}) {
    const w = h.window, c = h.context, loads = [];
    w.location = { pathname: '/web_chat_compact', search: '', href: 'https://example.test/web_chat_compact' };
    c.URL = URL; c.URLSearchParams = URLSearchParams; c.requestAnimationFrame = () => 0;
    c.document.querySelector = () => null;
    c.fetch = fetch;
    w.__NEKO_MULTI_WINDOW__ = true; w.__nekoInjectedConfig = injected;
    w.mmdModuleLoaded = w.vrmModuleLoaded = true;
    w.VRMCore = {};
    c.MMDManager = class {
        async init() {
            this.renderer = {}; this.scene = {}; this._activeLoadToken = 0; this._isDisposed = false;
            if (hooks.mmdInit) await hooks.mmdInit({ manager: this });
        }
        dispose() { this._isDisposed = true; this.currentModel = null; }
        async loadModel(url) {
            if (this._isDisposed) throw new Error('cannot load onto a disposed MMD manager');
            this._activeLoadToken++;
            loads.push(url);
            if (hooks.mmdLoadModel) {
                const hooked = await hooks.mmdLoadModel({ url, manager: this, loads });
                if (hooked !== undefined) return hooked;
            }
            return (this.currentModel = { url, mesh: {} });
        }
        async waitForRenderFrame() {}
    };
    const loadLive2D = () => {
        const getElementById = c.document.getElementById;
        c.document.getElementById = id => id === 'live2d-canvas' ? { style: {} } : getElementById(id);
        c.Live2DManager = class {
            currentModel = null; pixi_app = { renderer: {} };
            getCurrentModel() { return this.currentModel; }
            removeModel() { this.currentModel = null; }
            async ensurePIXIReady() {}
            async loadUserPreferences() {
                return hooks.live2dPreferences ? await hooks.live2dPreferences() : [];
            }
            async loadModel(input) {
                const url = typeof input === 'string' ? input : input.url;
                loads.push(url);
                return (this.currentModel = { url });
            }
        };
        h.load('static/live2d/live2d-init.js');
    };
    if (live2d === 'before-index') loadLive2D();
    h.load('static/js/index.js'); h.load('static/vrm/vrm-manager.js'); h.load('static/vrm/vrm-init.js');
    w.vrmManager = Object.assign(Object.create(w.VRMManager.prototype), {
        _activeLoadToken: 0, _pendingModelLoadCount: 0, initThreeJS: async () => {},
        pauseRendering() {}, resumeRendering() {}, cleanupUI() {},
        async _loadModelInternal(url, _options, token) {
            loads.push(url);
            if (this._isLoadTokenActive(token)) this.currentModel = { url, vrm: {} };
            return this.currentModel;
        }
    });
    h.load('static/mmd/mmd-init.js'); h.load('static/app/app-character.js');
    if (live2d && live2d !== 'before-index') loadLive2D();
    return { loads, ready: w.startPageConfigLoad(), vrmBoot: w.initVRMModel() };
}

function desktopAvatarConfig(name, kind, revision, modelPath) {
    return { success: true, lanlan_name: name, switch_revision: revision,
        model_type: kind === 'live2d' ? kind : 'live3d', live3d_sub_type: kind === 'live2d' ? '' : kind,
        model_path: modelPath, _reserved: { character_uid: name === 'C' ? 'c'.repeat(32) : UID_B } };
}

for (const kind of ['mmd', 'vrm']) {
    test('desktop ' + kind + ' boot waits for real model config instead of loading a guessed fallback', async () => {
        const h = harness(), slow = deferred(), started = deferred();
        const path = kind === 'mmd' ? '/static/mmd/B/B.pmx' : '/static/vrm/B.vrm';
        const model = desktopAvatarConfig('B', kind, 82, path);
        const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'A', model_type: 'live2d' }, async url => {
            if (url === '/api/characters/current_catgirl') return { json: async () => ({ current_catgirl: 'B', switch_revision: 82 }) };
            if (String(url).startsWith('/api/config/page_config')) { started.resolve(); await slow.promise; return { json: async () => model }; }
            return { ok: true, json: async () => ({}) };
        });
        await started.promise;
        for (const t of [...h.timers.values()]) if (t.delay === 1500) t.callback();
        await new Promise(resolve => setImmediate(resolve));
        assert.equal(h.window.lanlan_config.lanlan_name, 'B');
        assert.deepEqual(startup.loads, []);
        await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 82 });
        assert.deepEqual(startup.loads, [], 'a valid in-flight read still owns the renderer');
        slow.resolve();
        assert.equal(await startup.ready, true);
        await startup.vrmBoot;
        await new Promise(resolve => setImmediate(resolve));
        assert.deepEqual(startup.loads, [path]);
    });
}

test('matching desktop IPC still needs asset config on a local model host', async () => {
    const h = harness(), path = '/static/mmd/B/B.pmx';
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'B', switch_revision: 82, model_type: 'live3d', live3d_sub_type: 'mmd' }, async url => ({
        ok: true, json: async () => String(url).startsWith('/api/config/page_config') ? desktopAvatarConfig('B', 'mmd', 82, path) : {}
    }));
    assert.equal(await startup.ready, true);
    await startup.vrmBoot; await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(startup.loads, [path]);
});

test('a verified MMD role with no configured path may use the normal MMD default', async () => {
    const h = harness();
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'B', switch_revision: 82, model_type: 'live2d' }, async url => ({
        ok: true, json: async () => String(url).startsWith('/api/config/page_config') ? desktopAvatarConfig('B', 'mmd', 82, '') : {}
    }));
    h.window.mmdModel = '/A/stale.pmx';
    assert.equal(await startup.ready, true);
    await startup.vrmBoot; await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(startup.loads, ['/static/mmd/Miku/Miku.pmx']);
});

test('pinned multi-window preview rejects a successful config response for another character', async () => {
    const h = harness();
    h.context.URLSearchParams = URLSearchParams; h.window.__NEKO_MULTI_WINDOW__ = true;
    h.window.location.search = '?lanlan_name=Preview';
    h.window.__nekoInjectedConfig = { lanlan_name: 'A' };
    h.context.fetch = async () => ({ json: async () => desktopAvatarConfig('A', 'mmd', 0, '/A/wrong.pmx') });
    h.load('static/js/index.js');
    const loading = h.window.startPageConfigLoad();
    for (const delay of [600, 1200, 1800, 2400, 3000, 3600]) {
        for (let turn = 0; turn < 12 && ![...h.timers.values()].some(timer => timer.delay === delay); turn++) {
            await new Promise(setImmediate);
        }
        await advanceModelBootTimer(h, delay);
    }
    assert.equal(await loading, false);
    assert.equal(h.window.lanlan_config.lanlan_name, 'Preview');
    assert.ok(!h.window.mmdModel);
});

test('pinned preview accepts revision zero despite the current-character high-water mark', async () => {
    const h = harness();
    h.context.URLSearchParams = URLSearchParams; h.window.__NEKO_MULTI_WINDOW__ = true;
    h.window.location.pathname = '/';
    h.window.location.search = '?lanlan_name=Preview';
    h.window.__nekoInjectedConfig = { lanlan_name: 'Preview' };
    h.context.fetch = async () => ({ json: async () => desktopAvatarConfig('Preview', 'mmd', 0, '/Preview/model.pmx') });
    h.load('static/js/index.js');
    h.window.lanlan_config = { lanlan_name: 'C', switch_revision: 83, model_type: 'live2d' };
    assert.equal(await h.window.startPageConfigLoad(), true);
    assert.equal(h.window.lanlan_config.lanlan_name, 'Preview');
    assert.equal(h.window.lanlan_config.switch_revision, 0);
    assert.equal(h.window.mmdModel, '/Preview/model.pmx');
    assert.equal(h.context.latestPageSwitchRevision(), 83, 'preview must preserve current-character high-water');

    h.window.location.search = '';
    h.context.fetch = async () => ({ json: async () => desktopAvatarConfig('A', 'mmd', 82, '/A/stale.pmx') });
    assert.equal(await h.window.reloadPageConfig(), false, 'preview revision zero must not lower current-character high-water 83');
    assert.equal(h.window.lanlan_config.lanlan_name, 'Preview');
    assert.equal(h.window.mmdModel, '/Preview/model.pmx');
});

test('same-name positive IPC preview accepts a later revision-zero HTTP preview', async () => {
    const h = harness();
    h.context.URLSearchParams = URLSearchParams; h.window.__NEKO_MULTI_WINDOW__ = true;
    h.window.location.pathname = '/web_chat_compact'; h.window.location.search = '?lanlan_name=A';
    h.window.__nekoInjectedConfig = { lanlan_name: 'A', switch_revision: 82, model_type: 'live3d', live3d_sub_type: 'mmd' };
    h.context.fetch = async url => {
        assert.ok(String(url).includes('lanlan_name=A'));
        return { json: async () => desktopAvatarConfig('A', 'mmd', 0, '/A/preview.pmx') };
    };
    h.load('static/js/index.js');
    assert.equal(await h.window.startPageConfigLoad(), true);
    assert.equal(h.window.lanlan_config.lanlan_name, 'A');
    assert.equal(h.window.lanlan_config.switch_revision, 0);
    assert.equal(h.window.mmdModel, '/A/preview.pmx');
    assert.equal(h.context.latestPageSwitchRevision(), 82, 'preview revision zero must not lower the IPC high-water');
});

test('stale positive pinned HTTP snapshot is rejected after the request has started', async () => {
    const h = harness(), body = deferred(), started = deferred();
    h.context.URLSearchParams = URLSearchParams; h.window.__NEKO_MULTI_WINDOW__ = true;
    h.window.location.pathname = '/web_chat_compact'; h.window.location.search = '?lanlan_name=Preview';
    h.window.__nekoInjectedConfig = { lanlan_name: 'Preview', switch_revision: 82 };
    let reads = 0;
    h.context.fetch = async url => {
        assert.ok(String(url).includes('lanlan_name=Preview'));
        reads++;
        if (reads === 1) {
            started.resolve();
            return { ok: true, json: async () => body.promise };
        }
        return { ok: false, status: 404 };
    };
    h.load('static/js/index.js');
    const loading = h.window.startPageConfigLoad();
    await started.promise;
    const before = {
        name: h.window.lanlan_config.lanlan_name,
        revision: h.window.lanlan_config.switch_revision,
        model: h.window.mmdModel,
    };
    h.window.appState._latestCatgirlSwitchRevision = 83;
    body.resolve(desktopAvatarConfig('Preview', 'mmd', 82, '/Preview/stale-after-start.pmx'));
    for (let turn = 0; turn < 12 && ![...h.timers.values()].some(timer => timer.delay === 600); turn++) {
        await new Promise(setImmediate);
    }
    assert.deepEqual(
        [h.window.lanlan_config.lanlan_name, h.window.lanlan_config.switch_revision, h.window.mmdModel],
        [before.name, before.revision, before.model],
        'the stale positive HTTP response must not mutate identity, revision, or model path'
    );
    assert.equal(h.context.latestPageSwitchRevision(), 83);
    const retry = [...h.timers.entries()].find(([, timer]) => timer.delay === 600);
    assert.ok(retry);
    h.timers.delete(retry[0]); retry[1].callback();
    assert.equal(await loading, false);
    assert.equal(reads, 2);
});

test('positive pinned preview snapshot is still ordered against the current-character high-water', async () => {
    const h = harness();
    h.context.URLSearchParams = URLSearchParams; h.window.__NEKO_MULTI_WINDOW__ = true;
    h.window.location.pathname = '/'; h.window.location.search = '?lanlan_name=Preview';
    h.window.__nekoInjectedConfig = { lanlan_name: 'Preview', switch_revision: 82 };
    let configReads = 0;
    h.context.fetch = async () => { configReads++; return { json: async () => desktopAvatarConfig('Preview', 'mmd', 82, '/Preview/stale.pmx') }; };
    h.load('static/js/index.js');
    h.window.lanlan_config = { lanlan_name: 'C', switch_revision: 83, model_type: 'live2d' };
    assert.equal(await h.window.startPageConfigLoad(), false);
    assert.equal(configReads, 0, 'stale positive pinned receipt is rejected before model config is fetched');
    assert.equal(h.window.lanlan_config.lanlan_name, 'C');
    assert.equal(h.window.lanlan_config.switch_revision, 83);
});

test('path-form preview uses the same explicit-character parser for desktop IPC bootstrap', async () => {
    const h = harness();
    h.context.URLSearchParams = URLSearchParams; h.window.__NEKO_MULTI_WINDOW__ = true;
    h.window.location.pathname = '/Preview'; h.window.location.search = '';
    h.window.__nekoInjectedConfig = { lanlan_name: 'A', switch_revision: 83 };
    h.context.fetch = async url => {
        assert.ok(String(url).includes('lanlan_name=Preview'));
        return { json: async () => desktopAvatarConfig('Preview', 'mmd', 0, '/Preview/path.pmx') };
    };
    h.load('static/js/index.js');
    assert.equal(await h.window.startPageConfigLoad(), true);
    assert.equal(h.window.lanlan_config.lanlan_name, 'Preview');
    assert.equal(h.window.lanlan_config.switch_revision, 0);
    assert.equal(h.window.mmdModel, '/Preview/path.pmx');
});

test('permanent model bootstrap failure releases the renderer gate', async () => {
    const h = harness();
    h.context.URLSearchParams = URLSearchParams; h.window.__NEKO_MULTI_WINDOW__ = true;
    h.window.location.pathname = '/web_chat_compact'; h.window.location.search = '';
    h.window.__nekoInjectedConfig = { lanlan_name: 'B', switch_revision: 82, model_type: 'live3d', live3d_sub_type: 'vrm' };
    h.context.fetch = async url => {
        if (String(url).startsWith('/api/config/page_config')) return { ok: false, status: 404 };
        return { ok: true, json: async () => ({}) };
    };
    h.load('static/js/index.js');
    assert.equal(await h.window.startPageConfigLoad(), false);
    assert.equal(h.window.__nekoPageModelConfigPending, null, 'non-retryable bootstrap failure must unblock normal renderer recovery');
});

test('failed page-model handoff keeps bootstrap ownership and schedules a retry', async () => {
    const h = harness(); let pageReads = 0, handoffs = 0;
    h.context.URLSearchParams = URLSearchParams; h.window.__NEKO_MULTI_WINDOW__ = true;
    h.window.location.pathname = '/web_chat_compact'; h.window.location.search = '';
    h.window.__nekoInjectedConfig = { lanlan_name: 'B', switch_revision: 82, model_type: 'live2d' };
    const cfg = desktopAvatarConfig('B', 'live2d', 82, '/B.model3.json');
    h.context.fetch = async url => {
        if (String(url).startsWith('/api/config/page_config')) {
            pageReads++;
            return pageReads === 1 ? { ok: true, json: async () => cfg } : { ok: false, status: 404 };
        }
        return { ok: true, json: async () => ({}) };
    };
    h.load('static/js/index.js');
    h.window.lanlan_config = { lanlan_name: 'A', switch_revision: 81, model_type: 'live2d' };
    h.window.appState = { isSwitchingCatgirl: false, _latestCatgirlSwitchRevision: 81, _pendingCatgirlSwitch: null };
    h.window.handleCatgirlSwitch = async () => { handoffs++; return false; };
    const loading = h.window.startPageConfigLoad();
    for (let turn = 0; turn < 12 && ![...h.timers.values()].some(timer => timer.delay === 600); turn++) await new Promise(setImmediate);
    assert.equal(handoffs, 1);
    assert.ok(h.window.__nekoPageModelConfigPending, 'failed handoff must retain bootstrap ownership for retry');
    await advanceModelBootTimer(h, 600);
    assert.equal(await loading, false);
    assert.equal(pageReads, 2, 'temporary handoff failure must retry page_config');
    assert.equal(h.window.__nekoPageModelConfigPending, null, 'permanent retry failure releases the renderer gate');
});

test('queued page-model handoff waits for the central switch result without a competing retry', async () => {
    const h = harness();
    h.context.URLSearchParams = URLSearchParams; h.window.__NEKO_MULTI_WINDOW__ = true;
    h.window.location.pathname = '/web_chat_compact'; h.window.location.search = '';
    h.window.__nekoInjectedConfig = { lanlan_name: 'B', switch_revision: 82, model_type: 'live2d' };
    const cfg = desktopAvatarConfig('B', 'live2d', 82, '/B.model3.json');
    h.context.fetch = async url => String(url).startsWith('/api/config/page_config')
        ? { ok: true, json: async () => cfg } : { ok: true, json: async () => ({}) };
    h.load('static/js/index.js');
    h.window.lanlan_config = { lanlan_name: 'A', switch_revision: 81, model_type: 'live2d' };
    h.window.appState = { isSwitchingCatgirl: true, _latestCatgirlSwitchRevision: 81, _pendingCatgirlSwitch: null };
    h.window.handleCatgirlSwitch = async (name, _old, options) => {
        h.window.appState._pendingCatgirlSwitch = { newCatgirl: name, switchRevision: options.switchRevision };
        return false;
    };
    const loading = h.window.startPageConfigLoad();
    for (let turn = 0; turn < 12 && ![...h.timers.values()].some(timer => timer.delay === 50); turn++) {
        await new Promise(setImmediate);
    }
    assert.ok(h.window.__nekoPageModelConfigPending, 'queued central switch keeps renderer gate until it settles');
    assert.equal([...h.timers.values()].some(timer => timer.delay === 600), false,
        'queued handoff must not start a competing page-model retry');

    h.window.appState.isSwitchingCatgirl = false;
    h.window.appState._pendingCatgirlSwitch = null;
    h.window.appState._latestCatgirlSwitchRevision = 82;
    h.window.lanlan_config = { lanlan_name: 'B', switch_revision: 82, model_type: 'live2d' };
    // Model the central commit point, not just the incoming identity watermark.
    const boot = h.window.__nekoPageModelConfigPending;
    h.window.__nekoPageModelConfigPending = null;
    boot.retire();
    const settle = [...h.timers.entries()].find(([, timer]) => timer.delay === 50);
    assert.ok(settle);
    h.timers.delete(settle[0]); settle[1].callback();

    assert.equal(await loading, true);
    assert.equal(h.window.__nekoPageModelConfigPending, null);
    assert.equal([...h.timers.values()].some(timer => timer.delay === 600), false);
});

test('queued page-model handoff failure releases into the bounded bootstrap retry', async () => {
    const h = harness(); let pageReads = 0;
    h.context.URLSearchParams = URLSearchParams; h.window.__NEKO_MULTI_WINDOW__ = true;
    h.window.location.pathname = '/web_chat_compact'; h.window.location.search = '';
    h.window.__nekoInjectedConfig = { lanlan_name: 'B', switch_revision: 82, model_type: 'live2d' };
    const cfg = desktopAvatarConfig('B', 'live2d', 82, '/B.model3.json');
    h.context.fetch = async url => {
        if (String(url).startsWith('/api/config/page_config')) {
            pageReads++;
            return pageReads === 1 ? { ok: true, json: async () => cfg } : { ok: false, status: 404 };
        }
        return { ok: true, json: async () => ({}) };
    };
    h.load('static/js/index.js');
    h.window.lanlan_config = { lanlan_name: 'A', switch_revision: 81, model_type: 'live2d' };
    h.window.appState = { isSwitchingCatgirl: true, _latestCatgirlSwitchRevision: 81, _pendingCatgirlSwitch: null };
    h.window.handleCatgirlSwitch = async (name, _old, options) => {
        h.window.appState._pendingCatgirlSwitch = { newCatgirl: name, switchRevision: options.switchRevision };
        return false;
    };
    const loading = h.window.startPageConfigLoad();
    for (let turn = 0; turn < 12 && ![...h.timers.values()].some(timer => timer.delay === 50); turn++) {
        await new Promise(setImmediate);
    }
    h.window.appState.isSwitchingCatgirl = false;
    h.window.appState._pendingCatgirlSwitch = null;
    const settle = [...h.timers.entries()].find(([, timer]) => timer.delay === 50);
    assert.ok(settle);
    h.timers.delete(settle[0]); settle[1].callback();

    for (let turn = 0; turn < 12 && ![...h.timers.values()].some(timer => timer.delay === 600); turn++) {
        await new Promise(setImmediate);
    }
    const retry = [...h.timers.entries()].find(([, timer]) => timer.delay === 600);
    assert.ok(retry, 'failed queued replay must return bootstrap ownership to the bounded retry');
    h.timers.delete(retry[0]); retry[1].callback();
    assert.equal(await loading, false);
    assert.equal(pageReads, 2);
    assert.equal(h.window.__nekoPageModelConfigPending, null);
});

test('failed model bootstrap preserves identity and a real same-revision receipt recovers it', async () => {
    const h = harness();
    const path = '/static/vrm/B.vrm', model = desktopAvatarConfig('B', 'vrm', 82, path);
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'A', model_type: 'live2d' }, async url => {
        if (url === '/api/characters/current_catgirl') return { json: async () => ({ current_catgirl: 'B', switch_revision: 82 }) };
        if (String(url).startsWith('/api/config/page_config')) return { ok: false, status: 503 };
        return { ok: true, json: async () => url === '/api/characters' ? { '猫娘': { B: { ...model, vrm: path } } } : {} };
    });
    await new Promise(setImmediate);
    assert.equal(h.window.lanlan_config.lanlan_name, 'B');
    assert.deepEqual(startup.loads, []);
    assert.equal(await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 82 }), true);
    assert.equal(await startup.ready, false, 'the committed live switch retires the unused boot payload');
    await startup.vrmBoot;
    assert.equal(await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 82 }), true);
    assert.deepEqual(startup.loads, [path]);
});

async function advanceModelBootTimer(h, delay) {
    const entry = [...h.timers.entries()].find(([, timer]) => timer.delay === delay);
    assert.ok(entry, 'expected active model timer ' + delay);
    const [id, timer] = entry;
    h.timers.delete(id);
    timer.callback();
    await new Promise(setImmediate);
}

for (const [kind, path] of [['live2d', '/B.model3.json'], ['mmd', '/static/mmd/B/B.pmx'], ['vrm', '/static/vrm/B.vrm']]) {
    test('single model config authority recovers first 503 for ' + kind, async () => {
        const h = harness(); let reads = 0;
        const cfg = desktopAvatarConfig('B', kind, 82, path);
        const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'B', switch_revision: 82 }, async url => {
            if (String(url).startsWith('/api/config/page_config')) {
                return ++reads === 1 ? { ok: false, status: 503 } : { json: async () => cfg };
            }
            return { ok: true, json: async () => ({}) };
        }, kind === 'live2d');
        let readiness = 'pending';
        h.window.pageConfigReady.then(result => { readiness = result; });
        await new Promise(setImmediate);
        assert.equal(readiness, 'pending');
        assert.deepEqual(startup.loads, []);
        assert.equal(reads, 1);
        await advanceModelBootTimer(h, 600);
        assert.equal(await startup.ready, true);
        await startup.vrmBoot; await new Promise(setImmediate);
        assert.deepEqual(startup.loads, [path]);
        assert.equal(reads, 2);
    });
}

for (const [phase, early] of [['headers', false], ['body', true]]) {
    test('stalled model read yields to its signed same-current receipt (' + phase + ')', async () => {
        const h = harness(), entered = deferred(), complete = deferred(), calls = avatarCalls(h);
        h.context.AbortController = AbortController;
        const path = '/static/vrm/B.vrm', model = desktopAvatarConfig('B', 'vrm', 82, path);
        let signal, reads = 0;
        const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'B', switch_revision: 82 }, async (url, options) => {
            if (String(url).startsWith('/api/config/page_config')) {
                signal = options.signal; reads++;
                if (phase === 'headers') { entered.resolve(); await complete.promise; }
                return { json: async () => {
                    if (phase === 'body') { entered.resolve(); await complete.promise; }
                    return { ...model, model_path: '/stale/B.vrm' };
                } };
            }
            return { ok: true, json: async () => url === '/api/characters' ? { '猫娘': { B: { ...model, vrm: path } } } : {} };
        });
        await entered.promise;
        if (early) await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 82 });
        assert.deepEqual(calls, []);
        await advanceModelBootTimer(h, 32000);
        assert.equal(signal?.aborted, true);
        if (!early) await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 82 });
        assert.equal(await startup.ready, false);
        await startup.vrmBoot;
        complete.resolve(); await new Promise(setImmediate);
        assert.deepEqual(startup.loads, [path]);
        assert.deepEqual(calls.map(c => c[0]), ['begin', 'commit']);
        assert.equal(reads, 1, 'a live commit cancels the scheduled retry');
    });
}

test('newer C retires the held B page request before a stale body can mutate anything', async () => {
    const h = harness(), entered = deferred(), complete = deferred(), calls = avatarCalls(h);
    h.context.AbortController = AbortController;
    const path = '/static/mmd/C/C.pmx', model = desktopAvatarConfig('C', 'mmd', 83, path);
    let signal;
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'B', switch_revision: 82 }, async (url, options) => {
        if (String(url).startsWith('/api/config/page_config')) {
            signal = options.signal;
            return { json: async () => { entered.resolve(); await complete.promise; return desktopAvatarConfig('B', 'mmd', 82, '/wrong/B.pmx'); } };
        }
        return { ok: true, json: async () => url === '/api/characters' ? { '猫娘': { C: { ...model, mmd: path } } } : {} };
    });
    await entered.promise;
    await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 82 });
    assert.equal(await h.window.handleCatgirlSwitch('C', 'B', { switchRevision: 83 }), true);
    await new Promise(setImmediate);
    assert.equal(signal?.aborted, true);
    assert.equal(await startup.ready, false);
    complete.resolve(); await startup.vrmBoot; await new Promise(setImmediate);
    await h.window.handleCatgirlSwitch('C', 'B', { switchRevision: 83 });
    assert.deepEqual(startup.loads, [path]);
    assert.deepEqual(calls.map(c => c[0]), ['begin', 'commit']);
    assert.equal(h.window.lanlan_config.lanlan_name, 'C');
    assert.equal(h.window.lanlan_config.switch_revision, 83);
});

test('central model bootstrap preserves recovery through a fourth successful read', async () => {
    const h = harness(); let reads = 0;
    const path = '/B.model3.json', cfg = desktopAvatarConfig('B', 'live2d', 82, path);
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'B', switch_revision: 82 }, async url => {
        if (String(url).startsWith('/api/config/page_config')) {
            reads++;
            return reads <= 3 ? { ok: false, status: 503 } : { json: async () => cfg };
        }
        return { ok: true, json: async () => ({}) };
    }, true);
    await new Promise(setImmediate);
    await advanceModelBootTimer(h, 600);
    await advanceModelBootTimer(h, 1200);
    await advanceModelBootTimer(h, 1800);
    assert.equal(await startup.ready, true);
    await startup.vrmBoot; await new Promise(setImmediate);
    assert.equal(reads, 4);
    assert.deepEqual(startup.loads, [path]);
});
test('bootstrap exhaustion is finite and still permits an authoritative receipt', async () => {
    const h = harness(), calls = avatarCalls(h); let reads = 0;
    const path = '/static/mmd/B/B.pmx', model = desktopAvatarConfig('B', 'mmd', 82, path);
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'B', switch_revision: 82 }, async url => {
        if (String(url).startsWith('/api/config/page_config')) { reads++; return { ok: false, status: 503 }; }
        return { ok: true, json: async () => url === '/api/characters' ? { '猫娘': { B: { ...model, mmd: path } } } : {} };
    });
    await new Promise(setImmediate);
    for (const delay of [600, 1200, 1800, 2400, 3000, 3600]) {
        await advanceModelBootTimer(h, delay);
    }
    assert.equal(await startup.ready, false);
    await startup.vrmBoot;
    assert.equal(reads, 7);
    assert.deepEqual(startup.loads, []);
    assert.equal(await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 81 }), false);
    await h.window.handleCatgirlSwitch('B', 'A');
    assert.deepEqual(calls, [], 'a legacy unstamped message cannot take over a verified identity');
    assert.equal(await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 82 }), true);
    assert.deepEqual(startup.loads, [path]);
    assert.deepEqual(calls.map(c => c[0]), ['begin', 'commit']);
});

for (const kind of ['vrm', 'mmd']) {
    test('late legacy identity bootstrap recognizes a committed healthy ' + kind, async () => {
        const h = harness(), entered = deferred(), identity = deferred(), calls = avatarCalls(h);
        const path = kind === 'vrm' ? '/static/vrm/B.vrm' : '/static/mmd/B/B.pmx';
        const model = desktopAvatarConfig('B', kind, 82, path);
        let fullReads = 0;
        const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'A' }, async url => {
            if (url === '/api/characters/current_catgirl') {
                entered.resolve(); await identity.promise;
                return { json: async () => ({ current_catgirl: 'B', switch_revision: 82 }) };
            }
            if (String(url).startsWith('/api/config/page_config')) { fullReads++; return { ok: false, status: 503 }; }
            return { ok: true, json: async () => url === '/api/characters' ? { '猫娘': { B: { ...model, [kind]: path } } } : {} };
        });
        await entered.promise;
        assert.equal(await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 82 }), true);
        identity.resolve(); await new Promise(setImmediate);
        assert.equal(fullReads, 0, 'a healthy committed role must not get a second boot owner');
        assert.equal(await startup.ready, true);
        await startup.vrmBoot; await new Promise(setImmediate);
        await h.window.handleCatgirlSwitch('B', 'B', { switchRevision: 82 });
        assert.deepEqual(calls.map(c => c[0]), ['begin', 'commit']);
        assert.deepEqual(startup.loads, [path]);
    });
}

test('a truly missing previously committed model still permits verified bootstrap and receipt recovery', async () => {
    const h = harness(), entered = deferred(), identity = deferred(), calls = avatarCalls(h);
    const path = '/static/mmd/B/B.pmx', model = desktopAvatarConfig('B', 'mmd', 82, path);
    let fullReads = 0;
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'A' }, async url => {
        if (url === '/api/characters/current_catgirl') {
            entered.resolve(); await identity.promise;
            return { json: async () => ({ current_catgirl: 'B', switch_revision: 82 }) };
        }
        if (String(url).startsWith('/api/config/page_config')) { fullReads++; return { ok: false, status: 503 }; }
        return { ok: true, json: async () => url === '/api/characters' ? { '猫娘': { B: { ...model, mmd: path } } } : {} };
    });
    await entered.promise;
    assert.equal(await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 82 }), true);
    h.window.mmdManager.currentModel = null;
    identity.resolve(); await new Promise(setImmediate);
    assert.equal(fullReads, 1);
    assert.equal(await h.window.handleCatgirlSwitch('B', 'B', { switchRevision: 82 }), true);
    assert.equal(await startup.ready, false);
    await startup.vrmBoot;
    assert.deepEqual(calls.map(c => c[0]), ['begin', 'commit', 'begin', 'commit']);
    assert.deepEqual(startup.loads, [path, path], 'one original load plus one genuine recovery');
});

for (const alreadyLoading of [false, true]) {
    test('verified snapshot alone recovers a lost committed VRM, excluding a real load: ' + alreadyLoading, async () => {
        const h = harness(), entered = deferred(), identity = deferred(), calls = avatarCalls(h);
        const path = '/static/vrm/B.vrm', model = desktopAvatarConfig('B', 'vrm', 82, path);
        let reads = 0;
        const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'A' }, async url => {
            if (url === '/api/characters/current_catgirl') {
                entered.resolve(); await identity.promise;
                return { json: async () => ({ current_catgirl: 'B', switch_revision: 82 }) };
            }
            if (String(url).startsWith('/api/config/page_config')) {
                return ++reads === 1 ? { ok: false, status: 503 } : { json: async () => model };
            }
            return { ok: true, json: async () => url === '/api/characters' ? { '猫娘': { B: { ...model, vrm: path } } } : {} };
        });
        await entered.promise;
        assert.equal(await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 82 }), true);
        h.window.vrmManager.currentModel = null;
        h.window.vrmManager._isLoadingModel = alreadyLoading;
        identity.resolve(); await new Promise(setImmediate);
        await advanceModelBootTimer(h, 600);
        assert.equal(await startup.ready, true);
        await startup.vrmBoot;
        assert.equal(reads, 2);
        assert.deepEqual(startup.loads, alreadyLoading ? [path] : [path, path]);
        assert.deepEqual(calls.map(c => c[0]), alreadyLoading ? ['begin', 'commit'] : ['begin', 'commit', 'begin', 'commit']);
    });
}

test('pre-index Live2D startup does not refetch or reload after C has committed', async () => {
    const h = harness(), calls = avatarCalls(h); let reads = 0;
    const domReady = [];
    h.context.document.addEventListener = (event, listener) => {
        if (event === 'DOMContentLoaded') domReady.push(listener);
    };
    const path = '/C.model3.json', config = desktopAvatarConfig('C', 'live2d', 83, path);
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'B', switch_revision: 82 }, async url => {
        if (String(url).startsWith('/api/config/page_config')) { reads++; return { ok: false, status: 503 }; }
        if (url.includes('/current_live2d_model')) return { ok: true, json: async () => ({
            success: true, model_info: { path },
        }) };
        return { ok: true, json: async () => url === '/api/characters' ? { '猫娘': { C: config } } : {} };
    }, 'before-index');
    await new Promise(setImmediate);
    assert.equal(await h.window.handleCatgirlSwitch('C', 'B', { switchRevision: 83 }), true);
    assert.equal(await startup.ready, false);
    for (const ready of domReady) ready();
    if ([...h.timers.values()].some(timer => timer.delay === 1000)) await advanceModelBootTimer(h, 1000);
    for (const [, timer] of [...h.timers]) if (timer.delay <= 4000) timer.callback();
    await new Promise(setImmediate);
    assert.equal(reads, 1);
    assert.deepEqual(startup.loads, [path]);
    assert.deepEqual(calls.map(c => c[0]), ['begin', 'commit']);
});

test('initial Live2D startup rechecks ownership immediately before delayed model load', () => {
    const source = fs.readFileSync(path.join(root, 'static/live2d/live2d-init.js'), 'utf8');
    const ownership = source.match(/const stillOwnsInitialLive2DStartup = \(\) => \{[\s\S]*?^    \};/m)?.[0] || '';
    assert.match(ownership, /currentPath === targetModelPath[\s\S]*?\(!hasMountedLive2D\(\) \|\| recoveringSkippedTutorialBoot\)/);
    assert.match(source, /hasMountedLive2D\(\) && !recoveringSkippedTutorialBoot/, 'tutorial recovery may replace the still-mounted Yui model');
    assert.doesNotMatch(ownership, /lanlan_config\?\.switch_revision/, 'receipt high-water mark is not renderer-commit ownership');
    assert.match(source, /const ownsInitialLive2DStartupAfterSwitchSettlement = async \(\) => \{[\s\S]*?isSwitchingCatgirl[\s\S]*?setTimeout\(resolve, 50\)/);
    assert.match(
        source,
        /if \(!await ownsInitialLive2DStartupAfterSwitchSettlement\(\)\) \{[\s\S]{0,500}?return;[\s\S]{0,500}?live2dManager\.loadModel\(targetModelPath/,
        'the settlement-aware ownership check must stay between delayed preference work and loadModel'
    );
    assert.match(source, /confirmRestoredRenderer\?\.\(startupName, 'live2d'/);
    const vrmSource = fs.readFileSync(path.join(root, 'static/vrm/vrm-init.js'), 'utf8');
    assert.match(vrmSource, /confirmRestoredRenderer\?\.\(startupName, 'vrm'/);
});

test('failed takeover may raise receipt revision without cancelling authoritative Live2D startup', async () => {
    const h = harness(), preferences = deferred(), preferencesStarted = deferred();
    const pathA = '/A.model3.json';
    const modelA = desktopAvatarConfig('A', 'live2d', 82, pathA);
    const startup = startDesktopAvatarBootstrap(
        h,
        { lanlan_name: 'A', switch_revision: 82, model_type: 'live2d' },
        async url => {
            if (String(url).startsWith('/api/config/page_config')) return { json: async () => modelA };
            if (url === '/api/characters') return { ok: false, status: 503, json: async () => ({}) };
            return { ok: true, json: async () => ({}) };
        },
        true,
        {
            live2dPreferences: async () => {
                preferencesStarted.resolve();
                await preferences.promise;
                return [];
            },
        }
    );
    assert.equal(await startup.ready, true);
    await preferencesStarted.promise;

    assert.equal(await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 83 }), false);
    assert.equal(h.window.lanlan_config.lanlan_name, 'A');
    assert.equal(h.window.lanlan_config.switch_revision, 83, 'receipt high-water mark remains newer after failed takeover');

    preferences.resolve();
    for (let turn = 0; turn < 12 && !startup.loads.includes(pathA); turn++) {
        await new Promise(setImmediate);
    }
    assert.ok(startup.loads.includes(pathA), 'A startup must continue because A name/path still own the renderer');
});


test('Live2D loaded ahead of index waits past the old 5-second local fallback for the shared retry', async () => {
    const h = harness(), delayed = deferred(), domReady = [];
    h.context.document.addEventListener = (event, listener) => {
        if (event === 'DOMContentLoaded') domReady.push(listener);
    };
    let reads = 0;
    const path = '/B.model3.json';
    const cfg = desktopAvatarConfig('B', 'live2d', 82, path);
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'B', switch_revision: 82 }, async url => {
        if (String(url).startsWith('/api/config/page_config')) {
            if (++reads === 1) return { ok: false, status: 503 };
            await delayed.promise;
            return { json: async () => cfg };
        }
        return { ok: true, json: async () => ({}) };
    }, 'before-index');
    for (const ready of domReady) ready();
    await new Promise(setImmediate);
    await advanceModelBootTimer(h, 600);
    if ([...h.timers.values()].some(timer => timer.delay === 1000)) await advanceModelBootTimer(h, 1000);
    for (const [id, timer] of [...h.timers]) if (timer.delay === 5000) {
        h.timers.delete(id);
        timer.callback();
    }
    await new Promise(setImmediate);
    assert.deepEqual(startup.loads, []);
    delayed.resolve();
    assert.equal(await startup.ready, true);
    await startup.vrmBoot; await new Promise(setImmediate);
    assert.deepEqual(startup.loads, [path]);
    assert.equal(reads, 2);
});

test('MMD startup waits for an in-flight switch to settle and resumes when takeover fails', async () => {
    const h = harness(), settings = deferred(), settingsStarted = deferred();
    const heldRoles = deferred(), rolesStarted = deferred();
    const canvas = { style: {}, dataset: {} };
    h.context.document.getElementById = id => id === 'mmd-canvas' ? canvas : null;
    const pathB = '/static/mmd/B/B.pmx';
    const modelB = desktopAvatarConfig('B', 'mmd', 82, pathB);
    let holdSwitchRead = false;
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'B', switch_revision: 82, model_type: 'live3d', live3d_sub_type: 'mmd' }, async url => {
        if (String(url).startsWith('/api/config/page_config')) return { json: async () => modelB };
        if (url.endsWith('/B/mmd_settings')) {
            settingsStarted.resolve();
            await settings.promise;
            return { ok: true, json: async () => ({ success: false }) };
        }
        if (url === '/api/characters' && holdSwitchRead) {
            rolesStarted.resolve();
            return heldRoles.promise;
        }
        if (url === '/api/characters') return { ok: true, json: async () => ({ ['\u732b\u5a18']: { B: modelB } }) };
        return { ok: true, json: async () => ({}) };
    });
    assert.equal(await startup.ready, true);

    await settingsStarted.promise;
    holdSwitchRead = true;
    const switching = h.window.handleCatgirlSwitch('C', 'B', { switchRevision: 83 });
    await rolesStarted.promise;

    settings.resolve();
    let settlementTimers = [];
    for (let turn = 0; turn < 8 && settlementTimers.length === 0; turn++) {
        await new Promise(setImmediate);
        settlementTimers = [...h.timers.entries()].filter(([, timer]) => timer.delay === 50);
    }
    assert.ok(settlementTimers.length > 0,
        'MMD startup should wait instead of abandoning A/B ownership while switch is active');

    heldRoles.resolve({ ok: false, status: 503, json: async () => ({}) });
    assert.equal(await switching, false);

    // VRM startup also uses a 50ms switch-settlement wait now. The harness can
    // therefore hold more than one legitimate settlement timer; advancing only
    // the first one makes this test depend on insertion order and can leave the
    // MMD waiter parked forever. After the switch has settled, drain every
    // settlement waiter while still requiring the MMD model to actually resume.
    for (let turn = 0; turn < 16 && canvas.style.visibility !== 'visible'; turn++) {
        for (const [id, timer] of [...h.timers.entries()]) {
            if (timer.delay !== 50) continue;
            h.timers.delete(id);
            timer.callback();
        }
        await new Promise(setImmediate);
    }

    assert.ok(startup.loads.includes(pathB), 'failed takeover leaves B current, so its startup must resume');
    assert.equal(canvas.style.visibility, 'visible');
});


test('MMD startup restarts when manager initialization loses ownership to a failed takeover', async () => {
    const h = harness(), firstInit = deferred(), initStarted = deferred();
    const pathB = '/static/mmd/B/B.pmx', pathC = '/static/mmd/C/C.pmx';
    const modelB = desktopAvatarConfig('B', 'mmd', 82, pathB);
    const modelC = desktopAvatarConfig('C', 'mmd', 83, pathC);
    let initCount = 0, bSettingsReads = 0;
    const startup = startDesktopAvatarBootstrap(
        h,
        { lanlan_name: 'B', switch_revision: 82, model_type: 'live3d', live3d_sub_type: 'mmd' },
        async url => {
            if (String(url).startsWith('/api/config/page_config')) return { json: async () => modelB };
            if (url.endsWith('/B/mmd_settings')) {
                bSettingsReads++;
                return { ok: true, json: async () => ({ success: false }) };
            }
            if (url.endsWith('/C/mmd_settings')) return { ok: true, json: async () => ({ success: false }) };
            if (url === '/api/characters') return { ok: true, json: async () => ({ ['猫娘']: {
                B: { ...modelB, mmd: pathB, _reserved: { character_uid: UID_B } },
                C: { ...modelC, mmd: pathC, _reserved: { character_uid: 'c'.repeat(32) } },
            } }) };
            return { ok: true, json: async () => ({}) };
        },
        false,
        {
            mmdInit: async ({ manager }) => {
                initCount++;
                if (initCount === 1) {
                    initStarted.resolve();
                    await firstInit.promise;
                }
                return manager;
            },
            mmdLoadModel: async ({ url }) => {
                if (url === pathC) {
                    throw new Error('C load failed before mounting a mesh after replacing B during B init');
                }
                return undefined;
            },
        }
    );
    assert.equal(await startup.ready, true);
    await initStarted.promise;
    const originalManager = h.window.mmdManager;

    assert.equal(await h.window.handleCatgirlSwitch('C', 'B', { switchRevision: 83 }), false);
    assert.ok(originalManager._isDisposed, 'takeover disposes the manager whose init is still suspended');
    assert.equal(h.window.lanlan_config.lanlan_name, 'B');

    firstInit.resolve();
    for (let turn = 0; turn < 24 && h.window.mmdManager?.currentModel?.url !== pathB; turn++) {
        await new Promise(setImmediate);
    }

    assert.equal(h.window.mmdManager?.currentModel?.url, pathB,
        'rollback must start B again even though its original init returned null');
    assert.ok(h.window.mmdManager?.currentModel?.mesh);
    assert.ok(initCount >= 3, 'B init, C takeover, and fresh B recovery must each own a manager');
    assert.ok(bSettingsReads >= 1);
});

test('MMD startup restarts from current config after an MMD-to-MMD takeover disposes its manager then fails', async () => {
    const h = harness(), firstSettings = deferred(), settingsStarted = deferred();
    const pathB = '/static/mmd/B/B.pmx', pathC = '/static/mmd/C/C.pmx';
    const modelB = desktopAvatarConfig('B', 'mmd', 82, pathB);
    const modelC = desktopAvatarConfig('C', 'mmd', 83, pathC);
    let bSettingsReads = 0;
    const startup = startDesktopAvatarBootstrap(
        h,
        { lanlan_name: 'B', switch_revision: 82, model_type: 'live3d', live3d_sub_type: 'mmd' },
        async url => {
            if (String(url).startsWith('/api/config/page_config')) return { json: async () => modelB };
            if (url.endsWith('/B/mmd_settings')) {
                bSettingsReads++;
                if (bSettingsReads === 1) {
                    settingsStarted.resolve();
                    await firstSettings.promise;
                }
                return { ok: true, json: async () => ({ success: false }) };
            }
            if (url.endsWith('/C/mmd_settings')) return { ok: true, json: async () => ({ success: false }) };
            if (url === '/api/characters') return { ok: true, json: async () => ({ ['猫娘']: {
                B: { ...modelB, mmd: pathB, _reserved: { character_uid: UID_B } },
                C: { ...modelC, mmd: pathC, _reserved: { character_uid: 'c'.repeat(32) } },
            } }) };
            return { ok: true, json: async () => ({}) };
        },
        false,
        {
            mmdLoadModel: async ({ url, manager }) => {
                if (url === pathC) {
                    manager.currentModel = { url, mesh: {} };
                    throw new Error('C load failed after MMD manager replacement');
                }
                return undefined;
            },
        }
    );
    assert.equal(await startup.ready, true);
    await settingsStarted.promise;
    const originalManager = h.window.mmdManager;

    assert.equal(await h.window.handleCatgirlSwitch('C', 'B', { switchRevision: 83 }), false);
    const failedManager = h.window.mmdManager;
    assert.ok(originalManager._isDisposed);
    assert.equal(h.window.lanlan_config.lanlan_name, 'B');
    firstSettings.resolve();

    for (let turn = 0; turn < 20 && h.window.mmdManager?.currentModel?.url !== pathB; turn++) {
        await new Promise(setImmediate);
    }
    const restoredManager = h.window.mmdManager;
    assert.ok(startup.loads.includes(pathC), 'the failed takeover reached the replacement MMD manager');
    assert.notEqual(restoredManager, originalManager);
    assert.notEqual(restoredManager, failedManager);
    assert.equal(restoredManager?.currentModel?.url, pathB, 'the fresh manager must actually own the restored mesh');
    assert.ok(restoredManager.currentModel.mesh);
    assert.ok(bSettingsReads >= 2, 'recovery must re-read B settings rather than reuse the disposed startup instance');
});

for (const refresh of [false, true]) test('stale MMD startup respects the latest same-name path (HTTP refresh: ' + refresh + ')', async () => {
    const h = harness(), firstSettings = deferred(), settingsStarted = deferred();
    const oldPath = '/static/mmd/B/old.pmx', newPath = '/static/mmd/B/new.pmx', savedPath = '/static/mmd/B/saved.pmx';
    const model = path => desktopAvatarConfig('B', 'mmd', path === oldPath ? 82 : 83, path);
    let currentPath = oldPath, settingsReads = 0;
    const startup = startDesktopAvatarBootstrap(
        h,
        { lanlan_name: 'B', switch_revision: 82, model_type: 'live3d', live3d_sub_type: 'mmd' },
        async url => {
            if (String(url).startsWith('/api/config/page_config')) return { json: async () => model(currentPath) };
            if (url.endsWith('/B/mmd_settings')) {
                settingsReads++;
                if (settingsReads === 1) {
                    settingsStarted.resolve();
                    await firstSettings.promise;
                }
                return { ok: true, json: async () => ({ success: false }) };
            }
            if (url === '/api/characters') {
                return { ok: true, json: async () => ({ ['猫娘']: {
                    B: { ...model(currentPath), mmd: currentPath, _reserved: { character_uid: UID_B } },
                } }) };
            }
            return { ok: true, json: async () => ({}) };
        }
    );
    assert.equal(await startup.ready, true);
    await settingsStarted.promise;

    currentPath = newPath;
    assert.equal(await h.window.handleCatgirlSwitch('B', 'B', { switchRevision: 83, forceReload: true }), true);
    const committedManager = h.window.mmdManager;
    assert.equal(committedManager?.currentModel?.url, newPath);
    assert.equal(h.window.lanlan_config.switch_revision, 83);

    if (refresh) {
        // Saving avatar.mmd.model_path does not advance the character-switch revision.
        currentPath = savedPath;
        assert.equal(await h.window.reloadPageConfig(), true);
        assert.equal(h.window.mmdModel, savedPath);
        assert.equal(h.window.lanlan_config.switch_revision, 83);
    }
    firstSettings.resolve();
    for (let turn = 0; turn < 12; turn++) await new Promise(setImmediate);
    if (refresh) {
        assert.equal(h.window.mmdManager.currentModel?.url, savedPath);
        assert.equal(h.window.mmdModel, savedPath);
        assert.deepEqual(startup.loads, [newPath, savedPath]);
    } else {
        assert.equal(h.window.mmdManager, committedManager);
        assert.equal(committedManager._isDisposed, false);
        assert.equal(committedManager.currentModel.url, newPath);
        assert.equal(h.window.mmdModel, newPath);
    }
    assert.ok(!startup.loads.includes(oldPath));
});

test('same-name MMD path refresh retires the old startup and loads only the new path', async () => {
    const h = harness(), firstSettings = deferred(), settingsStarted = deferred();
    const oldPath = '/static/mmd/B/old.pmx', newPath = '/static/mmd/B/new.pmx';
    const modelB = desktopAvatarConfig('B', 'mmd', 82, oldPath);
    let settingsReads = 0;
    const startup = startDesktopAvatarBootstrap(
        h,
        { lanlan_name: 'B', switch_revision: 82, model_type: 'live3d', live3d_sub_type: 'mmd' },
        async url => {
            if (String(url).startsWith('/api/config/page_config')) return { json: async () => modelB };
            if (url.endsWith('/B/mmd_settings')) {
                settingsReads++;
                if (settingsReads === 1) {
                    settingsStarted.resolve();
                    await firstSettings.promise;
                }
                return { ok: true, json: async () => ({ success: false }) };
            }
            return { ok: true, json: async () => ({ ['猫娘']: { B: modelB } }) };
        }
    );
    assert.equal(await startup.ready, true);
    await settingsStarted.promise;

    h.window.mmdModel = newPath;
    firstSettings.resolve();
    for (let turn = 0; turn < 20 && !startup.loads.includes(newPath); turn++) {
        await new Promise(setImmediate);
    }

    assert.ok(!startup.loads.includes(oldPath), 'the stale startup must not load the old MMD path');
    assert.ok(startup.loads.includes(newPath), 'a fresh startup must pick up the new same-name MMD path');
});

for (const rejectBeforeTakeover of [false, true]) {
    test('late MMD settings respect the renderer owner (C succeeds: ' + !rejectBeforeTakeover + ')', async () => {
        const h = harness(), settings = deferred(), started = deferred(), canvas = { style: {}, dataset: {} };
        h.context.document.getElementById = id => id === 'mmd-canvas' ? canvas : null;
        const pathB = '/static/mmd/B/B.pmx', pathC = '/static/mmd/C/C.pmx';
        const modelB = desktopAvatarConfig('B', 'mmd', 82, pathB);
        const modelC = desktopAvatarConfig('C', 'mmd', 83, pathC);
        const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'A', model_type: 'live2d' }, async url => {
            if (url === '/api/characters/current_catgirl') return { json: async () => ({ current_catgirl: 'B', switch_revision: 82 }) };
            if (String(url).startsWith('/api/config/page_config')) return { json: async () => modelB };
            if (url.endsWith('/B/mmd_settings')) { started.resolve(); await settings.promise; }
            if (url === '/api/characters' && rejectBeforeTakeover) return { ok: false, status: 503 };
            return { ok: true, json: async () => url === '/api/characters' ? { '猫娘': { C: { ...modelC, mmd: pathC } } } : {} };
        });
        assert.equal(await startup.ready, true);
        await started.promise;
        assert.equal(await h.window.handleCatgirlSwitch('C', 'B', { switchRevision: 83 }), !rejectBeforeTakeover);
        settings.resolve();

        // The bootstrap harness launches VRM startup alongside MMD startup. VRM
        // now waits for switch settlement with the same fake 50ms timer used by
        // the MMD startup path. Advance those harness-only settlement waiters so
        // this MMD ownership test does not leave the unrelated VRM boot promise
        // parked after the switch has already settled.
        let vrmBootSettled = false;
        Promise.resolve(startup.vrmBoot).finally(() => { vrmBootSettled = true; });
        for (let turn = 0; turn < 16 && !vrmBootSettled; turn++) {
            for (const [id, timer] of [...h.timers.entries()]) {
                if (timer.delay !== 50) continue;
                h.timers.delete(id);
                timer.callback();
            }
            await new Promise(resolve => setImmediate(resolve));
        }
        assert.equal(vrmBootSettled, true, 'unrelated VRM bootstrap must settle after the switch does');
        await startup.vrmBoot;
        assert.equal(h.window.lanlan_config.lanlan_name, rejectBeforeTakeover ? 'B' : 'C');
        assert.deepEqual(startup.loads, [rejectBeforeTakeover ? pathB : pathC]);
        assert.equal(canvas.style.visibility, 'visible');
        assert.equal(canvas.style.pointerEvents, 'auto');
    });
}


test('unnamed VRM cold startup binds resolved identity before ownership check', async () => {
    const h = harness(), w = h.window, ready = deferred(), loads = [];
    w.location.pathname = '/'; w.location.href = 'https://example.test/';
    h.context.document.querySelector = () => null;
    h.context.requestAnimationFrame = () => 0;
    h.context.URL = URL;
    w.vrmModuleLoaded = true;
    w.VRMCore = {};
    w.lanlan_config = { lanlan_name: '', model_type: 'live3d', live3d_sub_type: 'vrm' };
    w.vrmModel = '/static/vrm/A.vrm';
    w.pageConfigReady = ready.promise;
    w.appState._switchAttemptCounter = 0;
    h.context.fetch = async url => ({ ok: true, json: async () => url === '/api/characters'
        ? { '猫娘': { A: { model_type: 'live3d', live3d_sub_type: 'vrm', vrm: '/static/vrm/A.vrm',
            _reserved: { character_uid: UID_A } } } }
        : {} });
    h.load('static/vrm/vrm-manager.js');
    h.load('static/vrm/vrm-init.js');
    const manager = Object.create(w.VRMManager.prototype);
    Object.assign(manager, {
        currentModel: null, _pendingModelLoadCount: 0, _activeLoadToken: 0,
        initThreeJS: async () => {}, pauseRendering() {}, resumeRendering() {}, cleanupUI() {},
        async _loadModelInternal(url, _options, token) {
            loads.push(url);
            if (this._isLoadTokenActive(token)) this.currentModel = { url, vrm: {} };
            return this.currentModel;
        }
    });
    w.vrmManager = manager;
    h.load('static/app/app-character.js');

    const boot = w.initVRMModel();
    await new Promise(setImmediate);
    w.lanlan_config.lanlan_name = 'A';
    w.lanlan_config.switch_revision = 84;
    w.appState._switchAttemptCounter = 1;
    ready.resolve(true);
    await boot;

    assert.deepEqual(loads, ['/static/vrm/A.vrm']);
    assert.equal(w.vrmManager.currentModel.url, '/static/vrm/A.vrm');
});

test('VRM startup waits for an in-flight switch to settle and resumes when takeover fails', async () => {
    const h = harness(), metadata = deferred(), metadataStarted = deferred();
    const heldRoles = deferred(), rolesStarted = deferred();
    const pathA = '/static/vrm/A.vrm';
    const modelA = desktopAvatarConfig('A', 'vrm', 82, pathA);
    let holdSwitchRead = false;
    const startup = startDesktopAvatarBootstrap(
        h,
        { lanlan_name: 'A', switch_revision: 82, model_type: 'live3d', live3d_sub_type: 'vrm' },
        async url => {
            if (String(url).startsWith('/api/config/page_config')) return { json: async () => modelA };
            if (url === '/api/model/vrm/config') {
                metadataStarted.resolve();
                await metadata.promise;
            }
            if (url === '/api/characters' && holdSwitchRead) {
                rolesStarted.resolve();
                return heldRoles.promise;
            }
            if (url === '/api/characters') {
                return { ok: true, json: async () => ({ ['猫娘']: { A: { ...modelA, vrm: pathA } } }) };
            }
            return { ok: true, json: async () => ({}) };
        }
    );
    const restored = [];
    const confirm = h.window.appCharacter.confirmRestoredRenderer;
    h.window.appCharacter.confirmRestoredRenderer = (...args) => { restored.push(args); return confirm(...args); };
    assert.equal(await startup.ready, true);
    await metadataStarted.promise;

    holdSwitchRead = true;
    const switching = h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 83 });
    await rolesStarted.promise;

    metadata.resolve();
    let settleTimer;
    for (let turn = 0; turn < 8 && !settleTimer; turn++) {
        await new Promise(setImmediate);
        settleTimer = [...h.timers.entries()].find(([, timer]) => timer.delay === 50);
    }
    assert.ok(settleTimer, 'VRM startup should wait instead of abandoning A ownership while switch is active');

    heldRoles.resolve({ ok: false, status: 503, json: async () => ({}) });
    assert.equal(await switching, false);
    holdSwitchRead = false;
    h.timers.delete(settleTimer[0]);
    settleTimer[1].callback();

    await startup.vrmBoot;
    for (let turn = 0; turn < 12 && !startup.loads.includes(pathA); turn++) {
        await new Promise(setImmediate);
    }
    assert.equal(h.window.lanlan_config.lanlan_name, 'A');
    assert.equal(restored.length, 1);
    assert.equal(restored[0][2], h.window.vrmManager.currentModel.vrm, 'compare the actual mounted VRM object');
    assert.ok(startup.loads.includes(pathA), 'failed takeover leaves A current, so its VRM startup must resume');
});

test('VRM boot applies its own outline after a different switch fails before takeover', async () => {
    const h = harness(), metadata = deferred(), entered = deferred(), outlined = [];
    const path = '/static/vrm/B.vrm', model = desktopAvatarConfig('B', 'vrm', 82, path);
    const lighting = { outlineWidthScale: 1.75 };
    let failNextRoles = true;
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'A', model_type: 'live2d' }, async url => {
        if (url === '/api/characters/current_catgirl') return { json: async () => ({ current_catgirl: 'B', switch_revision: 82 }) };
        if (String(url).startsWith('/api/config/page_config')) return { json: async () => ({ ...model, lighting }) };
        if (url === '/api/model/vrm/config') { entered.resolve(); await metadata.promise; }
        if (url === '/api/characters' && failNextRoles) {
            failNextRoles = false;
            return { ok: false, status: 503 };
        }
        return { ok: true, json: async () => ({ '猫娘': { B: { ...model, lighting } } }) };
    });
    h.context.requestAnimationFrame = callback => { queueMicrotask(callback); return 0; };
    h.window.vrmManager.core = { applyOutlineWidth: value => outlined.push(value) };
    assert.equal(await startup.ready, true);
    await entered.promise;
    assert.equal(await h.window.handleCatgirlSwitch('C', 'B', { switchRevision: 83 }), false);
    metadata.resolve(); await startup.vrmBoot;
    assert.equal(h.window.lanlan_config.lanlan_name, 'B');
    assert.deepEqual(startup.loads, [path]);
    assert.deepEqual(outlined, [1.75]);
});

test('a newer page snapshot supersedes an older switch already loading', async () => {
    const h = harness(); const calls = avatarCalls(h);
    h.context.URLSearchParams = URLSearchParams;
    h.load('static/js/index.js'); h.load('static/app/app-character.js');
    const originalFetch = h.context.fetch;
    const pageStarted = deferred(), pageReply = deferred(), roleStarted = deferred(), roleReply = deferred();
    let held = false;
    h.context.fetch = async (url, ...args) => {
        if (String(url).startsWith('/api/config/page_config')) {
            pageStarted.resolve(); return pageReply.promise;
        }
        if (!held) { held = true; roleStarted.resolve(); await roleReply.promise; }
        return originalFetch(url, ...args);
    };
    const loading = h.window.startPageConfigLoad();
    await pageStarted.promise;
    const older = h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 922 });
    await roleStarted.promise;
    pageReply.resolve({ json: async () => ({
        success: true, lanlan_name: 'A', switch_revision: 923, model_type: 'live2d'
    }) });
    assert.equal(await loading, false, 'a queued target is not yet applied');
    roleReply.resolve(); await older;
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(h.window.lanlan_config.lanlan_name, 'A');
    assert.equal(h.window.lanlan_config.switch_revision, 923);
    assert.equal(calls.filter(x => x[0] === 'commit').at(-1)[2].name, 'A');
});

test('same-name backend re-selection retries the frontend rollback', async () => {
    const h = harness(), calls = avatarCalls(h), fetch = h.context.fetch;
    h.load('static/app/app-character.js');
    h.context.fetch = async () => { throw new Error('temporary role failure'); };
    await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 10 });
    assert.equal(h.window.lanlan_config.lanlan_name, 'A');
    h.context.fetch = fetch;
    await h.window.handleCatgirlSwitch('B', 'B', { switchRevision: 11 });
    await h.window.handleCatgirlSwitch('B', 'B', { switchRevision: 11 });
    assert.deepEqual(calls.map(c => c[0]), ['begin', 'rollback', 'begin', 'commit']);
    assert.equal(h.window.lanlan_config.lanlan_name, 'B');
});

for (const failFirst of [false, true]) {
    test('same revision BC duplicate after settlement: first attempt ' + (failFirst ? 'failed' : 'committed'), async () => {
        const h = harness(), calls = avatarCalls(h), fetch = h.context.fetch;
        h.load('static/app/app-character.js');
        const deliver = loadSwitchRelay(h);
        if (failFirst) h.context.fetch = async () => { throw new Error('offline'); };
        const send = async () => {
            await deliver({ data: { action: 'catgirl_switched', new_catgirl: 'B', old_catgirl: 'A',
                switch_revision: 20, timestamp: 123 } });
            await new Promise(setImmediate);
        };
        await send();
        h.context.fetch = fetch;
        await send(); await send();
        assert.equal(h.window.lanlan_config.lanlan_name, 'B');
        assert.deepEqual(calls.map(c => c[0]), failFirst ? ['begin', 'rollback', 'begin', 'commit'] : ['begin', 'commit']);
    });
}

for (const renderer of ['live2d', 'mmd']) {
    test('same-current ' + renderer + ' recovery after real cleanup and rollback, not during cold boot', async () => {
        const h = harness(), calls = avatarCalls(h);
        h.window.location.pathname = '/';
        h.window.lanlan_config = { lanlan_name: 'A', model_type: renderer };
        h.load('static/app/app-character.js');
        await h.window.handleCatgirlSwitch('A', 'A', { switchRevision: 30 });
        assert.deepEqual(calls, [], 'a cold renderer is not a failed switch');
        const manager = renderer === 'mmd'
            ? { currentModel: { mesh: {} }, dispose() { this.currentModel = null; } }
            : { currentModel: {}, async removeModel() { this.currentModel = null; } };
        h.window[renderer + 'Manager'] = manager;
        // Fetch the target B, then disconnect after cleanup but before its model is usable.
        h.context.fetch = async () => ({ ok: true, json: async () => ({ '猫娘': { B: { model_type: renderer } } }) });
        h.window.connectWebSocket = () => { throw new Error('disconnected after old renderer teardown'); };
        await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 31 });
        assert.equal(h.window.lanlan_config.lanlan_name, 'A');
        assert.equal(manager.currentModel, null);
        const before = calls.length;
        await h.window.handleCatgirlSwitch('A', 'B', { switchRevision: 32 });
        assert.equal(calls[before]?.[0], 'begin', 'the real lost model requires recovery');
    });
}

test('PNGTuber rollback makes a same-current authoritative receipt request avatar reload', async () => {
    const h = harness(), calls = avatarCalls(h);
    h.window.location.pathname = '/';
    h.window.lanlan_config = { lanlan_name: 'A', model_type: 'pngtuber', pngtuber: { idle_image: '/A.png' } };
    h.window.pngtuberManager = { image: {}, hidden: false, hide() { this.hidden = true; }, show() { this.hidden = false; } };
    h.window.PNGTuberManager = function PNGTuberManager() {};
    h.window.loadPNGTuberAvatar = async () => h.window.pngtuberManager;
    h.context.fetch = async () => ({ ok: true, json: async () => ({ ['\u732b\u5a18']: {
        A: { model_type: 'pngtuber', pngtuber: { idle_image: '/A.png' }, _reserved: { character_uid: UID_A } },
        B: { model_type: 'live2d', _reserved: { character_uid: UID_B } }
    } }) });
    h.window.connectWebSocket = () => { throw new Error('disconnect after PNGTuber teardown'); };
    h.load('static/app/app-character.js');

    await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 31 });
    assert.equal(h.window.lanlan_config.lanlan_name, 'A');
    assert.equal(h.window.pngtuberManager.hidden, true);
    const before = calls.length;

    h.window.connectWebSocket = () => {};
    await h.window.handleCatgirlSwitch('A', 'B', { switchRevision: 32 });
    assert.equal(calls[before]?.[0], 'begin', 'rollback evidence must bypass same-name dedupe for PNGTuber');
});

for (const path of ['cached', 'event']) {
    test('IPC ' + path + ' avatar error settles both readiness promises', async () => {
        const h = harness();
        h.context.URLSearchParams = URLSearchParams;
        h.window.__NEKO_MULTI_WINDOW__ = true;
        h.window.appChatAvatar = { setExternalAvatar() { throw new Error('avatar unavailable'); } };
        const detail = { lanlan_name: 'A', switch_revision: 40, avatarDataUrl: 'data:image/png;base64,YQ==' };
        if (path === 'cached') h.window.__nekoInjectedConfig = detail;
        h.load('static/js/index.js');
        const result = h.window.startPageConfigLoad();
        if (path === 'event') {
            await new Promise(setImmediate);
            h.window.dispatchEvent({ type: 'neko:config-injected', detail });
        }
        assert.equal(await result, false);
        assert.equal(await h.window.pageConfigReady, false);
    });
}

for (const route of ['HTTP', 'IPC']) {
    test(route + ' handoff reports an actual failed switch as not applied', async () => {
        const h = harness();
        h.context.URLSearchParams = URLSearchParams;
        h.load('static/js/index.js'); h.load('static/app/app-character.js');
        await h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 50 });
        const detail = { success: true, lanlan_name: 'A', switch_revision: 51, model_type: 'live2d' };
        h.window.__NEKO_MULTI_WINDOW__ = route === 'IPC';
        h.window.__nekoInjectedConfig = detail;
        h.context.fetch = async url => {
            if (url.startsWith('/api/config/page_config')) return { ok: true, json: async () => detail };
            throw new Error('new role load failed');
        };
        assert.equal(await h.window.startPageConfigLoad(), false);
        assert.equal(h.window.lanlan_config.lanlan_name, 'B');
    });
}

for (const settlement of ['finally', 'watchdog']) {
    test(settlement + ' replay cannot replace a newer receipt already queued on the microtask queue', async () => {
        const h = harness(), calls = avatarCalls(h), firstRead = deferred();
        h.load('static/app/app-character.js');
        const config = { ok: true, json: async () => ({ '猫娘': {
            B: { model_type: 'live2d' }, C: { model_type: 'live2d' }, D: { model_type: 'live2d' }
        } }) };
        let first = true;
        h.context.fetch = () => {
            if (!first) return Promise.resolve(config);
            first = false;
            return firstRead.promise;
        };
        const send = (name, revision) => h.window.handleCatgirlSwitch(name, 'A', { switchRevision: revision });
        const firstAttempt = send('B', 31);
        await send('C', 32);
        const enqueueD = () => queueMicrotask(() => send('D', 33));
        if (settlement === 'watchdog') {
            enqueueD();
            [...h.timers.values()].find(t => t.delay === 45000).callback();
        } else {
            const commit = h.window.appChatAvatarState.commitCharacterSwitch;
            h.window.appChatAvatarState.commitCharacterSwitch = (id, info) => {
                commit(id, info);
                if (id === 1) enqueueD();
            };
        }
        firstRead.resolve(config);
        await firstAttempt;
        await new Promise(setImmediate);
        assert.equal(h.window.appState._latestCatgirlSwitchRevision, 33);
        assert.equal(h.window.lanlan_config.lanlan_name, 'D');
        assert.equal(calls.filter(c => c[0] === 'commit' && c[2].name === 'C').length, 0);
    });
}

for (const initiallyMounted of [true, false]) {
    test('rollback distinguishes an incoming MMD mesh from the original: mounted=' + initiallyMounted, async () => {
        const h = harness(), w = h.window, calls = avatarCalls(h), mountedB = deferred(), animation = deferred();
        w.location.pathname = '/';
        w.lanlan_config = { lanlan_name: 'A', model_type: 'live3d', live3d_sub_type: 'mmd' };
        const manager = name => ({
            currentModel: name ? { mesh: { name } } : null, scene: {}, renderer: {},
            dispose() { this.currentModel = null; },
            async loadModel(url) { this.currentModel = { mesh: { name: url.slice(1, -4) } }; },
            loadAnimation() { mountedB.resolve(); return animation.promise; }, playAnimation() {}
        });
        w.mmdManager = initiallyMounted ? manager('A') : null;
        w.initMMDModel = async () => (w.mmdManager = manager(''));
        h.context.fetch = async url => ({ ok: true, json: async () => url === '/api/characters'
            ? { '猫娘': { A: { model_type: 'mmd', mmd: '/A.pmx' },
                B: { model_type: 'mmd', mmd: '/B.pmx', mmd_idle_animation: '/B.vmd' } } }
            : { success: false } });
        h.load('static/app/app-character.js');
        // A failed lookup before teardown cannot displace a mounted A or take over a cold boot.
        const read = h.context.fetch;
        h.context.fetch = async () => { throw new Error('offline'); };
        await w.handleCatgirlSwitch('B', 'A', { switchRevision: 100 });
        const before = calls.length;
        await w.handleCatgirlSwitch('A', 'B', { switchRevision: 101 });
        assert.equal(calls.length, before);
        h.context.fetch = read;
        const first = w.handleCatgirlSwitch('B', 'A', { switchRevision: 102 });
        await mountedB.promise;
        [...h.timers.values()].find(t => t.delay === 45000).callback();
        assert.equal(w.lanlan_config.lanlan_name, 'A');
        assert.equal(w.mmdManager.currentModel.mesh.name, 'B');
        // Another failed attempt before teardown must not promote B to A's rollback identity.
        h.context.fetch = async () => { throw new Error('offline again'); };
        await w.handleCatgirlSwitch('A', 'B', { switchRevision: 103 });
        h.context.fetch = read;
        await w.handleCatgirlSwitch('A', 'B', { switchRevision: 103 });
        animation.resolve(); await first;
        assert.equal(w.mmdManager.currentModel.mesh.name, 'A');
        assert.equal(calls.filter(c => c[0] === 'commit' && c[2].name === 'A').length, 1);
    });
}

test('a cold MMD renderer is not treated as replaced if a failed switch never installs a model', async () => {
    const h = harness(), calls = avatarCalls(h), w = h.window;
    w.location.pathname = '/';
    w.lanlan_config = { lanlan_name: 'A', model_type: 'mmd' };
    h.context.fetch = async () => ({ ok: true, json: async () => ({ '猫娘': { B: { model_type: 'mmd' } } }) });
    w.clearAudioQueue = async () => { throw new Error('stopped after cleanup, before model load'); };
    h.load('static/app/app-character.js');
    await w.handleCatgirlSwitch('B', 'A', { switchRevision: 200 });
    await w.handleCatgirlSwitch('A', 'B', { switchRevision: 201 });
    assert.deepEqual(calls.map(c => c[0]), ['begin', 'rollback']);
});

for (const rolledBack of [false, true]) {
    test('Live2D self-healing reconciles the active socket, rolledBack=' + rolledBack, async () => {
        const h = harness(), w = h.window, calls = avatarCalls(h);
        h.context.URLSearchParams = URLSearchParams;
        w.location.pathname = '/';
        const socket = name => ({ name, url: 'ws://app/ws/' + encodeURIComponent(name), readyState: 1, close() { this.readyState = 3; } });
        w.appState.socket = socket('A');
        w.connectWebSocket = () => { w.appState.socket = socket(w.lanlan_config.lanlan_name); };
        let active = 'A', reads = 0, mounts = 0, revision = 701, failTargetB = false;
        h.context.fetch = async url => {
            if (url.startsWith('/api/config/page_config')) {
                reads++;
                return { ok: true, json: async () => ({ success: true, lanlan_name: active,
                    switch_revision: revision, model_type: 'live2d',
                    model_path: revision === 701 ? '' : '/' + active + '.model3.json' }) };
            }
            if (url === '/api/characters') return { ok: true, json: async () => ({
                '猫娘': { A: { model_type: 'live2d' }, B: { model_type: 'live2d' } }
            }) };
            if (url.includes('/current_live2d_model')) {
                if (failTargetB && url.endsWith('=B')) return { ok: false, status: 503 };
                return { ok: true, json: async () => ({ success: true, model_info: { path: '/' + active + '.model3.json' } }) };
            }
            return { ok: true, json: async () => ({}) };
        };
        h.context.Live2DManager = class {
            currentModel = null; pixi_app = { renderer: {} };
            getCurrentModel() { return this.currentModel; }
            getEmotionMapping() { return {}; }
            removeModel() { this.currentModel = null; }
            loadUserPreferences() { return []; }
            loadModel() { mounts++; this.currentModel = {}; }
        };
        h.load('static/js/index.js'); h.load('static/app/app-character.js');
        assert.equal(await w.startPageConfigLoad(), true);
        h.load('static/live2d/live2d-init.js');
        await new Promise(setImmediate);
        const retry = [...h.timers.values()].find(t => t.delay === 600);
        assert.ok(retry, 'the real boot entry scheduled a retry for the empty A model path');
        // Count attempts to start the bootstrap loader again, not the owned central loader.
        vm.runInContext('const originalInit = initLive2DModel; window.retryInits = 0; '
            + 'initLive2DModel = (...args) => { window.retryInits++; return originalInit(...args); }', h.context);
        if (rolledBack) {
            failTargetB = true;
            await w.handleCatgirlSwitch('B', 'A', { switchRevision: 702 });
            assert.equal(w.lanlan_config.lanlan_name, 'A');
            assert.equal(w.appState.socket.name, 'B', 'the target socket already opened before the load failed');
            revision = 703;
        } else {
            active = 'B'; revision = 702;
        }
        await retry.callback(); await new Promise(setImmediate);
        assert.equal(w.appState.socket.name, active, 'the HTTP snapshot alone must repair the connection');
        await w.handleCatgirlSwitch(active, rolledBack ? 'B' : 'A', { switchRevision: revision });
        assert.equal(reads, 2);
        assert.deepEqual(calls.map(c => c[0]), rolledBack ? ['begin', 'rollback', 'begin', 'commit'] : ['begin', 'commit']);
        assert.equal(w.appState.socket.name, active);
        assert.equal(w.lanlan_config.lanlan_name, active);
        assert.equal(mounts, 1);
        assert.equal(w.retryInits, 0, 'central already owns the mounted model; do not boot it again');
    });
}

test('healthy same-current chat socket is not replaced, including encoded names and non-character transports', async () => {
    for (const [url, readyState] of [['wss://example.test/ws/%E7%8C%AB', 1],
        ['ws://example.test/ws/%E7%8C%AB', 0], ['ws://example.test/bridge', 1], ['ws://example.test/ws/B', 3]]) {
        const h = harness(), calls = avatarCalls(h);
        h.window.lanlan_config.lanlan_name = '猫';
        h.window.appState.socket = { url, readyState };
        h.load('static/app/app-character.js');
        assert.equal(await h.window.handleCatgirlSwitch('猫', '猫', { switchRevision: 700 }), true);
        assert.deepEqual(calls, []);
    }
});

test('same-character HTTP refresh still fills a cold VRM model path without a switch attempt', async () => {
    const h = harness(), calls = avatarCalls(h);
    h.context.URLSearchParams = URLSearchParams;
    h.window.location.pathname = '/';
    let path = '';
    h.context.fetch = async () => ({ ok: true, json: async () => ({ success: true, lanlan_name: 'A',
        switch_revision: 710, model_type: 'live3d', live3d_sub_type: 'vrm', model_path: path }) });
    h.load('static/js/index.js'); h.load('static/app/app-character.js');
    assert.equal(await h.window.startPageConfigLoad(), true);
    path = '/A.vrm';
    assert.equal(await h.window.reloadPageConfig(), true);
    assert.equal(h.window.vrmModel, path);
    assert.deepEqual(calls, []);
});

test('MMD startup restores the original settings after watchdog rollback between roles sharing the same asset', async () => {
    const h = harness(), firstSettings = deferred(), earlyStarted = deferred();
    const waitingIdle = deferred(), idleStarted = deferred();
    const shared = '/static/mmd/shared/avatar.pmx';
    const cfgA = desktopAvatarConfig('A', 'mmd', 82, shared);
    const cfgB = desktopAvatarConfig('B', 'mmd', 83, shared);
    let settingsA = 0, failedManager, attempts = 0;
    const startup = startDesktopAvatarBootstrap(
        h,
        { lanlan_name: 'A', switch_revision: 82, model_type: 'live3d', live3d_sub_type: 'mmd' },
        async url => {
            if (String(url).startsWith('/api/config/page_config')) return { json: async () => cfgA };
            if (url.endsWith('/A/mmd_settings')) {
                settingsA++;
                if (settingsA === 1) {
                    earlyStarted.resolve();
                    await firstSettings.promise;
                }
                return { ok: true, json: async () => ({ success: true, settings: { physics: { enabled: false, strength: 0.4 } } }) };
            }
            if (url.endsWith('/B/mmd_settings')) {
                return { ok: true, json: async () => ({ success: true, settings: { physics: { enabled: true, strength: 1.8 } } }) };
            }
            if (url === '/api/characters') return { ok: true, json: async () => ({ ['猫娘']: {
                A: { ...cfgA, mmd: shared, _reserved: { character_uid: UID_A } },
                B: { ...cfgB, mmd: shared, mmd_idle_animation: '/B.vmd', _reserved: { character_uid: UID_B } },
            } }) };
            return { ok: true, json: async () => ({}) };
        },
        false,
        {
            mmdLoadModel: async ({ url, manager }) => {
                const character = (++attempts === 1) ? 'B' : 'A';
                manager.applySettings = settings => { manager.appliedPhysics = settings.physics ?? null; };
                manager.currentModel = { url, mesh: {}, character };
                manager.loadAnimation = async animation => {
                    assert.equal(animation, '/B.vmd');
                    idleStarted.resolve();
                    await waitingIdle.promise;
                };
                manager.playAnimation = () => {};
                if (character === 'B') failedManager = manager;
                return manager.currentModel;
            },
        },
    );
    assert.equal(await startup.ready, true);
    await earlyStarted.promise;
    const originalManager = h.window.mmdManager;
    const takeover = h.window.handleCatgirlSwitch('B', 'A', { switchRevision: 83 });
    await idleStarted.promise;
    assert.equal(failedManager.currentModel.character, 'B', 'the failed takeover already mounted its mesh');
    assert.equal(failedManager.appliedPhysics.strength, 1.8);
    assert.ok(originalManager._isDisposed);
    const watchdog = [...h.timers.values()].find(x => x.delay === 45000);
    assert.ok(watchdog);
    watchdog.callback();
    assert.equal(h.window.lanlan_config.lanlan_name, 'A');
    assert.equal(h.window.appCharacter.needsCurrentCharacterRecovery('A', { fromSnapshot: true }), true);
    firstSettings.resolve();
    waitingIdle.resolve();
    assert.equal(await takeover, false);
    await new Promise(setImmediate);
    const restoredManager = h.window.mmdManager;
    assert.notEqual(restoredManager, originalManager);
    assert.notEqual(restoredManager, failedManager);
    assert.equal(restoredManager?.currentModel?.character, 'A');
    assert.equal(restoredManager.currentModel.url, shared);
    assert.equal(restoredManager.physicsStrength, 0.4);
    assert.deepEqual([settingsA, attempts], [2, 2]);
    assert.equal(h.window.appCharacter.needsCurrentCharacterRecovery('A', { fromSnapshot: true }), false);
    assert.equal(await h.window.handleCatgirlSwitch('A', 'B', { switchRevision: 84 }), true);
    assert.equal(await h.window.handleCatgirlSwitch('A', 'A', { switchRevision: 84 }), true);
    assert.deepEqual([settingsA, attempts], [2, 2], 'healthy shared-file recovery absorbs repeated replay');
});

test('retired websocket still forwards stamped character-switch control frames', () => {
    const h = harness(); const forwarded = [];
    h.window.t = key => key;
    h.window.location.protocol = 'http:'; h.window.location.host = 'fixture.test';
    h.window.appConst = { HEARTBEAT_INTERVAL: 30000 };
    h.window.handleCatgirlSwitch = (next, old, options) => {
        forwarded.push([next, old, options?.switchRevision || 0]);
    };
    h.context.Blob = Blob;
    h.context.WebSocket = class {
        static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
        constructor(url) { this.url = url; this.readyState = 0; }
        send() {}
        close() { this.readyState = 3; }
    };
    h.load('static/app/app-websocket.js');
    h.window.connectWebSocket();
    const retired = h.window.appState.socket;
    h.window.connectWebSocket();

    retired.onmessage({ data: JSON.stringify({
        type: 'catgirl_switched', new_catgirl: 'C', old_catgirl: 'B', switch_revision: 77
    }) });
    retired.onmessage({ data: JSON.stringify({
        type: 'catgirl_switched', new_catgirl: 'D', old_catgirl: 'C'
    }) });

    assert.deepEqual(forwarded, [['C', 'B', 77]]);
});



test('pinned preview keeps committed receipt ordering across a revision-zero projection', async () => {
    const h = harness(), w = h.window, calls = avatarCalls(h), originalFetch = h.context.fetch;
    h.context.URLSearchParams = URLSearchParams;
    w.location.pathname = '/web_chat_compact'; w.location.search = '?lanlan_name=A';
    // Cold HTTP learns the authoritative high-water before app-character is loaded.
    let revision = 902;
    h.context.fetch = async (url, options) => String(url).startsWith('/api/config/page_config')
        ? { json: async () => desktopAvatarConfig('A', 'live2d', revision, '/A.model3.json') }
        : originalFetch(url, options);
    h.load('static/js/index.js');
    assert.equal(await w.reloadPageConfig(), true);
    revision = 0;
    assert.equal(await w.reloadPageConfig(), true);
    assert.equal(w.lanlan_config.switch_revision, 0);
    h.load('static/app/app-character.js');
    const deliver = loadSwitchRelay(h);
    const receipt = revision => deliver({ data: { action: 'catgirl_switched', new_catgirl: 'B', old_catgirl: 'A',
        switch_revision: revision, timestamp: 2000 } });
    await receipt(901);
    await new Promise(setImmediate);
    assert.deepEqual(calls, [], 'a late delivery predating 902 must not start an attempt');
    assert.equal(w.lanlan_config.lanlan_name, 'A');
    await receipt(903);
    await new Promise(setImmediate);
    assert.equal(calls[0]?.[0], 'begin', 'a genuine later receipt is still actionable');
});


for (const [priorCommits, latest] of [[false, 'D'], [true, 'D'], [true, 'C']]) {
    test('queued page-model handoff recovers after replay rollback (prior committed: ' + priorCommits + ', latest: ' + latest + ')', async () => {
        const h = harness(), calls = avatarCalls(h), entered = deferred(), firstConfig = deferred(), firstSwitch = deferred();
        const path = '/static/vrm/' + latest + '-84.vrm', model = desktopAvatarConfig(latest, 'vrm', 84, path);
        let pageReads = 0, switchReads = 0;
        const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'B', switch_revision: 82 }, async url => {
            if (String(url).startsWith('/api/config/page_config')) {
                if (++pageReads === 1) { entered.resolve(); await firstConfig.promise; }
                return { ok: true, json: async () => model };
            }
            if (url === '/api/characters') {
                if (++switchReads === 1) {
                    await firstSwitch.promise;
                    if (priorCommits) {
                        const c = desktopAvatarConfig('C', 'vrm', 83, '/static/vrm/C.vrm');
                        return { ok: true, json: async () => ({ '猫娘': { C: { ...c, vrm: c.model_path } } }) };
                    }
                }
                if (switchReads <= 2) return { ok: false, status: 503, json: async () => ({}) };
                return { ok: true, json: async () => ({ '猫娘': { [latest]: { ...model, vrm: path } } }) };
            }
            return { ok: true, json: async () => ({}) };
        });
        await entered.promise;
        const prior = h.window.handleCatgirlSwitch('C', 'B', { switchRevision: 83 });
        await new Promise(setImmediate);
        assert.equal(switchReads, 1);
        firstConfig.resolve(); await new Promise(setImmediate);
        assert.equal(h.window.appState._pendingCatgirlSwitch?.newCatgirl, latest);
        assert.equal([...h.timers.values()].some(t => t.delay === 600), false);
        firstSwitch.resolve(); await prior; await new Promise(setImmediate);
        assert.deepEqual(calls.map(c => c[0]), ['begin', priorCommits ? 'commit' : 'rollback', 'begin', 'rollback']);
        assert.equal(h.window.appState.isSwitchingCatgirl, false);
        // The production handoff observes settlement on its 50ms wait tick.
        const observation = [...h.timers.entries()].find(([, t]) => t.delay === 50);
        if (observation) {
            h.timers.delete(observation[0]); observation[1].callback(); await new Promise(setImmediate);
        }
        assert.ok([...h.timers.values()].some(t => t.delay === 600), 'a failed replay must return ownership to the bounded bootstrap retry');
        await advanceModelBootTimer(h, 600);
        await startup.ready; await startup.vrmBoot; await new Promise(setImmediate);
        assert.equal(pageReads, 2);
        assert.equal(h.window.__nekoPageModelConfigPending, null);
        assert.equal(h.window.lanlan_config.lanlan_name, latest);
        assert.deepEqual(startup.loads, priorCommits ? ['/static/vrm/C.vrm', path] : [path]);
    });
}


for (const succeeds of [false, true]) test('MMD startup rechecks immediately before load: takeover success=' + succeeds, async () => {
    const h = harness(), entered = deferred(), release = deferred(), releaseSwitch = deferred();
    const path = '/static/mmd/A.pmx', other = '/static/mmd/C.pmx';
    let armed = false, switching, collided = false;
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'A', switch_revision: 82 }, async url => {
        if (String(url).startsWith('/api/config/page_config')) return { json: async () => desktopAvatarConfig('A', 'mmd', 82, path) };
        if (url.endsWith('/A/mmd_settings')) { entered.resolve(); await release.promise; return {ok: false}; }
        if (url === '/api/characters') {
            await releaseSwitch.promise;
            if (!succeeds) return {ok: false};
            return {ok: true, json: async () => ({ '猫娘': { C: {model_type: 'mmd', mmd: other} } })};
        }
        return {ok: true, json: async () => ({})};
    }, false, { mmdLoadModel({url}) { if (url === path && h.window.appState.isSwitchingCatgirl) collided = true; } });
    await startup.ready; await entered.promise;
    // Interleave a real incoming switch between the async ownership helper and its caller.
    Object.defineProperty(h.window, 'mmdModel', {configurable: true, get() {
        if (armed) { armed = false; queueMicrotask(() => { switching = h.window.handleCatgirlSwitch('C', 'A', {switchRevision: 83}); }); }
        return path;
    }, set() {}});
    armed = true; release.resolve(); await new Promise(setImmediate);
    assert.ok(switching);
    releaseSwitch.resolve(); assert.equal(await switching, succeeds);
    const settle=[...h.timers.entries()].find(([,t])=>t.delay===50);
    if (settle) {h.timers.delete(settle[0]);settle[1].callback();await new Promise(setImmediate);}
    assert.deepEqual(startup.loads, [succeeds ? other : path], 'rollback must still resume the old current model');
    assert.equal(collided, false, 'a cold load must not start while the switch owns the renderer');
});

test('a VRM path refreshed during Three.js startup loads only the fresh asset at the same revision', async () => {
    const h = harness(), entered = deferred(), resume = deferred();
    const first = '/static/vrm/B-old.vrm', fresh = '/static/vrm/B-fresh.vrm';
    let path = first;
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'B', switch_revision: 82 }, async url => {
        if (String(url).startsWith('/api/config/page_config')) return { json: async () => desktopAvatarConfig('B', 'vrm', 82, path) };
        return { ok: true, json: async () => ({}) };
    });
    h.window.vrmManager.initThreeJS = async () => { entered.resolve(); await resume.promise; };
    await startup.ready; await entered.promise;
    path = fresh;
    assert.equal(await h.window.reloadPageConfig(), true);
    assert.equal(h.window.vrmModel, fresh);
    resume.resolve(); await startup.vrmBoot; await new Promise(setImmediate);
    assert.deepEqual(startup.loads, [fresh]);
    assert.equal(h.window.vrmManager.currentModel.url, fresh);
});

for (const takeover of [false, true]) test('late VRM load confirms only its own mounted model (takeover: ' + takeover + ')', async () => {
    const h = harness(), entered = deferred(), resume = deferred(), restored = [];
    const a = '/static/vrm/A.vrm', b = '/static/vrm/B.vrm';
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'A', switch_revision: 82 }, async url => {
        if (String(url).startsWith('/api/config/page_config')) return { json: async () => desktopAvatarConfig('A', 'vrm', 82, a) };
        return { ok: true, json: async () => url === '/api/characters' ? {'猫娘': {B: {model_type: 'vrm', vrm: b}}} : {} };
    });
    const w = h.window, manager = w.vrmManager;
    const load = manager._loadModelInternal;
    manager._loadModelInternal = async function (url, options, token) {
        if (url === a) { entered.resolve(); await resume.promise; }
        return load.call(this, url, options, token);
    };
    const confirm = w.appCharacter.confirmRestoredRenderer;
    w.appCharacter.confirmRestoredRenderer = (...args) => {restored.push(args); return confirm(...args);};
    await startup.ready; await entered.promise;
    if (takeover) assert.equal(await w.handleCatgirlSwitch('B', 'A', {switchRevision: 83}), true);
    resume.resolve(); await startup.vrmBoot;
    assert.equal(manager.currentModel.url, takeover ? b : a);
    assert.equal(restored.length, takeover ? 0 : 1);
    if (!takeover) assert.equal(restored[0][2], manager.currentModel.vrm);
});

for (const failFirstFresh of [false, true]) test('a B/83 central commit cannot retire the in-flight B/84 IPC model snapshot (fresh read fails initially: ' + failFirstFresh + ')', async () => {
    const h = harness(), olderStarted = deferred(), releaseOlder = deferred(), snapshotStarted = deferred(), releaseSnapshot = deferred();
    const older = '/static/vrm/B-rev83.vrm', fresh = '/static/vrm/B-rev84.vrm';
    const snapshotPath = '/static/vrm/B-snapshot-before-last-save.vrm';
    let roles = 0;
    const startup = startDesktopAvatarBootstrap(h, undefined, async url => {
        if (String(url).startsWith('/api/config/page_config')) {
            snapshotStarted.resolve();
            await releaseSnapshot.promise;
            return { json: async () => desktopAvatarConfig('B', 'vrm', 84, snapshotPath) };
        }
        if (url === '/api/characters') {
            if (++roles === 1) {
                olderStarted.resolve();
                await releaseOlder.promise;
            }
            if (roles === 2 && failFirstFresh) return { ok: false };
            const path = roles === 1 ? older : fresh;
            return { ok: true, json: async () => ({ '猫娘': { B: { model_type: 'vrm', vrm: path } } }) };
        }
        return { ok: true, json: async () => ({}) };
    });
    const w = h.window;
    Object.assign(w.lanlan_config, { lanlan_name: 'A', switch_revision: 82 });
    const oldSwitch = w.handleCatgirlSwitch('B', 'A', { switchRevision: 83 });
    await olderStarted.promise;
    w.dispatchEvent(new h.context.CustomEvent('neko:config-injected', { detail: {lanlan_name: 'B', switch_revision: 84} }));
    await snapshotStarted.promise;
    assert.equal(w.__nekoPageModelConfigPending.revision, 84);
    releaseOlder.resolve();
    assert.equal(await oldSwitch, true);
    const retained = w.__nekoPageModelConfigPending?.revision;
    assert.equal(w.vrmManager.currentModel.url, older);
    let ready = 'pending';
    startup.ready.then(result => { ready = result; });
    await new Promise(setImmediate);
    const stillWaiting = ready;
    releaseSnapshot.resolve();
    if (failFirstFresh) {
        await new Promise(setImmediate);
        assert.equal(ready, 'pending', 'a failed newer model load is not readiness');
        await advanceModelBootTimer(h, 600);
    }
    assert.equal(await startup.ready, true, 'the newer full snapshot must actually settle as successful');
    await startup.vrmBoot;
    assert.equal(retained, 84, 'B/83 may not retire the B/84 boot');
    assert.equal(stillWaiting, 'pending', 'B/83 is not B/84 page readiness');
    assert.deepEqual(startup.loads, [older, fresh]);
    assert.deepEqual([w.lanlan_config.lanlan_name, w.lanlan_config.switch_revision, w.vrmManager.currentModel.url],
        ['B', 84, fresh]);
    assert.equal(w.appCharacter.hasCommittedPageModel('B', 84), true);
    const published = w.vrmModel;
    // The same startup entrypoint is called again by tutorial-model recovery.
    // It must not fall back to the sample or the earlier HTTP snapshot.
    await w.initVRMModel();
    assert.equal(w.vrmManager.currentModel.url, fresh, 'later startup must load the committed B/84 asset');
    assert.equal(published, fresh, 'publish the actual mounted asset, not the earlier HTTP snapshot');
    assert.deepEqual(startup.loads, [older, fresh, fresh]);
});

for (const failStale of [false, true]) test('VRM startup follows an authoritative path change during the model load (old failure: ' + failStale + ')', async () => {
    const h = harness(), entered = deferred(), finish = deferred(), requested = [], confirmed = [];
    const first = '/static/vrm/B-old.vrm', fresh = '/static/vrm/B-fresh.vrm';
    let path = first;
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'B', switch_revision: 82 }, async url => {
        if (String(url).startsWith('/api/config/page_config')) return { json: async () => desktopAvatarConfig('B', 'vrm', 82, path) };
        return { ok: true, json: async () => ({}) };
    });
    const w = h.window, manager = w.vrmManager;
    const load = manager._loadModelInternal;
    manager._loadModelInternal = async function (url, options, token) {
        requested.push(url);
        if (url === first) {
            entered.resolve(); await finish.promise;
            if (failStale) throw new Error('saved path replaced the old asset');
        }
        return load.call(this, url, options, token);
    };
    const confirm = w.appCharacter.confirmRestoredRenderer;
    w.appCharacter.confirmRestoredRenderer = (...args) => {
        confirmed.push(manager.currentModel?.url);
        return confirm(...args);
    };
    await startup.ready;
    await entered.promise;
    path = fresh;
    assert.equal(await w.reloadPageConfig(), true);
    assert.deepEqual([w.lanlan_config.switch_revision, w.vrmModel], [82, fresh]);
    finish.resolve();
    await startup.vrmBoot; await new Promise(setImmediate);
    assert.deepEqual(requested, [first, fresh]);
    assert.equal(manager.currentModel.url, fresh);
    assert.deepEqual(confirmed, [fresh]);
    assert.equal(w._isVRMLoading, false);
});

for (const succeeds of [false, true]) test('VRM startup rechecks ownership in the actual load turn (takeover success: ' + succeeds + ')', async () => {
    const h = harness(), switchEntered = deferred(), switchReleased = deferred();
    const first = '/static/vrm/A.vrm', other = '/static/mmd/C.pmx';
    let armed = false, started, switching = false;
    const w = h.window;
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'A', switch_revision: 82 }, async url => {
        if (String(url).startsWith('/api/config/page_config')) return { json: async () => desktopAvatarConfig('A', 'vrm', 82, first) };
        if (url === '/api/characters' && switching) {
            switchEntered.resolve();
            await switchReleased.promise;
            if (!succeeds) return { ok: false };
            return { ok: true, json: async () => ({ '猫娘': { C: { model_type: 'mmd', mmd: other } } }) };
        }
        return { ok: true, json: async () => ({}) };
    });
    w.vrmManager.initThreeJS = async () => { armed = true; };
    // Run the real switch in the microtask between the async owner's
    // positive result and the caller resuming; hold its actual role read.
    Object.defineProperty(w.appState, 'isSwitchingCatgirl', { configurable: true,
        get() {
            if (armed) {
                armed = false;
                queueMicrotask(() => { started = w.handleCatgirlSwitch('C', 'A', { switchRevision: 83 }); });
            }
            return switching;
        },
        set(value) { switching = value; }
    });
    await startup.ready; await switchEntered.promise; await new Promise(setImmediate);
    const whileSwitchOwnsRenderer = startup.loads.slice();
    switchReleased.resolve(); assert.equal(await started, succeeds);
    const settle = [...h.timers.entries()].find(([,t]) => t.delay === 50);
    if (settle) { h.timers.delete(settle[0]); settle[1].callback(); }
    await startup.vrmBoot;
    assert.deepEqual(whileSwitchOwnsRenderer, [], 'stale startup must never call loadModel inside a live switch');
    assert.deepEqual(startup.loads, [succeeds ? other : first], 'a failed handoff must still resume the only cold VRM');
});

test('VRM startup path refresh has a bounded retry budget under continuous saves', async () => {
    const h = harness(), requested = [], confirmed = [];
    let sequence = 0;
    const path = () => '/static/vrm/current-' + sequence + '.vrm';
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'B', switch_revision: 82 }, async url => {
        if (String(url).startsWith('/api/config/page_config')) return { json: async () => desktopAvatarConfig('B', 'vrm', 82, path()) };
        return { ok: true, json: async () => ({}) };
    });
    const w = h.window, manager = w.vrmManager, load = manager._loadModelInternal;
    manager._loadModelInternal = async function (url, options, token) {
        requested.push(url);
        // Hard cap in the fixture only, so a broken unbounded retry cannot hang Node.
        if (requested.length > 6) throw new Error('unexpected retry loop');
        const result = await load.call(this, url, options, token);
        ++sequence;
        await w.reloadPageConfig();
        return result;
    };
    const confirm = w.appCharacter.confirmRestoredRenderer;
    w.appCharacter.confirmRestoredRenderer = (...args) => {
        confirmed.push(manager.currentModel?.url);
        return confirm(...args);
    };
    assert.equal(await startup.ready, true);
    await startup.vrmBoot; await new Promise(setImmediate);
    assert.ok(requested.length > 1 && requested.length <= 3, 'refreshes retry, but at most twice per startup chain');
    assert.deepEqual(confirmed, []);
    assert.equal(w._isVRMLoading, false);
});

test('same-name VRM replay retires the old loader through the real manager token', async () => {
    const h = harness(), entered = deferred(), release = deferred(), confirmed = [], requested = [];
    const oldPath = '/static/vrm/A-rev82.vrm', latest = '/static/vrm/A-rev84.vrm';
    const startup = startDesktopAvatarBootstrap(h, { lanlan_name: 'A', switch_revision: 82 }, async url => {
        if (String(url).startsWith('/api/config/page_config')) return { json: async () => desktopAvatarConfig('A', 'vrm', 82, oldPath) };
        return { ok: true, json: async () => ({'猫娘': { A: { model_type: 'vrm', vrm: latest } } }) };
    });
    const w = h.window, sameManager = w.vrmManager, load = sameManager._loadModelInternal;
    sameManager._loadModelInternal = async function (url, options, token) {
        requested.push(url);
        if (url === oldPath) { entered.resolve(); await release.promise; }
        return load.call(this, url, options, token);
    };
    const confirm = w.appCharacter.confirmRestoredRenderer;
    w.appCharacter.confirmRestoredRenderer = (...args) => { confirmed.push(args); return confirm(...args); };
    await startup.ready; await entered.promise;
    // Use the same public handler options as pending replay, retaining the same
    // name and manager so either check alone cannot catch the stale load.
    assert.equal(await w.handleCatgirlSwitch('A', 'A', { switchRevision: 84, forceReload: true }), true);
    assert.equal(w.vrmManager, sameManager);
    assert.equal(w.lanlan_config.lanlan_name, 'A');
    release.resolve();
    await startup.vrmBoot;
    assert.equal(sameManager.currentModel.url, latest);
    assert.deepEqual(confirmed, []);
    await new Promise(setImmediate);
    assert.deepEqual(requested, [oldPath, latest], 'retired startup may not schedule its own retry for the successor asset');
});

for (const takeoverSucceeds of [false, true]) test('post-load VRM path reconciliation survives only a failed takeover (' + takeoverSucceeds + ')', async () => {
    const h = harness(), oldEntered = deferred(), releaseOld = deferred(), switchEntered = deferred(), releaseSwitch = deferred();
    const older = '/static/vrm/A-old.vrm', fresh = '/static/vrm/A-fresh.vrm', other = '/static/vrm/C.vrm';
    const w = h.window, requested = [], confirmed = [];
    let snapshotPath = older, armed = false, switching = false, started;
    const startup = startDesktopAvatarBootstrap(h, {lanlan_name: 'A', switch_revision: 82}, async url => {
        if (String(url).startsWith('/api/config/page_config')) {
            return {json: async () => desktopAvatarConfig('A', 'vrm', 82, snapshotPath)};
        }
        if (url === '/api/characters' && switching) {
            switchEntered.resolve();
            await releaseSwitch.promise;
            if (!takeoverSucceeds) return {ok: false};
            return {ok: true, json: async () => ({'猫娘': {C: {model_type: 'vrm', vrm: other}}})};
        }
        return {ok: true, json: async () => ({})};
    });
    const manager = w.vrmManager, originalLoad = manager._loadModelInternal;
    manager._loadModelInternal = async function (url, options, token) {
        requested.push(url);
        if (url === older) { oldEntered.resolve(); await releaseOld.promise; }
        const result = await originalLoad.call(this, url, options, token);
        if (url === older) armed = true;
        return result;
    };
    const confirm = w.appCharacter.confirmRestoredRenderer;
    w.appCharacter.confirmRestoredRenderer = (...args) => {
        confirmed.push(manager.currentModel?.url); return confirm(...args);
    };
    // Deliver an actual central receipt in the microtask between the old
    // boot's post-load ownership observation and its continuation.
    Object.defineProperty(w.appState, 'isSwitchingCatgirl', {configurable: true,
        get() {
            if (armed) {
                armed = false;
                queueMicrotask(() => {started = w.handleCatgirlSwitch('C', 'A', {switchRevision: 83});});
            }
            return switching;
        },
        set(value) {switching = value;}
    });
    await startup.ready; await oldEntered.promise;
    snapshotPath = fresh;
    assert.equal(await w.reloadPageConfig(), true);
    assert.deepEqual([w.lanlan_config.switch_revision, w.vrmModel], [82, fresh]);
    releaseOld.resolve(); await switchEntered.promise; await new Promise(setImmediate);
    assert.deepEqual(requested, [older], 'do not interfere while central switch still owns the renderer');
    releaseSwitch.resolve(); assert.equal(await started, takeoverSucceeds);
    const tick = [...h.timers.entries()].find(([, t]) => t.delay === 50);
    if (tick) {h.timers.delete(tick[0]);tick[1].callback();}
    await startup.vrmBoot; await new Promise(setImmediate);
    assert.deepEqual(requested, [older, takeoverSucceeds ? other : fresh]);
    assert.equal(w.lanlan_config.lanlan_name, takeoverSucceeds ? 'C' : 'A');
    assert.equal(manager.currentModel.url, takeoverSucceeds ? other : fresh);
    assert.deepEqual(confirmed, takeoverSucceeds ? [] : [fresh]);
});
