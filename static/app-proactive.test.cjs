const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app/app-proactive.js'), 'utf8');

test('tutorial suppression rejects late vision acquisition at every async boundary', async () => {
    for (const stage of ['prepare', 'backend', 'stream', 'manual-stream']) {
        const acquiresStream = stage === 'stream' || stage === 'manual-stream';
        const listeners = new Map();
        let settle;
        let enterStage;
        const entered = new Promise((resolve) => { enterStage = resolve; });
        const gate = new Promise((resolve) => { settle = resolve; });
        let stopped = 0;
        let promptCalls = 0;
        const stream = { getTracks() { return [{ stop() { stopped += 1; } }]; } };
        const state = { isRecording: true, proactiveVisionEnabled: true };
        const window = {
            appState: state, appConst: {},
            addEventListener(type, listener) { listeners.set(type, listener); },
            prepareRememberedWindowCapture: async () => {
                if (stage === 'prepare') { enterStage(); await gate; }
                return { required: false, allowed: true };
            },
            appScreen: {
                fetchBackendScreenshot: async () => {
                    if (stage === 'backend') { enterStage(); await gate; }
                    return { dataUrl: null };
                },
                acquireOrReuseCachedStream: async () => {
                    promptCalls += 1;
                    enterStage();
                    await gate;
                    state.screenCaptureStream = stream;
                    return stream;
                }
            }
        };
        vm.runInNewContext(source, {
            window,
            document: {
                addEventListener() {},
                getElementById() {
                    return stage === 'manual-stream' ? { classList: { contains() { return true; } } } : null;
                }
            },
            localStorage: { getItem() { return null; } },
            console, clearTimeout() {}, clearInterval() {}
        });
        const pending = window.acquireProactiveVisionStream();
        await entered;
        listeners.get('neko:home-tutorial-features-suppressed')({ detail: { active: true, source: 'director' } });
        window.releaseProactiveVisionStream(); // Begin-time release has no late stream yet.
        settle();
        assert.equal(await pending, false, stage);
        assert.equal(promptCalls, acquiresStream ? 1 : 0, 'no prompt after earlier suppression');
        assert.equal(stopped, stage === 'stream' ? 1 : 0, 'release the late cached track');
        assert.equal(state.screenCaptureStream == null, stage !== 'manual-stream', 'manual sharing remains protected');
        await window.acquireProactiveVisionStream();
        assert.equal(promptCalls, acquiresStream ? 1 : 0, 'already suppressed calls never acquire');
    }
});

test('both proactive screenshot acquisitions release late streams under suppression', async () => {
    for (const targetAcquire of [1, 2]) {
        const listeners = new Map();
        let settle;
        let entered;
        const waiting = new Promise((resolve) => { entered = resolve; });
        const gate = new Promise((resolve) => { settle = resolve; });
        let acquisitions = 0;
        let frames = 0;
        let stopped = 0;
        let backendCalls = 0;
        const state = { isRecording: true, proactiveVisionEnabled: true };
        const window = {
            appState: state, appConst: {},
            addEventListener(type, listener) { listeners.set(type, listener); },
            appScreen: {
                acquireOrReuseCachedStream: async () => {
                    acquisitions += 1;
                    if (acquisitions === targetAcquire) { entered(); await gate; }
                    const stream = { getTracks() { return [{ stop() { stopped += 1; } }]; } };
                    state.screenCaptureStream = stream;
                    return stream;
                },
                captureFrameFromStream: async () => { frames += 1; return null; },
                fetchBackendScreenshot: async () => { backendCalls += 1; return { dataUrl: null }; }
            }
        };
        vm.runInNewContext(source, {
            window,
            document: { addEventListener() {}, getElementById() { return null; } },
            localStorage: { getItem() { return null; } },
            console, clearTimeout() {}, clearInterval() {}
        });
        const pending = window.appProactive.captureProactiveChatScreenshotWithSource();
        await waiting;
        listeners.get('neko:home-tutorial-features-suppressed')({ detail: { active: true, source: 'director' } });
        settle();
        assert.equal((await pending).dataUrl, null);
        assert.equal(frames, targetAcquire - 1, 'do not extract a frame from the late stream');
        assert.equal(backendCalls, 0, 'do not fall back to another capture after suppression');
        assert.equal(stopped, targetAcquire, 'discard any first black stream and release the late one');
        assert.equal(state.screenCaptureStream, null);
    }
});

