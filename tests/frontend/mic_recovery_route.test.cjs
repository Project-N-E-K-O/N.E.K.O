const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../static/app/app-audio-capture.js'), 'utf8');

function loadCapture(active, enabled = active) {
    const timers = new Map();
    const listeners = new Map();
    const messages = [];
    const controls = [];
    let timerId = 0;
    const S = {
        isRecording: true, isMicMuted: true, independentAsrActive: active,
        independentAsrEnabled: enabled, voiceInputLifecycleState: 'off',
        socket: { readyState: 1, send: text => controls.push(JSON.parse(text)) },
    };
    const window = {
        appState: S, appConst: {}, appUtils: {},
        addEventListener(type, listener) { listeners.set(type, listener); },
        dispatchEvent(event) { listeners.get(event.type)?.(event); },
        showStatusToast(message) { messages.push(message); },
    };
    const context = {
        window, console, navigator: {}, WebSocket: { OPEN: 1 },
        CustomEvent: class { constructor(type, init) { this.type = type; Object.assign(this, init); } },
        document: {
            getElementById: id => id === 'status-toast' ? {} : null,
            documentElement: { setAttribute() {} },
        },
        setTimeout(callback, delay) { timers.set(++timerId, { callback, delay }); return timerId; },
        clearTimeout(id) { timers.delete(id); },
    };
    vm.runInNewContext(source, context);
    timers.clear(); // Module startup UI timers are outside this test's scope.
    return {
        window, S, messages, controls, timers,
        recoveryTimers: () => [...timers.values()].filter(timer => timer.delay === 32000),
        emit: type => window.dispatchEvent({ type }),
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
    env.emit('voice-input-recovery-ready');
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
    assert.equal(env.S.voiceInputRecoveryState, 'failed');
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

test('stale recovery failure cannot fail a newer lease generation', () => {
    const env = loadCapture(true);
    env.window.setMicMuted(false);
    const lease = env.S.voiceInputRecoveryLeaseGeneration;
    env.window.setMicMuted(true);
    env.window.setMicMuted(false);
    env.window.dispatchEvent({
        type: 'voice-input-recovery-failed',
        detail: { session_epoch: env.S.voiceInputRecoverySessionEpoch, lease_generation: lease },
    });
    assert.equal(env.S.voiceInputRecoveryState, 'recovering');
});

test('reasserting an unmuted state does not start a second recovery', () => {
    const env = loadCapture(true);
    env.window.setMicMuted(false);
    const generation = env.S.voiceInputRecoveryGeneration;
    env.window.setMicMuted(false);
    assert.equal(env.S.voiceInputRecoveryGeneration, generation);
    assert.equal(env.recoveryTimers().length, 1);
});

test('stopping recording cancels pending recovery even when already stopped', () => {
    const env = loadCapture(true);
    env.window.setMicMuted(false);
    env.S.isRecording = false;
    env.window.stopRecording();
    assert.equal(env.S.voiceInputRecoveryState, 'idle');
    assert.equal(env.recoveryTimers().length, 0);
    assert.equal(env.S.voiceInputRecoverySessionEpoch, null);
    assert.equal(env.S.voiceInputRecoveryLeaseGeneration, null);
});

test('socket reconnect during recovery rebinds the expected lease generation', () => {
    const env = loadCapture(true);
    env.window.setMicMuted(false); // advance the old socket's generation scope
    env.window.setMicMuted(true);
    env.S.socket.readyState = 0; // unmute while the socket is down: no lease is sent
    env.window.setMicMuted(false);
    assert.equal(env.S.voiceInputRecoveryState, 'recovering');
    const staleLease = env.S.voiceInputRecoveryLeaseGeneration;
    env.S.socket.readyState = 1;
    env.emit('voice-input-socket-open');
    const sentLease = env.controls.at(-1).lease_generation;
    assert.equal(sentLease, 1);
    assert.notEqual(staleLease, sentLease);
    env.window.dispatchEvent({
        type: 'voice-input-recovery-ready',
        detail: { session_epoch: env.S.voiceInputRecoverySessionEpoch, lease_generation: sentLease },
    });
    assert.equal(env.S.voiceInputRecoveryState, 'ready');
});
