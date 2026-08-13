/**
 * Desktop-only CAT1 presentation: walk to the already-sensed native window
 * top edge and perch there. This runner owns no sensing session and shares no
 * compact/chat journey state.
 */
(function () {
    'use strict';

    const coordinator = window.NekoDesktopWindowInteractions;
    if (!coordinator
        || typeof coordinator.register !== 'function'
        || typeof coordinator.completePresentation !== 'function') {
        return;
    }

    const TARGET_KIND = 'desktop-window-top-edge';
    const TRIGGER_DISTANCE_PX = 200;
    const SIDE_PADDING_PX = 12;
    const TOP_EDGE_OVERLAP_PX = 28;
    const WALK_FINISH_DISTANCE_PX = 14;
    const WALK_SPEED_PX_PER_SEC = 82;
    const WALK_MIN_STEP_MS = 12;
    const WALK_MAX_STEP_MS = 48;
    const DROP_DISTANCE_PX = 52;
    const DROP_DURATION_MS = 360;
    const DROP_COOLDOWN_MS = 30000;
    const MANUAL_MOVE_EVENT = 'neko:return-ball-manual-move';
    const PLAYGROUND_EVENT = 'neko:idle-cat1-playground-state';
    const ACTIVE_CLASS = 'is-cat1-desktop-window-top-edge-active';
    const WALKING_CLASS = 'is-cat1-desktop-window-top-edge-walking';
    const FACING_RIGHT_CLASS = 'is-cat1-desktop-window-top-edge-facing-right';
    const DROPPING_CLASS = 'is-cat1-desktop-window-top-edge-dropping';
    const TARGET_OPPORTUNITY_NONE = 'none';
    const TARGET_OPPORTUNITY_READY = 'ready';
    const TARGET_OPPORTUNITY_CONSUMED = 'consumed';
    const TARGET_OPPORTUNITY_AWAIT_NEXT = 'await-next';

    let disposed = false;
    let currentAction = null;
    const targetState = {
        sessionId: '',
        revision: 0,
        current: null,
        opportunity: TARGET_OPPORTUNITY_NONE,
    };
    let removalObserver = null;

    function normalizeRect(value) {
        if (!value || typeof value !== 'object') return null;
        const left = Number(value.left === undefined ? value.x : value.left);
        const top = Number(value.top === undefined ? value.y : value.top);
        const width = Number(value.width);
        const height = Number(value.height);
        if (!Number.isFinite(left)
            || !Number.isFinite(top)
            || !Number.isFinite(width)
            || !Number.isFinite(height)
            || width <= 0
            || height <= 0) {
            return null;
        }
        return {
            left: left,
            top: top,
            right: left + width,
            bottom: top + height,
            width: width,
            height: height,
        };
    }

    function getScreenOrigin() {
        const left = Number.isFinite(Number(window.screenX))
            ? Number(window.screenX)
            : Number(window.screenLeft) || 0;
        const top = Number.isFinite(Number(window.screenY))
            ? Number(window.screenY)
            : Number(window.screenTop) || 0;
        return { left: left, top: top };
    }

    function getLocalWindowRect(value) {
        const rect = normalizeRect(value);
        if (!rect) return null;
        const origin = getScreenOrigin();
        return normalizeRect({
            left: rect.left - origin.left,
            top: rect.top - origin.top,
            width: rect.width,
            height: rect.height,
        });
    }

    function updateTargetState(result) {
        if (!result || typeof result !== 'object') {
            targetState.current = null;
            targetState.opportunity = TARGET_OPPORTUNITY_NONE;
            return 'cleared';
        }
        const sessionId = typeof result.sessionId === 'string' ? result.sessionId : '';
        const revision = Number(result.revision);
        if (!sessionId || !Number.isFinite(revision)) return 'invalid';

        const newSession = sessionId !== targetState.sessionId;
        if (newSession) {
            targetState.sessionId = sessionId;
            targetState.revision = 0;
            targetState.current = null;
            targetState.opportunity = TARGET_OPPORTUNITY_NONE;
        } else if (revision <= targetState.revision) {
            return 'stale';
        }
        targetState.revision = revision;

        if (result.status === 'unavailable') {
            targetState.current = null;
            targetState.opportunity = TARGET_OPPORTUNITY_NONE;
            return 'unavailable';
        }
        if ((result.status !== 'current' && result.status !== 'changed')
            || !normalizeRect(result.rect)) {
            return 'invalid';
        }

        const identityChanged = !newSession
            && Array.isArray(result.changes)
            && result.changes.includes('identity');
        const hadTarget = targetState.current !== null;
        targetState.current = result;
        if (!hadTarget) {
            targetState.opportunity = TARGET_OPPORTUNITY_READY;
            return 'first-target';
        }
        if (identityChanged) {
            if (currentAction && currentAction.phase !== 'idle') {
                targetState.opportunity = TARGET_OPPORTUNITY_AWAIT_NEXT;
                return 'identity';
            }
            targetState.opportunity = TARGET_OPPORTUNITY_READY;
            return 'identity-candidate';
        }
        if (targetState.opportunity === TARGET_OPPORTUNITY_AWAIT_NEXT) {
            targetState.opportunity = TARGET_OPPORTUNITY_READY;
            return 'same-target-candidate';
        }
        return 'same-target';
    }

    function computeTarget(catValue, desktopValue) {
        const catRect = normalizeRect(catValue);
        const windowRect = getLocalWindowRect(desktopValue);
        if (!catRect || !windowRect) return null;

        const viewportWidth = Number(window.innerWidth);
        const viewportHeight = Number(window.innerHeight);
        if (!Number.isFinite(viewportWidth)
            || !Number.isFinite(viewportHeight)
            || viewportWidth <= 0
            || viewportHeight <= 0) {
            return null;
        }

        const halfWidth = catRect.width / 2;
        const safeCenterLeft = Math.max(
            windowRect.left + SIDE_PADDING_PX + halfWidth,
            halfWidth
        );
        const safeCenterRight = Math.min(
            windowRect.right - SIDE_PADDING_PX - halfWidth,
            viewportWidth - halfWidth
        );
        if (safeCenterRight < safeCenterLeft) return null;

        const targetTop = windowRect.top - catRect.height + TOP_EDGE_OVERLAP_PX;
        if (targetTop < 0 || targetTop + catRect.height > viewportHeight) return null;

        const catCenterX = catRect.left + halfWidth;
        const catCenterY = catRect.top + catRect.height / 2;
        const targetCenterX = Math.max(safeCenterLeft, Math.min(catCenterX, safeCenterRight));
        const targetCenterY = targetTop + catRect.height / 2;
        const distancePx = Math.hypot(
            targetCenterX - catCenterX,
            targetCenterY - catCenterY
        );
        return Object.freeze({
            kind: TARGET_KIND,
            left: targetCenterX - halfWidth,
            top: targetTop,
            centerX: targetCenterX,
            centerY: targetCenterY,
            distancePx: distancePx,
            facingRight: targetCenterX > catCenterX,
        });
    }

    function findVisibleCat1Button() {
        if (!document || typeof document.querySelectorAll !== 'function') return null;
        const buttons = document.querySelectorAll('.neko-idle-return-btn');
        for (let index = 0; index < buttons.length; index += 1) {
            const button = buttons[index];
            if (!button || button.isConnected === false) continue;
            if (button.getAttribute('data-neko-idle-tier') !== 'cat1') continue;
            const container = typeof _getNekoIdleReturnContainerFromButton === 'function'
                ? _getNekoIdleReturnContainerFromButton(button)
                : null;
            if (container && container.style && container.style.display !== 'none') {
                return button;
            }
        }
        return null;
    }

    function isRealCat1Appearance(button) {
        if (!button || button.getAttribute('data-neko-idle-tier') !== 'cat1') return false;
        if (typeof _getNekoGoodbyeIdleAppearance === 'function'
            && typeof _NEKO_GOODBYE_IDLE_APPEARANCE_CAT !== 'undefined'
            && _getNekoGoodbyeIdleAppearance() !== _NEKO_GOODBYE_IDLE_APPEARANCE_CAT) {
            return false;
        }
        return true;
    }

    function canStartForButton(button) {
        if (!isRealCat1Appearance(button)) return false;
        const container = typeof _getNekoIdleReturnContainerFromButton === 'function'
            ? _getNekoIdleReturnContainerFromButton(button)
            : null;
        const art = button.querySelector('.neko-idle-return-art');
        if (!container
            || !art
            || container.style.display === 'none'
            || art.__nekoIdleHoverSrc) {
            return false;
        }
        const dragging = container.getAttribute('data-dragging');
        if (dragging === 'true' || dragging === 'pending') return false;
        if (typeof _isNekoIdleCat1PlaygroundEntryOrDropActive === 'function'
            && _isNekoIdleCat1PlaygroundEntryOrDropActive(button)) {
            return false;
        }
        return typeof coordinator.canStart === 'function'
            && coordinator.canStart(button);
    }

    function setContainerPosition(container, left, top) {
        container.style.left = `${Math.round(left)}px`;
        container.style.top = `${Math.round(top)}px`;
        container.style.right = '';
        container.style.bottom = '';
        container.style.transform = 'none';
    }

    function setOwnClasses(state) {
        if (!state || !state.button || !state.container) return;
        const active = state.phase !== 'idle';
        state.button.classList.toggle(ACTIVE_CLASS, active);
        state.button.classList.toggle(WALKING_CLASS, state.phase === 'walking');
        state.button.classList.toggle(FACING_RIGHT_CLASS, active && state.facingRight);
        state.container.classList.toggle(DROPPING_CLASS, state.phase === 'dropping');
    }

    function clearOwnClasses(state) {
        if (!state || !state.button || !state.container) return;
        [ACTIVE_CLASS, WALKING_CLASS, FACING_RIGHT_CLASS].forEach((name) => {
            state.button.classList.remove(name);
        });
        state.container.classList.remove(DROPPING_CLASS);
    }

    function setArt(state, src) {
        if (!state || !state.art || !src) return;
        if (typeof _setNekoIdleReturnArtSource === 'function') {
            _setNekoIdleReturnArtSource(state.art, src, 'cat1', { animate: false });
        } else {
            state.art.src = src;
        }
    }

    function restoreIdleArt(state) {
        if (!state || !isRealCat1Appearance(state.button)) return;
        const dragging = state.container && state.container.getAttribute('data-dragging');
        if (dragging === 'true' || dragging === 'pending') return;
        const src = typeof _getNekoIdleReturnCurrentArtUrl === 'function'
            ? _getNekoIdleReturnCurrentArtUrl(state.button, 'cat1')
            : '';
        setArt(state, src);
    }

    function stopFramesAndTimer(state) {
        if (!state) return;
        if (state.frame) {
            window.cancelAnimationFrame(state.frame);
            state.frame = 0;
        }
        if (state.dropTimer) {
            window.clearTimeout(state.dropTimer);
            state.dropTimer = 0;
        }
    }

    function finishState(state, options) {
        if (!state || currentAction !== state || state.phase === 'idle') return false;
        stopFramesAndTimer(state);
        state.phase = 'idle';
        clearOwnClasses(state);
        if (!options || options.restoreArt !== false) {
            restoreIdleArt(state);
        }
        state.target = null;
        state.facingRight = false;
        currentAction = null;
        disconnectRemovalObserver();
        state.button = null;
        state.container = null;
        state.art = null;
        return true;
    }

    function cancel(button, options) {
        const state = currentAction;
        if (!state || state.phase === 'idle') return false;
        if (button && state.button !== button) return false;
        return finishState(state, options || {});
    }

    function finishPerch(state) {
        if (!state || currentAction !== state || state.phase !== 'walking') return;
        stopFramesAndTimer(state);
        setContainerPosition(state.container, state.target.left, state.target.top);
        state.phase = 'perched';
        setOwnClasses(state);
        restoreIdleArt(state);
    }

    function stepWalk(state, timestamp) {
        if (!state || currentAction !== state || state.phase !== 'walking') return;
        if (!state.button || state.button.isConnected === false || !state.container) {
            finishState(state, { reason: 'container-removed', restoreArt: false });
            return;
        }
        const rect = normalizeRect(state.container.getBoundingClientRect());
        if (!rect) {
            finishState(state, { reason: 'invalid-cat-rect' });
            return;
        }
        const dx = state.target.left - rect.left;
        const dy = state.target.top - rect.top;
        const distance = Math.hypot(dx, dy);
        if (distance <= WALK_FINISH_DISTANCE_PX) {
            finishPerch(state);
            return;
        }
        const previousAt = state.lastStepAt || timestamp;
        const elapsedMs = Math.max(
            WALK_MIN_STEP_MS,
            Math.min(Number(timestamp) - previousAt, WALK_MAX_STEP_MS)
        );
        state.lastStepAt = Number(timestamp);
        const stepDistance = WALK_SPEED_PX_PER_SEC * elapsedMs / 1000;
        const ratio = Math.min(1, stepDistance / distance);
        const nextLeft = rect.left + dx * ratio;
        const nextTop = rect.top + dy * ratio;
        setContainerPosition(state.container, nextLeft, nextTop);
        state.frame = window.requestAnimationFrame((nextTimestamp) => {
            state.frame = 0;
            stepWalk(state, nextTimestamp);
        });
    }

    function getCandidate() {
        if (disposed || (currentAction && currentAction.phase !== 'idle')) return null;
        const result = targetState.current;
        if (!result
            || (result.status !== 'current' && result.status !== 'changed')
            || !result.sessionId
            || !Number.isFinite(Number(result.revision))) {
            return null;
        }
        if (targetState.opportunity !== TARGET_OPPORTUNITY_READY) {
            return null;
        }
        const button = findVisibleCat1Button();
        if (!canStartForButton(button)) return null;
        const container = _getNekoIdleReturnContainerFromButton(button);
        const catRect = normalizeRect(container.getBoundingClientRect());
        const target = computeTarget(catRect, result.rect);
        if (!target || target.distancePx > TRIGGER_DISTANCE_PX) return null;

        return Object.freeze({
            targetKind: TARGET_KIND,
            sessionId: result.sessionId,
            revision: Number(result.revision),
            distancePx: target.distancePx,
            button: button,
            container: container,
            art: button.querySelector('.neko-idle-return-art'),
            target: target,
        });
    }

    function tryStart(expectedCandidate) {
        const candidate = getCandidate();
        if (!candidate) return false;
        if (expectedCandidate
            && (expectedCandidate.targetKind !== TARGET_KIND
                || expectedCandidate.sessionId !== candidate.sessionId
                || Number(expectedCandidate.revision) !== candidate.revision)) {
            return false;
        }

        const state = {
            phase: 'walking',
            button: candidate.button,
            container: candidate.container,
            art: candidate.art,
            target: candidate.target,
            sessionId: candidate.sessionId,
            lastRevision: candidate.revision,
            facingRight: candidate.target.facingRight,
            lastStepAt: 0,
            frame: 0,
            dropTimer: 0,
        };
        currentAction = state;
        bindRemovalObserver(state);
        targetState.opportunity = TARGET_OPPORTUNITY_CONSUMED;
        setOwnClasses(state);
        const walkingSrc = typeof _getNekoIdleCat1WalkingAssetUrl === 'function'
            ? _getNekoIdleCat1WalkingAssetUrl()
            : '';
        setArt(state, walkingSrc);
        const timestamp = typeof performance !== 'undefined'
            && typeof performance.now === 'function'
            ? performance.now()
            : Date.now();
        stepWalk(state, timestamp);
        return true;
    }

    function shouldReduceMotion() {
        try {
            return !!(window.matchMedia
                && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
        } catch (_) {
            return false;
        }
    }

    function startDrop(state, reason) {
        if (!state || currentAction !== state || state.phase !== 'perched') return false;
        const rect = normalizeRect(state.container.getBoundingClientRect());
        if (!rect) {
            finishState(state, { reason: 'invalid-cat-rect' });
            return false;
        }
        state.phase = 'dropping';
        setContainerPosition(state.container, rect.left, rect.top + DROP_DISTANCE_PX);
        setOwnClasses(state);
        const duration = shouldReduceMotion() ? 0 : DROP_DURATION_MS;
        state.dropTimer = window.setTimeout(() => {
            if (currentAction !== state || state.phase !== 'dropping') return;
            state.dropTimer = 0;
            coordinator.completePresentation(DROP_COOLDOWN_MS);
            finishState(state, {
                reason: reason || 'window-geometry-changed',
                restoreArt: true,
            });
        }, duration);
        return true;
    }

    function leaveChangedSupport(state, reason) {
        if (!state || currentAction !== state || state.phase === 'dropping') return;
        if (state.phase === 'walking') {
            finishState(state, { reason: reason });
        } else if (state.phase === 'perched') {
            startDrop(state, reason);
        }
    }

    function handleSensingResult(result) {
        if (disposed) return false;
        const targetTransition = updateTargetState(result);
        const state = currentAction;
        if (!state || state.phase === 'idle') {
            const eligible = targetState.opportunity === TARGET_OPPORTUNITY_READY
                && targetTransition !== 'identity'
                && targetTransition !== 'unavailable'
                && targetTransition !== 'cleared'
                && targetTransition !== 'invalid'
                && targetTransition !== 'stale';
            return eligible;
        }
        if (!result) {
            finishState(state, { reason: 'sensing-session-cleared', restoreArt: false });
            return false;
        }
        if (state.phase === 'dropping') return false;
        const revision = Number(result.revision);
        if (result.sessionId === state.sessionId
            && Number.isFinite(revision)
            && revision <= state.lastRevision) {
            return false;
        }
        state.lastRevision = Number.isFinite(revision) ? revision : state.lastRevision;
        const targetLost = result.sessionId !== state.sessionId
            || result.status === 'unavailable'
            || (Array.isArray(result.changes) && result.changes.includes('identity'));
        const geometryChanged = Array.isArray(result.changes)
            && (result.changes.includes('position') || result.changes.includes('size'));
        if (!targetLost && !geometryChanged) return false;
        const reason = result.status === 'unavailable'
            ? 'window-unavailable'
            : (targetLost ? 'window-identity-changed' : 'window-geometry-changed');
        leaveChangedSupport(state, reason);
        return false;
    }

    function consumeOpportunity(result) {
        if (!result || result.sessionId !== targetState.sessionId) return false;
        if (Number(result.revision) !== targetState.revision) return false;
        if (targetState.current === null) return false;
        targetState.opportunity = TARGET_OPPORTUNITY_CONSUMED;
        return true;
    }

    function rearmOpportunity(result) {
        if (disposed || (currentAction && currentAction.phase !== 'idle')) return false;
        if (!result || result.sessionId !== targetState.sessionId) return false;
        if (Number(result.revision) !== targetState.revision || targetState.current === null) return false;
        if (result.status !== 'current' && result.status !== 'changed') return false;
        targetState.opportunity = TARGET_OPPORTUNITY_READY;
        return true;
    }

    function getState() {
        const state = currentAction;
        if (!state || state.phase === 'idle') {
            return Object.freeze({ phase: 'idle', targetKind: TARGET_KIND });
        }
        return Object.freeze({
            phase: state.phase,
            targetKind: TARGET_KIND,
            sessionId: state.sessionId,
            revision: state.lastRevision,
            targetLeft: Math.round(state.target.left),
            targetTop: Math.round(state.target.top),
            distancePx: state.target.distancePx,
        });
    }

    function isActive(button) {
        return !!(currentAction
            && currentAction.phase !== 'idle'
            && (!button || currentAction.button === button));
    }

    function handleManualMove(event) {
        const detail = event && event.detail;
        if (!detail) return;
        if (detail.reason === 'return-ball-drag-active') {
            if (currentAction && detail.container === currentAction.container) {
                cancel(currentAction.button, {
                    reason: 'return-ball-drag-active',
                    restoreArt: false,
                });
            }
            return;
        }
    }

    function handlePlayground(event) {
        if (event && event.detail && event.detail.active === true) {
            cancel(null, { reason: 'playground-active', restoreArt: false });
        }
    }

    function disconnectRemovalObserver() {
        if (removalObserver) {
            try { removalObserver.disconnect(); } catch (_) {}
        }
        removalObserver = null;
    }

    function bindRemovalObserver(state) {
        disconnectRemovalObserver();
        const parent = state && state.container && state.container.parentNode;
        if (!parent || typeof MutationObserver !== 'function') return;
        removalObserver = new MutationObserver(() => {
            if (currentAction !== state) return;
            if ((state.button && state.button.isConnected !== false)
                && (state.container && state.container.isConnected !== false)) return;
            finishState(state, { reason: 'container-removed', restoreArt: false });
        });
        removalObserver.observe(parent, { childList: true });
    }

    let unregisterCoordinator = coordinator.register({
        kind: TARGET_KIND,
        priority: 0,
        handleSensingResult: handleSensingResult,
        getCandidate: getCandidate,
        startCandidate: tryStart,
        consumeOpportunity: consumeOpportunity,
        rearmOpportunity: rearmOpportunity,
        isActive: isActive,
        cancel: cancel,
    });

    function dispose() {
        if (disposed) return;
        disposed = true;
        cancel(null, { reason: 'page-dispose', restoreArt: false });
        if (typeof unregisterCoordinator === 'function') {
            try { unregisterCoordinator(); } catch (_) {}
        }
        unregisterCoordinator = null;
        disconnectRemovalObserver();
        window.removeEventListener(MANUAL_MOVE_EVENT, handleManualMove);
        window.removeEventListener(PLAYGROUND_EVENT, handlePlayground);
        window.removeEventListener('pagehide', dispose);
        window.removeEventListener('beforeunload', dispose);
        try {
            delete window.NekoDesktopWindowTopEdgePerch;
        } catch (_) {
            window.NekoDesktopWindowTopEdgePerch = undefined;
        }
    }

    window.NekoDesktopWindowTopEdgePerch = Object.freeze({
        tryStart: tryStart,
        cancel: cancel,
        getState: getState,
        isActive: isActive,
    });
    window.addEventListener(MANUAL_MOVE_EVENT, handleManualMove);
    window.addEventListener(PLAYGROUND_EVENT, handlePlayground);
    window.addEventListener('pagehide', dispose);
    window.addEventListener('beforeunload', dispose);
})();
