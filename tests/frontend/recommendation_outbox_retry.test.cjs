const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const { JSDOM, VirtualConsole } = createRequire(path.join(root, 'frontend/react-neko-chat/package.json'))('jsdom');
const source = process.env.RECOMMENDATIONS_REVISION
    ? execFileSync('git', ['show', `${process.env.RECOMMENDATIONS_REVISION}:static/app/app-recommendations.js`], { cwd: root, encoding: 'utf8' })
    : fs.readFileSync(path.join(root, 'static/app/app-recommendations.js'), 'utf8');
const A = '00000000-0000-4000-8000-000000000001';
const B = '00000000-0000-4000-8000-000000000002';
const C = '00000000-0000-4000-8000-000000000003';
const settle = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

async function surface(t, options = {}) {
    const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://127.0.0.1:48911/chat',
        runScripts: 'outside-only', virtualConsole: new VirtualConsole() });
    const w = dom.window, timers = new Map(), calls = [], applied = [], seen = new Set(), messages = new Map(), actions = [];
    let now = 1_700_000_000_000, timerId = 0;
    const RealDate = w.Date;
    w.Date = class extends RealDate { static now() { return now; } };
    w.setTimeout = (fn, delay = 0) => { const id = ++timerId; timers.set(id, { fn, at: now + Number(delay), delay: Number(delay) }); return id; };
    w.clearTimeout = id => timers.delete(id);
    w.AbortSignal.timeout = () => undefined;
    w.lanlan_config = { lanlan_name: 'Neko' };
    w.nekoLocalMutationSecurity = { getMutationHeaders: async () => ({ 'X-CSRF-Token': 'fixture' }) };
    w.reactChatWindowHost = { appendMessage: m => messages.set(m.id, m), updateMessage: (id, patch) => Object.assign(messages.get(id), patch) };
    w.dispatchMusicPlayDetailed = async () => { actions.push('play'); return { ok: true }; };
    w.open = () => { actions.push('open'); };
    const api = {
        owner: 'A', owners: new Map([[A, 'A'], [B, 'B'], [C, 'A']]),
        reply(call) {
            // Like the local route, the recommendation UUID selects its immutable
            // owner. There is no client-supplied owner that could rebind a fact.
            if (api.owners.get(call.id) !== call.owner) return { status: 409, body: { success: false, error_code: 'recommendation_not_found' } };
            const key = call.id + ':' + call.event.event_id;
            if (!seen.has(key)) { seen.add(key); applied.push({ id: call.id, owner: call.owner, event: call.event }); }
            return { status: 200, body: { success: true, state: call.event.kind, playback_grant: true, action_grant: true } };
        }
    };
    w.fetch = async (url, init) => {
        const match = url.match(/\/recommendations\/([^/]+)\/events$/);
        assert.ok(match, 'outbox only calls the local evidence endpoint');
        const call = { id: decodeURIComponent(match[1]), event: JSON.parse(init.body), owner: api.owner, at: now };
        calls.push(call);
        const response = options.respond ? await options.respond(call, api, calls) : api.reply(call);
        return { status: response.status, ok: response.status < 300, json: async () => response.body };
    };
    const items = () => Array.from({ length: w.localStorage.length }, (_, i) => w.localStorage.key(i))
        .filter(key => key.startsWith('neko.music-recommendation-event.v1.')).map(key => JSON.parse(w.localStorage.getItem(key)));
    async function advance(ms) {
        const end = now + ms;
        for (let n = 0; n < 1000; n++) {
            await settle();
            assert.ok(timers.size <= 1, 'retry callbacks never multiply');
            const next = Array.from(timers).filter(([, value]) => value.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
            if (!next) { now = end; await settle(); return; }
            now = next[1].at; timers.delete(next[0]); next[1].fn();
        }
        throw Error('retry scheduling did not remain bounded');
    }
    async function until(predicate) {
        for (let n = 0; n < 40; n++) {
            await settle(); if (predicate()) return;
            assert.ok(timers.size, 'pending evidence must schedule its own recovery');
            assert.ok(timers.size <= 1, 'only one outbox retry timer may be armed');
            const next = Math.min(...Array.from(timers.values(), timer => timer.at));
            assert.ok(next > now && next - now <= 30_000, 'retry is delayed and bounded by 30 seconds');
            await advance(next - now);
        }
        throw Error('evidence did not recover within bounded retries');
    }
    const emit = (id, kind = 'stopped') => {
        if (!messages.has('music-recommendation-' + id)) w.nekoMusicRecommendations.offer({ id, state: 'accepted',
            expires_at: now / 1000 + 1800, track: { name: 'Fixture', artist: 'Fixture', url: 'https://media.example/song' } }, 'original-turn');
        w.nekoMusicRecommendations.recordPlayback(id, kind, { playback_id: 'playback-' + id, listened_seconds: 12, position_seconds: 12 });
    };
    t.after(() => { w.dispatchEvent(new w.Event('pagehide')); timers.clear(); dom.window.close(); });
    w.eval(source); await settle(); // Finish natural startup events before injecting a failure.
    return { w, api, calls, applied, actions, timers, items, emit, advance, until, now: () => now,
        online: () => w.dispatchEvent(new w.Event('online')),
        hide: () => w.dispatchEvent(new w.Event('pagehide')),
        show: () => w.dispatchEvent(new w.Event('pageshow')) };
}

