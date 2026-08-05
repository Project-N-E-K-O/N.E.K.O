'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'js/voice_identity.js'), 'utf8');
const stylesheet = fs.readFileSync(path.join(__dirname, 'css/voice_identity.css'), 'utf8');
const template = fs.readFileSync(path.join(__dirname, '../templates/voice_identity.html'), 'utf8');

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
}

function createElement({ withRecordLabel = false } = {}) {
    const listeners = new Map();
    const classes = new Set();
    const recordLabel = withRecordLabel ? createElement() : null;
    const element = {
        textContent: '',
        hidden: false,
        disabled: false,
        checked: false,
        addEventListener(type, listener) {
            listeners.set(type, listener);
        },
        async emit(type) {
            return listeners.get(type)?.({ type, target: element });
        },
        querySelector(selector) {
            return selector === 'span:last-child' ? recordLabel : null;
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
        recordLabel,
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
    route,
    audio = false,
    audioBlocks = 120,
    audioSample,
    fixedPrompts,
    mediaError,
    nativeConfirm,
    showConfirm,
    scheduleTimeout,
} = {}) {
    const elementIds = [
        'voice-identity-status-dot',
        'voice-identity-profile-status',
        'voice-identity-step-count',
        'voice-identity-step-title',
        'voice-identity-step-body',
        'voice-identity-prompt',
        'voice-identity-timer',
        'voice-identity-message',
        'voice-identity-start',
        'voice-identity-record',
        'voice-identity-cancel',
        'voice-identity-reenroll',
        'voice-identity-delete',
        'voice-identity-filter',
    ];
    const elements = new Map(elementIds.map(id => [
        id,
        createElement({ withRecordLabel: id === 'voice-identity-record' }),
    ]));
    const progress = Array.from({ length: 5 }, () => createElement());
    const documentListeners = new Map();
    const windowListeners = new Map();
    const fetchCalls = [];
    let locale = 'en';
    let processor = null;
    let audioContext = null;
    let mediaRequests = 0;
    let sourceSampleIndex = 0;
    const mediaStreams = [];

    const translations = {
        en: {
            'voiceIdentity.fixedTitle': 'Read the fixed text',
            'voiceIdentity.fixedHelp': 'Use a natural voice.',
            'voiceIdentity.retry': 'Retry',
            'voiceIdentity.record': 'Record',
            'voiceIdentity.recording': 'Recording...',
            'voiceIdentity.enrollmentComplete': 'Enrollment complete.',
            'voiceIdentity.microphoneDenied': 'Microphone unavailable.',
            'voiceIdentity.requestFailed': 'Request failed.',
        },
        ja: {
            'voiceIdentity.fixedTitle': '固定テキストを読む',
            'voiceIdentity.fixedHelp': '自然な声で読んでください。',
            'voiceIdentity.retry': '再試行',
            'voiceIdentity.record': '録音',
            'voiceIdentity.recording': '録音中...',
            'voiceIdentity.requestFailed': '操作に失敗しました。',
        },
    };
    const prompts = {
        en: ['English one', 'English two', 'English three'],
        ja: ['日本語一', '日本語二', '日本語三'],
    };
    const translate = key => translations[locale]?.[key] || key;

    class MockAudioContext {
        constructor() {
            this.sampleRate = 48000;
            this.state = 'running';
            this.resumeCalls = 0;
            this.destination = {};
            audioContext = this;
        }

        createMediaStreamSource() {
            return { connect() {}, disconnect() {} };
        }

        createScriptProcessor() {
            processor = { connect() {}, disconnect() {}, onaudioprocess: null };
            return processor;
        }

        createGain() {
            return { gain: { value: 1 }, connect() {}, disconnect() {} };
        }

        resume() {
            this.resumeCalls += 1;
            this.state = 'running';
            return Promise.resolve();
        }

        close() {
            this.state = 'closed';
            return Promise.resolve();
        }
    }

    const document = {
        getElementById(id) {
            return elements.get(id);
        },
        querySelectorAll(selector) {
            return selector === '.step-progress span' ? progress : [];
        },
        addEventListener(type, listener) {
            documentListeners.set(type, listener);
        },
    };
    const window = {
        t: translate,
        i18next: {
            t(key) {
                return key === 'voiceIdentity.fixedPrompts'
                    ? (fixedPrompts === undefined ? prompts[locale] : fixedPrompts)
                    : translate(key);
            },
        },
        addEventListener(type, listener) {
            windowListeners.set(type, listener);
        },
        dispatchEvent(event) {
            return windowListeners.get(event.type)?.(event);
        },
        setInterval() {
            return 1;
        },
        clearInterval() {},
        setTimeout(callback, delay) {
            if (scheduleTimeout) return scheduleTimeout(callback, delay);
            if (audio && processor?.onaudioprocess) {
                for (let index = 0; index < audioBlocks; index += 1) {
                    const input = new Float32Array(2048);
                    for (let sampleIndex = 0; sampleIndex < input.length; sampleIndex += 1) {
                        input[sampleIndex] = typeof audioSample === 'function'
                            ? audioSample(sourceSampleIndex)
                            : 0.25;
                        sourceSampleIndex += 1;
                    }
                    processor.onaudioprocess({
                        inputBuffer: { getChannelData: () => input },
                    });
                }
            }
            callback();
            return 1;
        },
        clearTimeout() {},
        AudioContext: audio ? MockAudioContext : undefined,
        confirm: nativeConfirm,
        showConfirm,
    };
    const context = vm.createContext({
        window,
        document,
        navigator: {
            mediaDevices: {
                getUserMedia: async () => {
                    mediaRequests += 1;
                    if (mediaError) throw mediaError;
                    const track = {
                        stopped: false,
                        stop() {
                            this.stopped = true;
                        },
                    };
                    const mediaStream = {
                        active: true,
                        getTracks() {
                            return [track];
                        },
                    };
                    mediaStreams.push(mediaStream);
                    return mediaStream;
                },
            },
        },
        Headers: MockHeaders,
        performance: { now: () => 0 },
        fetch: async (url, options = {}) => {
            fetchCalls.push({ url, options });
            if (route) return route(url, options, fetchCalls);
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({ enrollment: { stage: 'idle' } });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
        console,
    });
    vm.runInContext(source, context, { filename: 'voice_identity.js' });

    return {
        elements,
        fetchCalls,
        async initialize() {
            return documentListeners.get('DOMContentLoaded')();
        },
        setLocale(nextLocale) {
            locale = nextLocale;
            window.dispatchEvent({ type: 'localechange' });
        },
        getAudioContext() {
            return audioContext;
        },
        getMediaRequests() {
            return mediaRequests;
        },
        getMediaStreams() {
            return mediaStreams;
        },
        beforeClose() {
            return window.nekoBeforeWindowClose();
        },
        pagehide() {
            return window.dispatchEvent({ type: 'pagehide' });
        },
    };
}

test('mutation controls stay disabled until CSRF and initial status both resolve', async () => {
    const pageConfig = deferred();
    const status = deferred();
    const harness = createHarness({
        route(url) {
            if (url === '/api/config/page_config') return pageConfig.promise;
            if (url === '/api/voice-identity/status') return status.promise;
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    const initialization = harness.initialize();
    const start = harness.elements.get('voice-identity-start');
    const reenroll = harness.elements.get('voice-identity-reenroll');
    assert.equal(start.disabled, true);
    assert.equal(reenroll.disabled, true);

    pageConfig.resolve(jsonResponse({ autostart_csrf_token: 'csrf-token' }));
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(start.disabled, true);
    assert.equal(reenroll.disabled, true);

    status.resolve(jsonResponse({ enrollment: { stage: 'idle' } }));
    await initialization;
    assert.equal(start.disabled, false);
    assert.equal(reenroll.disabled, false);
});

test('active enrollment disables profile mutations between recording steps', async () => {
    const harness = createHarness({
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'fixed_1' },
                    profile: { available: true, state: 'active' },
                    filter: { enabled: true },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    assert.equal(harness.elements.get('voice-identity-reenroll').disabled, true);
    assert.equal(harness.elements.get('voice-identity-delete').disabled, true);
    assert.equal(harness.elements.get('voice-identity-filter').disabled, true);
});

test('filter updates block competing profile mutations with a scoped pending state', async () => {
    const filterUpdate = deferred();
    const harness = createHarness({
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({
                    enrollment: { stage: 'idle' },
                    profile: { available: true, state: 'active' },
                    filter: { enabled: false },
                });
            }
            if (url === '/api/voice-identity/filter') return filterUpdate.promise;
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    const filter = harness.elements.get('voice-identity-filter');
    filter.checked = true;
    const update = filter.emit('change');

    assert.equal(filter.checked, true);
    assert.equal(filter.disabled, true);
    assert.equal(harness.elements.get('voice-identity-start').disabled, true);
    assert.equal(harness.elements.get('voice-identity-reenroll').disabled, true);
    assert.equal(harness.elements.get('voice-identity-delete').disabled, true);
    await harness.elements.get('voice-identity-reenroll').emit('click');
    assert.equal(harness.getMediaRequests(), 0);

    filterUpdate.resolve(jsonResponse({
        enrollment: { stage: 'idle' },
        profile: { available: true, state: 'active' },
        filter: { enabled: true },
    }));
    await update;

    assert.equal(filter.checked, true);
    assert.equal(filter.disabled, false);
    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
    assert.equal(harness.elements.get('voice-identity-reenroll').disabled, false);
    assert.equal(harness.elements.get('voice-identity-delete').disabled, false);
});

test('partial cancellation status preserves the existing profile and filter state', async () => {
    const harness = createHarness({
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'fixed_1' },
                    profile: { available: true, state: 'active' },
                    filter: { enabled: true },
                });
            }
            if (url === '/api/voice-identity/enrollment/cancel') {
                return jsonResponse({ enrollment: { stage: 'idle' } });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    await harness.elements.get('voice-identity-cancel').emit('click');

    assert.equal(harness.elements.get('voice-identity-delete').disabled, false);
    assert.equal(harness.elements.get('voice-identity-filter').checked, true);
    assert.equal(
        harness.elements.get('voice-identity-profile-status').textContent.includes('Owner Profile'),
        true,
    );
});

test('ambiguous filter response reconciles the authoritative enabled state', async () => {
    let statusRequests = 0;
    let filterRequests = 0;
    const harness = createHarness({
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                statusRequests += 1;
                return jsonResponse({
                    enrollment: { stage: 'idle' },
                    profile: { available: true, state: 'active' },
                    filter: { enabled: statusRequests > 1 },
                });
            }
            if (url === '/api/voice-identity/filter') {
                filterRequests += 1;
                throw new Error('connection_lost');
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    const filter = harness.elements.get('voice-identity-filter');
    filter.checked = true;
    await filter.emit('change');

    assert.equal(filterRequests, 1);
    assert.equal(statusRequests, 2);
    assert.equal(filter.checked, true);
    assert.equal(harness.elements.get('voice-identity-message').textContent, '');
});

test('locale changes re-render the current enrollment step and prompt', async () => {
    const harness = createHarness({
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'fixed_1' },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    assert.equal(harness.elements.get('voice-identity-step-title').textContent, 'Read the fixed text');
    assert.equal(harness.elements.get('voice-identity-prompt').textContent, 'English one');

    harness.setLocale('ja');
    assert.equal(harness.elements.get('voice-identity-step-title').textContent, '固定テキストを読む');
    assert.equal(harness.elements.get('voice-identity-prompt').textContent, '日本語一');
});

test('invalid localized fixed prompts fall back to the short recording copy', async () => {
    const harness = createHarness({
        fixedPrompts: null,
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'fixed_1' },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();

    assert.equal(
        harness.elements.get('voice-identity-prompt').textContent,
        '今天我想和你分享一件趣事。',
    );
});

test('failed enrollment commit exposes a retry that can finish without re-recording', async () => {
    let commitAttempts = 0;
    const harness = createHarness({
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'ready_to_commit' },
                });
            }
            if (url === '/api/voice-identity/enrollment/commit') {
                commitAttempts += 1;
                if (commitAttempts === 1) {
                    return jsonResponse({ error: 'temporary_failure' }, { ok: false, status: 503 });
                }
                return jsonResponse({
                    enrollment: { stage: 'idle' },
                    profile: { available: true, state: 'active' },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    const record = harness.elements.get('voice-identity-record');
    assert.equal(record.hidden, false);
    assert.equal(record.recordLabel.textContent, 'Retry');

    await record.emit('click');
    assert.equal(commitAttempts, 1);
    assert.equal(record.hidden, false);
    assert.equal(record.disabled, false);
    assert.equal(record.recordLabel.textContent, 'Retry');

    await record.emit('click');
    assert.equal(commitAttempts, 2);
    assert.equal(harness.elements.get('voice-identity-start').hidden, false);
    assert.equal(harness.elements.get('voice-identity-profile-status').textContent.includes('Owner Profile'), true);
});

test('ambiguous commit response reconciles activation without entering recording state', async () => {
    const commitResponse = deferred();
    let statusRequests = 0;
    let commitRequests = 0;
    const harness = createHarness({
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                statusRequests += 1;
                return jsonResponse(statusRequests === 1
                    ? {
                        enrollment: {
                            session_id: 'session-1',
                            stage: 'ready_to_commit',
                        },
                    }
                    : {
                        enrollment: { stage: 'idle' },
                        profile: { available: true, state: 'active' },
                    });
            }
            if (url === '/api/voice-identity/enrollment/commit') {
                commitRequests += 1;
                return commitResponse.promise;
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    const record = harness.elements.get('voice-identity-record');
    const commit = record.emit('click');

    assert.equal(record.disabled, true);
    assert.equal(record.recordLabel.textContent, 'Retry');
    assert.equal(record.classList.contains('recording'), false);

    commitResponse.reject(new Error('connection_lost'));
    await commit;

    assert.equal(commitRequests, 1);
    assert.equal(statusRequests, 2);
    assert.equal(record.classList.contains('recording'), false);
    assert.equal(harness.elements.get('voice-identity-start').hidden, false);
    assert.equal(
        harness.elements.get('voice-identity-profile-status').textContent.includes('Owner Profile'),
        true,
    );
});

test('a pre-existing profile does not prove that a failed re-enrollment committed', async () => {
    let statusRequests = 0;
    let commitRequests = 0;
    const harness = createHarness({
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                statusRequests += 1;
                return jsonResponse(statusRequests === 1
                    ? {
                        enrollment: { session_id: 'session-1', stage: 'ready_to_commit' },
                        profile: { available: true, state: 'active' },
                    }
                    : {
                        enrollment: { stage: 'idle' },
                        profile: { available: true, state: 'active' },
                    });
            }
            if (url === '/api/voice-identity/enrollment/commit') {
                commitRequests += 1;
                return jsonResponse(
                    { error: 'rejected' },
                    { ok: false, status: 409 },
                );
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    await harness.elements.get('voice-identity-record').emit('click');

    assert.equal(commitRequests, 1);
    assert.equal(statusRequests, 2);
    assert.equal(
        harness.elements.get('voice-identity-message').textContent,
        'Request failed.',
    );
});

test('automatic commit clears the recording indicator before the save request settles', async () => {
    const commitStarted = deferred();
    const commitResponse = deferred();
    const harness = createHarness({
        audio: true,
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'free_verify_2' },
                });
            }
            if (url === '/api/voice-identity/enrollment/verify') {
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'ready_to_commit' },
                    verification: { passed: true },
                });
            }
            if (url === '/api/voice-identity/enrollment/commit') {
                commitStarted.resolve();
                return commitResponse.promise;
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    const record = harness.elements.get('voice-identity-record');
    const saving = record.emit('click');
    await commitStarted.promise;

    assert.equal(record.classList.contains('recording'), false);
    assert.equal(record.recordLabel.textContent, 'Retry');

    commitResponse.resolve(jsonResponse({
        enrollment: { stage: 'idle' },
        profile: { available: true, state: 'active' },
    }));
    await saving;
});

test('starting enrollment releases the permission-check microphone before the first prompt', async () => {
    const harness = createHarness({
        audio: true,
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({ enrollment: { stage: 'idle' } });
            }
            if (url === '/api/voice-identity/enrollment/start') {
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'fixed_1' },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    await harness.elements.get('voice-identity-start').emit('click');

    assert.equal(harness.getMediaRequests(), 1);
    assert.equal(harness.getAudioContext().state, 'closed');
    assert.equal(
        harness.getMediaStreams()[0].getTracks().every(track => track.stopped),
        true,
    );
    assert.equal(harness.elements.get('voice-identity-record').hidden, false);
});

test('a stale CSRF token is refreshed and the mutation is retried once', async () => {
    let pageConfigRequests = 0;
    let startRequests = 0;
    const mutationTokens = [];
    const harness = createHarness({
        audio: true,
        route(url, options) {
            if (url === '/api/config/page_config') {
                pageConfigRequests += 1;
                return jsonResponse({
                    autostart_csrf_token: pageConfigRequests === 1
                        ? 'stale-token'
                        : 'fresh-token',
                });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({ enrollment: { stage: 'idle' } });
            }
            if (url === '/api/voice-identity/enrollment/start') {
                startRequests += 1;
                mutationTokens.push(options.headers.get('X-CSRF-Token'));
                if (startRequests === 1) {
                    return jsonResponse(
                        { error: 'forbidden', error_code: 'csrf_validation_failed' },
                        { ok: false, status: 403 },
                    );
                }
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'fixed_1' },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    await harness.elements.get('voice-identity-start').emit('click');

    assert.equal(pageConfigRequests, 2);
    assert.equal(startRequests, 2);
    assert.deepEqual(mutationTokens, ['stale-token', 'fresh-token']);
    assert.equal(harness.elements.get('voice-identity-record').hidden, false);
});

test('a repeated CSRF rejection stops after the single refresh retry', async () => {
    let pageConfigRequests = 0;
    let startRequests = 0;
    const harness = createHarness({
        audio: true,
        route(url) {
            if (url === '/api/config/page_config') {
                pageConfigRequests += 1;
                return jsonResponse({ autostart_csrf_token: `csrf-token-${pageConfigRequests}` });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({ enrollment: { stage: 'idle' } });
            }
            if (url === '/api/voice-identity/enrollment/start') {
                startRequests += 1;
                return jsonResponse(
                    { error: 'forbidden', error_code: 'csrf_validation_failed' },
                    { ok: false, status: 403 },
                );
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    await harness.elements.get('voice-identity-start').emit('click');

    assert.equal(pageConfigRequests, 2);
    assert.equal(startRequests, 2);
    assert.equal(
        harness.elements.get('voice-identity-message').textContent,
        'Request failed.',
    );
});

test('a non-CSRF 403 is not refreshed or replayed', async () => {
    let pageConfigRequests = 0;
    let startRequests = 0;
    const harness = createHarness({
        audio: true,
        route(url) {
            if (url === '/api/config/page_config') {
                pageConfigRequests += 1;
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({ enrollment: { stage: 'idle' } });
            }
            if (url === '/api/voice-identity/enrollment/start') {
                startRequests += 1;
                return jsonResponse(
                    { error: 'forbidden', error_code: 'different_failure' },
                    { ok: false, status: 403 },
                );
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    await harness.elements.get('voice-identity-start').emit('click');

    assert.equal(pageConfigRequests, 1);
    assert.equal(startRequests, 1);
    assert.equal(
        harness.elements.get('voice-identity-message').textContent,
        'Request failed.',
    );
});

test('starting enrollment reports an unavailable microphone', async () => {
    const mediaError = new Error('microphone_locked');
    mediaError.name = 'NotReadableError';
    const harness = createHarness({
        mediaError,
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({ enrollment: { stage: 'idle' } });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    await harness.elements.get('voice-identity-start').emit('click');

    assert.equal(
        harness.elements.get('voice-identity-message').textContent,
        'Microphone unavailable.',
    );
});

test('ambiguous enrollment start failure reconciles the server session', async () => {
    let statusRequests = 0;
    let startRequests = 0;
    const harness = createHarness({
        audio: true,
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                statusRequests += 1;
                return jsonResponse(statusRequests === 1
                    ? { enrollment: { stage: 'idle' } }
                    : { enrollment: { session_id: 'session-1', stage: 'fixed_1' } });
            }
            if (url === '/api/voice-identity/enrollment/start') {
                startRequests += 1;
                throw new Error('connection_lost');
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    await harness.elements.get('voice-identity-start').emit('click');

    assert.equal(statusRequests, 2);
    assert.equal(startRequests, 1);
    assert.equal(harness.elements.get('voice-identity-start').hidden, true);
    assert.equal(harness.elements.get('voice-identity-record').hidden, false);
    assert.equal(harness.elements.get('voice-identity-cancel').hidden, false);
    assert.equal(harness.elements.get('voice-identity-message').textContent, '');
});

test('recording upload is capped at four seconds of source samples', async () => {
    let segmentBody = null;
    let segmentHeaders = null;
    const harness = createHarness({
        audio: true,
        route(url, options) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'fixed_1' },
                });
            }
            if (url === '/api/voice-identity/enrollment/segment') {
                segmentBody = options.body;
                segmentHeaders = options.headers;
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'fixed_2' },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    await harness.elements.get('voice-identity-record').emit('click');

    assert.equal(Object.prototype.toString.call(segmentBody), '[object ArrayBuffer]');
    assert.equal(segmentBody.byteLength, 16000 * 4 * Int16Array.BYTES_PER_ELEMENT);
    assert.equal(
        segmentHeaders.get('Content-Type'),
        'audio/pcm;format=pcm_s16le;rate=16000;channels=1',
    );
    assert.deepEqual(Array.from(new Uint8Array(segmentBody, 0, 2)), [0xff, 0x1f]);
});

test('underfilled recording times out without uploading a partial segment', async () => {
    let segmentRequests = 0;
    const harness = createHarness({
        audio: true,
        audioBlocks: 92,
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'fixed_1' },
                });
            }
            if (url === '/api/voice-identity/enrollment/segment') {
                segmentRequests += 1;
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'fixed_2' },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    await harness.elements.get('voice-identity-record').emit('click');

    assert.equal(segmentRequests, 0);
    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Request failed.');
    assert.equal(
        harness.getMediaStreams()[0].getTracks().every(track => track.stopped),
        true,
    );
});

test('recording reports a microphone that becomes unavailable', async () => {
    const mediaError = new Error('microphone_locked');
    mediaError.name = 'NotReadableError';
    const harness = createHarness({
        mediaError,
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'fixed_1' },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    await harness.elements.get('voice-identity-record').emit('click');

    assert.equal(
        harness.elements.get('voice-identity-message').textContent,
        'Microphone unavailable.',
    );
});

test('ambiguous segment upload failure refreshes the next recording stage', async () => {
    let statusRequests = 0;
    let segmentRequests = 0;
    const harness = createHarness({
        audio: true,
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                statusRequests += 1;
                return jsonResponse({
                    enrollment: {
                        session_id: 'session-1',
                        stage: statusRequests === 1 ? 'fixed_1' : 'fixed_2',
                    },
                });
            }
            if (url === '/api/voice-identity/enrollment/segment') {
                segmentRequests += 1;
                throw new Error('connection_lost');
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    await harness.elements.get('voice-identity-record').emit('click');

    assert.equal(statusRequests, 2);
    assert.equal(segmentRequests, 1);
    assert.equal(harness.elements.get('voice-identity-prompt').textContent, 'English two');
    assert.equal(harness.elements.get('voice-identity-record').disabled, false);
    assert.equal(harness.elements.get('voice-identity-message').textContent, '');
});

test('a recovered final upload continues through automatic commit', async () => {
    let statusRequests = 0;
    let verificationRequests = 0;
    let commitRequests = 0;
    const harness = createHarness({
        audio: true,
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                statusRequests += 1;
                return jsonResponse(statusRequests === 1
                    ? {
                        enrollment: { session_id: 'session-1', stage: 'free_verify_2' },
                    }
                    : {
                        enrollment: { session_id: 'session-1', stage: 'ready_to_commit' },
                    });
            }
            if (url === '/api/voice-identity/enrollment/verify') {
                verificationRequests += 1;
                throw new Error('connection_lost');
            }
            if (url === '/api/voice-identity/enrollment/commit') {
                commitRequests += 1;
                return jsonResponse({
                    enrollment: { stage: 'idle' },
                    profile: { available: true, state: 'active' },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    await harness.elements.get('voice-identity-record').emit('click');

    assert.equal(verificationRequests, 1);
    assert.equal(statusRequests, 2);
    assert.equal(commitRequests, 1);
    assert.equal(
        harness.elements.get('voice-identity-message').textContent,
        'Enrollment complete.',
    );
    assert.equal(harness.elements.get('voice-identity-start').hidden, false);
});

test('microphone resources are released and reacquired between recording steps', async () => {
    let segments = 0;
    const harness = createHarness({
        audio: true,
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'fixed_1' },
                });
            }
            if (url === '/api/voice-identity/enrollment/segment') {
                segments += 1;
                return jsonResponse({
                    enrollment: {
                        session_id: 'session-1',
                        stage: segments === 1 ? 'fixed_2' : 'fixed_3',
                    },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    const record = harness.elements.get('voice-identity-record');
    await record.emit('click');
    const firstContext = harness.getAudioContext();

    assert.equal(firstContext.state, 'closed');
    assert.equal(harness.getMediaRequests(), 1);
    assert.equal(
        harness.getMediaStreams()[0].getTracks().every(track => track.stopped),
        true,
    );

    await record.emit('click');
    const secondContext = harness.getAudioContext();

    assert.equal(segments, 2);
    assert.notEqual(secondContext, firstContext);
    assert.equal(secondContext.state, 'closed');
    assert.equal(harness.getMediaRequests(), 2);
    assert.equal(
        harness.getMediaStreams().every(stream => (
            stream.getTracks().every(track => track.stopped)
        )),
        true,
    );
});

test('downsampling attenuates microphone energy above the target Nyquist limit', async () => {
    let segmentBody = null;
    const harness = createHarness({
        audio: true,
        audioSample(index) {
            return Math.sin(2 * Math.PI * 12000 * index / 48000);
        },
        route(url, options) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'fixed_1' },
                });
            }
            if (url === '/api/voice-identity/enrollment/segment') {
                segmentBody = options.body;
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'fixed_2' },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    await harness.elements.get('voice-identity-record').emit('click');

    const samples = new Int16Array(segmentBody);
    const stable = samples.subarray(128, samples.length - 128);
    const rms = Math.sqrt(
        stable.reduce((sum, sample) => sum + sample * sample, 0) / stable.length
    ) / 0x8000;
    assert.ok(rms < 0.1, `expected anti-aliased RMS below 0.1, got ${rms}`);
});

test('delete falls back to native confirmation when the shared dialog is unavailable', async () => {
    let confirmations = 0;
    const harness = createHarness({
        nativeConfirm() {
            confirmations += 1;
            return false;
        },
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({
                    enrollment: { stage: 'idle' },
                    profile: { available: true, state: 'active' },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    await harness.elements.get('voice-identity-delete').emit('click');

    assert.equal(confirmations, 1);
    assert.equal(
        harness.fetchCalls.some(call => call.url === '/api/voice-identity/profile'),
        false,
    );
});

test('async delete confirmation locks mutations and restores them when declined', async () => {
    const confirmation = deferred();
    const harness = createHarness({
        showConfirm() {
            return confirmation.promise;
        },
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({
                    enrollment: { stage: 'idle' },
                    profile: { available: true, state: 'active' },
                    filter: { enabled: true },
                });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    const deletion = harness.elements.get('voice-identity-delete').emit('click');

    assert.equal(harness.elements.get('voice-identity-start').disabled, true);
    assert.equal(harness.elements.get('voice-identity-reenroll').disabled, true);
    assert.equal(harness.elements.get('voice-identity-delete').disabled, true);
    assert.equal(harness.elements.get('voice-identity-filter').disabled, true);
    await harness.elements.get('voice-identity-reenroll').emit('click');
    assert.equal(harness.getMediaRequests(), 0);

    confirmation.resolve(false);
    await deletion;

    assert.equal(harness.elements.get('voice-identity-start').disabled, false);
    assert.equal(harness.elements.get('voice-identity-reenroll').disabled, false);
    assert.equal(harness.elements.get('voice-identity-delete').disabled, false);
    assert.equal(harness.elements.get('voice-identity-filter').disabled, false);
    assert.equal(
        harness.fetchCalls.some(call => call.url === '/api/voice-identity/profile'),
        false,
    );
});

test('ambiguous profile deletion reconciles the authoritative absent state', async () => {
    let statusRequests = 0;
    let deleteRequests = 0;
    const harness = createHarness({
        showConfirm() {
            return true;
        },
        route(url, options) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                statusRequests += 1;
                return jsonResponse(statusRequests === 1
                    ? {
                        enrollment: { stage: 'idle' },
                        profile: { available: true, state: 'active' },
                        filter: { enabled: true },
                    }
                    : {
                        enrollment: { stage: 'idle' },
                        profile: { available: false, state: 'empty' },
                        filter: { enabled: false },
                    });
            }
            if (url === '/api/voice-identity/profile' && options.method === 'DELETE') {
                deleteRequests += 1;
                throw new Error('connection_lost');
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    await harness.elements.get('voice-identity-delete').emit('click');

    assert.equal(deleteRequests, 1);
    assert.equal(statusRequests, 2);
    assert.equal(harness.elements.get('voice-identity-delete').disabled, true);
    assert.equal(harness.elements.get('voice-identity-message').textContent, '');
});

test('ambiguous explicit cancellation reconciles a server-side success', async () => {
    let statusRequests = 0;
    const harness = createHarness({
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                statusRequests += 1;
                return jsonResponse(statusRequests === 1
                    ? { enrollment: { session_id: 'session-1', stage: 'fixed_1' } }
                    : { enrollment: { stage: 'idle' } });
            }
            if (url === '/api/voice-identity/enrollment/cancel') {
                return jsonResponse(
                    { error: 'response_lost' },
                    { ok: false, status: 503 },
                );
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    const cancel = harness.elements.get('voice-identity-cancel');
    await cancel.emit('click');

    assert.equal(statusRequests, 2);
    assert.equal(cancel.hidden, true);
    assert.equal(harness.elements.get('voice-identity-message').textContent, '');
});

test('failed explicit cancellation preserves the confirmed session and can be retried', async () => {
    const firstCancellation = deferred();
    let cancellationAttempts = 0;
    let statusRequests = 0;
    const harness = createHarness({
        route(url, options) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                statusRequests += 1;
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'fixed_1' },
                });
            }
            if (url === '/api/voice-identity/enrollment/cancel') {
                cancellationAttempts += 1;
                if (cancellationAttempts === 1) return firstCancellation.promise;
                return jsonResponse({ enrollment: { stage: 'idle' } });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    const cancel = harness.elements.get('voice-identity-cancel');
    const record = harness.elements.get('voice-identity-record');
    const cancellation = cancel.emit('click');
    assert.equal(cancel.disabled, true);
    assert.equal(record.disabled, true);
    await record.emit('click');
    assert.equal(harness.getMediaRequests(), 0);
    firstCancellation.resolve(
        jsonResponse({ error: 'temporary_failure' }, { ok: false, status: 503 })
    );
    await cancellation;

    const firstCall = harness.fetchCalls.find(
        call => call.url === '/api/voice-identity/enrollment/cancel'
    );
    assert.equal(firstCall.options.headers.get('X-Voice-Identity-Enrollment'), 'session-1');
    assert.equal(cancel.hidden, false);
    assert.equal(cancel.disabled, false);
    assert.equal(statusRequests, 2);
    assert.equal(harness.elements.get('voice-identity-message').textContent, 'Request failed.');

    await cancel.emit('click');
    assert.equal(cancellationAttempts, 2);
    assert.equal(cancel.hidden, true);
    assert.equal(harness.elements.get('voice-identity-message').textContent, '');
});

test('window close starts keepalive cancellation without waiting for the response', async () => {
    const keepaliveCancellation = deferred();
    const harness = createHarness({
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({
                    enrollment: { session_id: 'session-1', stage: 'fixed_1' },
                });
            }
            if (url === '/api/voice-identity/enrollment/cancel') {
                return keepaliveCancellation.promise;
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    assert.equal(await harness.beforeClose(), true);
    harness.pagehide();

    const cancellationCalls = harness.fetchCalls.filter(
        call => call.url === '/api/voice-identity/enrollment/cancel'
    );
    assert.equal(cancellationCalls.length, 1);
    assert.equal(cancellationCalls[0].options.keepalive, true);
    keepaliveCancellation.resolve(jsonResponse({}));
    await Promise.resolve();
});

test('window close waits for an in-flight start and cancels its late session', async () => {
    const startRequested = deferred();
    const startResponse = deferred();
    let cancellationRequests = 0;
    const harness = createHarness({
        audio: true,
        scheduleTimeout() {
            return 1;
        },
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({ enrollment: { stage: 'idle' } });
            }
            if (url === '/api/voice-identity/enrollment/start') {
                startRequested.resolve();
                return startResponse.promise;
            }
            if (url === '/api/voice-identity/enrollment/cancel') {
                cancellationRequests += 1;
                return jsonResponse({ enrollment: { stage: 'idle' } });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    const starting = harness.elements.get('voice-identity-start').emit('click');
    await startRequested.promise;
    const closing = harness.beforeClose();
    assert.equal(typeof closing.then, 'function');
    harness.pagehide();
    await Promise.resolve();
    assert.equal(cancellationRequests, 0);

    startResponse.resolve(jsonResponse({
        enrollment: { session_id: 'session-after-close', stage: 'fixed_1' },
    }));
    await starting;
    assert.equal(await closing, true);
    await Promise.resolve();

    assert.equal(cancellationRequests, 1);
    const cancellationCall = harness.fetchCalls.find(
        call => call.url === '/api/voice-identity/enrollment/cancel'
    );
    assert.equal(cancellationCall.options.keepalive, true);
    assert.equal(
        cancellationCall.options.headers.get('X-Voice-Identity-Enrollment'),
        'session-after-close',
    );
});

test('window close stops waiting after the bounded start timeout', async () => {
    const startRequested = deferred();
    const startResponse = deferred();
    const scheduledTimeouts = [];
    let cancellationRequests = 0;
    const harness = createHarness({
        audio: true,
        scheduleTimeout(callback, delay) {
            scheduledTimeouts.push({ callback, delay });
            return scheduledTimeouts.length;
        },
        route(url) {
            if (url === '/api/config/page_config') {
                return jsonResponse({ autostart_csrf_token: 'csrf-token' });
            }
            if (url === '/api/voice-identity/status') {
                return jsonResponse({ enrollment: { stage: 'idle' } });
            }
            if (url === '/api/voice-identity/enrollment/start') {
                startRequested.resolve();
                return startResponse.promise;
            }
            if (url === '/api/voice-identity/enrollment/cancel') {
                cancellationRequests += 1;
                return jsonResponse({ enrollment: { stage: 'idle' } });
            }
            throw new Error(`Unexpected request: ${url}`);
        },
    });

    await harness.initialize();
    void harness.elements.get('voice-identity-start').emit('click');
    await startRequested.promise;
    const closing = harness.beforeClose();
    let closeSettled = false;
    void closing.then(() => {
        closeSettled = true;
    });
    await Promise.resolve();

    assert.equal(closeSettled, false);
    assert.equal(scheduledTimeouts.length, 1);
    assert.equal(scheduledTimeouts[0].delay, 500);
    scheduledTimeouts[0].callback();

    assert.equal(await closing, true);
    assert.equal(cancellationRequests, 0);
});

test('dark theme overrides panel, text, accent, border, and action colors', () => {
    const darkBlock = stylesheet.slice(
        stylesheet.indexOf('[data-theme="dark"] {'),
        stylesheet.indexOf('}', stylesheet.indexOf('[data-theme="dark"] {')) + 1,
    );

    for (const property of [
        '--voice-ink',
        '--voice-muted',
        '--voice-blue-dark',
        '--voice-border',
        '--voice-panel',
        '--voice-danger',
    ]) {
        assert.match(darkBlock, new RegExp(`${property}:`));
    }
    assert.match(stylesheet, /\[data-theme="dark"\] \.secondary-button/);
    assert.match(stylesheet, /\[data-theme="dark"\] \.danger-button/);
    const primaryButtonRule = stylesheet.match(
        /body\.voice-identity-page \.primary-button,\s*body\.voice-identity-page \.record-button\s*\{([^}]*)\}/,
    );
    assert.ok(primaryButtonRule, 'Missing primary/record button color rule');
    assert.match(primaryButtonRule[1], /(?:^|;)\s*color:\s*#082f45\s*;/);
    const secondaryButtonRule = stylesheet.match(/\.secondary-button\s*\{([^}]*)\}/);
    assert.ok(secondaryButtonRule, 'Missing light-theme secondary button rule');
    assert.match(secondaryButtonRule[1], /(?:^|;)\s*color:\s*#075b80\s*;/);
    const windowControlRule = stylesheet.match(
        /\.voice-identity-header \.neko-window-control-btn\s*\{([^}]*)\}/,
    );
    assert.ok(windowControlRule, 'Missing voice header window-control override');
    assert.match(windowControlRule[1], /(?:^|;)\s*color:\s*#082f45\s*;/);
    assert.match(template, /id="voice-identity-timer" aria-hidden="true"/);
    assert.match(template, /<body class="voice-identity-page">/);
    assert.match(
        stylesheet,
        /html\[data-theme="dark"\] body\.voice-identity-page:not\(\.subtitle-web-host\):not\(\.subtitle-window-host\)/,
    );
    assert.match(
        stylesheet,
        /\[data-theme="dark"\] \.voice-close img\s*\{[^}]*filter:\s*brightness\(0\) saturate\(100%\)/,
    );
});