test('every successful proactive screenshot route rechecks tutorial and privacy suppression', async () => {
    for (const blocker of ['tutorial', 'privacy']) {
        for (const route of ['cached', 'native', 'stream', 'retry', 'backend']) {
            const listeners = new Map();
            let settle;
            let enter;
            const entered = new Promise((resolve) => { enter = resolve; });
            const gate = new Promise((resolve) => { settle = resolve; });
            let frames = 0;
            let stopped = 0;
            const stream = {
                active: true,
                getTracks() { return [{ stop() { stopped += 1; } }]; },
                getVideoTracks() { return [{ readyState: 'live' }]; }
            };
            const delayedFrame = async () => { enter(); await gate; return { dataUrl: 'FRAME' }; };
            const state = {
                isRecording: true, proactiveVisionEnabled: true,
                screenCaptureStream: route === 'cached' ? stream : null,
                selectedScreenSourceId: route === 'native' ? 'screen:0' : null
            };
            const window = {
                appState: state, appConst: {},
                addEventListener(type, listener) { listeners.set(type, listener); },
                getDesktopCaptureProvider: () => route === 'native' ? { captureSourceAsDataUrl() {} } : null,
                captureDesktopSourceWithTimeout: async () => {
                    await delayedFrame(); return { success: true, dataUrl: 'NATIVE' };
                },
                appScreen: {
                    acquireOrReuseCachedStream: async () => {
                        if (route === 'backend') return null;
                        state.screenCaptureStream = stream;
                        return stream;
                    },
                    captureFrameFromStream: async () => {
                        frames += 1;
                        if (route === 'retry' && frames === 1) return null;
                        return delayedFrame();
                    },
                    fetchBackendScreenshot: delayedFrame,
                    scheduleScreenCaptureIdleCheck() {}
                }
            };
            vm.runInNewContext(source, {
                window,
                document: { addEventListener() {}, getElementById() { return null; } },
                localStorage: { getItem() { return null; } },
                console, clearTimeout() {}, clearInterval() {}
            });
            const pending = window.appProactive.captureProactiveChatScreenshotWithSource();
            await entered;
            if (blocker === 'privacy') state.proactiveVisionEnabled = false;
            else listeners.get('neko:home-tutorial-features-suppressed')({ detail: { active: true, source: 'director' } });
            settle();
            assert.equal((await pending).dataUrl, null, route);
            assert.equal(frames, route === 'native' || route === 'backend' ? 0 : (route === 'retry' ? 2 : 1));
            assert.equal(state.screenCaptureStream == null, true, route);
            assert.equal(stopped, route === 'native' || route === 'backend' ? 0 : (route === 'retry' ? 2 : 1));
        }
    }
});

