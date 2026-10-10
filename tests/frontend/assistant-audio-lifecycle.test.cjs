'use strict';
// The real queue/parser wrapper runs against a stateful decoder double here.
// Codec fidelity is separately checked with the vendored WASM in Chromium.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const defer = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const ticks = async () => { for (let i = 0; i < 100; i++) await Promise.resolve(); };
const page = (value, bos = false) => { const b = new Uint8Array(29); b.set([79, 103, 103, 83]); b[5] = bos ? 2 : 0; b[26] = 1; b[27] = 1; b[28] = value; return b; };
const join = (...parts) => Uint8Array.from(parts.flatMap(p => Array.from(p)));
const pcm = r => r ? Array.from(r.float32Data) : [];

function harness(withPlayback = false) {
    const timers = new Map(), listeners = new Map(), instances = [], buffers = [], logs = [];
    let nextTimer = 1;
    const control = { ready: null, decode: null, flush: null, rejectDecode: false, rejectFlush: false, decoderErrors: false };
    const result = values => ({ channelData: [Float32Array.from(values)], sampleRate: 48000, errors: [] });
    class Decoder {
        constructor() { this.ready = control.ready ? control.ready.promise : Promise.resolve(); this.held = []; this.busy = 0; this.freed = false; instances.push(this); }
        async decode(bytes) {
            this.busy++;
            try {
                if (control.decode) await control.decode.promise;
                if (control.rejectDecode) throw new Error('decode rejection');
                const out = this.held; this.held = [bytes[bytes.length - 1]];
                const decoded = result(out);
                if (control.decoderErrors) decoded.errors = ['invalid packet'];
                return decoded;
            } finally { this.busy--; }
        }
        async flush() {
            this.busy++;
            try {
                if (control.flush) await control.flush.promise;
                if (control.rejectFlush) throw new Error('flush rejection');
                const out = this.held; this.held = []; return result(out);
            } finally { this.busy--; }
        }
        async reset() { this.held = []; }
        async free() { assert.equal(this.busy, 0, 'free raced a live WASM owner'); this.freed = true; }
    }
    class Event { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } }
    const win = {
        'ogg-opus-decoder': { OggOpusDecoder: Decoder },
        t: key => 'translated:' + key,
        setTimeout(fn, ms) { const id = nextTimer++; timers.set(id, { fn, ms }); return id; },
        clearTimeout(id) { timers.delete(id); },
        addEventListener(type, handler) { if (!listeners.has(type)) listeners.set(type, []); listeners.get(type).push(handler); },
        removeEventListener() {},
        dispatchEvent(ev) { for (const handler of listeners.get(ev.type) || []) handler(ev); return true; },
        requestAnimationFrame() { return 0; }, cancelAnimationFrame() {},
    };
    const box = { window: win, console: { log() {}, warn(...args) { logs.push(args); }, error(...args) { logs.push(args); } },
        document: { getElementById() { return null; }, addEventListener() {}, removeEventListener() {} },
        localStorage: { getItem() { return null; }, setItem() {} }, navigator: { userAgent: 'node-test' },
        CustomEvent: Event, setTimeout: win.setTimeout, clearTimeout: win.clearTimeout };
    vm.createContext(box);
    for (const file of ['static/app/app-state.js', 'static/ogg-opus-decoder-wrapper.js', ...(withPlayback ? ['static/app/app-audio-playback.js'] : [])]) {
        vm.runInContext(fs.readFileSync(path.join(root, file), 'utf8'), box, { filename: path.join(root, file) });
    }
    const S = win.appState, A = win.appAudioPlayback;
    if (withPlayback) {
        S.audioPlayerContext = {
            currentTime: 1, state: 'running', sampleRate: 48000, destination: {},
            createBuffer(_channels, size, sampleRate) { const b = { duration: size / sampleRate, sampleRate, copyToChannel(data) { buffers.push(Array.from(data)); } }; return b; },
            createBufferSource() { return { connect() {}, disconnect() {}, start() {}, stop() {} }; },
            createGain() { return { gain: { value: 1 }, connect() {}, disconnect() {} }; },
        };
    }
    const fire = ms => { for (const [id, timer] of Array.from(timers)) if (timer.ms === ms) { timers.delete(id); timer.fn(); } };
    const emit = (type, turnId) => win.dispatchEvent(new Event(type, { detail: { turnId } }));
    const start = (turnId, sid = turnId) => { S.assistantTurnId = turnId; S.currentPlayingSpeechId = sid; emit('neko-assistant-turn-start', turnId); };
    const enqueue = (sid, turn, bytes, gate = null) => {
        A.rememberAssistantAudioSpeechTurn(sid, turn);
        S.pendingAudioChunkMetaQueue.push({ speechId: sid, turnId: turn, epoch: S.incomingAudioEpoch, receivedAt: Date.now(), shouldSkip: false, playbackGain: .7 });
        A.enqueueIncomingAudioBlob({ arrayBuffer: async () => { if (gate) await gate.promise; return Uint8Array.from(bytes).buffer; } });
    };
    const endSources = () => { fire(25); for (const source of S.scheduledSources.slice()) source.onended(); };
    return { box, win, S, A, control, instances, buffers, logs, fire, start, enqueue, emit, endSources };
}

