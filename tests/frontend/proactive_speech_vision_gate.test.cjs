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
        holdsProactiveVisionStream: () => S.isRecording && gate.value,
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
            holdsProactiveVisionStream: () => gate,
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
    delete screen.window.holdsProactiveVisionStream;
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
        window, S, settings,
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

// The reported case: privacy mode off, only music recommendations on, the
// Screen Share option off. Stopping the manual share must not hand the stream
// to the speech heartbeat.
test('user scenario, privacy mode off but screen sub-switch off: no frames after stopping the share', async () => {
    const h = loadUserScenario({ privacyAllowsVision: true });
    await stopSharingThenTick(h, 6);
    assert.equal(h.trackStops(), 1);
    assert.equal(h.S.screenCaptureStream, null);
    assert.equal(h.heartbeat(), undefined);
    assert.equal(h.frames(), 0);
});

test('user scenario, privacy mode off and Screen Share on: the heartbeat takes over the kept stream', async () => {
    const h = loadUserScenario({ privacyAllowsVision: true });
    h.S.proactiveVisionChatEnabled = true;
    h.settings.proactiveVisionChatEnabled = true;
    await stopSharingThenTick(h, 6);
    assert.equal(h.trackStops(), 0);
    assert.ok(h.heartbeat(), 'heartbeat resumes on the kept stream');
    assert.equal(h.frames(), 6);
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

    const ok = await h.window.acquireProactiveVisionStream();

    assert.equal(ok, false);
    assert.equal(stream.stopped, 1);
    assert.equal(h.S.screenCaptureStream, null);
});

