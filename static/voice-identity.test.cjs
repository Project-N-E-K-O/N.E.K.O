'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'js/voice_identity.js'), 'utf8');
const stylesheet = fs.readFileSync(path.join(__dirname, 'css/voice_identity.css'), 'utf8');
const darkModeStylesheet = fs.readFileSync(path.join(__dirname, 'css/dark-mode.css'), 'utf8');
const template = fs.readFileSync(
    path.join(__dirname, '../templates/voice_identity.html'),
    'utf8',
);

const API_ROOT = '/api/voice-identity';
const PCM_CONTENT_TYPE = 'audio/pcm;format=pcm_s16le;rate=16000;channels=1';
const PROFILE_HEADER = 'X-Voice-Identity-Profile';
const TARGET_SAMPLE_RATE = 16000;
const RECORDING_MS = 4000;
const CAPTURE_TIMEOUT_MS = RECORDING_MS + 1000;
const WINDOW_CLOSE_START_WAIT_MS = 500;
const TARGET_SAMPLES = TARGET_SAMPLE_RATE * RECORDING_MS / 1000;
const CHUNK_SAMPLES = 512;
const FULL_AUDIO_CHUNKS = Math.ceil(TARGET_SAMPLES / CHUNK_SAMPLES);

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

function jsonResponse(payload, { ok = true, status = 200 } = {}) {
    return {
        ok,
        status,
        async json() {
            return payload;
        },
    };
}

class MockHeaders {
    constructor(initial = {}) {
        this.values = new Map();
        if (initial instanceof MockHeaders) {
            initial.values.forEach((value, key) => this.values.set(key, value));
            return;
        }
        Object.entries(initial).forEach(([key, value]) => this.set(key, value));
    }

    set(key, value) {
        this.values.set(String(key).toLowerCase(), String(value));
    }

    get(key) {
        return this.values.get(String(key).toLowerCase());
    }

    has(key) {
        return this.values.has(String(key).toLowerCase());
    }
}

function createElement() {
    const listeners = new Map();
    const classes = new Set();
    const element = {
        textContent: '',
        hidden: false,
        disabled: false,
        checked: false,
        addEventListener(type, listener) {
            listeners.set(type, listener);
        },
        emit(type) {
            return listeners.get(type)?.({ type, target: element });
        },
        classList: {
            add(...names) {
                names.forEach(name => classes.add(name));
            },
            toggle(name, force) {
                const enabled = force === undefined ? !classes.has(name) : Boolean(force);
                if (enabled) classes.add(name);
                else classes.delete(name);
                return enabled;
            },
            contains(name) {
                return classes.has(name);
            },
        },
    };
    Object.defineProperty(element, 'className', {
        get() {
            return Array.from(classes).join(' ');
        },
        set(value) {
            classes.clear();
            String(value).split(/\s+/).filter(Boolean).forEach(name => classes.add(name));
        },
    });
    return element;
}

