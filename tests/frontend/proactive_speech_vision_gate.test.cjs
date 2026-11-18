// Guard tests: while in a voice call, the speech-time vision heartbeat may only
// capture the screen while canProactiveVisionRunDuringSpeech() allows it. Every
// entry that starts the heartbeat, its per-tick re-check, and every
// keep-the-stream decision must go through that one function.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const repo = path.join(__dirname, '../..');
const read = (rel) => fs.readFileSync(path.join(repo, rel), 'utf8');
const proactiveSource = read('static/app/app-proactive.js');
const screenSource = read('static/app/app-screen.js');
const buttonsSource = read('static/app/app-buttons.js');
const audioCaptureSource = read('static/app/app-audio-capture.js');
const popupSource = read('static/avatar/avatar-ui-popup.js');

const quietConsole = { log() {}, info() {}, debug() {}, warn() {}, error() {} };

function fakeStream() {
    const stream = { stopped: 0 };
    stream.getTracks = () => [{ stop() { stream.stopped += 1; } }];
    stream.getVideoTracks = () => [{}];
    return stream;
}

function settingsStorage(settings) {
    return {
        getItem: (key) => (key === 'project_neko_settings' ? JSON.stringify(settings) : null),
        setItem() {},
        removeItem() {},
    };
}

// ── app-proactive.js, loaded for real ──────────────────────────────────────

function loadProactive({ privacyAllowsVision, appScreen = {} }) {
    const settings = { proactiveVisionEnabled: privacyAllowsVision };
    const intervals = new Map();
    let nextId = 1;
    const sent = [];
    const S = {
        isRecording: true,
        proactiveVisionInterval: 10, // code default (app-state.js)
        proactiveVisionEnabled: privacyAllowsVision,
        proactiveChatEnabled: true,
        proactiveVisionChatEnabled: true,
        proactiveChatTimer: null,
        socket: { readyState: 1, send: (message) => sent.push(JSON.parse(message)) },
    };
    const document = {
        body: { classList: { contains: () => false }, getAttribute: () => null },
        addEventListener() {},
        getElementById: () => null,
        querySelector: () => null,
        hasFocus: () => true,
    };
    const window = {
        console: quietConsole,
        location: { pathname: '/', origin: 'http://local.test' },
        appState: S,
        appConst: {},
        document,
        navigator: { userAgent: 'node' },
        WebSocket: { OPEN: 1 },
        localStorage: settingsStorage(settings),
        addEventListener() {},
        setTimeout: () => 0,
        clearTimeout() {},
        setInterval(fn, ms) {
            const id = nextId++;
            intervals.set(id, { fn, ms });
            return id;
        },
        clearInterval(id) { intervals.delete(id); },
        fetch: () => Promise.resolve({ ok: true, json: async () => ({}) }),
        appScreen: Object.assign({
            acquireOrReuseCachedStream: async () => ({ fake: 'stream' }),
            captureFrameFromStream: async () => ({ dataUrl: 'data:image/jpeg;base64,AAAA' }),
            buildStreamDataMessage: (data, inputType) => ({ action: 'stream_data', input_type: inputType, data }),
            fetchBackendScreenshot: async () => ({ dataUrl: null }),
        }, appScreen),
        detectScreenshotCaptureType: () => 'screen',
    };
    window.window = window;
    vm.createContext(window);
    vm.runInContext(proactiveSource, window, { filename: 'app-proactive.js' });
    const frames = () => sent.filter((message) => message.action === 'stream_data').length;
    const heartbeat = () => [...intervals.values()].find((timer) => timer.ms === S.proactiveVisionInterval * 1000);
    return { window, S, settings, frames, heartbeat };
}

test('heartbeat runs and sends frames while the gate allows it', async () => {
    const h = loadProactive({ privacyAllowsVision: true });
    assert.equal(typeof h.window.canProactiveVisionRunDuringSpeech, 'function');
    assert.equal(h.window.canProactiveVisionRunDuringSpeech(), true);

    h.window.startProactiveVisionDuringSpeech();
    const timer = h.heartbeat();
    assert.ok(timer, 'heartbeat timer should be armed');
    await timer.fn();
    await timer.fn();
    assert.equal(h.frames(), 2);
});

