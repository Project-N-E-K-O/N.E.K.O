// Deterministic browser lifecycle tests. Audio nodes and frame delivery are controlled;
// the app playback, global bridge and Live2D manager methods are production code.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');

function fixture({ paced = false, playbackTransform = source => source } = {}) {
    const frames = new Map(), timers = new Map(), listeners = new Map();
    const sources = [];
    let serial = 0, sample = 144;
    const add = (type, fn) => {
        if (!listeners.has(type)) listeners.set(type, new Set());
        listeners.get(type).add(fn);
    };
    const emit = (type, detail) => {
        for (const fn of [...(listeners.get(type) || [])]) fn({ type, detail });
    };
    const document = {
        readyState: 'loading', hidden: false, visibilityState: 'visible',
        getElementById() { return null; }, addEventListener: add, removeEventListener() {},
        body: { classList: { contains() { return false; } } },
    };
    const queueFrame = fn => { const id = ++serial; frames.set(id, fn); return id; };
    const setTimer = (fn, ms) => { const id = ++serial; timers.set(id, { fn, ms }); return id; };
    const errors = [];
    const quietConsole = { log() {}, warn() {}, error(...args) { errors.push(args); }, debug() {}, info() {} };
    const window = {
        document, console: quietConsole, location: { pathname: '/model_manager', search: '' },
        innerWidth: 1200, innerHeight: 800, lanlan_config: { model_type: 'live2d' },
        addEventListener: add, removeEventListener() {},
        dispatchEvent: event => { emit(event.type, event.detail); return true; },
        setTimeout: setTimer, clearTimeout: id => timers.delete(id),
        setInterval: setTimer, clearInterval: id => timers.delete(id),
        requestAnimationFrame: queueFrame, cancelAnimationFrame: id => frames.delete(id),
        localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
        PIXI: { live2d: { Live2DModel: class {} } },
    };
    if (paced) window.nekoFramePacing = {
        currentTimerTickFps: () => 30,
        requestPacedFrame: fn => { const id = queueFrame(fn); return () => frames.delete(id); },
    };
    const context = vm.createContext({ window, document, console: quietConsole,
        PIXI: window.PIXI, performance: { now: () => 0 },
        requestAnimationFrame: queueFrame, cancelAnimationFrame: id => frames.delete(id),
        setTimeout: setTimer, clearTimeout: id => timers.delete(id),
        setInterval: setTimer, clearInterval: id => timers.delete(id),
        localStorage: window.localStorage, navigator: {}, URL, URLSearchParams,
        CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
        resetOggOpusDecoder: async () => {},
    });
    const run = name => vm.runInContext(read(name), context, { filename: name });
    run('static/app/app-state.js');
    run('static/live2d/live2d-core.js');
    run('static/live2d/live2d-emotion.js');
    run('static/live2d/live2d-model.js');
    run('static/live2d/live2d-init.js');
    vm.runInContext(playbackTransform(read('static/app/app-audio-playback.js')), context,
        { filename: 'static/app/app-audio-playback.js' });
    const manager = window.live2dManager, S = window.appState;
    const makeModel = () => {
        const ids = ['ParamMouthOpenY', 'ParamMouthForm'];
        const values = [0, -.4];
        const core = {
            getParameterIndex: id => ids.indexOf(id), getParameterCount: () => ids.length,
            getParameterId: i => ids[i], getParameterValueByIndex: i => values[i],
            getParameterValueById: id => values[ids.indexOf(id)],
            setParameterValueByIndex: (i, value) => { values[i] = value; },
            setParameterValueById: (id, value) => { if (ids.includes(id)) values[ids.indexOf(id)] = value; },
            update() { this.rendered = [...values]; },
        };
        return { destroy() { this.destroyed = true; }, internalModel: { coreModel: core, motionManager: {
            update() { values[0] = .23; values[1] = -.4; },
        } }, destroyed: false, deltaTime: 1000 / 30, values, core };
    };
    const load = () => {
        const model = makeModel();
        manager.currentModel = model;
        manager.installMouthOverride();
        manager.onModelLoaded(model);
        return model;
    };
    let model = load();
    const analyser = { fftSize: 8, getByteTimeDomainData(array) { array.fill(sample); }, connect() {} };
    const parameter = () => ({ value: 0, setTargetAtTime() {} });
    S.globalAnalyser = analyser;
    S.speakerGainNode = { gain: parameter() };
    S.audioPlayerContext = {
        currentTime: 1, state: 'running', destination: {}, sampleRate: 48000,
        createGain: () => ({ gain: parameter(), connect() {}, disconnect() {} }),
        createBufferSource: () => {
            const source = { connect() {}, disconnect() { this.disconnected = true; },
                start() {}, stop() { this.stopped = true; } };
            sources.push(source);
            return source;
        },
    };
    const frame = () => {
        const pending = [...frames]; frames.clear();
        for (const [, fn] of pending) fn();
    };
    const render = () => {
        model = manager.currentModel;
        model.internalModel.motionManager.update(model.core, 0);
        model.core.update();
        return model.core.rendered;
    };
    const schedule = (turn = 'turn') => {
        S.assistantTurnId = turn;
        window.appAudioPlayback.rememberAssistantAudioSpeechTurn(turn, turn);
        S.isPlaying = true;
        S.audioBufferQueue.push({ buffer: { duration: .2, sampleRate: 48000 }, turnId: turn, speechId: turn });
        window.appAudioPlayback.scheduleAudioChunks();
        return sources.at(-1);
    };
    return { context, window, manager, S, frames, timers, sources, analyser, emit, run, errors,
        frame, render, load, makeModel, schedule, silence() { sample = 128; } };
}

for (const paced of [false, true]) {
    test(`speech owns opening through silence (${paced ? 'paced timer' : 'rAF'})`, () => {
        const f = fixture({ paced });
        const owner = f.window.appAudioPlayback.startLipSync(f.manager.currentModel, f.analyser, 'turn');
        for (let i = 0; i < 4; i++) f.frame();
        assert.ok(f.render()[0] > .1);
        assert.equal(f.render()[1], -.4, 'audio must leave expression shape intact');
        f.silence();
        for (let i = 0; i < 80; i++) f.frame();
        assert.ok(f.manager.hasActiveLipSync());
        assert.ok(f.render()[0] < .001, 'motion .23 must not reopen the mouth in a speech pause');
        f.window.appAudioPlayback.stopLipSync(f.manager.currentModel, owner);
        assert.equal(f.manager.hasActiveLipSync(), false);
        assert.equal(f.frames.size, 0);
        assert.equal(f.render()[0], .23, 'motion regains control only after speech ends');
    });
}

