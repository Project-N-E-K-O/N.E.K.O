const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const stateApi = require('./tutorial/core/seven-day-state.js');
const stateSource = fs.readFileSync(
    require.resolve('./tutorial/core/seven-day-state.js'),
    'utf8'
);

function createMemoryStorage(initial = {}) {
    const values = new Map(Object.entries(initial));
    return {
        getItem(key) {
            return values.has(key) ? values.get(key) : null;
        },
        setItem(key, value) {
            values.set(key, String(value));
        },
        removeItem(key) {
            values.delete(key);
        },
    };
}

function createJsonResponse(payload, status = 200) {
    return {
        ok: status >= 200 && status < 300,
        status,
        async json() {
            return payload;
        },
    };
}

function loadBrowserStateApi({ storage, fetch, storageBarrier = Promise.resolve() }) {
    const window = {
        document: {},
        location: { origin: 'http://localhost:48911' },
        localStorage: storage,
        fetch,
        console,
        __nekoStorageLocationStartupBarrier: storageBarrier,
    };
    vm.runInNewContext(stateSource, {
        window,
        globalThis: window,
        console,
        Promise,
        Date,
        JSON,
        Set,
        Object,
        Number,
        String,
        Array,
        Math,
        Error,
    });
    return window.NekoSevenDayTutorialState;
}

function applyAuthoritativePut(authoritativeStore, options) {
    const body = JSON.parse(options.body);
    if (body.expectedRevision !== authoritativeStore.revision) {
        return createJsonResponse({
            ok: false,
            error_code: 'seven_day_tutorial_revision_conflict',
            ...authoritativeStore,
        }, 409);
    }
    authoritativeStore.initialized = true;
    authoritativeStore.revision += 1;
    authoritativeStore.state = body.state;
    return createJsonResponse({ ok: true, ...authoritativeStore });
}

test('legacy home completion migrates into the unified Day 1 round state once', () => {
    const storage = createMemoryStorage({
        neko_tutorial_home_yui_v1: 'true',
    });

    const state = stateApi.loadState({ storage, today: '2026-07-22' });

    assert.equal(state.version, 2);
    assert.deepEqual(state.completedRounds, [1]);
    assert.deepEqual(state.skippedRounds, []);
    assert.equal(state.legacyMigrationCompleted, true);

    const persisted = JSON.parse(storage.getItem(stateApi.STORAGE_KEY));
    assert.deepEqual(persisted.completedRounds, [1]);
    assert.equal(persisted.legacyMigrationCompleted, true);

    stateApi.resetRound(1, { storage, source: 'test-reset' });
    const afterReset = stateApi.loadState({ storage, today: '2026-07-22' });
    assert.deepEqual(afterReset.completedRounds, []);
    assert.equal(afterReset.manualResetRound, 1);
    assert.equal(storage.getItem('neko_tutorial_home_yui_v1'), null);
});

test('existing seven-day progress wins over legacy markers during migration', () => {
    const storage = createMemoryStorage({
        neko_tutorial_home_yui_v1: 'true',
        [stateApi.STORAGE_KEY]: JSON.stringify({
            version: 1,
            firstSeenDate: '2026-07-20',
            completedRounds: [1, 2],
            skippedRounds: [3],
        }),
    });

    const state = stateApi.loadState({ storage, today: '2026-07-22' });

    assert.deepEqual(state.completedRounds, [1, 2]);
    assert.deepEqual(state.skippedRounds, [3]);
    assert.equal(state.firstSeenDate, '2026-07-20');
});

test('the unified scheduler advances one round on each of the first seven calendar days', () => {
    const storage = createMemoryStorage();
    let state = stateApi.loadState({ storage, today: '2026-07-01' });

    for (let day = 1; day <= 7; day += 1) {
        const today = `2026-07-${String(day).padStart(2, '0')}`;
        assert.equal(stateApi.getNextAutoRound(state, today), day);
        state = stateApi.markRoundOutcome(day, 'complete', { storage, today });
    }

    assert.equal(stateApi.getNextAutoRound(state, '2026-07-08'), null);
    assert.deepEqual(state.completedRounds, [1, 2, 3, 4, 5, 6, 7]);
});

