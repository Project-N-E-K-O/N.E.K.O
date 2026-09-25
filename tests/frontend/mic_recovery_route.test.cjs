const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../static/app/app-audio-capture.js'), 'utf8');
const websocketSource = fs.readFileSync(path.join(__dirname, '../../static/app/app-websocket.js'), 'utf8');
const stateSource = fs.readFileSync(path.join(__dirname, '../../static/app/app-state.js'), 'utf8');

async function automaticRecoveryFixture(buffering = true) {
    const env = loadCapture(true);
    env.installMicrophone();
    env.loadWebsocket();
    env.S.voiceSessionEpoch = 12;
    env.window.setMicMuted(false);
    await env.window.startMicCapture();
    const detail = { recovery_id: 1, session_epoch: 12,
        lease_generation: env.S.voiceInputCurrentLeaseGeneration, route_generation: 7, buffering };
    env.status('ASR_RECOVERY_STARTED', detail);
    return { ...env, detail };
}

test('automatic recovery preserves PCM only with explicit backend buffering and retires unmute timer', async () => {
    for (const buffering of [true, false]) {
        const env = await automaticRecoveryFixture(buffering);
        assert.equal(env.S.asrAutomaticRecovery.state, 'recovering');
        assert.equal(env.recoveryTimers().length, 0);
        env.sendFrame();
        assert.equal(env.frames.length, buffering ? 1 : 0);
        env.status('ASR_RECOVERY_READY', env.detail);
        assert.equal(env.S.isMicMuted, false);
        assert.equal(env.S.isRecording, true);
        env.sendFrame();
        assert.equal(env.frames.length, buffering ? 2 : 1);
    }
});

test('automatic recovery rejects stale identities, duplicate completion, and old blocked teardown', async () => {
    const env = await automaticRecoveryFixture();
    const second = { ...env.detail, recovery_id: 2 };
    env.status('ASR_RECOVERY_STARTED', second);
    for (const stale of [{ recovery_id: 1 }, { session_epoch: 11 }, { lease_generation: -1 }, { route_generation: 6 }]) {
        for (const code of ['ASR_RECOVERY_READY', 'ASR_RECOVERY_FAILED', 'ASR_TURN_INCOMPLETE']) {
            env.status(code, { ...second, ...stale });
        }
        env.status('ASR_LIFECYCLE_STATE', { ...second, ...stale, state: 'blocked' });
        assert.equal(env.S.asrAutomaticRecovery.state, 'recovering');
        assert.equal(env.S.isRecording, true);
    }
    env.status('ASR_RECOVERY_FAILED', second);
    env.status('ASR_RECOVERY_READY', second);
    assert.equal(env.S.asrAutomaticRecovery.state, 'failed');
    env.sendFrame();
    assert.equal(env.frames.length, 0);
    let stopped = 0;
    env.window.stopMicCapture = () => { stopped += 1; };
    env.status('ASR_LIFECYCLE_STATE', { ...second, state: 'blocked' });
    assert.equal(stopped, 1, 'current failed operation still performs fatal teardown');
});

test('incomplete notice is distinct from restored connectivity and deduplicated', async () => {
    const env = await automaticRecoveryFixture();
    const count = env.messages.length;
    env.status('ASR_TURN_INCOMPLETE', env.detail);
    env.status('ASR_TURN_INCOMPLETE', env.detail);
    assert.equal(env.messages.length, count + 1);
    assert.match(env.messages.at(-1), /previous sentence/);
    env.status('ASR_RECOVERY_READY', env.detail);
    assert.equal(env.S.asrAutomaticRecovery.incomplete, true);
    assert.equal(env.S.asrAutomaticRecovery.state, 'ready');
    env.status('ASR_LIFECYCLE_STATE', { ...env.detail, state: 'blocked' });
    assert.equal(env.S.isRecording, true, 'late failure cannot override successful operation');
});