test('request_screenshot rechecks tutorial suppression immediately before sending', async () => {
    const websocketSource = fs.readFileSync(path.join(__dirname, 'app/app-websocket.js'), 'utf8');
    const branch = websocketSource.indexOf("response.type === 'request_screenshot'");
    const start = websocketSource.indexOf('(async function () {', branch);
    const end = websocketSource.indexOf('})();', start) + 5;
    for (const paired of [true, false]) {
        for (const phase of ['before', 'during', 'none']) {
            let suppressed = phase === 'before';
            let captures = 0;
            const sent = [];
            const window = {
                isNekoHomeTutorialProactiveSuppressed: () => suppressed,
                captureProactiveChatScreenshot: async () => { captures += 1; suppressed = phase === 'during'; return 'FRAME'; }
            };
            if (paired) window.appProactive = {
                captureProactiveChatScreenshotWithSource: async () => {
                    captures += 1;
                    suppressed = phase === 'during';
                    return { dataUrl: 'FRAME', captureType: 'screen' };
                }
            };
            await vm.runInNewContext(websocketSource.slice(start, end), {
                window, S: { socket: { readyState: 1, send(value) { sent.push(value); } } },
                response: { request_id: 'current-round' }, WebSocket: { OPEN: 1 }, console
            });
            assert.equal(sent.length, 1, 'always resolve the backend screenshot request');
            assert.equal(JSON.parse(sent[0]).data, phase === 'none' ? 'FRAME' : '');
            assert.equal(JSON.parse(sent[0]).declined === true, phase !== 'none');
            assert.equal(JSON.parse(sent[0]).request_id, 'current-round');
            assert.equal(captures, phase === 'before' ? 0 : 1);
        }
    }
});

test('screenshot replies correlate all paths and preserve legacy-server and ordinary failure contracts', async () => {
    const websocketSource = fs.readFileSync(path.join(__dirname, 'app/app-websocket.js'), 'utf8');
    const branch = websocketSource.indexOf("response.type === 'request_screenshot'");
    const start = websocketSource.indexOf('(async function () {', branch);
    const end = websocketSource.indexOf('})();', start) + 5;
    for (const requestId of [undefined, 'current-round']) {
        for (const route of ['success', 'declined', 'error-privacy', 'error-tutorial', 'error-normal', 'empty', 'legacy-success', 'closed']) {
            const sent = [];
            let tutorial = false;
            const state = {
                proactiveVisionEnabled: true,
                socket: { readyState: route === 'closed' ? 3 : 1, send(raw) { sent.push(JSON.parse(raw)); } }
            };
            const window = {
                isNekoHomeTutorialProactiveSuppressed: () => tutorial,
                captureProactiveChatScreenshot: async () => 'LEGACY',
                appProactive: {
                    captureProactiveChatScreenshotWithSource: async () => {
                        if (route === 'declined') return { dataUrl: 'IGNORE', declined: true };
                        if (route.startsWith('error-')) {
                            if (route === 'error-privacy') state.proactiveVisionEnabled = false;
                            if (route === 'error-tutorial') tutorial = true;
                            throw new Error('synthetic capture failure');
                        }
                        return { dataUrl: route === 'empty' ? null : 'FRAME', captureType: 'screen' };
                    }
                }
            };
            if (route === 'legacy-success') delete window.appProactive;
            await vm.runInNewContext(websocketSource.slice(start, end), {
                window, S: state, response: { request_id: requestId },
                WebSocket: { OPEN: 1 }, console: { warn() {} }
            });
            const replies = !['error-normal', 'empty', 'closed'].includes(route);
            assert.equal(sent.length, replies ? 1 : 0, route);
            if (!replies) continue;
            assert.equal(sent[0].request_id, requestId, route);
            assert.equal(Object.hasOwn(sent[0], 'request_id'), requestId !== undefined, route);
            const declined = ['declined', 'error-privacy', 'error-tutorial'].includes(route);
            assert.equal(sent[0].declined === true, declined, route);
            assert.equal(sent[0].data, declined ? '' : (route === 'legacy-success' ? 'LEGACY' : 'FRAME'), route);
        }
    }
});

