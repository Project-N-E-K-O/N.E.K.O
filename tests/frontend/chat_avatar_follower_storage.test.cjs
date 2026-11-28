const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const modulePath = 'static/app/app-chat-avatar.js';
const source = readFileSync(path.resolve(__dirname, '../..', modulePath), 'utf8');
const owner = 'data:image/png;base64,b3duZXI=';
const mirrored = 'data:image/png;base64,Zm9sbG93ZXI=';
const captured = 'data:image/png;base64,Y2FwdHVyZWQ=';
const cacheKey = 'neko_avatar:A';
const pngtuberCacheKey = 'pngtuber:' + JSON.stringify({
    layeredMetadata: '', idleImage: '/idle.png', talkingImage: '',
});

function run(pathname, order, {cold = false, pngtuber = false, autoload = true, transport = null} = {}) {
    const writes = [];
    const network = [];
    const seen = [];
    const listeners = new Map();
    const timers = new Map();
    let nextTimer = 0;
    let requests = 0;
    let releaseCapture;
    const stored = new Map();
    const modelType = pngtuber ? 'pngtuber' : 'vrm';
    function putOwner(name = 'A', dataUrl = owner, key = pngtuber ? pngtuberCacheKey : 'vrm:/A.vrm') {
        stored.set('neko_avatar:' + name, JSON.stringify({
            dataUrl, modelType, capturedAt: 1, cacheKey: key,
        }));
    }
    if (!cold) putOwner();
    const stub = () => ({hidden: false, classList: {add() {}, remove() {}}, removeAttribute() {}});
    const image = stub();
    const nodes = {
        'chat-avatar-preview-popup': stub(),
        'chat-avatar-preview-image': image,
        'chat-avatar-preview-image-shell': stub(),
        'chat-avatar-preview-placeholder': stub(),
    };
    const window = {
        location: {pathname},
        appState: {dom: {}},
        lanlan_config: {lanlan_name: 'A', model_type: modelType, switch_revision: 83},
        vrmModel: '/A.vrm',
        pngtuberManager: pngtuber ? {config: {idle_image: '/idle.png'}} : null,
        requestAnimationFrame(handler) { handler(); },
        addEventListener(type, handler) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(handler);
        },
        removeEventListener(type, handler) {
            listeners.set(type, (listeners.get(type) ?? []).filter(fn => fn !== handler));
        },
        dispatchEvent(event) {
            if (event.type === 'chat-avatar-preview-updated') seen.push(event.detail);
            for (const handler of listeners.get(event.type) ?? []) handler(event);
        },
    };
    if (transport === 'ipc') {
        window.__NEKO_MULTI_WINDOW__ = true;
        window.__nekoRequestAvatarPreview = () => { requests++; };
        releaseCapture = dataUrl => window.dispatchEvent({
            type: 'neko:avatar-preview-ipc-result', detail: {dataUrl, modelType},
        });
    } else if (transport === 'local') {
        window.avatarPortrait = {capture() {
            requests++;
            return new Promise(resolve => { releaseCapture = dataUrl => resolve({dataUrl, modelType}); });
        }};
    }
    vm.runInNewContext(source, {
        window,
        document: {getElementById: id => nodes[id] ?? null},
        CustomEvent: class { constructor(type, {detail} = {}) { Object.assign(this, {type, detail}); } },
        setTimeout(fn, delay) { timers.set(++nextTimer, {fn, delay}); return nextTimer; },
        clearTimeout(id) { timers.delete(id); },
        localStorage: {
            getItem: key => stored.get(key) ?? null,
            removeItem: key => stored.delete(key),
            setItem(key, value) { writes.push(key); stored.set(key, value); },
        },
        fetch: async (url, options) => {
            if (options?.method === 'POST') network.push({url, body: JSON.parse(options.body)});
            return {ok: true, json: async () => ({'猫娘': {A: {}, B: {}}})};
        },
    }, {filename: modulePath});
    // Both old and staged ingress call this same preview consumer. Ordering
    // and the old private pending slot are tested separately by the loader.
    if (order === 'during bootstrap') window.appChatAvatar.setExternalAvatar(mirrored, modelType);
    window.appChatAvatar.init();
    if (order === 'after init') window.appChatAvatar.setExternalAvatar(mirrored, modelType);
    if (autoload) window.dispatchEvent({type: 'vrm-model-loaded'});
    return {
        image, writes, stored, network, seen, window, putOwner, timers,
        requestCount: () => requests,
        complete: dataUrl => { assert.ok(releaseCapture); releaseCapture(dataUrl); },
        fireCapture() {
            const entry = [...timers].find(([, timer]) => timer.delay === 180);
            assert.ok(entry, 'cold init must queue the real automatic preview timer');
            const [id, timer] = entry;
            timers.delete(id);
            timer.fn();
            assert.equal(requests, 1, 'the real capture receiver must run exactly once');
        },
    };
}

