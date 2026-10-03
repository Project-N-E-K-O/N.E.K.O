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
const PCM_CONTENT_TYPE = 'audio/pcm;format=pcm_s16le;rate=48000;channels=1';
const AUDIO_CONTRACT_ID = 'owner-campplus-desktop-v1';
const PROFILE_HEADER = 'X-Voice-Identity-Profile';
const TARGET_SAMPLE_RATE = 48000;
const REFERENCE_RECORDING_MS = 3000;
const VERIFICATION_RECORDING_MS = 5000;
const MINIMUM_RECORDING_MS = 1500;
const REFERENCE_TIMEOUT_MS = REFERENCE_RECORDING_MS + 1000;
const VERIFICATION_TIMEOUT_MS = VERIFICATION_RECORDING_MS + 1000;
const WINDOW_CLOSE_START_WAIT_MS = 500;
const REFERENCE_SAMPLES = TARGET_SAMPLE_RATE * REFERENCE_RECORDING_MS / 1000;
const VERIFICATION_SAMPLES = TARGET_SAMPLE_RATE * VERIFICATION_RECORDING_MS / 1000;
const MINIMUM_SAMPLES = TARGET_SAMPLE_RATE * MINIMUM_RECORDING_MS / 1000;
const CAPTURE_CHUNK_MS = 10;
const CHUNK_SAMPLES = TARGET_SAMPLE_RATE * CAPTURE_CHUNK_MS / 1000;
const FULL_AUDIO_CHUNKS = Math.ceil(VERIFICATION_RECORDING_MS / CAPTURE_CHUNK_MS);

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
    initialEnrollmentNextSegment = null,
    statusGate,
    startGate,
    cancelGate,
    explicitCancelGate,
    mediaGate,
    mediaError,
    selectedMicrophoneId,
    profileStatus = 422,
    audioChunks = FULL_AUDIO_CHUNKS,
    manualAudio = false,
    autoFinish = true,
    autoAdvance = true,
    startResponseErrorAfterCreate = false,
    startResponseErrorAfterAbort = false,
    profileError,
    verificationFailures = 0,
    profileTransportErrorAfterCommit = false,
    statusFailures = 0,
    focusStatusGate,
    statusGates = {},
    segmentGate,
    inconsistentReference = false,
    remainingSeconds = 45,
    promptPaintGate,
    showConfirm,
    nativeConfirm = true,
    webCryptoAvailable = true,
    initialEffectiveReason = null,
    audioContextSampleRate = 48000,
    resumeGate,
    initialStatusError = false,
    pageConfigGates = {},
    segmentInProgressOnce = null,
    cancelCsrfFailureOnce = false,
    startGates = {},
    runtimeMode = 'enforce',
} = {}) {
    const elementIds = [
        'voice-identity-status-dot',
        'voice-identity-profile-status',
        'voice-identity-enrollment',
        'voice-identity-capture-status',
        'voice-identity-step-count',
        'voice-identity-step-title',
        'voice-identity-step-body',
        'voice-identity-prompt',
        'voice-identity-next',
        'voice-identity-capture-label',
        'voice-identity-voice-state',
        'voice-identity-timer',
        'voice-identity-message',
        'voice-identity-start',
        'voice-identity-finish',
        'voice-identity-cancel',
        'voice-identity-profile-controls',
        'voice-identity-profile-actions',
        'voice-identity-reenroll',
        'voice-identity-delete',
        'voice-identity-filter',
        'voice-identity-retry',
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
    let remainingVerificationFailures = verificationFailures;
    let enrollmentId = initialEnrollmentNextSegment ? 'enrollment-1' : null;
    let enrollmentSerial = enrollmentId ? 1 : 0;
    let serverNextSegment = initialEnrollmentNextSegment || 1;
    let remainingInconsistentReferences = inconsistentReference ? 1 : 0;
    let statusRequestCount = 0;
    let pageConfigRequestCount = 0;
    let startRequestCount = 0;
    let pendingSegmentInProgress = segmentInProgressOnce;
    const mediaConstraintCalls = [];
    let timerId = 0;
    const statusTimeouts = new Map();
    let intervalCallback = null;
    let enrollmentLeaseTimeoutCallback = null;
    let promptPaintFrames = 0;
    let fakeNow = 1000;
    const autoFinishDurations = [];
    let audioContext = null;

    const statusPayload = () => ({
        requested_enabled: serverRequested,
        effective_enabled: serverProfile && serverRequested,
        effective_reason: initialEffectiveReason || (serverProfile
            ? (serverRequested ? 'ready' : 'disabled')
            : (enrollmentId ? 'enrollment_active' : 'no_profile')),
        has_profile: serverProfile,
        enrollment: enrollmentId
            ? { enrollment_id: enrollmentId, expires_at: 123.5, remaining_seconds: remainingSeconds, next_segment_index: serverNextSegment }
            : null,
        profile_generation: serverProfileGeneration,
        runtime_mode: runtimeMode,
    });

    async function defaultRoute(call) {
        if (call.url === '/api/config/page_config') {
            pageConfigRequestCount += 1;
            const gate = pageConfigGates[pageConfigRequestCount];
            if (gate) {
                await new Promise((resolve, reject) => {
                    const signal = call.options.signal;
                    const onAbort = () => reject(new Error('aborted'));
                    if (signal?.aborted) return onAbort();
                    signal?.addEventListener('abort', onAbort, { once: true });
                    gate.promise.then(resolve, reject);
                });
            }
            return jsonResponse({ autostart_csrf_token: 'csrf-token' });
        }
        if (call.url === `${API_ROOT}/status`) {
            statusRequestCount += 1;
            if (initialStatusError && statusRequestCount === 1) throw new Error('status_unavailable');
            if (statusGate && statusRequestCount === 1) return statusGate.promise;
            if (focusStatusGate && statusRequestCount === 2) return focusStatusGate.promise;
            if (statusGates[statusRequestCount]) {
                const gate = statusGates[statusRequestCount];
                return new Promise((resolve, reject) => {
                    const signal = call.options.signal;
                    const onAbort = () => reject(new Error('aborted'));
                    if (signal?.aborted) return onAbort();
                    signal?.addEventListener('abort', onAbort, { once: true });
                    gate.promise.then(resolve, reject);
                });
            }
            if (statusFailures > 0 && statusRequestCount > 1) {
                statusFailures -= 1;
                throw new Error('status_transient');
            }
            return jsonResponse(statusPayload());
        }
        if (call.url === `${API_ROOT}/enrollment/start`) {
            if (startGate) await startGate.promise;
            startRequestCount += 1;
            if (!enrollmentId) {
                enrollmentSerial += 1;
                enrollmentId = `enrollment-${enrollmentSerial}`;
            }
            // Session created server-side; the response itself is delayed.
            if (startGates[startRequestCount]) await startGates[startRequestCount].promise;
            if (startResponseErrorAfterAbort) {
                await new Promise((resolve, reject) => {
                    const signal = call.options.signal;
                    const onAbort = () => reject(new Error('start_response_lost'));
                    if (signal?.aborted) return onAbort();
                    signal?.addEventListener('abort', onAbort, { once: true });
                });
            }
            if (startResponseErrorAfterCreate) throw new Error('start_response_lost');
            return jsonResponse(statusPayload());
        }
        if (call.url === `${API_ROOT}/enrollment/segment` || call.url === `${API_ROOT}/enrollment/profile`) {
            const segment = call.options.headers.get('x-voice-identity-segment');
            if (segmentGate && call.url.endsWith('/segment')) {
                await new Promise((resolve, reject) => {
                    const signal = call.options.signal;
                    const onAbort = () => reject(new Error('aborted'));
                    if (signal?.aborted) return onAbort();
                    signal?.addEventListener('abort', onAbort, { once: true });
                    segmentGate.promise.then(resolve, reject);
                });
            }
            if (profileError) return jsonResponse({ error_code: profileError }, { ok: false, status: profileStatus });
            if (pendingSegmentInProgress && call.url.endsWith('/segment')) {
                if (pendingSegmentInProgress === 'accepted') serverNextSegment = Number(segment) + 1;
                pendingSegmentInProgress = null;
                return jsonResponse({ error_code: 'segment_in_progress' }, { ok: false, status: 409 });
            }
            if (segment === '3' && remainingInconsistentReferences > 0) {
                remainingInconsistentReferences -= 1;
                serverNextSegment = 1;
                return jsonResponse({ error_code: 'voice_samples_inconsistent' }, { ok: false, status: 422 });
            }
            if (call.url.endsWith('/profile') || segment === '4') {
                if (segment === '4' && remainingVerificationFailures > 0) {
                    remainingVerificationFailures -= 1;
                    return jsonResponse({
                        ...statusPayload(),
                        verification: { passed: false, match_percent: 31 },
                    });
                }
                enrollmentId = null;
                serverProfile = true;
                serverProfileGeneration = call.options.headers.get(PROFILE_HEADER);
                serverRequested = initialProfile ? serverRequested : true;
                if (profileTransportErrorAfterCommit) throw new Error('profile_response_lost');
            } else {
                serverNextSegment = Number(segment) + 1;
            }
            return jsonResponse(statusPayload());
        }
        if (call.url === `${API_ROOT}/enrollment/cancel`) {
            if (cancelCsrfFailureOnce && !call.options.keepalive) {
                cancelCsrfFailureOnce = false;
                return jsonResponse({ error_code: 'csrf_validation_failed' }, { ok: false, status: 403 });
            }
            if (cancelGate && call.options.keepalive) await cancelGate.promise;
            if (explicitCancelGate && !call.options.keepalive) {
                await new Promise((resolve, reject) => {
                    const signal = call.options.signal;
                    const onAbort = () => reject(new Error('aborted'));
                    if (signal?.aborted) return onAbort();
                    signal?.addEventListener('abort', onAbort, { once: true });
                    explicitCancelGate.promise.then(resolve, reject);
                });
            }
            if (call.options.headers.get('x-voice-identity-enrollment') === enrollmentId) {
                enrollmentId = null;
            }
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
        querySelectorAll(selector) {
            return selector === '#voice-identity-progress span' ? [createElement(), createElement(), createElement()] : [];
        },
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
        constructor(options) {
            assert.equal(options?.sampleRate, TARGET_SAMPLE_RATE);
            audioContext = this;
            this.sampleRate = audioContextSampleRate;
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
            if (resumeGate) await resumeGate.promise;
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
            assert.equal(options.processorOptions.targetSampleRate, TARGET_SAMPLE_RATE);
            this.originalSampleRate = context.sampleRate;
            this.targetSampleRate = TARGET_SAMPLE_RATE;
            this.inputSamples = 0;
            this.outputSamples = 0;
            this.pendingOutput = [];
            const node = this;
            this.port = {
                onmessage: null,
                postMessage(message) {
                    if (message && message.type === 'flush') {
                        const pcmData = Int16Array.from(node.pendingOutput);
                        node.pendingOutput = [];
                        Promise.resolve().then(() => this.onmessage?.({
                            data: { type: 'flush_complete', pcmData },
                        }));
                    }
                },
            };
            processor = this;
        }

        emitInput(input) {
            const samples = input instanceof Int16Array ? input : new Int16Array(input);
            for (const sample of samples) {
                const outputBefore = Math.floor(this.inputSamples * this.targetSampleRate / this.originalSampleRate);
                this.inputSamples += 1;
                const outputAfter = Math.floor(this.inputSamples * this.targetSampleRate / this.originalSampleRate);
                for (let index = outputBefore; index < outputAfter; index += 1) {
                    this.pendingOutput.push(sample);
                    this.outputSamples += 1;
                    if (this.pendingOutput.length === CHUNK_SAMPLES) {
                        const pcmData = Int16Array.from(this.pendingOutput);
                        this.pendingOutput = [];
                        this.port.onmessage?.({ data: pcmData });
                    }
                }
            }
        }

        connect() {}

        disconnect() {}
    }

    const window = {
        __voiceIdentityTestAutoAdvance: autoAdvance,
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
                'voiceIdentity.featureDisabled': 'Voice identity is turned off',
                'voiceIdentity.recording': 'Recording...',
                'voiceIdentity.voiceWaiting': 'Waiting for speech',
                'voiceIdentity.voiceDetected': 'Speech detected',
                'voiceIdentity.voiceQuiet': 'Voice is quiet',
                'voiceIdentity.saving': 'Saving...',
                'voiceIdentity.enrollmentComplete': 'Enrollment complete.',
                'voiceIdentity.microphoneDenied': 'Microphone unavailable.',
                'voiceIdentity.requestFailed': 'Request failed.',
                'voiceIdentity.errorInvalidPcm': 'Invalid recording format.',
                'voiceIdentity.errorAudioTooLong': 'Recording is too long.',
                'voiceIdentity.errorSpeechTooShort': 'Not enough speech detected.',
                'voiceIdentity.errorSilence': 'No speech detected.',
                'voiceIdentity.errorSevereClipping': 'Recording is distorted.',
                'voiceIdentity.errorIncompleteCapture': 'Recording did not finish.',
                'voiceIdentity.errorInsufficientTime': 'Not enough time remains for the next recording.',
                'voiceIdentity.finishTooSoon': 'Keep speaking for about 1.5 seconds before saving.',
                'voiceIdentity.errorModelUnavailable': 'Voice model unavailable.',
                'voiceIdentity.errorAudioProcessingUnavailable': 'Audio processing unavailable.',
                'voiceIdentity.errorSecureStorageUnavailable': 'Secure storage unavailable.',
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
        setInterval(callback) {
            timerId += 1;
            intervalCallback = callback;
            return timerId;
        },
        clearInterval() {},
        setTimeout(callback, delay) {
            timerId += 1;
            if (delay === REFERENCE_TIMEOUT_MS || delay === VERIFICATION_TIMEOUT_MS) {
                if (!manualAudio) {
                    Promise.resolve().then(() => {
                        const recordingMs = delay === REFERENCE_TIMEOUT_MS
                            ? REFERENCE_RECORDING_MS : VERIFICATION_RECORDING_MS;
                        const targetChunks = Math.ceil(recordingMs / CAPTURE_CHUNK_MS);
                        const chunksToEmit = Math.min(audioChunks, targetChunks);
                        const inputChunkSamples = Math.round(
                            audioContextSampleRate * CAPTURE_CHUNK_MS / 1000,
                        );
                        for (let index = 0; index < chunksToEmit; index += 1) {
                            processor?.emitInput(new Int16Array(inputChunkSamples).fill(1024));
                        }
                        if (audioChunks < targetChunks) callback();
                        else if (autoFinish) {
                            autoFinishDurations.push(
                                delay === REFERENCE_TIMEOUT_MS
                                    ? REFERENCE_RECORDING_MS : VERIFICATION_RECORDING_MS,
                            );
                            fakeNow += delay === REFERENCE_TIMEOUT_MS
                                ? REFERENCE_RECORDING_MS : VERIFICATION_RECORDING_MS;
                            intervalCallback?.();
                        }
                    });
                }
            } else if (delay === 400) {
                // Successful flush acknowledgement clears this watchdog.
            } else if (delay === 1000 || delay === 5000) {
                // Both status and prompt-paint watchdogs are driven explicitly.
                statusTimeouts.set(timerId, callback);
            } else if (delay === 0) {
                Promise.resolve().then(callback);
            } else if (delay === WINDOW_CLOSE_START_WAIT_MS) {
                Promise.resolve().then(callback);
            } else if (delay > VERIFICATION_TIMEOUT_MS) {
                enrollmentLeaseTimeoutCallback = callback;
            } else {
                throw new Error(`unmodeled setTimeout delay: ${delay}`);
            }
            return timerId;
        },
        clearTimeout(id) { statusTimeouts.delete(id); },
        requestAnimationFrame(callback) {
            promptPaintFrames += 1;
            if (promptPaintGate && promptPaintFrames === 2) {
                promptPaintGate.promise.then(callback);
            } else {
                Promise.resolve().then(callback);
            }
        },
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
                async getUserMedia(constraints) {
                    mediaRequests += 1;
                    mediaConstraintCalls.push(constraints);
                    const gate = typeof mediaGate === 'function' ? mediaGate(mediaRequests) : mediaGate;
                    if (gate) await gate.promise;
                    const requestError = Array.isArray(mediaError)
                        ? mediaError[mediaRequests - 1] : mediaError;
                    if (requestError) throw requestError;
                    const track = { enabled: true, stopped: false, stop() { this.stopped = true; } };
                    const stream = { getTracks: () => [track], track };
                    mediaStreams.push(stream);
                    return stream;
                },
            },
        },
        localStorage: selectedMicrophoneId
            ? { getItem: key => key === 'neko_selected_microphone' ? selectedMicrophoneId : null }
            : undefined,
        fetch: async (url, options = {}) => {
            const call = { url, options: { ...options, headers: new MockHeaders(options.headers) } };
            fetchCalls.push(call);
            return defaultRoute(call);
        },
        Headers: MockHeaders,
        AudioWorkletNode: MockAudioWorkletNode,
        performance: { now: () => fakeNow },
        console: { log() {}, warn() {}, error() {} },
        Uint8Array,
        Int16Array,
        ArrayBuffer,
        AbortController,
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
        autoFinishDurations,
        getAudioContext() {
            return audioContext;
        },
        get mediaRequests() {
            return mediaRequests;
        },
        get serverEnrollmentId() {
            return enrollmentId;
        },
        fireStatusTimeouts() {
            const callbacks = [...statusTimeouts.values()];
            statusTimeouts.clear();
            callbacks.forEach(callback => callback());
        },
        mediaConstraintCalls,
        emitAudio(samples) {
            const chunk = samples instanceof Int16Array
                ? samples
                : new Int16Array(samples).fill(1024);
            processor?.emitInput(chunk);
        },
        expireEnrollmentLease() {
            serverNextSegment = 1;
            enrollmentId = null;
            enrollmentLeaseTimeoutCallback?.();
        },
        advanceTime(ms) {
            fakeNow += ms;
        },
        tickCaptureClock() {
            intervalCallback?.();
        },
        advanceServerSegment() {
            serverNextSegment += 1;
        },
        fireEnrollmentLeaseTimer() {
            enrollmentLeaseTimeoutCallback?.();
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
    assert.equal(harness.elements.get('voice-identity-cancel').hidden, true);
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

test('one click records three reference segments and one five-second verification segment', async () => {
    const harness = createHarness();
    await harness.initialize();

    await harness.emit('voice-identity-start');

    const paths = harness.fetchCalls.map(call => call.url);
    assert.deepEqual(paths, [
        '/api/config/page_config',
        `${API_ROOT}/status`,
        `${API_ROOT}/enrollment/start`,
        `${API_ROOT}/enrollment/segment`,
        `${API_ROOT}/status`,
        `${API_ROOT}/enrollment/segment`,
        `${API_ROOT}/status`,
        `${API_ROOT}/enrollment/segment`,
        `${API_ROOT}/status`,
        `${API_ROOT}/enrollment/segment`,
        `${API_ROOT}/status`,
    ]);
    assert.deepEqual(harness.autoFinishDurations, [
        REFERENCE_RECORDING_MS,
        REFERENCE_RECORDING_MS,
        REFERENCE_RECORDING_MS,
        VERIFICATION_RECORDING_MS,
    ]);
    const upload = harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).at(-1);
    assert.equal(upload.options.method, 'PUT');
    assert.equal(upload.options.body.byteLength, VERIFICATION_SAMPLES * 2);
    assert.equal(upload.options.headers.get('content-type'), PCM_CONTENT_TYPE);
    assert.equal(upload.options.headers.get('x-voice-identity-enrollment'), 'enrollment-1');
    assert.equal(upload.options.headers.get('x-voice-identity-profile'), 'profile-1');
    assert.equal(upload.options.headers.get('x-voice-identity-segment'), '4');
    assert.equal(upload.options.headers.get('x-voice-audio-contract'), AUDIO_CONTRACT_ID);
    const segmentUploads = harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`);
    assert.deepEqual(segmentUploads.slice(0, 3).map(call => call.options.body.byteLength), [
        REFERENCE_SAMPLES * 2,
        REFERENCE_SAMPLES * 2,
        REFERENCE_SAMPLES * 2,
    ]);
    assert.equal(harness.mediaRequests, 1);
    for (const call of harness.mediaConstraintCalls) {
        assert.equal(call.audio.noiseSuppression, false);
        assert.equal(call.audio.echoCancellation, true);
        assert.equal(call.audio.autoGainControl, true);
        assert.equal(call.audio.channelCount, 1);
    }
    assert.deepEqual(harness.workletModules, ['/static/audio-processor.js?v=voice-identity-flush-v1']);
    assert.equal(harness.mediaStreams[0].track.stopped, true);
    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Enrollment complete.');
    assert.equal(harness.elements.get('voice-identity-enrollment').hidden, false);
    assert.equal(harness.elements.get('voice-identity-profile-controls').hidden, false);
});

test('the first prompt is visible before recording starts', async () => {
    const harness = createHarness();
    await harness.initialize();

    assert.equal(harness.elements.get('voice-identity-prompt').hidden, false);
    assert.equal(harness.elements.get('voice-identity-prompt').textContent, '今天我想和你分享一件趣事。');
});

test('a browser 44.1 kHz context is resampled to the enrollment contract', async () => {
    const harness = createHarness({ audioContextSampleRate: 44100 });
    await harness.initialize();

    await harness.emit('voice-identity-start');

    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Enrollment complete.');
    const upload = harness.fetchCalls.find(call => call.url === `${API_ROOT}/enrollment/segment`);
    assert.equal(upload.options.headers.get('content-type'), PCM_CONTENT_TYPE);
    assert.equal(upload.options.body.byteLength, REFERENCE_SAMPLES * Int16Array.BYTES_PER_ELEMENT);
});

test('clearing an active enrollment resets local segment state', async () => {
    const harness = createHarness({ initialProfile: true, initialEnrollmentNextSegment: 3 });
    await harness.initialize();

    assert.equal(harness.elements.get('voice-identity-profile-controls').hidden, true);
    assert.equal(harness.elements.get('voice-identity-cancel').hidden, false);
    await harness.emit('voice-identity-cancel');
    await flush(2);
    assert.equal(harness.elements.get('voice-identity-profile-controls').hidden, false);
    assert.equal(harness.elements.get('voice-identity-profile-actions').hidden, false);
    assert.equal(harness.elements.get('voice-identity-start').hidden, true);
    assert.equal(harness.elements.get('voice-identity-cancel').hidden, true);
});

test('a lost enrollment-start response adopts the active server session', async () => {
    const harness = createHarness({ startResponseErrorAfterCreate: true });
    await harness.initialize();

    await harness.emit('voice-identity-start');

    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Enrollment complete.');
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/cancel`).length, 0);
});