function createHarness({
    initialProfile = false,
    initialRequested = false,
    statusGate,
    startGate,
    mediaGate,
    mediaError,
    audioChunks = FULL_AUDIO_CHUNKS,
    manualAudio = false,
    profileError,
    profileTransportErrorAfterCommit = false,
    showConfirm,
    nativeConfirm = true,
    webCryptoAvailable = true,
    initialEffectiveReason = null,
} = {}) {
    const elementIds = [
        'voice-identity-status-dot',
        'voice-identity-profile-status',
        'voice-identity-enrollment',
        'voice-identity-capture-status',
        'voice-identity-capture-label',
        'voice-identity-timer',
        'voice-identity-message',
        'voice-identity-start',
        'voice-identity-cancel',
        'voice-identity-profile-controls',
        'voice-identity-reenroll',
        'voice-identity-delete',
        'voice-identity-filter',
    ];
    const elements = new Map(elementIds.map(id => [id, createElement()]));
    const documentListeners = new Map();
    const windowListeners = new Map();
    const fetchCalls = [];
    const mediaStreams = [];
    const workletModules = [];
    let processor = null;
    let mediaRequests = 0;
    let serverProfile = initialProfile;
    let serverProfileGeneration = initialProfile ? 'profile-0' : null;
    let serverRequested = initialRequested;
    let enrollmentId = null;
    let statusRequestCount = 0;
    let timerId = 0;
    let audioContext = null;

    const statusPayload = () => ({
        requested_enabled: serverRequested,
        effective_enabled: serverProfile && serverRequested,
        effective_reason: serverProfile
            ? (serverRequested ? 'ready' : 'disabled')
            : (enrollmentId ? 'enrollment_active' : (initialEffectiveReason || 'no_profile')),
        has_profile: serverProfile,
        enrollment: enrollmentId
            ? { enrollment_id: enrollmentId, expires_at: 123.5 }
            : null,
        profile_generation: serverProfileGeneration,
        runtime_mode: 'enforce',
    });

    async function defaultRoute(call) {
        if (call.url === '/api/config/page_config') {
            return jsonResponse({ autostart_csrf_token: 'csrf-token' });
        }
        if (call.url === `${API_ROOT}/status`) {
            statusRequestCount += 1;
            if (statusGate && statusRequestCount === 1) return statusGate.promise;
            return jsonResponse(statusPayload());
        }
        if (call.url === `${API_ROOT}/enrollment/start`) {
            if (startGate) await startGate.promise;
            enrollmentId = 'enrollment-1';
            return jsonResponse(statusPayload());
        }
        if (call.url === `${API_ROOT}/enrollment/profile`) {
            enrollmentId = null;
            if (profileError) {
                return jsonResponse(
                    { error_code: profileError },
                    { ok: false, status: 422 },
                );
            }
            serverProfile = true;
            serverProfileGeneration = call.options.headers.get(PROFILE_HEADER);
            serverRequested = initialProfile ? serverRequested : true;
            if (profileTransportErrorAfterCommit) {
                throw new Error('profile_response_lost');
            }
            return jsonResponse(statusPayload());
        }
        if (call.url === `${API_ROOT}/enrollment/cancel`) {
            enrollmentId = null;
            return jsonResponse(statusPayload());
        }
        if (call.url === `${API_ROOT}/filter`) {
            serverRequested = JSON.parse(call.options.body).enabled;
            return jsonResponse(statusPayload());
        }
        if (call.url === `${API_ROOT}/profile`) {
            serverProfile = false;
            serverProfileGeneration = null;
            serverRequested = false;
            enrollmentId = null;
            return jsonResponse(statusPayload());
        }
        throw new Error(`unexpected request: ${call.options.method || 'GET'} ${call.url}`);
    }

    const document = {
        activeElement: null,
        getElementById(id) {
            return elements.get(id);
        },
        addEventListener(type, listener) {
            documentListeners.set(type, listener);
        },
    };
    elements.forEach(element => {
        element.focus = () => {
            document.activeElement = element;
        };
    });

    class MockAudioContext {
        constructor() {
            audioContext = this;
            this.sampleRate = 48000;
            this.destination = {};
            this.state = 'suspended';
            this.audioWorklet = {
                addModule: async url => {
                    workletModules.push(url);
                },
            };
        }

        createMediaStreamSource() {
            return { connect() {}, disconnect() {} };
        }

        createGain() {
            return { gain: { value: 1 }, connect() {}, disconnect() {} };
        }

        async resume() {
            this.state = 'running';
        }

        async close() {
            this.state = 'closed';
        }
    }

    class MockAudioWorkletNode {
        constructor(context, name, options) {
            assert.equal(name, 'audio-processor');
            assert.equal(options.processorOptions.originalSampleRate, context.sampleRate);
            assert.equal(options.processorOptions.targetSampleRate, 16000);
            this.port = { onmessage: null };
            processor = this;
        }

        connect() {}

        disconnect() {}
    }

    const window = {
        t(key, options) {
            if (key === 'voiceIdentity.recordingSeconds') {
                return `${options.seconds} s`;
            }
            const translations = {
                'voiceIdentity.profileMissing': 'No Owner voice profile enrolled',
                'voiceIdentity.profileReady': 'Owner voice profile is saved and enabled',
                'voiceIdentity.profileSavedDisabled': 'Owner voice profile is saved; filtering is off',
                'voiceIdentity.reasonRuntimeDegraded': 'Voice filtering is unavailable',
                'voiceIdentity.reasonSecureStorageUnavailable': 'Secure storage is unavailable',
                'voiceIdentity.recording': 'Recording...',
                'voiceIdentity.saving': 'Saving...',
                'voiceIdentity.enrollmentComplete': 'Enrollment complete.',
                'voiceIdentity.microphoneDenied': 'Microphone unavailable.',
                'voiceIdentity.requestFailed': 'Request failed.',
                'voiceIdentity.errorInvalidPcm': 'Invalid recording format.',
                'voiceIdentity.errorAudioTooLong': 'Recording is too long.',
                'voiceIdentity.deleteConfirm': 'Delete the profile?',
                'voiceIdentity.delete': 'Delete voice profile',
            };
            return translations[key] || key;
        },
        addEventListener(type, listener) {
            windowListeners.set(type, listener);
        },
        dispatchEvent(event) {
            return windowListeners.get(event.type)?.(event);
        },
        setInterval() {
            timerId += 1;
            return timerId;
        },
        clearInterval() {},
        setTimeout(callback, delay) {
            timerId += 1;
            if (delay === CAPTURE_TIMEOUT_MS) {
                if (!manualAudio) {
                    Promise.resolve().then(() => {
                        for (let index = 0; index < audioChunks; index += 1) {
                            processor?.port.onmessage?.({
                                data: new Int16Array(CHUNK_SAMPLES).fill(1024),
                            });
                        }
                        if (audioChunks < FULL_AUDIO_CHUNKS) callback();
                    });
                }
            } else if (delay === WINDOW_CLOSE_START_WAIT_MS) {
                Promise.resolve().then(callback);
            } else {
                throw new Error(`unmodeled setTimeout delay: ${delay}`);
            }
            return timerId;
        },
        clearTimeout() {},
        AudioContext: MockAudioContext,
        webkitAudioContext: undefined,
        showConfirm,
        confirm: () => nativeConfirm,
        crypto: webCryptoAvailable ? {
            randomUUID: () => 'profile-1',
            getRandomValues(values) {
                values.fill(1);
                return values;
            },
        } : undefined,
    };

    const context = {
        window,
        document,
        navigator: {
            mediaDevices: {
                async getUserMedia() {
                    mediaRequests += 1;
                    if (mediaGate) await mediaGate.promise;
                    if (mediaError) throw mediaError;
                    const track = { stopped: false, stop() { this.stopped = true; } };
                    const stream = { getTracks: () => [track], track };
                    mediaStreams.push(stream);
                    return stream;
                },
            },
        },
        fetch: async (url, options = {}) => {
            const call = { url, options: { ...options, headers: new MockHeaders(options.headers) } };
            fetchCalls.push(call);
            return defaultRoute(call);
        },
        Headers: MockHeaders,
        AudioWorkletNode: MockAudioWorkletNode,
        performance: { now: () => 1000 },
        console: { log() {}, warn() {}, error() {} },
        Uint8Array,
        Int16Array,
        ArrayBuffer,
        Promise,
        Error,
        JSON,
        Math,
    };
    window.window = window;
    window.document = document;
    window.navigator = context.navigator;
    window.fetch = context.fetch;
    window.Headers = MockHeaders;
    window.AudioWorkletNode = MockAudioWorkletNode;
    window.performance = context.performance;

    vm.runInNewContext(source, context, { filename: 'voice_identity.js' });

    return {
        elements,
        fetchCalls,
        mediaStreams,
        workletModules,
        getAudioContext() {
            return audioContext;
        },
        get mediaRequests() {
            return mediaRequests;
        },
        async initialize() {
            await documentListeners.get('DOMContentLoaded')();
        },
        startInitialization() {
            return documentListeners.get('DOMContentLoaded')();
        },
        emit(id, type = 'click') {
            return elements.get(id).emit(type);
        },
        dispatch(type, event = {}) {
            return window.dispatchEvent({ type, ...event });
        },
        beforeClose() {
            return window.nekoBeforeWindowClose();
        },
    };
}

