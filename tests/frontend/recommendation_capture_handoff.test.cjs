const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const test = require('node:test');
const root = path.resolve(__dirname, '../..');
const { JSDOM } = createRequire(path.join(root, 'frontend/react-neko-chat/package.json'))('jsdom');
const source = fs.readFileSync(path.join(root, 'static/app/app-recommendations.js'), 'utf8');
const recId = '19f3bbca-a275-43d4-923d-1b2ceee91943';
const binding = { owner_key: 'a'.repeat(64), account_incarnation: 'account-a', clear_generation: 1,
    registration_id: 'device-a', registration_version: 1, binding_revision: 2,
    anchor_id: 'anchor', anchor_token: 'signed-anchor' };
function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (!value || typeof value !== 'object') return value;
    return Object.fromEntries(Object.keys(value).sort().filter(key => key !== 'time_proof').map(key => [key, canonical(value[key])]));
}
const hash = event => crypto.createHash('sha256').update(JSON.stringify(canonical(event))).digest('hex');
const ack = (event, status = 'durable') => ({ event_id: event.event_id, payload_hash: hash(event), status });
async function flush() { for (let i = 0; i < 70; i++) await Promise.resolve(); }
function surface(t, options = {}) {
    const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost:48911/chat', runScripts: 'outside-only' });
    t.after(() => dom.window.close());
    const w = dom.window, messages = new Map(), events = [], plays = [], dispatches = [];
    let currentBinding = { ...binding }, respond = options.respond, captureResponse = null;
    w.lanlan_config = { lanlan_name: 'Neko' };
    w.AbortSignal.timeout = () => undefined;
    w.nekoLocalMutationSecurity = { getMutationHeaders: async () => ({ 'X-CSRF-Token': 'test' }) };
    w.reactChatWindowHost = { appendMessage(m) { messages.set(m.id, m); }, updateMessage(id, patch) { Object.assign(messages.get(id), patch); } };
    w.fetch = async (url, init) => {
        const input = JSON.parse(init.body);
        let body;
        if (url.endsWith('/capture-context')) body = captureResponse ? await captureResponse() : { success: true, context: currentBinding };
        else if (url.endsWith('/dispatch')) { dispatches.push(input); body = { success: true, dispatch_grant: true }; }
        else {
            events.push(input);
            body = respond ? await respond(input, init) : { success: true, state: input.kind,
                playback_grant: input.kind === 'accepted', telemetry: ack(input) };
        }
        return { ok: body.success, status: body.http_status || (body.success ? 200 : 409), json: async () => body };
    };
    w.dispatchMusicPlayDetailed = async (_, opts) => { plays.push(opts); return { ok: true }; };
    if (options.beforeLoad) options.beforeLoad(w);
    w.eval(source);
    const offer = { id: recId, state: 'pending', expires_at: Date.now() / 1000 + 1800,
        track: { name: '猫の歌', artist: 'Artist', url: 'https://example.com/audio.mp3' },
        offer_context: { owner_key: binding.owner_key, offer_id: recId,
            origin_context: { ...binding, capture: { clock_session_id: 'origin-session', tick_ms: 0 },
                time_proof: { anchor_token: 'original-proof' } } } };
    w.nekoMusicRecommendations.offer(offer, 'turn');
    const choose = kind => w.dispatchEvent(new w.CustomEvent('react-chat-window:action', { detail: {
        message: messages.values().next().value,
        action: { action: 'music_recommendation_decision', payload: { recommendationId: recId, kind } }
    } }));
    return { w, events, messages, plays, dispatches, choose, setRespond(value) { respond = value; },
        setCaptureResponse(value) { captureResponse = value; },
        setBinding(value) { currentBinding = value; } };
}

test('v2 retains renderer evidence until an exact durable handoff, not merely business success', async t => {
    const s = surface(t, { respond: async event => ({ success: true, state: event.kind }) });
    await flush();
    s.w.nekoMusicRecommendations.recordPlayback(recId, 'paused', { playback_id: 'p', listened_seconds: 12 });
    await flush();
    assert.equal(s.w.localStorage.length, 1);
    assert.match(s.w.localStorage.key(0), /^neko\.recommendation-capture\.v2\./);
    const first = s.events[0];
    assert.equal(first.context.owner_key, binding.owner_key);
    assert.ok(Number.isSafeInteger(first.context.capture.tick_ms));
    assert.match(first.context.capture.clock_session_id, /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/);
    s.setRespond(async event => ({ success: true, state: event.kind, telemetry: ack(event) }));
    s.w.dispatchEvent(new s.w.Event('online')); await flush();
    assert.equal(s.w.localStorage.length, 0);
    assert.deepEqual(s.events[1], first);
});

