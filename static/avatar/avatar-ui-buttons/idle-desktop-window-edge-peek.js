/**
 * Desktop-only CAT1 presentation: walk to a sensed native window's left,
 * right, or bottom edge and appear to peek from behind it. This runner owns
 * no sensing session and shares no state with the screen-edge peek feature.
 */
(function () {
    'use strict';

    const coordinator = window.NekoDesktopWindowInteractions;
    if (!coordinator
        || typeof coordinator.register !== 'function'
        || typeof coordinator.completePresentation !== 'function') {
        return;
    }

    const TARGET_KIND = 'desktop-window-edge-peek';
    const TRIGGER_DISTANCE_PX = 200;
    const EDGE_PADDING_PX = 12;
    const SIDE_VISIBLE_RATIO = 0.5;
    const SIDE_HIDDEN_RATIO = 1 - SIDE_VISIBLE_RATIO;
    const BOTTOM_HIDDEN_RATIO = 0.56;
    const SIDE_ROTATION_DEG = 60;
    const WALK_FINISH_DISTANCE_PX = 14;
    const WALK_SPEED_PX_PER_SEC = 82;
    const WALK_MIN_STEP_MS = 12;
    const WALK_MAX_STEP_MS = 48;
    const LEAVE_DISTANCE_PX = 52;
    const LEAVE_DURATION_MS = 360;
    const LEAVE_COOLDOWN_MS = 30000;
    const CUE_DURATION_MS = 700;
    const PEEK_CYCLE_DELAY_MIN_MS = 6000;
    const PEEK_CYCLE_DELAY_MAX_MS = 12000;
    const PEEK_CYCLE_DURATION_MIN_MS = 700;
    const PEEK_CYCLE_DURATION_MAX_MS = 1000;
    const MANUAL_MOVE_EVENT = 'neko:return-ball-manual-move';
    const PLAYGROUND_EVENT = 'neko:idle-cat1-playground-state';
    const WALKING_CLASS = 'is-cat1-desktop-window-edge-peek-walking';
    const FACING_RIGHT_CLASS = 'is-cat1-desktop-window-edge-peek-facing-right';
    const PEEKING_CLASS = 'is-cat1-desktop-window-edge-peeking';
    const LEAVING_CLASS = 'is-cat1-desktop-window-edge-peek-leaving';
    const CUE_CLASS = 'is-cat1-desktop-window-edge-peek-cue-active';
    const PEEK_CYCLE_CLASS = 'is-cat1-desktop-window-edge-peek-cycle-active';
    const PEEK_CYCLE_DURATION_PROPERTY = '--neko-desktop-window-edge-peek-cycle-duration';
    const EDGE_CLASS_PREFIX = 'is-cat1-desktop-window-edge-peek-';
    const EDGES = Object.freeze(['left', 'right', 'bottom']);
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

    function clamp(value, minValue, maxValue) {
        if (!Number.isFinite(value)
            || !Number.isFinite(minValue)
            || !Number.isFinite(maxValue)
            || maxValue < minValue) {
            return null;
        }
        return Math.max(minValue, Math.min(value, maxValue));
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

    function getRotationMargin(width, height, degrees) {
        const radians = Math.abs(Number(degrees) || 0) * Math.PI / 180;
        const rotatedWidth = Math.abs(width * Math.cos(radians)) + Math.abs(height * Math.sin(radians));
        const rotatedHeight = Math.abs(width * Math.sin(radians)) + Math.abs(height * Math.cos(radians));
        return {
            x: Math.max(0, (rotatedWidth - width) / 2),
            y: Math.max(0, (rotatedHeight - height) / 2),
        };
    }

    function buildCandidate(edge, catRect, windowRect, viewportWidth, viewportHeight) {
        const width = catRect.width;
        const height = catRect.height;
        let left = 0;
        let top = 0;

        if (edge === 'left' || edge === 'right') {
            const margin = getRotationMargin(width, height, SIDE_ROTATION_DEG);
            const minTop = Math.max(windowRect.top + EDGE_PADDING_PX, margin.y);
            const maxTop = Math.min(
                windowRect.bottom - EDGE_PADDING_PX - height,
                viewportHeight - height - margin.y
            );
            top = clamp(catRect.top, minTop, maxTop);
            if (top === null) return null;
            left = edge === 'left'
                ? windowRect.left - width * SIDE_VISIBLE_RATIO
                : windowRect.right - width * SIDE_HIDDEN_RATIO;
            if (left - margin.x < 0 || left + width + margin.x > viewportWidth) return null;
        } else if (edge === 'bottom') {
            const minLeft = Math.max(windowRect.left + EDGE_PADDING_PX, 0);
            const maxLeft = Math.min(
                windowRect.right - EDGE_PADDING_PX - width,
                viewportWidth - width
            );
            left = clamp(catRect.left, minLeft, maxLeft);
            if (left === null) return null;
            top = windowRect.bottom - height * BOTTOM_HIDDEN_RATIO;
            if (top < 0 || top + height > viewportHeight) return null;
        } else {
            return null;
        }

        const centerX = left + width / 2;
        const centerY = top + height / 2;
        const catCenterX = catRect.left + width / 2;
        const catCenterY = catRect.top + height / 2;
        return Object.freeze({
            kind: TARGET_KIND,
            edge: edge,
            left: left,
            top: top,
            centerX: centerX,
            centerY: centerY,
            distancePx: Math.hypot(centerX - catCenterX, centerY - catCenterY),
            facingRight: centerX > catCenterX,
        });
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

        const candidates = EDGES.map((edge) => (
            buildCandidate(edge, catRect, windowRect, viewportWidth, viewportHeight)
        )).filter(Boolean);
        candidates.sort((left, right) => {
            const distanceDelta = left.distancePx - right.distancePx;
            if (Math.abs(distanceDelta) > 0.000001) return distanceDelta;
            return EDGES.indexOf(left.edge) - EDGES.indexOf(right.edge);
        });
        return candidates[0] || null;
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
            if (container && container.style && container.style.display !== 'none') return button;
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
        if (!container || !art || container.style.display === 'none' || art.__nekoIdleHoverSrc) return false;
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

    function randomDuration(minValue, maxValue) {
        const randomValue = Number(Math.random());
        const ratio = Number.isFinite(randomValue)
            ? Math.max(0, Math.min(randomValue, 0.999999))
            : 0.5;
        return Math.round(minValue + (maxValue - minValue) * ratio);
    }

    function resetPeekCycleVisual(state) {
        if (!state || !state.container) return;
        state.container.classList.remove(PEEK_CYCLE_CLASS);
        if (state.container.style
            && typeof state.container.style.removeProperty === 'function') {
            state.container.style.removeProperty(PEEK_CYCLE_DURATION_PROPERTY);
        }
    }

    function clearPeekCycle(state) {
        if (!state) return;
        if (state.peekCycleTimer) {
            window.clearTimeout(state.peekCycleTimer);
            state.peekCycleTimer = 0;
        }
        resetPeekCycleVisual(state);
    }

    function schedulePeekCycle(state) {
        if (!state
            || currentAction !== state
            || state.phase !== 'peeking'
            || shouldReduceMotion()) {
            return;
        }
        clearPeekCycle(state);
        const delay = randomDuration(PEEK_CYCLE_DELAY_MIN_MS, PEEK_CYCLE_DELAY_MAX_MS);
        state.peekCycleTimer = window.setTimeout(() => {
            if (currentAction !== state || state.phase !== 'peeking') return;
            state.peekCycleTimer = 0;
            const duration = randomDuration(
                PEEK_CYCLE_DURATION_MIN_MS,
                PEEK_CYCLE_DURATION_MAX_MS
            );
            if (state.container.style
                && typeof state.container.style.setProperty === 'function') {
                state.container.style.setProperty(
                    PEEK_CYCLE_DURATION_PROPERTY,
                    `${duration}ms`
                );
            }
            state.container.classList.add(PEEK_CYCLE_CLASS);
            state.peekCycleTimer = window.setTimeout(() => {
                if (currentAction !== state || state.phase !== 'peeking') return;
                state.peekCycleTimer = 0;
                resetPeekCycleVisual(state);
                schedulePeekCycle(state);
            }, duration);
        }, delay);
    }

    function clearCue(state) {
        if (!state) return;
        if (state.cueTimer) {
            window.clearTimeout(state.cueTimer);
            state.cueTimer = 0;
        }
        if (state.container) state.container.classList.remove(CUE_CLASS);
    }

    function clearOwnClasses(state) {
        if (!state || !state.button || !state.container) return;
        state.button.classList.remove(WALKING_CLASS, FACING_RIGHT_CLASS);
        state.container.classList.remove(
            PEEKING_CLASS,
            LEAVING_CLASS,
            CUE_CLASS,
            PEEK_CYCLE_CLASS
        );
        EDGES.forEach((edge) => {
            const edgeClass = `${EDGE_CLASS_PREFIX}${edge}`;
            state.button.classList.remove(edgeClass);
            state.container.classList.remove(edgeClass);
        });
    }

    function setWalkingClasses(state) {
        clearOwnClasses(state);
        state.button.classList.add(WALKING_CLASS);
        state.button.classList.toggle(FACING_RIGHT_CLASS, state.facingRight);
    }

    function setPeekingClasses(state) {
        clearOwnClasses(state);
        const edgeClass = `${EDGE_CLASS_PREFIX}${state.target.edge}`;
        state.button.classList.add(edgeClass);
        state.container.classList.add(PEEKING_CLASS, edgeClass, CUE_CLASS);
        state.cueTimer = window.setTimeout(() => {
            if (currentAction !== state || state.phase !== 'peeking') return;
            state.cueTimer = 0;
            state.container.classList.remove(CUE_CLASS);
            schedulePeekCycle(state);
        }, CUE_DURATION_MS);
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

    function stopFramesAndTimers(state) {
        if (!state) return;
        if (state.frame) {
            window.cancelAnimationFrame(state.frame);
            state.frame = 0;
        }
        if (state.leaveTimer) {
            window.clearTimeout(state.leaveTimer);
            state.leaveTimer = 0;
        }
        clearCue(state);
        clearPeekCycle(state);
    }

    function disconnectRemovalObserver() {
        if (removalObserver) {
            try { removalObserver.disconnect(); } catch (_) {}
        }
        removalObserver = null;
    }

    function finishState(state, options) {
        if (!state || currentAction !== state || state.phase === 'idle') return false;
        stopFramesAndTimers(state);
        state.phase = 'idle';
        clearOwnClasses(state);
        if (!options || options.restoreArt !== false) restoreIdleArt(state);
        state.target = null;
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

    function finishPeekArrival(state) {
        if (!state || currentAction !== state || state.phase !== 'walking') return;
        if (state.frame) {
            window.cancelAnimationFrame(state.frame);
            state.frame = 0;
        }
        setContainerPosition(state.container, state.target.left, state.target.top);
        state.phase = 'peeking';
        setPeekingClasses(state);
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
            finishPeekArrival(state);
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
            || !Number.isFinite(Number(result.revision))
            || targetState.opportunity !== TARGET_OPPORTUNITY_READY) {
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
            leaveTimer: 0,
            cueTimer: 0,
            peekCycleTimer: 0,
        };
        currentAction = state;
        bindRemovalObserver(state);
        targetState.opportunity = TARGET_OPPORTUNITY_CONSUMED;
        setWalkingClasses(state);
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

    function startLeave(state, reason) {
        if (!state || currentAction !== state || state.phase !== 'peeking') return false;
        clearPeekCycle(state);
        const rect = normalizeRect(state.container.getBoundingClientRect());
        if (!rect) {
            finishState(state, { reason: 'invalid-cat-rect' });
            return false;
        }
        clearCue(state);
        clearOwnClasses(state);
        state.phase = 'leaving';
        setContainerPosition(state.container, rect.left, rect.top + LEAVE_DISTANCE_PX);
        state.container.classList.add(LEAVING_CLASS);
        const duration = shouldReduceMotion() ? 0 : LEAVE_DURATION_MS;
        state.leaveTimer = window.setTimeout(() => {
            if (currentAction !== state || state.phase !== 'leaving') return;
            state.leaveTimer = 0;
            coordinator.completePresentation(LEAVE_COOLDOWN_MS);
            finishState(state, {
                reason: reason || 'window-geometry-changed',
                restoreArt: true,
            });
        }, duration);
        return true;
    }

    function leaveChangedSupport(state, reason) {
        if (!state || currentAction !== state || state.phase === 'leaving') return;
        if (state.phase === 'walking') finishState(state, { reason: reason });
        else if (state.phase === 'peeking') startLeave(state, reason);
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
        if (state.phase === 'leaving') return false;
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
        if (Number(result.revision) !== targetState.revision || targetState.current === null) return false;
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
            edge: state.target.edge,
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
        if (!detail
            || (detail.reason !== 'return-ball-drag-start'
                && detail.reason !== 'return-ball-drag-active')) {
            return;
        }
        if (currentAction && detail.container === currentAction.container) {
            cancel(currentAction.button, {
                reason: detail.reason,
                restoreArt: false,
            });
        }
    }

    function handlePlayground(event) {
        if (event && event.detail && event.detail.active === true) {
            cancel(null, { reason: 'playground-active', restoreArt: false });
        }
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
        priority: 1,
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
            delete window.NekoDesktopWindowEdgePeek;
        } catch (_) {
            window.NekoDesktopWindowEdgePeek = undefined;
        }
    }

    window.NekoDesktopWindowEdgePeek = Object.freeze({
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