test('not-found under another account retains the original UUID and event until its owner returns', async t => {
    const s = await surface(t, { respond: (call, api, calls) => calls.length === 1
        ? { status: 503, body: { success: false, error_code: 'feedback_unavailable' } } : api.reply(call) });
    s.emit(A); await settle();
    const original = s.items()[0]; assert.equal(original.id, A);
    s.api.owner = 'B'; s.online(); await settle();
    assert.deepEqual(s.items(), [original]); assert.equal(s.applied.length, 0);
    s.api.owner = 'A'; await s.until(() => s.items().length === 0);
    assert.equal(s.applied.length, 1); assert.equal(s.applied[0].owner, 'A');
    assert.ok(s.calls.every(call => call.id === A && call.event.event_id === original.event.event_id));
    assert.deepEqual(s.actions, []);
});

test('a deferred A recommendation does not block B and each recommendation keeps its own event order', async t => {
    const s = await surface(t); s.api.owner = 'B';
    s.emit(A, 'playing'); s.emit(A, 'stopped'); s.emit(B, 'playing'); s.emit(B, 'stopped');
    await s.until(() => s.applied.filter(row => row.id === B).length === 2);
    assert.deepEqual(s.applied.map(row => row.event.kind), ['playing', 'stopped']);
    assert.ok(s.applied.every(row => row.id === B && row.owner === 'B'));
    assert.deepEqual(s.items().map(row => [row.id, row.event.kind]), [[A, 'playing'], [A, 'stopped']]);
    assert.ok(s.calls.filter(call => call.owner === 'B' && call.id === A).every(call => call.event.kind === 'playing'),
        'do not send a later event for the same deferred recommendation');
    s.api.owner = 'A'; await s.until(() => s.items().length === 0);
    assert.deepEqual(s.applied.filter(row => row.id === A).map(row => row.event.kind), ['playing', 'stopped']);
    assert.deepEqual(s.actions, []);
});

test('anonymous facts resume anonymously without attaching them to the intervening account', async t => {
    const s = await surface(t, { respond: (call, api, calls) => calls.length === 1
        ? { status: 503, body: { success: false } } : api.reply(call) });
    s.api.owners.set(A, 'anonymous'); s.api.owner = 'anonymous'; s.emit(A); await settle();
    const original = s.items()[0];
    s.api.owner = 'B'; s.online(); await settle(); assert.deepEqual(s.items(), [original]);
    s.emit(B); await s.until(() => s.applied.some(row => row.id === B));
    assert.ok(!s.applied.some(row => row.id === A));
    s.api.owner = 'anonymous'; await s.until(() => s.items().length === 0);
    assert.equal(s.applied.find(row => row.id === A).owner, 'anonymous');
    assert.equal(s.applied.find(row => row.id === A).event.event_id, original.event.event_id);
});

for (const failure of ['503', 'timeout']) test(`${failure} recovers online with bounded backoff and becomes idle after acknowledgement`, async t => {
    const s = await surface(t, { respond: (call, api, calls) => {
        if (calls.length <= 3) {
            if (failure === 'timeout') throw Object.assign(new Error('fixture timeout'), { name: 'TimeoutError' });
            return { status: 503, body: { success: false, error_code: 'feedback_unavailable' } };
        }
        return api.reply(call);
    } });
    s.emit(A); await settle(); const original = s.items()[0];
    await s.until(() => s.items().length === 0);
    assert.equal(s.calls.length, 4);
    for (let i = 1; i < s.calls.length; i++) assert.ok(s.calls[i].at - s.calls[i - 1].at >= 1000 && s.calls[i].at - s.calls[i - 1].at <= 30_000);
    assert.ok(s.calls.every(call => call.event.event_id === original.event.event_id));
    assert.equal(s.timers.size, 0); await s.advance(120_000); assert.equal(s.calls.length, 4);
    assert.deepEqual(s.actions, []); assert.ok(s.calls.every(call => call.event.kind === 'stopped'));
});

