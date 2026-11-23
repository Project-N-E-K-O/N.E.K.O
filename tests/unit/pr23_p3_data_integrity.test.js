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

test('F0484: rejects & clears invalid/expired/skewed snapshots while preserving mismatched-owner snapshots untouched', async () => {
    const now = Date.now();
    const invalidSnapshots = [
        { name: 'expired (>24h)', payload: { version: 1, token: 1, createdAt: now - 25 * 60 * 60 * 1000, agentFlags: { agent_enabled: true } } },
        { name: 'bad version', payload: { version: 99, token: 2, createdAt: now, agentFlags: { agent_enabled: true } } },
        { name: 'missing createdAt', payload: { version: 1, token: 3, agentFlags: { agent_enabled: true } } },
        { name: 'future skew (>60s)', payload: { version: 1, token: 4, createdAt: now + 120 * 1000, agentFlags: { agent_enabled: true } } },
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

    // Mismatched owner: skip auto-restore on current character WITHOUT deleting snapshot for original owner
    const mismatchStorage = createStorage();
    const mismatchPayload = {
        version: 1,
        token: 5,
        createdAt: now,
        lanlanName: 'CatOther',
        agentFlags: { agent_enabled: true, computer_use_enabled: true },
    };
    const mismatchRawJson = JSON.stringify(mismatchPayload);
    mismatchStorage.setItem(HOME_TUTORIAL_KEY, mismatchRawJson);
    const mismatchRt = loadHomeTutorialRuntime({
        localStorage: mismatchStorage,
        lanlanName: 'CatCurrent',
        fetchHandler: async () => ({ ok: true, json: async () => ({ success: true }) }),
    });
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    assert.equal(mismatchRt.fetchCalls.length, 0, 'mismatched owner must NOT call agent endpoints');
    assert.equal(
        mismatchStorage.getItem(HOME_TUTORIAL_KEY),
        mismatchRawJson,
        'mismatched-owner snapshot must remain byte-for-byte identical in localStorage'
    );

    // Owner pending: lanlan_name not ready yet -> must NOT clear or apply CatOther snapshot; once ready, mismatch preserves or match restores
    const pendingStorage = createStorage();
    pendingStorage.setItem(HOME_TUTORIAL_KEY, mismatchRawJson);
    const pendingRt1 = loadHomeTutorialRuntime({
        localStorage: pendingStorage,
        lanlanName: '',
        fetchHandler: async () => ({ ok: true, json: async () => ({ success: true }) }),
    });
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    assert.equal(pendingRt1.fetchCalls.length, 0, 'owner_pending must NOT call agent endpoints before lanlan_name is ready');
    assert.equal(pendingStorage.getItem(HOME_TUTORIAL_KEY), mismatchRawJson, 'owner_pending must preserve snapshot byte-for-byte');
    const pendingRetry1 = pendingRt1.scheduledTimeouts.find(t => !t.cleared);
    assert.ok(pendingRetry1, 'owner_pending must schedule retry while waiting for lanlan_name');
    pendingRt1.win.lanlan_config.lanlan_name = 'CatCurrent';
    pendingRetry1.fn();
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    assert.equal(pendingRt1.fetchCalls.length, 0, 'once resolved to CatCurrent, CatOther snapshot must NOT apply');
    assert.equal(pendingStorage.getItem(HOME_TUTORIAL_KEY), mismatchRawJson, 'CatOther snapshot must remain intact for CatOther');

    // Same owner_pending flow when lanlan_name resolves to matching CatOther -> restores and clears
    const pendingStorage2 = createStorage();
    pendingStorage2.setItem(HOME_TUTORIAL_KEY, mismatchRawJson);
    const pendingRt2 = loadHomeTutorialRuntime({
        localStorage: pendingStorage2,
        lanlanName: '',
        fetchHandler: async (url, init) => {
            if (url === '/api/agent/flags' && (init.method || 'GET') === 'GET') {
                return { ok: true, json: async () => ({ success: true, analyzer_enabled: false, agent_flags: {} }) };
            }
            return { ok: true, json: async () => ({ success: true }) };
        },
    });
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    const pendingRetry2 = pendingRt2.scheduledTimeouts.find(t => !t.cleared);
    assert.ok(pendingRetry2);
    pendingRt2.win.lanlan_config.lanlan_name = 'CatOther';
    pendingRetry2.fn();
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    assert.ok(pendingRt2.fetchCalls.some(c => c.url === '/api/agent/command' && c.method === 'POST'));
    assert.equal(pendingStorage2.getItem(HOME_TUTORIAL_KEY), null, 'matched owner restore after owner_pending must clear snapshot');
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
    let toolServerMasterEnabled = false;
    let mainServerMasterEnabled = false;
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
                        analyzer_enabled: toolServerMasterEnabled,
                        agent_flags: childFlagsOnServer,
                    }),
                };
            }
            if (url === '/api/agent/command' && init.method === 'POST') {
                toolServerMasterEnabled = !!body.enabled;
                mainServerMasterEnabled = !!body.enabled;
                if (body.command === 'set_agent_enabled' && body.enabled === true) {
                    // Match real backend side effect (app/agent_server/api_routes.py): enabling master also turns user_plugin_enabled ON
                    childFlagsOnServer.user_plugin_enabled = true;
                }
                return { ok: true, json: async () => ({ success: true }) };
            }
            if (url === '/api/agent/flags' && init.method === 'POST') {
                flagsPostCount += 1;
                if (flagsPostCount === 1) {
                    // Simulate main_routers/agent_router.py rollback on 502: main_server turns OFF while tool_server remains ON
                    mainServerMasterEnabled = false;
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

    // Fire retry 1 -> GET succeeds (all false), POST command(enabled=true) succeeds, POST flags hits 502 (main_server resets agent_enabled=false while tool_server stays true)!
    retry1.fn();
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    assert.equal(toolServerMasterEnabled, true);
    assert.equal(mainServerMasterEnabled, false, 'simulated agent_router 502 rollback resets main_server session master');
    assert.equal(childFlagsOnServer.user_plugin_enabled, true);
    assert.equal(flagsPostCount, 1);
    assert.notEqual(storage.getItem(HOME_TUTORIAL_KEY), null, 'snapshot must remain until child flags succeed');

    // Fire retry 2 -> re-issues idempotent master ON (re-syncing main_server) AND restores child flags (user_plugin_enabled back to false)!
    const retry2 = scheduledTimeouts.filter(t => !t.cleared).at(-1);
    assert.ok(retry2 && retry2.ms === 2000);
    retry2.fn();
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    assert.equal(toolServerMasterEnabled, true);
    assert.equal(mainServerMasterEnabled, true, 'retry must re-sync main_server session master ON after prior 502 rollback');
    assert.equal(childFlagsOnServer.computer_use_enabled, true);
    assert.equal(childFlagsOnServer.user_plugin_enabled, false, 'snapshot user_plugin_enabled:false must be restored');
    assert.equal(storage.getItem(HOME_TUTORIAL_KEY), null, 'snapshot must be cleared once restore succeeds');

    // Part B: pending retry is cancelled if beginHomeTutorialFeatureSuppression starts AND original restoreInterrupted promise settles to false
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
    // Also test explicit restoreInterrupted() promise resolution when cancelled by begin()
    const explicitRestorePromise = rt2.win.NekoHomeTutorialFeatureController.restoreInterrupted('manual-test');
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    const pendingRetry = rt2.scheduledTimeouts.find(t => !t.cleared);
    assert.ok(pendingRetry);
    rt2.win.NekoHomeTutorialFeatureController.begin('new-tutorial');
    assert.equal(pendingRetry.cleared, true, 'starting tutorial suppression must cancel pending restore retry');
    const explicitResult = await Promise.race([
        explicitRestorePromise,
        new Promise((_, reject) => setTimeout(() => reject(new Error('restoreInterrupted promise hung after cancellation')), 200)),
    ]);
    assert.equal(explicitResult, false, 'cancelled restoreInterrupted promise must settle to false');
});