test('late old samples and explicit stop cannot alter a replacement session', () => {
    const f = fixture();
    const old = f.window.appAudioPlayback.startLipSync(f.manager.currentModel, f.analyser, 'turn');
    const oldFrame = [...f.frames.values()][0];
    const current = f.window.appAudioPlayback.startLipSync(f.manager.currentModel, f.analyser, 'turn');
    const pending = [...f.frames.keys()];
    assert.equal(f.window.appAudioPlayback.stopLipSync(f.manager.currentModel, old), false);
    oldFrame();
    assert.deepEqual([...f.frames.keys()], pending);
    assert.equal(f.window.LanLan1.setMouth(.7, current), true);
    assert.equal(f.window.LanLan1.setMouth(0, old), false);
    assert.equal(f.window.LanLan1.setMouth(0), false);
    assert.equal(f.render()[0], .7);
});

test('Live2D model replacement replays the current owner and latest amplitude', async () => {
    const f = fixture();
    const owner = {};
    f.window.LanLan1.beginLipSync(owner);
    f.window.LanLan1.setMouth(.6, owner);
    const old = f.manager.currentModel;
    await f.manager.removeModel(); // real teardown, before the replacement is ready
    assert.equal(old.destroyed, true);
    f.window.LanLan1.setMouth(.35, owner);
    assert.equal(f.window.LanLan1.setMouth(.9), false);
    const next = f.load();
    assert.equal(f.manager.hasActiveLipSync(), true);
    assert.equal(f.render()[0], .35);
    assert.equal(next.values[1], -.4);
    f.window.LanLan1.endLipSync(owner);
    assert.equal(f.manager.hasActiveLipSync(), false);
});

test('failed model loading and cancellation leave no owner to replay later', async () => {
    const f = fixture();
    const owner = f.window.appAudioPlayback.startLipSync(f.manager.currentModel, f.analyser, 'turn');
    await f.manager.removeModel();
    f.frame();
    f.emit('neko-assistant-speech-cancel', { turnId: 'turn' });
    f.load();
    assert.equal(f.manager.hasActiveLipSync(), false);
    assert.equal(f.frames.size, 0);
});

test('avatar type change retires Live2D sampling without touching the new type', () => {
    const f = fixture();
    let mmdWrites = 0;
    f.window.mmdManager = { expression: { setMorphWeight() { mmdWrites++; } } };
    const owner = f.window.appAudioPlayback.startLipSync(f.manager.currentModel, f.analyser, 'turn');
    f.S.lipSyncActive = true;
    f.window.lanlan_config = { model_type: 'live3d', live3d_sub_type: 'mmd' };
    f.frame();
    assert.equal(f.manager.hasActiveLipSync(), false);
    assert.equal(f.frames.size, 0);
    assert.equal(f.S.lipSyncActive, false);
    assert.equal(f.window.LanLan1.setMouth(.8, owner), false);
    assert.equal(mmdWrites, 0);
    f.window.LanLan1.setMouth(.2);
    assert.equal(mmdWrites, 1, 'legacy single-argument MMD dispatch is preserved');
});

test('real source.onended cannot finalize a newer owner even with the same turn and epoch', () => {
    const f = fixture();
    const old = f.schedule();
    f.window.appAudioPlayback.clearAudioQueueWithoutDecoderReset();
    const next = f.schedule();
    f.emit('neko-assistant-turn-end', { turnId: 'turn', source: 'test' });
    f.window.appAudioPlayback.noteAssistantAudioStreamClosed('turn');
    // Deliver the old source while the new one is no longer queued: turn-only checks
    // would consider the same turn drained and release its new owner.
    f.S.scheduledSources = [];
    const published = f.window.NekoSpeechPlaybackState;
    old.onended();
    assert.equal(f.window.NekoSpeechPlaybackState, published, 'old source must not publish state for its successor');
    assert.equal(f.manager.hasActiveLipSync(), true);
    assert.equal(f.S.lipSyncActive, true);
    assert.equal(f.S.assistantTurnCompletedId, 'turn');
    f.window.appAudioPlayback.stopLipSync(f.manager.currentModel, next._nekoLipSyncOwner);
});

