const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const { JSDOM } = createRequire(path.join(root, 'frontend/react-neko-chat/package.json'))('jsdom');
const source = fs.readFileSync(path.join(root, 'static/app/app-recommendations.js'), 'utf8');
const decisionPrefix = 'neko.recommendation-decision.v1.';
const dispatchRetryPrefix = 'neko.recommendation-dispatch-retry.v1.';
const settle = async () => { for (let i = 0; i < 80; i++) await Promise.resolve(); };

function surface(t, { storage = [], respond, reportChoice, character = 'Neko' } = {}) {
    const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost:48911/chat', runScripts: 'outside-only' });
    const w = dom.window, messages = new Map(), events = [], musicChoices = [], plays = [], opens = [], timers = new Map();
    let now = 100000, timerId = 0;
    const RealDate = w.Date;
    w.Date = class extends RealDate { static now() { return now; } };
    w.setTimeout = (fn, delay) => { timers.set(++timerId, { fn, at: now + delay }); return timerId; };
    w.clearTimeout = id => timers.delete(id);
    w.lanlan_config = { lanlan_name: character };
    w.AbortSignal.timeout = () => undefined;
    w.nekoLocalMutationSecurity = { getMutationHeaders: async () => ({}) };
    w.reactChatWindowHost = { appendMessage: m => messages.set(m.id, m), updateMessage: (id, patch) => Object.assign(messages.get(id), patch) };
    w.fetch = async (url, init) => {
        if (url.endsWith('/capture-context')) return { ok: false, status: 503, json: async () => ({ success: false }) };
        if (url === '/api/proactive/music_card_decision') {
            const choice = JSON.parse(init.body); musicChoices.push(choice);
            return reportChoice ? await reportChoice(choice) : { ok: true, status: 200, json: async () => ({ success: true }) };
        }
        const event = JSON.parse(init.body); events.push({ id: decodeURIComponent(url.split('/').at(-2)), event });
        const result = respond ? await respond(event, events.at(-1).id) : { success: true, state: event.kind, playback_grant: event.kind === 'accepted' };
        return { ok: result.success, status: result.status || (result.success ? 200 : 409), json: async () => result };
    };
    w.dispatchMusicPlayDetailed = async (track, options) => { plays.push({ track, options }); return { ok: true }; };
    w.electronShell = { openExternal: async url => opens.push(url) };
    for (const [key, value] of storage) w.localStorage.setItem(key, value);
    w.eval(source);
    t.after(() => { w.dispatchEvent(new w.Event('pagehide')); timers.clear(); w.close(); });
    const offer = (id = 'original', extras = {}) => w.nekoMusicRecommendations.offer({ id, state: 'pending', expires_at: 1900,
        track: { name: id, artist: 'Artist', url: 'https://example.com/' + id + '.mp3' }, ...extras });
    const choose = (kind, id = 'original') => w.dispatchEvent(new w.CustomEvent('react-chat-window:action', { detail: {
        message: messages.get('music-recommendation-' + id) || messages.get('proactive-recommendation-' + id),
        action: { action: messages.has('music-recommendation-' + id) ? 'music_recommendation_decision' : 'proactive_recommendation_decision', payload: { recommendationId: id, kind } }
    } }));
    return { w, events, musicChoices, plays, opens, messages, timers, offer, choose,
        offerContent: (id = 'original') => w.nekoRecommendations.offer({ id, state: 'pending', category: 'news', expires_at: 1900,
            content: { title: id, source: 'Example', url: 'https://example.com/' + id } }),
        message: (id = 'original') => messages.get('music-recommendation-' + id),
        storage: () => Array.from({ length: w.localStorage.length }, (_, i) => { const key = w.localStorage.key(i); return [key, w.localStorage.getItem(key)]; }),
        advance: async ms => { now += ms; for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn(); } await settle(); }
    };
}

test('v1 rejection survives a reload and resumes its exact event without playing', async t => {
    const offline = surface(t, { respond: async () => { throw Error('offline'); } });
    offline.offer(); offline.choose('skipped'); await settle();
    assert.deepEqual(offline.musicChoices, [{ lanlan_name: 'Neko', recommendation_id: 'original', decision: 'skipped' }]);
    assert.equal(offline.message().blocks[0].error, true);
    const stored = offline.storage();
    assert.equal(stored.length, 1); assert.ok(stored[0][0].startsWith(decisionPrefix));
    const original = offline.events[0].event;
    const online = surface(t, { storage: stored }); online.offer(); await settle();
    assert.deepEqual(online.events[0].event, original);
    assert.equal(online.message().blocks[0].state, 'skipped');
    assert.equal(online.storage().length, 0); assert.equal(online.plays.length, 0);
    assert.equal(online.musicChoices.length, 0, 'durable background retry does not repeat an independent card-choice report');
});