test('mute, stop and game takeover retire automatic recovery without reacquiring microphone', async () => {
    for (const operation of ['mute', 'stop', 'game']) {
        const env = await automaticRecoveryFixture();
        if (operation === 'mute') env.window.setMicMuted(true);
        if (operation === 'stop') env.window.stopRecording({ notifyServer: false });
        if (operation === 'game') {
            env.S.gameVoiceSttGateActive = true;
            env.window.appAudioCapture.canUploadOrdinaryMicFrame();
        }
        const count = env.messages.length;
        env.status('ASR_RECOVERY_READY', env.detail);
        env.status('ASR_RECOVERY_FAILED', env.detail);
        assert.equal(env.messages.length, count);
        if (operation === 'mute') assert.equal(env.S.isMicMuted, true);
        if (operation === 'stop') assert.equal(env.S.isRecording, false);
        assert.notEqual(env.S.asrAutomaticRecovery?.state, 'ready');
    }
});

test('unsigned automatic statuses and old socket messages cannot affect active recording', async () => {
    const env = await automaticRecoveryFixture();
    for (const key of ['session_epoch', 'lease_generation', 'recovery_id']) {
        const unsigned = { ...env.detail };
        delete unsigned[key];
        env.status('ASR_RECOVERY_READY', unsigned);
        assert.equal(env.S.asrAutomaticRecovery.state, 'recovering');
    }
    const oldSocket = env.S.socket;
    env.S.socket = { readyState: 1, send() {} };
    oldSocket.onmessage({ data: JSON.stringify({ type: 'status', message: JSON.stringify({
        code: 'ASR_RECOVERY_READY', details: env.detail
    }) }) });
    assert.equal(env.S.asrAutomaticRecovery.state, 'recovering');
});

test('all supported locales contain the incomplete-turn recovery message', () => {
    for (const locale of ['en', 'ja', 'ko', 'zh-CN', 'zh-TW', 'ru', 'pt', 'es']) {
        const content = JSON.parse(fs.readFileSync(path.join(__dirname, `../../static/locales/${locale}.json`), 'utf8'));
        assert.ok(content.microphone.voiceInputTurnIncomplete.length > 10);
    }
});

function loadCapture(active, enabled = active) {
    const timers = new Map();
    const listeners = new Map();
    const messages = [];
    const controls = [];
    const frames = [];
    const send = data => typeof data === 'string' ? controls.push(JSON.parse(data)) : frames.push(data);
    let timerId = 0;
    let S = {
        isRecording: true, isMicMuted: true, independentAsrActive: active,
        independentAsrEnabled: enabled, voiceInputLifecycleState: 'off',
        socket: { readyState: 1, send },
    };
    const window = {
        appState: S, appConst: {}, appUtils: {},
        addEventListener(type, listener) {
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(listener);
        },
        dispatchEvent(event) { for (const listener of listeners.get(event.type) || []) listener(event); },
        showStatusToast(message) { messages.push(message); },
        location: { protocol: 'http:', host: 'localhost:48911' },
        lanlan_config: { lanlan_name: '' },
        t: key => key,
    };
    class FakeWebSocket {
        static OPEN = 1;
        constructor(url) { this.url = url; this.readyState = 1; }
        send(data) { send(data); }
    }
    const context = {
        window, console, navigator: {}, WebSocket: FakeWebSocket, Blob,
        CustomEvent: class { constructor(type, init) { this.type = type; Object.assign(this, init); } },
        document: {
            getElementById: id => id === 'status-toast' ? {} : null,
            documentElement: { setAttribute() {} },
            querySelectorAll: () => [],
        },
        setTimeout(callback, delay) {
            const id = ++timerId;
            timers.set(id, { callback() { timers.delete(id); callback(); }, delay });
            return id;
        },
        clearTimeout(id) { timers.delete(id); },
    };
    vm.runInNewContext(stateSource, context);
    Object.assign(window.appState, S);
    S = window.appState;
    vm.runInNewContext(source, context);
    timers.clear(); // Module startup UI timers are outside this test's scope.
    return {
        window, S, messages, controls, frames, timers,
        installMicrophone() {
            const node = extra => Object.assign({ connect() {}, disconnect() {} }, extra);
            class FakeAudioContext {
                constructor() {
                    this.state = 'running'; this.sampleRate = 48000;
                    this.audioWorklet = { addModule: async () => {} };
                }
                createMediaStreamSource() { return node(); }
                createGain() { return node({ gain: { value: 1 } }); }
                createAnalyser() { return node(); }
                async close() { this.state = 'closed'; }
                async resume() { this.state = 'running'; }
            }
            class FakeMediaStream {
                constructor() { this.track = { label: 'test mic', enabled: true, readyState: 'live', stop() { this.readyState = 'ended'; } }; }
                getTracks() { return [this.track]; }
                getAudioTracks() { return this.getTracks(); }
            }
            context.AudioContext = window.AudioContext = FakeAudioContext;
            context.MediaStream = FakeMediaStream;
            context.AudioWorkletNode = class {
                constructor() { this.port = { onmessage: null, postMessage() {} }; }
                connect() {}
                disconnect() {}
            };
            context.fetch = async () => ({ ok: true, json: async () => ({}) });
            context.navigator.mediaDevices = {
                getUserMedia: async () => new FakeMediaStream(),
                enumerateDevices: async () => [],
            };
        },
        sendFrame() { S.workletNode.port.onmessage({ data: [100, -100] }); },
        recoveryTimers: () => [...timers.values()].filter(timer => timer.delay === 4000),
        emit: type => window.dispatchEvent({ type }),
        loadWebsocket() {
            vm.runInNewContext(websocketSource, context);
            window.appWebSocket.connectWebSocket();
        },
        status(code, details = {}) {
            S.socket.onmessage({ data: JSON.stringify({
                type: 'status', message: JSON.stringify({ code, details }),
            }) });
        },
    };
}