test('removing the onended owner fence exposes the stale-source publication', () => {
    let removed = false;
    const f = fixture({ playbackTransform(source) {
        const guard = /if \(src\._nekoAudioEpoch !== S\.incomingAudioEpoch \|\|[\s\S]*?return;/;
        removed = guard.test(source);
        return source.replace(guard, '');
    } });
    assert.equal(removed, true, 'counterfactual must remove the production fence');
    const old = f.schedule();
    f.window.appAudioPlayback.clearAudioQueueWithoutDecoderReset();
    f.schedule();
    f.S.scheduledSources = [];
    const published = f.window.NekoSpeechPlaybackState;
    old.onended();
    assert.notEqual(f.window.NekoSpeechPlaybackState, published,
        'the same event order must expose the missing fence');
});

test('consecutive turns share the analyser but receive different owners', () => {
    const f = fixture();
    const old = f.schedule('old-turn');
    for (let i = 0; i < 4; i++) f.frame();
    const next = f.schedule('new-turn');
    assert.notEqual(old._nekoLipSyncOwner, next._nekoLipSyncOwner);
    assert.equal(f.frames.size, 1);
    for (let i = 0; i < 4; i++) f.frame();
    const opening = f.render()[0];
    assert.ok(opening > .1);
    f.emit('neko-assistant-turn-end', { turnId: 'new-turn', source: 'test' });
    f.window.appAudioPlayback.noteAssistantAudioStreamClosed('new-turn');
    old.onended();
    assert.equal(f.manager.hasActiveLipSync(), true);
    assert.equal(f.render()[0], opening, 'old turn completion must not close the currently audible mouth');
    next.onended();
    assert.equal(f.manager.hasActiveLipSync(), false);
});

test('sampling and source-start failures release only the owner they created', () => {
    const f = fixture({ paced: true });
    f.analyser.getByteTimeDomainData = () => { throw new Error('analyser retired'); };
    assert.equal(f.window.appAudioPlayback.startLipSync(f.manager.currentModel, f.analyser, 'turn'), null);
    assert.equal(f.manager.hasActiveLipSync(), false);
    assert.equal(f.frames.size, 0);
    f.analyser.getByteTimeDomainData = array => array.fill(144);
    f.S.audioPlayerContext.createBufferSource = () => ({ connect() {}, disconnect() {},
        start() { throw new Error('source failed'); } });
    assert.throws(() => f.schedule(), /source failed/);
    assert.equal(f.manager.hasActiveLipSync(), false);
    assert.equal(f.frames.size, 0);
});

test('pagehide retires Live2D sampling and prevents a retained callback from restarting it', () => {
    const f = fixture();
    f.window.appAudioPlayback.startLipSync(f.manager.currentModel, f.analyser, 'turn');
    const oldFrame = [...f.frames.values()][0];
    f.emit('pagehide');
    oldFrame();
    assert.equal(f.manager.hasActiveLipSync(), false);
    assert.equal(f.frames.size, 0);
});

test('late cancellation from a different turn cannot release the current speech', () => {
    const f = fixture();
    f.schedule('new-turn');
    f.emit('neko-assistant-speech-cancel', { turnId: 'old-turn' });
    assert.equal(f.manager.hasActiveLipSync(), true);
    assert.equal(f.S.lipSyncActive, true);
    f.emit('neko-assistant-speech-cancel', { turnId: 'new-turn' });
    assert.equal(f.manager.hasActiveLipSync(), false);
});

async function checkCharacterSwitchCancellation({ paced = false, speechActive = true, characterTransform = source => source } = {}) {
    const f = fixture({ paced });
    let rejectCharacters;
    const characters = new Promise((resolve, reject) => { rejectCharacters = reject; });
    const oldSocket = { readyState: 1, close() { throw new Error('early failure must keep the old socket'); } };
    const requests = [], cancellations = [];
    f.context.WebSocket = { OPEN: 1, CLOSED: 3, CLOSING: 2 };
    f.S.socket = oldSocket;
    f.window.lanlan_config.lanlan_name = 'old-character';
    f.window.invalidatePendingMusicSearch = () => {};
    f.manager.pixi_app = { ticker: {
        started: true, stop() { this.started = false; }, start() { this.started = true; },
    } };
    f.context.fetch = url => { requests.push(url); return characters; };
    f.window.addEventListener('neko-assistant-speech-cancel', event => cancellations.push(event.detail));
    if (speechActive) f.schedule('audio-turn');
    const oldFrame = [...f.frames.values()][0];

    // Use the production text-turn and character-switch entry points, not a
    // synthetic cancellation event. Text can advance before old audio drains.
    f.run('static/app/app-websocket.js');
    f.S.assistantTurnId = null;
    f.S.assistantPendingTurnServerId = 'text-turn';
    f.S.assistantTurnAwaitingBubble = true;
    f.window.appWebSocket.ensureAssistantTurnStarted('gemini_response_first_chunk', 'text-turn');
    assert.equal(f.S.assistantTurnId, 'text-turn');
    if (speechActive) assert.equal(f.S.assistantSpeechActiveTurnId, 'audio-turn');
    vm.runInContext(characterTransform(read('static/app/app-character.js')), f.context);
    const switchTask = f.window.appCharacter.handleCatgirlSwitch('new-character', 'old-character');
    try {
        assert.deepEqual(requests, ['/api/characters']);
        assert.equal(cancellations.length, 1);
        assert.equal(cancellations[0].source, 'character_switch');
        assert.equal(f.S.isPlaying, false, 'cancel before waiting for configuration');
        assert.equal(f.manager.hasActiveLipSync(), false);
        assert.equal(f.S.lipSyncActive, false);
        assert.equal(f.frames.size, 0);
        assert.equal(cancellations[0].turnId, speechActive ? 'audio-turn' : 'text-turn');
        if (oldFrame) oldFrame();
        assert.equal(f.frames.size, 0, 'a captured old sample cannot restart after cancellation');
        rejectCharacters(new Error('characters unavailable'));
        await switchTask;
        assert.equal(f.S.isPlaying, false, 'early switch failure must not restore cancelled playback state');
        assert.equal(f.manager.hasActiveLipSync(), false);
        assert.equal(f.frames.size, 0);
        assert.equal(f.S.socket, oldSocket);
        assert.equal(f.S.isSwitchingCatgirl, false);
        assert.equal(f.manager.pixi_app.ticker.started, true);
        assert.equal(f.window.lanlan_config.lanlan_name, 'old-character');
        assert.ok(f.errors.some(args => args[1]?.message === 'characters unavailable'));
    } finally {
        rejectCharacters(new Error('characters unavailable'));
        await switchTask;
        await f.window.appAudioPlayback.clearAudioQueue();
    }
}

for (const paced of [false, true]) {
    for (const speechActive of [true, false]) {
        test(`real character-switch failure cancels ${speechActive ? 'old audio before newer text' : 'text-only turn'} (${paced ? 'paced timer' : 'rAF'})`, () =>
            checkCharacterSwitchCancellation({ paced, speechActive }));
    }
    test(`text-first cancellation mutation fails the real overlapping-turn regression (${paced ? 'paced timer' : 'rAF'})`, async () => {
        const source = read('static/app/app-character.js');
        const mutant = source.replace(
            'var turnId = S.assistantSpeechActiveTurnId || S.assistantTurnId || null;',
            'var turnId = S.assistantTurnId || S.assistantSpeechActiveTurnId || null;');
        assert.notEqual(mutant, source, 'mutation must reach the character-switch producer');
        await assert.rejects(checkCharacterSwitchCancellation({ paced, characterTransform: () => mutant }),
            { name: 'AssertionError', code: 'ERR_ASSERTION', actual: true, expected: false,
                operator: 'strictEqual', message: /^cancel before waiting for configuration\b/ });
    });
}

test('natural source end waits for audio_done; normal finish releases the owner', () => {
    const f = fixture();
    const source = f.schedule();
    f.emit('neko-assistant-turn-end', { turnId: 'turn', source: 'test' });
    source.onended();
    assert.equal(f.manager.hasActiveLipSync(), true, 'empty chunk queues can be a speech gap');
    f.window.appAudioPlayback.noteAssistantAudioStreamClosed('turn');
    assert.equal(f.manager.hasActiveLipSync(), false);
    assert.equal(f.S.lipSyncActive, false);
    assert.equal(f.frames.size, 0);
});

for (const termination of ['socket_close', 'session_ended_by_server']) {
    for (const paced of [false, true]) {
        test(`real ${termination} clears overlapping audio/text turns (${paced ? 'paced timer' : 'rAF'})`, async () => {
            const f = fixture({ paced });
            class Socket {
                static OPEN = 1;
                constructor(url) { this.url = url; this.readyState = 1; }
                send() {}
            }
            f.context.WebSocket = Socket;
            f.context.Blob = Blob;
            f.context.Node = { ELEMENT_NODE: 1 };
            f.window.t = key => key;
            f.window.location.protocol = 'http:';
            f.window.location.host = 'localhost:48911';
            f.window.lanlan_config.lanlan_name = '';
            f.run('static/app/app-websocket.js');
            f.window.appWebSocket.connectWebSocket();
            const source = f.schedule('audio-turn');
            const oldFrame = [...f.frames.values()][0];
            f.S.assistantTurnId = null;
            f.S.assistantPendingTurnServerId = 'text-turn';
            f.S.assistantTurnAwaitingBubble = true;
            f.window.appWebSocket.ensureAssistantTurnStarted('gemini_response_first_chunk', 'text-turn');
            assert.equal(f.S.assistantTurnId, 'text-turn');
            assert.equal(f.S.assistantSpeechActiveTurnId, 'audio-turn');
            const cancellations = [];
            f.window.addEventListener('neko-assistant-speech-cancel', event => cancellations.push(event.detail));
            const socket = f.S.socket;
            const epoch = f.S.incomingAudioEpoch;
            try {
                if (termination === 'socket_close') socket.onclose();
                else await socket.onmessage({ data: JSON.stringify({ type: termination }) });
                assert.equal(cancellations[0].source, termination);
                assert.equal(cancellations[0].turnId, 'text-turn', 'reproduce the reviewed ID mismatch');
                assert.ok(cancellations.some(event => event.source === 'clear_audio_queue'),
                    'the full disconnect path must reach its production queue cleanup');
                assert.equal(f.S.isPlaying, false);
                assert.equal(f.S.lipSyncActive, false);
                assert.equal(f.manager.hasActiveLipSync(), false);
                assert.equal(f.frames.size, 0);
                assert.equal(f.S.scheduledSources.length, 0);
                assert.equal(source.stopped, true);
                assert.equal(source.disconnected, true);
                assert.equal(f.S.assistantTurnCompletedId, null);
                assert.equal(f.S.assistantSpeechActiveTurnId, null);
                assert.ok(f.S.incomingAudioEpoch > epoch);
                assert.equal(f.window.NekoSpeechPlaybackState.active, false);
                f.schedule('successor');
                const published = f.window.NekoSpeechPlaybackState;
                oldFrame();
                source.onended();
                assert.equal(f.window.NekoSpeechPlaybackState, published);
                assert.equal(f.S.isPlaying, true);
                assert.equal(f.manager.hasActiveLipSync(), true);
                assert.equal(f.frames.size, 1);
            } finally {
                f.window.appWebSocket.cancelAutoReconnect();
                await f.window.appAudioPlayback.clearAudioQueue();
            }
        });
    }
}

test('audio still finalizes after avatar change retired its Live2D sampler', () => {
    const f = fixture();
    const source = f.schedule();
    f.emit('neko-assistant-turn-end', { turnId: 'turn', source: 'test' });
    f.window.appAudioPlayback.noteAssistantAudioStreamClosed('turn');
    f.window.lanlan_config = { model_type: 'vrm' };
    f.frame();
    assert.equal(f.manager.hasActiveLipSync(), false);
    source.onended();
    assert.equal(f.S.isPlaying, false, 'no successor owner means ordinary audio cleanup must still run');
    assert.equal(f.S.assistantTurnSettledId, 'turn');
});

test('AudioManager chunks share one sampler and old frame cannot revive after stop', () => {
    const f = fixture();
    f.run('static/audio-loader.js');
    const am = f.window.AM;
    am.register('LanLan1');
    const owner = am.startLipSync('LanLan1', f.analyser);
    assert.equal(am.startLipSync('LanLan1', f.analyser), owner);
    assert.equal(f.frames.size, 1);
    const oldFrame = [...f.frames.values()][0];
    am.stopLipSync('LanLan1', owner);
    const next = am.startLipSync('LanLan1', f.analyser);
    oldFrame();
    assert.equal(am.stopLipSync('LanLan1', owner), false);
    assert.equal(f.frames.size, 1);
    assert.ok(f.manager.hasActiveLipSync());
    am.stopLipSync('LanLan1', next);
    assert.equal(f.frames.size, 0);
});

for (const mode of ['context', 'element']) {
    for (const paced of [false, true]) {
        test(`real guide ${mode} playback retires assistant audio when taking Live2D ownership (${paced ? 'paced timer' : 'rAF'})`, async () => {
            const f = fixture({ paced });
            const old = f.schedule('assistant');
            const oldFrame = [...f.frames.values()][0];
            let decoderResets = 0;
            f.context.resetOggOpusDecoder = async () => { decoderResets++; };
            let resolveStarted, releaseStart;
            const started = new Promise(resolve => { resolveStarted = resolve; });
            const startGate = new Promise(resolve => { releaseStart = resolve; });
            f.window.__YuiGuideDirector = {
                shouldGuideAudioDriveMouth: key => typeof key === 'string' && !!key.trim(),
                clamp: (value, low, high) => Math.max(low, Math.min(high, value)),
                wait: () => startGate,
                estimateSpeechDurationMs: () => 100,
                guideAudioSrc: () => '/controlled-guide.wav',
                resumeKnownAudioContexts: async () => {},
                fetchWithTimeout: async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(8) }),
            };
            f.S.audioPlayerContext.createAnalyser = () => f.analyser;
            f.S.audioPlayerContext.createMediaElementSource = () => ({ connect() {}, disconnect() {} });
            f.S.audioPlayerContext.decodeAudioData = async () => ({ duration: 1 });
            const createSource = f.S.audioPlayerContext.createBufferSource;
            f.S.audioPlayerContext.createBufferSource = () => {
                const source = createSource();
                source.start = () => resolveStarted(source);
                return source;
            };
            f.analyser.disconnect = () => {};
            f.context.Audio = class {
                play() { resolveStarted(this); return Promise.resolve(); }
                pause() {} removeAttribute() {} load() {}
            };
            f.run('static/tutorial/yui-guide/director/voice-queue.js');
            const queue = new f.window.__YuiGuideDirector.YuiGuideVoiceQueue();
            if (mode === 'element') f.S.audioPlayerContext.decodeAudioData = async () => {
                throw new Error('controlled context decode failure');
            };
            const playing = queue.speak('Guide narration', { voiceKey: 'guide' });
            try {
                assert.notEqual(old.stopped, true, 'startup delay has not taken the mouth owner');
                releaseStart();
                const guide = await started;
                assert.equal(old.stopped, true, 'the displaced assistant must not remain audible');
                assert.equal(old.disconnected, true);
                assert.equal(f.S.isPlaying, false);
                assert.equal(f.S.scheduledSources.length, 0);
                assert.equal(f.S.lipSyncActive, false);
                assert.equal(decoderResets, 0, 'handoff does not reset the shared decoder');
                const session = queue.currentMouthMotionSession;
                assert.ok(session?.mouthOwner, 'the guide must retain the newer owner');
                oldFrame(); old.onended();
                assert.equal(f.manager.hasActiveLipSync(), true);
                assert.equal(f.frames.size, 1);
                f.frame();
                assert.ok(f.render()[0] > 0);
                guide.onended();
                await playing;
                assert.equal(f.manager.hasActiveLipSync(), false);
                assert.equal(f.frames.size, 0);
                f.schedule('next-assistant');
                oldFrame(); old.onended();
                assert.equal(f.manager.hasActiveLipSync(), true);
                assert.equal(f.S.isPlaying, true);
                assert.equal(f.frames.size, 1);
            } finally {
                releaseStart(); queue.stop(); await playing;
                await f.window.appAudioPlayback.clearAudioQueue();
            }
        });
    }
}