async function flush(turns = 8) {
    for (let index = 0; index < turns; index += 1) {
        await new Promise(resolve => setImmediate(resolve));
    }
}

test('mutation controls stay disabled until CSRF and canonical status resolve', async () => {
    const statusGate = deferred();
    const harness = createHarness({ statusGate });

    const initializing = harness.startInitialization();
    await flush(2);

    assert.equal(harness.elements.get('voice-identity-start').disabled, true);
    statusGate.resolve(jsonResponse({
        requested_enabled: false,
        effective_enabled: false,
        effective_reason: 'no_profile',
        has_profile: false,
        enrollment: null,
        runtime_mode: 'enforce',
    }));
    await initializing;

    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
});

test('one click requests permission, records four seconds, and PUTs exact PCM16', async () => {
    const harness = createHarness();
    await harness.initialize();

    await harness.emit('voice-identity-start');

    const paths = harness.fetchCalls.map(call => call.url);
    assert.deepEqual(paths, [
        '/api/config/page_config',
        `${API_ROOT}/status`,
        `${API_ROOT}/enrollment/start`,
        `${API_ROOT}/enrollment/profile`,
    ]);
    const upload = harness.fetchCalls.at(-1);
    assert.equal(upload.options.method, 'PUT');
    assert.equal(upload.options.body.byteLength, TARGET_SAMPLES * 2);
    assert.equal(upload.options.headers.get('content-type'), PCM_CONTENT_TYPE);
    assert.equal(upload.options.headers.get('x-voice-identity-enrollment'), 'enrollment-1');
    assert.equal(upload.options.headers.get('x-voice-identity-profile'), 'profile-1');
    assert.equal(harness.mediaRequests, 1);
    assert.deepEqual(harness.workletModules, ['/static/audio-processor.js']);
    assert.equal(harness.mediaStreams[0].track.stopped, true);
    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Enrollment complete.');
    assert.equal(harness.elements.get('voice-identity-profile-controls').hidden, false);
});