test('accepted segment waits for explicit next-segment action', async () => {
    const harness = createHarness({ autoAdvance: false });
    await harness.initialize();
    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length, 1);
    assert.equal(harness.elements.get('voice-identity-next').hidden, false);
    assert.equal(harness.elements.get('voice-identity-prompt').textContent, '窗外的光线正在慢慢变化。');
    assert.equal(harness.elements.get('voice-identity-step-count').textContent, '第 2 / 4 段');
    assert.equal(harness.elements.get('voice-identity-voice-state').textContent, 'Waiting for speech');
    assert.equal(harness.mediaRequests, 1);
    assert.equal(harness.mediaStreams[0].track.enabled, false);
    await harness.emit('voice-identity-next');
    await flush(4);
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length, 2);
    await harness.emit('voice-identity-cancel');
    await enrolling;
});

test('enrollment lease expiry releases the saved-segment wait', async () => {
    const harness = createHarness({ autoAdvance: false });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length, 1);
    assert.equal(harness.elements.get('voice-identity-next').hidden, false);

    harness.expireEnrollmentLease();
    await enrolling;
    await flush(2);

    assert.equal(harness.elements.get('voice-identity-message').textContent, '本次录入已过期，请重新开始。');
    assert.equal(harness.elements.get('voice-identity-next').hidden, true);
    assert.equal(harness.mediaStreams[0].track.stopped, true);
});