for (const entry of ['toggleMicMute', 'setMicMuted']) {
    test(`${entry}: native voice does not await independent ASR, even if next-session setting is enabled`, () => {
        const env = loadCapture(false, true);
        env.S.voiceInputRecoveryState = 'failed';
        env.window[entry](false);
        assert.equal(env.S.isMicMuted, false);
        assert.equal(env.S.voiceInputRecoveryState, 'idle');
        assert.equal(env.recoveryTimers().length, 0);
        assert.equal(env.messages.length, 0);
        assert.equal(env.window.appAudioCapture.canUploadOrdinaryMicFrame(), true);
        assert.equal(env.controls.at(-1).hard_muted, false);
        env.emit('voice-input-recovery-failed');
        assert.equal(env.S.voiceInputRecoveryState, 'idle');
        assert.equal(env.window.appAudioCapture.canUploadOrdinaryMicFrame(), true);
    });
}

test('active independent route waits even if its next-session setting is disabled', () => {
    const env = loadCapture(true, false);
    env.window.setMicMuted(false);
    assert.equal(env.S.voiceInputRecoveryState, 'recovering');
    assert.equal(env.recoveryTimers().length, 1);
    assert.equal(env.window.appAudioCapture.canUploadOrdinaryMicFrame(), false);
    env.window.dispatchEvent({
        type: 'voice-input-recovery-ready',
        detail: { lease_generation: env.S.voiceInputRecoveryLeaseGeneration },
    });
    assert.equal(env.S.voiceInputRecoveryState, 'ready');
    assert.equal(env.recoveryTimers().length, 0);
    assert.equal(env.window.appAudioCapture.canUploadOrdinaryMicFrame(), true);
});

test('stale recovery identity cannot complete the current recovery cycle', () => {
    const env = loadCapture(true);
    env.S.voiceSessionEpoch = 12;
    env.window.setMicMuted(false);
    env.window.dispatchEvent({
        type: 'voice-input-recovery-ready',
        detail: { session_epoch: 11, lease_generation: env.S.voiceInputRecoveryLeaseGeneration },
    });
    assert.equal(env.S.voiceInputRecoveryState, 'recovering');
    env.window.dispatchEvent({
        type: 'voice-input-recovery-ready',
        detail: { session_epoch: 12, lease_generation: env.S.voiceInputRecoveryLeaseGeneration },
    });
    assert.equal(env.S.voiceInputRecoveryState, 'ready');
});

