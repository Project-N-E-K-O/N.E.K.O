const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const { JSDOM } = createRequire(path.join(root, 'frontend/react-neko-chat/package.json'))('jsdom');
const source = fs.readFileSync(path.join(root, 'static/app/app-recommendations.js'), 'utf8');
const id = '19f3bbca-a275-43d4-923d-1b2ceee91943';
const settle = async () => { for (let i = 0; i < 60; i++) await Promise.resolve(); };

function surface(t, hub = new Set(), server = new Map()) {
    const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost:48911/chat', runScripts: 'outside-only', pretendToBeVisual: true });
    const w = dom.window, messages = new Map(), timers = new Map(), calls = [], plays = [];
    let now = 100000, next = 0, hidden = false, failing = false;
    const RealDate = w.Date;
    w.Date = class extends RealDate { static now() { return now; } };
    w.setTimeout = (fn, ms) => { const key = ++next; timers.set(key, { fn, at: now + ms }); return key; };
    w.clearTimeout = key => timers.delete(key);
    Object.defineProperty(w.document, 'visibilityState', { get: () => hidden ? 'hidden' : 'visible' });
    w.lanlan_config = { lanlan_name: 'Neko' };
    w.AbortSignal.timeout = () => undefined;
    w.nekoLocalMutationSecurity = { getMutationHeaders: async () => ({}) };
    w.reactChatWindowHost = { appendMessage: message => messages.set(message.id, message),
        updateMessage: (key, patch) => Object.assign(messages.get(key), patch) };
    w.__nekoMirrorChatUpdate = (host, messageId, patch) => {
        host.updateMessage(messageId, patch);
        for (const peer of hub) if (peer !== w) peer.queueMicrotask(() => peer.dispatchEvent(new peer.CustomEvent('neko:chat-mirror', {
            detail: { type: 'update', messageId, patch: JSON.parse(JSON.stringify(patch)) }
        })));
    };
    w.fetch = async (_url, init) => {
        const event = JSON.parse(init.body); calls.push(event);
        if (failing) throw Error('offline');
        let grant = false;
        if (['accepted', 'skipped'].includes(event.kind) && !server.has(id)) { server.set(id, event.kind); grant = event.kind === 'accepted'; }
        return { ok: true, status: 200, json: async () => ({ success: true, state: server.get(id) || 'pending', playback_grant: grant }) };
    };
    w.dispatchMusicPlayDetailed = async (track, options) => { plays.push({ track, options }); return { ok: true }; };
    hub.add(w);
    w.eval(source);
    w.nekoMusicRecommendations.offer({ id, state: 'pending', expires_at: 1900,
        track: { name: 'Song', artist: 'Artist', url: 'https://example.com/song.mp3', cover: '' } });
    const message = () => messages.values().next().value;
    const action = (name, kind) => w.dispatchEvent(new w.CustomEvent('react-chat-window:action', {
        detail: { message: message(), action: { action: name, payload: { recommendationId: id, kind } } }
    }));
    t.after(() => { w.dispatchEvent(new w.Event('pagehide')); hub.delete(w); timers.clear(); w.close(); });
    return { w, calls, plays, timers, message,
        show: () => action('music_recommendation_shown'),
        choose: kind => action('music_recommendation_decision', kind),
        fail: value => { failing = value; },
        visibility(value) { hidden = value; w.document.dispatchEvent(new w.Event('visibilitychange')); },
        async advance(ms) {
            const end = now + ms;
            for (let i = 0; i < 1000; i++) {
                await settle();
                const entry = [...timers].filter(([, timer]) => timer.at <= end).sort((a,b) => a[1].at - b[1].at)[0];
                if (!entry) { now = end; await settle(); return; }
                now = entry[1].at; timers.delete(entry[0]); entry[1].fn();
            }
            throw Error('unbounded timers');
        }
    };
}

test('ten seconds starts on actual exposure and records automatic acceptance before playing', async t => {
    const s = surface(t);
    await s.advance(20000); assert.equal(s.plays.length, 0);
    s.show(); await s.advance(9999); assert.equal(s.plays.length, 0);
    await s.advance(1); assert.equal(s.plays.length, 1);
    assert.equal(s.calls.find(event => event.kind === 'accepted').reason, 'countdown_elapsed');
    s.show(); await s.advance(20000); assert.equal(s.plays.length, 1);
});
for (const kind of ['accepted', 'skipped']) test(`manual ${kind} cancels the countdown`, async t => {
    const s = surface(t); s.show(); await s.advance(4000); s.choose(kind); await s.advance(20000);
    assert.equal(s.plays.length, kind === 'accepted' ? 1 : 0);
    assert.equal(s.calls.filter(event => event.kind === kind).length, 1);
    assert.equal(s.calls.find(event => event.kind === kind).reason, undefined);
});
test('concurrent mirrored exposures share the earliest deadline and a single playback grant', async t => {
    const hub = new Set(), server = new Map(), a = surface(t, hub, server), b = surface(t, hub, server);
    a.show(); b.show(); await settle();
    assert.equal(a.message().blocks[0].autoPlayAt, b.message().blocks[0].autoPlayAt);
    await a.advance(10000); await b.advance(10000);
    assert.equal(a.plays.length + b.plays.length, 1);
});
test('remote skip prevents the other window from automatically accepting', async t => {
    const hub = new Set(), server = new Map(), a = surface(t, hub, server), b = surface(t, hub, server);
    a.show(); b.show(); b.choose('skipped'); await settle(); await a.advance(20000);
    assert.equal(a.plays.length + b.plays.length, 0);
});
for (const reset of ['pagehide', 'react-chat-window:messages-cleared', 'character']) test(`${reset} retires automatic playback`, async t => {
    const s = surface(t); s.show();
    if (reset === 'character') s.w.lanlan_config.lanlan_name = 'Other';
    else s.w.dispatchEvent(new s.w.Event(reset));
    await s.advance(20000); assert.equal(s.plays.length, 0);
});
test('hidden pages do not autoplay overdue proposals when restored', async t => {
    const s = surface(t); s.show(); await s.advance(3000); s.visibility(true);
    await s.advance(20000); s.visibility(false); await s.advance(10000);
    assert.equal(s.plays.length, 0);
    s.choose('accepted'); await settle(); assert.equal(s.plays.length, 1);
});
test('an offline automatic decision fails closed and remains manually retryable', async t => {
    const s = surface(t); s.show(); s.fail(true); await s.advance(10000);
    assert.equal(s.plays.length, 0); assert.equal(s.message().blocks[0].error, true);
    const attempt = s.calls.find(event => event.kind === 'accepted');
    s.fail(false); s.choose('accepted'); await settle();
    assert.equal(s.plays.length, 1);
    assert.equal(s.calls.filter(event => event.kind === 'accepted')[1].event_id, attempt.event_id);
    assert.deepEqual(s.calls.filter(event => event.kind === 'accepted')[1], attempt, 'retry preserves the original time and automatic trigger');
});