test('a stalled cancellation after the local lease timer cannot keep the workflow busy', async () => {
    const explicitCancelGate = deferred();
    const harness = createHarness({ autoAdvance: false, explicitCancelGate });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    assert.equal(harness.elements.get('voice-identity-next').hidden, false);

    harness.fireEnrollmentLeaseTimer();
    await flush(6);
    const cancel = harness.fetchCalls.find(call => call.url === `${API_ROOT}/enrollment/cancel`);
    assert.ok(cancel);
    harness.fireStatusTimeouts();
    await enrolling;
    await flush(2);

    assert.equal(cancel.options.signal.aborted, true);
    assert.equal(harness.elements.get('voice-identity-message').textContent, '本次录入已过期，请重新开始。');
    assert.equal(harness.mediaStreams[0].track.stopped, true);
    assert.equal(harness.elements.get('voice-identity-next').hidden, true);
});

test('the bounded expiry cancellation also bounds its CSRF token refresh', async () => {
    const stalledPageConfig = deferred();
    const harness = createHarness({
        autoAdvance: false,
        cancelCsrfFailureOnce: true,
        pageConfigGates: { 2: stalledPageConfig },
    });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    harness.fireEnrollmentLeaseTimer();
    await flush(8);
    const pageConfig = harness.fetchCalls.filter(call => call.url === '/api/config/page_config').at(-1);
    assert.equal(harness.fetchCalls.filter(call => call.url === '/api/config/page_config').length, 2);
    harness.fireStatusTimeouts();
    await enrolling;
    await flush(2);

    assert.equal(pageConfig.options.signal.aborted, true);
    assert.equal(harness.elements.get('voice-identity-message').textContent, '本次录入已过期，请重新开始。');
    assert.equal(harness.mediaStreams[0].track.stopped, true);
});