for (const paced of [false, true]) {
    test(`cancel during guide startup leaves assistant audio and owner intact (${paced ? 'paced timer' : 'rAF'})`, async () => {
        const f = fixture({ paced });
        const source = f.schedule('assistant');
        let release;
        const gate = new Promise(resolve => { release = resolve; });
        f.window.__YuiGuideDirector = { wait: () => gate };
        f.run('static/tutorial/yui-guide/director/voice-queue.js');
        const queue = new f.window.__YuiGuideDirector.YuiGuideVoiceQueue();
        const playing = queue.speak('Cancelled guide', { voiceKey: 'guide' });
        try {
            queue.stop(); release(); await playing;
            assert.notEqual(source.stopped, true);
            assert.equal(f.S.isPlaying, true);
            assert.equal(f.S.scheduledSources[0], source);
            assert.equal(f.manager.hasActiveLipSync(), true);
            assert.equal(f.frames.size, 1);
        } finally { release(); queue.stop(); await playing; await f.window.appAudioPlayback.clearAudioQueue(); }
    });

    test(`cancel during guide decode does not interrupt the assistant (${paced ? 'paced timer' : 'rAF'})`, async () => {
        const f = fixture({ paced });
        const source = f.schedule('assistant');
        let enter, release;
        const entered = new Promise(resolve => { enter = resolve; });
        const gate = new Promise(resolve => { release = resolve; });
        f.window.__YuiGuideDirector = {
            resumeKnownAudioContexts: async () => {},
            fetchWithTimeout: async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(8) }),
        };
        f.S.audioPlayerContext.decodeAudioData = () => { enter(); return gate; };
        f.run('static/tutorial/yui-guide/director/voice-queue.js');
        const queue = new f.window.__YuiGuideDirector.YuiGuideVoiceQueue();
        const playing = queue.playPreviewAudioThroughContext('/controlled.wav', 0, 0, { voiceKey: 'guide' });
        try {
            await entered;
            queue.stop(); release({ duration: 1 });
            assert.equal(await playing, true);
            assert.notEqual(source.stopped, true);
            assert.equal(f.S.isPlaying, true);
            assert.equal(f.S.scheduledSources[0], source);
            assert.equal(f.manager.hasActiveLipSync(), true);
            assert.equal(f.frames.size, 1);
        } finally {
            release({ duration: 1 }); queue.stop(); await playing;
            await f.window.appAudioPlayback.clearAudioQueue();
        }
    });

    test(`guide frame startup failure releases the acquired owner after retiring assistant sources (${paced ? 'paced timer' : 'rAF'})`, async () => {
        const f = fixture({ paced });
        const source = f.schedule('assistant');
        const oldFrame = [...f.frames.values()][0];
        f.window.__YuiGuideDirector = { shouldGuideAudioDriveMouth: () => true };
        f.run('static/tutorial/yui-guide/director/voice-queue.js');
        const queue = new f.window.__YuiGuideDirector.YuiGuideVoiceQueue();
        f.window.requestAnimationFrame = () => { throw new Error('controlled guide frame failure'); };
        try {
            assert.equal(queue.startGuideMouthMotion('guide', { analyserNode: f.analyser }), null);
            assert.equal(source.stopped, true);
            assert.equal(source.disconnected, true);
            assert.equal(f.manager.hasActiveLipSync(), false);
            assert.equal(f.frames.size, 0);
            assert.equal(f.S.isPlaying, false);
            oldFrame(); source.onended();
            assert.equal(f.frames.size, 0);
            assert.equal(f.manager.hasActiveLipSync(), false);
        } finally { queue.stop(); await f.window.appAudioPlayback.clearAudioQueue(); }
    });
}