test('first v2 offer retires dormant legacy evidence beyond the loaded queue cap', async t => {
    const s = surface(t, { beforeLoad(w) {
        for (let i = 0; i < 270; i++) w.localStorage.setItem('neko.music-recommendation-event.v1.' + i,
            JSON.stringify({ id: recId, event: { event_id: 'legacy-' + i, kind: 'paused', occurred_at: i } }));
        w.localStorage.setItem('unrelated-setting', 'keep');
    } });
    await flush();
    assert.equal(s.w.localStorage.getItem('unrelated-setting'), 'keep');
    assert.equal(Array.from({length:s.w.localStorage.length}, (_, i) => s.w.localStorage.key(i))
        .filter(key => key.startsWith('neko.music-recommendation-event.v1.')).length, 0);
});

test('proof attachments are excluded recursively from the immutable handoff hash', async t => {
    const s = surface(t, { respond: async event => {
        const equivalent = JSON.parse(JSON.stringify(event));
        equivalent.context.time_proof = { anchor_token: 'refreshed-proof' };
        equivalent.offer_context.origin_context.time_proof = { anchor_token: 'different-independent-proof' };
        equivalent.measurement.origin_context.time_proof = { anchor_token: 'another-proof' };
        return { success: true, telemetry: ack(equivalent) };
    } });
    await flush();
    const original = s.w.nekoFeedbackCapture.capture();
    s.w.nekoMusicRecommendations.recordPlayback(recId, 'measurement', { playback_id: 'p',
        measurement: { version: 'playback_elapsed_observed_v2', epoch_id: 'meter', range_start_ms: 0,
            range_end_ms: 1000, origin_context: original } });
    await flush();
    assert.equal(s.w.localStorage.length, 0);
    assert.equal(s.messages.values().next().value.blocks[0].state, 'pending', 'measurement must not change normal playback/card state');
});

test('handoff canonical header preserves exact UTF8 and JavaScript exponent spelling', async t => {
    const s = surface(t, { respond: async (event, init) => {
        const bytes = Buffer.from(init.headers['X-NEKO-Feedback-Canonical'], 'base64');
        const canonicalText = bytes.toString('utf8');
        assert.equal(canonicalText, JSON.stringify(canonical(event)));
        assert.match(canonicalText, /1e-7/);
        assert.match(canonicalText, /猫の歌😀/);
        assert.ok(!canonicalText.includes('time_proof'));
        return { success: true, telemetry: { event_id: event.event_id,
            payload_hash: crypto.createHash('sha256').update(bytes).digest('hex'), status: 'durable' } };
    } });
    await flush();
    s.w.nekoMusicRecommendations.recordPlayback(recId, 'paused', { playback_id:'p',
        position_seconds: 1e-7, reason: '猫の歌😀' });
    await flush();
    assert.equal(s.events.length, 1);
    assert.equal(s.w.localStorage.length, 0);
});

test('binding-changed receipt refreshes future captures without rewriting the retired fact or dispatching again', async t => {
    const s = surface(t); await flush();
    s.choose('accepted'); await flush();
    assert.equal(s.plays.length, 1);
    s.setBinding({ ...binding, clear_generation: 2, binding_revision: 3 });
    s.setRespond(async event => ({ success: true, telemetry: { ...ack(event, 'discarded'), reason:'binding_changed' } }));
    s.w.nekoMusicRecommendations.recordPlayback(recId, 'paused', {playback_id:'p'}); await flush();
    const retired = s.events.findLast(event => event.kind === 'paused');
    assert.equal(retired.context.clear_generation, 1);
    assert.equal(s.w.nekoFeedbackCapture.capture().clear_generation, 2);
    assert.equal(retired.context.clear_generation, 1);
    assert.equal(s.plays.length, 1);
});

for (const failure of ['401', 'network']) test(`capture refresh ${failure} revokes cached binding until a successful refresh`, async t => {
    const s = surface(t); await flush();
    assert.equal(s.w.nekoFeedbackCapture.capture().clear_generation, 1);
    s.setCaptureResponse(async () => {
        if (failure === 'network') throw new TypeError('synthetic network failure');
        return {success:false,http_status:401};
    });
    s.w.dispatchEvent(new s.w.Event('online')); await flush();
    assert.equal(s.w.nekoFeedbackCapture.capture().owner_key, undefined);
    s.w.nekoMusicRecommendations.recordPlayback(recId, 'paused', {playback_id:'p'}); await flush();
    const original = s.events.findLast(event => event.kind === 'paused');
    assert.equal(original.context.owner_key, undefined, 'new observations remain held, never get the invalidated binding');
    s.setCaptureResponse(null);
    await s.w.nekoFeedbackCapture.refresh();
    assert.equal(s.w.nekoFeedbackCapture.capture().owner_key, binding.owner_key);
    assert.equal(original.context.owner_key, undefined, 'recovery cannot rebind the held original fact');
});

