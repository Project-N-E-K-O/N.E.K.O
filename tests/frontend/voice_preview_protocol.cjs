// Coupled by test_voice_readiness_control.py to actual Core + ASGI handlers.
// Only the microphone, DOM, and IPC transport are controlled here; both product
// frontend controllers receive the real backend results over this JSON pipe.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const readline = require('node:readline');
const assert = require('node:assert/strict');
const { randomUUID } = require('node:crypto');

const requests = new Map();
const timers = new Set();
const input = readline.createInterface({ input: process.stdin });
let sequence = 0;
let closed = false;
let failProtocol;
function setTimer(callback, milliseconds) {
    const timer = setTimeout(() => { timers.delete(timer); callback(); }, milliseconds);
    timers.add(timer);
    return timer;
}
function clearTimer(timer) { timers.delete(timer); clearTimeout(timer); }
input.on('line', line => {
    try {
        const reply = JSON.parse(line);
        const pending = requests.get(reply.id);
        if (!pending) throw new Error('protocol_unknown_reply: ' + reply.id);
        requests.delete(reply.id);
        clearTimer(pending.timer);
        if (reply.error) pending.reject(new Error(reply.error)); else pending.resolve(reply.payload);
    } catch (error) { failProtocol(error); }
});
input.on('close', () => { if (!closed) failProtocol(new Error('protocol_pipe_closed')); });
function rpc(channel, payload) {
    return new Promise((resolve, reject) => {
        if (closed) { reject(new Error('protocol_closed')); return; }
        const id = ++sequence;
        // Core begin may finish a shielded close after its outer budget,
        // taking up to ten seconds. Fail before the owner's thirteen seconds.
        const timer = setTimer(() => {
            requests.delete(id);
            const error = new Error('protocol_rpc_timeout: ' + channel + ' ' + (payload.event || '') + ' id=' + id);
            reject(error);
            failProtocol(error);
        }, 12000);
        requests.set(id, { resolve, reject, timer });
        process.stdout.write(JSON.stringify({ id, channel, payload }) + '\n');
    });
}
function element() {
    return {
        value: '', hidden: false, disabled: false, textContent: '', handlers: {},
        classList: { toggle() {} }, append() {}, appendChild() {}, replaceChildren() {},
        setAttribute() {}, removeAttribute() {},
        addEventListener(name, handler) { this.handlers[name] = handler; },
    };
}
function environment() {
    const elements = new Map();
    const document = {
        readyState: 'complete', documentElement: element(), body: element(),
        createElement: element,
        getElementById(id) { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); },
    };
    const root = {
        document, crypto: { randomUUID }, setTimeout: setTimer, clearTimeout: clearTimer, AbortController,
        Uint8Array, Promise, Date, console,
        location: { origin: 'http://localhost', hostname: 'localhost' },
        addEventListener() {}, removeEventListener() {},
        localStorage: { getItem() { return null; }, setItem() {} },
        navigator: { mediaDevices: {
            enumerateDevices: async () => [{ kind: 'audioinput', deviceId: 'actual-device', label: 'Controlled microphone' }],
            addEventListener() {},
        } },
    };
    root.window = root;
    const context = vm.createContext(root);
    function load(relative) {
        vm.runInContext(fs.readFileSync(path.join(__dirname, '../..', relative), 'utf8'), context, { filename: relative });
    }
    load('static/js/microphone-input.js');
    return { root, elements, load };
}
async function run() {
    const failed = new Promise((_, reject) => { failProtocol = reject; });
    try { await Promise.race([failed, (async () => {
    const main = environment();
    let identity;
    let prepare;
    main.root.nekoVoiceEnrollment = {
        registerCapture: async value => { identity = value; return { accepted: true }; },
        onStopCapture: handler => { prepare = handler; }, onOpen() {},
    };
    const S = { isRecording: true, stream: null };
    let owner;
    const socket = { readyState: 1, send(json) {
        rpc('control', JSON.parse(json))
            .then(details => owner.controlResult(details, socket))
            .catch(failProtocol);
    } };
    S.socket = socket;
    main.load('static/app/app-voice-readiness.js');
    owner = main.root.createVoiceCaptureReadiness(S, async () => { S.isRecording = false; S.stream = null; });
    await owner.register(true);

    const child = environment();
    let prepared;
    child.root.nekoVoiceEnrollment = {
        async prepare({ operationId }) {
            prepared = { operationId, sessionId: identity.sessionId, revision: identity.revision };
            const ack = await prepare(prepared);
            prepared.token = ack.token;
            return { ...ack, operationId };
        },
        async release({ operationId }) {
            assert.equal(operationId, prepared.operationId);
            return prepare({ ...prepared, event: 'release' });
        },
    };
    child.load('static/js/voice-identity-readiness.js');
    let stream = null;
    let readiness;
    let track;
    let microphoneStops = 0;
    const pcm = new ArrayBuffer(48000 * 3 * 2);
    readiness = child.root.createVoiceIdentityReadiness({
        translate: (_key, fallback) => fallback, enrolling: () => false,
        render() {}, cancel() {}, pause() {}, status() {}, error: error => error.message,
        stream: () => stream,
        async microphone() {
            track = { readyState: 'live', label: 'Controlled microphone', getSettings: () => ({ deviceId: 'actual-device' }), stop() { this.readyState = 'ended'; } };
            stream = { getAudioTracks: () => [track], getTracks: () => [track] };
            readiness.receivedStream({ stream, deviceId: 'actual-device', label: track.label, fallback: false });
        },
        stop() { microphoneStops++; if (stream) child.root.nekoMicrophoneInput.stop(stream); stream = null; },
        capture: async () => pcm,
        async request(url, options) {
            if (url === '/resources') return { can_enroll: true, resources: {}, audio_contract: 'owner-campplus-desktop-v1' };
            assert.equal(url, '/audio/check');
            assert.equal(options.body.byteLength, 288000);
            return rpc('audio-check', { headers: options.headers, bytes: options.body.byteLength });
        },
    });
    await readiness.refreshResources();
    await child.elements.get('voice-identity-test').handlers.click();
    process.stdout.write(JSON.stringify({ channel: 'result', payload: {
        canStart: readiness.canStart(), audioContract: readiness.audioContract(),
        ownerBlocked: owner.blocked(), recording: S.isRecording,
        trackEnded: track.readyState === 'ended', microphoneStops,
        message: child.elements.get('voice-identity-test-result').textContent,
    } }) + '\n');
    })()]); } finally {
        closed = true;
        requests.clear();
        timers.forEach(clearTimeout);
        timers.clear();
        input.close();
        process.stdin.pause();
    }
}
run().catch(error => {
    console.error('voice_preview_protocol_failed:', error.stack || error.message);
    process.exitCode = 1;
});