test('independent timeout blocks upload; remuting cancels the next recovery', () => {
    const env = loadCapture(true);
    env.window.setMicMuted(false);
    env.recoveryTimers()[0].callback();
    assert.equal(env.S.voiceInputRecoveryState, 'timed_out');
    assert.equal(env.window.appAudioCapture.canUploadOrdinaryMicFrame(), false);
    env.window.setMicMuted(true);
    env.window.setMicMuted(false);
    const pending = env.recoveryTimers()[0].callback;
    env.window.setMicMuted(true);
    pending();
    env.emit('voice-input-recovery-ready');
    assert.equal(env.S.voiceInputRecoveryState, 'idle');
    assert.equal(env.recoveryTimers().length, 0);
    assert.equal(env.window.appAudioCapture.canUploadOrdinaryMicFrame(), false);
});

test('late READY resumes upload after the UI deadline, but stale identities do not', () => {
    const env = loadCapture(true);
    env.S.voiceSessionEpoch = 12;
    env.window.setMicMuted(false);
    env.recoveryTimers()[0].callback();
    assert.equal(env.window.appAudioCapture.canUploadOrdinaryMicFrame(), false);
    const current = {
        session_epoch: 12,
        lease_generation: env.controls.at(-1).lease_generation,
        generation: env.S.voiceInputRecoveryGeneration,
    };
    for (const stale of [
        { session_epoch: 11 }, { lease_generation: current.lease_generation - 1 },
        { generation: current.generation - 1 },
    ]) {
        env.window.dispatchEvent({ type: 'voice-input-recovery-ready', detail: { ...current, ...stale } });
        assert.equal(env.S.voiceInputRecoveryState, 'timed_out');
        assert.equal(env.window.appAudioCapture.canUploadOrdinaryMicFrame(), false);
    }
    env.window.dispatchEvent({ type: 'voice-input-recovery-ready', detail: current });
    assert.equal(env.S.voiceInputRecoveryState, 'ready');
    assert.equal(env.window.appAudioCapture.canUploadOrdinaryMicFrame(), true);
    assert.equal(env.messages.at(-1), '语音识别已恢复');
});

for (const timedOut of [false, true]) {
    test(`terminal backend failure rejects subsequent READY (timedOut=${timedOut})`, () => {
        const env = loadCapture(true);
        env.S.voiceSessionEpoch = 12;
        env.window.setMicMuted(false);
        if (timedOut) env.recoveryTimers()[0].callback();
        const lease = env.S.voiceInputRecoveryLeaseGeneration;
        env.window.dispatchEvent({ type: 'voice-input-recovery-failed', detail: { session_epoch: 11, lease_generation: lease } });
        assert.equal(env.S.voiceInputRecoveryState, timedOut ? 'timed_out' : 'recovering');
        env.window.dispatchEvent({ type: 'voice-input-recovery-failed', detail: { session_epoch: 12 } });
        assert.equal(env.S.voiceInputRecoveryState, timedOut ? 'timed_out' : 'recovering');
        env.window.dispatchEvent({ type: 'voice-input-recovery-failed', detail: { session_epoch: 12, lease_generation: lease - 1 } });
        assert.equal(env.S.voiceInputRecoveryState, timedOut ? 'timed_out' : 'recovering');
        env.window.dispatchEvent({ type: 'voice-input-recovery-failed', detail: { session_epoch: 12, lease_generation: lease } });
        env.emit('voice-input-recovery-ready');
        assert.equal(env.S.voiceInputRecoveryState, 'failed');
        assert.equal(env.window.appAudioCapture.canUploadOrdinaryMicFrame(), false);
        assert.equal(env.S.voiceInputRecoveryTimer, null);
    });

    test(`reconnect replay binds recovery to the sent lease (timedOut=${timedOut})`, () => {
        const env = loadCapture(true);
        env.window.setMicMuted(false);
        env.window.setMicMuted(true);
        const previousLease = env.controls.at(-1).lease_generation;
        assert.ok(previousLease > 0);
        env.S.socket.readyState = 3;
        env.window.setMicMuted(false);
        assert.equal(env.S.voiceInputRecoveryLeaseGeneration, null);
        assert.equal(env.controls.at(-1).lease_generation, previousLease);
        env.window.dispatchEvent({ type: 'voice-input-recovery-ready', detail: { lease_generation: previousLease } });
        assert.equal(env.S.voiceInputRecoveryState, 'recovering');
        if (timedOut) env.recoveryTimers()[0].callback();
        env.S.socket = { readyState: 1, send: text => env.controls.push(JSON.parse(text)) };
        env.window.dispatchEvent({ type: 'voice-input-socket-open', detail: { socket: env.S.socket } });
        assert.equal(env.controls.at(-1).lease_generation, 1);
        assert.equal(env.S.voiceInputRecoveryLeaseGeneration, 1);
        assert.equal(env.window.appAudioCapture.canUploadOrdinaryMicFrame(), false);
        env.window.dispatchEvent({ type: 'voice-input-recovery-ready', detail: { lease_generation: 1 } });
        assert.equal(env.window.appAudioCapture.canUploadOrdinaryMicFrame(), true);
    });
}