test('F0485: handles ambiguous master-ON timeout and stops retrying if user manually turns master OFF', async () => {
    // Subcase 1: master-ON request fails/times out client-side AFTER backend applied agent_enabled=true + user_plugin_enabled=true
    {
        const storage = createStorage();
        storage.setItem(HOME_TUTORIAL_KEY, JSON.stringify({
            version: 1,
            token: 101,
            createdAt: Date.now(),
            lanlanName: 'CatA',
            agentFlags: {
                agent_enabled: true,
                computer_use_enabled: true,
                browser_use_enabled: false,
                user_plugin_enabled: false,
            },
        }));
        let masterOnServer = false;
        let childOnServer = { computer_use_enabled: false, browser_use_enabled: false, user_plugin_enabled: false };
        let cmdCount = 0;

        const { scheduledTimeouts } = loadHomeTutorialRuntime({
            localStorage: storage,
            lanlanName: 'CatA',
            fetchHandler: async (url, init, body) => {
                if (url === '/api/agent/flags' && (init.method || 'GET') === 'GET') {
                    return {
                        ok: true,
                        json: async () => ({ success: true, analyzer_enabled: masterOnServer, agent_flags: childOnServer }),
                    };
                }
                if (url === '/api/agent/command' && init.method === 'POST') {
                    cmdCount += 1;
                    masterOnServer = !!body.enabled;
                    if (body.enabled) {
                        childOnServer.user_plugin_enabled = true;
                    }
                    if (cmdCount === 1) {
                        return { ok: false, status: 504, json: async () => ({}) };
                    }
                    return { ok: true, json: async () => ({ success: true }) };
                }
                if (url === '/api/agent/flags' && init.method === 'POST') {
                    childOnServer = Object.assign({}, childOnServer, body.flags);
                    return { ok: true, json: async () => ({ success: true }) };
                }
                return { ok: true, json: async () => ({ success: true }) };
            },
        });
        await new Promise(r => setImmediate(r));
        await new Promise(r => setImmediate(r));
        assert.notEqual(storage.getItem(HOME_TUTORIAL_KEY), null, 'ambiguous master-ON failure must keep snapshot for retry');
        const retry = scheduledTimeouts.find(t => !t.cleared);
        assert.ok(retry);
        retry.fn();
        await new Promise(r => setImmediate(r));
        await new Promise(r => setImmediate(r));
        await new Promise(r => setImmediate(r));
        assert.equal(cmdCount, 2, 'retry must idempotently re-sync master across both services before restoring child flags');
        assert.equal(childOnServer.computer_use_enabled, true);
        assert.equal(childOnServer.user_plugin_enabled, false);
        assert.equal(storage.getItem(HOME_TUTORIAL_KEY), null, 'snapshot must clear after child restore completes');
    }

    // Subcase 2: master-ON succeeded, child POST failed, then user turns master OFF during retry backoff -> must NOT re-enable master
    {
        const storage = createStorage();
        storage.setItem(HOME_TUTORIAL_KEY, JSON.stringify({
            version: 1,
            token: 102,
            createdAt: Date.now(),
            lanlanName: 'CatA',
            agentFlags: {
                agent_enabled: true,
                computer_use_enabled: true,
                browser_use_enabled: false,
                user_plugin_enabled: false,
            },
        }));
        let masterOnServer = false;
        let childOnServer = { computer_use_enabled: false, browser_use_enabled: false, user_plugin_enabled: false };
        let cmdCount = 0;

        const { scheduledTimeouts } = loadHomeTutorialRuntime({
            localStorage: storage,
            lanlanName: 'CatA',
            fetchHandler: async (url, init, body) => {
                if (url === '/api/agent/flags' && (init.method || 'GET') === 'GET') {
                    return {
                        ok: true,
                        json: async () => ({ success: true, analyzer_enabled: masterOnServer, agent_flags: childOnServer }),
                    };
                }
                if (url === '/api/agent/command' && init.method === 'POST') {
                    cmdCount += 1;
                    masterOnServer = !!body.enabled;
                    if (body.enabled) {
                        childOnServer.user_plugin_enabled = true;
                    }
                    return { ok: true, json: async () => ({ success: true }) };
                }
                if (url === '/api/agent/flags' && init.method === 'POST') {
                    return { ok: false, status: 502, json: async () => ({}) };
                }
                return { ok: true, json: async () => ({ success: true }) };
            },
        });
        await new Promise(r => setImmediate(r));
        await new Promise(r => setImmediate(r));
        assert.equal(cmdCount, 1);
        assert.equal(masterOnServer, true);

        // User turns master OFF during backoff (backend preserves child state or sets master off)
        masterOnServer = false;
        const retry = scheduledTimeouts.find(t => !t.cleared);
        assert.ok(retry);
        retry.fn();
        await new Promise(r => setImmediate(r));
        await new Promise(r => setImmediate(r));
        assert.equal(cmdCount, 1, 'must NOT re-enable master after user turned master OFF during backoff');
        assert.equal(masterOnServer, false);
        assert.equal(storage.getItem(HOME_TUTORIAL_KEY), null, 'snapshot must be cleared when user intentionally turned master OFF');
    }
});