test('seven cold starts trigger at most once per day and advance through Day 1-7', () => {
    const storage = createMemoryStorage();

    for (let day = 1; day <= 7; day += 1) {
        const today = `2026-07-${String(day).padStart(2, '0')}`;
        let state = stateApi.loadState({ storage, today });
        assert.equal(stateApi.getNextAutoRound(state, today), day);

        stateApi.markAutoStartReservation(day, {
            storage,
            today,
            reservationId: `cold-start-${day}`,
        });
        state = stateApi.loadState({ storage, today });
        assert.equal(stateApi.getNextAutoRound(state, today), null);

        stateApi.markRoundOutcome(day, 'complete', { storage, today });
    }

    const state = stateApi.loadState({ storage, today: '2026-07-08' });
    assert.deepEqual(state.completedRounds, [1, 2, 3, 4, 5, 6, 7]);
    assert.equal(stateApi.getNextAutoRound(state, '2026-07-08'), null);
});

test('calendar day delta is stable across a daylight-saving transition', () => {
    assert.equal(stateApi.getCalendarDayDelta('2026-03-07', '2026-03-09'), 2);
    assert.equal(stateApi.getCalendarDayDelta('2026-11-01', '2026-11-03'), 2);
});

test('pre-start failure rolls back only the matching unfinished auto reservation', () => {
    const storage = createMemoryStorage({
        [stateApi.STORAGE_KEY]: JSON.stringify({
            version: 2,
            firstSeenDate: '2026-07-01',
            completedRounds: [1],
            skippedRounds: [],
            lastAutoShownRound: 2,
            lastAutoShownDate: '2026-07-02',
        }),
    });

    assert.equal(stateApi.rollbackAutoStartReservation(3, {
        storage,
        today: '2026-07-02',
    }), false);
    assert.equal(stateApi.rollbackAutoStartReservation(2, {
        storage,
        today: '2026-07-02',
    }), true);

    const state = stateApi.loadState({ storage, today: '2026-07-02' });
    assert.equal(state.lastAutoShownRound, null);
    assert.equal(state.lastAutoShownDate, '');
    assert.equal(stateApi.getNextAutoRound(state, '2026-07-02'), 2);
});

test('a stale failure cannot roll back a newer matching-round reservation', () => {
    const storage = createMemoryStorage({
        [stateApi.STORAGE_KEY]: JSON.stringify({
            version: 2,
            firstSeenDate: '2026-07-01',
            completedRounds: [1],
            skippedRounds: [],
            lastAutoShownRound: 2,
            lastAutoShownDate: '2026-07-02',
            lastAutoReservationId: 'new-run',
            legacyMigrationCompleted: true,
        }),
    });

    assert.equal(stateApi.rollbackAutoStartReservation(2, {
        storage,
        today: '2026-07-02',
        reservationId: 'old-run',
    }), false);
    assert.equal(stateApi.rollbackAutoStartReservation(2, {
        storage,
        today: '2026-07-02',
        reservationId: 'new-run',
    }), true);
});

test('resetting Day 1 clears only Day 1 progress', () => {
    const storage = createMemoryStorage({
        [stateApi.STORAGE_KEY]: JSON.stringify({
            version: 2,
            firstSeenDate: '2026-07-01',
            completedRounds: [1, 2],
            skippedRounds: [3],
            lastAutoShownRound: 1,
            lastAutoShownDate: '2026-07-04',
        }),
    });

    const reset = stateApi.resetRound(1, { storage, source: 'test' });

    assert.deepEqual(reset.completedRounds, [2]);
    assert.deepEqual(reset.skippedRounds, [3]);
    assert.equal(reset.pendingRound, 1);
    assert.equal(reset.manualResetRound, 1);
    assert.equal(reset.lastAutoShownRound, null);
    assert.equal(reset.lastAutoShownDate, '');
});