test('a BFCache restore supersedes an in-flight connection retry', async () => {
    const stalledRetryStatus = deferred();
    const harness = createHarness({ initialStatusError: true, statusGates: { 2: stalledRetryStatus } });
    await harness.initialize();
    assert.equal(harness.elements.get('voice-identity-retry').hidden, false);

    const retrying = harness.emit('voice-identity-retry');
    await flush(4);
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/status`).length, 2);
    await harness.dispatch('pageshow', { persisted: true });
    // The superseded retry's status read then times out (or aborts) late.
    harness.fireStatusTimeouts();
    await retrying;
    await flush(2);

    assert.equal(harness.elements.get('voice-identity-retry').hidden, true);
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
    assert.equal(harness.elements.get('voice-identity-message').textContent, '');
});

test('a retry superseded while loading its token does not read status afterwards', async () => {
    const slowPageConfig = deferred();
    const harness = createHarness({ initialStatusError: true, pageConfigGates: { 2: slowPageConfig } });
    await harness.initialize();

    const retrying = harness.emit('voice-identity-retry');
    await flush(2);
    await harness.dispatch('pageshow', { persisted: true });
    await flush(2);
    const statusReadsAfterRestore = harness.fetchCalls.filter(call => call.url === `${API_ROOT}/status`).length;
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);

    slowPageConfig.resolve();
    await retrying;
    await flush(4);

    assert.equal(
        harness.fetchCalls.filter(call => call.url === `${API_ROOT}/status`).length,
        statusReadsAfterRestore,
    );
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
});

test('a stalled Cancel releases the page and the next start replaces the unconfirmed session', async () => {
    const explicitCancelGate = deferred();
    const harness = createHarness({ autoAdvance: false, explicitCancelGate });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    assert.equal(harness.elements.get('voice-identity-next').hidden, false);
    harness.emit('voice-identity-cancel');
    await enrolling;
    await flush(4);
    assert.equal(harness.elements.get('voice-identity-start').disabled, true);

    harness.fireStatusTimeouts();
    await flush(8);
    const stalledCancel = harness.fetchCalls.find(call => call.url === `${API_ROOT}/enrollment/cancel`);
    assert.equal(stalledCancel.options.signal.aborted, true);
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
    assert.equal(harness.elements.get('voice-identity-cancel').disabled, false);
    assert.equal(harness.serverEnrollmentId, 'enrollment-1');

    explicitCancelGate.resolve();
    const callsBeforeRestart = harness.fetchCalls.length;
    const restarting = harness.emit('voice-identity-start');
    await flush(8);
    const restartCalls = harness.fetchCalls.slice(callsBeforeRestart);
    const cancelIndex = restartCalls.findIndex(call => call.url === `${API_ROOT}/enrollment/cancel`);
    const startIndex = restartCalls.findIndex(call => call.url === `${API_ROOT}/enrollment/start`);
    assert.ok(cancelIndex >= 0);
    assert.ok(startIndex > cancelIndex);
    assert.equal(restartCalls[cancelIndex].options.headers.get('x-voice-identity-enrollment'), 'enrollment-1');
    assert.equal(harness.serverEnrollmentId, 'enrollment-2');
    await harness.emit('voice-identity-cancel');
    await restarting;
});

test('error cleanup after a futile lease bounds its cancellation request', async () => {
    const explicitCancelGate = deferred();
    const harness = createHarness({ remainingSeconds: 9, explicitCancelGate });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(12);
    assert.ok(harness.fetchCalls.some(call => call.url === `${API_ROOT}/enrollment/cancel`));
    harness.fireStatusTimeouts();
    await enrolling;
    await flush(4);

    const cancel = harness.fetchCalls.find(call => call.url === `${API_ROOT}/enrollment/cancel`);
    assert.equal(cancel.options.signal.aborted, true);
    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Not enough time remains for the next recording.');
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
});

test('cancelling during the in-progress reconciliation does not install a new segment wait', async () => {
    const inProgressStatus = deferred();
    const harness = createHarness({
        segmentInProgressOnce: 'pending',
        autoAdvance: false,
        statusGates: { 2: inProgressStatus },
    });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(8);
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/status`).length, 2);
    harness.emit('voice-identity-cancel');
    await flush(8);

    assert.equal(harness.elements.get('voice-identity-next').hidden, true);
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
    assert.equal(harness.serverEnrollmentId, null);
    await enrolling;
});

test('a late cancellation recovery after a restore timeout clears the timeout alert', async () => {
    const cancelGate = deferred();
    const harness = createHarness({ initialEnrollmentNextSegment: 1, cancelGate, manualAudio: true });
    await harness.initialize();

    const closing = harness.beforeClose();
    await flush(3);
    const restoring = harness.dispatch('pageshow', { persisted: true });
    await flush(4);
    harness.fireStatusTimeouts();
    await restoring;
    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Request failed.');

    cancelGate.resolve();
    await closing;
    await flush(6);
    assert.equal(harness.elements.get('voice-identity-message').textContent, '');
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
});

test('continuous quiet speech does not flicker back to waiting', async () => {
    const harness = createHarness({ manualAudio: true, autoAdvance: false });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    const quietChunk = () => harness.emitAudio(new Int16Array(CHUNK_SAMPLES).fill(164));
    quietChunk();
    await flush(2);
    const voiceState = harness.elements.get('voice-identity-voice-state');
    assert.equal(voiceState.textContent, 'Voice is quiet');
    for (let step = 0; step < 10; step += 1) {
        harness.advanceTime(100);
        quietChunk();
        harness.tickCaptureClock();
        await flush(1);
        assert.equal(voiceState.textContent, 'Voice is quiet');
    }

    harness.advanceTime(900);
    harness.tickCaptureClock();
    await flush(1);
    assert.equal(voiceState.textContent, 'Waiting for speech');
    await harness.emit('voice-identity-cancel');
    await enrolling;
});