test('underfilled capture cancels the lease and never uploads partial PCM', async () => {
    const harness = createHarness({ audioChunks: 100 });
    await harness.initialize();

    await harness.emit('voice-identity-start');

    assert.equal(
        harness.fetchCalls.some(call => call.url === `${API_ROOT}/enrollment/profile`),
        false,
    );
    assert.equal(
        harness.fetchCalls.some(call => call.url === `${API_ROOT}/enrollment/cancel`),
        true,
    );
    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Request failed.');
});

test('server rejection for insufficient usable speech stays fail-safe and visible', async () => {
    const harness = createHarness({ profileError: 'speech_too_short' });
    await harness.initialize();

    await harness.emit('voice-identity-start');

    assert.equal(
        harness.fetchCalls.some(call => call.url === `${API_ROOT}/enrollment/profile`),
        true,
    );
    assert.equal(harness.elements.get('voice-identity-profile-controls').hidden, true);
    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Request failed.');
});

test('canonical enrollment audio errors show localized messages', async () => {
    const invalid = createHarness({ profileError: 'invalid_pcm' });
    await invalid.initialize();
    await invalid.emit('voice-identity-start');
    assert.equal(
        invalid.elements.get('voice-identity-message').textContent,
        'Invalid recording format.',
    );

    const tooLong = createHarness({ profileError: 'audio_too_long' });
    await tooLong.initialize();
    await tooLong.emit('voice-identity-start');
    assert.equal(
        tooLong.elements.get('voice-identity-message').textContent,
        'Recording is too long.',
    );
});

