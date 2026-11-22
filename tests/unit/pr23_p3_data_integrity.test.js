'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '../..');
const HOME_TUTORIAL_KEY = 'neko.homeTutorial.agentRestoreSnapshot.v1';

function createStorage() {
    const map = new Map();
    return {
        getItem(k) { return map.has(k) ? map.get(k) : null; },
        setItem(k, v) { map.set(String(k), String(v)); },
        removeItem(k) { map.delete(String(k)); },
        dump() { return Object.fromEntries(map.entries()); },
    };
}

function loadHomeTutorialRuntime(options = {}) {
    const code = fs.readFileSync(
        path.join(ROOT, 'static/tutorial/core/home-tutorial-runtime.js'),
        'utf8'
    );
    const localStorage = options.localStorage || createStorage();
    const fetchCalls = [];
    const scheduledTimeouts = [];
    let timerId = 1;

    const fakeSetTimeout = options.useRealTimers ? setTimeout : (fn, ms) => {
        const id = timerId++;
        const entry = { id, fn, ms, cleared: false };
        scheduledTimeouts.push(entry);
        return id;
    };
    const fakeClearTimeout = options.useRealTimers ? clearTimeout : (id) => {
        const t = scheduledTimeouts.find(x => x.id === id);
        if (t) t.cleared = true;
    };

    const win = {
        location: { pathname: '/' },
        lanlan_config: options.lanlanName ? { lanlan_name: options.lanlanName } : {},
        waitForStorageLocationStartupBarrier: options.waitForBarrier || undefined,
        addEventListener() {},
        removeEventListener() {},
        dispatchEvent() {},
        checkAndToggleTaskHUD() {},
    };
    win.window = win;

    const ctx = vm.createContext({
        window: win,
        localStorage,
        fetch: async (url, init = {}) => {
            const body = init.body ? JSON.parse(init.body) : null;
            fetchCalls.push({ url: String(url), method: init.method || 'GET', body });
            return await options.fetchHandler(String(url), init, body);
        },
        CustomEvent: class { constructor(type, o = {}) { this.type = type; this.detail = o.detail; } },
        setTimeout: fakeSetTimeout,
        clearTimeout: fakeClearTimeout,
        Date,
        console: { log() {}, warn() {}, error() {} },
    });

    vm.runInContext(code, ctx);
    return { win, localStorage, fetchCalls, scheduledTimeouts };
}

test('F0484: rejects & clears expired, invalid version, future-skewed, or mismatched-owner restore snapshots', async () => {
    const now = Date.now();
    const invalidSnapshots = [
        { name: 'expired (>24h)', payload: { version: 1, token: 1, createdAt: now - 25 * 60 * 60 * 1000, agentFlags: { agent_enabled: true } } },
        { name: 'bad version', payload: { version: 99, token: 2, createdAt: now, agentFlags: { agent_enabled: true } } },
        { name: 'missing createdAt', payload: { version: 1, token: 3, agentFlags: { agent_enabled: true } } },
        { name: 'future skew (>60s)', payload: { version: 1, token: 4, createdAt: now + 120 * 1000, agentFlags: { agent_enabled: true } } },
        { name: 'mismatched owner', payload: { version: 1, token: 5, createdAt: now, lanlanName: 'CatOther', agentFlags: { agent_enabled: true } } },
    ];

    for (const tc of invalidSnapshots) {
        const storage = createStorage();
        storage.setItem(HOME_TUTORIAL_KEY, JSON.stringify(tc.payload));
        const { fetchCalls } = loadHomeTutorialRuntime({
            localStorage: storage,
            lanlanName: 'CatCurrent',
            fetchHandler: async () => ({ ok: true, json: async () => ({ success: true }) }),
        });
        await new Promise(r => setImmediate(r));
        await new Promise(r => setImmediate(r));

        assert.equal(fetchCalls.length, 0, `${tc.name}: must NOT call agent endpoints`);
        assert.equal(storage.getItem(HOME_TUTORIAL_KEY), null, `${tc.name}: invalid snapshot must be cleared`);
    }
});