test('a stale start response is cancelled within the cancellation timeout', async () => {
    const startGate = deferred();
    const explicitCancelGate = deferred();
    const harness = createHarness({ startGate, explicitCancelGate, manualAudio: true });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    harness.emit('voice-identity-cancel');
    await flush(8);
    startGate.resolve();
    await flush(8);
    const staleCancel = harness.fetchCalls.find(call => (
        call.url === `${API_ROOT}/enrollment/cancel`
        && call.options.headers.get('x-voice-identity-enrollment') === 'enrollment-1'
    ));
    assert.ok(staleCancel);
    harness.fireStatusTimeouts();
    await enrolling;
    await flush(4);

    assert.equal(staleCancel.options.signal.aborted, true);
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
});

test('a stalled preflight cancellation before restart is bounded and keeps the pending id', async () => {
    const explicitCancelGate = deferred();
    const harness = createHarness({ autoAdvance: false, explicitCancelGate });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    harness.emit('voice-identity-cancel');
    await enrolling;
    await flush(4);
    harness.fireStatusTimeouts();
    await flush(8);
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);

    const callsBeforeRestart = harness.fetchCalls.length;
    const restarting = harness.emit('voice-identity-start');
    await flush(8);
    const preflight = harness.fetchCalls.slice(callsBeforeRestart)
        .find(call => call.url === `${API_ROOT}/enrollment/cancel`);
    assert.ok(preflight);
    // The preflight times out; error cleanup then issues its own bounded
    // cancellation for the still-active session, which also times out.
    for (let round = 0; round < 3; round += 1) {
        harness.fireStatusTimeouts();
        await flush(6);
    }
    await restarting;
    await flush(4);

    assert.equal(preflight.options.signal.aborted, true);
    assert.equal(
        harness.fetchCalls.slice(callsBeforeRestart).some(call => call.url === `${API_ROOT}/enrollment/start`),
        false,
    );
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
    assert.equal(harness.mediaStreams.at(-1).track.stopped, true);
});

test('cancelling a replacement start after the preflight cancels the new session', async () => {
    const explicitCancelGate = deferred();
    const replacementStart = deferred();
    const harness = createHarness({
        autoAdvance: false,
        explicitCancelGate,
        startGates: { 2: replacementStart },
    });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    harness.emit('voice-identity-cancel');
    await enrolling;
    await flush(4);
    harness.fireStatusTimeouts();
    await flush(8);
    assert.equal(harness.serverEnrollmentId, 'enrollment-1');
    explicitCancelGate.resolve();

    const restarting = harness.emit('voice-identity-start');
    await flush(8);
    assert.equal(harness.serverEnrollmentId, 'enrollment-2');
    harness.emit('voice-identity-cancel');
    await flush(8);
    // Even while the replacement start response is still outstanding, the
    // cancellation must reach the new session rather than the old ID.
    assert.equal(harness.serverEnrollmentId, null);
    replacementStart.resolve();
    await restarting;
    await flush(8);

    assert.equal(harness.serverEnrollmentId, null);
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
});

test('cancelling a resumed enrollment during the permission prompt releases the page', async () => {
    const mediaGate = deferred();
    const harness = createHarness({ initialEnrollmentNextSegment: 2, mediaGate });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(2);
    await harness.emit('voice-identity-cancel');
    await flush(4);

    assert.equal(harness.serverEnrollmentId, null);
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
    assert.equal(harness.elements.get('voice-identity-cancel').hidden, true);
    mediaGate.resolve();
    await enrolling;
    await flush(2);
    assert.equal(harness.mediaStreams[0].track.stopped, true);
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
});

test('voice-activity renders do not rewrite the unchanged live prompt', async () => {
    const harness = createHarness({ manualAudio: true, autoAdvance: false });
    await harness.initialize();
    const prompt = harness.elements.get('voice-identity-prompt');
    let text = prompt.textContent;
    let writes = 0;
    Object.defineProperty(prompt, 'textContent', {
        get() { return text; },
        set(value) { writes += 1; text = value; },
    });

    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    const writesBeforeSpeech = writes;
    const voiceState = harness.elements.get('voice-identity-voice-state');
    const voiceStates = new Set([voiceState.textContent]);
    for (let round = 0; round < 3; round += 1) {
        harness.emitAudio(new Int16Array(REFERENCE_SAMPLES / 8).fill(1024));
        await flush(4);
        voiceStates.add(voiceState.textContent);
        harness.emitAudio(new Int16Array(REFERENCE_SAMPLES / 8));
        await flush(4);
        voiceStates.add(voiceState.textContent);
    }

    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length, 0);
    assert.ok(voiceStates.size > 1);
    assert.equal(writes, writesBeforeSpeech);
    assert.equal(text, '今天我想和你分享一件趣事。');
    await harness.emit('voice-identity-cancel');
    await enrolling;
});

test('a segment still being checked elsewhere stays in the retry flow', async () => {
    const harness = createHarness({
        profileError: 'segment_in_progress',
        profileStatus: 409,
        autoAdvance: false,
    });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(8);

    assert.equal(harness.elements.get('voice-identity-message').textContent, '当前录音仍在检查，请稍后继续。');
    assert.equal(harness.elements.get('voice-identity-next').hidden, false);
    assert.equal(harness.fetchCalls.some(call => call.url === `${API_ROOT}/enrollment/cancel`), false);
    await harness.emit('voice-identity-cancel');
    await enrolling;
});

test('a segment accepted elsewhere during the in-progress wait is adopted instead of re-recorded', async () => {
    const harness = createHarness({ segmentInProgressOnce: 'pending', autoAdvance: false });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(8);
    assert.equal(harness.elements.get('voice-identity-message').textContent, '当前录音仍在检查，请稍后继续。');
    assert.equal(harness.elements.get('voice-identity-next').hidden, false);

    harness.advanceServerSegment();
    await harness.emit('voice-identity-next');
    await flush(8);
    const uploads = () => harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`);
    assert.equal(uploads().length, 1);
    assert.equal(harness.elements.get('voice-identity-message').textContent, '');
    assert.equal(harness.elements.get('voice-identity-next').hidden, false);

    await harness.emit('voice-identity-next');
    await flush(8);
    assert.equal(uploads().length, 2);
    assert.equal(uploads()[1].options.headers.get('x-voice-identity-segment'), '2');
    await harness.emit('voice-identity-cancel');
    await enrolling;
});

test('an in-progress response adopts progress the service already accepted', async () => {
    const harness = createHarness({ segmentInProgressOnce: 'accepted', autoAdvance: false });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(8);
    assert.equal(harness.elements.get('voice-identity-next').hidden, false);
    await harness.emit('voice-identity-next');
    await flush(8);

    const uploads = harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`);
    assert.equal(uploads.length, 2);
    assert.equal(uploads[1].options.headers.get('x-voice-identity-segment'), '2');
    await harness.emit('voice-identity-cancel');
    await enrolling;
});

test('a stalled retry connection releases the page and keeps Retry available', async () => {
    const stalledPageConfig = deferred();
    const harness = createHarness({ initialStatusError: true, pageConfigGates: { 2: stalledPageConfig } });
    await harness.initialize();
    assert.equal(harness.elements.get('voice-identity-retry').hidden, false);

    const retrying = harness.emit('voice-identity-retry');
    await flush(2);
    assert.equal(harness.elements.get('voice-identity-retry').hidden, true);
    harness.fireStatusTimeouts();
    await retrying;

    const pageConfig = harness.fetchCalls.filter(call => call.url === '/api/config/page_config').at(-1);
    assert.equal(pageConfig.options.signal.aborted, true);
    assert.equal(harness.elements.get('voice-identity-retry').hidden, false);
    assert.equal(harness.elements.get('voice-identity-retry').disabled, false);
    assert.notEqual(harness.elements.get('voice-identity-message').textContent, '');
});

test('later segment prompt paints before recording starts', async () => {
    const promptPaintGate = deferred();
    const harness = createHarness({ autoAdvance: false, promptPaintGate });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length, 1);

    await harness.emit('voice-identity-next');
    await flush(2);
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length, 1);
    assert.equal(harness.elements.get('voice-identity-prompt').hidden, false);

    promptPaintGate.resolve();
    await flush(4);
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length, 2);
    await harness.emit('voice-identity-cancel');
    await enrolling;
});

