/**
 * Binds the Electron desktop-window sensing service to the visible cat
 * lifecycle. The page remains a consumer: it does not read windows, schedule
 * checks, retain a second target, or produce Cat Mind actions.
 */
(function () {
    'use strict';

    const CAT_ACTIVE_EVENT = 'neko:cat-local-active-change';
    const CAT_TIER_EVENT = 'neko:auto-goodbye:state-change';
    const PLAYGROUND_STATE_EVENT = 'neko:idle-cat1-playground-state';
    const GOODBYE_STATE_CLEARED_EVENT = 'neko:goodbye-state-cleared';
    const CAT1_TIER = 'cat1';
    const FALLBACK_OBSERVATION_EVENT = 'neko:cat-mind:observation';
    const FALLBACK_OBSERVATION_TYPE = 'desktop_occlusion_or_layer_change';
    const VALID_CHANGES = new Set(['identity', 'position', 'size']);

    let catAppearanceActive = false;
    let cat1Active = false;
    let playgroundActive = false;
    let sensingActive = false;
    let disposed = false;
    let generation = 0;
    let startPending = false;
    let sessionId = '';
    let sessionMode = '';
    let unsubscribeChanged = null;

    function getBridge() {
        const bridge = window.nekoDesktopWindowSensing;
        if (!bridge
            || typeof bridge.start !== 'function'
            || typeof bridge.stop !== 'function'
            || typeof bridge.onChanged !== 'function') {
            return null;
        }
        return bridge;
    }

    if (!getBridge()) {
        return;
    }

    let sharedRevision = 0;
    let sharedResult = null;
    let lastObservedRect = null;
    const sharedListeners = new Set();

    function readSessionId(value) {
        return typeof value === 'string' && value.length > 0 ? value : '';
    }

    function readRect(value) {
        if (!value || typeof value !== 'object') return null;
        const rect = {
            x: Number(value.x),
            y: Number(value.y),
            width: Number(value.width),
            height: Number(value.height),
        };
        if (!Number.isFinite(rect.x)
            || !Number.isFinite(rect.y)
            || !Number.isFinite(rect.width)
            || !Number.isFinite(rect.height)
            || rect.width <= 0
            || rect.height <= 0) {
            return null;
        }
        return rect;
    }

    function readMovement(value) {
        if (!value || typeof value !== 'object') return null;
        const movement = {
            x: Number(value.x),
            y: Number(value.y),
        };
        if (![-1, 0, 1].includes(movement.x)
            || ![-1, 0, 1].includes(movement.y)) {
            return null;
        }
        return movement;
    }

    function readChanges(value) {
        if (!Array.isArray(value)) return [];
        return value.filter((change, index, all) => (
            VALID_CHANGES.has(change) && all.indexOf(change) === index
        ));
    }

    function readWindowScene(value) {
        const keys = new Set();
        const windows = [];
        for (const item of Array.isArray(value) ? value.slice(0, 256) : []) {
            const rect = item && readRect(item.rect);
            if (!rect || typeof item.key !== 'string' || !/^window-\d+$/.test(item.key)
                || keys.has(item.key) || !['external', 'app'].includes(item.kind)) continue;
            keys.add(item.key);
            windows.push(Object.freeze({ key: item.key, kind: item.kind,
                ...(item.kind === 'app' && item.collisionOnly === true ? { collisionOnly: true } : {}),
                rect: Object.freeze(rect) }));
        }
        return Object.freeze(windows);
    }

    function normalizeSharedResult(value, expectedSessionId) {
        if (!value || typeof value !== 'object') return null;
        const valueSessionId = readSessionId(value.sessionId);
        if (!valueSessionId || valueSessionId !== expectedSessionId) return null;
        const status = value.status === 'ready' ? 'current' : value.status;
        const base = {
            status: status,
            sessionId: valueSessionId,
            revision: sharedRevision + 1,
        };
        if (status === 'unavailable') {
            if (typeof value.reason !== 'string' || !value.reason) return null;
            return Object.freeze({
                ...base,
                reason: value.reason,
                timestamp: Date.now(),
            });
        }
        // A gravity start may be waiting for the single-flight native read.
        // This is not an unavailable scene: dropping it here makes the
        // renderer mistake "busy" for a real no-window result and strands a
        // manual handoff until the next lifecycle restart.
        if (status === 'pending') {
            if (value.reason !== 'busy') return null;
            return Object.freeze({
                ...base,
                reason: value.reason,
                timestamp: Date.now(),
            });
        }
        if (status !== 'current' && status !== 'changed') return null;
        const rect = readRect(value.rect);
        if (!rect) return null;
        const changes = status === 'changed' ? readChanges(value.changes) : [];
        if (status === 'changed' && changes.length === 0) return null;
        const movement = status === 'changed' ? readMovement(value.movement) : null;
        if (status === 'changed' && value.movement != null && !movement) return null;
        return Object.freeze({
            ...base,
            changes: Object.freeze(changes),
            movement: movement ? Object.freeze(movement) : null,
            rect: Object.freeze(rect),
            ...(Array.isArray(value.windows) ? { windows: readWindowScene(value.windows) } : {}),
            timestamp: Date.now(),
        });
    }

    function notifySharedListeners(value) {
        Array.from(sharedListeners).forEach((listener) => {
            try {
                listener(value);
            } catch (_) {}
        });
    }

    function updateSharedResult(value, expectedSessionId) {
        const normalized = normalizeSharedResult(value, expectedSessionId);
        if (!normalized) return false;
        sharedRevision = normalized.revision;
        sharedResult = normalized;
        notifySharedListeners(sharedResult);
        return true;
    }

    function clearSharedResult() {
        if (sharedResult === null) return;
        sharedResult = null;
        notifySharedListeners(null);
    }

    const sharedContext = Object.freeze({
        getCurrent() {
            return sharedResult;
        },
        subscribe(listener) {
            if (typeof listener !== 'function' || disposed) {
                return function noop() {};
            }
            sharedListeners.add(listener);
            return function unsubscribe() {
                sharedListeners.delete(listener);
            };
        },
    });
    window.nekoDesktopWindowSensingContext = sharedContext;

    function readTier(fallbackTier) {
        if (['cat1', 'cat2', 'cat3'].includes(fallbackTier)) {
            return fallbackTier;
        }
        try {
            const catMind = window.nekoCatMind;
            const state = catMind && typeof catMind.getState === 'function'
                ? catMind.getState()
                : null;
            if (state && ['cat1', 'cat2', 'cat3'].includes(state.tier)) {
                return state.tier;
            }
        } catch (_) {}
        return '';
    }

    function publishObservation(value) {
        if (!cat1Active || playgroundActive || disposed || !value || typeof value !== 'object') {
            return false;
        }
        const status = value.status;
        const detail = { status: status === 'ready' ? 'current' : status };
        if (status === 'ready' || status === 'current' || status === 'changed') {
            const rect = readRect(value.rect);
            if (!rect) return false;
            detail.changes = status === 'changed' ? readChanges(value.changes) : [];
            detail.movement = status === 'changed' ? readMovement(value.movement) : null;
            detail.rect = rect;
        } else if (status === 'unavailable') {
            if (typeof value.reason !== 'string' || !value.reason) return false;
            detail.reason = value.reason;
        } else {
            return false;
        }

        const contract = window.NekoCatMindContract;
        const observationEvent = contract
            && contract.EVENT_NAMES
            && contract.EVENT_NAMES.OBSERVATION
            ? contract.EVENT_NAMES.OBSERVATION
            : FALLBACK_OBSERVATION_EVENT;
        const observationType = contract
            && contract.OBSERVATION_TYPES
            && contract.OBSERVATION_TYPES.DESKTOP_OCCLUSION_OR_LAYER_CHANGE
            ? contract.OBSERVATION_TYPES.DESKTOP_OCCLUSION_OR_LAYER_CHANGE
            : FALLBACK_OBSERVATION_TYPE;
        window.dispatchEvent(new CustomEvent(observationEvent, {
            detail: {
                type: observationType,
                source: 'desktop-window-sensing',
                tier: CAT1_TIER,
                timestamp: Date.now(),
                detail: detail,
            },
        }));
        return true;
    }

    function sameRect(left, right) {
        return !!(left
            && right
            && left.x === right.x
            && left.y === right.y
            && left.width === right.width
            && left.height === right.height);
    }

    sharedContext.subscribe((value) => {
        if (value === null) {
            lastObservedRect = null;
            return;
        }
        if (value.status === 'unavailable') {
            lastObservedRect = null;
            publishObservation(value);
            return;
        }
        const rect = readRect(value.rect);
        if (!rect) return;
        if (value.status === 'current' && sameRect(lastObservedRect, rect)) return;
        lastObservedRect = rect;
        publishObservation(value);
    });

    function removeChangedSubscription() {
        const cleanup = unsubscribeChanged;
        unsubscribeChanged = null;
        if (typeof cleanup === 'function') {
            try {
                cleanup();
            } catch (_) {}
        }
    }

    function stopSession() {
        if (!sensingActive
            && !startPending
            && !sessionId
            && unsubscribeChanged === null) {
            return;
        }
        sensingActive = false;
        generation += 1;
        startPending = false;
        sessionMode = '';
        clearSharedResult();
        removeChangedSubscription();
        const activeSessionId = sessionId;
        sessionId = '';
        const bridge = getBridge();
        if (!bridge || !activeSessionId) return;
        try {
            Promise.resolve(bridge.stop(activeSessionId)).catch(() => {});
        } catch (_) {}
    }

    async function startSession(mode) {
        if (disposed || !sensingActive || startPending || sessionId) return;
        const bridge = getBridge();
        if (!bridge) return;
        const expectedGeneration = generation;
        startPending = true;
        removeChangedSubscription();
        let ownUnsubscribe = null;
        const removeOwnSubscription = () => {
            const cleanup = ownUnsubscribe;
            ownUnsubscribe = null;
            if (unsubscribeChanged === cleanup) {
                unsubscribeChanged = null;
            }
            if (typeof cleanup === 'function') {
                try {
                    cleanup();
                } catch (_) {}
            }
        };
        try {
            ownUnsubscribe = bridge.onChanged((value) => {
                const changedSessionId = readSessionId(value && value.sessionId);
                if (!sensingActive
                    || disposed
                    || expectedGeneration !== generation
                    || !sessionId
                    || changedSessionId !== sessionId) {
                    return;
                }
                updateSharedResult(value, sessionId);
            });
            unsubscribeChanged = ownUnsubscribe;
            const result = await bridge.start({ mode: mode === 'gravity' ? 'gravity' : 'legacy' });
            const startedSessionId = readSessionId(result && result.sessionId);
            if (disposed
                || !sensingActive
                || expectedGeneration !== generation
                || sessionMode !== mode) {
                removeOwnSubscription();
                if (startedSessionId) {
                    try {
                        await bridge.stop(startedSessionId);
                    } catch (_) {}
                }
                return;
            }
            sessionId = startedSessionId;
            if (sessionId) {
                updateSharedResult(result, sessionId);
            } else {
                publishObservation(result);
            }
            if (!sessionId) {
                removeOwnSubscription();
            }
        } catch (_) {
            removeOwnSubscription();
        } finally {
            if (expectedGeneration === generation) {
                startPending = false;
            }
        }
    }

    function syncCatSession(tier) {
        const currentTier = readTier(tier);
        const wasCat1Active = cat1Active;
        const desiredMode = playgroundActive ? 'gravity' : 'legacy';
        const shouldRun = catAppearanceActive && currentTier === CAT1_TIER;
        cat1Active = shouldRun;
        if (!shouldRun) {
            stopSession();
            return;
        }
        // A pending start belongs to the mode that created it just as much as
        // an established session does. Invalidate it before starting the new
        // mode; otherwise a slow legacy start can become the Playground's
        // gravity session (and vice versa).
        if ((sessionId || startPending) && sessionMode !== desiredMode) {
            stopSession();
        }
        if (!sensingActive) {
            sensingActive = true;
            generation += 1;
        }
        if (desiredMode === 'legacy' && cat1Active && !wasCat1Active && sharedResult) {
            publishObservation(sharedResult);
        }
        if (!sessionId && !startPending) {
            sessionMode = desiredMode;
            startSession(desiredMode);
        }
    }

    function handleCatAppearanceChange(event) {
        const detail = event && event.detail && typeof event.detail === 'object'
            ? event.detail
            : {};
        catAppearanceActive = detail.active === true
            && detail.appearance === 'cat';
        syncCatSession(detail.tier);
    }

    function handleCatTierChange(event) {
        const detail = event && event.detail && typeof event.detail === 'object'
            ? event.detail
            : {};
        if (detail.type !== 'visual-tier') return;
        syncCatSession(detail.tier);
    }

    function handlePlaygroundState(event) {
        const detail = event && event.detail && typeof event.detail === 'object'
            ? event.detail
            : {};
        playgroundActive = detail.active === true;
        syncCatSession(detail.tier);
    }

    function handleGoodbyeStateCleared() {
        catAppearanceActive = false;
        playgroundActive = false;
        stopSession();
    }

    function dispose() {
        if (disposed) return;
        catAppearanceActive = false;
        stopSession();
        disposed = true;
        sharedListeners.clear();
        try {
            delete window.nekoDesktopWindowSensingContext;
        } catch (_) {
            window.nekoDesktopWindowSensingContext = undefined;
        }
        window.removeEventListener(CAT_ACTIVE_EVENT, handleCatAppearanceChange);
        window.removeEventListener(CAT_TIER_EVENT, handleCatTierChange);
        window.removeEventListener(PLAYGROUND_STATE_EVENT, handlePlaygroundState);
        window.removeEventListener(GOODBYE_STATE_CLEARED_EVENT, handleGoodbyeStateCleared);
        window.removeEventListener('pagehide', dispose);
        window.removeEventListener('beforeunload', dispose);
    }

    window.addEventListener(CAT_ACTIVE_EVENT, handleCatAppearanceChange);
    window.addEventListener(CAT_TIER_EVENT, handleCatTierChange);
    window.addEventListener(PLAYGROUND_STATE_EVENT, handlePlaygroundState);
    window.addEventListener(GOODBYE_STATE_CLEARED_EVENT, handleGoodbyeStateCleared);
    window.addEventListener('pagehide', dispose);
    window.addEventListener('beforeunload', dispose);
})();
