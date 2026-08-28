/**
 * Selects at most one CAT1 desktop-window presentation for each sensing fact.
 * Runners keep their own lifecycle; this layer only removes listener-order
 * races when more than one presentation can reach the same native window.
 */
(function () {
    'use strict';

    const sensingContext = window.nekoDesktopWindowSensingContext;
    if (!sensingContext
        || typeof sensingContext.getCurrent !== 'function'
        || typeof sensingContext.subscribe !== 'function') {
        return;
    }

    const REQUIRED_RUNNER_KINDS = Object.freeze([
        'desktop-window-top-edge',
        'desktop-window-edge-peek',
    ]);
    const MANUAL_MOVE_EVENT = 'neko:return-ball-manual-move';
    const MANUAL_MOVE_COOLDOWN_MS = 30000;
    const runners = new Map();
    let disposed = false;
    let replayTimer = 0;
    let presentationCooldownUntil = 0;
    let rearmOnNextFact = false;
    let manualMoveStartedFromPresentation = false;
    let lastRunnerFailure = null;

    function canStart(button) {
        if (!button
            || _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) !== _NEKO_IDLE_TIER_CAT1) {
            return false;
        }
        if (_getActiveNekoIdleReturnTier() !== _NEKO_IDLE_TIER_CAT1) return false;
        if (_isNekoIdleReturnDragActionBlocking(button) || _isAnyNekoIdleReturnDragActionBlocking()) return false;
        if (_isNekoIdleReturnPending(button) || _isAnyNekoIdleReturnPending()) return false;
        if (_isNekoIdlePresentationTransitionActive(button)) return false;
        if (_isNekoIdleCompactSurfaceDragging()) return false;
        if (_isNekoIdleCat1EdgePeekActive(button)) return false;
        if (_isAnyNekoIdleCat1IndependentActionActive()) return false;
        if (_isNekoIdleCat1PositionPresentationBusy(button)) return false;
        return true;
    }

    function reportRunnerFailure(stage, runner, error) {
        lastRunnerFailure = Object.freeze({
            stage: String(stage || ''),
            runnerKind: runner && typeof runner.kind === 'string' ? runner.kind : '',
            message: error && error.message ? String(error.message) : String(error || ''),
            timestamp: Date.now(),
        });
        try {
            const debugEnabled = window.__NEKO_DESKTOP_WINDOW_INTERACTIONS_DEBUG__ === true
                || (window.localStorage
                    && window.localStorage.getItem('nekoDesktopWindowInteractionsDebug') === '1');
            if (debugEnabled && window.console && typeof window.console.warn === 'function') {
                window.console.warn(
                    '[NekoDesktopWindowInteractions] runner failed',
                    lastRunnerFailure
                );
            }
        } catch (_) {}
    }

    function getActiveRunner(button) {
        let active = null;
        runners.forEach((runner) => {
            if (active || typeof runner.isActive !== 'function') return;
            try {
                if (runner.isActive(button)) active = runner;
            } catch (error) {
                reportRunnerFailure('isActive', runner, error);
            }
        });
        return active;
    }

    function processSensingResult(result) {
        if (disposed) return false;
        if (!REQUIRED_RUNNER_KINDS.every((kind) => runners.has(kind))) return false;
        const activeBefore = getActiveRunner(null);
        const eligibleRunners = [];
        let runnerFailed = false;
        runners.forEach((runner) => {
            if (typeof runner.handleSensingResult !== 'function') return;
            try {
                if (runner.handleSensingResult(result) === true) {
                    eligibleRunners.push(runner);
                }
            } catch (error) {
                reportRunnerFailure('handleSensingResult', runner, error);
                runnerFailed = true;
            }
        });

        // A fact that terminates one presentation must not start another one
        // during the same revision. The next sensing fact may offer again.
        if (runnerFailed || activeBefore || getActiveRunner(null)) return false;

        if (rearmOnNextFact) {
            if (Date.now() < presentationCooldownUntil) return false;
            let rearmed = false;
            runners.forEach((runner) => {
                if (typeof runner.rearmOpportunity !== 'function') return;
                try {
                    if (runner.rearmOpportunity(result) === true) {
                        rearmed = true;
                        if (!eligibleRunners.includes(runner)) eligibleRunners.push(runner);
                    }
                } catch (error) {
                    reportRunnerFailure('rearmOpportunity', runner, error);
                }
            });
            if (!rearmed) return false;
            rearmOnNextFact = false;
        }

        const candidates = [];
        let candidateFailed = false;
        eligibleRunners.forEach((runner) => {
            if (typeof runner.getCandidate !== 'function') return;
            let candidate = null;
            try {
                candidate = runner.getCandidate();
            } catch (error) {
                reportRunnerFailure('getCandidate', runner, error);
                candidateFailed = true;
            }
            if (!candidate || !Number.isFinite(Number(candidate.distancePx))) return;
            candidates.push({ runner: runner, candidate: candidate });
        });
        if (candidateFailed) return false;
        candidates.sort((left, right) => {
            const distanceDelta = Number(left.candidate.distancePx) - Number(right.candidate.distancePx);
            if (Math.abs(distanceDelta) > 0.000001) return distanceDelta;
            const priorityDelta = Number(left.runner.priority) - Number(right.runner.priority);
            if (Number.isFinite(priorityDelta) && priorityDelta !== 0) return priorityDelta;
            return String(left.runner.kind).localeCompare(String(right.runner.kind));
        });

        const selected = candidates[0];
        if (!selected || typeof selected.runner.startCandidate !== 'function') return false;
        let started = false;
        try {
            started = selected.runner.startCandidate(selected.candidate) === true;
        } catch (error) {
            reportRunnerFailure('startCandidate', selected.runner, error);
        }
        if (!started) return false;

        runners.forEach((runner) => {
            if (runner === selected.runner || typeof runner.consumeOpportunity !== 'function') return;
            try {
                runner.consumeOpportunity(result);
            } catch (error) {
                reportRunnerFailure('consumeOpportunity', runner, error);
            }
        });
        return true;
    }

    function scheduleCurrentReplay() {
        if (disposed || replayTimer) return;
        replayTimer = window.setTimeout(() => {
            replayTimer = 0;
            const current = sensingContext.getCurrent();
            if (current) processSensingResult(current);
        }, 0);
    }

    function register(runner) {
        if (disposed
            || !runner
            || typeof runner.kind !== 'string'
            || !runner.kind
            || runners.has(runner.kind)) {
            return function noop() {};
        }
        runners.set(runner.kind, runner);
        scheduleCurrentReplay();
        return function unregister() {
            if (runners.get(runner.kind) === runner) runners.delete(runner.kind);
        };
    }

    function cancel(button, options) {
        manualMoveStartedFromPresentation = false;
        let cancelled = false;
        runners.forEach((runner) => {
            if (typeof runner.cancel !== 'function') return;
            try {
                cancelled = runner.cancel(button, options || {}) === true || cancelled;
            } catch (error) {
                reportRunnerFailure('cancel', runner, error);
            }
        });
        return cancelled;
    }

    function completePresentation(cooldownMs) {
        const durationMs = Math.max(0, Number(cooldownMs) || 0);
        presentationCooldownUntil = Math.max(
            presentationCooldownUntil,
            Date.now() + durationMs
        );
        rearmOnNextFact = true;
    }

    function handleManualMove(event) {
        const detail = event && event.detail;
        if (!detail) return;
        if (detail.reason === 'return-ball-drag-start') {
            manualMoveStartedFromPresentation = !!getActiveRunner(null);
            return;
        }
        if (detail.reason === 'return-ball-drag-active') {
            const movedFromPresentation = manualMoveStartedFromPresentation
                || !!getActiveRunner(null);
            manualMoveStartedFromPresentation = false;
            if (movedFromPresentation) {
                completePresentation(MANUAL_MOVE_COOLDOWN_MS);
            } else {
                rearmOnNextFact = true;
            }
            return;
        }
        if (detail.reason === 'return-ball-drag-end'
            || detail.reason === 'return-ball-drag-cancel') {
            manualMoveStartedFromPresentation = false;
        }
    }

    function getState() {
        const active = getActiveRunner(null);
        return Object.freeze({
            activeKind: active ? active.kind : '',
            registeredKinds: Object.freeze(Array.from(runners.keys())),
            cooldownUntil: presentationCooldownUntil,
            rearmOnNextFact: rearmOnNextFact,
            lastRunnerFailure: lastRunnerFailure,
        });
    }

    const unsubscribe = sensingContext.subscribe(processSensingResult);

    function dispose() {
        if (disposed) return;
        disposed = true;
        if (replayTimer) {
            window.clearTimeout(replayTimer);
            replayTimer = 0;
        }
        if (typeof unsubscribe === 'function') {
            try { unsubscribe(); } catch (_) {}
        }
        runners.clear();
        presentationCooldownUntil = 0;
        rearmOnNextFact = false;
        manualMoveStartedFromPresentation = false;
        lastRunnerFailure = null;
        window.removeEventListener(MANUAL_MOVE_EVENT, handleManualMove);
        window.removeEventListener('pagehide', dispose);
        window.removeEventListener('beforeunload', dispose);
        try {
            delete window.NekoDesktopWindowInteractions;
        } catch (_) {
            window.NekoDesktopWindowInteractions = undefined;
        }
    }

    window.NekoDesktopWindowInteractions = Object.freeze({
        register: register,
        cancel: cancel,
        canStart: canStart,
        completePresentation: completePresentation,
        isActive(button) {
            return !!getActiveRunner(button);
        },
        getState: getState,
    });
    window.addEventListener(MANUAL_MOVE_EVENT, handleManualMove);
    window.addEventListener('pagehide', dispose);
    window.addEventListener('beforeunload', dispose);
})();