test('F0484: checks GET /api/agent/flags before replaying and aborts/clears when flags are already enabled by user/backend', async () => {
    const storage = createStorage();
    storage.setItem(HOME_TUTORIAL_KEY, JSON.stringify({
        version: 1,
        token: 42,
        createdAt: Date.now(),
        lanlanName: 'CatCurrent',
        agentFlags: {
            agent_enabled: true,
            computer_use_enabled: false,
            browser_use_enabled: true,
            user_plugin_enabled: false,
        },
    }));

    const { fetchCalls } = loadHomeTutorialRuntime({
        localStorage: storage,
        lanlanName: 'CatCurrent',
        fetchHandler: async (url, init) => {
            if (url === '/api/agent/flags' && (init.method || 'GET') === 'GET') {
                // User has already manually enabled computer_use_enabled (and agent_enabled)
                return {
                    ok: true,
                    json: async () => ({
                        success: true,
                        analyzer_enabled: true,
                        agent_flags: {
                            computer_use_enabled: true,
                            browser_use_enabled: false,
                            user_plugin_enabled: false,
                        },
                    }),
                };
            }
            return { ok: true, json: async () => ({ success: true }) };
        },
    });
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));

    assert.equal(fetchCalls.length, 1, 'should only GET /api/agent/flags');
    assert.equal(fetchCalls[0].method, 'GET');
    assert.equal(storage.getItem(HOME_TUTORIAL_KEY), null, 'diverged snapshot must be cleared without overwriting');
});