test('S-F10B-003: achievement playtime uses single-flight serialization, preserves sub-second remainder, and recovers via 15s timeout', async () => {
    const code = fs.readFileSync(path.join(ROOT, 'static/achievement_manager.js'), 'utf8');
    let fakeNow = 1000000;
    const timers = [];
    const docListeners = new Map();
    const winListeners = new Map();
    const playtimePosts = [];
    let activeFetchResolve = null;
    let activeFetchReject = null;
    let hangForeverMode = false;
    let inFlightCount = 0;
    let maxConcurrent = 0;

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
            const entry = {
                body: JSON.parse(init.body),
                keepalive: init.keepalive,
                headers: init.headers,
                signal: init.signal || null,
                abortedAtDispatch: !!(init.signal && init.signal.aborted),
                settled: false,
            };
            playtimePosts.push(entry);
            return new Promise((resolve, reject) => {
                const cleanupSignal = () => {
                    if (init.signal && typeof init.signal.removeEventListener === 'function') {
                        init.signal.removeEventListener('abort', onAbort);
                    }
                };
                const onAbort = () => {
                    cleanupSignal();
                    inFlightCount -= 1;
                    entry.settled = true;
                    const err = new Error('The operation was aborted.');
                    err.name = 'AbortError';
                    reject(err);
                };
                if (init.signal) {
                    if (init.signal.aborted) {
                        onAbort();
                        return;
                    }
                    init.signal.addEventListener('abort', onAbort, { once: true });
                }
                if (hangForeverMode) {
                    return;
                }
                activeFetchResolve = () => {
                    cleanupSignal();
                    inFlightCount -= 1;
                    entry.settled = true;
                    resolve({
                        ok: true,
                        status: 200,
                        json: async () => ({ success: true, progressUnlocked: [] }),
                    });
                };
                activeFetchReject = (err) => {
                    cleanupSignal();
                    inFlightCount -= 1;
                    entry.settled = true;
                    reject(err);
                };
            });
        }
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ success: true, stats: {} }) });
    };

    const DateMock = class extends Date {
        static now() { return fakeNow; }
    };

    let nextTimerId = 1;
    const ctx = vm.createContext({
        window: win,
        document: doc,
        location: win.location,
        localStorage: win.localStorage,
        AbortController,
        fetch: fakeFetch,
        Date: DateMock,
        setTimeout(fn, ms) {
            const entry = { id: nextTimerId++, fn, ms, cleared: false };
            timers.push(entry);
            return entry.id;
        },
        clearTimeout(id) {
            const t = timers.find(x => x.id === id);
            if (t) t.cleared = true;
        },
        console: { log() {}, warn() {}, error() {}, debug() {} },
    });

    vm.runInContext(code, ctx);
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));

    const intervalTimers = () => timers.filter(t => t.ms === 60000);
    const timeoutTimers = () => timers.filter(t => t.ms === 15000);
    assert.equal(intervalTimers().length, 1, 'initial 60s playtime timer scheduled');

    // Step 1: 60s passes -> regular flush starts (slow pending fetch)
    fakeNow += 60000;
    const firstTimerPromise = intervalTimers()[0].fn();
    assert.equal(playtimePosts.length, 1);
    assert.equal(playtimePosts[0].body.seconds, 60);
    assert.equal(playtimePosts[0].keepalive, true, 'all playtime flushes must set keepalive:true');
    assert.equal(playtimePosts[0].headers['X-CSRF-Token'], 'cached-csrf-token');
    assert.ok(playtimePosts[0].signal, 'playtime fetch should carry request timeout AbortSignal');
    assert.equal(playtimePosts[0].abortedAtDispatch, false);

    // Step 2: 500ms later, visibilitychange(hidden) + pagehide both fire mid-flight -> must NOT abort in-flight request
    fakeNow += 500;
    doc.dispatchVisibility('hidden');
    win.dispatchPageHide();
    assert.equal(playtimePosts.length, 1, 'must NOT start concurrent request while first is in flight');
    assert.equal(playtimePosts[0].signal.aborted, false, 'force flush must NOT abort active in-flight request');
    assert.equal(maxConcurrent, 1);

    // Step 3: Resolve first request (which covered [1000000, 1060000], 60s).
    // Pending force flush runs next with remainder = 500ms (< 1s) -> must NOT send 0s or wipe sub-second remainder!
    activeFetchResolve();
    await firstTimerPromise;
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    assert.equal(playtimePosts.length, 1, 'sub-second remainder (<1s) must not trigger POST or wipe prevTs');
    assert.equal(timeoutTimers()[0].cleared, true, 'request timeout timer must be cleared on completion');

    // Step 4: Another 1500ms passes (total 2000ms since T=1060000: 500ms preserved + 1500ms new = 2s!)
    // Firing pagehide synchronously dispatches fetch BEFORE any await!
    fakeNow += 1500;
    win.dispatchPageHide();
    assert.equal(playtimePosts.length, 2, 'must dispatch synchronously on pagehide when peekCachedToken exists');
    assert.equal(playtimePosts[1].body.seconds, 2, 'preserved 500ms + 1500ms must sum to 2s without truncation');

    // Verify queued pendingForceFlush actually auto-dispatches a 2s POST when >1s elapses during an in-flight request (without extra leave events)
    fakeNow += 2000;
    doc.dispatchVisibility('hidden');
    win.dispatchPageHide();
    assert.equal(playtimePosts.length, 2, 'queued force flush waits for current in-flight request');
    activeFetchResolve();
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    assert.equal(playtimePosts.length, 3, 'pendingForceFlush must automatically dispatch POST once in-flight settles');
    assert.equal(playtimePosts[2].body.seconds, 2, 'auto-dispatched queued flush must carry the 2s remainder');

    // Finish queued flush and verify sub-second remainder is ALSO preserved after successful flush (60.5s -> sends 60s, +0.5s -> sends 1s)
    activeFetchResolve();
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    fakeNow += 60500;
    const tick2Promise = intervalTimers()[1].fn();
    assert.equal(playtimePosts.length, 4);
    assert.equal(playtimePosts[3].body.seconds, 60);
    activeFetchResolve();
    await tick2Promise;
    await new Promise(r => setImmediate(r));
    fakeNow += 500;
    win.dispatchPageHide();
    assert.equal(playtimePosts.length, 5, '500ms remainder after OK flush + 500ms = 1s must flush');
    assert.equal(playtimePosts[4].body.seconds, 1);
    activeFetchResolve();
    await new Promise(r => setImmediate(r));

    // Verify 3600s cap discards excess rather than replaying capped hours later
    fakeNow += 4000 * 1000;
    win.dispatchPageHide();
    assert.equal(playtimePosts.length, 6);
    assert.equal(playtimePosts[5].body.seconds, 3600);
    activeFetchResolve();
    await new Promise(r => setImmediate(r));
    win.dispatchPageHide();
    assert.equal(playtimePosts.length, 6, 'excess beyond 3600s cap must remain discarded');
    await new Promise(r => setImmediate(r));

    // Verify hung request triggers 15s timeout abort, clears inFlightFlush, preserves prevTs, and next 60s tick recovers
    hangForeverMode = true;
    fakeNow += 30000;
    const tick3Promise = intervalTimers()[2].fn();
    assert.equal(playtimePosts.length, 7);
    assert.equal(playtimePosts[6].body.seconds, 30);
    const activeTimeout = timeoutTimers().filter(t => !t.cleared).at(-1);
    assert.ok(activeTimeout, 'hung request must arm 15s timeout timer');
    activeTimeout.fn();
    await tick3Promise;
    await new Promise(r => setImmediate(r));
    assert.equal(playtimePosts[6].signal.aborted, true, '15s timeout must abort hung fetch');

    hangForeverMode = false;
    fakeNow += 60000;
    const tick4Promise = intervalTimers()[3].fn();
    assert.equal(playtimePosts.length, 8, 'next scheduled tick must still run after timeout recovery');
    assert.equal(playtimePosts[7].body.seconds, 90, 'prevTs preserved across timeout so 30s + 60s = 90s is reported');
    activeFetchResolve();
    await tick4Promise;

    // U2: clock moves backward by 2 hours -> resets prevTs baseline cleanly so subsequent +10s tick still reports 10s
    fakeNow -= 7200 * 1000;
    await intervalTimers()[4].fn();
    assert.equal(playtimePosts.length, 8, 'backward clock jump must not send POST');
    fakeNow += 10000;
    const tick5Promise = intervalTimers()[5].fn();
    assert.equal(playtimePosts.length, 9, 'subsequent positive advance after clock jump must resume reporting');
    assert.equal(playtimePosts[8].body.seconds, 10);
    activeFetchResolve();
    await tick5Promise;

    assert.equal(maxConcurrent, 1);
    assert.equal(typeof activeFetchReject, 'function');
});