test('heartbeat does not start while the gate blocks it', async () => {
    const h = loadProactive({ privacyAllowsVision: false });
    assert.equal(h.window.canProactiveVisionRunDuringSpeech(), false);

    h.window.startProactiveVisionDuringSpeech();

    assert.equal(h.heartbeat(), undefined);
    assert.equal(h.frames(), 0);
});

test('a tick after revocation stops the heartbeat without sending', async () => {
    const h = loadProactive({ privacyAllowsVision: true });
    h.window.startProactiveVisionDuringSpeech();
    const timer = h.heartbeat();
    await timer.fn();
    assert.equal(h.frames(), 1);

    h.settings.proactiveVisionEnabled = false; // revoked in this or another window
    await timer.fn();
    await timer.fn();

    assert.equal(h.frames(), 1);
    assert.equal(h.heartbeat(), undefined, 'heartbeat timer should be cleared');
});

test('releaseProactiveVisionStream releases during a call once the gate blocks', () => {
    const blocked = loadProactive({ privacyAllowsVision: false });
    blocked.S.proactiveVisionChatEnabled = false;
    const stream = fakeStream();
    blocked.S.screenCaptureStream = stream;

    blocked.window.releaseProactiveVisionStream();

    assert.equal(stream.stopped, 1);
    assert.equal(blocked.S.screenCaptureStream, null);

    const allowed = loadProactive({ privacyAllowsVision: true });
    allowed.S.proactiveVisionChatEnabled = false;
    const kept = fakeStream();
    allowed.S.screenCaptureStream = kept;

    allowed.window.releaseProactiveVisionStream();

    assert.equal(kept.stopped, 0, 'stream stays while the call may still see the screen');
});

test('the gate follows the latest saved privacy setting across windows', () => {
    const h = loadProactive({ privacyAllowsVision: true });
    h.S.proactiveVisionEnabled = true;
    h.settings.proactiveVisionEnabled = false; // saved by another window
    assert.equal(h.window.canProactiveVisionRunDuringSpeech(), false);
});

// ── app-screen.js, loaded for real ─────────────────────────────────────────

function loadScreen({ gate }) {
    const S = {
        isRecording: true,
        proactiveVisionEnabled: true, // stale in-memory value must not decide
        proactiveVisionChatEnabled: false,
        proactiveChatEnabled: false,
    };
    const timers = [];
    const started = [];
    const element = () => ({
        style: {},
        classList: { add() {}, remove() {}, contains: () => false },
        appendChild() {},
        setAttribute() {},
    });
    const window = {
        console: quietConsole,
        appState: S,
        appConst: { SCREEN_IDLE_TIMEOUT: 300000, SCREEN_CHECK_INTERVAL: 60000 },
        appUtils: { isMobile: () => false },
        safeT: (key, fallback) => fallback || key,
        t: (key) => key,
        location: { pathname: '/', origin: 'http://local.test', search: '' },
        navigator: { userAgent: 'node', mediaDevices: {} },
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; },
        clearTimeout() {},
        setInterval: () => 0,
        clearInterval() {},
        addEventListener() {},
        removeEventListener() {},
        dispatchEvent() {},
        startProactiveVisionDuringSpeech() { started.push('start'); },
        canProactiveVisionRunDuringSpeech: () => gate.value,
        document: {
            getElementById: () => null,
            querySelector: () => null,
            querySelectorAll: () => [],
            addEventListener() {},
            createElement: element,
            body: { appendChild() {}, classList: { contains: () => false } },
            readyState: 'complete',
        },
    };
    window.window = window;
    vm.createContext(window);
    vm.runInContext(screenSource, window, { filename: 'app-screen.js' });
    return { window, S, timers, started };
}

test('stopping manual sharing keeps the stream and resumes the heartbeat only when allowed', async () => {
    const allowed = loadScreen({ gate: { value: true } });
    const kept = fakeStream();
    allowed.S.screenCaptureStream = kept;
    await allowed.window.stopScreenSharing();
    assert.equal(kept.stopped, 0);
    assert.deepEqual(allowed.started, ['start']);

    const blocked = loadScreen({ gate: { value: false } });
    const released = fakeStream();
    blocked.S.screenCaptureStream = released;
    await blocked.window.stopScreenSharing();
    assert.equal(released.stopped, 1, 'stream must be released');
    assert.equal(blocked.S.screenCaptureStream, null);
    assert.deepEqual(blocked.started, [], 'heartbeat must not be started');
});