for (const pathname of ['/chat', '/chat_full']) {
    for (const order of ['during bootstrap', 'after init']) {
        test(pathname + ': show the mirrored portrait without replacing the durable owner cache (' + order + ')', () => {
            const {image, writes, stored, network, seen, window} = run(pathname, order);
            assert.equal(image.src, mirrored);
            assert.equal(image.hidden, false);
            assert.equal(window.appChatAvatar.getExternalAvatar()?.dataUrl, mirrored);
            assert.equal(seen.filter(x => x.source === 'ipc').length, 1);
            assert.deepEqual(writes, []);
            assert.equal(JSON.parse(stored.get(cacheKey)).dataUrl, owner);
            assert.deepEqual(network, []);
        });
    }
    for (const entry of ['cold init', 'model loaded', 'manual refresh']) {
        test(pathname + ': ' + entry + ' cannot persist a capture even without a pushed IPC portrait', async () => {
            const h = run(pathname, 'no IPC', {cold: true, autoload: false, transport: 'local'});
            assert.equal(h.image.hidden, true);
            assert.equal(h.window.appChatAvatar.getExternalAvatar(), null);
            let manual;
            if (entry === 'manual refresh') {
                manual = h.window.appChatAvatar.showPopup(null, {forceRefresh: true, showCard: false});
                assert.equal(h.requestCount(), 1);
            } else {
                if (entry === 'model loaded') h.window.dispatchEvent({type: 'vrm-model-loaded'});
                h.fireCapture();
            }
            h.complete(captured);
            await manual;
            await tick();
            assert.equal(h.image.src, captured);
            assert.deepEqual(h.writes, []);
            assert.equal(h.stored.get(cacheKey), undefined);
            assert.deepEqual(h.network, []);
        });
    }
}

test('canonical Pet window still publishes, and follower can still display the persisted fallback', () => {
    const pet = run('/', 'after init');
    assert.ok(pet.writes.includes(cacheKey));
    assert.equal(JSON.parse(pet.stored.get(cacheKey)).dataUrl, mirrored);
    assert.ok(pet.network.some(x => x.url === '/api/card-drop/active-character' && x.body.dataUrl === mirrored));
    const fallback = run('/chat', 'no IPC');
    assert.equal(fallback.image.src, owner);
    assert.deepEqual(fallback.writes, []);
    assert.deepEqual(fallback.network, []);
});

const tick = () => new Promise(setImmediate);
for (const pathname of ['/chat', '/chat_full']) {
    for (const phase of ['queued', 'in-flight']) {
        test(pathname + ': ' + phase + ' cold capture may display but must not store its result', async () => {
            const h = run(pathname, 'no IPC', {cold: true, autoload: false, transport: 'ipc'});
            assert.equal(h.image.hidden, true, 'start with the default empty preview');
            assert.equal(h.stored.get(cacheKey), undefined);
            assert.equal(h.requestCount(), 0);
            if (phase === 'in-flight') h.fireCapture();
            h.window.appChatAvatar.setExternalAvatar(mirrored, 'vrm');
            assert.equal(h.image.src, mirrored);
            h.putOwner(); // The independent Pet owner populates the shared storage.
            if (phase === 'queued') h.fireCapture();
            h.complete(captured);
            await tick();
            assert.equal(h.image.src, captured, 'receive and display the ordinary capture result');
            assert.equal(h.window.appChatAvatar.getCurrentAvatarDataUrl(), captured);
            assert.deepEqual(h.writes, []);
            assert.equal(JSON.parse(h.stored.get(cacheKey)).dataUrl, owner);
            assert.deepEqual(h.network, []);
        });
    }
}

