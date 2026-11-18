const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const { JSDOM, VirtualConsole } = createRequire(path.join(root, 'frontend/react-neko-chat/package.json'))('jsdom');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const controller = process.env.RECOMMENDATIONS_REVISION
    ? execFileSync('git', ['show', `${process.env.RECOMMENDATIONS_REVISION}:static/app/app-recommendations.js`], { cwd: root, encoding: 'utf8' })
    : read('static/app/app-recommendations.js');
const settle = async () => { await new Promise(setImmediate); for (let i = 0; i < 80; i++) await Promise.resolve(); };
const rec = (id = '19f3bbca-a275-43d4-923d-1b2ceee91943') => ({ id, state: 'pending', expires_at: Date.now() / 1000 + 1800,
    track: { name: 'Existing song', artist: 'Artist', url: 'https://example.test/song.mp3', cover: '' } });

function fixture(t) {
    const channels = new Set(), wire = [], errors = [], surfaces = [];
    t.after(() => {
        for (const surface of surfaces) surface.dom.window.close();
        for (const channel of channels) channel.close();
        assert.deepEqual(errors, [], 'production initialization must not emit script errors');
    });
    async function surface(label, { name = 'Second', owner = false, injected = false, interactive = false } = {}) {
        const virtualConsole = new VirtualConsole();
        virtualConsole.on('jsdomError', error => errors.push(error.message));
        const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://127.0.0.1:48911/chat', runScripts: 'outside-only', virtualConsole });
        const w = dom.window, messages = new Map(), fetches = [];
        let releaseHttp, resolveConfigStarted;
        const configStarted = new Promise(resolve => { resolveConfigStarted = resolve; });
        w.BroadcastChannel = class {
            constructor(channel) { this.channel = channel; channels.add(this); }
            postMessage(data) {
                wire.push({ label, channel: this.channel, data: JSON.parse(JSON.stringify(data)) });
                for (const other of channels) if (other !== this && other.channel === this.channel) {
                    queueMicrotask(() => { if (channels.has(other)) other.onmessage?.({ data }); });
                }
            }
            close() { channels.delete(this); }
        };
        w.appState = { dom: {} };
        w.VRM_PATHS = { user_vrm: '/user_vrm', static_vrm: '/static/vrm', isLoaded: true };
        w.AbortSignal.timeout = () => undefined;
        w.reactChatWindowHost = {
            appendMessage: message => messages.set(message.id, message),
            updateMessage: (id, patch) => Object.assign(messages.get(id), patch),
            removeMessage: id => messages.delete(id)
        };
        w.fetch = async url => {
            if (url === '/api/music/domains') return { ok: true, json: async () => ({ domains: [] }) };
            fetches.push(url);
            assert.equal(url, '/api/config/page_config', 'this fixture must exercise the production configuration request');
            resolveConfigStarted();
            await new Promise(resolve => { releaseHttp = resolve; });
            return { json: async () => ({ success: true, lanlan_name: name, model_type: 'live2d' }) };
        };
        if (owner) w.lanlan_config = { lanlan_name: name };
        if (injected) { w.__NEKO_MULTI_WINDOW__ = true; w.__nekoRequestConfigInjection = () => {}; }
        if (interactive) await settle();
        // These are the real /chat template's ordering and lifecycle listeners.
        w.eval(read('static/jukebox/music_ui.js'));
        if (interactive && !owner) w.eval(read('static/js/index.js'));
        w.eval(controller);
        if (!owner) {
            if (!interactive) w.eval(read('static/js/index.js'));
            w.eval(read('static/app/app.js'));
        }
        const s = { w, dom, messages, fetches,
            requests: () => wire.filter(item => item.label === label && item.data.type === 'recommendation_request'),
            async resolveConfig() {
                if (injected) w.dispatchEvent(new w.CustomEvent('neko:config-injected', { detail: { lanlan_name: name, model_type: 'live2d' } }));
                else { assert.ok(releaseHttp, 'real app.ready must start loadPageConfig'); releaseHttp(); }
                await w.pageConfigReady; await settle();
            }
        };
        surfaces.push(s);
        // ready() on an interactive document starts asynchronously. Observe its
        // real HTTP side effect instead of assuming a timer has already fired.
        if (interactive && !owner && !injected) await configStarted;
        await settle();
        return s;
    }
    return { surface, wire };
}

for (const injected of [false, true]) {
    test(`reopened nondefault character requests its existing snapshot after ${injected ? 'IPC' : 'HTTP'} configuration readiness`, async t => {
        const f = fixture(t), owner = await f.surface('owner', { owner: true });
        owner.w.nekoMusicRecommendations.offer(rec(), 'existing-turn');
        const reopened = await f.surface('reopened', { injected });
        assert.equal(reopened.w.lanlan_config.lanlan_name, '');
        assert.equal(reopened.requests().length, 0, 'an unresolved character must not send fallback-Neko requests');
        assert.equal(reopened.messages.size, 0);
        await reopened.resolveConfig();
        assert.equal(reopened.w.lanlan_config.lanlan_name, 'Second');
        assert.equal(reopened.messages.size, 1, 'configuration completion alone must recover the existing card');
        assert.equal(reopened.messages.values().next().value.author, 'Second');
        assert.equal(reopened.requests().length, 1, 'the readiness promise is observed once despite DCL/pageshow');
        assert.equal(reopened.requests()[0].data.character, 'Second');
        assert.equal(reopened.fetches.length, injected ? 0 : 1);
    });
}