test('idle check stops renewing the stream once the gate blocks', async () => {
    const gate = { value: true };
    const h = loadScreen({ gate });
    const stream = fakeStream();
    h.S.screenCaptureStream = stream;
    h.S.screenCaptureStreamLastUsed = Date.now() - 10 * 60 * 1000;

    h.window.scheduleScreenCaptureIdleCheck();
    await h.timers.at(-1).fn();
    assert.equal(stream.stopped, 0, 'renewed while allowed');
    assert.ok(h.S.screenCaptureStream);

    gate.value = false;
    h.S.screenCaptureStreamLastUsed = Date.now() - 10 * 60 * 1000;
    await h.timers.at(-1).fn();
    assert.equal(stream.stopped, 1, 'released once blocked');
    assert.equal(h.S.screenCaptureStream, null);
});

// ── entry blocks inside larger functions, run from the real source ─────────

function between(source, start, end) {
    const from = source.indexOf(start);
    assert.ok(from >= 0, `marker not found: ${start}`);
    const to = source.indexOf(end, from + start.length);
    assert.ok(to > from, `end marker not found: ${end}`);
    return source.slice(from, to);
}

function statementAt(source, start) {
    const from = source.indexOf(start);
    assert.ok(from >= 0, `marker not found: ${start}`);
    const open = source.indexOf('{', from);
    let depth = 0;
    for (let i = open; i < source.length; i += 1) {
        if (source[i] === '{') depth += 1;
        else if (source[i] === '}') {
            depth -= 1;
            if (depth === 0) return source.slice(from, i + 1);
        }
    }
    throw new Error(`unbalanced block after ${start}`);
}

async function runBlock(code, globals) {
    const context = vm.createContext(Object.assign({ console: quietConsole }, globals));
    context.window = context;
    await vm.runInContext(`(async () => {\n${code}\n})()`, context);
    return context;
}

function entryWindow(gate) {
    const calls = [];
    return {
        calls,
        globals: {
            appState: { isRecording: true, proactiveVisionEnabled: true },
            isRecording: true,
            t: (key) => key,
            isNekoHomeTutorialProactiveSuppressed: () => false,
            canProactiveVisionRunDuringSpeech: () => gate,
            acquireProactiveVisionStream: async () => { calls.push('acquire'); },
            startProactiveVisionDuringSpeech: () => { calls.push('start'); },
            releaseProactiveVisionStream: () => { calls.push('release'); },
            resetProactiveChatBackoff: () => {},
            saveNEKOSettings: () => {},
        },
    };
}

const voiceStartBlock = between(
    buttonsSource,
    '// Start proactive vision during speech if enabled.',
    '// acquireProactiveVisionStream awaits a backend request AND a',
);

test('starting a voice call goes through the gate', async () => {
    for (const gate of [true, false]) {
        const { calls, globals } = entryWindow(gate);
        globals.S = globals.appState;
        await runBlock(voiceStartBlock, globals);
        assert.deepEqual(calls, gate ? ['acquire', 'start'] : [], `gate=${gate}`);
    }
});

const micSwitchSnapshot = between(
    audioCaptureSource,
    'const tutorialHoldsVision = typeof window.isNekoHomeTutorialProactiveSuppressed',
    'const shouldRestartScreening',
);
const micSwitchRestart = statementAt(
    audioCaptureSource,
    'if (shouldRestartProactiveVision',
);

test('switching microphones restarts the heartbeat only through the gate', async () => {
    for (const gate of [true, false]) {
        const { calls, globals } = entryWindow(gate);
        globals.S = globals.appState;
        await runBlock(micSwitchSnapshot + micSwitchRestart, globals);
        assert.deepEqual(calls, gate ? ['acquire', 'start'] : [], `gate=${gate}`);
    }
});

test('a revocation during the microphone switch is honored at restart', async () => {
    const { calls, globals } = entryWindow(true);
    globals.S = globals.appState;
    const code = micSwitchSnapshot
        + '\nwindow.canProactiveVisionRunDuringSpeech = () => false;\n'
        + micSwitchRestart;
    await runBlock(code, globals);
    assert.deepEqual(calls, []);
});

