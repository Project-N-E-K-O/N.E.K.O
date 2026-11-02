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
    const w = dom.window, messages = new Map(), timers = new Map(), calls = [], plays = [], cardChoices = [];
    let now = 100000, next = 0, hidden = false, failing = false, cardChoiceStatus = 200;
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
    w.fetch = async (url, init) => {
        if (String(url).endsWith('/api/proactive/music_card_decision')) {
            cardChoices.push(JSON.parse(init.body));
            return { ok: cardChoiceStatus === 200, status: cardChoiceStatus, json: async () => ({ success: cardChoiceStatus === 200 }) };
        }
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
    return { w, calls, plays, timers, message, cardChoices,
        show: () => action('music_recommendation_shown'),
        choose: kind => action('music_recommendation_decision', kind),
        fail: value => { failing = value; },
        failCardChoice: status => { cardChoiceStatus = status; },
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

for (const restoreBeforeGrant of [false, true]) test(`hiding during an automatic decision revokes playback even if restored=${restoreBeforeGrant}`, async t => {
    const s = surface(t);
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    const request = s.w.fetch;
    s.w.fetch = async (url, init) => {
        const result = await request(url, init);
        if (JSON.parse(init.body).kind === 'accepted') await gate;
        return result;
    };
    try {
        s.show(); await s.advance(10000);
        assert.equal(s.calls.filter(event => event.kind === 'accepted').length, 1);
        assert.equal(s.message().blocks[0].state, 'saving');
        s.visibility(true);
        if (restoreBeforeGrant) s.visibility(false);
        release(); await settle();
        assert.equal(s.plays.length, 0);
        assert.equal(s.message().blocks[0].state, 'failed');
        assert.equal(s.calls.find(event => event.kind === 'failed')?.reason, 'autoplay_visibility_changed');
        s.visibility(false); await s.advance(20000);
        assert.equal(s.plays.length, 0, 'returning to the page does not replay the revoked grant');
        assert.equal(s.calls.filter(event => event.kind === 'accepted').length, 1);
    } finally { release(); await settle(); }
});

test('an explicit music choice remains authorized if the page hides while its response is pending', async t => {
    const s = surface(t);
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    const request = s.w.fetch;
    s.w.fetch = async (url, init) => {
        const result = await request(url, init);
        if (JSON.parse(init.body).kind === 'accepted') await gate;
        return result;
    };
    try {
        s.show(); s.choose('accepted'); await settle();
        s.visibility(true); release(); await settle();
        assert.equal(s.plays.length, 1);
        assert.equal(s.calls.find(event => event.kind === 'accepted').reason, undefined);
    } finally { release(); await settle(); }
});

for (const [incoming, expected] of [[112000, 110000], [108000, 108000], [0, 110000]]) test(`mirrored deadline ${incoming} converges in the serialized host view to ${expected}`, async t => {
    const s = surface(t);
    const original = structuredClone(s.message());
    let displayed = structuredClone(original);
    // applyChatMirror updates the host first, then delivers neko:chat-mirror.
    // Keep the host snapshot separate from the controller's original object.
    s.w.reactChatWindowHost = {
        updateMessage(_id, patch) { displayed = structuredClone({ ...displayed, ...patch }); },
    };
    const publish = s.w.__nekoMirrorChatUpdate;
    let publications = 0;
    s.w.__nekoMirrorChatUpdate = (...args) => { publications++; publish(...args); };
    s.show(); await s.advance(2000);
    const before = publications;
    const patch = { blocks: [{ ...displayed.blocks[0], autoPlayAt: incoming }] };
    s.w.reactChatWindowHost.updateMessage(original.id, patch);
    s.w.dispatchEvent(new s.w.CustomEvent('neko:chat-mirror', {
        detail: { type: 'update', messageId: original.id, patch: structuredClone(patch) },
    }));
    assert.equal(displayed.blocks[0].autoPlayAt, expected);
    assert.equal(Math.ceil((displayed.blocks[0].autoPlayAt - s.w.Date.now()) / 1000), (expected - 102000) / 1000);
    assert.equal(publications - before, incoming === expected ? 0 : 1);
    s.w.dispatchEvent(new s.w.CustomEvent('neko:chat-mirror', {
        detail: { type: 'update', messageId: original.id, patch: { blocks: structuredClone(displayed.blocks) } },
    }));
    assert.equal(publications - before, incoming === expected ? 0 : 1, 'the canonical deadline does not echo');
    await s.advance(expected - s.w.Date.now() - 1); assert.equal(s.plays.length, 0);
    await s.advance(1); assert.equal(s.plays.length, 1);
});

for (const state of ['skipped', 'accepted', 'playing', 'paused', 'ended', 'stopped', 'failed', 'unavailable']) {
    for (const incoming of ['pending', 'saving', ...(state === 'unavailable' ? [] : ['unavailable']), ...(['accepted', 'unavailable'].includes(state) ? [] : ['accepted'])]) test(`late ${incoming} mirror cannot reopen ${state} music`, async t => {
        const s = surface(t);
        const original = structuredClone(s.message());
        let displayed = structuredClone(original);
        s.w.reactChatWindowHost = { updateMessage(_id, patch) { displayed = structuredClone({ ...displayed, ...patch }); } };
        s.w.nekoMusicRecommendations.recordPlayback(id, state);
        const settled = structuredClone(displayed.blocks[0]);
        const patch = { blocks: [{ ...original.blocks[0], state: incoming, autoPlayAt: 110000 }] };
        s.w.reactChatWindowHost.updateMessage(original.id, patch);
        s.w.dispatchEvent(new s.w.CustomEvent('neko:chat-mirror', {
            detail: { type: 'update', messageId: original.id, patch: structuredClone(patch) },
        }));
        assert.deepEqual(displayed.blocks[0], settled, 'correct the host that already rendered the stale patch');
        assert.equal(s.w.nekoMusicRecommendations.hasPending(), false);
        s.show(); await s.advance(20000);
        assert.equal(s.plays.length, 0, 'stale choices cannot restart a countdown or replay audio');
    });
}

for (const kind of ['accepted', 'skipped']) test(`manual ${kind} feeds the backend music cooldown once`, async t => {
    const s = surface(t); s.show(); s.choose(kind); s.choose(kind); await s.advance(20000);
    assert.deepEqual(s.cardChoices, [{ lanlan_name: 'Neko', recommendation_id: id, decision: kind }]);
});
test('countdown acceptance is not reported as a user choice', async t => {
    const s = surface(t); s.show(); await s.advance(20000);
    assert.equal(s.plays.length, 1);
    assert.deepEqual(s.cardChoices, []);
});
test('an offline skip still counts toward the cooldown, and its retry does not count twice', async t => {
    const s = surface(t); s.show(); s.fail(true); s.choose('skipped'); await settle();
    assert.equal(s.message().blocks[0].state, 'pending');
    s.fail(false); s.choose('skipped'); await settle();
    assert.equal(s.message().blocks[0].state, 'skipped');
    assert.deepEqual(s.cardChoices, [{ lanlan_name: 'Neko', recommendation_id: id, decision: 'skipped' }]);
});
test('a changed choice after a failed save is reported for the same card', async t => {
    const s = surface(t); s.show(); s.fail(true); s.choose('skipped'); await settle();
    s.fail(false); s.choose('accepted'); await settle();
    assert.deepEqual(s.cardChoices.map(choice => [choice.recommendation_id, choice.decision]),
        [[id, 'skipped'], [id, 'accepted']]);
});
test('the card owner is captured before waiting for request headers', async t => {
    const s = surface(t); s.show();
    const waiting = [];
    s.w.nekoLocalMutationSecurity.getMutationHeaders = () => new Promise(resolve => waiting.push(() => resolve({})));
    s.choose('skipped'); await settle();
    s.w.lanlan_config.lanlan_name = 'Other';
    for (const release of waiting) release();
    await settle();
    assert.equal(s.cardChoices[0].lanlan_name, 'Neko');
});
test('a rejected card choice report is sent again on the next click', async t => {
    const s = surface(t); s.show(); s.fail(true); s.failCardChoice(503);
    s.choose('skipped'); await settle();
    s.fail(false); s.failCardChoice(200); s.choose('skipped'); await settle();
    assert.deepEqual(s.cardChoices.map(choice => choice.decision), ['skipped', 'skipped']);
    s.choose('skipped'); await settle();
    assert.equal(s.cardChoices.length, 2, 'a delivered report is not repeated');
});