test('PR3976 U3: hanging cold-cache getMutationHeaders times out at 15s and unblocks subsequent playtime ticks', async () => {
    const code = fs.readFileSync(path.join(ROOT, 'static/achievement_manager.js'), 'utf8');
    let fakeNow = 2000000;
    const timers = [];
    const playtimePosts = [];
    let hangHeaders = true;

    const doc = {
        visibilityState: 'visible',
        addEventListener() {},
        getElementById: () => null,
    };
    const win = {
        document: doc,
        location: { pathname: '/' },
        localStorage: createStorage(),
        nekoLocalMutationSecurity: {
            peekCachedToken: () => null,
            getMutationHeaders: () => {
                if (hangHeaders) return new Promise(() => {});
                return Promise.resolve({ 'X-CSRF-Token': 'warm-csrf-token' });
            },
        },
        addEventListener() {},
    };
    win.window = win;

    let nextTimerId = 1;
    const ctx = vm.createContext({
        window: win,
        document: doc,
        location: win.location,
        localStorage: win.localStorage,
        AbortController,
        fetch: async (url, init = {}) => {
            if (String(url) === '/api/steam/update-playtime') {
                playtimePosts.push({ body: JSON.parse(init.body), headers: init.headers });
                return { ok: true, status: 200, json: async () => ({ success: true, progressUnlocked: [] }) };
            }
            return { ok: true, status: 200, json: async () => ({ success: true, stats: {} }) };
        },
        Date: class extends Date { static now() { return fakeNow; } },
        setTimeout(fn, ms) {
            const entry = { id: nextTimerId++, fn, ms, cleared: false };
            timers.push(entry);
            return entry.id;
        },
        clearTimeout(id) {
            const t = timers.find(x => x.id === id);
            if (t) t.cleared = true;
        },
        console: { log() {}, warn() {}, error() {}, debug() {} },
    });

    vm.runInContext(code, ctx);
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));

    const intervalTimers = () => timers.filter(t => t.ms === 60000);
    const timeoutTimers = () => timers.filter(t => t.ms === 15000);

    // Tick 1: cold-cache getMutationHeaders hangs forever -> firing 15s timeout clears inFlightFlush and skips fetch
    fakeNow += 60000;
    const tick1Promise = intervalTimers()[0].fn();
    await new Promise(r => setImmediate(r));
    assert.equal(playtimePosts.length, 0, 'must not call fetch while header lookup hangs');
    const activeHeaderTimeout = timeoutTimers().filter(t => !t.cleared).at(-1);
    assert.ok(activeHeaderTimeout, 'must arm 15s timeout before awaiting cold-cache getMutationHeaders');
    activeHeaderTimeout.fn();
    await tick1Promise;
    await new Promise(r => setImmediate(r));
    assert.equal(playtimePosts.length, 0);

    // Tick 2: header lookup recovers -> accumulated 120s flushes cleanly with warm-csrf-token
    hangHeaders = false;
    fakeNow += 60000;
    await intervalTimers()[1].fn();
    assert.equal(playtimePosts.length, 1, 'next tick must not stay wedged after header timeout');
    assert.equal(playtimePosts[0].body.seconds, 120);
    assert.equal(playtimePosts[0].headers['X-CSRF-Token'], 'warm-csrf-token');
});