const privacyToggleBlock = statementAt(
    popupSource,
    "if (toggle.id === 'proactive-vision')",
);

test('turning privacy mode off starts the heartbeat only through the gate', async () => {
    for (const gate of [true, false]) {
        const { calls, globals } = entryWindow(gate);
        globals.toggle = { id: 'proactive-vision', inverted: true };
        globals.isChecked = false; // privacy mode off -> vision allowed
        await runBlock(privacyToggleBlock, globals);
        assert.deepEqual(
            calls.filter((name) => name === 'start'),
            gate ? ['start'] : [],
            `gate=${gate}`,
        );
    }
});

// The source-slice tests above run each entry block with the real globals it
// reads; if a marker comment moves, they fail on the marker assert instead of
// passing silently.

test('a missing gate function counts as "may not see the screen"', async () => {
    const blocks = [
        ['voice start', voiceStartBlock, {}],
        ['mic switch', micSwitchSnapshot + micSwitchRestart, {}],
        ['privacy toggle', privacyToggleBlock, { toggle: { id: 'proactive-vision', inverted: true }, isChecked: false }],
    ];
    for (const [label, code, extra] of blocks) {
        const { calls, globals } = entryWindow(true);
        delete globals.canProactiveVisionRunDuringSpeech;
        globals.S = globals.appState;
        Object.assign(globals, extra);
        await runBlock(code, globals);
        assert.deepEqual(calls.filter((name) => name === 'start'), [], label);
    }

    const screen = loadScreen({ gate: { value: true } });
    delete screen.window.canProactiveVisionRunDuringSpeech;
    const stream = fakeStream();
    screen.S.screenCaptureStream = stream;
    await screen.window.stopScreenSharing();
    assert.equal(stream.stopped, 1, 'app-screen must release without the gate');
    assert.deepEqual(screen.started, []);
});

// ── user scenario: app-proactive.js + app-screen.js loaded together ────────
// Voice call, proactive chat on with only music recommendations, screen
// sub-switch off, manual screen share running; the user stops sharing.

function loadUserScenario({ privacyAllowsVision }) {
    const intervals = [];
    const sent = [];
    let trackStops = 0;
    const settings = {
        proactiveVisionEnabled: privacyAllowsVision,
        proactiveChatEnabled: true,
        proactiveVisionChatEnabled: false,
        proactiveMusicEnabled: true,
    };
    const S = Object.assign({
        isRecording: true,
        proactiveVisionInterval: 10, // code default (app-state.js)
        proactiveChatInterval: 15,
        socket: { readyState: 1, send: (message) => sent.push(JSON.parse(message)) },
    }, settings);
    const track = { readyState: 'live', stop() { trackStops += 1; this.readyState = 'ended'; } };
    S.screenCaptureStream = { active: true, getTracks: () => [track], getVideoTracks: () => [track] };
    S.screenCaptureStreamLastUsed = Date.now();
    const elements = {};
    const element = (id) => {
        const classes = new Set(id === 'screenButton' ? ['active'] : []);
        return {
            id, disabled: false, style: {}, dataset: {},
            classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c), toggle() {} },
            addEventListener() {}, removeEventListener() {}, setAttribute() {}, getAttribute: () => null,
            appendChild() {}, querySelector: () => null, querySelectorAll: () => [],
        };
    };
    const document = {
        body: element('body'), documentElement: element('html'), addEventListener() {}, hasFocus: () => true,
        getElementById: (id) => (elements[id] = elements[id] || element(id)),
        querySelector: () => null, querySelectorAll: () => [], createElement: (tag) => element(tag),
    };
    const noop = () => {};
    const window = {
        console: quietConsole,
        location: { pathname: '/', origin: 'http://local.test', search: '' },
        appState: S,
        appConst: { MAX_SCREENSHOT_WIDTH: 1280, MAX_SCREENSHOT_HEIGHT: 720 },
        appUtils: { isMobile: () => false },
        safeT: (key, fallback) => fallback, t: (key) => key,
        document, navigator: { userAgent: 'node', mediaDevices: {} }, WebSocket: { OPEN: 1 },
        localStorage: settingsStorage(settings),
        sessionStorage: { getItem: () => null, setItem: noop, removeItem: noop },
        addEventListener: noop, dispatchEvent: noop, CustomEvent: function CustomEvent() {},
        setTimeout: () => 0, clearTimeout: noop, clearInterval: noop,
        setInterval(fn, ms) { intervals.push({ fn, ms }); return intervals.length; },
        fetch: () => Promise.resolve({ ok: false, status: 501, json: async () => ({ success: false }) }),
        showStatusToast: noop, detectScreenshotCaptureType: () => 'window',
    };
    window.window = window;
    vm.createContext(window);
    vm.runInContext(proactiveSource, window, { filename: 'app-proactive.js' });
    vm.runInContext(screenSource, window, { filename: 'app-screen.js' });
    window.appScreen.captureFrameFromStream = async () => ({ dataUrl: 'data:image/jpeg;base64,FRAME' });
    return {
        window, S,
        trackStops: () => trackStops,
        frames: () => sent.filter((message) => message.action === 'stream_data').length,
        heartbeat: () => intervals.find((timer) => timer.ms === S.proactiveVisionInterval * 1000),
    };
}