test('text-chat vision does not keep the stream after another window revoked', async () => {
    // Real app-proactive + app-screen: not in a call, text-chat vision on.
    const h = loadUserScenario({ privacyAllowsVision: true });
    h.S.isRecording = false;
    h.S.proactiveVisionChatEnabled = true;
    h.settings.proactiveVisionEnabled = false; // saved by another window; S still true
    await h.window.stopScreenSharing();
    assert.equal(h.trackStops(), 1);

    const kept = loadUserScenario({ privacyAllowsVision: true });
    kept.S.isRecording = false;
    kept.S.proactiveVisionChatEnabled = true;
    kept.settings.proactiveVisionChatEnabled = true;
    await kept.window.stopScreenSharing();
    assert.equal(kept.trackStops(), 0, 'text vision still holds the stream while allowed');
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

// ── a revocation whose local save failed (storage still says allowed) ──────

test('failed save: acquiring stops when this window revoked during the window check', async () => {
    let backendCalls = 0;
    let h;
    h = loadProactive({
        privacyAllowsVision: true,
        appScreen: {
            fetchBackendScreenshot: async () => { backendCalls += 1; return { dataUrl: 'data:image/jpeg;base64,DESK' }; },
        },
    });
    h.window.prepareRememberedWindowCapture = async () => {
        h.S.proactiveVisionEnabled = false; // saved value stays true
        return { required: false, allowed: true };
    };
    assert.equal(await h.window.acquireProactiveVisionStream(), false);
    assert.equal(backendCalls, 0);
});

test('failed save: a late stream is released and text-chat vision does not keep it', async () => {
    let h;
    const stream = fakeStream();
    h = loadProactive({
        privacyAllowsVision: true,
        appScreen: {
            acquireOrReuseCachedStream: async () => {
                h.S.proactiveVisionEnabled = false; // saved value stays true
                h.S.screenCaptureStream = stream;
                return stream;
            },
        },
    });
    h.S.proactiveVisionChatEnabled = true; // text-chat vision would otherwise keep it

    assert.equal(await h.window.acquireProactiveVisionStream(), false);
    assert.equal(stream.stopped, 1);
    assert.equal(h.S.screenCaptureStream, null);
});

test('failed save: a revocation during the backend probe stops before any stream capture', async () => {
    for (const probe of ['data:image/jpeg;base64,DESK', null]) {
        let streamCalls = 0;
        let h;
        h = loadProactive({
            privacyAllowsVision: true,
            appScreen: {
                fetchBackendScreenshot: async () => {
                    h.S.proactiveVisionEnabled = false; // saved value stays true
                    return { dataUrl: probe };
                },
                acquireOrReuseCachedStream: async () => { streamCalls += 1; return null; },
            },
        });
        assert.equal(await h.window.acquireProactiveVisionStream(), false, `probe=${probe}`);
        assert.equal(streamCalls, 0, `probe=${probe}`);
    }
});

// ── the switches: Proactive Chat and its Screen Share option ────────────────

function runningCall() {
    const h = loadProactive({ privacyAllowsVision: true });
    h.S.proactiveChatEnabled = true;
    h.S.proactiveVisionChatEnabled = true;
    const stream = fakeStream();
    h.S.screenCaptureStream = stream;
    h.window.startProactiveVisionDuringSpeech();
    assert.ok(h.heartbeat(), 'heartbeat runs before the switch changes');
    return { h, stream };
}

for (const key of ['proactiveChatEnabled', 'proactiveVisionChatEnabled']) {
    test(`turning ${key} off stops the heartbeat and releases the stream at once`, async () => {
        const { h, stream } = runningCall();
        const timer = h.heartbeat();
        h.S[key] = false;
        h.window.syncProactiveVisionDuringSpeech();
        assert.equal(h.heartbeat(), undefined, 'heartbeat timer cleared');
        assert.equal(stream.stopped, 1, 'capture stopped, indicator goes out');
        assert.equal(h.S.screenCaptureStream, null);
        await timer.fn(); // a tick already queued must not send either
        assert.equal(h.frames(), 0);
    });

    test(`${key} closed in another window stops this window's heartbeat`, () => {
        const { h, stream } = runningCall();
        h.settings[key] = false; // saved elsewhere; this window not synced yet
        assert.equal(h.window.canProactiveVisionRunDuringSpeech(), false);
        h.window.syncProactiveVisionDuringSpeech();
        assert.equal(h.heartbeat(), undefined);
        assert.equal(stream.stopped, 1);
    });
}

test('turning the switch back on during a call restarts the heartbeat', async () => {
    const { h } = runningCall();
    h.S.proactiveVisionChatEnabled = false;
    h.window.syncProactiveVisionDuringSpeech();
    assert.equal(h.heartbeat(), undefined);

    h.S.proactiveVisionChatEnabled = true;
    h.window.syncProactiveVisionDuringSpeech();
    const timer = h.heartbeat();
    assert.ok(timer, 'heartbeat restarted');
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(h.frames(), 1, 'a fresh frame goes out at once, not after one interval');
    await timer.fn();
    assert.equal(h.frames(), 2);
});

test('turning the switch on outside a call does not start the heartbeat', () => {
    const h = loadProactive({ privacyAllowsVision: true });
    h.S.isRecording = false;
    h.window.syncProactiveVisionDuringSpeech();
    assert.equal(h.heartbeat(), undefined);
});

test('repeated syncs while off release only once and spare a manual share being started', () => {
    const { h, stream } = runningCall();
    h.S.proactiveChatEnabled = false;
    h.window.syncProactiveVisionDuringSpeech();
    assert.equal(stream.stopped, 1);

    const later = fakeStream(); // e.g. a stream a manual share obtained afterwards
    h.S.screenCaptureStream = later;
    h.window.syncProactiveVisionDuringSpeech(); // any later settings save
    assert.equal(later.stopped, 0);

    const pending = runningCall();
    pending.h.window.isScreenSharingStartPending = () => true;
    pending.h.S.proactiveChatEnabled = false;
    pending.h.window.syncProactiveVisionDuringSpeech();
    assert.equal(pending.h.heartbeat(), undefined, 'heartbeat still stops');
    assert.equal(pending.stream.stopped, 0, 'the stream a manual share is about to use stays');
});

// app-settings is too large to load here. Its two write paths (every save and
// every cross-window sync) must run the switch reconcile right after the
// privacy check; see syncSpeechVisionWithSwitches in app-settings.js.
test('app-settings reconciles the heartbeat after every save and cross-window sync', () => {
    const settingsSource = read('static/app/app-settings.js');
    const calls = settingsSource.match(/stopVisionAfterPrivacyEnabled\(\);\s*\n\s*syncSpeechVisionWithSwitches\(\);/g) || [];
    assert.equal(calls.length, 2);
    assert.match(settingsSource, /function syncSpeechVisionWithSwitches\(\) \{[\s\S]*?window\.syncProactiveVisionDuringSpeech\(\)/);
});

test('turning privacy mode off acquires through the shared click entry', async () => {
    for (const holds of [true, false]) {
        const { calls, globals } = entryWindow(holds);
        globals.toggle = { id: 'proactive-vision', inverted: true };
        globals.isChecked = false;
        globals.acquireProactiveVisionAfterUserEnable = () => { calls.push('acquireAfterEnable'); };
        await runBlock(privacyToggleBlock, globals);
        // The shared click entry decides (it checks the keep-the-stream rule).
        assert.deepEqual(calls.filter((name) => name.startsWith('acquire')), ['acquireAfterEnable'], `holds=${holds}`);
    }
});

test('turning a switch back on by click re-acquires the screen stream', async () => {
    const { h } = runningCall();
    let acquires = 0;
    h.window.appScreen.acquireOrReuseCachedStream = async () => { acquires += 1; return fakeStream(); };
    h.S.proactiveChatEnabled = false;
    h.window.syncProactiveVisionDuringSpeech();
    h.window.acquireProactiveVisionAfterUserEnable(); // switch still off: nothing to do
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(acquires, 0);

    h.S.proactiveChatEnabled = true;
    h.window.acquireProactiveVisionAfterUserEnable();
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(acquires, 1);
});

test('a stream that arrives after a switch was turned off elsewhere is released', async () => {
    let h;
    const stream = fakeStream();
    h = loadProactive({
        privacyAllowsVision: true,
        appScreen: {
            acquireOrReuseCachedStream: async () => {
                h.settings.proactiveVisionChatEnabled = false; // another window
                h.S.screenCaptureStream = stream;
                return stream;
            },
        },
    });
    assert.equal(await h.window.acquireProactiveVisionStream(), false);
    assert.equal(stream.stopped, 1);
});

test('the voice nudge asks for vision only while the speech gate allows it', () => {
    const proactive = read('static/app/app-proactive.js');
    const block = proactive.slice(proactive.indexOf('var voiceModes = [];'), proactive.indexOf("voiceModes.push('vision');"));
    assert.match(block, /if \(canProactiveVisionRunDuringSpeech\(\)\) \{\s*$/);
});

test('both switch click handlers re-acquire the stream after turning on', () => {
    const drag = read('static/avatar/avatar-ui-drag.js');
    const popup = read('static/avatar/avatar-ui-popup.js');
    assert.match(drag, /if \(globalVarName === 'proactiveVisionChatEnabled'\s*&& typeof window\.acquireProactiveVisionAfterUserEnable === 'function'\) \{\s*window\.acquireProactiveVisionAfterUserEnable\(\)/);
    const chatToggle = statementAt(popup, "else if (toggle.id === 'proactive-chat') {");
    assert.match(chatToggle, /if \(isChecked && typeof window\.acquireProactiveVisionAfterUserEnable === 'function'\)/);
});

test('re-enabling by click sends a frame once the re-acquired stream arrives', async () => {
    const h = loadProactive({ privacyAllowsVision: true });
    // Browser after the switch went off: no cached stream, no backend capture.
    h.S.screenCaptureStream = null;
    let streamReady = false;
    h.window.appScreen.acquireOrReuseCachedStream = async (opts) => {
        if (opts && opts.allowPrompt === false && !streamReady) return null;
        streamReady = true;
        const stream = fakeStream();
        h.S.screenCaptureStream = stream;
        return stream;
    };
    h.window.syncProactiveVisionDuringSpeech(); // the save on click
    assert.ok(h.heartbeat(), 'heartbeat restarted');
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(h.frames(), 0, 'no stream yet, nothing to send');

    h.window.acquireProactiveVisionAfterUserEnable(); // the click handler
    for (let i = 0; i < 6; i += 1) await new Promise((resolve) => setImmediate(resolve));
    assert.equal(h.frames(), 1, 'a fresh frame goes out as soon as the stream is back');
});

test('turning a switch on by click outside a call acquires for text-chat screenshots, without sending frames', async () => {
    const h = loadProactive({ privacyAllowsVision: true });
    h.S.isRecording = false;
    h.S.screenCaptureStream = null;
    let acquires = 0;
    h.window.appScreen.acquireOrReuseCachedStream = async () => { acquires += 1; return fakeStream(); };
    h.window.acquireProactiveVisionAfterUserEnable();
    for (let i = 0; i < 6; i += 1) await new Promise((resolve) => setImmediate(resolve));
    assert.equal(acquires, 1, 'privacy off first, Screen Share on last: the stream is still obtained');
    assert.equal(h.frames(), 0);
    assert.equal(h.heartbeat(), undefined);
});

test('turning a switch on by click does not acquire while a manual picker is open or the gate is closed', async () => {
    for (const setup of [
        (h) => { h.window.isScreenSharingStartPending = () => true; },
        (h) => { h.S.proactiveVisionChatEnabled = false; },
        (h) => { h.settings.proactiveVisionEnabled = false; },
    ]) {
        const h = loadProactive({ privacyAllowsVision: true });
        let acquires = 0;
        h.window.appScreen.acquireOrReuseCachedStream = async () => { acquires += 1; return fakeStream(); };
        setup(h);
        h.window.acquireProactiveVisionAfterUserEnable();
        await new Promise((resolve) => setImmediate(resolve));
        await new Promise((resolve) => setImmediate(resolve));
        assert.equal(acquires, 0);
    }
});

test('the Proactive Chat popup switch acquires the stream when turned on, not when turned off', async () => {
    const marker = "else if (toggle.id === 'proactive-chat') {";
    const block = statementAt(popupSource, marker).slice('else '.length);
    for (const isChecked of [true, false]) {
        const calls = [];
        await runBlock(block, {
            toggle: { id: 'proactive-chat' },
            isChecked,
            saveNEKOSettings: () => {},
            resetProactiveChatBackoff: () => {},
            stopProactiveChatSchedule: () => {},
            acquireProactiveVisionAfterUserEnable: () => { calls.push('acquire'); },
        });
        assert.deepEqual(calls, isChecked ? ['acquire'] : [], `isChecked=${isChecked}`);
    }
});

test('a frame captured before an off -> on toggle is dropped and the new heartbeat keeps running', async () => {
    const h = loadProactive({ privacyAllowsVision: true });
    let release;
    const stream = fakeStream();
    h.window.appScreen.acquireOrReuseCachedStream = async () => stream;
    h.window.appScreen.captureFrameFromStream = () => new Promise((resolve) => { release = resolve; });
    h.window.startProactiveVisionDuringSpeech();
    const inFlight = h.heartbeat().fn();
    for (let i = 0; i < 4; i += 1) await new Promise((resolve) => setImmediate(resolve));
    assert.ok(release, 'capture started');

    h.S.proactiveVisionChatEnabled = false;
    h.window.syncProactiveVisionDuringSpeech();
    h.S.proactiveVisionChatEnabled = true;
    h.window.syncProactiveVisionDuringSpeech();
    assert.ok(h.heartbeat(), 'heartbeat restarted');

    release({ dataUrl: 'data:image/jpeg;base64,OLD' });
    await inFlight;
    assert.equal(h.frames(), 0, 'the frame from before the toggle is not sent');
    assert.ok(h.heartbeat(), 'dropping it does not stop the restarted heartbeat');

    // The fresh frame asked for at re-enable was blocked by the capture in
    // flight; it is captured right after, not one interval later.
    for (let i = 0; i < 4; i += 1) await new Promise((resolve) => setImmediate(resolve));
    release({ dataUrl: 'data:image/jpeg;base64,NEW' });
    for (let i = 0; i < 6; i += 1) await new Promise((resolve) => setImmediate(resolve));
    assert.equal(h.frames(), 1, 'the fresh frame follows the dropped one at once');
});

test('the voice nudge re-checks vision after the async header fetch, before every send', () => {
    const proactive = read('static/app/app-proactive.js');
    const send = statementAt(proactive, 'async function _sendVoiceProactive() {');
    const afterHeaders = send.slice(send.indexOf('getMutationHeaders()'));
    assert.match(afterHeaders, /if \(voiceProactivePayload\.enabled_modes\.length > 0\s*&& !canProactiveVisionRunDuringSpeech\(\)\) \{\s*voiceProactivePayload\.enabled_modes = \[\];/);
    assert.match(afterHeaders, /body: JSON\.stringify\(voiceProactivePayload\)/);
});

test('a failed manual start releases the reused stream once proactive vision no longer holds it', () => {
    const fn = statementAt(screenSource, 'function releaseReusedStreamIfUnheld(attempt) {');
    assert.match(fn, /^function releaseReusedStreamIfUnheld\(attempt\) \{\s*(\/\/[^\n]*\n\s*)*if \(isProactiveVisionHoldingStream\(\)\) return;/);
});

test('tutorial suppression still releases the stream while a manual share is starting', () => {
    const h = loadProactive({ privacyAllowsVision: true });
    h.window.isScreenSharingStartPending = () => true;
    const stream = fakeStream();
    h.S.screenCaptureStream = stream;
    h.window.releaseProactiveVisionStream();
    assert.equal(stream.stopped, 0, 'not suppressed: the starting manual share keeps it');

    h.window.isNekoClickGuideActive = true; // tutorial suppression
    h.window.releaseProactiveVisionStream();
    assert.equal(stream.stopped, 1);
});

test('a second click while the first acquisition is pending does not open another picker', async () => {
    const h = loadProactive({ privacyAllowsVision: true });
    h.S.screenCaptureStream = null;
    let acquires = 0;
    let finish;
    h.window.appScreen.acquireOrReuseCachedStream = () => {
        acquires += 1;
        return new Promise((resolve) => { finish = resolve; });
    };
    h.window.acquireProactiveVisionAfterUserEnable();
    for (let i = 0; i < 4; i += 1) await new Promise((resolve) => setImmediate(resolve));
    h.window.acquireProactiveVisionAfterUserEnable();
    for (let i = 0; i < 4; i += 1) await new Promise((resolve) => setImmediate(resolve));
    assert.equal(acquires, 1);

    const stream = fakeStream();
    h.S.screenCaptureStream = stream;
    finish(stream);
    for (let i = 0; i < 6; i += 1) await new Promise((resolve) => setImmediate(resolve));
    h.window.acquireProactiveVisionAfterUserEnable(); // after it settled, a click may acquire again
    for (let i = 0; i < 4; i += 1) await new Promise((resolve) => setImmediate(resolve));
    assert.equal(acquires, 2);
});

test('accepting the "watch the screen with me" prompt acquires the stream after saving', async () => {
    const source = read('static/app/app-context-prompt.js');
    const apply = statementAt(source, 'function _apply(context) {');
    for (const context of ['play', 'work']) {
        const calls = [];
        await runBlock(`${apply}\n_apply(${JSON.stringify(context)});`, {
            _setFlag: (key, value) => { calls.push(`set:${key}=${value}`); },
            _persistAndReschedule: () => { calls.push('save'); },
            acquireProactiveVisionAfterUserEnable: () => { calls.push('acquire'); },
        });
        const acquireAt = calls.indexOf('acquire');
        if (context === 'play') {
            assert.ok(acquireAt > calls.indexOf('save'), `acquire after save: ${calls}`);
        } else {
            assert.equal(acquireAt, -1, 'turning Screen Share off does not acquire');
        }
    }
});

test('text-chat vision modes use the shared keep-the-stream rule', () => {
    const proactive = read('static/app/app-proactive.js');
    assert.equal((proactive.match(/if \(holdsProactiveVisionStream\(\)\) \{\s*(availableModes\.push\('vision'\)|latestModes\.push\('vision', 'window'\))/g) || []).length, 2);
    assert.doesNotMatch(proactive, /S\.proactiveVisionChatEnabled && S\.proactiveChatEnabled && isProactiveVisionEnabledNow\(\)/);
});

test('a click-acquired stream asks for a fresh frame even while a capture is in flight', async () => {
    for (const inFlightResult of [{ dataUrl: null }, { dataUrl: 'data:image/jpeg;base64,AAAA' }]) {
        const sent = !!inFlightResult.dataUrl;
        const h = loadProactive({ privacyAllowsVision: true });
        const stream = fakeStream();
        h.S.screenCaptureStream = stream;
        h.window.appScreen.acquireOrReuseCachedStream = async () => stream;
        const pending = [];
        h.window.appScreen.captureFrameFromStream = () => new Promise((resolve) => { pending.push(resolve); });
        h.window.startProactiveVisionDuringSpeech();
        const inFlight = h.heartbeat().fn();
        for (let i = 0; i < 4; i += 1) await new Promise((resolve) => setImmediate(resolve));
        assert.equal(pending.length, 1, 'a capture is in flight');

        h.window.acquireProactiveVisionAfterUserEnable();
        for (let i = 0; i < 6; i += 1) await new Promise((resolve) => setImmediate(resolve));
        assert.equal(pending.length, 1, 'no second capture while one is in flight');

        pending[0](inFlightResult);
        await inFlight;
        for (let i = 0; i < 6; i += 1) await new Promise((resolve) => setImmediate(resolve));
        assert.equal(h.frames(), sent ? 1 : 0, `sent=${sent}`);
        assert.equal(pending.length, sent ? 1 : 2,
            sent ? 'the frame in flight went out: no second frame' : 'nothing went out: a fresh frame is captured right after');
    }
});