test('click-guide activation stops vision and releases only automatic capture', () => {
    for (const manualSharing of [false, true]) {
        const listeners = new Map();
        let stopped = 0;
        let cleared = 0;
        const stream = { getTracks() { return [{ stop() { stopped += 1; } }]; } };
        const state = {
            isRecording: true, proactiveVisionEnabled: true,
            screenCaptureStream: stream, proactiveVisionFrameTimer: 1
        };
        const window = {
            appState: state, appConst: {},
            addEventListener(type, listener) { listeners.set(type, listener); }
        };
        vm.runInNewContext(source, {
            window,
            document: {
                addEventListener() {},
                getElementById() { return { classList: { contains() { return manualSharing; } } }; }
            },
            localStorage: { getItem() { return null; } },
            console, clearTimeout() {}, clearInterval() { cleared += 1; }
        });
        window.isNekoClickGuideActive = true;
        listeners.get('neko:click-guide-active')({ detail: { active: true } });
        assert.equal(state.proactiveVisionFrameTimer, null);
        assert.equal(cleared, 1);
        assert.equal(stopped, manualSharing ? 0 : 1);
        assert.equal(state.screenCaptureStream == null, !manualSharing);
    }
});

test('click-guide release restores chat after its timer expires while suppressed', async () => {
    const listeners = new Map();
    const timers = new Map();
    let nextTimer = 0;
    const state = {
        isRecording: true,
        proactiveChatEnabled: true,
        proactiveNewsChatEnabled: true,
        proactiveVisionEnabled: false,
        proactiveChatInterval: 15
    };
    const window = {
        appState: state, appConst: {},
        addEventListener(type, listener) { listeners.set(type, listener); }
    };
    vm.runInNewContext(source, {
        window,
        document: { addEventListener() {}, getElementById() { return null; } },
        localStorage: { getItem() { return null; } },
        console,
        setTimeout(fn) { timers.set(++nextTimer, fn); return nextTimer; },
        clearTimeout(id) { timers.delete(id); },
        clearInterval() {}
    });
    const release = () => listeners.get('neko:click-guide-active')({ detail: { active: false } });
    release();
    assert.equal(timers.size, 0, 'release must not initialize the scheduler early');
    state._proactiveSchedulerInitialized = true;
    window.scheduleProactiveChat();
    assert.equal(timers.size, 1);
    window.isNekoClickGuideActive = true;
    const timer = timers.get(state.proactiveChatTimer);
    timers.delete(state.proactiveChatTimer);
    await timer();
    assert.equal(state.proactiveChatTimer, null);
    assert.equal(timers.size, 0, 'the guide blocks rearming the expired timer');
    window.isNekoClickGuideActive = false;
    release();
    assert.equal(timers.size, 1, 'release must restore the chat timer');

    listeners.get('neko:home-tutorial-features-suppressed')({ detail: { active: true, source: 'director' } });
    release();
    assert.equal(timers.size, 0, 'other tutorial sources must still block rearming');
});

test('overlapping tutorial sources keep vision stopped until the final release', () => {
    const listeners = new Map();
    let intervalCount = 0;
    const window = {
        appState: { isRecording: true, proactiveVisionEnabled: true, proactiveChatEnabled: false },
        appConst: {},
        addEventListener(type, listener) { listeners.set(type, listener); }
    };
    vm.runInNewContext(source, {
        window,
        document: { addEventListener() {}, getElementById() { return null; } },
        localStorage: { getItem() { return null; } },
        console,
        setInterval() { intervalCount += 1; return intervalCount; },
        clearInterval() {},
        clearTimeout() {}
    });
    const emit = (active, source) => listeners.get('neko:home-tutorial-features-suppressed')({
        detail: { active, source }
    });
    emit(true, 'yui-guide-director');
    emit(true, 'avatar-reload-override');
    emit(true, 'yui-guide-director'); // Enforce repeats must not create extra holds.
    emit(false, 'avatar-reload-override');
    assert.equal(window.appProactive.isHomeTutorialFeatureSuppressed(), true);
    assert.equal(intervalCount, 0);
    emit(false, 'yui-guide-director');
    assert.equal(window.appProactive.isHomeTutorialFeatureSuppressed(), false);
    assert.equal(intervalCount, 1);

    emit(true); // Feature controller's begin/end have no source field.
    emit(true, 'avatar-reload-override');
    emit(false);
    assert.equal(window.appProactive.isHomeTutorialFeatureSuppressed(), true);
    assert.equal(intervalCount, 1);
    emit(false, 'avatar-reload-override');
    assert.equal(window.appProactive.isHomeTutorialFeatureSuppressed(), false);
    assert.equal(intervalCount, 2);
});