test('resetting Day 2 leaves Day 1 progress untouched', () => {
    const storage = createMemoryStorage({
        [stateApi.STORAGE_KEY]: JSON.stringify({
            version: 2,
            firstSeenDate: '2026-07-01',
            completedRounds: [1, 2],
            skippedRounds: [],
            legacyMigrationCompleted: true,
        }),
    });

    const reset = stateApi.resetRound(2, { storage, source: 'test' });

    assert.deepEqual(reset.completedRounds, [1]);
    assert.equal(reset.manualResetRound, 2);
});

test('a new port origin restores the authoritative progress seeded by the previous origin', async () => {
    let authoritativeStore = {
        initialized: false,
        revision: 0,
        state: null,
    };
    const requests = [];
    const fetch = async (url, options = {}) => {
        requests.push({ url, options });
        if (url === '/api/config/page_config') {
            return createJsonResponse({ autostart_csrf_token: 'test-token' });
        }
        if (url === stateApi.SERVER_STATE_ENDPOINT && options.method === 'PUT') {
            return applyAuthoritativePut(authoritativeStore, options);
        }
        if (url === stateApi.SERVER_STATE_ENDPOINT) {
            return createJsonResponse({ ok: true, ...authoritativeStore });
        }
        return createJsonResponse({}, 404);
    };

    const firstOriginStorage = createMemoryStorage({
        [stateApi.STORAGE_KEY]: JSON.stringify({
            version: 2,
            firstSeenDate: '2026-07-01',
            completedRounds: [1, 2],
            skippedRounds: [],
            legacyMigrationCompleted: true,
        }),
    });
    const firstOriginApi = loadBrowserStateApi({ storage: firstOriginStorage, fetch });
    await firstOriginApi.ready();

    assert.equal(authoritativeStore.initialized, true);
    assert.deepEqual(authoritativeStore.state.completedRounds, [1, 2]);
    assert.equal(
        requests.find(item => item.options.method === 'PUT').options.headers['X-CSRF-Token'],
        'test-token'
    );

    const fallbackPortStorage = createMemoryStorage();
    const fallbackPortApi = loadBrowserStateApi({ storage: fallbackPortStorage, fetch });
    const restored = await fallbackPortApi.ready();

    assert.deepEqual(Array.from(restored.completedRounds), [1, 2]);
    assert.equal(restored.firstSeenDate, '2026-07-01');
    assert.deepEqual(
        JSON.parse(fallbackPortStorage.getItem(stateApi.STORAGE_KEY)).completedRounds,
        [1, 2]
    );
});

test('mutations are queued to the authoritative store after initial synchronization', async () => {
    const writes = [];
    const storage = createMemoryStorage();
    const fetch = async (url, options = {}) => {
        if (url === stateApi.SERVER_STATE_ENDPOINT && options.method === 'PUT') {
            const body = JSON.parse(options.body);
            assert.equal(body.expectedRevision, writes.length);
            const state = body.state;
            writes.push(state);
            return createJsonResponse({ ok: true, initialized: true, revision: writes.length, state });
        }
        if (url === stateApi.SERVER_STATE_ENDPOINT) {
            return createJsonResponse({ ok: true, initialized: false, revision: 0, state: null });
        }
        if (url === '/api/config/page_config') {
            return createJsonResponse({ autostart_csrf_token: 'test-token' });
        }
        return createJsonResponse({}, 404);
    };
    const browserApi = loadBrowserStateApi({ storage, fetch });
    await browserApi.ready();

    browserApi.markRoundOutcome(1, 'complete');
    browserApi.markRoundOutcome(2, 'skip');
    await browserApi.flush();

    assert.deepEqual(Array.from(writes.at(-1).completedRounds), [1]);
    assert.deepEqual(Array.from(writes.at(-1).skippedRounds), [2]);
});