test('a persistent outage retains one capped retry timer without a tight request loop', async t => {
    const s = await surface(t, { respond: () => ({ status: 503, body: { success: false } }) });
    s.emit(A); await settle(); await s.advance(120_000);
    assert.ok(s.calls.length >= 5 && s.calls.length <= 12);
    assert.equal(s.items().length, 1); assert.equal(s.timers.size, 1);
    assert.deepEqual(s.actions, []);
});

test('new facts, B progress and online wakes do not postpone or reset A backoff', async t => {
    const s = await surface(t); s.api.owner = 'B';
    s.emit(A); await settle(); await s.advance(1000);
    const due = s.timers.values().next().value.at;
    assert.ok(due - s.now() >= 2000);
    s.emit(B); s.online(); await settle();
    assert.equal(s.applied.filter(row => row.id === B).length, 1);
    assert.equal(s.timers.size, 1); assert.equal(s.timers.values().next().value.at, due);
    await s.advance(due - s.now());
    assert.ok(s.timers.values().next().value.at - s.now() >= 4000,
        'unrelated progress did not collapse a blocked item back to first-retry frequency');
    assert.equal(s.items().length, 1); assert.equal(s.items()[0].id, A);
});

test('pagehide retires a retry callback and pageshow resumes retained evidence', async t => {
    let offline = true;
    const s = await surface(t, { respond: (call, api) => offline ? { status: 503, body: { success: false } } : api.reply(call) });
    s.emit(A); await settle(); assert.equal(s.timers.size, 1);
    const retiredCallback = s.timers.values().next().value.fn;
    s.hide(); assert.equal(s.timers.size, 0); await s.advance(120_000); assert.equal(s.calls.length, 1);
    offline = false; retiredCallback(); await settle(); assert.equal(s.calls.length, 1, 'a queued retired callback cannot run while hidden');
    s.show(); await s.until(() => s.items().length === 0); assert.equal(s.applied.length, 1);
});

test('an in-flight failure settling while hidden does not arm work until pageshow', async t => {
    const first = deferred();
    const s = await surface(t, { respond: (call, api, calls) => calls.length === 1 ? first.promise : api.reply(call) });
    s.emit(A); await settle(); s.hide();
    first.resolve({ status: 503, body: { success: false } }); await settle();
    assert.equal(s.items().length, 1); assert.equal(s.timers.size, 0);
    await s.advance(120_000); assert.equal(s.calls.length, 1);
    s.show(); await s.until(() => s.items().length === 0); assert.equal(s.applied.length, 1);
});

test('a late acknowledged old pass removes only its item and preserves work appended after restoration', async t => {
    const first = deferred(); let saved;
    const s = await surface(t, { respond: (call, api, calls) => {
        const response = api.reply(call);
        if (calls.length === 1) { saved = response; return first.promise; }
        return response;
    } });
    s.emit(A); await settle(); s.hide(); s.show(); s.emit(C); await settle();
    assert.ok(s.items().some(row => row.id === C));
    first.resolve(saved); await s.until(() => s.items().length === 0);
    assert.deepEqual(s.applied.map(row => row.id).sort(), [A, C]);
    assert.deepEqual(s.actions, []);
});

test('an event produced at the final flush boundary gets its own recovery without an external wake', async t => {
    const s = await surface(t);
    const storage = s.w.Storage.prototype, remove = storage.removeItem;
    let injected = false;
    storage.removeItem = function (key) {
        const result = remove.call(this, key);
        if (!injected && key.startsWith('neko.music-recommendation-event.v1.')) {
            injected = true;
            // Only a scheduling barrier: the producer still uses the real public
            // player evidence API while the old flush promise is completing.
            s.w.queueMicrotask(() => s.emit(C));
        }
        return result;
    };
    t.after(() => { storage.removeItem = remove; });
    s.emit(A); await settle(); assert.equal(injected, true);
    await s.until(() => s.items().length === 0);
    assert.deepEqual(s.applied.map(row => row.id).sort(), [A, C]);
    assert.ok(s.calls.every(call => !['accepted', 'skipped'].includes(call.event.kind)));
    assert.deepEqual(s.actions, []);
});