test('a deduplicated unmute uses the already-sent lease generation', () => {
    const env = loadCapture(true);
    env.window.setMicMuted(false);
    const sentCount = env.controls.length;
    const lease = env.controls.at(-1).lease_generation;
    env.window.setMicMuted(false);
    assert.equal(env.controls.length, sentCount);
    assert.equal(env.S.voiceInputRecoveryLeaseGeneration, lease);
    env.window.dispatchEvent({ type: 'voice-input-recovery-ready', detail: { lease_generation: lease } });
    assert.equal(env.window.appAudioCapture.canUploadOrdinaryMicFrame(), true);
});

test('ASR status updates routing but only the first activation displays its toast', () => {
    const env = loadCapture(false);
    env.loadWebsocket();
    env.status('ASR_INDEPENDENT_READY', { provider: 'test', session_epoch: 12 });
    assert.equal(env.S.independentAsrActive, true);
    assert.equal(env.messages.length, 1);
    env.S.voiceInputRouteBlocked = true;
    env.status('ASR_INDEPENDENT_READY', { provider: 'test', session_epoch: 12 });
    assert.equal(env.S.voiceInputRouteBlocked, false);
    assert.equal(env.messages.length, 1);
    env.window.setMicMuted(false);
    env.recoveryTimers()[0].callback();
    const count = env.messages.length;
    env.status('ASR_INDEPENDENT_READY', { provider: 'test', session_epoch: 12 });
    assert.equal(env.messages.length, count);
    env.status('VOICE_INPUT_READY', { session_epoch: 12, lease_generation: env.controls.at(-1).lease_generation });
    assert.equal(env.S.voiceInputRecoveryState, 'ready');
    assert.equal(env.messages.at(-1), '语音识别已恢复');
    assert.equal(env.window.appAudioCapture.canUploadOrdinaryMicFrame(), true);
});

test('READY and FAILED without the current lease identity cannot finish recovery', () => {
    const env = loadCapture(true);
    env.S.voiceSessionEpoch = 12;
    env.window.setMicMuted(false);
    const lease = env.S.voiceInputRecoveryLeaseGeneration;
    env.emit('voice-input-recovery-ready');
    env.window.dispatchEvent({
        type: 'voice-input-recovery-ready',
        detail: { session_epoch: 12 },
    });
    env.window.dispatchEvent({
        type: 'voice-input-recovery-failed',
        detail: { session_epoch: 12 },
    });
    assert.equal(env.S.voiceInputRecoveryState, 'recovering');
    assert.equal(env.window.appAudioCapture.canUploadOrdinaryMicFrame(), false);
    env.window.dispatchEvent({
        type: 'voice-input-recovery-ready',
        detail: { session_epoch: 12, lease_generation: lease },
    });
    assert.equal(env.S.voiceInputRecoveryState, 'ready');
});

test('websocket recovery failure requires the current lease generation', () => {
    const env = loadCapture(true);
    env.loadWebsocket();
    env.S.voiceSessionEpoch = 12;
    env.window.setMicMuted(false);
    const lease = env.controls.at(-1).lease_generation;
    env.status('VOICE_INPUT_RECOVERY_FAILED', { session_epoch: 12 });
    assert.equal(env.S.voiceInputRecoveryState, 'recovering');
    env.status('VOICE_INPUT_RECOVERY_FAILED', { session_epoch: 12, lease_generation: lease });
    assert.equal(env.S.voiceInputRecoveryState, 'failed');
    assert.equal(env.window.appAudioCapture.canUploadOrdinaryMicFrame(), false);
});