test('F0485: waits for storageLocationStartupBarrier, retries 502s with backoff, handles partial master-ON retry, and cancels on begin()', async () => {
    // Part A: storage barrier + two 502s + partial master-ON failure then recovery
    let releaseBarrier;
    const barrierPromise = new Promise(resolve => { releaseBarrier = resolve; });
    const storage = createStorage();
    storage.setItem(HOME_TUTORIAL_KEY, JSON.stringify({
        version: 1,
        token: 77,
        createdAt: Date.now(),
        lanlanName: 'CatA',
        agentFlags: {
            agent_enabled: true,
            computer_use_enabled: true,
            browser_use_enabled: false,
            user_plugin_enabled: false,
        },
    }));

    let flagsGetCount = 0;
    let flagsPostCount = 0;
    let masterEnabledOnServer = false;
    let childFlagsOnServer = { computer_use_enabled: false, browser_use_enabled: false, user_plugin_enabled: false };

    const { win, fetchCalls, scheduledTimeouts } = loadHomeTutorialRuntime({
        localStorage: storage,
        lanlanName: '', // Not populated yet at script eval time
        waitForBarrier: async () => {
            await barrierPromise;
            win.lanlan_config.lanlan_name = 'CatA';
        },
        fetchHandler: async (url, init, body) => {
            if (url === '/api/agent/flags' && (init.method || 'GET') === 'GET') {
                flagsGetCount += 1;
                if (flagsGetCount === 1) {
                    return { ok: false, status: 502, json: async () => ({}) };
                }
                return {
                    ok: true,
                    json: async () => ({
                        success: true,
                        analyzer_enabled: masterEnabledOnServer,
                        agent_flags: childFlagsOnServer,
                    }),
                };
            }
            if (url === '/api/agent/command' && init.method === 'POST') {
                masterEnabledOnServer = !!body.enabled;
                return { ok: true, json: async () => ({ success: true }) };
            }
            if (url === '/api/agent/flags' && init.method === 'POST') {
                flagsPostCount += 1;
                if (flagsPostCount === 1) {
                    // Simulate partial failure after master was turned ON!
                    return { ok: false, status: 502, json: async () => ({}) };
                }
                childFlagsOnServer = Object.assign({}, childFlagsOnServer, body.flags);
                return { ok: true, json: async () => ({ success: true }) };
            }
            return { ok: true, json: async () => ({ success: true }) };
        },
    });

    await new Promise(r => setImmediate(r));
    assert.equal(fetchCalls.length, 0, 'must wait for storageLocationStartupBarrier before querying agent');
    assert.equal(typeof win.NekoHomeTutorialFeatureController.restoreInterrupted, 'function');

    // Release barrier -> attempt 0 hits 502 on GET /api/agent/flags and schedules retry 0 (1000ms)
    releaseBarrier();
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    assert.equal(flagsGetCount, 1);
    const retry1 = scheduledTimeouts.find(t => !t.cleared);
    assert.ok(retry1 && retry1.ms === 1000, 'must schedule first bounded backoff retry');

    // Fire retry 1 -> GET succeeds (all false), POST command(enabled=true) succeeds, POST flags hits 502!
    retry1.fn();
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    assert.equal(masterEnabledOnServer, true);
    assert.equal(flagsPostCount, 1);
    assert.notEqual(storage.getItem(HOME_TUTORIAL_KEY), null, 'snapshot must remain until child flags succeed');

    // Fire retry 2 -> GET sees masterEnabledOnServer=true (from masterAppliedInAttempt), restores child flags!
    const retry2 = scheduledTimeouts.filter(t => !t.cleared).at(-1);
    assert.ok(retry2 && retry2.ms === 2000);
    retry2.fn();
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    assert.equal(childFlagsOnServer.computer_use_enabled, true);
    assert.equal(storage.getItem(HOME_TUTORIAL_KEY), null, 'snapshot must be cleared once restore succeeds');

    // Part B: pending retry is cancelled if beginHomeTutorialFeatureSuppression starts
    const storage2 = createStorage();
    storage2.setItem(HOME_TUTORIAL_KEY, JSON.stringify({
        version: 1,
        token: 88,
        createdAt: Date.now(),
        agentFlags: { agent_enabled: true },
    }));
    const rt2 = loadHomeTutorialRuntime({
        localStorage: storage2,
        fetchHandler: async (url, init) => {
            if (url === '/api/agent/flags' && (init.method || 'GET') === 'GET') {
                return { ok: false, status: 502, json: async () => ({}) };
            }
            return { ok: true, json: async () => ({ success: true }) };
        },
    });
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    const pendingRetry = rt2.scheduledTimeouts.find(t => !t.cleared);
    assert.ok(pendingRetry);
    rt2.win.NekoHomeTutorialFeatureController.begin('new-tutorial');
    assert.equal(pendingRetry.cleared, true, 'starting tutorial suppression must cancel pending restore retry');
});