test('unresolved chat identity rejects both an existing Neko snapshot and live Neko appends', async t => {
    const f = fixture(t), neko = await f.surface('neko', { owner: true, name: 'Neko' });
    neko.w.nekoMusicRecommendations.offer(rec(), 'neko-existing');
    const reopened = await f.surface('reopened');
    assert.equal(reopened.messages.size, 0);
    neko.w.nekoMusicRecommendations.offer(rec('023bf0f3-77ac-433a-8138-5ba7dc6da15f'), 'neko-new');
    await settle();
    assert.equal(reopened.messages.size, 0, 'production mirror appends must be removed while ownership is unknown');
    await reopened.resolveConfig();
    assert.equal(reopened.messages.size, 0, 'a later Second identity must not retain Neko cards');
    assert.equal(reopened.w.nekoMusicRecommendations.hasPending(), false);
});

for (const restoreBeforeReady of [false, true]) {
    test(`pagehide before config readiness ${restoreBeforeReady ? 'followed by early pageshow' : 'defers snapshots until pageshow'}`, async t => {
        const f = fixture(t), owner = await f.surface('owner', { owner: true });
        owner.w.nekoMusicRecommendations.offer(rec(), 'existing-turn');
        const reopened = await f.surface('reopened');
        reopened.w.dispatchEvent(new reopened.w.PageTransitionEvent('pagehide', { persisted: true }));
        if (restoreBeforeReady) reopened.w.dispatchEvent(new reopened.w.Event('pageshow'));
        await reopened.resolveConfig();
        assert.equal(reopened.requests().length, restoreBeforeReady ? 1 : 0);
        assert.equal(reopened.messages.size, restoreBeforeReady ? 1 : 0);
        if (!restoreBeforeReady) {
            reopened.w.dispatchEvent(new reopened.w.Event('pageshow')); await settle();
            assert.equal(reopened.messages.size, 1);
            assert.equal(reopened.requests().length, 1);
        }
    });
}

test('a controller loaded after DOMContentLoaded observes the existing unresolved configuration promise', { timeout: 5000 }, async t => {
    const f = fixture(t), owner = await f.surface('owner', { owner: true });
    owner.w.nekoMusicRecommendations.offer(rec(), 'existing-turn');
    const reopened = await f.surface('reopened', { interactive: true });
    assert.notEqual(reopened.w.document.readyState, 'loading');
    assert.equal(reopened.messages.size, 0);
    await reopened.resolveConfig();
    assert.equal(reopened.messages.size, 1);
    assert.ok(reopened.requests().every(item => item.data.character === 'Second'));
});

test('a default Neko character also waits for authoritative configuration then recovers normally', async t => {
    const f = fixture(t), owner = await f.surface('owner', { owner: true, name: 'Neko' });
    owner.w.nekoMusicRecommendations.offer(rec(), 'existing-turn');
    const reopened = await f.surface('reopened', { name: 'Neko' });
    assert.equal(reopened.requests().length, 0);
    assert.equal(reopened.messages.size, 0);
    await reopened.resolveConfig();
    assert.equal(reopened.messages.size, 1);
    assert.equal(reopened.requests()[0].data.character, 'Neko');
});

test('fresh readiness requests retain nonce and foreign-character guards', async t => {
    const f = fixture(t), reopened = await f.surface('reopened');
    await reopened.resolveConfig();
    const old = reopened.requests().at(-1).data;
    reopened.w.dispatchEvent(new reopened.w.Event('pageshow')); await settle();
    const current = reopened.requests().at(-1).data;
    assert.notEqual(current.requestId, old.requestId);
    const message = { id: 'music-recommendation-' + rec().id, role: 'assistant', author: 'Second', blocks: [{ type: 'music_recommendation', recommendationId: rec().id, state: 'pending', track: rec().track, expiresAt: Date.now() + 1800000 }] };
    const deliver = (requestId, character, author = 'Second') => reopened.w.dispatchEvent(new reopened.w.CustomEvent('neko:chat-mirror', { detail: {
        type: 'recommendation_snapshot', requestId, character, messages: [{ ...message, author }]
    } }));
    deliver(old.requestId, 'Second');
    deliver(current.requestId, 'Neko');
    deliver(current.requestId, 'Second', 'Neko');
    assert.equal(reopened.messages.size, 0);
    deliver(current.requestId, 'Second');
    assert.equal(reopened.messages.size, 1);
});
