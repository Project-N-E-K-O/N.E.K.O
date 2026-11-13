const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const code = fs.readFileSync('static/avatar/avatar-listening-context.js', 'utf8');

function fixture() {
    const peers = [], queue = [];
    function peer(pet) {
        const handlers = {};
        let state = { audible: true, playbackId: 'a', track: { name: 'Song A', artist: 'Artist' } };
        const doc = { visibilityState: 'visible' };
        const resource = { active: true, observedFrame: { at: Date.now(), weight: 1,
            parameters: { Param76: 1, ParamMouthForm: -1, ParamAngleZ: -14 } },
            performance: { snapshot: () => ({ visibleCue: 'tears' }) } };
        const mood = { playbackId: 'a', state: 'ready', result: { track_id: 'track:a', category: 'sad',
            confidence: 0.78, source: 'lyrics_text_model', classifier_version: 'v1' } };
        const listening = { source: 'music', playbackId: 'a', expression: 'expressions/listening-sad.exp3.json' };
        const w = { crypto: require('node:crypto').webcrypto, innerWidth: 1000, innerHeight: 1000,
            setTimeout, clearTimeout, getMusicPlaybackSnapshot: () => state,
            nekoMusicMood: { sync() {}, snapshot: () => mood }, nekoMusicListening: { snapshot: () => listening },
            addEventListener: (key, fn) => (handlers[key] ||= []).push(fn),
            __nekoBroadcastChatMirror: data => queue.push({ from: p, data }) };
        if (pet) w.live2dManager = { currentModel: { worldAlpha: 1 }, modelName: 'yui-sister',
            _manualExpressionResource: resource, getHeadScreenRectInfo: () => ({ rect: { left: 50, top: 50, width: 50, height: 50 } }) };
        const p = { w, doc, resource, mood, listening, emit: (name, detail) => (handlers[name] || []).forEach(fn => fn({ detail })),
            change: patch => { state = { ...state, ...patch }; p.emit('neko-music-playback-change'); } };
        peers.push(p);
        vm.runInNewContext(code, { window: w, document: doc, Date, Map, WeakMap });
        return p;
    }
    return { peer, queue, deliver() { while (queue.length) { const { from, data } = queue.shift();
        for (const p of peers) if (p !== from) p.emit('neko:listening-context', data); } } };
}

test('cross-window one-shot snapshot records rendered pose before chat takes over', async () => {
    const h = fixture(), pet = h.peer(true), chat = h.peer(false);
    const promise = chat.w.nekoListeningContext.capture('request'); h.deliver();
    const snap = await promise;
    pet.listening.source = 'chat'; pet.resource.active = false;
    assert.equal(snap.request_id, 'request');
    assert.deepEqual(Array.from(snap.facts), ['sad_face', 'head_tilt', 'hand_near_mouth', 'tears']);
    assert.equal(chat.w.nekoListeningContext.forMessage(snap), snap);
    assert.equal(h.queue.length, 0);
});
for (const reason of ['hidden', 'not-rendered', 'fade', 'stale', 'chat', 'inactive']) {
    test(`does not invent visible actions for ${reason}`, async () => {
        const p = fixture().peer(true);
        if (reason === 'hidden') p.doc.visibilityState = 'hidden';
        if (reason === 'not-rendered') p.resource.observedFrame = null;
        if (reason === 'fade') p.resource.observedFrame.weight = .2;
        if (reason === 'stale') p.resource.observedFrame.at -= 1000;
        if (reason === 'chat') p.listening.source = 'chat';
        if (reason === 'inactive') p.resource.active = false;
        const snap = await p.w.nekoListeningContext.capture('r');
        assert.equal(snap.expression, null); assert.equal(snap.facts.length, 0);
        assert.equal(snap.mood.category, 'sad');
    });
}
test('sad classification alone cannot claim tears; plain profiles can report their actual smile', async () => {
    const p = fixture().peer(true); p.resource.performance = null;
    p.resource.observedFrame.parameters = { ParamMouthForm: .8 };
    const snap = await p.w.nekoListeningContext.capture('r');
    assert.deepEqual(Array.from(snap.facts), ['smile']);
    p.mood.playbackId = 'old';
    assert.equal((await p.w.nekoListeningContext.capture('r2')).mood, null);
});
for (const event of ['pause', 'ended', 'track', 'pause-resume']) {
    test(`invalidates both pending and captured state on ${event}`, async () => {
        const h = fixture(), pet = h.peer(true), chat = h.peer(false);
        const first = chat.w.nekoListeningContext.capture('first'); h.deliver(); const snap = await first;
        const second = chat.w.nekoListeningContext.capture('second');
        chat.change(event === 'track' ? { playbackId: 'b' } : { audible: false, ended: event === 'ended' });
        if (event === 'pause-resume') chat.change({ audible: true });
        h.deliver();
        assert.equal(await second, null);
        assert.equal(chat.w.nekoListeningContext.forMessage(snap), null);
        assert.equal(pet.listening.source, 'music');
    });
}
test('missing pet times out without replaying a prior context or triggering any reply', async () => {
    const h = fixture(), chat = h.peer(false);
    assert.equal(await chat.w.nekoListeningContext.capture('r'), null);
    assert.deepEqual(h.queue.map(x => x.data.type), ['listening_context_request']);
});
test('response for another request cannot attach; new model with no frame cannot reuse a pose', async () => {
    const h = fixture(), pet = h.peer(true), chat = h.peer(false);
    const p = chat.w.nekoListeningContext.capture('r');
    pet.w.live2dManager._manualExpressionResource = null;
    h.deliver(); const snap = await p;
    assert.equal(snap.expression, null); assert.equal(snap.facts.length, 0);
});