async function stopSharingThenTick(h, ticks) {
    await h.window.stopScreenSharing();
    const timer = h.heartbeat();
    if (timer) for (let i = 0; i < ticks; i += 1) await timer.fn();
}

test('user scenario, privacy mode on: stopping the share releases the stream and sends nothing', async () => {
    const h = loadUserScenario({ privacyAllowsVision: false });
    await stopSharingThenTick(h, 6);
    assert.equal(h.trackStops(), 1);
    assert.equal(h.S.screenCaptureStream, null);
    assert.equal(h.heartbeat(), undefined);
    assert.equal(h.frames(), 0);
});

// Current rule (part 1): only privacy mode decides. Update together with the
// todo case below when the gate changes.
test('user scenario, privacy mode off: the heartbeat takes over the kept stream', async () => {
    const h = loadUserScenario({ privacyAllowsVision: true });
    await stopSharingThenTick(h, 6);
    assert.equal(h.trackStops(), 0);
    assert.ok(h.heartbeat(), 'heartbeat resumes on the kept stream');
    assert.equal(h.frames(), 6);
});

// Pending product decision (part 2): the screen sub-switch is off here, so
// the heartbeat should not take over after the share stops.
test('user scenario, privacy mode off but screen sub-switch off: no frames after stopping the share',
    { todo: 'part 2: gate also requires proactive chat + screen sub-switch' },
    async () => {
        const h = loadUserScenario({ privacyAllowsVision: true });
        await stopSharingThenTick(h, 6);
        assert.equal(h.trackStops(), 1);
        assert.equal(h.frames(), 0);
    });

// ── races between capture and a revocation ─────────────────────────────────

test('the gate stays closed when only this window revoked (save failed)', () => {
    const h = loadProactive({ privacyAllowsVision: true });
    h.S.proactiveVisionEnabled = false; // storage still says true
    assert.equal(h.window.canProactiveVisionRunDuringSpeech(), false);
});

test('a frame captured across revoke-then-regrant is not sent', async () => {
    let h;
    h = loadProactive({
        privacyAllowsVision: true,
        appScreen: {
            captureFrameFromStream: async () => {
                h.window.appProactive.noteProactiveVisionConsent(false);
                h.window.appProactive.noteProactiveVisionConsent(true);
                return { dataUrl: 'data:image/jpeg;base64,OLD' };
            },
        },
    });
    h.window.startProactiveVisionDuringSpeech();
    await h.heartbeat().fn();
    assert.equal(h.frames(), 0);
    assert.equal(h.window.canProactiveVisionRunDuringSpeech(), true, 'consent is back on');
});

test('a stale frame does not stop the heartbeat restarted by the regrant', async () => {
    let h;
    let first = true;
    h = loadProactive({
        privacyAllowsVision: true,
        appScreen: {
            captureFrameFromStream: async () => {
                if (first) {
                    first = false;
                    // privacy on then off again: the toggle restarts the heartbeat
                    h.window.appProactive.noteProactiveVisionConsent(false);
                    h.window.appProactive.noteProactiveVisionConsent(true);
                    h.window.startProactiveVisionDuringSpeech();
                }
                return { dataUrl: 'data:image/jpeg;base64,FRAME' };
            },
        },
    });
    h.window.startProactiveVisionDuringSpeech();
    await h.heartbeat().fn();
    assert.equal(h.frames(), 0, 'the frame from before the revocation is dropped');
    const restarted = h.heartbeat();
    assert.ok(restarted, 'the heartbeat started by the regrant keeps running');
    await restarted.fn();
    assert.equal(h.frames(), 1);
});