test('proactive scheduler re-arms after new-user icebreaker suppression', () => {
    assert.match(source, /function getNewUserIcebreakerRetryDelayMs\(\)/);
    assert.match(source, /function getNewUserIcebreakerBlockingRetryMs\(\)/);

    const blockingStart = source.indexOf('function getNewUserIcebreakerBlockingRetryMs()');
    assert.notEqual(blockingStart, -1, 'missing blocking retry helper');
    const blockingEnd = source.indexOf('const store = readNewUserIcebreakerStore();', blockingStart);
    assert.notEqual(blockingEnd, -1, 'missing blocking retry persisted-store branch');
    const activeSessionBlock = source.slice(blockingStart, blockingEnd);
    assert.match(activeSessionBlock, /return getNewUserIcebreakerRetryDelayMs\(\);/);
    assert.doesNotMatch(activeSessionBlock, /return NEW_USER_ICEBREAKER_BLOCKING_WINDOW_MS;/);

    const scheduleStart = source.indexOf('function scheduleProactiveChat()');
    assert.notEqual(scheduleStart, -1, 'missing proactive scheduler function');

    const preconditionStart = source.indexOf('if (!canTriggerProactively()) {', scheduleStart);
    assert.notEqual(preconditionStart, -1, 'missing proactive precondition guard block');
    const preconditionEnd = source.indexOf('return;', preconditionStart);
    assert.notEqual(preconditionEnd, -1, 'missing proactive return in precondition block');
    const preconditionBlock = source.slice(preconditionStart, preconditionEnd);

    assert.match(preconditionBlock, /var icebreakerRetryMs = getNewUserIcebreakerBlockingRetryMs\(\);/);
    assert.match(preconditionBlock, /S\.proactiveChatTimer = setTimeout\(scheduleProactiveChat, icebreakerRetryMs \+ 250\);/);

    const activeStart = source.indexOf('if (isNewUserIcebreakerPeriodActive()) {', scheduleStart);
    assert.notEqual(activeStart, -1, 'missing active icebreaker suppression block');
    const activeEnd = source.indexOf('return;', activeStart);
    assert.notEqual(activeEnd, -1, 'missing active icebreaker return');
    const activeBlock = source.slice(activeStart, activeEnd);
    assert.match(activeBlock, /getNewUserIcebreakerBlockingRetryMs\(\) \|\| getNewUserIcebreakerRetryDelayMs\(\)/);
});

test('youtube login does not enable the unrelated personal dynamics mode', () => {
    assert.match(
        source,
        /new Set\(\['bilibili', 'douyin', 'kuaishou', 'weibo', 'reddit', 'twitter'\]\)/
    );
    const personalPlatformStart = source.indexOf('async function getAvailablePersonalPlatforms()');
    const personalPlatformEnd = source.indexOf('mod.getAvailablePersonalPlatforms', personalPlatformStart);
    const personalPlatformBlock = source.slice(personalPlatformStart, personalPlatformEnd);
    assert.match(personalPlatformBlock, /personalFeedPlatforms\.has\(platform\) && info\.has_cookies/);
    assert.doesNotMatch(personalPlatformBlock, /personalFeedPlatforms[^;]*youtube/);
});