for (const state of ['recovering', 'failed', 'timed_out']) {
    test(`${state}: stopping retires recovery and a new session actually sends PCM`, async () => {
        const env = loadCapture(true);
        env.installMicrophone();
        env.S.voiceSessionEpoch = 12;
        env.window.setMicMuted(false);
        const oldGeneration = env.S.voiceInputRecoveryGeneration;
        const oldTimer = env.recoveryTimers()[0];
        if (state === 'timed_out') oldTimer.callback();
        if (state === 'failed') env.window.dispatchEvent({
            type: 'voice-input-recovery-failed',
            detail: { session_epoch: 12, lease_generation: env.S.voiceInputRecoveryLeaseGeneration },
        });
        assert.equal(env.S.voiceInputRecoveryState, state);
        env.window.stopRecording({ notifyServer: false });
        assert.equal(env.S.voiceInputRecoveryState, 'idle');
        assert.ok(env.S.voiceInputRecoveryGeneration > oldGeneration);
        assert.equal(env.S.voiceInputRecoverySessionEpoch, null);
        assert.equal(env.S.voiceInputRecoveryLeaseGeneration, null);
        assert.equal(env.recoveryTimers().length, 0);
        const owner = env.window.claimSessionStart('audio', () => {}, () => {});
        env.S.voiceSessionEpoch = 13;
        env.S.independentAsrActive = true;
        assert.equal(await env.window.startMicCapture(), true);
        env.window.releaseSessionStart(owner);
        oldTimer.callback(); // Even a callback already queued before clearTimeout is stale.
        assert.equal(env.S.voiceInputRecoveryState, 'idle');
        env.sendFrame();
        assert.equal(env.frames.length, 1);
        assert.equal(env.frames[0].byteLength, 12);
    });
}

test('stop before recording commits clears recovery and cancels an in-flight microphone start', async () => {
    const env = loadCapture(true);
    env.installMicrophone();
    env.window.setMicMuted(false);
    let release;
    env.window.ensureAudioPlayerContext = () => new Promise(resolve => { release = resolve; });
    env.S.isRecording = false;
    const pending = env.window.startMicCapture();
    env.window.stopRecording({ notifyServer: false });
    assert.equal(env.S.voiceInputRecoveryState, 'idle');
    assert.equal(env.recoveryTimers().length, 0);
    release();
    assert.equal(await pending, false);
    assert.equal(env.S.isRecording, false);
    assert.equal(env.frames.length, 0);
});

test('only a newly claimed audio session resets recovery, before displaced cleanup runs', () => {
    const env = loadCapture(true);
    const firstOwner = env.window.claimSessionStart('audio', () => {}, () => {
        assert.equal(env.window.sessionStartIsCurrent(firstOwner), false);
        assert.equal(env.S.voiceInputRecoveryState, 'idle');
    });
    env.window.setMicMuted(false);
    const generation = env.S.voiceInputRecoveryGeneration;
    env.window.claimSessionStart('audio', () => {}, () => {});
    assert.ok(env.S.voiceInputRecoveryGeneration > generation);
    assert.equal(env.S.voiceInputRecoveryLeaseGeneration, null);
    env.window.setMicMuted(false);
    const nextGeneration = env.S.voiceInputRecoveryGeneration;
    env.window.claimSessionStart('text', () => {}, () => {});
    assert.equal(env.S.voiceInputRecoveryState, 'recovering');
    assert.equal(env.S.voiceInputRecoveryGeneration, nextGeneration);
});

test('old session READY and FAILED cannot complete a new recovery even with a reused lease', () => {
    const env = loadCapture(true);
    env.S.voiceSessionEpoch = 12;
    env.window.setMicMuted(false);
    const oldLease = env.S.voiceInputRecoveryLeaseGeneration;
    env.window.stopRecording({ notifyServer: false });
    env.window.claimSessionStart('audio', () => {}, () => {});
    env.S.isRecording = true;
    env.S.independentAsrActive = true;
    env.S.voiceSessionEpoch = 13;
    env.window.setMicMuted(false);
    // Reconnect may reuse a lease generation: session identity must still fence it.
    env.S.voiceInputRecoveryLeaseGeneration = oldLease;
    for (const type of ['voice-input-recovery-ready', 'voice-input-recovery-failed']) {
        env.window.dispatchEvent({ type, detail: { session_epoch: 12, lease_generation: oldLease } });
        assert.equal(env.S.voiceInputRecoveryState, 'recovering');
    }
    env.window.dispatchEvent({
        type: 'voice-input-recovery-ready', detail: { session_epoch: 13, lease_generation: oldLease },
    });
    assert.equal(env.S.voiceInputRecoveryState, 'ready');
});

