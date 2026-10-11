const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fixture(options = {}) {
    const frames = new Map();
    let nextFrame = 0;
    let owner = null;
    let opening = 0;
    let modelType = options.modelType || 'live2d';
    const morphWrites = [];
    const bridge = {
        beginLipSync(token) { owner = token; opening = 0; return true; },
        setMouth(value, token) {
            if (token !== owner) return false;
            opening = value;
            return true;
        },
        endLipSync(token) {
            if (token !== owner) return false;
            owner = null;
            opening = 0;
            return true;
        },
    };
    const namespace = {
        shouldGuideAudioDriveMouth: () => true,
        clamp: (value, low, high) => Math.max(low, Math.min(high, value)),
        ...options.namespace,
    };
    const window = {
        __YuiGuideDirector: namespace,
        LanLan1: bridge,
        requestAnimationFrame(fn) { frames.set(++nextFrame, fn); return nextFrame; },
        cancelAnimationFrame(id) { frames.delete(id); },
    };
    const context = vm.createContext({ window, performance, Uint8Array, console,
        _getActiveModelType: () => modelType });
    if (options.realBridge) {
        // Preserve a separate manager sink before the real LanLan1 bridge replaces its methods.
        window.live2dManager = { ...bridge, currentModel: {} };
        window.mmdManager = { expression: { setMorphWeight(id, value) {
            morphWrites.push([id, value]); opening = value;
        } } };
        const init = fs.readFileSync(path.join(__dirname, '../../static/live2d/live2d-init.js'), 'utf8');
        const start = init.indexOf('let _live2dLipSyncOwner');
        const end = init.indexOf('async function cleanupVRMResources', start);
        assert.ok(start >= 0 && end > start);
        vm.runInContext(init.slice(start, end), context);
    }
    if (options.legacy) {
        delete bridge.beginLipSync;
        delete bridge.endLipSync;
    }
    const queueSourcePath = path.join(__dirname, '../../static/tutorial/yui-guide/director/voice-queue.js');
    vm.runInContext(fs.readFileSync(queueSourcePath, 'utf8'), context, { filename: queueSourcePath });
    const queue = new namespace.YuiGuideVoiceQueue();
    const analyser = {
        fftSize: 64, quiet: false,
        getByteTimeDomainData(data) { data.fill(this.quiet ? 128 : 150); },
        disconnect() {},
    };
    return { queue, analyser, bridge, window, frames, morphWrites, context,
        setModelType(value) { modelType = value; },
        state: () => ({ owner, opening }),
        sample(session) {
            const fn = frames.get(session.animationFrameId);
            frames.delete(session.animationFrameId);
            assert.ok(fn);
            fn(100);
        },
    };
}

test('guide pauses keep mouth ownership and stop releases only its own speech', () => {
    const f = fixture();
    const session = f.queue.startGuideMouthMotion('guide', { analyser: f.analyser });
    f.sample(session);
    assert.ok(f.state().opening > 0);
    const owner = f.state().owner;
    f.analyser.quiet = true;
    for (let i = 0; i < 10; i++) f.sample(session);
    assert.equal(f.state().opening, 0);
    assert.equal(f.state().owner, owner);
    f.queue.stopGuideMouthMotion(session);
    assert.equal(f.state().owner, null);
    assert.equal(f.frames.size, 0);
});

test('late guide sample and cleanup cannot close a newer assistant speech', () => {
    const f = fixture();
    const session = f.queue.startGuideMouthMotion('guide', { analyser: f.analyser });
    const lateSample = f.frames.get(session.animationFrameId);
    f.frames.delete(session.animationFrameId);
    const assistant = {};
    f.bridge.beginLipSync(assistant);
    f.bridge.setMouth(0.7, assistant);
    lateSample(200);
    assert.equal(f.state().opening, 0.7);
    f.queue.stopGuideMouthMotion(session);
    assert.equal(f.state().owner, assistant);
    assert.equal(f.state().opening, 0.7);
    lateSample(300);
    assert.equal(f.frames.size, 0);
});

test('late cleanup of old guide preserves the replacement guide', () => {
    const f = fixture();
    const old = f.queue.startGuideMouthMotion('old', { analyser: f.analyser });
    const current = f.queue.startGuideMouthMotion('new', { analyser: f.analyser });
    f.sample(current);
    const before = f.state();
    f.queue.stopGuideMouthMotion(old);
    assert.equal(f.queue.currentMouthMotionSession, current);
    assert.deepEqual(f.state(), before);
    f.queue.stopGuideMouthMotion(current);
});

test('frame startup failure releases the acquired guide owner', () => {
    const f = fixture();
    f.window.requestAnimationFrame = () => { throw new Error('frame unavailable'); };
    assert.equal(f.queue.startGuideMouthMotion('guide', { analyser: f.analyser }), null);
    assert.equal(f.state().owner, null);
    assert.equal(f.queue.currentMouthMotionSession, null);
});

for (const legacy of [false, true]) {
    test(`real MMD bridge retains guide mouth animation, silence and cleanup (legacy=${legacy})`, () => {
        const f = fixture({ realBridge: true, modelType: 'mmd', legacy });
        const session = f.queue.startGuideMouthMotion('guide', { analyser: f.analyser });
        assert.ok(session, 'refusing Live2D ownership must not disable MMD animation');
        assert.equal(session.mouthOwner, undefined);
        f.sample(session);
        assert.ok(f.state().opening > 0);
        assert.ok(f.morphWrites.some(([id, value]) => id === 'あ' && value > 0));
        f.analyser.quiet = true;
        for (let i = 0; i < 10; i++) f.sample(session);
        assert.equal(f.state().opening, 0);
        f.queue.stopGuideMouthMotion(session);
        assert.equal(f.frames.size, 0);
        assert.equal(f.queue.currentMouthMotionSession, null);
    });
}