test('AudioManager failed source start releases its new owner', () => {
    const f = fixture();
    f.run('static/audio-loader.js');
    const am = f.window.AM;
    am.ctx = f.S.audioPlayerContext;
    am.register('LanLan1');
    const parameter = { setValueAtTime() {}, linearRampToValueAtTime() {} };
    am.ctx.createGain = () => ({ gain: parameter, connect() {}, disconnect() {} });
    am.ctx.createBufferSource = () => ({ connect() {}, disconnect() {},
        start() { throw new Error('source failed'); } });
    const data = am.models.get('LanLan1');
    data.analyser = f.analyser;
    data.gain = {};
    assert.throws(() => am.enqueue('LanLan1', { duration: .2 }, 0), /source failed/);
    assert.equal(f.manager.hasActiveLipSync(), false);
    assert.equal(f.frames.size, 0);
});

function streamingAudioFixture({ paced = false } = {}) {
    const f = fixture({ paced, playbackTransform(source) {
        const call = 'void processIncomingAudioBlobQueue();';
        assert.ok(source.includes(call), 'observe the real queue task without replacing its implementation');
        return source.replaceAll(call, 'window.testIncomingAudioTask = processIncomingAudioBlobQueue();');
    } });
    class AudioBlob {
        constructor(readBuffer) { this.readBuffer = readBuffer; }
        arrayBuffer() { return this.readBuffer(); }
    }
    const signals = [];
    class Socket {
        static OPEN = 1;
        constructor(url) { this.url = url; this.readyState = 1; }
        send(data) { signals.push(JSON.parse(data)); }
    }
    f.context.Blob = AudioBlob;
    f.context.WebSocket = Socket;
    f.context.Node = { ELEMENT_NODE: 1 };
    let decoderResets = 0;
    f.window.resetOggOpusDecoder = async () => { decoderResets++; };
    f.window.t = key => key;
    f.window.location.protocol = 'http:';
    f.window.location.host = 'localhost:48911';
    f.window.lanlan_config.lanlan_name = '';
    f.S.audioPlayerContext.createBuffer = (_, length, sampleRate) => ({
        duration: length / sampleRate, sampleRate,
        copyToChannel(data) { this.pcm = [...data]; },
    });
    f.run('static/app/app-websocket.js');
    f.window.appWebSocket.connectWebSocket();
    f.window.__YuiGuideDirector = {
        shouldGuideAudioDriveMouth: () => true,
        clamp: (value, low, high) => Math.max(low, Math.min(high, value)),
    };
    f.run('static/tutorial/yui-guide/director/voice-queue.js');
    const queue = new f.window.__YuiGuideDirector.YuiGuideVoiceQueue();
    const header = (speechId, turnId = speechId) => f.S.socket.onmessage({
        data: JSON.stringify({ type: 'audio_chunk', speech_id: speechId, turn_id: turnId }),
    });
    const pcm = () => new Int16Array([1000, 2000, 3000, 4000]).buffer;
    const blob = readBuffer => {
        f.window.testIncomingAudioTask = null;
        f.S.socket.onmessage({ data: new AudioBlob(readBuffer || (async () => pcm())) });
        return f.window.testIncomingAudioTask || Promise.resolve();
    };
    const handoff = () => queue.startGuideMouthMotion('guide', { analyserNode: f.analyser });
    const cleanup = async () => {
        queue.stop(); f.window.appWebSocket.cancelAutoReconnect();
        await f.window.appAudioPlayback.clearAudioQueue();
    };
    return { ...f, queue, signals, header, blob, pcm, handoff, cleanup,
        decoderResets: () => decoderResets };
}