test('a held new measurement recovers capture after a short 503 without an online or visibility event', async t => {
    const s = surface(t); await flush();
    s.setCaptureResponse(async () => ({success:false,http_status:503}));
    await s.w.nekoFeedbackCapture.refresh();
    assert.equal(s.w.nekoFeedbackCapture.capture().owner_key, undefined);
    s.setCaptureResponse(null);
    s.setRespond(async event => ({success:true,telemetry:{...ack(event),reason:'binding_unavailable'}}));
    s.w.nekoMusicRecommendations.recordPlayback(recId, 'measurement', {playback_id:'p',
        measurement:{version:'playback_elapsed_observed_v2',epoch_id:'held',range_start_ms:0,range_end_ms:15000,
            origin_context:s.w.nekoFeedbackCapture.capture()}});
    await flush();
    const held = s.events[0];
    assert.equal(held.context.owner_key, undefined);
    assert.equal(held.measurement.origin_context.owner_key, undefined);
    assert.equal(s.w.nekoFeedbackCapture.capture().owner_key, binding.owner_key);
    assert.equal(held.context.owner_key, undefined, 'automatic recovery only qualifies future observations');
});

for (const invalid of ['wrong_hash', 'wrong_id', 'retry']) {
    test(`v2 ${invalid} receipt cannot erase the original renderer record`, async t => {
        const s = surface(t, { respond: async event => {
            const receipt = ack(event);
            if (invalid === 'wrong_hash') receipt.payload_hash = '0'.repeat(64);
            if (invalid === 'wrong_id') receipt.event_id = 'other';
            if (invalid === 'retry') receipt.status = 'retry';
            return { success: true, telemetry: receipt };
        } });
        await flush();
        s.w.nekoMusicRecommendations.recordPlayback(recId, 'paused', { playback_id: 'p' }); await flush();
        assert.equal(s.w.localStorage.length, 1);
    });
}

test('a durably discarded original capture is a terminal handoff, without claiming cloud storage', async t => {
    const s = surface(t, { respond: async event => ({ success: true, telemetry: ack(event, 'discarded') }) });
    await flush();
    s.w.nekoMusicRecommendations.recordPlayback(recId, 'paused', { playback_id: 'p' }); await flush();
    assert.equal(s.w.localStorage.length, 0);
});

for (const direct of [false, true]) for (const receipt of ['exact', 'wrong_hash', 'wrong_id', 'missing']) {
    test(`${direct ? 'decision' : 'queued'} conflict only retires exact discarded telemetry (${receipt})`, async t => {
        const s = surface(t, { respond: async event => {
            const telemetry = ack(event, 'discarded');
            if (receipt === 'wrong_hash') telemetry.payload_hash = '0'.repeat(64);
            if (receipt === 'wrong_id') telemetry.event_id = 'another-action';
            return { success:false, http_status:409, error_code:'recommendation_decision_conflict',
                ...(receipt === 'missing' ? {} : {telemetry}) };
        } });
        await flush();
        if (direct) s.choose('accepted');
        else s.w.nekoMusicRecommendations.recordPlayback(recId, 'accepted');
        await flush();
        assert.equal(s.w.localStorage.length, receipt === 'exact' ? 0 : 1);
        assert.equal(s.plays.length, 0);
        assert.equal(s.dispatches.length, 0);
    });
}

test('retrying telemetry from a successful decision never issues another dispatch', async t => {
    const s = surface(t, { respond: async event => ({ success: true, state: 'accepted', playback_grant: true,
        telemetry: ack(event, 'retry') }) });
    await flush(); s.choose('accepted'); await flush();
    assert.equal(s.plays.length, 1);
    assert.equal(s.dispatches.length, 1);
    assert.equal(s.w.localStorage.length, 1);
    s.setRespond(async event => ({ success: true, state: 'accepted', playback_grant: true, telemetry: ack(event) }));
    s.w.dispatchEvent(new s.w.Event('online')); await flush();
    assert.equal(s.w.localStorage.length, 0);
    assert.equal(s.dispatches.length, 1);
    assert.equal(s.plays.length, 1);
});

test('refreshing control never rewrites already captured owner, generation, or range', async t => {
    const s = surface(t, { respond: async () => { throw new Error('offline'); } });
    await flush();
    s.w.nekoMusicRecommendations.recordPlayback(recId, 'paused', { playback_id: 'p' }); await flush();
    const original = JSON.parse(s.w.localStorage.getItem(s.w.localStorage.key(0)));
    s.setBinding({ ...binding, clear_generation: 2, binding_revision: 3 });
    await s.w.nekoFeedbackCapture.refresh();
    const after = JSON.parse(s.w.localStorage.getItem(s.w.localStorage.key(0)));
    assert.deepEqual(after, original);
    assert.equal(s.w.nekoFeedbackCapture.capture().clear_generation, 2);
});