test('late MMD guide frame and cleanup preserve the replacement guide', () => {
    const f = fixture({ realBridge: true, modelType: 'mmd' });
    const old = f.queue.startGuideMouthMotion('old', { analyser: f.analyser });
    assert.ok(old);
    const late = f.frames.get(old.animationFrameId);
    const current = f.queue.startGuideMouthMotion('new', { analyser: f.analyser });
    f.sample(current);
    const opening = f.state().opening;
    const writes = f.morphWrites.length;
    late(200); f.queue.stopGuideMouthMotion(old);
    assert.equal(f.queue.currentMouthMotionSession, current);
    assert.equal(f.state().opening, opening);
    assert.equal(f.morphWrites.length, writes);
    f.queue.stopGuideMouthMotion(current);
});

test('MMD frame startup failure closes the mouth and cleans analyser', () => {
    const f = fixture({ realBridge: true, modelType: 'mmd' });
    let disconnected = 0;
    f.analyser.disconnect = () => { disconnected++; };
    f.window.requestAnimationFrame = () => { throw new Error('frame unavailable'); };
    assert.equal(f.queue.startGuideMouthMotion('guide', { analyser: f.analyser }), null);
    assert.equal(disconnected, 1);
    assert.equal(f.state().opening, 0);
    assert.equal(f.queue.currentMouthMotionSession, null);
});

test('a legacy MMD guide cannot overwrite Live2D speech after switching model type', () => {
    const f = fixture({ realBridge: true, modelType: 'mmd' });
    const old = f.queue.startGuideMouthMotion('old', { analyser: f.analyser });
    assert.ok(old);
    f.setModelType('live2d');
    const currentOwner = {};
    assert.equal(f.bridge.beginLipSync(currentOwner), true);
    f.bridge.setMouth(0.7, currentOwner);
    f.sample(old); f.queue.stopGuideMouthMotion(old);
    assert.equal(f.state().owner, currentOwner);
    assert.equal(f.state().opening, 0.7);
    f.bridge.endLipSync(currentOwner);
});

test('real Live2D bridge still acquires and releases guide ownership', () => {
    const f = fixture({ realBridge: true });
    const session = f.queue.startGuideMouthMotion('guide', { analyser: f.analyser });
    assert.ok(session.mouthOwner);
    assert.equal(f.state().owner, session.mouthOwner);
    f.sample(session); assert.ok(f.state().opening > 0);
    f.queue.stopGuideMouthMotion(session);
    assert.equal(f.state().owner, null); assert.equal(f.state().opening, 0);
});

function playbackFixture(mode, failStart = false) {
    let signalStarted;
    const started = new Promise(resolve => { signalStarted = resolve; });
    const f = fixture({ realBridge: true, modelType: 'mmd', namespace: {
        resumeKnownAudioContexts: async () => {}, estimateSpeechDurationMs: () => 100,
        fetchWithTimeout: async () => ({ ok: true, arrayBuffer: async () => new ArrayBuffer(8) }),
    } });
    const timers = new Map();
    let nextTimer = 0, disconnected = 0, source;
    f.window.setTimeout = fn => { timers.set(++nextTimer, fn); return nextTimer; };
    f.window.clearTimeout = id => timers.delete(id);
    const node = () => ({ connect() {}, disconnect() { disconnected++; } });
    f.analyser.connect = () => {};
    f.analyser.disconnect = () => { disconnected++; };
    f.window.lanlanAudioContext = {
        state: 'running', currentTime: 0, destination: {},
        createAnalyser: () => f.analyser,
        createMediaElementSource: node,
        decodeAudioData: async () => ({ duration: 1 }),
        createBufferSource() {
            source = { ...node(), stop() {}, start() {
                if (failStart) throw new Error('controlled playback failure');
                signalStarted(source);
            } };
            return source;
        },
    };
    f.context.Audio = class {
        constructor() { source = this; }
        play() {
            if (failStart) return Promise.reject(new Error('controlled playback failure'));
            signalStarted(this); return Promise.resolve();
        }
        pause() {} removeAttribute() {} load() {}
    };
    const play = () => mode === 'element'
        ? f.queue.playPreviewAudio('/controlled.wav', 0, 0, { voiceKey: 'guide' })
        : f.queue.playPreviewAudioThroughContext('/controlled.wav', 0, 0, { voiceKey: 'guide' });
    return { ...f, started, timers, play, disconnected: () => disconnected };
}

for (const mode of ['element', 'context']) {
    test(`real MMD bridge receives ${mode} playback samples and ends cleanly`, async () => {
        const f = playbackFixture(mode);
        const playing = f.play();
        const source = await f.started;
        const session = f.queue.currentMouthMotionSession;
        assert.ok(session, 'the actual playback entry must create a mouth session');
        f.sample(session); assert.ok(f.morphWrites.some(([, value]) => value > 0));
        source.onended(); assert.equal(await playing, true);
        assert.equal(f.state().opening, 0);
        assert.equal(f.queue.currentMouthMotionSession, null);
        assert.equal(f.frames.size, 0); assert.equal(f.timers.size, 0);
        assert.ok(f.disconnected() > 0);
    });

    test(`${mode} playback startup failure cleans the fallback mouth session`, async () => {
        const f = playbackFixture(mode, true);
        await assert.rejects(f.play(), /controlled playback failure/);
        assert.equal(f.state().opening, 0);
        assert.equal(f.queue.currentMouthMotionSession, null);
        assert.equal(f.frames.size, 0); assert.equal(f.timers.size, 0);
        assert.ok(f.disconnected() > 0);
    });
}
