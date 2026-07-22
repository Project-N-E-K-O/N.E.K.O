(function (root, factory) {
    'use strict';

    const api = factory(root || {});
    if (root) {
        root.NekoSevenDayTutorialState = api;
    }
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function (host) {
    'use strict';

    const STORAGE_KEY = 'neko_avatar_floating_guide_v1';
    const SERVER_STATE_ENDPOINT = '/api/seven-day-tutorial/state';
    const LEGACY_HOME_TUTORIAL_KEYS = Object.freeze(['neko_tutorial_home_yui_v1']);
    const ROUND_COUNT = 7;
    const RESET_HISTORY_LIMIT = 20;
    let authoritativeReady = false;
    let authoritativeRevision = 0;
    let readyPromise = null;
    let serverWriteQueue = Promise.resolve();
    let localMutationSequence = 0;
    let dirtyServerState = null;
    let dirtyServerGeneration = 0;
    let lastServerSyncError = null;

    function getTodayLocalDate(now) {
        const value = now instanceof Date ? now : new Date();
        const year = value.getFullYear();
        const month = String(value.getMonth() + 1).padStart(2, '0');
        const day = String(value.getDate()).padStart(2, '0');
        return `${year}-${month}-${day}`;
    }

    function resolveToday(options) {
        const explicit = options && typeof options.today === 'string'
            ? options.today.trim()
            : '';
        return explicit || getTodayLocalDate(options && options.now);
    }

    function resolveStorage(options) {
        if (options && Object.prototype.hasOwnProperty.call(options, 'storage')) {
            return options.storage;
        }
        return host.localStorage || null;
    }

    function normalizeRound(value) {
        const round = Number(value);
        return Number.isInteger(round) && round >= 1 && round <= ROUND_COUNT ? round : null;
    }

    function requireRound(value) {
        const round = normalizeRound(value);
        if (!round) {
            throw new Error(`Invalid tutorial day: ${value}`);
        }
        return round;
    }

    function normalizeRoundList(value) {
        if (!Array.isArray(value)) {
            return [];
        }
        return Array.from(new Set(
            value
                .map(normalizeRound)
                .filter(Boolean)
        )).sort((left, right) => left - right);
    }

    function omitRound(value, round) {
        return normalizeRoundList(value).filter(item => item !== round);
    }

    function parseCalendarDayNumber(value) {
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || '').trim());
        if (!match) {
            return null;
        }
        const year = Number(match[1]);
        const month = Number(match[2]);
        const day = Number(match[3]);
        const timestamp = Date.UTC(year, month - 1, day);
        const parsed = new Date(timestamp);
        if (
            parsed.getUTCFullYear() !== year
            || parsed.getUTCMonth() !== month - 1
            || parsed.getUTCDate() !== day
        ) {
            return null;
        }
        return Math.floor(timestamp / 86400000);
    }

    function getCalendarDayDelta(fromDate, toDate) {
        const fromDay = parseCalendarDayNumber(fromDate);
        const toDay = parseCalendarDayNumber(toDate);
        if (fromDay === null || toDay === null) {
            return 0;
        }
        return Math.max(0, toDay - fromDay);
    }

    function readRawState(storage) {
        if (!storage || typeof storage.getItem !== 'function') {
            return {};
        }
        try {
            const raw = storage.getItem(STORAGE_KEY);
            const parsed = raw ? JSON.parse(raw) : {};
            return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
        } catch (error) {
            console.warn('[SevenDayTutorialState] 状态读取失败，使用空状态:', error);
            return {};
        }
    }

    function hasLegacyHomeTutorialCompletion(storage) {
        if (!storage || typeof storage.getItem !== 'function') {
            return false;
        }
        try {
            return LEGACY_HOME_TUTORIAL_KEYS.some(key => storage.getItem(key) === 'true');
        } catch (_) {
            return false;
        }
    }

    function clearLegacyHomeTutorialCompletion(options) {
        const storage = resolveStorage(options);
        if (!storage || typeof storage.removeItem !== 'function') {
            return false;
        }
        try {
            LEGACY_HOME_TUTORIAL_KEYS.forEach(key => storage.removeItem(key));
            return true;
        } catch (error) {
            console.warn('[SevenDayTutorialState] 旧主页教程状态清理失败:', error);
            return false;
        }
    }

    function normalizeState(rawState, options) {
        const parsed = rawState && typeof rawState === 'object' && !Array.isArray(rawState)
            ? rawState
            : {};
        const storage = resolveStorage(options);
        const today = resolveToday(options);
        let completedRounds = normalizeRoundList(parsed.completedRounds);
        let skippedRounds = normalizeRoundList(parsed.skippedRounds)
            .filter(round => !completedRounds.includes(round));
        const legacyCompletion = parsed.legacyMigrationCompleted !== true
            && hasLegacyHomeTutorialCompletion(storage);

        if (legacyCompletion && !completedRounds.includes(1) && !skippedRounds.includes(1)) {
            completedRounds = normalizeRoundList(completedRounds.concat(1));
        }
        if (completedRounds.includes(1)) {
            skippedRounds = omitRound(skippedRounds, 1);
        }

        return {
            version: 2,
            firstSeenDate: parseCalendarDayNumber(parsed.firstSeenDate) === null
                ? today
                : parsed.firstSeenDate,
            completedRounds,
            skippedRounds,
            currentRound: normalizeRound(parsed.currentRound),
            pendingRound: normalizeRound(parsed.pendingRound),
            manualResetRound: normalizeRound(parsed.manualResetRound),
            lastAutoShownRound: normalizeRound(parsed.lastAutoShownRound),
            lastAutoShownDate: parseCalendarDayNumber(parsed.lastAutoShownDate) === null
                ? ''
                : parsed.lastAutoShownDate,
            lastAutoReservationId: typeof parsed.lastAutoReservationId === 'string'
                ? parsed.lastAutoReservationId
                : '',
            lastEndState: parsed.lastEndState && typeof parsed.lastEndState === 'object'
                ? parsed.lastEndState
                : null,
            legacyMigrationCompleted: true,
            updatedAt: parsed.updatedAt || null,
            resetHistory: Array.isArray(parsed.resetHistory)
                ? parsed.resetHistory.slice(-RESET_HISTORY_LIMIT)
                : [],
        };
    }

    function writeLocalState(state, options) {
        const storage = resolveStorage(options);
        if (!storage || typeof storage.setItem !== 'function') {
            return false;
        }
        try {
            storage.setItem(STORAGE_KEY, JSON.stringify(state));
            return true;
        } catch (error) {
            console.warn('[SevenDayTutorialState] 状态写入失败:', error);
            return false;
        }
    }

    function canUseServerState() {
        return !!(
            host
            && host.document
            && host.location
            && typeof host.fetch === 'function'
        );
    }

    async function getMutationHeaders() {
        const headers = { 'Content-Type': 'application/json' };
        const helper = host.nekoLocalMutationSecurity;
        if (helper && typeof helper.getMutationHeaders === 'function') {
            try {
                return Object.assign(headers, await helper.getMutationHeaders());
            } catch (error) {
                console.warn('[SevenDayTutorialState] 获取写入安全头失败，尝试页面配置:', error);
            }
        }

        try {
            const response = await host.fetch('/api/config/page_config', { cache: 'no-store' });
            if (!response.ok) return headers;
            const payload = await response.json();
            if (payload && typeof payload.autostart_csrf_token === 'string' && payload.autostart_csrf_token) {
                headers['X-CSRF-Token'] = payload.autostart_csrf_token;
            }
        } catch (error) {
            console.warn('[SevenDayTutorialState] 读取页面配置失败，保留本地进度:', error);
        }
        return headers;
    }

    function getStateUpdatedAtMs(state) {
        const timestamp = Date.parse(state && state.updatedAt || '');
        return Number.isFinite(timestamp) ? timestamp : 0;
    }

    function selectNewestLocalState(left, right) {
        return getStateUpdatedAtMs(right) > getStateUpdatedAtMs(left) ? right : left;
    }

    function normalizeServerPayloadState(payload, fallbackState) {
        const rawState = payload && payload.state ? payload.state : fallbackState;
        return normalizeState(rawState, {
            storage: null,
            today: fallbackState && fallbackState.firstSeenDate,
        });
    }

    async function writeServerState(state, expectedRevision) {
        const response = await host.fetch(SERVER_STATE_ENDPOINT, {
            method: 'PUT',
            headers: await getMutationHeaders(),
            body: JSON.stringify({
                state,
                expectedRevision,
            }),
            keepalive: true,
        });
        const payload = await response.json().catch(() => null);
        if (response.status === 409 && payload) {
            const conflict = new Error('seven-day tutorial state revision conflict');
            conflict.code = 'seven_day_tutorial_revision_conflict';
            conflict.store = payload;
            throw conflict;
        }
        if (!response.ok) {
            throw new Error(`seven-day tutorial state write failed: ${response.status}`);
        }
        return payload || {};
    }

    async function syncServerSnapshot(state) {
        let snapshot = normalizeState(state, {
            storage: null,
            today: state && state.firstSeenDate,
        });
        for (let attempt = 0; attempt < 3; attempt += 1) {
            try {
                const payload = await writeServerState(snapshot, authoritativeRevision);
                authoritativeRevision = Math.max(0, Number(payload.revision) || 0);
                return normalizeServerPayloadState(payload, snapshot);
            } catch (error) {
                if (!error || error.code !== 'seven_day_tutorial_revision_conflict' || !error.store) {
                    throw error;
                }
                const currentStore = error.store;
                authoritativeRevision = Math.max(0, Number(currentStore.revision) || 0);
                const serverState = normalizeServerPayloadState(currentStore, snapshot);
                const currentLocalState = loadState({ persistMigration: false });
                const newestLocalState = selectNewestLocalState(snapshot, currentLocalState);
                if (getStateUpdatedAtMs(newestLocalState) <= getStateUpdatedAtMs(serverState)) {
                    return serverState;
                }
                snapshot = newestLocalState;
            }
        }
        throw new Error('seven-day tutorial state conflict retry limit reached');
    }

    function rememberDirtyServerState(state) {
        const snapshot = normalizeState(state, {
            storage: null,
            today: state && state.firstSeenDate,
        });
        dirtyServerState = snapshot;
        dirtyServerGeneration += 1;
        return dirtyServerGeneration;
    }

    function enqueueServerWrite(state) {
        const generation = rememberDirtyServerState(state);
        if (!authoritativeReady || !canUseServerState()) {
            return serverWriteQueue;
        }
        serverWriteQueue = serverWriteQueue
            .then(async () => {
                const snapshot = dirtyServerState;
                if (!snapshot) {
                    return null;
                }
                const synchronizedState = await syncServerSnapshot(snapshot);
                if (dirtyServerGeneration === generation) {
                    writeLocalState(synchronizedState);
                    dirtyServerState = null;
                    lastServerSyncError = null;
                }
                return synchronizedState;
            })
            .catch((error) => {
                lastServerSyncError = error;
                console.warn('[SevenDayTutorialState] 后端同步失败，保留本地进度:', error);
                return null;
            });
        return serverWriteQueue;
    }

    function saveState(state, options) {
        const saved = writeLocalState(state, options);
        if (saved && (!options || options.syncServer !== false)) {
            localMutationSequence += 1;
            enqueueServerWrite(state);
        }
        return saved;
    }

    function loadState(options) {
        const storage = resolveStorage(options);
        const rawState = readRawState(storage);
        const isLegacyOnlyMigration = Object.keys(rawState).length === 0
            && hasLegacyHomeTutorialCompletion(storage);
        const state = normalizeState(rawState, Object.assign({}, options, { storage }));
        const needsMigration = rawState.version !== state.version
            || rawState.legacyMigrationCompleted !== true
            || JSON.stringify(normalizeRoundList(rawState.completedRounds)) !== JSON.stringify(state.completedRounds)
            || JSON.stringify(normalizeRoundList(rawState.skippedRounds)) !== JSON.stringify(state.skippedRounds);
        if (needsMigration && (!options || options.persistMigration !== false)) {
            if (!isLegacyOnlyMigration) {
                state.updatedAt = state.updatedAt || new Date().toISOString();
            }
            saveState(state, { storage, syncServer: false });
        }
        return state;
    }

    async function waitForStorageBarrier() {
        const barrier = host && host.__nekoStorageLocationStartupBarrier;
        if (!barrier || typeof barrier.then !== 'function') {
            return true;
        }
        try {
            await barrier;
            return true;
        } catch (error) {
            console.warn('[SevenDayTutorialState] 存储位置未放行，跳过后端同步:', error);
            return false;
        }
    }

    function finishInitialServerSynchronization(synchronizedState, mutationSequence) {
        authoritativeReady = true;
        if (localMutationSequence === mutationSequence) {
            writeLocalState(synchronizedState);
            dirtyServerState = null;
            lastServerSyncError = null;
            return synchronizedState;
        }
        const latestLocalState = loadState();
        enqueueServerWrite(latestLocalState);
        return latestLocalState;
    }

    function ready() {
        if (readyPromise) {
            return readyPromise;
        }
        readyPromise = (async () => {
            const storage = resolveStorage();
            const localWasStored = Object.keys(readRawState(storage)).length > 0
                || hasLegacyHomeTutorialCompletion(storage);
            const localState = loadState();
            const initialMutationSequence = localMutationSequence;
            if (!canUseServerState() || !await waitForStorageBarrier()) {
                authoritativeReady = true;
                return localState;
            }
            try {
                const response = await host.fetch(SERVER_STATE_ENDPOINT, { cache: 'no-store' });
                if (!response.ok) {
                    throw new Error(`seven-day tutorial state read failed: ${response.status}`);
                }
                const payload = await response.json();
                authoritativeRevision = Math.max(0, Number(payload && payload.revision) || 0);
                if (localMutationSequence !== initialMutationSequence) {
                    const synchronizedMutationSequence = localMutationSequence;
                    const mutatedLocalState = loadState();
                    const synchronizedState = await syncServerSnapshot(mutatedLocalState);
                    return finishInitialServerSynchronization(
                        synchronizedState,
                        synchronizedMutationSequence
                    );
                }
                if (payload && payload.initialized === true && payload.state) {
                    const authoritativeState = normalizeState(payload.state, {
                        storage: null,
                        today: localState.firstSeenDate,
                    });
                    if (
                        localWasStored
                        && getStateUpdatedAtMs(localState) > getStateUpdatedAtMs(authoritativeState)
                    ) {
                        const synchronizedMutationSequence = localMutationSequence;
                        const synchronizedState = await syncServerSnapshot(localState);
                        return finishInitialServerSynchronization(
                            synchronizedState,
                            synchronizedMutationSequence
                        );
                    }
                    writeLocalState(authoritativeState);
                    authoritativeReady = true;
                    return authoritativeState;
                }

                const currentLocalState = loadState();
                const synchronizedMutationSequence = localMutationSequence;
                const synchronizedState = await syncServerSnapshot(currentLocalState);
                return finishInitialServerSynchronization(
                    synchronizedState,
                    synchronizedMutationSequence
                );
            } catch (error) {
                console.warn('[SevenDayTutorialState] 后端初始化同步失败，使用本地进度:', error);
                lastServerSyncError = error;
                rememberDirtyServerState(loadState());
                authoritativeReady = true;
                return loadState();
            }
        })();
        return readyPromise;
    }

    function isReady() {
        return authoritativeReady;
    }

    async function flush() {
        const initialization = readyPromise || ready();
        await initialization;
        await serverWriteQueue;
        if (dirtyServerState && canUseServerState()) {
            enqueueServerWrite(dirtyServerState);
            await serverWriteQueue;
        }
        if (dirtyServerState) {
            throw lastServerSyncError || new Error('seven-day tutorial state is not synchronized');
        }
        return loadState();
    }

    function isRoundSettled(state, roundValue) {
        const round = normalizeRound(roundValue);
        if (!round || !state) {
            return false;
        }
        return normalizeRoundList(state.completedRounds).includes(round)
            || normalizeRoundList(state.skippedRounds).includes(round);
    }

    function getNextAutoRound(stateValue, todayValue) {
        const state = normalizeState(stateValue, {
            storage: null,
            today: todayValue || getTodayLocalDate(),
        });
        const today = todayValue || getTodayLocalDate();
        if (state.manualResetRound) {
            return state.manualResetRound;
        }
        if (state.lastAutoShownDate === today) {
            return null;
        }
        if (!isRoundSettled(state, 1)) {
            return 1;
        }

        const maxDueRound = Math.min(
            ROUND_COUNT,
            getCalendarDayDelta(state.firstSeenDate, today) + 1
        );
        for (let round = 2; round <= maxDueRound; round += 1) {
            if (!isRoundSettled(state, round)) {
                return round;
            }
        }
        return null;
    }

    function markRoundOutcome(roundValue, outcome, options) {
        const round = requireRound(roundValue);
        const normalizedOutcome = outcome === 'skip' ? 'skip' : 'complete';
        const state = loadState(options);
        state.currentRound = null;
        if (state.pendingRound === round) state.pendingRound = null;
        if (state.manualResetRound === round) state.manualResetRound = null;
        if (normalizedOutcome === 'complete') {
            state.completedRounds = normalizeRoundList(state.completedRounds.concat(round));
            state.skippedRounds = omitRound(state.skippedRounds, round);
        } else {
            state.skippedRounds = normalizeRoundList(state.skippedRounds.concat(round));
            state.completedRounds = omitRound(state.completedRounds, round);
        }
        state.updatedAt = new Date().toISOString();
        saveState(state, options);
        return state;
    }

    function markDay1Completed(options) {
        return markRoundOutcome(1, 'complete', options);
    }

    function resetRound(roundValue, options) {
        const round = requireRound(roundValue);
        const state = loadState(options);
        const resetAt = new Date().toISOString();
        state.completedRounds = omitRound(state.completedRounds, round);
        state.skippedRounds = omitRound(state.skippedRounds, round);
        if (state.currentRound === round) state.currentRound = null;
        if (state.lastAutoShownRound === round) {
            state.lastAutoShownRound = null;
            state.lastAutoShownDate = '';
            state.lastAutoReservationId = '';
        }
        if (state.lastEndState && Number(state.lastEndState.day) === round) {
            state.lastEndState = null;
        }
        state.pendingRound = round;
        state.manualResetRound = round;
        if (round === 1) {
            clearLegacyHomeTutorialCompletion(options);
        }
        state.updatedAt = resetAt;
        state.resetHistory = state.resetHistory.concat([{
            day: round,
            source: options && options.source ? options.source : 'home_reset_button',
            resetAt,
        }]).slice(-RESET_HISTORY_LIMIT);
        saveState(state, options);
        return state;
    }

    function resetAll(options) {
        const state = loadState(options);
        const resetAt = new Date().toISOString();
        state.firstSeenDate = resolveToday(options);
        state.completedRounds = [];
        state.skippedRounds = [];
        state.currentRound = null;
        state.pendingRound = 1;
        state.manualResetRound = 1;
        state.lastAutoShownRound = null;
        state.lastAutoShownDate = '';
        state.lastAutoReservationId = '';
        state.lastEndState = null;
        clearLegacyHomeTutorialCompletion(options);
        state.updatedAt = resetAt;
        state.resetHistory = state.resetHistory.concat([{
            day: 'all',
            source: options && options.source ? options.source : 'all_tutorial_reset',
            resetAt,
        }]).slice(-RESET_HISTORY_LIMIT);
        saveState(state, options);
        return state;
    }

    function markAutoStartReservation(roundValue, options) {
        const round = requireRound(roundValue);
        const state = loadState(options);
        state.lastAutoShownRound = round;
        state.lastAutoShownDate = resolveToday(options);
        state.lastAutoReservationId = options && typeof options.reservationId === 'string'
            ? options.reservationId
            : '';
        state.updatedAt = new Date().toISOString();
        saveState(state, options);
        return state;
    }

    function rollbackAutoStartReservation(roundValue, options) {
        const round = requireRound(roundValue);
        const state = loadState(options);
        const today = resolveToday(options);
        const reservationId = options && typeof options.reservationId === 'string'
            ? options.reservationId
            : '';
        if (
            state.lastAutoShownRound !== round
            || state.lastAutoShownDate !== today
            || (reservationId && state.lastAutoReservationId !== reservationId)
            || isRoundSettled(state, round)
        ) {
            return false;
        }
        state.lastAutoShownRound = null;
        state.lastAutoShownDate = '';
        state.lastAutoReservationId = '';
        state.updatedAt = new Date().toISOString();
        saveState(state, options);
        return true;
    }

    if (canUseServerState()) {
        host.__nekoSevenDayTutorialStateReady = ready();
        void host.__nekoSevenDayTutorialStateReady;
    }

    return Object.freeze({
        STORAGE_KEY,
        SERVER_STATE_ENDPOINT,
        LEGACY_HOME_TUTORIAL_KEYS,
        ROUND_COUNT,
        getTodayLocalDate,
        getCalendarDayDelta,
        normalizeRound,
        normalizeRoundList,
        normalizeState,
        clearLegacyHomeTutorialCompletion,
        ready,
        isReady,
        flush,
        loadState,
        saveState,
        isRoundSettled,
        getNextAutoRound,
        markRoundOutcome,
        markDay1Completed,
        resetRound,
        resetAll,
        markAutoStartReservation,
        rollbackAutoStartReservation,
    });
});