test('sample count finishes a capture without waiting for the duration timer', async () => {
    const harness = createHarness({ manualAudio: true, autoAdvance: false });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    harness.emitAudio(new Int16Array(REFERENCE_SAMPLES).fill(1024));
    await flush(4);

    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length, 1);
    assert.equal(harness.elements.get('voice-identity-next').hidden, false);
    await harness.emit('voice-identity-cancel');
    await enrolling;
});

test('initial silence does not satisfy the minimum speech duration', async () => {
    const harness = createHarness({ manualAudio: true, autoAdvance: false });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    harness.emitAudio(new Int16Array(MINIMUM_SAMPLES));
    await flush(4);
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length, 0);

    harness.emitAudio(new Int16Array(MINIMUM_SAMPLES).fill(1024));
    await flush(4);
    const upload = harness.fetchCalls.find(call => call.url === `${API_ROOT}/enrollment/segment`);
    assert.ok(upload);
    assert.equal(upload.options.body.byteLength, REFERENCE_SAMPLES * Int16Array.BYTES_PER_ELEMENT);
    await harness.emit('voice-identity-cancel');
    await enrolling;
});

test('automatic capture waits for the fixed segment duration after the minimum speech threshold', async () => {
    const harness = createHarness({ manualAudio: true, autoAdvance: false });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    harness.emitAudio(new Int16Array(MINIMUM_SAMPLES).fill(1024));
    await flush(4);
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length, 0);

    harness.emitAudio(new Int16Array(REFERENCE_SAMPLES - MINIMUM_SAMPLES).fill(1024));
    await flush(4);
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length, 1);
    await harness.emit('voice-identity-cancel');
    await enrolling;
});

test('the upcoming prompt is visible while the first microphone is preparing', async () => {
    const mediaGate = deferred();
    const harness = createHarness({ mediaGate });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(2);
    assert.equal(harness.elements.get('voice-identity-prompt').hidden, false);
    assert.notEqual(harness.elements.get('voice-identity-prompt').textContent, '');
    assert.equal(harness.elements.get('voice-identity-capture-status').classList.contains('preparing'), true);
    mediaGate.resolve();
    await enrolling;
});

test('recording clock starts only after the audio context resumes', async () => {
    const resumeGate = deferred();
    const harness = createHarness({ resumeGate });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    assert.equal(harness.getAudioContext().state, 'suspended');
    assert.equal(harness.elements.get('voice-identity-timer').textContent, '');

    resumeGate.resolve();
    await enrolling;
    assert.equal(harness.autoFinishDurations[0], REFERENCE_RECORDING_MS);
});

test('cancellation during microphone setup releases a late stream and context', async () => {
    const mediaGate = deferred();
    const harness = createHarness({ mediaGate, manualAudio: true });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(2);
    await harness.emit('voice-identity-cancel');
    mediaGate.resolve();
    await enrolling;
    await flush(2);

    assert.equal(harness.mediaStreams.length, 1);
    assert.equal(harness.mediaStreams[0].track.stopped, true);
    assert.equal(harness.getAudioContext(), null);
    assert.equal(harness.fetchCalls.some(call => call.url.endsWith('/enrollment/start')), false);
});

test('cancelling while microphone permission is pending releases the controls immediately', async () => {
    const mediaGate = deferred();
    const harness = createHarness({ mediaGate });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(2);
    await harness.emit('voice-identity-cancel');

    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
    assert.equal(harness.elements.get('voice-identity-cancel').hidden, true);
    mediaGate.resolve();
    await enrolling;
});

test('a cancelled permission response cannot stop a successor capture', async () => {
    const oldPermission = deferred();
    const harness = createHarness({
        mediaGate: request => request === 1 ? oldPermission : null,
        manualAudio: true,
    });
    await harness.initialize();
    const oldStart = harness.emit('voice-identity-start');
    await flush(2);
    await harness.emit('voice-identity-cancel');
    const successor = harness.emit('voice-identity-start');
    await flush(8);
    const stream = harness.mediaStreams[0];
    const context = harness.getAudioContext();
    assert.ok(stream);
    oldPermission.resolve();
    await oldStart;
    assert.equal(harness.mediaStreams[1].track.stopped, true);
    assert.equal(stream.track.stopped, false);
    assert.equal(context.state, 'running');
    assert.equal(harness.elements.get('voice-identity-start').hidden, true);
    await harness.emit('voice-identity-cancel');
    await successor;
});

test('BFCache status timeout unlocks controls and ignores its late response', async () => {
    const pending = deferred();
    const harness = createHarness({ statusGates: { 2: pending } });
    await harness.initialize();
    const restoring = harness.dispatch('pageshow', { persisted: true });
    await flush(2);
    assert.equal(harness.elements.get('voice-identity-start').disabled, true);
    harness.fireStatusTimeouts();
    await restoring;
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Request failed.');
    const statusRequest = harness.fetchCalls.filter(call => call.url.endsWith('/status')).at(-1);
    assert.equal(statusRequest.options.signal.aborted, true);
    pending.resolve(jsonResponse({ has_profile: true, requested_enabled: true }));
    await flush(2);
    assert.equal(harness.elements.get('voice-identity-start').hidden, false);
});

test('an older BFCache restore cannot unlock the newer restore', async () => {
    const first = deferred();
    const second = deferred();
    const harness = createHarness({ statusGates: { 2: first, 3: second } });
    await harness.initialize();
    const oldRestore = harness.dispatch('pageshow', { persisted: true });
    const newRestore = harness.dispatch('pageshow', { persisted: true });
    first.resolve(jsonResponse({ has_profile: false }));
    await oldRestore;
    assert.equal(harness.elements.get('voice-identity-start').disabled, true);
    second.resolve(jsonResponse({ has_profile: false }));
    await newRestore;
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
});

test('resumed enrollment shows the canonical segment prompt before microphone setup', async () => {
    const harness = createHarness({ initialEnrollmentNextSegment: 3 });
    await harness.initialize();
    assert.equal(harness.elements.get('voice-identity-prompt').hidden, false);
    assert.equal(harness.elements.get('voice-identity-prompt').textContent, '今天也用自然的声音聊天。');
    await harness.emit('voice-identity-start');
    const firstUpload = harness.fetchCalls.find(call => call.url === `${API_ROOT}/enrollment/segment`);
    assert.equal(firstUpload.options.headers.get('x-voice-identity-segment'), '3');
});

test('failed fourth verification stays in the session and retries the holdout', async () => {
    const harness = createHarness({ verificationFailures: 1, autoAdvance: true });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(12);
    const segments = harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`);
    assert.equal(segments.length, 4);
    assert.equal(harness.elements.get('voice-identity-next').hidden, false);
    assert.equal(harness.elements.get('voice-identity-capture-status').hidden, true);
    assert.match(harness.elements.get('voice-identity-message').textContent, /31/);
    await harness.emit('voice-identity-next');
    await enrolling;
    assert.equal(
        harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length,
        5,
    );
    assert.equal(harness.elements.get('voice-identity-profile-controls').hidden, false);
});

test('transient progress status failure keeps the active enrollment resumable', async () => {
    const harness = createHarness({ statusFailures: 1 });
    await harness.initialize();

    await harness.emit('voice-identity-start');

    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Enrollment complete.');
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/cancel`).length, 0);
});