test('a canonical cold local capture still displays and owns durable storage', async () => {
    const h = run('/', 'no IPC', {cold: true, transport: 'local', autoload: false});
    h.fireCapture();
    h.complete(captured);
    await tick();
    assert.equal(h.image.src, captured);
    assert.deepEqual(h.writes, [cacheKey]);
    assert.equal(JSON.parse(h.stored.get(cacheKey)).dataUrl, captured);
    assert.ok(h.network.some(x => x.url === '/api/card-drop/active-character' && x.body.dataUrl === captured));
});

for (const pathname of ['/chat', '/chat_full']) {
    test(pathname + ': the current IPC portrait wins over an older stored portrait after same-model reload', () => {
        const h = run(pathname, 'after init', {pngtuber: true, autoload: false});
        const old = JSON.parse(h.stored.get(cacheKey));
        h.stored.set(cacheKey, JSON.stringify({...old, modelType: 'vrm'}));
        h.window.dispatchEvent({type: 'pngtuber-model-loading', detail: {loadToken: 1}});
        assert.equal(h.window.appChatAvatar.getCachedPreview(), null);
        h.window.appChatAvatar.refreshDisplayedAvatar();
        assert.deepEqual([h.image.src, h.window.appChatAvatar.getCurrentAvatarModelType()], [mirrored, 'pngtuber']);
        assert.equal(JSON.parse(h.stored.get(cacheKey)).dataUrl, owner);
    });
}

for (const change of ['different role', 'new revision', 'different model path']) {
    test('a prior IPC portrait does not override a current stored fallback after a ' + change, () => {
        const h = run('/chat', 'after init', {pngtuber: true, autoload: false});
        const w = h.window;
        if (change === 'different role') {
            w.lanlan_config.lanlan_name = 'B';
            h.putOwner('B');
        } else if (change === 'new revision') {
            w.lanlan_config.switch_revision++;
        } else {
            w.pngtuberManager.config.idle_image = '/new-idle.png';
            h.putOwner('A', owner, pngtuberCacheKey.replace('/idle.png','/new-idle.png'));
        }
        assert.equal(w.appChatAvatar.getCurrentAvatarDataUrl(), owner, 'stale memory is already ineligible');
        w.dispatchEvent({type: 'pngtuber-model-loading', detail: {loadToken: 1}});
        const activeKey = 'neko_avatar:' + w.lanlan_config.lanlan_name;
        const old = JSON.parse(h.stored.get(activeKey));
        h.stored.set(activeKey, JSON.stringify({...old, modelType: 'live2d'}));
        assert.deepEqual([w.appChatAvatar.getCurrentAvatarDataUrl(), w.appChatAvatar.getCurrentAvatarModelType()],
            [owner, 'live2d']);
        h.stored.clear();
        assert.equal(w.appChatAvatar.getCurrentAvatarDataUrl(), '', 'do not display a known foreign IPC portrait');
        w.appChatAvatar.setExternalAvatar(captured, 'pngtuber');
        assert.equal(w.appChatAvatar.getCurrentAvatarDataUrl(), captured, 'a new matching IPC receipt works normally');
    });
}

test('an unbound early IPC portrait does not outrank a valid current stored fallback', () => {
    const h = run('/chat', 'no IPC', {pngtuber: true, autoload: false});
    const w = h.window;
    w.lanlan_config.lanlan_name = '';
    w.appChatAvatar.setExternalAvatar(mirrored, 'pngtuber');
    w.lanlan_config.lanlan_name = 'A';
    w.dispatchEvent({type: 'pngtuber-model-loading', detail: {loadToken: 1}});
    assert.equal(w.appChatAvatar.getModelAvatarDataUrl(), owner);
    h.stored.clear();
    assert.equal(w.appChatAvatar.getModelAvatarDataUrl(), mirrored, 'preserve legacy last-resort fallback');
});
