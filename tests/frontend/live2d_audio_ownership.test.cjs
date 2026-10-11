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
            { code: 'ERR_ASSERTION', message: 'cancel before waiting for configuration\n\ntrue !== false\n' });
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