test('same-session microphone replacement preserves recovery until the matching READY', async () => {
    const env = loadCapture(true);
    env.installMicrophone();
    env.window.setMicMuted(false);
    const generation = env.S.voiceInputRecoveryGeneration;
    assert.equal(await env.window.startMicCapture(), true);
    const previousWorklet = env.S.workletNode;
    const selection = env.window.selectMicrophone(null);
    for (let i = 0; i < 30 && ![...env.timers.values()].some(timer => timer.delay === 500); i++) await Promise.resolve();
    const delay = [...env.timers.values()].find(timer => timer.delay === 500);
    assert.ok(delay, 'device change reached its restart delay');
    delay.callback();
    await selection;
    assert.notEqual(env.S.workletNode, previousWorklet);
    assert.equal(env.S.voiceInputRecoveryGeneration, generation);
    assert.equal(env.S.voiceInputRecoveryState, 'recovering');
    env.sendFrame();
    assert.equal(env.frames.length, 0);
    env.window.dispatchEvent({
        type: 'voice-input-recovery-ready', detail: { lease_generation: env.S.voiceInputRecoveryLeaseGeneration },
    });
    env.sendFrame();
    assert.equal(env.frames.length, 1);
});

test('game-STT pipeline repair preserves the same session recovery gate', async () => {
    const env = loadCapture(true);
    env.installMicrophone();
    env.window.setMicMuted(false);
    const generation = env.S.voiceInputRecoveryGeneration;
    env.S.gameVoiceSttGateActive = true;
    env.window.stopGameVoiceSttGate(); // No ordinary pipeline yet: invokes the real repair path.
    for (let i = 0; i < 30 && !env.S.workletNode; i++) await Promise.resolve();
    assert.ok(env.S.workletNode);
    assert.equal(env.S.voiceInputRecoveryGeneration, generation);
    assert.equal(env.S.voiceInputRecoveryState, 'recovering');
    env.sendFrame();
    assert.equal(env.frames.length, 0);
});

test('a failed cold microphone start leaves the new session recovery state retired', async () => {
    const env = loadCapture(true);
    env.S.voiceInputRecoveryState = 'failed';
    env.S.isRecording = false;
    env.window.claimSessionStart('audio', () => {}, () => {});
    const error = new Error('audio playback initialization rejected');
    env.window.ensureAudioPlayerContext = async () => { throw error; };
    await assert.rejects(env.window.startMicCapture(), candidate => candidate === error);
    assert.equal(env.S.isRecording, false);
    assert.equal(env.S.voiceInputRecoveryState, 'idle');
    assert.equal(env.recoveryTimers().length, 0);
});

test('a superseded microphone start failing late cannot retire the new owner recovery', async () => {
    const env = loadCapture(true);
    env.installMicrophone();
    let rejectOld;
    env.window.ensureAudioPlayerContext = () => new Promise((resolve, reject) => { rejectOld = reject; });
    const oldStart = env.window.startMicCapture();
    const oldRejection = assert.rejects(oldStart, /old playback setup failed/);
    delete env.window.ensureAudioPlayerContext;
    env.window.claimSessionStart('audio', () => {}, () => {});
    assert.equal(await env.window.startMicCapture(), true);
    env.window.setMicMuted(false);
    const generation = env.S.voiceInputRecoveryGeneration;
    const worklet = env.S.workletNode;
    rejectOld(new Error('old playback setup failed'));
    await oldRejection;
    assert.equal(env.S.workletNode, worklet);
    assert.equal(env.S.isRecording, true);
    assert.equal(env.S.voiceInputRecoveryGeneration, generation);
    assert.equal(env.S.voiceInputRecoveryState, 'recovering');
    env.sendFrame();
    assert.equal(env.frames.length, 0);
});
