/**
 * Independent runtime resource protection for Game Mode Beta.
 * Keeps memory, embedding, messages, TTS, and Activity Signal online.
 */
(function () {
    'use strict';

    const API_ACK = '/api/game-mode-beta/resource/ack';
    const API_INTERACTION = '/api/game-mode-beta/resource/interaction';
    const API_EXIT = '/api/game-mode-beta/resource/exit';
    const DRAG_THRESHOLD_PX = 4;
    const state = {
        sessionId: null,
        petInstanceId: null,
        phase: 'idle',
        targetFps: 15,
        deepDelayMs: 90000,
        deepTimer: 0,
        deepGeneration: 0,
        compactEnabled: false,
        compactAcquired: false,
        appliedOriginDelta: { x: 0, y: 0 },
        pointer: null,
        dragSuspended: false,
        invalidationCleanup: null,
    };

    function currentManager() {
        const config = window.lanlan_config || {};
        let type = String(config.model_type || 'live2d').toLowerCase();
        if (type === 'live3d') type = String(config.live3d_sub_type || 'vrm').toLowerCase();
        if (type === 'vrm') return window.vrmManager || null;
        if (type === 'mmd') return window.mmdManager || null;
        if (type === 'pngtuber') return window.pngtuberManager || null;
        return window.live2dManager || null;
    }

    function hostState() {
        try {
            const gameModeState = window.nekoGameModeBeta && window.nekoGameModeBeta.getState
                ? window.nekoGameModeBeta.getState()
                : null;
            const contract = gameModeState && gameModeState.hostContract;
            return {
                host: window.nekoGameModeHost || null,
                contract: contract || null,
            };
        } catch (_) {
            return { host: null, contract: null };
        }
    }

    async function mutationHeaders() {
        const headers = { 'Content-Type': 'application/json' };
        try {
            const security = window.nekoLocalMutationSecurity;
            if (security && typeof security.getMutationHeaders === 'function') {
                Object.assign(headers, await security.getMutationHeaders());
            }
        } catch (_) {}
        return headers;
    }

    async function postJson(url, payload) {
        const response = await fetch(url, {
            method: 'POST',
            headers: await mutationHeaders(),
            body: JSON.stringify(payload),
        });
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.json().catch(function () { return {}; });
    }

    function targetsThisWindow(payload) {
        const ids = Array.isArray(payload && payload.pet_instance_ids)
            ? payload.pet_instance_ids
            : [];
        return !ids.length || !state.petInstanceId || ids.includes(state.petInstanceId);
    }

    function applyManagerPhase(phase) {
        const manager = currentManager();
        if (!manager || typeof manager.setGameModeResourceProtection !== 'function') return;
        try { manager.setGameModeResourceProtection(phase); } catch (error) {
            console.warn('[GameModeResource] manager phase failed:', error);
        }
    }

    function visibleScreenBounds() {
        const manager = currentManager();
        let bounds = null;
        try {
            if (manager && typeof manager.getModelScreenBounds === 'function') {
                bounds = manager.getModelScreenBounds();
            } else if (manager && manager.image && manager.image.getBoundingClientRect) {
                bounds = manager.image.getBoundingClientRect();
            }
        } catch (_) {}
        if (!bounds) return null;
        const left = Number(bounds.left ?? bounds.x);
        const top = Number(bounds.top ?? bounds.y);
        const width = Number(bounds.width);
        const height = Number(bounds.height);
        if (![left, top, width, height].every(Number.isFinite) || width <= 2 || height <= 2) return null;
        return {
            x: Math.round(Number(window.screenX || 0) + left),
            y: Math.round(Number(window.screenY || 0) + top),
            width: Math.round(width),
            height: Math.round(height),
        };
    }

    function leasePayload() {
        const bounds = visibleScreenBounds();
        if (!bounds || !state.sessionId || !state.petInstanceId) return null;
        return {
            sessionId: state.sessionId,
            petInstanceId: state.petInstanceId,
            visibleScreenBounds: bounds,
        };
    }

    function applyOriginDelta(result) {
        const next = result && result.originDelta
            ? result.originDelta
            : null;
        if (!next) return;
        const x = Number(next.x);
        const y = Number(next.y);
        if (!Number.isFinite(x) || !Number.isFinite(y)) return;
        const dx = x - state.appliedOriginDelta.x;
        const dy = y - state.appliedOriginDelta.y;
        state.appliedOriginDelta = { x, y };
        if (!dx && !dy) return;
        const manager = currentManager();
        if (manager && typeof manager.translateModelByScreenPixels === 'function') {
            manager.translateModelByScreenPixels(dx, dy);
        }
    }

    async function acknowledge(phase, compactLease, error) {
        if (!state.sessionId || !state.petInstanceId) return;
        try {
            await postJson(API_ACK, {
                resource_session_id: state.sessionId,
                pet_instance_id: state.petInstanceId,
                phase,
                compact_lease: compactLease,
                error: error ? String(error).slice(0, 160) : null,
            });
        } catch (ackError) {
            console.warn('[GameModeResource] phase ACK failed:', ackError);
        }
    }

    async function acquireCompactLease(methodName) {
        const pair = hostState();
        const supported = pair.contract && pair.contract.hostCapabilities
            && pair.contract.hostCapabilities.compactPetWindowLeaseV1 === true;
        if (!state.compactEnabled || !supported || !pair.host) {
            await acknowledge(state.phase, supported ? 'disabled' : 'unsupported');
            return false;
        }
        const payload = leasePayload();
        if (!payload || typeof pair.host[methodName] !== 'function') return false;
        try {
            const result = await pair.host[methodName](payload);
            applyOriginDelta(result);
            state.compactAcquired = !!(result && result.ok);
            await acknowledge(state.phase, state.compactAcquired ? 'acquired' : 'rejected', result && result.code);
            return state.compactAcquired;
        } catch (error) {
            await acknowledge(state.phase, 'failed', error);
            return false;
        }
    }

    function scheduleDeepSleep() {
        if (state.deepTimer) clearTimeout(state.deepTimer);
        const generation = ++state.deepGeneration;
        state.deepTimer = setTimeout(function () {
            if (generation !== state.deepGeneration || state.phase !== 'soft_protected') return;
            state.deepTimer = 0;
            state.phase = 'deep_sleep';
            if (document.body) document.body.classList.add('neko-game-resource-deep');
            applyManagerPhase('deep_sleep');
            void acknowledge('deep_sleep', state.compactAcquired ? 'acquired' : 'unsupported');
        }, state.deepDelayMs);
    }

    function wakeForInteraction(interaction, reportInteraction) {
        if (!state.sessionId) return;
        if (state.phase === 'deep_sleep') {
            state.phase = 'soft_protected';
            if (document.body) document.body.classList.remove('neko-game-resource-deep');
            applyManagerPhase('soft_protected');
        }
        scheduleDeepSleep();
        if (reportInteraction !== false && state.petInstanceId) {
            void postJson(API_INTERACTION, {
                resource_session_id: state.sessionId,
                pet_instance_id: state.petInstanceId,
                interaction,
            }).catch(function () {});
        }
    }

    async function releaseCompactLease() {
        const pair = hostState();
        if (!pair.host || typeof pair.host.releaseCompactLease !== 'function') return;
        const payload = leasePayload() || {
            sessionId: state.sessionId,
            petInstanceId: state.petInstanceId,
        };
        try {
            const result = await pair.host.releaseCompactLease(payload);
            applyOriginDelta(result);
        } catch (_) {}
        state.compactAcquired = false;
    }

    async function exitCurrentSession() {
        if (!state.sessionId) return false;
        const exitingSessionId = state.sessionId;
        try {
            await postJson(API_EXIT, {
                resource_session_id: exitingSessionId,
                reason: 'user-exit',
            });
            if (state.sessionId === exitingSessionId) {
                await restore({
                    resource_session_id: exitingSessionId,
                    pet_instance_ids: state.petInstanceId ? [state.petInstanceId] : [],
                });
            }
            return true;
        } catch (error) {
            console.warn('[GameModeResource] explicit exit failed:', error);
            return false;
        }
    }

    async function enter(payload) {
        if (!payload || payload.source !== 'game_mode_resource_protection') return;
        if (!payload.resource_session_id || state.sessionId === payload.resource_session_id) return;
        const pair = hostState();
        state.sessionId = payload.resource_session_id;
        state.petInstanceId = pair.contract && pair.contract.petInstanceId || null;
        if (!targetsThisWindow(payload)) {
            state.sessionId = null;
            return;
        }
        state.phase = 'soft_protected';
        state.targetFps = Math.max(1, Math.min(15, Number(payload.target_fps) || 15));
        state.deepDelayMs = Math.max(1000, (Number(payload.deep_sleep_after_seconds) || 90) * 1000);
        state.compactEnabled = payload.compact_pet_window_enabled === true;
        state.appliedOriginDelta = { x: 0, y: 0 };
        applyManagerPhase('soft_protected');
        scheduleDeepSleep();
        await acquireCompactLease('acquireCompactLease');
    }

    async function restore(payload) {
        if (!payload || payload.resource_session_id !== state.sessionId || !targetsThisWindow(payload)) return;
        ++state.deepGeneration;
        if (state.deepTimer) clearTimeout(state.deepTimer);
        state.deepTimer = 0;
        await releaseCompactLease();
        state.phase = 'idle';
        if (document.body) document.body.classList.remove('neko-game-resource-deep');
        applyManagerPhase('idle');
        state.sessionId = null;
        state.petInstanceId = null;
        state.pointer = null;
        state.dragSuspended = false;
        state.appliedOriginDelta = { x: 0, y: 0 };
    }

    function handleMessage(payload) {
        if (!payload || payload.source !== 'game_mode_resource_protection') return;
        if (payload.type === 'game_mode_resource_protection_enter') void enter(payload);
        if (payload.type === 'game_mode_resource_protection_restore') void restore(payload);
    }

    function bindInteractions() {
        window.addEventListener('pointerdown', function (event) {
            if (!state.sessionId) return;
            state.pointer = { x: Number(event.clientX) || 0, y: Number(event.clientY) || 0 };
            state.dragSuspended = false;
            wakeForInteraction('explicit-wake', false);
        }, { passive: true });
        window.addEventListener('pointermove', function (event) {
            if (!state.pointer || state.dragSuspended || !state.compactAcquired) return;
            const distance = Math.hypot(
                (Number(event.clientX) || 0) - state.pointer.x,
                (Number(event.clientY) || 0) - state.pointer.y,
            );
            if (distance <= DRAG_THRESHOLD_PX) return;
            state.dragSuspended = true;
            wakeForInteraction('drag-start');
            const pair = hostState();
            const payload = leasePayload();
            if (pair.host && payload && typeof pair.host.suspendCompactLeaseForDrag === 'function') {
                void pair.host.suspendCompactLeaseForDrag(payload).then(applyOriginDelta).catch(function () {});
            }
        }, { passive: true });
        window.addEventListener('pointerup', function () {
            if (!state.pointer) return;
            const dragged = state.dragSuspended;
            state.pointer = null;
            if (!dragged) {
                wakeForInteraction('click');
                return;
            }
            state.dragSuspended = false;
            wakeForInteraction('drag-end');
            void acquireCompactLease('resumeCompactLeaseAfterDrag');
        }, { passive: true });
        window.addEventListener('keydown', function () { wakeForInteraction('explicit-wake'); }, { passive: true });
    }

    function bindHostInvalidation() {
        const pair = hostState();
        if (!pair.host || typeof pair.host.onCompactLeaseInvalidated !== 'function') return;
        state.invalidationCleanup = pair.host.onCompactLeaseInvalidated(function (payload) {
            if (!state.sessionId || (payload && payload.sessionId && payload.sessionId !== state.sessionId)) return;
            applyOriginDelta({ ok: true, originDelta: { x: 0, y: 0 } });
            state.compactAcquired = false;
            void acquireCompactLease('resumeCompactLeaseAfterDrag');
        });
    }

    function reapplyAfterModelChange() {
        if (!state.sessionId) return;
        applyManagerPhase(state.phase);
        if (state.compactEnabled) {
            void acquireCompactLease(state.compactAcquired
                ? 'updateCompactLease'
                : 'acquireCompactLease');
        }
    }

    window.addEventListener('neko:game-mode-beta-message', function (event) {
        handleMessage(event && event.detail);
    });
    window.addEventListener('neko:game-mode-resource-registration', function (event) {
        const registration = event && event.detail;
        if (!registration || registration.resource_session_active !== true) return;
        if (state.sessionId === registration.resource_session_id) {
            state.petInstanceId = registration.pet_instance_id || state.petInstanceId;
            state.compactEnabled = registration.compact_pet_window_enabled === true;
            reapplyAfterModelChange();
            return;
        }
        void enter({
            type: 'game_mode_resource_protection_enter',
            source: 'game_mode_resource_protection',
            resource_session_id: registration.resource_session_id,
            pet_instance_ids: registration.pet_instance_id ? [registration.pet_instance_id] : [],
            target_fps: registration.resource_target_fps,
            deep_sleep_after_seconds: registration.resource_deep_sleep_after_seconds,
            compact_pet_window_enabled: registration.compact_pet_window_enabled,
        });
    });
    [
        'live2d-model-loaded',
        'live2d-floating-buttons-ready',
        'vrm-model-loaded',
        'mmd-model-loaded',
        'pngtuber-model-loaded',
    ].forEach(function (eventName) {
        window.addEventListener(eventName, reapplyAfterModelChange);
    });
    window.addEventListener('neko:live2d-game-mode-edge-peek-changed', function () {
        if (state.sessionId && state.compactAcquired) {
            void acquireCompactLease('updateCompactLease');
        }
    });
    bindInteractions();
    bindHostInvalidation();

    window.nekoGameModeResourceRuntime = Object.freeze({
        getState: function () { return Object.assign({}, state); },
        handleMessage,
        exitCurrentSession,
    });
})();
