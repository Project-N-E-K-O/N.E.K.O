const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const modulePath = 'static/app/app-chat-avatar.js';
const source = readFileSync(path.resolve(__dirname, '../..', modulePath), 'utf8');
const owner = 'data:image/png;base64,b3duZXI=';
const mirrored = 'data:image/png;base64,Zm9sbG93ZXI=';
const cacheKey = 'neko_avatar:A';

function run(pathname, order) {
    const writes = [];
    const network = [];
    const seen = [];
    const listeners = new Map();
    const stored = new Map([[cacheKey, JSON.stringify({
        dataUrl: owner, modelType: 'vrm', capturedAt: 1, cacheKey: 'vrm:/A.vrm',
    })]]);
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
        lanlan_config: {lanlan_name: 'A', model_type: 'vrm'},
        vrmModel: '/A.vrm',
        addEventListener(type, handler) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(handler);
        },
        dispatchEvent(event) {
            if (event.type === 'chat-avatar-preview-updated') seen.push(event.detail);
            for (const handler of listeners.get(event.type) ?? []) handler(event);
        },
    };
    vm.runInNewContext(source, {
        window,
        document: {getElementById: id => nodes[id] ?? null},
        CustomEvent: class { constructor(type, {detail} = {}) { Object.assign(this, {type, detail}); } },
        setTimeout() {},
        clearTimeout() {},
        localStorage: {
            getItem: key => stored.get(key) ?? null,
            removeItem: key => stored.delete(key),
            setItem(key, value) { writes.push(key); stored.set(key, value); },
        },
        fetch: async (url, options) => {
            if (options?.method === 'POST') network.push({url, body: JSON.parse(options.body)});
            return {ok: true, json: async () => ({})};
        },
    }, {filename: modulePath});
    // Both old and staged ingress call this same preview consumer. Ordering
    // and the old private pending slot are tested separately by the loader.
    if (order === 'during bootstrap') window.appChatAvatar.setExternalAvatar(mirrored, 'vrm');
    window.appChatAvatar.init();
    if (order === 'after init') window.appChatAvatar.setExternalAvatar(mirrored, 'vrm');
    window.dispatchEvent({type: 'vrm-model-loaded'});
    return {image, writes, stored, network, seen, window};
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