test('a late focus status response cannot clear a newly started enrollment', async () => {
    const focusStatusGate = deferred();
    const harness = createHarness({ focusStatusGate });
    await harness.initialize();

    harness.dispatch('focus');
    await flush(2);
    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    focusStatusGate.resolve(jsonResponse({
        requested_enabled: false,
        effective_enabled: false,
        effective_reason: 'no_profile',
        has_profile: false,
        enrollment: null,
        runtime_mode: 'enforce',
    }));
    await enrolling;

    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Enrollment complete.');
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/cancel`).length, 0);
});

test('a stale passive refresh cannot overwrite a newer refresh', async () => {
    const focusStatusGate = deferred();
    const harness = createHarness({
        initialProfile: true,
        initialRequested: true,
        focusStatusGate,
    });
    await harness.initialize();

    harness.dispatch('focus');
    await flush(2);
    harness.dispatch('focus');
    await flush(2);
    assert.equal(harness.elements.get('voice-identity-filter').checked, true);

    focusStatusGate.resolve(jsonResponse({
        requested_enabled: false,
        effective_enabled: false,
        effective_reason: 'disabled',
        has_profile: true,
        enrollment: null,
        runtime_mode: 'enforce',
    }));
    await flush(2);
    assert.equal(harness.elements.get('voice-identity-filter').checked, true);
});

test('a failed newer refresh falls back to an older successful response', async () => {
    const focusStatusGate = deferred();
    const harness = createHarness({
        initialProfile: true,
        initialRequested: true,
        focusStatusGate,
        statusFailures: 1,
    });
    await harness.initialize();

    harness.dispatch('focus');
    await flush(2);
    harness.dispatch('focus');
    await flush(2);
    focusStatusGate.resolve(jsonResponse({
        requested_enabled: false,
        effective_enabled: false,
        effective_reason: 'disabled',
        has_profile: true,
        enrollment: null,
        runtime_mode: 'enforce',
    }));
    await flush(3);

    assert.equal(harness.elements.get('voice-identity-filter').checked, false);
});

test('a late response cannot roll back an already applied passive refresh', async () => {
    const request2 = deferred();
    const request3 = deferred();
    const request4 = deferred();
    const harness = createHarness({
        initialProfile: true,
        initialRequested: true,
        statusGates: { 2: request2, 3: request3, 4: request4 },
    });
    await harness.initialize();

    harness.dispatch('focus');
    harness.dispatch('focus');
    harness.dispatch('focus');
    await flush(2);
    request3.resolve(jsonResponse({
        requested_enabled: false,
        effective_enabled: false,
        effective_reason: 'disabled',
        has_profile: true,
        enrollment: null,
        runtime_mode: 'enforce',
    }));
    await flush(2);
    request4.reject(new Error('status_transient'));
    await flush(2);
    request2.resolve(jsonResponse({
        requested_enabled: true,
        effective_enabled: true,
        effective_reason: 'ready',
        has_profile: true,
        enrollment: null,
        runtime_mode: 'enforce',
    }));
    await flush(3);

    assert.equal(harness.elements.get('voice-identity-filter').checked, false);
});

test('disabled runtime blocks enrollment without a stored profile', async () => {
    const harness = createHarness({ runtimeMode: 'off' });
    await harness.initialize();

    assert.equal(harness.elements.get('voice-identity-profile-status').textContent, 'Voice identity is turned off');
    assert.equal(harness.elements.get('voice-identity-start').disabled, true);
});

test('disabled runtime keeps delete but blocks re-enrollment and filter enable', async () => {
    const harness = createHarness({ runtimeMode: 'off', initialProfile: true });
    await harness.initialize();

    assert.equal(harness.elements.get('voice-identity-profile-status').textContent, 'Voice identity is turned off');
    assert.equal(harness.elements.get('voice-identity-reenroll').disabled, true);
    assert.equal(harness.elements.get('voice-identity-filter').disabled, true);
    assert.equal(harness.elements.get('voice-identity-delete').disabled, false);
});

test('disabled runtime still lets a previously enabled filter be turned off', async () => {
    const harness = createHarness({ runtimeMode: 'off', initialProfile: true, initialRequested: true });
    await harness.initialize();

    assert.equal(harness.elements.get('voice-identity-filter').checked, true);
    assert.equal(harness.elements.get('voice-identity-filter').disabled, false);
});

test('a short remaining lease is rejected before starting a futile recording', async () => {
    const harness = createHarness({ remainingSeconds: 9 });
    await harness.initialize();

    await harness.emit('voice-identity-start');

    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Not enough time remains for the next recording.');
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length, 3);
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/cancel`).length, 1);
});