test('PR3976 4661235259: character switch mid-restore aborts cleanly without writing flags or clearing original snapshot', async () => {
    // Case A: owner switches from CatA to CatB while GET /api/agent/flags is in flight
    let resolveFlagsGetA;
    const storageA = createStorage();
    const snapshotA = JSON.stringify({
        version: 1,
        token: 501,
        createdAt: Date.now(),
        lanlanName: 'CatA',
        agentFlags: {
            agent_enabled: true,
            computer_use_enabled: true,
            browser_use_enabled: false,
            user_plugin_enabled: false,
        },
    });
    storageA.setItem(HOME_TUTORIAL_KEY, snapshotA);

    const rtA = loadHomeTutorialRuntime({
        localStorage: storageA,
        lanlanName: 'CatA',
        fetchHandler: async (url, init) => {
            if (url === '/api/agent/flags' && (init.method || 'GET') === 'GET') {
                return await new Promise(resolve => { resolveFlagsGetA = resolve; });
            }
            return { ok: true, json: async () => ({ success: true }) };
        },
    });
    await new Promise(r => setImmediate(r));
    assert.equal(typeof resolveFlagsGetA, 'function');

    rtA.win.lanlan_config.lanlan_name = 'CatB';
    resolveFlagsGetA({
        ok: true,
        json: async () => ({
            success: true,
            analyzer_enabled: false,
            agent_flags: { computer_use_enabled: false, browser_use_enabled: false, user_plugin_enabled: false },
        }),
    });
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));

    const postCallsA = rtA.fetchCalls.filter(c => c.method === 'POST');
    assert.equal(postCallsA.length, 0, 'owner switch during GET must abort before any POST writes');
    assert.equal(storageA.getItem(HOME_TUTORIAL_KEY), snapshotA, 'CatA snapshot must remain untouched in localStorage');

    // Case B: owner switches from CatA to CatB while POST /api/agent/command is in flight
    let resolveCommandPostB;
    const storageB = createStorage();
    const snapshotB = JSON.stringify({
        version: 1,
        token: 502,
        createdAt: Date.now(),
        lanlanName: 'CatA',
        agentFlags: {
            agent_enabled: true,
            computer_use_enabled: true,
            browser_use_enabled: false,
            user_plugin_enabled: false,
        },
    });
    storageB.setItem(HOME_TUTORIAL_KEY, snapshotB);

    const rtB = loadHomeTutorialRuntime({
        localStorage: storageB,
        lanlanName: 'CatA',
        fetchHandler: async (url, init) => {
            if (url === '/api/agent/flags' && (init.method || 'GET') === 'GET') {
                return {
                    ok: true,
                    json: async () => ({
                        success: true,
                        analyzer_enabled: false,
                        agent_flags: { computer_use_enabled: false, browser_use_enabled: false, user_plugin_enabled: false },
                    }),
                };
            }
            if (url === '/api/agent/command' && init.method === 'POST') {
                return await new Promise(resolve => { resolveCommandPostB = resolve; });
            }
            return { ok: true, json: async () => ({ success: true }) };
        },
    });
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));
    assert.equal(typeof resolveCommandPostB, 'function');

    rtB.win.lanlan_config.lanlan_name = 'CatB';
    resolveCommandPostB({ ok: true, json: async () => ({ success: true }) });
    await new Promise(r => setImmediate(r));
    await new Promise(r => setImmediate(r));

    const flagsPostsB = rtB.fetchCalls.filter(c => c.url === '/api/agent/flags' && c.method === 'POST');
    const commandPostsB = rtB.fetchCalls.filter(c => c.url === '/api/agent/command' && c.method === 'POST');
    assert.equal(flagsPostsB.length, 0, 'owner switch during master command must abort before POST /api/agent/flags');
    assert.equal(commandPostsB.length, 1, 'owner switch must NOT trigger re-disable POST /api/agent/command on new character');
    const persistedB = JSON.parse(storageB.getItem(HOME_TUTORIAL_KEY));
    const originalB = JSON.parse(snapshotB);
    assert.equal(persistedB.token, originalB.token, 'CatA snapshot must keep its token when aborted after command');
    assert.equal(persistedB.lanlanName, 'CatA');
    assert.deepEqual(persistedB.agentFlags, originalB.agentFlags, 'CatA snapshot flags must remain untouched when aborted after command');
    assert.equal(persistedB.masterApplied, true, 'snapshot must remember that the master switch was turned on by the restore');
});