test('a reset made during startup wins over the initial authoritative read', async () => {
    let releaseStorageBarrier;
    const storageBarrier = new Promise(resolve => {
        releaseStorageBarrier = resolve;
    });
    const writes = [];
    const fetch = async (url, options = {}) => {
        if (url === stateApi.SERVER_STATE_ENDPOINT && options.method === 'PUT') {
            const body = JSON.parse(options.body);
            assert.equal(body.expectedRevision, 1);
            writes.push(body.state);
            return createJsonResponse({
                ok: true,
                initialized: true,
                revision: writes.length + 1,
                state: writes.at(-1),
            });
        }
        if (url === stateApi.SERVER_STATE_ENDPOINT) {
            return createJsonResponse({
                ok: true,
                initialized: true,
                revision: 1,
                state: {
                    version: 2,
                    firstSeenDate: '2026-07-01',
                    completedRounds: [1],
                    skippedRounds: [],
                    legacyMigrationCompleted: true,
                },
            });
        }
        if (url === '/api/config/page_config') {
            return createJsonResponse({ autostart_csrf_token: 'test-token' });
        }
        return createJsonResponse({}, 404);
    };
    const browserApi = loadBrowserStateApi({
        storage: createMemoryStorage(),
        fetch,
        storageBarrier,
    });

    browserApi.resetRound(1, { source: 'startup-reset' });
    const flushed = browserApi.flush();
    releaseStorageBarrier();
    await flushed;

    assert.equal(writes.length, 1);
    assert.deepEqual(Array.from(writes[0].completedRounds), []);
    assert.equal(writes[0].manualResetRound, 1);
});

test('a mutation made during the initial server write is not overwritten', async () => {
    let releaseInitialPut;
    let initialPutStarted = false;
    let revision = 0;
    let authoritativeState = null;
    const initialPutGate = new Promise(resolve => {
        releaseInitialPut = resolve;
    });
    const fetch = async (url, options = {}) => {
        if (url === stateApi.SERVER_STATE_ENDPOINT && options.method === 'PUT') {
            const body = JSON.parse(options.body);
            if (!initialPutStarted) {
                initialPutStarted = true;
                await initialPutGate;
            }
            assert.equal(body.expectedRevision, revision);
            revision += 1;
            authoritativeState = body.state;
            return createJsonResponse({
                ok: true,
                initialized: true,
                revision,
                state: authoritativeState,
            });
        }
        if (url === stateApi.SERVER_STATE_ENDPOINT) {
            return createJsonResponse({ ok: true, initialized: false, revision: 0, state: null });
        }
        if (url === '/api/config/page_config') {
            return createJsonResponse({ autostart_csrf_token: 'test-token' });
        }
        return createJsonResponse({}, 404);
    };
    const browserApi = loadBrowserStateApi({ storage: createMemoryStorage(), fetch });
    while (!initialPutStarted) {
        await Promise.resolve();
    }

    browserApi.markRoundOutcome(1, 'complete');
    releaseInitialPut();
    await browserApi.flush();

    assert.deepEqual(Array.from(authoritativeState.completedRounds), [1]);
    assert.deepEqual(Array.from(browserApi.loadState().completedRounds), [1]);
});