/**
 * Door walk decorates the existing CAT1 walk towards the minimized yarn. It is
 * not a sensing candidate: the current walk decides when to offer its route.
 */
(function () {
    'use strict';

    const sensingContext = window.nekoDesktopWindowSensingContext;
    if (!sensingContext
        || typeof sensingContext.getCurrent !== 'function'
        || typeof sensingContext.subscribe !== 'function'
        || typeof document === 'undefined'
        || typeof document.createElement !== 'function'
        || !document.body
        || typeof document.body.appendChild !== 'function') return;

    const KIND = 'desktop-window-door-walk';
    const WALK_TARGET_KIND = 'minimized-side';
    const GAP = 6;
    const WALK_SPEED = 82;
    const ARRIVAL_DISTANCE = 14;
    const DOOR_DEPTH = 30;
    const DOOR_PADDING = 8;
    const CLIPPED_CLASS = 'is-cat1-desktop-window-door-clipped';
    const MANUAL_MOVE_EVENT = 'neko:return-ball-manual-move';
    const PLAYGROUND_EVENT = 'neko:idle-cat1-playground-state';

    let disposed = false;
    let currentAction = null;
    let pendingDragResume = null;
    let removalObserver = null;

    function normalizeRect(value) {
        if (!value || typeof value !== 'object') return null;
        const left = Number(value.left === undefined ? value.x : value.left);
        const top = Number(value.top === undefined ? value.y : value.top);
        const width = Number(value.width);
        const height = Number(value.height);
        if (![left, top, width, height].every(Number.isFinite) || width <= 0 || height <= 0) return null;
        return { left, top, right: left + width, bottom: top + height, width, height };
    }

    function sameRect(left, right) {
        return !!(left && right
            && left.left === right.left
            && left.top === right.top
            && left.width === right.width
            && left.height === right.height);
    }

    function localWindowRect(value) {
        const desktop = normalizeRect(value);
        if (!desktop) return null;
        const origin = {
            x: Number.isFinite(Number(window.screenX)) ? Number(window.screenX) : Number(window.screenLeft) || 0,
            y: Number.isFinite(Number(window.screenY)) ? Number(window.screenY) : Number(window.screenTop) || 0,
        };
        return normalizeRect({
            left: desktop.left - origin.x,
            top: desktop.top - origin.y,
            width: desktop.width,
            height: desktop.height,
        });
    }

    function clamp(value, minimum, maximum) {
        return Math.max(minimum, Math.min(value, maximum));
    }

    function centerFits(point, catRect) {
        const viewport = { width: window.innerWidth, height: window.innerHeight };
        return point.x >= catRect.width / 2
            && point.x <= viewport.width - catRect.width / 2
            && point.y >= catRect.height / 2
            && point.y <= viewport.height - catRect.height / 2;
    }

    function getRectCrossing(start, end, rect) {
        let enterAt = 0;
        let exitAt = 1;
        let entrySide = '';
        let exitSide = '';
        const axes = [
            [start.x, end.x - start.x, rect.left, rect.right, 'left', 'right'],
            [start.y, end.y - start.y, rect.top, rect.bottom, 'top', 'bottom'],
        ];
        for (const [origin, delta, minimum, maximum, minimumSide, maximumSide] of axes) {
            if (Math.abs(delta) < 0.000001) {
                if (origin <= minimum || origin >= maximum) return null;
                continue;
            }
            let near = (minimum - origin) / delta;
            let far = (maximum - origin) / delta;
            let nearSide = minimumSide;
            let farSide = maximumSide;
            if (near > far) {
                [near, far] = [far, near];
                [nearSide, farSide] = [farSide, nearSide];
            }
            if (near > enterAt) {
                enterAt = near;
                entrySide = nearSide;
            }
            if (far < exitAt) {
                exitAt = far;
                exitSide = farSide;
            }
            if (enterAt >= exitAt) return null;
        }
        if (!entrySide || !exitSide || entrySide === exitSide
            || enterAt <= 0 || exitAt >= 1) return null;
        return { enterAt, exitAt, entrySide, exitSide };
    }

    function makeDoorSide(side, crossingPoint, catRect, windowRect) {
        const halfWidth = catRect.width / 2;
        const halfHeight = catRect.height / 2;
        const horizontalEdge = side === 'left' || side === 'right';
        const doorLength = horizontalEdge ? Math.max(72, catRect.height) : Math.max(72, catRect.width);
        const windowLength = horizontalEdge ? windowRect.height : windowRect.width;
        if (doorLength + DOOR_PADDING * 2 > windowLength) return null;

        if (horizontalEdge) {
            const y = clamp(
                crossingPoint.y,
                windowRect.top + DOOR_PADDING + doorLength / 2,
                windowRect.bottom - DOOR_PADDING - doorLength / 2
            );
            const left = side === 'left' ? windowRect.left : windowRect.right - DOOR_DEPTH;
            return {
                outside: {
                    x: side === 'left'
                        ? windowRect.left - halfWidth - GAP
                        : windowRect.right + halfWidth + GAP,
                    y,
                },
                hidden: {
                    x: side === 'left'
                        ? windowRect.left + halfWidth + GAP
                        : windowRect.right - halfWidth - GAP,
                    y,
                },
                door: { left, top: y - doorLength / 2, width: DOOR_DEPTH, height: doorLength },
            };
        }

        const x = clamp(
            crossingPoint.x,
            windowRect.left + DOOR_PADDING + doorLength / 2,
            windowRect.right - DOOR_PADDING - doorLength / 2
        );
        const top = side === 'top' ? windowRect.top : windowRect.bottom - DOOR_DEPTH;
        return {
            outside: {
                x,
                y: side === 'top'
                    ? windowRect.top - halfHeight - GAP
                    : windowRect.bottom + halfHeight + GAP,
            },
            hidden: {
                x,
                y: side === 'top'
                    ? windowRect.top + halfHeight + GAP
                    : windowRect.bottom - halfHeight - GAP,
            },
            door: { left: x - doorLength / 2, top, width: doorLength, height: DOOR_DEPTH },
        };
    }

    function makePlan(catRect, target, windowRect) {
        const halfWidth = catRect.width / 2;
        const halfHeight = catRect.height / 2;
        const start = { x: catRect.left + halfWidth, y: catRect.top + halfHeight };
        const targetLeft = Number(target && target.left);
        const targetTop = Number(target && target.top);
        if (!Number.isFinite(targetLeft) || !Number.isFinite(targetTop)) return null;
        const end = { x: targetLeft + halfWidth, y: targetTop + halfHeight };
        const expanded = {
            left: windowRect.left - halfWidth - GAP,
            right: windowRect.right + halfWidth + GAP,
            top: windowRect.top - halfHeight - GAP,
            bottom: windowRect.bottom + halfHeight + GAP,
        };
        const crossing = getRectCrossing(start, end, expanded);
        if (!crossing) return null;
        const dx = end.x - start.x;
        const dy = end.y - start.y;
        const entry = makeDoorSide(crossing.entrySide, {
            x: start.x + dx * crossing.enterAt,
            y: start.y + dy * crossing.enterAt,
        }, catRect, windowRect);
        const exit = makeDoorSide(crossing.exitSide, {
            x: start.x + dx * crossing.exitAt,
            y: start.y + dy * crossing.exitAt,
        }, catRect, windowRect);
        if (!entry || !exit
            || !centerFits(entry.outside, catRect)
            || !centerFits(exit.outside, catRect)) return null;
        return {
            entrySide: crossing.entrySide,
            exitSide: crossing.exitSide,
            entryOutside: entry.outside,
            entryHidden: entry.hidden,
            exitHidden: exit.hidden,
            exitOutside: exit.outside,
            entryDoor: entry.door,
            exitDoor: exit.door,
        };
    }

    function readFact(result) {
        if (!result || typeof result !== 'object') return null;
        const sessionId = typeof result.sessionId === 'string' ? result.sessionId : '';
        const revision = Number(result.revision);
        const windowRect = localWindowRect(result.rect);
        if (!sessionId || !Number.isFinite(revision)
            || result.status === 'unavailable'
            || (result.status !== 'current' && result.status !== 'changed')
            || !windowRect) {
            return null;
        }
        return { sessionId, revision, windowRect };
    }

    function isCat(button) {
        if (!button || button.getAttribute('data-neko-idle-tier') !== 'cat1') return false;
        return typeof _getNekoGoodbyeIdleAppearance !== 'function'
            || typeof _NEKO_GOODBYE_IDLE_APPEARANCE_CAT === 'undefined'
            || _getNekoGoodbyeIdleAppearance() === _NEKO_GOODBYE_IDLE_APPEARANCE_CAT;
    }

    function buildWalkState(button, target, continuation) {
        const fact = readFact(sensingContext.getCurrent());
        if (disposed || currentAction || !fact || !isCat(button)) return null;
        if (typeof _isAnyNekoIdleCat1IndependentActionActive === 'function'
            && _isAnyNekoIdleCat1IndependentActionActive()) return null;
        const container = _getNekoIdleReturnContainerFromButton(button);
        const art = button.querySelector('.neko-idle-return-art');
        const dragging = container && container.getAttribute('data-dragging');
        if (!container || !art || art.__nekoIdleHoverSrc
            || dragging === 'true' || dragging === 'pending') return null;
        const catRect = normalizeRect(container.getBoundingClientRect());
        const route = catRect && makePlan(catRect, target, fact.windowRect);
        if (!route) return null;
        return {
            phase: 'walking-to-entry',
            sessionId: fact.sessionId,
            lastRevision: fact.revision,
            button,
            container,
            catRect,
            windowRect: fact.windowRect,
            route,
            continuation,
            relocated: false,
            frame: 0,
            lastStepAt: 0,
            door: null,
        };
    }

    function setCenter(state, point, options) {
        const left = point.x - state.catRect.width / 2;
        const top = point.y - state.catRect.height / 2;
        try {
            if (typeof _setNekoIdleCat1ContainerPosition !== 'function') throw new Error('position helper unavailable');
            _setNekoIdleCat1ContainerPosition(state.container, left, top);
        } catch (_) {
            state.container.style.left = `${Math.round(left)}px`;
            state.container.style.top = `${Math.round(top)}px`;
            state.container.style.right = '';
            state.container.style.bottom = '';
            state.container.style.transform = 'none';
        }
        const continuation = state.continuation;
        const callback = options && options.rebase === true
            ? continuation && continuation.rebasePosition
            : continuation && continuation.recordPosition;
        if (typeof callback === 'function') {
            try { callback(left, top); } catch (_) {}
        }
    }

    function getCenter(state) {
        const value = normalizeRect(state.container.getBoundingClientRect());
        return value && { x: value.left + value.width / 2, y: value.top + value.height / 2 };
    }

    function setVisibleRatio(state, side, ratio) {
        const hidden = `${(1 - clamp(ratio, 0, 1)) * 100}%`;
        let clip = 'inset(0)';
        if (side === 'left') clip = `inset(0 ${hidden} 0 0)`;
        if (side === 'right') clip = `inset(0 0 0 ${hidden})`;
        if (side === 'top') clip = `inset(0 0 ${hidden} 0)`;
        if (side === 'bottom') clip = `inset(${hidden} 0 0 0)`;
        state.button.style.setProperty('--neko-desktop-window-door-clip', clip);
        state.button.classList.add(CLIPPED_CLASS);
    }

    function clearClip(state) {
        state.button.classList.remove(CLIPPED_CLASS);
        state.button.style.removeProperty('--neko-desktop-window-door-clip');
    }

    function removeDoor(state) {
        try {
            if (state.door && typeof state.door.remove === 'function') state.door.remove();
        } catch (_) {}
        state.door = null;
    }

    function showDoor(state, side, placement) {
        removeDoor(state);
        const layer = document.createElement('div');
        layer.className = `neko-desktop-window-door-layer neko-desktop-window-door is-${side}`;
        layer.setAttribute('aria-hidden', 'true');
        ['opening', 'frame'].forEach((name) => {
            const part = document.createElement('div');
            part.className = `neko-desktop-window-door-${name}`;
            Object.assign(part.style, {
                left: `${Math.round(placement.left)}px`,
                top: `${Math.round(placement.top)}px`,
                width: `${Math.round(placement.width)}px`,
                height: `${Math.round(placement.height)}px`,
            });
            layer.appendChild(part);
        });
        state.door = layer;
        try {
            document.body.appendChild(layer);
        } catch (error) {
            removeDoor(state);
            throw error;
        }
    }

    function recoveryPoint(state) {
        if (state.relocated) return state.route.exitOutside;
        if (state.phase === 'entering' || state.phase === 'hidden-relocation') return state.route.entryOutside;
        return null;
    }

    function finish(state, options) {
        if (!state || currentAction !== state) return false;
        const settings = options || {};
        if (state.frame) window.cancelAnimationFrame(state.frame);
        if (removalObserver) removalObserver.disconnect();
        removalObserver = null;
        clearClip(state);
        removeDoor(state);
        const point = settings.recover === false ? null : recoveryPoint(state);
        const dragging = state.container.getAttribute('data-dragging');
        const hasOwner = dragging === 'true' || dragging === 'pending' || !isCat(state.button);
        if (point && !hasOwner) {
            setCenter(state, point, { rebase: true });
        }
        currentAction = null;
        state.phase = 'idle';
        if (settings.resumeWalk === true && !hasOwner) {
            let resumable = false;
            try { resumable = state.continuation.canResume() === true; } catch (_) {}
            if (!resumable) return true;
            try { state.continuation.resume(); } catch (_) {}
        }
        return true;
    }

    function cancel(button, options) {
        pendingDragResume = null;
        if (!currentAction || (button && currentAction.button !== button)) return false;
        return finish(currentAction, options);
    }

    function moveToward(state, target, timestamp) {
        const from = getCenter(state);
        if (!from) return { invalid: true };
        const dx = target.x - from.x;
        const dy = target.y - from.y;
        const distance = Math.hypot(dx, dy);
        if (distance <= ARRIVAL_DISTANCE) {
            setCenter(state, target);
            return { done: true, point: target };
        }
        const elapsed = Math.max(12, Math.min(timestamp - (state.lastStepAt || timestamp), 48));
        state.lastStepAt = timestamp;
        const ratio = Math.min(1, WALK_SPEED * elapsed / 1000 / distance);
        const next = { x: from.x + dx * ratio, y: from.y + dy * ratio };
        setCenter(state, next);
        return { done: ratio >= 1, point: next };
    }

    function schedule(state) {
        state.frame = window.requestAnimationFrame((timestamp) => {
            state.frame = 0;
            try {
                step(state, timestamp);
            } catch (_) {
                finish(state, { resumeWalk: true });
            }
        });
    }

    function ownerIsCurrent(state) {
        try { return state.continuation.isCurrent() === true; } catch (_) { return false; }
    }

    function lockedWindowIsCurrent(state) {
        const current = sensingContext.getCurrent();
        const sameWindow = !!(current && current.sessionId === state.sessionId
            && (current.status === 'current' || current.status === 'changed')
            && sameRect(localWindowRect(current.rect), state.windowRect)
            && !(Number(current.revision) > state.lastRevision
            && Array.isArray(current.changes)
            && current.changes.includes('identity')));
        return sameWindow;
    }

    function step(state, timestamp) {
        if (!state || currentAction !== state) return;
        if (!state.button || state.button.isConnected === false
            || !state.container || state.container.isConnected === false
            || !isCat(state.button)) {
            finish(state, { recover: false });
            return;
        }
        if (!ownerIsCurrent(state)) {
            finish(state, { resumeWalk: true });
            return;
        }
        if (state.phase === 'walking-to-entry') {
            const result = moveToward(state, state.route.entryOutside, timestamp);
            if (result.invalid) return finish(state, { resumeWalk: true });
            if (!result.done) return schedule(state);
            state.phase = 'entering';
            state.lastStepAt = 0;
            setVisibleRatio(state, state.route.entrySide, 1);
            return schedule(state);
        }
        if (state.phase === 'entering') {
            const total = Math.hypot(
                state.route.entryHidden.x - state.route.entryOutside.x,
                state.route.entryHidden.y - state.route.entryOutside.y
            );
            const result = moveToward(state, state.route.entryHidden, timestamp);
            if (result.invalid) return finish(state, { resumeWalk: true });
            const remaining = Math.hypot(
                state.route.entryHidden.x - result.point.x,
                state.route.entryHidden.y - result.point.y
            );
            setVisibleRatio(state, state.route.entrySide, remaining / total);
            if (!result.done) return schedule(state);
            setVisibleRatio(state, state.route.entrySide, 0);
            state.phase = 'hidden-relocation';
            if (!lockedWindowIsCurrent(state)) return finish(state, { resumeWalk: true });
            removeDoor(state);
            setCenter(state, state.route.exitHidden, { rebase: true });
            state.relocated = true;
            showDoor(state, state.route.exitSide, state.route.exitDoor);
            setVisibleRatio(state, state.route.exitSide, 0);
            state.phase = 'exiting';
            state.lastStepAt = 0;
            return schedule(state);
        }
        if (state.phase === 'exiting') {
            const total = Math.hypot(
                state.route.exitOutside.x - state.route.exitHidden.x,
                state.route.exitOutside.y - state.route.exitHidden.y
            );
            const result = moveToward(state, state.route.exitOutside, timestamp);
            if (result.invalid) return finish(state, { resumeWalk: true });
            const travelled = Math.hypot(
                result.point.x - state.route.exitHidden.x,
                result.point.y - state.route.exitHidden.y
            );
            setVisibleRatio(state, state.route.exitSide, travelled / total);
            if (!result.done) return schedule(state);
            clearClip(state);
            removeDoor(state);
            return finish(state, { recover: false, resumeWalk: true });
        }
    }

    function startState(state) {
        if (!state || currentAction) return false;
        currentAction = state;
        try {
            showDoor(state, state.route.entrySide, state.route.entryDoor);
            const parent = state.container.parentNode;
            if (parent && typeof MutationObserver === 'function') {
                removalObserver = new MutationObserver(() => {
                    if (currentAction !== state) return;
                    if (state.button.isConnected !== false && state.container.isConnected !== false) return;
                    finish(state, { recover: false });
                });
                removalObserver.observe(parent, { childList: true });
            }
            step(state, performance.now());
            return true;
        } catch (_) {
            finish(state, { recover: false });
            return false;
        }
    }

    function tryStartWalk(button, target, continuation) {
        if (!target || target.kind !== WALK_TARGET_KIND
            || !continuation
            || typeof continuation.isCurrent !== 'function'
            || typeof continuation.canResume !== 'function'
            || typeof continuation.resume !== 'function') {
            return false;
        }
        return startState(buildWalkState(button, target, continuation));
    }

    function handleSensingResult(result) {
        if (!result) {
            pendingDragResume = null;
            if (currentAction) finish(currentAction, { recover: false });
            return false;
        }
        if (!currentAction) return false;
        const fact = readFact(result);
        if (!fact) {
            finish(currentAction, { resumeWalk: true });
            return false;
        }
        if (fact.sessionId === currentAction.sessionId && fact.revision <= currentAction.lastRevision) {
            return false;
        }
        if (fact.sessionId !== currentAction.sessionId
            || !sameRect(fact.windowRect, currentAction.windowRect)
            || (Array.isArray(result.changes) && result.changes.includes('identity'))) {
            finish(currentAction, { resumeWalk: true });
            return false;
        }
        currentAction.lastRevision = fact.revision;
        return false;
    }

    function handleManualMove(event) {
        const detail = event && event.detail;
        if (!detail) return;
        if (currentAction && detail.container === currentAction.container
            && detail.reason === 'return-ball-drag-start') {
            pendingDragResume = currentAction.continuation;
            finish(currentAction, { recover: false });
            return;
        }
        if (detail.reason === 'return-ball-drag-active'
            || detail.reason === 'return-ball-drag-end') {
            pendingDragResume = null;
            return;
        }
        if (detail.reason === 'return-ball-drag-cancel' && pendingDragResume) {
            const continuation = pendingDragResume;
            pendingDragResume = null;
            let resumable = false;
            try { resumable = continuation.canResume() === true; } catch (_) {}
            if (resumable) {
                try { continuation.resume(); } catch (_) {}
            }
        }
    }

    function handlePlayground(event) {
        if (event && event.detail && event.detail.active === true) {
            pendingDragResume = null;
            cancel(null, { recover: false });
        }
    }

    function getState() {
        if (!currentAction) return Object.freeze({ phase: 'idle', targetKind: KIND });
        return Object.freeze({
            phase: currentAction.phase,
            targetKind: KIND,
            sessionId: currentAction.sessionId,
            revision: currentAction.lastRevision,
            entrySide: currentAction.route.entrySide,
            exitSide: currentAction.route.exitSide,
        });
    }

    const unsubscribe = sensingContext.subscribe(handleSensingResult);

    function disposeDoorWalk() {
        disposed = true;
        pendingDragResume = null;
        cancel(null, { recover: false });
        if (typeof unsubscribe === 'function') {
            try { unsubscribe(); } catch (_) {}
        }
        window.removeEventListener(MANUAL_MOVE_EVENT, handleManualMove);
        window.removeEventListener(PLAYGROUND_EVENT, handlePlayground);
        window.removeEventListener('pagehide', disposeDoorWalk);
        window.removeEventListener('beforeunload', disposeDoorWalk);
        try { delete window.NekoDesktopWindowDoorWalk; } catch (_) {
            window.NekoDesktopWindowDoorWalk = undefined;
        }
    }

    window.NekoDesktopWindowDoorWalk = Object.freeze({
        tryStartWalk,
        cancel,
        getState,
        isActive(button) {
            return !!(currentAction && (!button || currentAction.button === button));
        },
    });
    window.addEventListener(MANUAL_MOVE_EVENT, handleManualMove);
    window.addEventListener(PLAYGROUND_EVENT, handlePlayground);
    window.addEventListener('pagehide', disposeDoorWalk);
    window.addEventListener('beforeunload', disposeDoorWalk);
})();