for (const paced of [false, true]) {
    for (const phase of ['header-only', 'blob-read', 'ogg-decode', 'decoder-reset']) {
        for (const queued of phase === 'header-only' ? [false] : [false, true]) {
            test(`guide handoff fences the first assistant chunk during ${phase}${queued ? ' with queued continuation' : ''} (${paced ? 'paced timer' : 'rAF'})`, async () => {
                const f = streamingAudioFixture({ paced });
                let enteredResolve, release, pending, successorTask;
                const entered = new Promise(resolve => { enteredResolve = resolve; });
                const gate = new Promise(resolve => { release = resolve; });
                const ogg = new Uint8Array([79, 103, 103, 83, 0, 0, 0, 0]).buffer;
                const decoded = { float32Data: new Float32Array([.2, .3]), sampleRate: 48000 };
                const result = phase === 'ogg-decode' ? decoded : f.pcm();
                try {
                    if (phase === 'decoder-reset') {
                        f.S.socket.onmessage({ data: JSON.stringify({ type: 'user_activity', interrupted_speech_id: 'previous' }) });
                        f.window.resetOggOpusDecoder = () => { enteredResolve(); return gate; };
                    }
                    if (phase === 'ogg-decode') f.context.decodeOggOpusChunk = () => { enteredResolve(); return gate; };
                    f.header('assistant');
                    if (phase !== 'header-only') {
                        pending = f.blob(() => {
                            if (phase === 'blob-read') { enteredResolve(); return gate; }
                            return Promise.resolve(phase === 'ogg-decode' ? ogg : f.pcm());
                        });
                        await entered;
                        assert.equal(f.S.processingAudioBlobTurnId, 'assistant');
                        if (queued) {
                            // Another accepted chunk queues behind the first, before any source exists.
                            f.header('assistant'); await f.blob();
                        }
                        assert.equal(f.S.incomingAudioBlobQueue.length, queued ? 1 : 0);
                    } else {
                        assert.equal(f.S.pendingAudioChunkMetaQueue.length, 1);
                    }
                    assert.equal(f.S.isPlaying, false);
                    assert.equal(f.sources.length, 0);
                    const epoch = f.S.incomingAudioEpoch;
                    const session = f.handoff();
                    assert.ok(session?.mouthOwner);
                    assert.equal(f.S.interruptedSpeechId, 'assistant');
                    assert.ok(f.S.incomingAudioEpoch > epoch);
                    assert.equal(f.S.pendingAudioChunkMetaQueue.length, 0);
                    assert.equal(f.S.incomingAudioBlobQueue.length, 0);
                    f.header('assistant'); await f.blob();
                    release(result);
                    if (pending) await pending;
                    assert.equal(f.sources.length, 0, 'neither the first chunk nor its queued continuation may publish');
                    assert.equal(f.S.isPlaying, false);
                    assert.equal(f.window.LanLan1.setMouth(.5, session.mouthOwner), true);
                    assert.equal(f.signals.filter(signal => signal.action === 'voice_play_start').length, 0);
                    f.queue.stopGuideMouthMotion(session);
                    f.header('assistant'); await f.blob();
                    assert.equal(f.sources.length, 0, 'guide end does not revive the first chunk');
                    f.window.resetOggOpusDecoder = async () => {};
                    f.header('successor'); successorTask = f.blob(); await successorTask;
                    assert.equal(f.sources.length, 1);
                    assert.equal(f.sources[0]._nekoSpeechId, 'successor');
                    assert.equal(f.S.pendingDecoderReset, false);
                    assert.equal(f.S.decoderResetPromise, null);
                } finally {
                    release(result);
                    if (pending) await pending;
                    if (successorTask) await successorTask;
                    await f.cleanup();
                }
            });
        }
    }

    test(`guide handoff rejects remaining chunks of the interrupted speech but accepts a new speech (${paced ? 'paced timer' : 'rAF'})`, async () => {
        const f = streamingAudioFixture({ paced });
        try {
            f.header('assistant'); await f.blob();
            assert.equal(f.sources.length, 1, 'initial PCM goes through the real websocket, queue and decoder');
            const old = f.sources[0];
            const epoch = f.S.incomingAudioEpoch;
            const session = f.handoff();
            assert.ok(session?.mouthOwner);
            assert.equal(old.stopped, true);
            f.header('assistant'); await f.blob();
            assert.equal(f.sources.length, 1, 'remaining audio of the interrupted speech must not restart');
            assert.equal(f.S.isPlaying, false);
            assert.equal(f.S.interruptedSpeechId, 'assistant');
            assert.ok(f.S.incomingAudioEpoch > epoch);
            assert.equal(f.window.LanLan1.setMouth(.5, session.mouthOwner), true);
            assert.equal(f.signals.filter(signal => signal.action === 'voice_play_start').length, 1);
            assert.equal(f.signals.filter(signal => signal.action === 'voice_play_end').length, 1);
            f.queue.stopGuideMouthMotion(session);
            f.header('assistant'); await f.blob();
            assert.equal(f.sources.length, 1, 'guide end does not revive the cancelled speech');
            f.header('successor'); await f.blob();
            assert.equal(f.sources.length, 2);
            assert.equal(f.S.isPlaying, true);
            assert.equal(f.S.interruptedSpeechId, null);
            assert.equal(f.S.currentPlayingSpeechId, 'successor');
            assert.equal(f.decoderResets(), 1, 'the next speech reaches the normal decoder-reset handshake');
            assert.equal(f.S.pendingDecoderReset, false);
            assert.equal(f.S.decoderResetPromise, null);
            const published = f.window.NekoSpeechPlaybackState;
            old.onended();
            assert.equal(f.window.NekoSpeechPlaybackState, published);
            assert.equal(f.manager.hasActiveLipSync(), true);
        } finally { await f.cleanup(); }
    });

    for (const phase of ['blob-read', 'ogg-decode']) {
        test(`guide handoff invalidates assistant ${phase} already in flight (${paced ? 'paced timer' : 'rAF'})`, async () => {
            const f = streamingAudioFixture({ paced });
            let enteredResolve, release;
            const entered = new Promise(resolve => { enteredResolve = resolve; });
            const gate = new Promise(resolve => { release = resolve; });
            const ogg = new Uint8Array([79, 103, 103, 83, 0, 0, 0, 0]).buffer;
            const decoded = { float32Data: new Float32Array([.2, .3]), sampleRate: 48000 };
            let pending;
            try {
                f.header('assistant'); await f.blob();
                f.header('assistant');
                if (phase === 'ogg-decode') f.context.decodeOggOpusChunk = bytes => {
                    assert.equal(bytes[0], 79); enteredResolve(); return gate;
                };
                pending = f.blob(() => {
                    if (phase === 'blob-read') { enteredResolve(); return gate; }
                    return Promise.resolve(ogg);
                });
                await entered;
                assert.equal(f.S.processingAudioBlobTurnId, 'assistant');
                const session = f.handoff();
                f.header('successor');
                const successorTask = f.blob(); // may queue behind or take over the retired worker
                release(phase === 'blob-read' ? f.pcm() : decoded);
                await pending;
                await successorTask;
                assert.equal(f.sources.length, 2, 'only the new speech may publish after the old decode');
                assert.equal(f.sources[1]._nekoSpeechId, 'successor');
                assert.equal(f.S.audioBufferQueue.length, 0);
                assert.equal(f.S.isPlaying, true);
                assert.equal(f.S.currentPlayingSpeechId, 'successor');
                assert.equal(f.window.LanLan1.setMouth(.5, session.mouthOwner), false);
                f.queue.stopGuideMouthMotion(session);
                assert.equal(f.manager.hasActiveLipSync(), true, 'retired guide cleanup preserves the new speech');
            } finally {
                release(phase === 'blob-read' ? f.pcm() : decoded);
                if (pending) await pending;
                await f.cleanup();
            }
        });
    }
}