test('missing Web Crypto cancels enrollment without attempting an upload', async () => {
    const harness = createHarness({ webCryptoAvailable: false });
    await harness.initialize();

    await harness.emit('voice-identity-start');

    assert.equal(
        harness.fetchCalls.some(call => call.url === `${API_ROOT}/enrollment/profile`),
        false,
    );
    assert.equal(
        harness.fetchCalls.some(call => call.url === `${API_ROOT}/enrollment/cancel`),
        true,
    );
    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Request failed.');
});

test('microphone denial prevents enrollment start and reports a useful error', async () => {
    const denied = new Error('denied');
    denied.name = 'NotAllowedError';
    const harness = createHarness({ mediaError: denied });
    await harness.initialize();

    await harness.emit('voice-identity-start');

    assert.equal(
        harness.fetchCalls.some(call => call.url === `${API_ROOT}/enrollment/start`),
        false,
    );
    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Microphone unavailable.');
});

test('canonical has_profile reveals only switch, re-enroll, and delete controls', async () => {
    const harness = createHarness({ initialProfile: true, initialRequested: true });
    await harness.initialize();

    assert.equal(harness.elements.get('voice-identity-enrollment').hidden, true);
    assert.equal(harness.elements.get('voice-identity-profile-controls').hidden, false);
    assert.equal(harness.elements.get('voice-identity-filter').checked, true);
    assert.equal(harness.elements.get('voice-identity-profile-status').textContent,
        'Owner voice profile is saved and enabled');
    assert.equal(template.includes('voice-identity-record'), false);
    assert.equal(template.includes('step-progress'), false);
});

test('backend degradation reason is preserved when no profile exists', async () => {
    const harness = createHarness({
        initialEffectiveReason: 'secure_storage_unavailable',
    });
    await harness.initialize();

    assert.equal(
        harness.elements.get('voice-identity-profile-status').textContent,
        'Secure storage is unavailable',
    );
    assert.equal(harness.elements.get('voice-identity-start').disabled, true);
});

test('filter toggle sends the requested boolean and adopts canonical state', async () => {
    const harness = createHarness({ initialProfile: true });
    await harness.initialize();
    const filter = harness.elements.get('voice-identity-filter');
    filter.checked = true;

    await harness.emit('voice-identity-filter', 'change');

    const request = harness.fetchCalls.at(-1);
    assert.equal(request.url, `${API_ROOT}/filter`);
    assert.deepEqual(JSON.parse(request.options.body), { enabled: true });
    assert.equal(filter.checked, true);
});

test('re-enrollment hides profile mutations while the new session starts', async () => {
    const startGate = deferred();
    const harness = createHarness({
        initialProfile: true,
        initialRequested: false,
        startGate,
    });
    await harness.initialize();

    const reenrolling = harness.emit('voice-identity-reenroll');
    await flush(2);
    assert.equal(harness.elements.get('voice-identity-profile-controls').hidden, true);
    assert.equal(harness.elements.get('voice-identity-enrollment').hidden, false);
    assert.equal(harness.elements.get('voice-identity-cancel').hidden, false);
    startGate.resolve();
    await reenrolling;

    assert.equal(harness.elements.get('voice-identity-profile-controls').hidden, false);
    assert.equal(harness.elements.get('voice-identity-filter').checked, false);
});