test('flush retries a failed authoritative write and reports permanent failure', async () => {
    const storage = createMemoryStorage();
    let putAttempts = 0;
    let authoritativeState = null;
    const fetch = async (url, options = {}) => {
        if (url === stateApi.SERVER_STATE_ENDPOINT && options.method === 'PUT') {
            putAttempts += 1;
            if (putAttempts === 1) {
                return createJsonResponse({ ok: false }, 503);
            }
            const body = JSON.parse(options.body);
            authoritativeState = body.state;
            return createJsonResponse({
                ok: true,
                initialized: true,
                revision: 2,
                state: authoritativeState,
            });
        }
        if (url === stateApi.SERVER_STATE_ENDPOINT) {
            return createJsonResponse({
                ok: true,
                initialized: true,
                revision: 1,
                state: {
                    version: 2,
                    firstSeenDate: '2026-07-01',
                    completedRounds: [],
                    skippedRounds: [],
                    legacyMigrationCompleted: true,
                },
            });
        }
        if (url === '/api/config/page_config') {
            return createJsonResponse({ autostart_csrf_token: 'test-token' });
        }
        return createJsonResponse({}, 404);
    };
    const browserApi = loadBrowserStateApi({ storage, fetch });
    await browserApi.ready();

    browserApi.markRoundOutcome(1, 'complete');
    await browserApi.flush();

    assert.equal(putAttempts, 2);
    assert.deepEqual(Array.from(authoritativeState.completedRounds), [1]);

    const failingApi = loadBrowserStateApi({
        storage: createMemoryStorage(),
        fetch: async (url, options = {}) => {
            if (url === stateApi.SERVER_STATE_ENDPOINT && options.method === 'PUT') {
                return createJsonResponse({ ok: false }, 503);
            }
            if (url === stateApi.SERVER_STATE_ENDPOINT) {
                return createJsonResponse({ ok: true, initialized: false, revision: 0, state: null });
            }
            if (url === '/api/config/page_config') {
                return createJsonResponse({ autostart_csrf_token: 'test-token' });
            }
            return createJsonResponse({}, 404);
        },
    });
    await failingApi.ready();
    failingApi.markRoundOutcome(1, 'complete');
    await assert.rejects(failingApi.flush(), /seven-day tutorial state write failed/);
});

test('a delayed stale window cannot overwrite a newer reset', async () => {
    const authoritativeStore = {
        initialized: true,
        revision: 1,
        state: {
            version: 2,
            firstSeenDate: '2026-07-01',
            completedRounds: [],
            skippedRounds: [],
            legacyMigrationCompleted: true,
            updatedAt: '2026-07-01T00:00:00.000Z',
        },
    };
    let releaseDelayedPut;
    let delayedPutStarted = false;
    const delayedGate = new Promise(resolve => {
        releaseDelayedPut = resolve;
    });
    const commonFetch = async (url, options = {}) => {
        if (url === '/api/config/page_config') {
            return createJsonResponse({ autostart_csrf_token: 'test-token' });
        }
        if (url === stateApi.SERVER_STATE_ENDPOINT && options.method === 'PUT') {
            return applyAuthoritativePut(authoritativeStore, options);
        }
        if (url === stateApi.SERVER_STATE_ENDPOINT) {
            return createJsonResponse({ ok: true, ...authoritativeStore });
        }
        return createJsonResponse({}, 404);
    };
    const delayedFetch = async (url, options = {}) => {
        if (url === stateApi.SERVER_STATE_ENDPOINT && options.method === 'PUT') {
            delayedPutStarted = true;
            await delayedGate;
        }
        return commonFetch(url, options);
    };
    const staleApi = loadBrowserStateApi({ storage: createMemoryStorage(), fetch: delayedFetch });
    const resetApi = loadBrowserStateApi({ storage: createMemoryStorage(), fetch: commonFetch });
    await Promise.all([staleApi.ready(), resetApi.ready()]);

    staleApi.markRoundOutcome(1, 'complete');
    const staleFlush = staleApi.flush();
    while (!delayedPutStarted) {
        await Promise.resolve();
    }
    resetApi.resetRound(1, { source: 'newer-reset' });
    await resetApi.flush();
    releaseDelayedPut();
    await staleFlush;

    assert.deepEqual(Array.from(authoritativeStore.state.completedRounds), []);
    assert.equal(authoritativeStore.state.manualResetRound, 1);
});

test('avatar model boot waits for the seven-day authoritative readiness barrier', () => {
    const indexSource = fs.readFileSync(require.resolve('./js/index.js'), 'utf8');
    const live2dSource = fs.readFileSync(require.resolve('./live2d/live2d-init.js'), 'utf8');

    assert.match(stateSource, /__nekoSevenDayTutorialStateReady\s*=\s*ready\(\)/);
    assert.match(indexSource, /await window\.__nekoSevenDayTutorialStateReady/);
    assert.match(live2dSource, /await window\.__nekoSevenDayTutorialStateReady/);
});