test('S-F10B-003: achievement playtime uses single-flight serialization without aborting in-flight requests or double-counting', async () => {
    const code = fs.readFileSync(path.join(ROOT, 'static/achievement_manager.js'), 'utf8');
    let fakeNow = 1000000;
    const intervalTimers = [];
    const docListeners = new Map();
    const winListeners = new Map();
    const playtimePosts = [];
    let activeFetchResolve = null;
    let inFlightCount = 0;
    let maxConcurrent = 0;
    let abortSignalsSeen = [];

    const doc = {
        visibilityState: 'visible',
        addEventListener(type, fn) {
            if (!docListeners.has(type)) docListeners.set(type, new Set());
            docListeners.get(type).add(fn);
        },
        dispatchVisibility(state) {
            doc.visibilityState = state;
            for (const fn of docListeners.get('visibilitychange') || []) fn();
        },
        getElementById: () => null,
    };

    const win = {
        document: doc,
        location: { pathname: '/' },
        localStorage: createStorage(),
        nekoLocalMutationSecurity: {
            peekCachedToken: () => 'cached-csrf-token',
            getMutationHeaders: async () => {
                throw new Error('must not await getMutationHeaders when peekCachedToken is present');
            },
        },
        addEventListener(type, fn) {
            if (!winListeners.has(type)) winListeners.set(type, new Set());
            winListeners.get(type).add(fn);
        },
        dispatchPageHide() {
            for (const fn of winListeners.get('pagehide') || []) fn();
        },
    };
    win.window = win;

    const fakeFetch = (url, init = {}) => {
        if (String(url) === '/api/steam/update-playtime') {
            inFlightCount += 1;
            maxConcurrent = Math.max(maxConcurrent, inFlightCount);
            abortSignalsSeen.push(!!init.signal);
            const entry = {
                body: JSON.parse(init.body),
                keepalive: init.keepalive,
                headers: init.headers,
                settled: false,
            };
            playtimePosts.push(entry);
            return new Promise((resolve) => {
                activeFetchResolve = () => {
                    inFlightCount -= 1;
                    entry.settled = true;
                    resolve({
                        ok: true,
                        status: 200,
                        json: async () => ({ success: true, progressUnlocked: [] }),
                    });
                };
            });
        }
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ success: true, stats: {} }) });
    };

    const DateMock = class extends Date {
        static now() { return fakeNow; }
    };

    const ctx = vm.createContext({
        window: win,
        document: doc,
        location: win.location,
        localStorage: win.localStorage,
        fetch: fakeFetch,
        Date: DateMock,
        setTimeout(fn, ms) {
            const entry = { fn, ms };
            intervalTimers.push(entry);
            return intervalTimers.length;
        },
        clearTimeout() {},
        console: { log() {}, warn() {}, error() {}, debug() {} },
    });

    vm.runInContext(code, ctx);
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));

    assert.equal(intervalTimers.length, 1, 'initial 60s playtime timer scheduled');

    // Step 1: 60s passes -> regular flush starts (slow pending fetch)
    fakeNow += 60000;
    const firstTimerPromise = intervalTimers[0].fn();
    assert.equal(playtimePosts.length, 1);
    assert.equal(playtimePosts[0].body.seconds, 60);
    assert.equal(playtimePosts[0].keepalive, true, 'all playtime flushes must set keepalive:true');
    assert.equal(playtimePosts[0].headers['X-CSRF-Token'], 'cached-csrf-token');
    assert.equal(abortSignalsSeen[0], false, 'no AbortController needed when single-flight serialized');

    // Step 2: 500ms later, visibilitychange(hidden) + pagehide both fire mid-flight
    fakeNow += 500;
    doc.dispatchVisibility('hidden');
    win.dispatchPageHide();
    assert.equal(playtimePosts.length, 1, 'must NOT start concurrent request while first is in flight');
    assert.equal(maxConcurrent, 1);

    // Step 3: Resolve first request (which covered [1000000, 1060000], 60s).
    // Pending force flush runs next with remainder = 500ms (< 1s) -> must NOT send 0s or wipe sub-second remainder!
    activeFetchResolve();
    await firstTimerPromise;
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    assert.equal(playtimePosts.length, 1, 'sub-second remainder (<1s) must not trigger POST or wipe prevTs');

    // Step 4: Another 1500ms passes (total 2000ms since T=1060000: 500ms preserved + 1500ms new = 2s!)
    // Firing pagehide synchronously dispatches fetch BEFORE any await!
    fakeNow += 1500;
    win.dispatchPageHide();
    assert.equal(playtimePosts.length, 2, 'must dispatch synchronously on pagehide when peekCachedToken exists');
    assert.equal(playtimePosts[1].body.seconds, 2, 'preserved 500ms + 1500ms must sum to 2s without truncation');

    // Verify total accumulated seconds == 60 + 2 == 62s (no double-counting!)
    activeFetchResolve();
    await new Promise(r => setImmediate(r));
    const totalReported = playtimePosts.reduce((sum, p) => sum + p.body.seconds, 0);
    assert.equal(totalReported, 62);
    assert.equal(maxConcurrent, 1);
});