for (const paced of [false, true]) {
    test(`a different speech can still take ownership during guide narration (${paced ? 'paced timer' : 'rAF'})`, async () => {
        const f = streamingAudioFixture({ paced });
        try {
            f.header('assistant'); await f.blob();
            const session = f.handoff();
            f.header('assistant'); await f.blob();
            assert.equal(f.sources.length, 1);
            f.header('successor'); await f.blob();
            assert.equal(f.sources.length, 2);
            assert.equal(f.S.isPlaying, true);
            assert.equal(f.window.LanLan1.setMouth(.5, session.mouthOwner), false);
            f.queue.stopGuideMouthMotion(session);
            assert.equal(f.manager.hasActiveLipSync(), true);
            assert.equal(f.frames.size, 1);
        } finally { await f.cleanup(); }
    });
}

for (const paced of [false, true]) {
    test(`guide interruption survives an A-B-A producer interleaving (${paced ? 'paced timer' : 'rAF'})`, async () => {
        const f = streamingAudioFixture({ paced });
        try {
            f.header('assistant'); await f.blob();
            const session = f.handoff();
            f.header('successor'); await f.blob();
            const successor = f.sources[1];
            const published = f.window.NekoSpeechPlaybackState;
            f.header('assistant'); await f.blob();
            assert.equal(f.sources.length, 2, 'an interrupted producer cannot become a fresh speech after B');
            assert.equal(f.S.currentPlayingSpeechId, 'successor');
            assert.equal(f.window.NekoSpeechPlaybackState, published);
            assert.notEqual(successor.stopped, true);
            f.S.socket.onmessage({ data: JSON.stringify({ type: 'audio_done', speech_id: 'assistant' }) });
            assert.equal(f.window.appAudioPlayback.isAssistantSpeechInterrupted('assistant'), false, 'the terminal releases the cancellation record');
            assert.equal(f.S.assistantSpeechActiveTurnId, 'successor', 'old completion cannot end B');
            f.queue.stopGuideMouthMotion(session);
            assert.equal(f.manager.hasActiveLipSync(), true);
        } finally { await f.cleanup(); }
    });

    test(`multiple guide interruptions stay fenced across a successor (${paced ? 'paced timer' : 'rAF'})`, async () => {
        const f = streamingAudioFixture({ paced });
        try {
            f.header('assistant'); await f.blob(); f.handoff();
            f.header('second'); await f.blob(); const session = f.handoff();
            f.header('successor'); await f.blob();
            for (const speech of ['assistant', 'second', 'assistant']) {
                f.header(speech); await f.blob();
                assert.equal(f.sources.length, 3, 'all cancelled identities survive later handoffs');
                assert.equal(f.S.currentPlayingSpeechId, 'successor');
                assert.equal(f.window.appAudioPlayback.isAssistantSpeechInterrupted(speech), true);
            }
            for (const speech of ['second', 'assistant']) {
                f.S.socket.onmessage({ data: JSON.stringify({ type: 'audio_done', speech_id: speech }) });
                assert.equal(f.window.appAudioPlayback.isAssistantSpeechInterrupted(speech), false);
            }
            assert.equal(f.S.assistantSpeechActiveTurnId, 'successor');
            f.queue.stopGuideMouthMotion(session);
            assert.equal(f.manager.hasActiveLipSync(), true);
        } finally { await f.cleanup(); }
    });
}

test('guide cancellation records without audio_done belong only to their socket', async () => {
    const f = streamingAudioFixture();
    try {
        f.header('assistant'); await f.blob(); f.handoff();
        f.header('successor'); await f.blob();
        const oldSocket = f.S.socket;
        assert.equal(f.window.appAudioPlayback.isAssistantSpeechInterrupted('assistant'), true);
        oldSocket.readyState = 3;
        oldSocket.onclose({ code: 1000, reason: 'controlled reconnect' });
        assert.equal(f.window.appAudioPlayback.isAssistantSpeechInterrupted('assistant'), false, 'close releases records even while the old socket is referenced');
        f.window.appWebSocket.connectWebSocket();
        assert.notEqual(f.S.socket, oldSocket);
        assert.equal(f.window.appAudioPlayback.isAssistantSpeechInterrupted('assistant'), false);
        // A stale socket cannot release a cancellation registered on its successor.
        f.header('assistant'); await f.blob(); f.handoff();
        oldSocket.onmessage({ data: JSON.stringify({ type: 'audio_done', speech_id: 'assistant' }) });
        assert.equal(f.window.appAudioPlayback.isAssistantSpeechInterrupted('assistant'), true);
        f.header('successor'); await f.blob();
        const count = f.sources.length;
        f.header('assistant'); await f.blob();
        assert.equal(f.sources.length, count);
    } finally { await f.cleanup(); }
});