test('no desktop screenshot is started after a revocation during capture', async () => {
    let backendCalls = 0;
    let h;
    h = loadProactive({
        privacyAllowsVision: true,
        appScreen: {
            acquireOrReuseCachedStream: async () => {
                h.settings.proactiveVisionEnabled = false;
                return null;
            },
            fetchBackendScreenshot: async () => { backendCalls += 1; return { dataUrl: 'data:image/jpeg;base64,DESK' }; },
        },
    });
    h.window.startProactiveVisionDuringSpeech();
    await h.heartbeat().fn();
    assert.equal(backendCalls, 0);
    assert.equal(h.frames(), 0);
});

test('a stream obtained after consent was revoked is released, not kept', async () => {
    let h;
    const stream = fakeStream();
    h = loadProactive({
        privacyAllowsVision: true,
        appScreen: {
            acquireOrReuseCachedStream: async () => {
                h.settings.proactiveVisionEnabled = false; // revoked while the picker was open
                h.S.screenCaptureStream = stream;
                return stream;
            },
        },
    });
    h.S.proactiveVisionChatEnabled = false;

    const ok = await h.window.acquireProactiveVisionStream();

    assert.equal(ok, false);
    assert.equal(stream.stopped, 1);
    assert.equal(h.S.screenCaptureStream, null);
});

test('text-chat vision does not keep the stream after another window revoked', async () => {
    const h = loadScreen({ gate: { value: false } });
    h.S.isRecording = false;
    h.S.proactiveVisionChatEnabled = true;
    h.S.proactiveChatEnabled = true;
    h.window.appProactive = { isProactiveVisionEnabledNow: () => false };
    const stream = fakeStream();
    h.S.screenCaptureStream = stream;

    await h.window.stopScreenSharing();

    assert.equal(stream.stopped, 1);

    const kept = loadScreen({ gate: { value: false } });
    kept.S.isRecording = false;
    kept.S.proactiveVisionChatEnabled = true;
    kept.S.proactiveChatEnabled = true;
    kept.window.appProactive = { isProactiveVisionEnabledNow: () => true };
    const keptStream = fakeStream();
    kept.S.screenCaptureStream = keptStream;
    await kept.window.stopScreenSharing();
    assert.equal(keptStream.stopped, 0, 'text vision still holds the stream while allowed');
});

test('no native desktop capture is started after a revocation during capture', async () => {
    let nativeCalls = 0;
    let h;
    h = loadProactive({
        privacyAllowsVision: true,
        appScreen: {
            acquireOrReuseCachedStream: async () => {
                h.settings.proactiveVisionEnabled = false;
                return null;
            },
        },
    });
    h.S.selectedScreenSourceId = 'window:1';
    h.window.getDesktopCaptureProvider = () => ({
        nativeFrameCapture: true,
        captureSourceAsDataUrl: async () => { nativeCalls += 1; return { success: true, dataUrl: 'data:image/png;base64,X' }; },
    });
    h.window.captureDesktopSourceWithTimeout = async (provider, method, id) => provider[method](id);
    h.window.startProactiveVisionDuringSpeech();
    await h.heartbeat().fn();
    assert.equal(nativeCalls, 0);
    assert.equal(h.frames(), 0);
});

test('acquiring the vision stream stops when consent is revoked during the window check', async () => {
    let backendCalls = 0;
    let h;
    h = loadProactive({
        privacyAllowsVision: true,
        appScreen: {
            fetchBackendScreenshot: async () => { backendCalls += 1; return { dataUrl: 'data:image/jpeg;base64,DESK' }; },
        },
    });
    h.window.prepareRememberedWindowCapture = async () => {
        h.settings.proactiveVisionEnabled = false;
        return { required: false, allowed: true };
    };
    assert.equal(await h.window.acquireProactiveVisionStream(), false);
    assert.equal(backendCalls, 0);
});