test('re-enrollment recovers a lost response and preserves disabled preference', async () => {
    const harness = createHarness({
        initialProfile: true,
        initialRequested: false,
        profileTransportErrorAfterCommit: true,
    });
    await harness.initialize();

    await harness.emit('voice-identity-reenroll');

    assert.equal(harness.elements.get('voice-identity-profile-controls').hidden, false);
    assert.equal(harness.elements.get('voice-identity-filter').checked, false);
    assert.equal(
        harness.elements.get('voice-identity-message').textContent,
        'Owner voice profile is saved; filtering is off',
    );
});

test('delete confirms, removes the profile, and returns to one-click enrollment', async () => {
    const confirmations = [];
    const harness = createHarness({
        initialProfile: true,
        initialRequested: true,
        showConfirm: async (...args) => {
            confirmations.push(args);
            return true;
        },
    });
    await harness.initialize();

    await harness.emit('voice-identity-delete');

    assert.equal(confirmations.length, 1);
    assert.equal(harness.fetchCalls.at(-1).url, `${API_ROOT}/profile`);
    assert.equal(harness.fetchCalls.at(-1).options.method, 'DELETE');
    assert.equal(harness.elements.get('voice-identity-profile-controls').hidden, true);
    assert.equal(harness.elements.get('voice-identity-start').hidden, false);
});

test('explicit cancel aborts an active capture and releases the server session', async () => {
    const harness = createHarness({ manualAudio: true });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush();
    assert.equal(harness.elements.get('voice-identity-cancel').hidden, false);
    await harness.emit('voice-identity-cancel');
    await enrolling;

    const cancel = harness.fetchCalls.find(call => (
        call.url === `${API_ROOT}/enrollment/cancel`
    ));
    assert.ok(cancel);
    assert.equal(cancel.options.headers.get('x-voice-identity-enrollment'), 'enrollment-1');
    assert.equal(harness.elements.get('voice-identity-start').hidden, false);
});

test('pagehide sends keepalive cancellation and stops microphone resources', async () => {
    const harness = createHarness({ manualAudio: true });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush();
    harness.dispatch('pagehide');
    await flush();
    await enrolling;

    const cancel = harness.fetchCalls.find(call => (
        call.url === `${API_ROOT}/enrollment/cancel`
        && call.options.keepalive === true
    ));
    assert.ok(cancel);
    assert.equal(harness.mediaStreams[0].track.stopped, true);
    assert.equal(harness.getAudioContext().state, 'closed');
});

test('slow enrollment start uses keepalive cancellation after close wait expires', async () => {
    const startGate = deferred();
    const harness = createHarness({ startGate, manualAudio: true });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush();
    await harness.beforeClose();
    startGate.resolve();
    await enrolling;

    const cancel = harness.fetchCalls.find(call => (
        call.url === `${API_ROOT}/enrollment/cancel`
        && call.options.keepalive === true
    ));
    assert.ok(cancel);
});

test('the one-click page keeps complete dark-theme overrides', () => {
    for (const token of [
        '--voice-ink: #e8f5fb',
        '--voice-muted: #afc5d1',
        '--voice-blue-dark: #8edcff',
        '--voice-border: rgba(91, 215, 255, 0.28)',
        '--voice-panel: rgba(27, 39, 48, 0.96)',
        '--voice-danger: #ff8d9b',
        '--voice-focus: #8edcff',
    ]) {
        assert.match(stylesheet, new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
    assert.match(stylesheet, /\[data-theme="dark"\] \.secondary-button/);
    assert.match(stylesheet, /\[data-theme="dark"\] \.danger-button/);
    assert.match(darkModeStylesheet, /html\[data-theme="dark"\]/);
    assert.match(template, /static\/css\/dark-mode\.css/);
});

test('old five-step endpoints and DOM contracts do not return', () => {
    for (const retired of [
        '/enrollment/segment',
        '/enrollment/verify',
        '/enrollment/commit',
        'ready_to_commit',
        'fixedPrompts',
        'voice-identity-record',
        'step-progress',
    ]) {
        assert.equal(source.includes(retired) || template.includes(retired), false, retired);
    }
});