test('guide interruption need not retain a producer that already sent audio_done', async () => {
    const f = streamingAudioFixture();
    try {
        f.header('assistant'); await f.blob();
        f.S.socket.onmessage({ data: JSON.stringify({ type: 'audio_done', speech_id: 'assistant' }) });
        assert.equal(f.S.isPlaying, true, 'the producer may finish while its source is still playing');
        f.handoff();
        f.header('successor'); await f.blob();
        assert.equal(f.sources.length, 2);
        assert.equal(f.window.appAudioPlayback.isAssistantSpeechInterrupted('assistant'), false);
    } finally { await f.cleanup(); }
});

test('MMD guide legacy dispatch does not interrupt real assistant playback', async () => {
    const f = streamingAudioFixture();
    const morphs = [];
    f.window.lanlan_config = { model_type: 'live3d', live3d_sub_type: 'mmd' };
    f.window.mmdManager = { currentModel: {}, animationModule: { startLipSync() {}, stopLipSync() {} },
        expression: { setMorphWeight(id, value) { morphs.push([id, value]); } } };
    try {
        f.header('assistant'); await f.blob();
        const source = f.sources[0], epoch = f.S.incomingAudioEpoch;
        const session = f.handoff();
        assert.ok(session);
        assert.equal(session.mouthOwner, undefined);
        f.frame();
        assert.ok(morphs.some(([, value]) => value > 0));
        assert.notEqual(source.stopped, true);
        assert.equal(f.S.isPlaying, true);
        assert.equal(f.S.incomingAudioEpoch, epoch);
        assert.equal(f.S.interruptedSpeechId, null);
    } finally { await f.cleanup(); }
});

test('Live2D guide with an idle assistant does not alter interruption state', async () => {
    const f = streamingAudioFixture();
    const snapshot = () => [f.S.interruptedSpeechId, f.S.incomingAudioEpoch, f.S.pendingDecoderReset];
    try {
        const before = snapshot();
        const session = f.handoff();
        assert.ok(session.mouthOwner);
        assert.deepEqual(snapshot(), before);
        assert.equal(f.S.isPlaying, false);
        assert.equal(f.sources.length, 0);
        assert.equal(f.window.LanLan1.setMouth(.5, session.mouthOwner), true);
    } finally { await f.cleanup(); }
});

for (const state of ['cancelled', 'cancelled-in-flight', 'skipped-header', 'text-only']) {
    test(`guide handoff preserves ${state} audio state without accepted work`, async () => {
        const f = streamingAudioFixture();
        let enteredResolve, release, pending;
        const entered = new Promise(resolve => { enteredResolve = resolve; });
        const gate = new Promise(resolve => { release = resolve; });
        try {
            f.header('previous');
            if (state === 'cancelled-in-flight') {
                pending = f.blob(() => { enteredResolve(); return gate; });
                await entered;
            }
            f.S.socket.onmessage({ data: JSON.stringify({ type: 'user_activity', interrupted_speech_id: 'previous' }) });
            if (state === 'skipped-header') f.header('previous');
            if (state === 'text-only') {
                f.S.socket.onmessage({ data: JSON.stringify({ type: 'gemini_response', isNewMessage: true, turn_id: 'text-only', text: 'hello' }) });
                assert.equal(f.S.assistantTurnId, 'text-only');
            }
            const before = [f.S.interruptedSpeechId, f.S.incomingAudioEpoch, f.S.pendingDecoderReset];
            assert.equal(f.S.currentPlayingSpeechId, 'previous', 'the last speech ID alone is not accepted audio');
            assert.equal(f.S.pendingDecoderReset, true, 'a reset latch alone is not accepted audio');
            const session = f.handoff();
            assert.ok(session?.mouthOwner);
            assert.deepEqual([f.S.interruptedSpeechId, f.S.incomingAudioEpoch, f.S.pendingDecoderReset], before);
            assert.equal(f.S.isPlaying, false);
            assert.equal(f.sources.length, 0);
        } finally {
            release(f.pcm());
            if (pending) await pending;
            await f.cleanup();
        }
    });
}

test('viewer adapters fence decode completion and late end after clear', async () => {
    const f = fixture();
    const viewer = read('templates/viewer.html');
    const start = viewer.indexOf('        let audioContext = null;');
    const end = viewer.indexOf('        function connectWebSocket()', start);
    assert.ok(start >= 0 && end > start);
    vm.runInContext(viewer.slice(start, end) + '\nwindow.viewerAudioTest = { playAudioChunk, clearAudioQueue, startLipSync, stopLipSync };', f.context);
    const decodes = [], created = [];
    f.window.AudioContext = class {
        constructor() { this.destination = {}; }
        createAnalyser() { return f.analyser; }
        decodeAudioData() { return new Promise(resolve => decodes.push(resolve)); }
        createBufferSource() { const src = { connect() {}, start() {} }; created.push(src); return src; }
        close() { return Promise.resolve(); }
    };
    const api = f.window.viewerAudioTest;
    const pending = api.playAudioChunk(new ArrayBuffer(16));
    api.clearAudioQueue();
    decodes.shift()({});
    await pending;
    assert.equal(created.length, 0, 'cancel during decode cannot restart lip sync');
    const first = api.playAudioChunk(new ArrayBuffer(16));
    decodes.shift()({});
    for (let i = 0; i < 4; i++) await Promise.resolve();
    const oldSource = created.at(-1);
    assert.ok(oldSource, JSON.stringify(f.errors));
    api.clearAudioQueue();
    const second = api.playAudioChunk(new ArrayBuffer(16));
    decodes.shift()({});
    for (let i = 0; i < 4; i++) await Promise.resolve();
    oldSource.onended();
    await first;
    assert.ok(f.manager.hasActiveLipSync());
    assert.equal(f.frames.size, 1);
    created.at(-1).onended();
    await second;
    assert.equal(f.manager.hasActiveLipSync(), false);
    assert.equal(f.frames.size, 0);
    f.window.AudioContext.prototype.createBufferSource = () => ({ connect() {},
        start() { throw new Error('source failed'); } });
    const failedStart = api.playAudioChunk(new ArrayBuffer(16));
    decodes.shift()({});
    await failedStart;
    assert.equal(f.manager.hasActiveLipSync(), false, 'failed start must release its owner');
    assert.equal(f.frames.size, 0);
});
