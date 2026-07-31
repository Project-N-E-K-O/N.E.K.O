/**
 * Binds the Electron desktop-window sensing service to the existing CAT1
 * lifecycle. The page remains a consumer: it does not read windows, schedule
 * checks, retain a second target, or produce Cat Mind actions.
 */
(function () {
    'use strict';

    const CAT_ACTIVE_EVENT = 'neko:cat-local-active-change';
    const CAT_TIER_EVENT = 'neko:auto-goodbye:state-change';
    const CAT1_TIER = 'cat1';
    const FALLBACK_OBSERVATION_EVENT = 'neko:cat-mind:observation';
    const FALLBACK_OBSERVATION_TYPE = 'desktop_occlusion_or_layer_change';
    const VALID_CHANGES = new Set(['identity', 'position', 'size']);

    let catAppearanceActive = false;
    let cat1Active = false;
    let disposed = false;
    let generation = 0;
    let startPending = false;
    let sessionId = '';
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
        if (!cat1Active || disposed || !value || typeof value !== 'object') {
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
        if (!cat1Active
            && !startPending
            && !sessionId
            && unsubscribeChanged === null) {
            return;
        }
        cat1Active = false;
        generation += 1;
        startPending = false;
        removeChangedSubscription();
        const activeSessionId = sessionId;
        sessionId = '';
        const bridge = getBridge();
        if (!bridge || !activeSessionId) return;
        try {
            Promise.resolve(bridge.stop(activeSessionId)).catch(() => {});
        } catch (_) {}
    }

    async function startSession() {
        if (disposed || !cat1Active || startPending || sessionId) return;
        const bridge = getBridge();
        if (!bridge) return;
        const expectedGeneration = generation;
        startPending = true;
        removeChangedSubscription();
        try {
            unsubscribeChanged = bridge.onChanged((value) => {
                const changedSessionId = readSessionId(value && value.sessionId);
                if (!cat1Active
                    || disposed
                    || expectedGeneration !== generation
                    || !sessionId
                    || changedSessionId !== sessionId) {
                    return;
                }
                publishObservation(value);
            });
            const result = await bridge.start();
            const startedSessionId = readSessionId(result && result.sessionId);
            if (disposed
                || !cat1Active
                || expectedGeneration !== generation) {
                removeChangedSubscription();
                if (startedSessionId) {
                    try {
                        await bridge.stop(startedSessionId);
                    } catch (_) {}
                }
                return;
            }
            sessionId = startedSessionId;
            publishObservation(result);
            if (!sessionId) {
                removeChangedSubscription();
            }
        } catch (_) {
            if (expectedGeneration === generation) {
                removeChangedSubscription();
            }
        } finally {
            if (expectedGeneration === generation) {
                startPending = false;
            }
        }
    }

    function syncCat1Session(tier) {
        const shouldRun = catAppearanceActive && readTier(tier) === CAT1_TIER;
        if (!shouldRun) {
            stopSession();
            return;
        }
        if (!cat1Active) {
            cat1Active = true;
            generation += 1;
        }
        startSession();
    }

    function handleCatAppearanceChange(event) {
        const detail = event && event.detail && typeof event.detail === 'object'
            ? event.detail
            : {};
        catAppearanceActive = detail.active === true
            && detail.appearance === 'cat';
        syncCat1Session(detail.tier);
    }

    function handleCatTierChange(event) {
        const detail = event && event.detail && typeof event.detail === 'object'
            ? event.detail
            : {};
        if (detail.type !== 'visual-tier') return;
        syncCat1Session(detail.tier);
    }

    function dispose() {
        if (disposed) return;
        catAppearanceActive = false;
        stopSession();
        disposed = true;
        window.removeEventListener(CAT_ACTIVE_EVENT, handleCatAppearanceChange);
        window.removeEventListener(CAT_TIER_EVENT, handleCatTierChange);
        window.removeEventListener('pagehide', dispose);
        window.removeEventListener('beforeunload', dispose);
    }

    window.addEventListener(CAT_ACTIVE_EVENT, handleCatAppearanceChange);
    window.addEventListener(CAT_TIER_EVENT, handleCatTierChange);
    window.addEventListener('pagehide', dispose);
    window.addEventListener('beforeunload', dispose);
})();
