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

function surface(t, { storage = [], respond, reportChoice, dispatchClaim, character = 'Neko' } = {}) {
    const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost:48911/chat', runScripts: 'outside-only', pretendToBeVisual: true });
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
        if (url.endsWith('/dispatch')) return dispatchClaim ? await dispatchClaim() : { ok: true, json: async () => ({ success: true, dispatch_grant: true }) };
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
        offerContent: (id = 'original', extras = {}) => w.nekoRecommendations.offer({ id, state: 'pending', category: 'news', expires_at: 1900,
            content: { title: id, source: 'Example', url: 'https://example.com/' + id }, ...extras }),
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

for (const category of ['music', 'news']) test(`restored ${category} rejection cannot become automatic acceptance while its retry fails`, async t => {
    const event = { event_id: 'rejected-before-reload', kind: 'skipped', occurred_at: 99 };
    let failing = true;
    const s = surface(t, { storage: [[decisionPrefix + event.event_id, JSON.stringify({ id: 'original', event,
        decision: true, character: 'Neko', expiresAt: 1900000 })]], respond: async next => {
        if (next.kind === 'skipped' && failing) throw Error('rejection offline');
        return { success: true, state: next.kind, playback_grant: true, action_grant: true };
    } });
    if (category === 'music') s.offer(); else s.offerContent();
    await settle();
    const message = s.messages.get((category === 'music' ? 'music' : 'proactive') + '-recommendation-original');
    s.w.dispatchEvent(new s.w.CustomEvent('neko:chat-mirror', { detail: { type: 'update', messageId: message.id,
        patch: { blocks: [{ ...message.blocks[0], error: false, autoPlayAt: 110000 }] } } }));
    s.w.dispatchEvent(new s.w.CustomEvent('react-chat-window:action', { detail: { message,
        action: { action: category === 'music' ? 'music_recommendation_shown' : 'proactive_recommendation_shown', payload: { recommendationId: 'original' } } } }));
    await s.advance(10000);
    assert.equal(s.events.some(item => item.event.kind === 'accepted'), false);
    assert.equal(s.plays.length + s.opens.length, 0);
    failing = false; s.choose('skipped'); await settle();
    assert.deepEqual(s.events.filter(item => item.event.kind === 'skipped').at(-1).event, event, 'explicit refusal retries its exact original event');
    assert.equal(message.blocks[0].state, 'skipped');
});

for (const category of ['music', 'news']) test(`a restored ${category} refusal still permits a fresh explicit change of mind`, async t => {
    const event = { event_id: 'old-refusal', kind: 'skipped', occurred_at: 99 };
    const s = surface(t, { storage: [[decisionPrefix + event.event_id, JSON.stringify({ id: 'original', event,
        decision: true, character: 'Neko', expiresAt: 1900000 })]], respond: async next => {
        if (next.kind === 'skipped') throw Error('refusal retry offline');
        return { success: true, state: next.kind, playback_grant: true, action_grant: true };
    } });
    if (category === 'music') s.offer(); else s.offerContent();
    await settle(); s.choose('accepted'); await settle();
    const accepted = s.events.find(item => item.event.kind === 'accepted').event;
    assert.notEqual(accepted.event_id, event.event_id); assert.equal(accepted.reason, undefined);
    assert.equal(s.plays.length + s.opens.length, 1);
});

for (const target of ['music', 'content', 'browser']) test(`recovered acceptance expiring during ${target} request cannot start its action`, async t => {
    const event = { event_id: 'slow-restored-' + target, kind: 'accepted', occurred_at: 99 };
    let release;
    const s = surface(t, { storage: [[dispatchRetryPrefix + event.event_id, JSON.stringify({ id: 'original', event,
        character: 'Neko', expiresAt: 101000 })]], respond: () => new Promise(resolve => { release = resolve; }) });
    let popup;
    if (target === 'browser') {
        delete s.w.electronShell;
        s.w.open = () => (popup = { closed: false, location: { href: '' }, close() { this.closed = true; } });
    }
    if (target === 'music') s.offer(); else s.offerContent();
    s.choose('accepted'); await settle(); assert.ok(release);
    await s.advance(1000);
    release({ success: true, state: 'accepted', playback_grant: true, action_grant: true }); await settle();
    assert.equal(s.plays.length + s.opens.length, 0);
    assert.equal(s.messages.values().next().value.blocks[0].state, 'unavailable');
    assert.equal(s.w.localStorage.getItem(dispatchRetryPrefix + event.event_id), null);
    if (popup) { assert.equal(popup.closed, true); assert.equal(popup.location.href, ''); }
});

for (const category of ['music', 'news']) test(`recovered ${category} acceptance expiring during v2 dispatch claim cannot act`, async t => {
    const event = { event_id: 'slow-claim-' + category, kind: 'accepted', occurred_at: 99, context: { owner_key: 'a'.repeat(64) } };
    let release;
    const s = surface(t, { storage: [[dispatchRetryPrefix + event.event_id, JSON.stringify({ id: 'original', event,
        character: 'Neko', expiresAt: 101000 })]], respond: async next => ({ success: true, state: next.kind, playback_grant: true, action_grant: true }),
        dispatchClaim: () => new Promise(resolve => { release = resolve; }) });
    if (category === 'music') s.offer(); else s.offerContent();
    s.choose('accepted'); await settle(); assert.ok(release, 'the accepted request reached its dispatch claim');
    await s.advance(1000);
    release({ ok: true, json: async () => ({ success: true, dispatch_grant: true }) }); await settle();
    assert.equal(s.plays.length + s.opens.length, 0);
    assert.equal(s.messages.values().next().value.blocks[0].state, 'unavailable');
});

for (const stage of ['factory', 'delayed play', 'media wait', 'already playing']) test(`restored consent expiry respects the real player's ${stage} boundary`, async t => {
    const harness = fs.readFileSync(path.join(__dirname, 'music_player_close.test.cjs'), 'utf8');
    const { createSurface } = new Function('require', '__dirname', harness.slice(0, harness.indexOf('\nfunction pair(')) + '\nreturn { createSurface };')(require, __dirname);
    const s = createSurface(t, { deferInit: stage === 'factory', honorCancellation: true, mediaPending: ['media wait', 'already playing'].includes(stage) });
    const w = s.w, messages = new Map();
    const event = { event_id: 'slow-player-recovered', kind: 'accepted', occurred_at: 99 };
    const expiresAt = stage === 'factory' ? 101000 : stage === 'already playing' ? 100500 : 100050;
    w.localStorage.setItem(dispatchRetryPrefix + event.event_id, JSON.stringify({ id: 'original', event, character: 'Neko', expiresAt }));
    w.lanlan_config = { lanlan_name: 'Neko' };
    w.nekoLocalMutationSecurity = { getMutationHeaders: async () => ({}) };
    w.AbortSignal.timeout = () => undefined;
    w.reactChatWindowHost = { appendMessage: m => messages.set(m.id, m), updateMessage: (id, patch) => Object.assign(messages.get(id), patch) };
    w.fetch = async (url, init) => ({ ok: true, status: 200, json: async () => url.endsWith('/events')
        ? { success: true, state: JSON.parse(init.body).kind, playback_grant: true } : { success: true, domains: [] } });
    w.dispatchMusicPlayDetailed = (track, options) => w.sendMusicMessageDetailed(track, true, options);
    w.eval(source);
    w.nekoMusicRecommendations.offer({ id: 'original', state: 'pending', expires_at: 1900,
        track: { name: 'Original song', artist: 'Neko', url: 'https://music.163.com/test.mp3' } });
    w.dispatchEvent(new w.CustomEvent('react-chat-window:action', { detail: { message: messages.values().next().value,
        action: { action: 'music_recommendation_decision', payload: { recommendationId: 'original', kind: 'accepted' } } } }));
    await settle(); assert.ok(s.bar(), 'real player initialization was reached before expiry');
    if (stage === 'already playing') {
        await s.advance(101); assert.equal(s.players[0].audio.paused, false);
        await s.advance(1000); s.players[0].audio.dispatchEvent(new w.Event('loadedmetadata')); await settle();
        assert.equal(s.players[0].audio.paused, false, 'expiry does not revoke audio already started within consent');
        assert.equal(s.players[0].destroyed, false);
        return;
    }
    await s.advance(stage === 'factory' ? 1000 : 101);
    if (stage === 'factory') s.completeInit();
    await settle();
    assert.equal(s.players.some(player => !player.audio.paused), false, 'expired restored consent never starts audio');
    if (stage === 'factory') assert.equal(s.players.length, 0, 'the factory predicate rejects expired consent before construction');
    else assert.ok(s.players.every(player => player.destroyed), 'expired initialization releases its owned player');
    assert.equal(w.isMusicPending(), false, 'expired initialization releases pending ownership');
});