const reports = [];
async function check(name, fn) { await fn(); reports.push(name); }
(async () => {
    await check('stream-boundaries-and-fragmentation', async () => {
        const h = harness(), bytes = join(page(1, true), page(2), page(3, true), page(4)), chunks = [bytes.slice(0, 3), bytes.slice(3, 39), bytes.slice(39)];
        let out = [];
        for (const b of chunks) out.push(...pcm(await h.box.decodeOggOpusChunk(b, { speechId: 's' })));
        out.push(...pcm(await h.box.flushOggOpusDecoder({ epoch: 0, speechId: 's' })));
        assert.deepEqual(out, [1, 2, 3, 4]);
        assert.equal(await h.box.flushOggOpusDecoder({ epoch: 0, speechId: 's' }), null);
    });
    await check('speech-owner-change-discards-tail', async () => {
        const h = harness(); await h.box.decodeOggOpusChunk(page(9, true), { speechId: 'old' });
        assert.deepEqual(pcm(await h.box.decodeOggOpusChunk(join(page(1, true), page(2)), { speechId: 'new' })), [1]);
    });
    await check('cancel-during-init-cannot-publish-old-cache', async () => {
        const h = harness(), gate = defer(); h.control.ready = gate;
        const old = h.box.decodeOggOpusChunk(page(9, true), { speechId: 'old' }); await ticks();
        h.S.incomingAudioEpoch++; await h.box.resetOggOpusDecoder(); h.control.ready = null;
        const current = await h.box.getOggOpusDecoder(); gate.resolve(); assert.equal(await old, null); await ticks();
        assert.equal(await h.box.getOggOpusDecoder(), current); assert.equal(h.instances[0].freed, true);
    });
    await check('cancel-during-decode-keeps-new-generation', async () => {
        const h = harness(), gate = defer(); h.control.decode = gate;
        const old = h.box.decodeOggOpusChunk(page(9, true), { speechId: 'old' }); await ticks();
        const queued = h.box.decodeOggOpusChunk(page(8), { speechId: 'old' });
        h.S.incomingAudioEpoch++; h.box.invalidateOggOpusDecoder(); h.control.decode = null;
        assert.deepEqual(pcm(await h.box.decodeOggOpusChunk(join(page(1, true), page(2)), { speechId: 'new' })), [1]);
        gate.resolve(); assert.equal(await old, null); assert.equal(await queued, null); await ticks();
        assert.equal(h.instances[0].freed, true); assert.equal(h.instances[1].freed, false);
        assert.deepEqual(pcm(await h.box.flushOggOpusDecoder({ epoch: 1, speechId: 'new' })), [2]);
    });
    await check('operation-timeout-releases-queue-and-fences-late-result', async () => {
        const h = harness(), gate = defer(); h.control.decode = gate;
        const old = h.box.decodeOggOpusChunk(page(9, true), { speechId: 'old' }); const rejected = assert.rejects(old, { name: 'TimeoutError' }); await ticks();
        h.fire(5000); await rejected; h.control.decode = null;
        assert.deepEqual(pcm(await h.box.decodeOggOpusChunk(join(page(1, true), page(2)), { speechId: 'new' })), [1]);
        const current = await h.box.getOggOpusDecoder(); gate.resolve(); await ticks();
        assert.equal(await h.box.getOggOpusDecoder(), current); assert.equal(current.freed, false);
    });
    await check('flush-timeout-does-not-block-next-speech', async () => {
        const h = harness(); await h.box.decodeOggOpusChunk(page(9, true), { speechId: 'old' }); const gate = defer(); h.control.flush = gate;
        const old = h.box.flushOggOpusDecoder({ epoch: 0, speechId: 'old' }); const rejected = assert.rejects(old, { name: 'TimeoutError' }); await ticks(); h.fire(5000); await rejected;
        h.control.flush = null; assert.deepEqual(pcm(await h.box.decodeOggOpusChunk(join(page(1, true), page(2)), { speechId: 'new' })), [1]); gate.resolve(); await ticks();
    });
    await check('decode-rejection-and-invalid-packet-recover', async () => {
        for (const flag of ['rejectDecode', 'decoderErrors']) {
            const h = harness(); h.control[flag] = true; await assert.rejects(h.box.decodeOggOpusChunk(page(9, true), { speechId: 'old' }));
            h.control[flag] = false; assert.deepEqual(pcm(await h.box.decodeOggOpusChunk(join(page(1, true), page(2)), { speechId: 'new' })), [1]);
        }
    });
    await check('init-rejection-and-missing-library-retry', async () => {
        const h = harness(), rejected = defer(); h.control.ready = rejected;
        const init = h.box.getOggOpusDecoder(); rejected.reject(new Error('init rejection')); assert.equal(await init, null); await ticks();
        h.control.ready = null; assert.ok(await h.box.getOggOpusDecoder());
        h.box.invalidateOggOpusDecoder(); const lib = h.win['ogg-opus-decoder']; h.win['ogg-opus-decoder'] = null;
        assert.equal(await h.box.getOggOpusDecoder(), null); h.win['ogg-opus-decoder'] = lib; assert.ok(await h.box.getOggOpusDecoder());
    });
    await check('bad-page-and-incomplete-final-page-retire-parser', async () => {
        const h = harness(); await assert.rejects(h.box.decodeOggOpusChunk(new Uint8Array(30), { speechId: 'bad' }), /Invalid Ogg/);
        await h.box.decodeOggOpusChunk(join(page(1, true), page(2).slice(0, 10)), { speechId: 's' });
        await assert.rejects(h.box.flushOggOpusDecoder({ epoch: 0, speechId: 's' }), /inside an Ogg page/);
        assert.deepEqual(pcm(await h.box.decodeOggOpusChunk(join(page(3, true), page(4)), { speechId: 'new' })), [3]);
    });
    await check('both-clear-entries-invalidate-inflight-blob', async () => {
        for (const method of ['clearAudioQueue', 'clearAudioQueueWithoutDecoderReset']) {
            const h = harness(true), gate = defer(); h.start('old'); h.S.assistantSpeechActiveTurnId = 'old'; h.enqueue('old', 'old', join(page(9, true), page(8)), gate); await ticks();
            let cancelEpoch; h.win.addEventListener('neko-assistant-speech-cancel', () => { cancelEpoch = h.S.incomingAudioEpoch; });
            await h.A[method](); assert.equal(cancelEpoch, 1);
            h.start('new'); h.enqueue('new', 'new', join(page(1, true), page(2))); gate.resolve(); await ticks(); h.A.noteAssistantAudioStreamClosed('new'); await ticks();
            assert.deepEqual(h.buffers.flat(), [1, 2]);
        }
    });
    await check('normal-done-delivers-tail-before-settlement', async () => {
        const h = harness(true); h.start('t', 's'); h.enqueue('s', 't', join(page(1, true), page(2)));
        h.emit('neko-assistant-turn-end', 't'); h.A.noteAssistantAudioStreamClosed('s'); assert.notEqual(h.S.assistantTurnSettledId, 't'); await ticks();
        assert.deepEqual(h.buffers.flat(), [1, 2]); assert.notEqual(h.S.assistantTurnSettledId, 't');
        h.endSources(); assert.equal(h.S.assistantTurnSettledId, 't'); assert.equal(h.S.isPlaying, false);
    });
    await check('cancel-wakes-receive-queue-before-old-owner-finishes', async () => {
        for (const method of ['clearAudioQueue', 'clearAudioQueueWithoutDecoderReset']) {
            for (const stage of ['ready', 'decode', 'flush']) {
                for (const rejectsLate of [false, true]) {
                    const h = harness(true), gate = defer();
                    h.start('old');
                    if (stage !== 'flush') h.control[stage] = gate;
                    h.enqueue('old', 'old', join(page(9, true), page(8))); await ticks();
                    if (stage === 'flush') {
                        h.control.flush = gate; h.A.noteAssistantAudioStreamClosed('old'); await ticks();
                    }
                    assert.equal(h.S.isProcessingIncomingAudioBlob, true);
                    const oldOwner = h.instances[0];
                    await h.A[method](); h.control[stage] = null; h.buffers.length = 0;
                    h.start('new'); h.enqueue('new', 'new', join(page(1, true), page(2)));
                    h.A.noteAssistantAudioStreamClosed('new'); await ticks();
                    // No clock advancement and the old physical barrier stays shut.
                    assert.deepEqual(h.buffers.flat(), [1, 2]);
                    assert.equal(h.S.isProcessingIncomingAudioBlob, false);
                    assert.equal(h.S.assistantAudioStreamClosedTurnId, 'new');
                    assert.equal(oldOwner.freed, false);
                    if (rejectsLate) gate.reject(new Error('late cancelled operation rejection'));
                    else gate.resolve();
                    await ticks();
                    assert.equal(oldOwner.freed, true);
                    assert.equal(h.instances[1].freed, false);
                    assert.deepEqual(h.buffers.flat(), [1, 2]);
                    assert.equal(h.S.assistantAudioStreamClosedTurnId, 'new');
                    assert.equal(h.S.isProcessingIncomingAudioBlob, false);
                }
            }
        }
    });
    await check('tail-only-stream-waits-for-close-in-both-end-orders', async () => {
        for (const endInflight of [true, false]) {
            for (const missingDone of [true, false]) {
                const h = harness(true); h.start('short'); h.enqueue('short', 'short', page(7, true));
                if (!endInflight) await ticks();
                h.emit('neko-assistant-turn-end', 'short'); await ticks();
                assert.deepEqual(h.buffers, []); assert.notEqual(h.S.assistantTurnSettledId, 'short');
                if (missingDone) h.fire(700);
                else assert.equal(h.A.noteAssistantAudioStreamClosed('short'), true);
                await ticks(); assert.deepEqual(h.buffers.flat(), [7]); assert.notEqual(h.S.assistantTurnSettledId, 'short');
                h.endSources(); assert.equal(h.S.assistantTurnSettledId, 'short'); assert.equal(h.S.isPlaying, false);
            }
        }
    });
    await check('header-only-close-and-cancel-do-not-leave-stale-turn-headers', async () => {
        const h = harness(true); h.start('header'); h.A.rememberAssistantAudioSpeechTurn('header', 'header');
        h.emit('neko-assistant-turn-end', 'header'); h.fire(700); await ticks();
        assert.equal(h.S.assistantTurnSettledId, 'header'); assert.deepEqual(h.buffers, []);
        // Reusing a turn id in this fixture exposes a stale local header map.
        h.start('header'); h.emit('neko-assistant-turn-end', 'header'); h.fire(700); await ticks();
        assert.equal(h.S.assistantTurnCompletedId, 'header'); assert.equal(h.S.assistantTurnSettledId, null);
        h.A.clearAudioQueueWithoutDecoderReset(); h.fire(700); await ticks();
        assert.equal(h.S.assistantTurnCompletedId, null); assert.deepEqual(h.buffers, []);
    });
    await check('missing-done-delivers-tail-and-waits-for-playback', async () => {
        const h = harness(true); h.start('t', 's'); h.enqueue('s', 't', join(page(1, true), page(2))); h.emit('neko-assistant-turn-end', 't'); await ticks();
        h.endSources(); h.fire(700); await ticks(); assert.deepEqual(h.buffers.flat(), [1, 2]); assert.notEqual(h.S.assistantTurnSettledId, 't');
        h.endSources(); assert.equal(h.S.assistantTurnSettledId, 't');
    });
    await check('cancel-during-fallback-flush-drops-old-tail', async () => {
        const h = harness(true); h.start('old'); h.enqueue('old', 'old', join(page(9, true), page(8))); h.emit('neko-assistant-turn-end', 'old'); await ticks(); h.endSources();
        const gate = defer(); h.control.flush = gate; h.fire(700); await ticks(); h.A.clearAudioQueueWithoutDecoderReset(); h.buffers.length = 0;
        h.control.flush = null; h.start('new'); h.enqueue('new', 'new', join(page(1, true), page(2))); gate.resolve(); await ticks(); h.A.noteAssistantAudioStreamClosed('new'); await ticks();
        assert.deepEqual(h.buffers.flat(), [1, 2]); assert.notEqual(h.S.assistantAudioStreamClosedTurnId, 'old');
    });
    await check('fallback-close-cannot-overwrite-reopened-turn', async () => {
        const h = harness(true); h.start('t', 'old'); h.enqueue('old', 't', join(page(1, true), page(2))); h.emit('neko-assistant-turn-end', 't'); await ticks(); h.endSources();
        const gate = defer(); h.control.flush = gate; h.fire(700); await ticks();
        h.enqueue('new', 't', join(page(3, true), page(4))); h.control.flush = null; gate.resolve(); await ticks();
        assert.notEqual(h.S.assistantAudioStreamClosedTurnId, 't'); h.A.noteAssistantAudioStreamClosed('new'); await ticks(); assert.deepEqual(h.buffers.flat(), [1, 2, 3, 4]);
    });
    await check('old-done-duplicate-done-and-pcm-are-isolated', async () => {
        const h = harness(true); h.start('t', 'old'); h.enqueue('old', 't', join(page(1, true), page(2))); h.A.noteAssistantAudioStreamClosed('old');
        h.enqueue('new', 't', join(page(3, true), page(4))); await ticks(); h.A.noteAssistantAudioStreamClosed('old'); await ticks(); assert.notEqual(h.S.assistantAudioStreamClosedTurnId, 't');
        h.A.noteAssistantAudioStreamClosed('new'); await ticks(); h.A.noteAssistantAudioStreamClosed('new'); await ticks(); assert.deepEqual(h.buffers.flat(), [1, 2, 3, 4]);
        await h.A.clearAudioQueue(); h.buffers.length = 0; h.start('pcm'); h.enqueue('pcm', 'pcm', new Uint8Array(new Int16Array([8192, -8192]).buffer)); h.A.noteAssistantAudioStreamClosed('pcm'); await ticks(); assert.deepEqual(h.buffers.flat(), [.25, -.25]);
    });
    await check('flush-rejection-releases-close-and-next-speech', async () => {
        const h = harness(true); h.start('old'); h.enqueue('old', 'old', join(page(9, true), page(8))); await ticks();
        h.control.rejectFlush = true; h.emit('neko-assistant-turn-end', 'old'); h.A.noteAssistantAudioStreamClosed('old'); await ticks(); h.endSources();
        assert.equal(h.S.assistantTurnSettledId, 'old'); assert.ok(h.logs.some(args => String(args).includes('flush rejection')));
        h.control.rejectFlush = false; h.buffers.length = 0; h.start('new'); h.enqueue('new', 'new', join(page(1, true), page(2))); h.A.noteAssistantAudioStreamClosed('new'); await ticks(); assert.deepEqual(h.buffers.flat(), [1, 2]);
    });
    await check('cleanup-rejection-cannot-retire-replacement', async () => {
        const h = harness(); const old = await h.box.getOggOpusDecoder(); let attempted = false;
        old.free = async () => { attempted = true; throw new Error('cleanup rejection'); };
        await h.box.resetOggOpusDecoder(); const current = await h.box.getOggOpusDecoder(); await ticks();
        assert.equal(attempted, true); assert.notEqual(current, old); assert.equal(await h.box.getOggOpusDecoder(), current);
        assert.ok(h.logs.some(args => String(args).includes('cleanup rejection')));
    });
    await check('owner-reset-rejection-recovers-on-fresh-generation', async () => {
        const h = harness(); await h.box.decodeOggOpusChunk(page(9, true), { speechId: 'old' });
        const old = await h.box.getOggOpusDecoder(); old.reset = async () => { throw new Error('reset rejection'); };
        await assert.rejects(h.box.decodeOggOpusChunk(page(8, true), { speechId: 'new' }), /reset rejection/);
        assert.deepEqual(pcm(await h.box.decodeOggOpusChunk(join(page(1, true), page(2)), { speechId: 'after_failure' })), [1]);
    });
    await check('cancel-during-context-resume-discards-old-output', async () => {
        const h = harness(true), gate = defer(); h.S.audioPlayerContext.state = 'suspended';
        h.S.audioPlayerContext.resume = async () => { await gate.promise; h.S.audioPlayerContext.state = 'running'; };
        h.start('old'); h.enqueue('old', 'old', join(page(9, true), page(8))); await ticks(); h.A.clearAudioQueueWithoutDecoderReset();
        h.start('new'); h.enqueue('new', 'new', join(page(1, true), page(2))); gate.resolve(); await ticks(); h.A.noteAssistantAudioStreamClosed('new'); await ticks(); assert.deepEqual(h.buffers.flat(), [1, 2]);
    });
    await check('diagnostic-fallbacks-and-stale-epoch', async () => {
        const h = harness(); h.win.t = null; assert.equal(h.box.safeT('x', 'fallback'), 'fallback');
        h.win.t = key => key; assert.equal(h.box.safeT('x', 'fallback'), 'fallback');
        h.win.t = () => { throw new Error('translation failure'); }; assert.equal(h.box.safeT('x', 'fallback'), 'fallback');
        h.win.t = (_key, params) => params.value; assert.equal(h.box.safeT('x', 'fallback', { value: 'translated' }), 'translated');
        h.S.incomingAudioEpoch = 1; assert.equal(await h.box.decodeOggOpusChunk(page(1, true), { epoch: 0, speechId: 'stale' }), null);
        assert.equal(h.instances.length, 0);
    });
    await check('cancel-wakes-outer-blob-resume-and-reset-waits', async () => {
        for (const method of ['clearAudioQueue', 'clearAudioQueueWithoutDecoderReset']) {
            for (const stage of ['blob', 'resume', 'reset']) {
                for (const rejectsLate of [false, true]) {
                    const h = harness(true), gate = defer(); h.start('old');
                    if (stage === 'resume') {
                        h.S.audioPlayerContext.state = 'suspended';
                        h.S.audioPlayerContext.resume = async () => { await gate.promise; };
                    }
                    if (stage === 'reset') h.S.decoderResetPromise = gate.promise;
                    h.enqueue('old', 'old', join(page(9, true), page(8)), stage === 'blob' ? gate : null); await ticks();
                    assert.equal(h.S.isProcessingIncomingAudioBlob, true);
                    await h.A[method](); h.buffers.length = 0;
                    // New playback no longer needs the cancelled resume operation.
                    if (stage === 'resume') h.S.audioPlayerContext.state = 'running';
                    h.start('new'); h.enqueue('new', 'new', join(page(1, true), page(2)));
                    h.A.noteAssistantAudioStreamClosed('new'); await ticks();
                    assert.deepEqual(h.buffers.flat(), [1, 2]);
                    assert.equal(h.S.isProcessingIncomingAudioBlob, false);
                    assert.equal(h.S.assistantAudioStreamClosedTurnId, 'new');
                    if (rejectsLate) gate.reject(new Error('late outer wait rejection')); else gate.resolve();
                    await ticks();
                    assert.deepEqual(h.buffers.flat(), [1, 2]);
                    assert.equal(h.S.assistantAudioStreamClosedTurnId, 'new');
                    assert.equal(h.S.isProcessingIncomingAudioBlob, false);
                }
            }
        }
    });
    await check('cancelled-reset-cannot-clear-replacement-reset', async () => {
        for (const rejectsLate of [false, true]) {
            const h = harness(true), old = defer(), current = defer(); h.S.decoderResetPromise = old.promise;
            h.start('old'); h.enqueue('old', 'old', page(9, true)); await ticks();
            h.A.clearAudioQueueWithoutDecoderReset(); h.S.decoderResetPromise = current.promise;
            h.start('new'); h.enqueue('new', 'new', join(page(1, true), page(2))); await ticks();
            assert.equal(h.S.processingAudioBlobTurnId, 'new');
            assert.equal(h.S.decoderResetPromise, current.promise);
            if (rejectsLate) old.reject(new Error('late reset rejection')); else old.resolve();
            await ticks(); assert.equal(h.S.decoderResetPromise, current.promise);
            assert.deepEqual(h.buffers, []); assert.equal(h.S.isProcessingIncomingAudioBlob, true);
            current.resolve(); await ticks(); h.A.noteAssistantAudioStreamClosed('new'); await ticks();
            assert.deepEqual(h.buffers.flat(), [1, 2]); assert.equal(h.S.decoderResetPromise, null);
        }
    });
    await check('active-outer-rejection-does-not-stop-next-blob', async () => {
        for (const stage of ['blob', 'resume']) {
            const h = harness(true), gate = defer(), error = new TypeError('active outer rejection');
            if (stage === 'resume') {
                h.S.audioPlayerContext.state = 'suspended';
                h.S.audioPlayerContext.resume = async () => { await gate.promise; };
            }
            h.start('t'); h.enqueue('t', 't', page(9, true), stage === 'blob' ? gate : null); await ticks();
            h.enqueue('t', 't', join(page(1, true), page(2))); h.A.noteAssistantAudioStreamClosed('t');
            h.S.audioPlayerContext.state = 'running'; gate.reject(error); await ticks();
            assert.deepEqual(h.buffers.flat(), [1, 2]);
            assert.ok(h.logs.some(args => args.includes(error)), 'preserve the actual exception');
            assert.equal(h.S.isProcessingIncomingAudioBlob, false);
        }
    });
    await check('cancel-releases-old-owner-during-shared-device-preparation', async () => {
        for (const method of ['clearAudioQueue', 'clearAudioQueueWithoutDecoderReset']) {
            const h = harness(true), gate = defer(), context = h.S.audioPlayerContext; let entered = false;
            context.setSinkId = async () => { entered = true; await gate.promise; };
            h.win.AudioContext = function () { return context; }; h.S.audioPlayerContext = null;
            h.start('old'); h.enqueue('old', 'old', page(9, true)); await ticks(); assert.equal(entered, true);
            await h.A[method](); h.start('new'); h.enqueue('new', 'new', join(page(1, true), page(2))); await ticks();
            // The receiver has handed over, but shared hardware still needs its
            // genuine preparation. Cancellation must not free that shared context.
            assert.equal(h.S.processingAudioBlobTurnId, 'new'); assert.equal(h.S.audioPlayerContext, context);
            gate.resolve(); await ticks(); h.A.noteAssistantAudioStreamClosed('new'); await ticks();
            assert.deepEqual(h.buffers.flat(), [1, 2]); assert.equal(h.S.isProcessingIncomingAudioBlob, false);
        }
    });
    await check('direct-decoder-cancel-settles-before-physical-owner', async () => {
        for (const stage of ['ready', 'decode', 'flush']) {
            const h = harness(), gate = defer();
            if (stage === 'flush') await h.box.decodeOggOpusChunk(page(9, true), { speechId: 'old' });
            h.control[stage] = gate;
            const work = stage === 'flush' ? h.box.flushOggOpusDecoder({ epoch: 0, speechId: 'old' })
                : h.box.decodeOggOpusChunk(page(9, true), { speechId: 'old' });
            let settled = false; work.then(value => { assert.equal(value, null); settled = true; }); await ticks();
            h.S.incomingAudioEpoch++; h.box.invalidateOggOpusDecoder(); h.control[stage] = null; await ticks();
            assert.equal(settled, true); assert.equal(h.instances[0].freed, false);
            assert.deepEqual(pcm(await h.box.decodeOggOpusChunk(join(page(1, true), page(2)), { speechId: 'new' })), [1]);
            gate.resolve(); await ticks(); assert.equal(h.instances[0].freed, true); assert.equal(h.instances[1].freed, false);
        }
    });
    await check('repeated-cancel-with-late-reads-keeps-one-receiver', async () => {
        const h = harness(true), pending = [];
        for (let i = 0; i < 40; i++) {
            const gate = defer(); pending.push(gate); h.start('old-' + i);
            h.enqueue('old-' + i, 'old-' + i, page(9, true), gate); await ticks();
            h.A.clearAudioQueueWithoutDecoderReset(); await ticks(); assert.equal(h.S.isProcessingIncomingAudioBlob, false);
        }
        h.start('new'); h.enqueue('new', 'new', join(page(1, true), page(2))); h.A.noteAssistantAudioStreamClosed('new'); await ticks();
        for (let i = 0; i < pending.length; i++) {
            if (i % 2) pending[i].reject(new Error('late read rejection')); else pending[i].resolve();
        }
        await ticks(); assert.deepEqual(h.buffers.flat(), [1, 2]); assert.equal(h.S.isProcessingIncomingAudioBlob, false);
        assert.equal(h.S.assistantAudioStreamClosedTurnId, 'new'); assert.equal(h.instances.length, 1);
    });
    console.log(JSON.stringify({ checks: reports, count: reports.length }));
})().catch(error => { console.error(error); process.exitCode = 1; });