function makeAgentServer() {
    const server = {
        master: false,
        child: { computer_use_enabled: false, browser_use_enabled: false, user_plugin_enabled: false },
        hangChildPost: false,
        onCommand: null,
    };
    server.handler = async (url, init, body) => {
        if (url === '/api/agent/flags' && (init.method || 'GET') === 'GET') {
            return {
                ok: true,
                json: async () => ({ success: true, analyzer_enabled: server.master, agent_flags: Object.assign({}, server.child) }),
            };
        }
        if (url === '/api/agent/command' && init.method === 'POST') {
            server.master = !!body.enabled;
            if (server.onCommand) {
                await server.onCommand(body);
            }
            return { ok: true, json: async () => ({ success: true }) };
        }
        if (url === '/api/agent/flags' && init.method === 'POST') {
            if (server.hangChildPost) {
                return await new Promise(() => {});
            }
            server.child = Object.assign({}, server.child, body.flags);
            return { ok: true, json: async () => ({ success: true }) };
        }
        return { ok: true, json: async () => ({ success: true }) };
    };
    return server;
}

async function flushMicrotasks(rounds = 6) {
    for (let i = 0; i < rounds; i += 1) {
        await new Promise(r => setImmediate(r));
    }
}

test('PR3976 master-on persisted: child flags still restore on next load after owner switch or page close between the two POSTs', async () => {
    const snapshotFlags = {
        agent_enabled: true,
        computer_use_enabled: true,
        browser_use_enabled: false,
        user_plugin_enabled: false,
    };

    // Case A: master POST lands, user switches CatA -> CatB before the child POST.
    {
        const storage = createStorage();
        storage.setItem(HOME_TUTORIAL_KEY, JSON.stringify({
            version: 1, token: 601, createdAt: Date.now(), lanlanName: 'CatA', agentFlags: snapshotFlags,
        }));
        const server = makeAgentServer();
        let rt1;
        server.onCommand = async () => {
            rt1.win.lanlan_config.lanlan_name = 'CatB';
        };
        rt1 = loadHomeTutorialRuntime({ localStorage: storage, lanlanName: 'CatA', fetchHandler: server.handler });
        await flushMicrotasks();
        assert.equal(server.master, true);
        assert.equal(server.child.computer_use_enabled, false, 'child POST must not run under the new owner');
        assert.equal(JSON.parse(storage.getItem(HOME_TUTORIAL_KEY)).masterApplied, true);

        // Next page load back on CatA: master is ON (from our restore), must not be mistaken for a user change.
        server.onCommand = null;
        const rt2 = loadHomeTutorialRuntime({ localStorage: storage, lanlanName: 'CatA', fetchHandler: server.handler });
        await flushMicrotasks();
        assert.equal(server.child.computer_use_enabled, true, 'child flags must be restored on the next load');
        assert.equal(server.child.user_plugin_enabled, false);
        assert.equal(storage.getItem(HOME_TUTORIAL_KEY), null, 'snapshot clears only after child restore completes');
        assert.ok(rt2.fetchCalls.some(c => c.url === '/api/agent/flags' && c.method === 'POST'));
    }

    // Case B: master POST lands, page is closed while the child POST is still pending.
    {
        const storage = createStorage();
        storage.setItem(HOME_TUTORIAL_KEY, JSON.stringify({
            version: 1, token: 602, createdAt: Date.now(), lanlanName: 'CatA', agentFlags: snapshotFlags,
        }));
        const server = makeAgentServer();
        server.hangChildPost = true;
        loadHomeTutorialRuntime({ localStorage: storage, lanlanName: 'CatA', fetchHandler: server.handler });
        await flushMicrotasks();
        assert.equal(server.master, true);
        assert.equal(JSON.parse(storage.getItem(HOME_TUTORIAL_KEY)).masterApplied, true);

        server.hangChildPost = false;
        loadHomeTutorialRuntime({ localStorage: storage, lanlanName: 'CatA', fetchHandler: server.handler });
        await flushMicrotasks();
        assert.equal(server.child.computer_use_enabled, true, 'child flags must be restored after reopening the page');
        assert.equal(storage.getItem(HOME_TUTORIAL_KEY), null);
    }

    // Case C: user turned master OFF themselves after the interrupted restore -> respect it, do not re-enable.
    {
        const storage = createStorage();
        storage.setItem(HOME_TUTORIAL_KEY, JSON.stringify({
            version: 1, token: 603, createdAt: Date.now(), lanlanName: 'CatA', agentFlags: snapshotFlags, masterApplied: true,
        }));
        const server = makeAgentServer();
        const rt = loadHomeTutorialRuntime({ localStorage: storage, lanlanName: 'CatA', fetchHandler: server.handler });
        await flushMicrotasks();
        assert.equal(rt.fetchCalls.filter(c => c.method === 'POST').length, 0, 'must not re-enable a master switch the user turned off');
        assert.equal(storage.getItem(HOME_TUTORIAL_KEY), null);
    }
});