test('request_screenshot declines privacy before capture and after an async capture', async () => {
    const websocketSource = fs.readFileSync(path.join(__dirname, 'app/app-websocket.js'), 'utf8');
    const branch = websocketSource.indexOf("response.type === 'request_screenshot'");
    const start = websocketSource.indexOf('(async function () {', branch);
    const end = websocketSource.indexOf('})();', start) + 5;
    for (const phase of ['before', 'during', 'none']) {
        let enabled = phase !== 'before';
        let captures = 0;
        const sent = [];
        const window = {
            appProactive: {
                isProactiveVisionEnabledNow: () => enabled,
                captureProactiveChatScreenshotWithSource: async () => {
                    captures += 1;
                    if (phase === 'during') enabled = false;
                    return { dataUrl: 'FRAME', captureType: 'screen' };
                }
            }
        };
        await vm.runInNewContext(websocketSource.slice(start, end), {
            window, S: { socket: { readyState: 1, send(raw) { sent.push(JSON.parse(raw)); } } },
            response: {}, WebSocket: { OPEN: 1 }, console
        });
        assert.equal(sent.length, 1);
        assert.equal(sent[0].declined === true, phase !== 'none');
        assert.equal(sent[0].data, phase === 'none' ? 'FRAME' : '');
        assert.equal(captures, phase === 'before' ? 0 : 1);
    }
});

test('request_screenshot forwards a remembered-window decline instead of waiting for timeout', async () => {
    const websocketSource = fs.readFileSync(path.join(__dirname, 'app/app-websocket.js'), 'utf8');
    const branch = websocketSource.indexOf("response.type === 'request_screenshot'");
    const start = websocketSource.indexOf('(async function () {', branch);
    const end = websocketSource.indexOf('})();', start) + 5;
    const sent = [];
    await vm.runInNewContext(websocketSource.slice(start, end), {
        window: { appProactive: { captureProactiveChatScreenshotWithSource: async () => ({ dataUrl: null, declined: true }) } },
        S: { socket: { readyState: 1, send(raw) { sent.push(JSON.parse(raw)); } } },
        response: {}, WebSocket: { OPEN: 1 }, console
    });
    assert.deepEqual(sent, [{ action: 'screenshot_response', data: '', declined: true }]);
});


test('screenshot exceptions and legacy replies keep privacy separate from technical failure', async () => {
    const websocketSource = fs.readFileSync(path.join(__dirname, 'app/app-websocket.js'), 'utf8');
    const branch = websocketSource.indexOf("response.type === 'request_screenshot'");
    const start = websocketSource.indexOf('(async function () {', branch);
    const end = websocketSource.indexOf('})();', start) + 5;
    for (const paired of [true, false]) {
        for (const blocked of [true, false]) {
            for (const throws of [true, false]) {
                let enabled = true;
                const state = { proactiveVisionEnabled: true };
                const sent = [];
                async function capture() {
                    enabled = !blocked;
                    state.proactiveVisionEnabled = enabled;
                    if (throws) throw new Error('synthetic capture exception');
                    return 'FRAME';
                }
                const window = { captureProactiveChatScreenshot: capture };
                if (paired) window.appProactive = {
                    isProactiveVisionEnabledNow: () => enabled,
                    captureProactiveChatScreenshotWithSource: async () => ({ dataUrl: await capture() })
                };
                state.socket = { readyState: 1, send(raw) { sent.push(JSON.parse(raw)); } };
                await vm.runInNewContext(websocketSource.slice(start, end), {
                    window, S: state, response: {}, WebSocket: { OPEN: 1 }, console: { warn() {} }
                });
                if (blocked) {
                    assert.deepEqual(sent, [{ action: 'screenshot_response', data: '', declined: true }]);
                } else if (throws) {
                    assert.equal(sent.length, 0, 'technical exceptions still allow the backend timeout fallback');
                } else {
                    assert.equal(sent.length, 1);
                    assert.equal(sent[0].data, 'FRAME');
                    assert.equal(sent[0].declined, undefined);
                }
            }
        }
    }
});