test('a failed independent music-choice report does not prevent saving or playing an accepted decision', async t => {
    const s = surface(t, { reportChoice: async () => { throw Error('independent report offline'); } });
    s.offer(); s.choose('accepted'); await settle();
    assert.deepEqual(s.musicChoices, [{ lanlan_name: 'Neko', recommendation_id: 'original', decision: 'accepted' }]);
    assert.equal(s.events.filter(item => item.event.kind === 'accepted').length, 1);
    assert.equal(s.plays.length, 1);
    assert.equal(s.storage().length, 0);
});

test('recovered v1 acceptance records only; a fresh explicit retry uses its original ID', async t => {
    const offline = surface(t, { respond: async () => { throw Error('offline'); } });
    offline.offer(); offline.choose('accepted'); await settle();
    const original = offline.events[0].event;
    const online = surface(t, { storage: offline.storage() }); online.offer(); await settle();
    assert.equal(online.plays.length, 0); assert.equal(online.message().blocks[0].error, true);
    online.w.dispatchEvent(new online.w.CustomEvent('react-chat-window:action', { detail: {
        message: online.message(), action: { action: 'music_recommendation_shown', payload: { recommendationId: 'original' } }
    } }));
    await online.advance(11000); assert.equal(online.plays.length, 0, 'restored acceptance must not re-arm autoplay');
    online.choose('accepted'); await settle();
    assert.deepEqual(online.events.filter(item => item.event.kind === 'accepted').map(item => item.event), [original, original]);
    assert.equal(online.plays.length, 1); assert.equal(online.storage().length, 0);
});

test('automatic acceptance reason and occurrence time survive background retry', async t => {
    const s = surface(t, { respond: async () => { throw Error('offline'); } }); s.offer();
    s.w.dispatchEvent(new s.w.CustomEvent('react-chat-window:action', { detail: {
        message: s.message(), action: { action: 'music_recommendation_shown', payload: { recommendationId: 'original' } }
    } }));
    await s.advance(10000);
    const original = s.events.find(item => item.event.kind === 'accepted').event;
    assert.equal(original.reason, 'countdown_elapsed');
    assert.equal(s.musicChoices.length, 0, 'automatic acceptance is not a manual music choice');
    const resumed = surface(t, { storage: s.storage() }); await settle();
    const retry = resumed.events.find(item => item.event.kind === 'accepted').event;
    assert.deepEqual(retry, original); assert.equal(resumed.plays.length, 0);
});

test('background content acceptance never opens an external URL and retains the exact manual retry', async t => {
    const offline = surface(t, { respond: async () => { throw Error('offline'); } });
    offline.offerContent(); offline.choose('accepted'); await settle();
    const original = offline.events[0].event;
    const online = surface(t, { storage: offline.storage(), respond: async event => ({ success: true, state: event.kind, action_grant: true }) });
    online.offerContent(); await settle(); assert.equal(online.opens.length, 0);
    online.choose('accepted'); await settle();
    assert.deepEqual(online.events.map(item => item.event), [original, original]);
    assert.deepEqual(online.opens, ['https://example.com/original']);
});

for (const category of ['music', 'news']) {
    test(`confirmed ${category} acceptance survives a second reload without upload or dispatch`, async t => {
        const offline = surface(t, { respond: async () => { throw Error('offline'); } });
        if (category === 'music') offline.offer(); else offline.offerContent();
        offline.choose('accepted'); await settle();
        const original = offline.events[0].event;
        const confirmed = surface(t, { storage: offline.storage() }); await settle();
        assert.equal(confirmed.storage().length, 1);
        assert.ok(confirmed.storage()[0][0].startsWith(dispatchRetryPrefix));
        const reloaded = surface(t, { storage: confirmed.storage(), respond: async event => ({ success: true,
            state: event.kind, playback_grant: category === 'music', action_grant: category === 'news' }) });
        if (category === 'music') reloaded.offer(); else reloaded.offerContent();
        await settle(); await reloaded.advance(11000);
        assert.equal(reloaded.events.length, 0, 'acknowledged retry metadata is not an upload queue');
        assert.equal(reloaded.plays.length + reloaded.opens.length, 0);
        reloaded.choose('accepted'); await settle();
        assert.deepEqual(reloaded.events.map(item => item.event), [original]);
        assert.equal(reloaded.plays.length + reloaded.opens.length, 1);
        assert.equal(reloaded.storage().length, 0, 'successful dispatch retires the retry hint');
    });
}

test('expired acknowledged retries are removed instead of recreating consent or upload', async t => {
    const event = { event_id: 'old-confirmed', kind: 'accepted', occurred_at: 1, reason: 'countdown_elapsed' };
    const s = surface(t, { storage: [[dispatchRetryPrefix + event.event_id, JSON.stringify({ id: 'original', event,
        character: 'Neko', expiresAt: 99999 })]] });
    await settle(); assert.equal(s.storage().length, 0); assert.equal(s.events.length, 0);
    assert.equal(s.plays.length + s.opens.length, 0);
});