test('inconsistent third reference adopts the server reset and restarts at segment one', async () => {
    const harness = createHarness({ inconsistentReference: true, autoAdvance: true });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(12);
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length, 3);
    assert.equal(harness.elements.get('voice-identity-next').hidden, false);
    assert.equal(harness.elements.get('voice-identity-prompt').textContent, '今天我想和你分享一件趣事。');
    await harness.emit('voice-identity-next');
    await enrolling;

    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length, 7);
    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/cancel`).length, 0);
});

test('underfilled capture can be cancelled without uploading partial PCM', async () => {
    const harness = createHarness({ audioChunks: 100 });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(6);
    await harness.elements.get('voice-identity-cancel').emit('click');
    await enrolling;
    await flush(4);

    assert.equal(
        harness.fetchCalls.some(call => call.url === `${API_ROOT}/enrollment/segment`),
        false,
    );
    assert.equal(
        harness.fetchCalls.some(call => call.url === `${API_ROOT}/enrollment/cancel`),
        true,
    );
    assert.equal(harness.elements.get('voice-identity-message').textContent, '');
});

test('server rejection for insufficient usable speech stays fail-safe and visible', async () => {
    const harness = createHarness({ profileError: 'speech_too_short' });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(8);
    await harness.elements.get('voice-identity-cancel').emit('click');
    await enrolling;
    await flush(4);

    assert.equal(
        harness.fetchCalls.some(call => call.url === `${API_ROOT}/enrollment/segment`),
        true,
    );
    assert.equal(harness.elements.get('voice-identity-profile-controls').hidden, true);
    assert.equal(harness.elements.get('voice-identity-message').textContent, '');
});

test('canonical enrollment audio errors show localized messages', async () => {
    const invalid = createHarness({ profileError: 'invalid_pcm' });
    await invalid.initialize();
    const invalidEnrollment = invalid.emit('voice-identity-start');
    await flush(8);
    await invalid.elements.get('voice-identity-cancel').emit('click');
    await invalidEnrollment;
    assert.equal(
        invalid.elements.get('voice-identity-message').textContent,
        'Invalid recording format.',
    );

    const tooLong = createHarness({ profileError: 'audio_too_long' });
    await tooLong.initialize();
    const tooLongEnrollment = tooLong.emit('voice-identity-start');
    await flush(8);
    await tooLong.elements.get('voice-identity-cancel').emit('click');
    await tooLongEnrollment;
    assert.equal(
        tooLong.elements.get('voice-identity-message').textContent,
        'Recording is too long.',
    );
});

test('stable backend failures keep their actionable enrollment messages', async () => {
    const cases = [
        ['model_unavailable', 'Voice model unavailable.'],
        ['audio_processing_unavailable', 'Audio processing unavailable.'],
        ['secure_storage_unavailable', 'Secure storage unavailable.'],
    ];
    for (const [profileError, expectedMessage] of cases) {
        const harness = createHarness({ profileError, profileStatus: 503 });
        await harness.initialize();
        const enrolling = harness.emit('voice-identity-start');
        await flush(8);
        assert.equal(harness.elements.get('voice-identity-message').textContent, expectedMessage);
        await harness.elements.get('voice-identity-cancel').emit('click');
        await enrolling;
    }
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

test('an unreadable selected microphone falls back to the default device', async () => {
    const harness = createHarness({
        selectedMicrophoneId: 'stale-device',
        mediaError: [{ name: 'NotReadableError' }, null],
    });
    await harness.initialize();
    await harness.emit('voice-identity-start');
    assert.equal(harness.mediaConstraintCalls[0].audio.deviceId.exact, 'stale-device');
    assert.equal('deviceId' in harness.mediaConstraintCalls[1].audio, false);
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
    assert.match(template, /voice-identity-progress/);
    assert.match(template, /voice-identity-prompt/);
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

test('backend degradation disables re-enrollment for an existing profile', async () => {
    const harness = createHarness({
        initialProfile: true,
        initialEffectiveReason: 'model_unavailable',
    });
    await harness.initialize();

    assert.equal(harness.elements.get('voice-identity-reenroll').disabled, true);
});

test('a pending filter request stays available when the profile is unavailable', async () => {
    const harness = createHarness({
        initialRequested: true,
        initialEffectiveReason: 'profile_incompatible',
    });
    await harness.initialize();

    assert.equal(harness.elements.get('voice-identity-profile-controls').hidden, false);
    assert.equal(harness.elements.get('voice-identity-profile-actions').hidden, true);
    assert.equal(harness.elements.get('voice-identity-filter').checked, true);

    harness.elements.get('voice-identity-filter').checked = false;
    await harness.emit('voice-identity-filter', 'change');

    assert.equal(harness.elements.get('voice-identity-filter').checked, false);
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

test('explicit cancel aborts an active capture and keeps controls locked until it settles', async () => {
    const harness = createHarness({ manualAudio: true, autoAdvance: false });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush();
    assert.equal(harness.elements.get('voice-identity-cancel').hidden, false);
    await harness.emit('voice-identity-cancel');
    assert.equal(harness.elements.get('voice-identity-start').disabled, true);
    await enrolling;
    await flush();

    const cancel = harness.fetchCalls.find(call => (
        call.url === `${API_ROOT}/enrollment/cancel`
    ));
    assert.ok(cancel);
    assert.equal(cancel.options.headers.get('x-voice-identity-enrollment'), 'enrollment-1');
    assert.equal(harness.elements.get('voice-identity-start').hidden, false);
});

test('cancellation aborts a pending segment upload and clears its PCM', async () => {
    const segmentGate = deferred();
    const harness = createHarness({ segmentGate });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(8);
    const upload = harness.fetchCalls.find(call => call.url === `${API_ROOT}/enrollment/segment`);
    assert.ok(upload);
    await harness.emit('voice-identity-cancel');
    await enrolling;
    await flush(2);
    segmentGate.resolve();

    assert.equal(upload.options.signal.aborted, true);
    assert.equal(new Int16Array(upload.options.body).every(sample => sample === 0), true);
    assert.ok(harness.fetchCalls.some(call => call.url === `${API_ROOT}/enrollment/cancel`));
});

test('manual finish rejects a capture shorter than the backend contract', async () => {
    const harness = createHarness({ manualAudio: true, autoAdvance: false });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush();
    assert.equal(harness.elements.get('voice-identity-finish').hidden, false);

    harness.emitAudio(new Int16Array(700).fill(1024));
    await harness.emit('voice-identity-finish');
    await flush();

    const upload = harness.fetchCalls.find(call => (
        call.url === `${API_ROOT}/enrollment/segment`
    ));
    assert.equal(upload, undefined);
    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Keep speaking for about 1.5 seconds before saving.');
    assert.equal(
        harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length,
        0,
    );
    await harness.emit('voice-identity-cancel');
    await enrolling;
});

test('a short natural utterance can be manually saved into the fixed upload shape', async () => {
    const harness = createHarness({ manualAudio: true, autoAdvance: false });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush();
    harness.emitAudio(new Int16Array(MINIMUM_SAMPLES).fill(1024));
    await flush(4);

    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length, 0);
    await harness.emit('voice-identity-finish');
    await flush(4);
    const upload = harness.fetchCalls.find(call => call.url === `${API_ROOT}/enrollment/segment`);
    assert.ok(upload);
    assert.equal(upload.options.body.byteLength, REFERENCE_SAMPLES * Int16Array.BYTES_PER_ELEMENT);
    assert.equal(harness.elements.get('voice-identity-next').hidden, false);
    await harness.emit('voice-identity-cancel');
    await enrolling;
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

test('BFCache restore waits for keepalive cancellation before reconciling status', async () => {
    const cancelGate = deferred();
    const harness = createHarness({
        initialEnrollmentNextSegment: 1,
        cancelGate,
        manualAudio: true,
    });
    await harness.initialize();

    const closing = harness.beforeClose();
    await flush(3);
    const restoring = harness.dispatch('pageshow', { persisted: true });
    await flush(4);

    assert.equal(
        harness.fetchCalls.filter(call => call.url === `${API_ROOT}/status`).length,
        1,
    );
    assert.equal(harness.elements.get('voice-identity-cancel').disabled, true);

    cancelGate.resolve();
    await closing;
    await restoring;
    await flush(4);

    assert.equal(
        harness.fetchCalls.filter(call => call.url === `${API_ROOT}/status`).length,
        2,
    );
    assert.equal(harness.elements.get('voice-identity-cancel').hidden, true);
});

test('BFCache restore releases controls when keepalive cancellation times out', async () => {
    const cancelGate = deferred();
    const harness = createHarness({
        initialEnrollmentNextSegment: 1,
        cancelGate,
        manualAudio: true,
    });
    await harness.initialize();

    const closing = harness.beforeClose();
    await flush(3);
    const restoring = harness.dispatch('pageshow', { persisted: true });
    await flush(4);
    harness.fireStatusTimeouts();
    await restoring;

    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
    assert.equal(harness.elements.get('voice-identity-cancel').disabled, false);

    cancelGate.resolve();
    await closing;
    await flush(4);
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
});

test('starting after a restore timeout replaces the session targeted by the late keepalive cancel', async () => {
    const cancelGate = deferred();
    const harness = createHarness({
        initialEnrollmentNextSegment: 1,
        cancelGate,
        manualAudio: true,
        autoAdvance: false,
    });
    await harness.initialize();

    const closing = harness.beforeClose();
    await flush(3);
    const restoring = harness.dispatch('pageshow', { persisted: true });
    await flush(4);
    harness.fireStatusTimeouts();
    await restoring;
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);

    const enrolling = harness.emit('voice-identity-start');
    await flush(8);
    const explicitCancelIndex = harness.fetchCalls.findIndex(call => (
        call.url === `${API_ROOT}/enrollment/cancel` && call.options.keepalive !== true
    ));
    const startIndex = harness.fetchCalls.findIndex(call => call.url === `${API_ROOT}/enrollment/start`);
    assert.ok(explicitCancelIndex >= 0);
    assert.ok(startIndex > explicitCancelIndex);
    assert.equal(
        harness.fetchCalls[explicitCancelIndex].options.headers.get('x-voice-identity-enrollment'),
        'enrollment-1',
    );
    assert.equal(harness.serverEnrollmentId, 'enrollment-2');

    cancelGate.resolve();
    await closing;
    await flush(4);
    assert.equal(harness.serverEnrollmentId, 'enrollment-2');

    harness.emitAudio(new Int16Array(REFERENCE_SAMPLES).fill(1024));
    await flush(4);
    const upload = harness.fetchCalls.find(call => call.url === `${API_ROOT}/enrollment/segment`);
    assert.equal(upload.options.headers.get('x-voice-identity-enrollment'), 'enrollment-2');
    await harness.emit('voice-identity-cancel');
    await enrolling;
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

test('close does not cancel an unowned session after start response abort', async () => {
    const harness = createHarness({ startResponseErrorAfterAbort: true, manualAudio: true });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(2);
    await harness.beforeClose();
    await enrolling;

    assert.equal(harness.fetchCalls.some(call => call.url === `${API_ROOT}/enrollment/cancel`), false);
});

test('BFCache restore invalidates the pending enrollment workflow', async () => {
    const startGate = deferred();
    const harness = createHarness({ startGate });
    await harness.initialize();

    const enrolling = harness.emit('voice-identity-start');
    await flush(4);
    assert.ok(harness.fetchCalls.some(call => call.url === `${API_ROOT}/enrollment/start`));

    await harness.dispatch('pageshow', { persisted: true });
    startGate.resolve();
    await enrolling;
    await flush(2);

    assert.equal(harness.fetchCalls.filter(call => call.url === `${API_ROOT}/enrollment/segment`).length, 0);
    assert.ok(harness.fetchCalls.some(call => call.url === `${API_ROOT}/enrollment/cancel`));
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
        '/enrollment/verify',
        '/enrollment/verify',
        '/enrollment/commit',
        'ready_to_commit',
        'voice-identity-record',
        'step-progress',
    ]) {
        assert.equal(source.includes(retired) || template.includes(retired), false, retired);
    }
});