test('live vision consent sends a typed snapshot only on an open socket', () => {
    const websocketSource = fs.readFileSync(path.join(__dirname, 'app/app-websocket.js'), 'utf8');
    const start = websocketSource.indexOf('    function syncProactiveVisionConsent(');
    const end = websocketSource.indexOf('    function ensureWebSocketOpen(', start);
    const sent = [];
    const state = { proactiveVisionEnabled: true, socket: { readyState: 1, send(raw) { sent.push(JSON.parse(raw)); } } };
    const mod = {};
    const window = {};
    vm.runInNewContext(websocketSource.slice(start, end), { window, S: state, mod, WebSocket: { OPEN: 1 }, console: { warn() {} } });
    mod.syncProactiveVisionConsent(false);
    mod.syncProactiveVisionConsent(true);
    state.proactiveVisionEnabled = false;
    mod.syncProactiveVisionConsent();
    assert.deepEqual(sent, [false, true, false].map(enabled => ({ action: 'proactive_vision_consent', enabled })));
    state.socket.readyState = 0;
    mod.syncProactiveVisionConsent(false);
    assert.equal(sent.length, 3);
    state.socket = null;
    mod.syncProactiveVisionConsent(false);
    state.socket = { readyState: 1, send() { throw new Error('synthetic disconnect'); } };
    assert.doesNotThrow(() => mod.syncProactiveVisionConsent(false));
    state.socket = { readyState: 1, send() { assert.fail('unknown consent must not be sent'); } };
    state.proactiveVisionEnabled = undefined;
    mod.syncProactiveVisionConsent();
});


test('a screenshot taken before a revocation never rides a later request', () => {
    const window = { appState: { proactiveVisionEnabled: true }, appConst: {}, addEventListener() {} };
    let sharedSettings = null;
    vm.runInNewContext(source, {
        window,
        document: { addEventListener() {}, getElementById() { return null; } },
        localStorage: { getItem(key) { return key === 'project_neko_settings' ? sharedSettings : null; } },
        console, clearTimeout() {}, clearInterval() {}
    });
    const proactive = window.appProactive;
    const makeBody = () => ({
        enabled_modes: ['vision', 'window', 'news'],
        screenshot_data: 'data:image/jpeg;base64,AAAA',
        screenshot_allow_desktop_fallback: true,
        avatar_position: { centerX: 0.5 },
        window_title: 'Editor',
    });

    const seqAtCapture = proactive.currentProactiveVisionRevocationSeq();
    proactive.noteProactiveVisionConsent(true);
    const untouched = makeBody();
    assert.equal(proactive.discardVisionRevokedSince(untouched, seqAtCapture), false);
    assert.deepEqual(untouched, makeBody(), 'enabling alone keeps the image');

    // Revoke, then re-enable before the request goes out.
    proactive.noteProactiveVisionConsent(false);
    proactive.noteProactiveVisionConsent(true);
    const revoked = makeBody();
    assert.equal(proactive.discardVisionRevokedSince(revoked, seqAtCapture), true);
    assert.deepEqual(revoked, { enabled_modes: ['news'] }, 'window shares the vision consent gate');

    // Screenshot failed but the window title was attached: still vision consent.
    const titleOnly = { enabled_modes: ['window', 'news'], window_title: 'Editor' };
    assert.equal(proactive.discardVisionRevokedSince(titleOnly, seqAtCapture), true);
    assert.deepEqual(titleOnly, { enabled_modes: ['news'] });
    const noVision = { enabled_modes: ['news'] };
    assert.equal(proactive.discardVisionRevokedSince(noVision, seqAtCapture), false);

    const later = makeBody();
    assert.equal(
        proactive.discardVisionRevokedSince(later, proactive.currentProactiveVisionRevocationSeq()),
        false,
        'a capture started after the revocation is unaffected',
    );

    // Another window revoked: localStorage already says off, while this window's
    // state and revocation seq have not seen the sync event yet.
    sharedSettings = JSON.stringify({ proactiveVisionEnabled: false });
    assert.equal(window.appState.proactiveVisionEnabled, true);
    const crossWindow = makeBody();
    assert.equal(
        proactive.discardVisionRevokedSince(crossWindow, proactive.currentProactiveVisionRevocationSeq()),
        true,
    );
    assert.equal(crossWindow.screenshot_data, undefined);
});