test('an acknowledged retry that expires while its page stays open cannot authorize dispatch', async t => {
    const event = { event_id: 'open-page', kind: 'accepted', occurred_at: 99 };
    const s = surface(t, { storage: [[dispatchRetryPrefix + event.event_id, JSON.stringify({ id: 'original', event,
        character: 'Neko', expiresAt: 101000 })]] });
    s.offer(); await s.advance(1000); s.choose('accepted'); await settle();
    assert.equal(s.message().blocks[0].state, 'unavailable'); assert.equal(s.storage().length, 0);
    assert.equal(s.events.length, 0); assert.equal(s.plays.length + s.opens.length, 0);
});

test('acknowledged dispatch retry hints have a finite capacity and retain the original automatic reason', async t => {
    const storage = Array.from({ length: 260 }, (_, i) => [dispatchRetryPrefix + i, JSON.stringify({ id: 'hint-' + i,
        character: 'Neko', expiresAt: 110000 + i, event: { event_id: String(i), kind: 'accepted', occurred_at: 99,
            reason: 'countdown_elapsed' } })]);
    const s = surface(t, { storage }); await settle();
    assert.equal(s.storage().length, 256); assert.equal(s.events.length, 0);
    assert.equal(s.w.localStorage.getItem(dispatchRetryPrefix + '0'), null);
    s.offer('hint-259'); s.choose('accepted', 'hint-259'); await settle();
    assert.deepEqual(s.events[0].event, { event_id: '259', kind: 'accepted', occurred_at: 99, reason: 'countdown_elapsed' });
    assert.equal(s.plays.length, 1);
});

test('a background rejection cannot update a card belonging to a different current character', async t => {
    let release;
    const original = { event_id: 'old-role', kind: 'skipped', occurred_at: 1 };
    const s = surface(t, { storage: [[decisionPrefix + original.event_id, JSON.stringify({ id: 'original', event: original, decision: true, character: 'Neko' })]],
        respond: async event => { await new Promise(resolve => { release = resolve; }); return { success: true, state: event.kind }; } });
    s.offer(); await settle(); s.w.lanlan_config = { lanlan_name: 'Other' }; release(); await settle();
    assert.equal(s.message().blocks[0].state, 'pending'); assert.equal(s.storage().length, 0);
});

test('expired rejection remains an error; recovery cannot turn it into acceptance', async t => {
    const original = { event_id: 'expired', kind: 'skipped', occurred_at: 1 };
    const s = surface(t, { storage: [[decisionPrefix + 'expired', JSON.stringify({ id: 'original', event: original, decision: true, character: 'Neko' })]],
        respond: async () => ({ success: false, error_code: 'recommendation_expired' }) });
    s.offer(); await settle();
    assert.deepEqual(s.events[0].event, original); assert.equal(s.storage().length, 0);
    assert.notEqual(s.message().blocks[0].state, 'skipped'); assert.equal(s.plays.length, 0);
});

test('original account UUID is retained while another account can submit its own decision', async t => {
    const old = { event_id: 'account-a-click', kind: 'skipped', occurred_at: 1 };
    const s = surface(t, { character: 'Other', storage: [[decisionPrefix + old.event_id, JSON.stringify({ id: 'account-a-offer', event: old, decision: true, character: 'Neko' })]],
        respond: async (event, id) => id === 'account-a-offer' ? { success: false, error_code: 'recommendation_not_found' } : { success: true, state: event.kind } });
    s.offer('account-b-offer'); s.choose('skipped', 'account-b-offer'); await settle();
    assert.equal(s.message('account-b-offer').blocks[0].state, 'skipped');
    assert.equal(s.storage().length, 1); assert.deepEqual(JSON.parse(s.storage()[0][1]).event, old);
    assert.equal(s.events.filter(item => item.id === 'account-a-offer').every(item => item.event.event_id === old.event_id), true);
});

test('queue capacity refuses a new decision visibly before submitting an unretained event', async t => {
    const storage = Array.from({ length: 256 }, (_, i) => [decisionPrefix + i, JSON.stringify({ id: 'old-' + i, decision: true,
        character: 'Neko', event: { event_id: 'old-' + i, kind: 'skipped', occurred_at: i } })]);
    const s = surface(t, { storage, respond: async () => { throw Error('offline'); } });
    s.offer(); s.choose('skipped'); await settle();
    assert.equal(s.message().blocks[0].state, 'pending'); assert.equal(s.message().blocks[0].error, true);
    assert.equal(s.events.some(item => item.id === 'original'), false); assert.equal(s.storage().length, 256);
});

test('a v2 offer does not purge pending v1 business decisions', async t => {
    const old = { event_id: 'legacy-decision', kind: 'skipped', occurred_at: 1 };
    const s = surface(t, { storage: [[decisionPrefix + old.event_id, JSON.stringify({ id: 'old', event: old, decision: true, character: 'Neko' })]],
        respond: async () => { throw Error('offline'); } });
    s.offer('v2', { offer_context: { owner_key: 'a'.repeat(64), offer_id: 'v2' } }); await settle();
    assert.equal(s.storage().length, 1); assert.deepEqual(JSON.parse(s.storage()[0][1]).event, old);
});
