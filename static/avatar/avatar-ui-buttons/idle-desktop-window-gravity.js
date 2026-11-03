/**
 * Gravity shared by every cat tier and action. The window is a moving box:
 * collisions use wall-relative velocity, while the cat keeps its own inertia.
 * Non-movement actions keep their art; walks resume after settling. Physics
 * owns motion and containment. The playground delegates its cat body here.
 */
(function () {
    'use strict';

    const sensingContext = window.nekoDesktopWindowSensingContext;
    if (!sensingContext || typeof sensingContext.subscribe !== 'function') return;

    const KIND = 'desktop-window-gravity';
    const ACTIVE_CLASS = 'is-desktop-window-gravity';
    const MANUAL_MOVE_EVENT = 'neko:return-ball-manual-move';
    const PLAYGROUND_EVENT = 'neko:idle-cat1-playground-state';
    const GRAVITY = 1440;
    const RESTITUTION = 0.52;
    const MAX_SPEED = 1800;
    const REST_SPEED = 38;
    const MAX_STEP_SECONDS = 1 / 120;
    const MAX_FRAME_SECONDS = 0.05;
    const MAX_SAMPLE_SECONDS = 0.8;
    const CONTACT_EPSILON = 0.5;
    // Fallback for callers without a Playground body. Real bodies supply the
    // current asset's stable, frame-union hull through the resource registry.
    const insets = Object.freeze({ left: 89 / 512, top: 49 / 512, right: 118 / 512, bottom: 19 / 512 });
    let current = null;
    let latest = null;
    let lastFreeDesktopSceneAt = 0;
    // Only an explicit pointer release may hand the cat to a window.
    // A held drag is free, while a throw and a fresh native scene must never
    // claim a stationary cat.
    let pendingAdmission = null;
    let sceneUnavailable = false;
    let playgroundActive = false;
    let disposed = false;
    let removalObserver = null;
    let dragMotions = new WeakMap();
    let cachedBorders = null;
    // A stationary press sets body.dragging, so a window that disappears during
    // the press cannot drop the cat until the pointer is released.
    let lostDesktopSupport = new WeakSet();

    function now() {
        return typeof performance !== 'undefined' && typeof performance.now === 'function'
            ? performance.now() : Date.now();
    }

    function rect(value) {
        if (!value) return null;
        const x = Number(value.x === undefined ? value.left : value.x);
        const y = Number(value.y === undefined ? value.top : value.y);
        const width = Number(value.width);
        const height = Number(value.height);
        if (![x, y, width, height].every(Number.isFinite) || width <= 0 || height <= 0) return null;
        return { x, y, width, height };
    }

    function clampSpeed(value) {
        return Math.max(-MAX_SPEED, Math.min(MAX_SPEED, value));
    }

    function screenOrigin() {
        return {
            x: Number(window.screenX ?? window.screenLeft) || 0,
            y: Number(window.screenY ?? window.screenTop) || 0,
        };
    }

    function boundsFor(windowRect, size, body = null, origin = null) {
        const target = rect(windowRect);
        const viewportWidth = Number(window.innerWidth);
        const viewportHeight = Number(window.innerHeight);
        if (!target || !size || !Number.isFinite(viewportWidth) || !Number.isFinite(viewportHeight)) return null;
        const sampleOrigin = origin && Number.isFinite(origin.x) && Number.isFinite(origin.y)
            ? origin : screenOrigin();
        origin = sampleOrigin;
        const hull = body && body.visibleInsetRatios || insets;
        // The collision hull excludes transparent GIF margins. Keep its visible
        // part on screen even when the native window extends beyond the display.
        const bounds = {
            left: Math.max(0, target.x - origin.x) - size.width * hull.left,
            right: Math.min(viewportWidth, target.x - origin.x + target.width) - size.width * (1 - hull.right),
            top: Math.max(0, target.y - origin.y) - size.height * hull.top,
            bottom: Math.min(viewportHeight, target.y - origin.y + target.height) - size.height * (1 - hull.bottom),
        };
        return bounds.right - bounds.left >= 2 && bounds.bottom - bounds.top >= 2 ? bounds : null;
    }

    function isCat(button, container) {
        return !!(button && container && button.isConnected !== false && container.isConnected !== false
            && container.style.display !== 'none'
            && ['cat1', 'cat2', 'cat3'].includes(button.getAttribute('data-neko-idle-tier'))
            && ['cat1', 'cat2', 'cat3'].includes(_getActiveNekoIdleReturnTier())
            && _getNekoGoodbyeIdleAppearance() === _NEKO_GOODBYE_IDLE_APPEARANCE_CAT);
    }

    function isReturning(button) {
        const container = _getNekoIdleReturnContainerFromButton(button);
        // The general return/action guard also includes the drag click guard,
        // which remains set briefly after mouseup. That guard must not discard
        // a throw or stop its following frames; only a real return owns physics.
        return container?.getAttribute('data-neko-model-cat-transitioning') === 'cat-to-model';
    }

    function getBody(button) {
        const playground = button && button.__nekoIdleCat1PlaygroundDropState;
        const body = playground && playground.active && !playground.released
            && playground.bodies && playground.bodies.get('cat') || null;
        if (body && typeof _syncNekoIdleCat1PlaygroundCatVisibleInsets === 'function') {
            _syncNekoIdleCat1PlaygroundCatVisibleInsets(body, button);
        }
        return body;
    }

    function contains(bounds, position) {
        return !!(bounds && position && position.x >= bounds.left - CONTACT_EPSILON
            && position.x <= bounds.right + CONTACT_EPSILON
            && position.y >= bounds.top - CONTACT_EPSILON && position.y <= bounds.bottom + CONTACT_EPSILON);
    }

    function sceneFor(result) {
        if (!result || result.status === 'unavailable') return [];
        // Legacy sensing has only the focused-window rect and is not a gravity
        // scene. Treating it as a synthetic container makes Playground gravity
        // depend on focus and reintroduces the old owner ambiguity.
        return Array.isArray(result.windows) ? result.windows : [];
    }

    function sceneSignature(scene) {
        return scene.map(item => `${item.key}:${item.collisionOnly === true}:${item.rect.x},${item.rect.y},${item.rect.width},${item.rect.height}`).join('|');
    }

    function localRect(value) {
        const origin = screenOrigin();
        return { x: value.x - origin.x, y: value.y - origin.y, width: value.width, height: value.height };
    }

    function buildBorders(scene, ownerKey) {
        const origin = screenOrigin();
        const cacheKey = `${ownerKey == null ? '\0' : ownerKey}|${origin.x},${origin.y}|${window.innerWidth},${window.innerHeight}|${sceneSignature(scene)}`;
        if (cachedBorders && cachedBorders.key === cacheKey) return cachedBorders.borders;
        const borders = [];
        const windows = scene.map(item => ({ ...item, rect: localRect(item.rect) }));
        windows.forEach((item, index) => {
            if (item.key === ownerKey) return;
            const r = item.rect;
            for (const [side, axis, line, low, high] of [
                ['left', 'x', r.x, r.y, r.y + r.height],
                ['right', 'x', r.x + r.width, r.y, r.y + r.height],
                ['top', 'y', r.y, r.x, r.x + r.width],
                ['bottom', 'y', r.y + r.height, r.x, r.x + r.width],
            ]) {
                const axisLimit = axis === 'x' ? window.innerWidth : window.innerHeight;
                const tangentLimit = axis === 'x' ? window.innerHeight : window.innerWidth;
                if (line < 0 || line > axisLimit) continue;
                let spans = [[Math.max(0, low), Math.min(tangentLimit, high)]];
                // Only painted foreground rectangles obscure a border. App
                // carriers are absent, so transparent gaps remain traversable.
                for (const foreground of windows.slice(0, index)) {
                    const f = foreground.rect;
                    const min = axis === 'x' ? f.x : f.y;
                    const max = min + (axis === 'x' ? f.width : f.height);
                    if (line < min || line > max) continue;
                    const a = axis === 'x' ? f.y : f.x;
                    const b = a + (axis === 'x' ? f.height : f.width);
                    spans = spans.flatMap(([start, end]) => b <= start || a >= end ? [[start, end]]
                        : [[start, Math.min(end, a)], [Math.max(start, b), end]].filter(([s, e]) => e > s));
                }
                for (const [start, end] of spans) {
                    if (end > start) borders.push({ key: item.key, side, axis, line, start, end });
                }
            }
        });
        cachedBorders = { key: cacheKey, borders };
        return borders;
    }

    function borderLine(item, side) {
        const r = localRect(item.rect);
        return side === 'left' ? r.x : side === 'right' ? r.x + r.width
            : side === 'top' ? r.y : r.y + r.height;
    }

    function collideBorders(state, previous, dt, previousScene = null, moving = false) {
        const hull = state.hull;
        const offsets = { left: state.size.width * hull.left, right: state.size.width * (1 - hull.right),
            top: state.size.height * hull.top, bottom: state.size.height * (1 - hull.bottom) };
        const oldWindows = previousScene && new Map(previousScene.map(item => [item.key, item]));
        // Swept body/segment crossings prevent tunnelling through thin borders,
        // including a wall moved across the entire cat between native samples.
        for (let pass = 0; pass < 4; pass++) {
            let first = null;
            for (const edge of state.borders) {
                const oldWindow = oldWindows && oldWindows.get(edge.key);
                const oldLine = oldWindow ? borderLine(oldWindow, edge.side) : edge.line;
                const axis = edge.axis, tangent = axis === 'x' ? 'y' : 'x';
                const low = axis === 'x' ? offsets.left : offsets.top;
                const high = axis === 'x' ? offsets.right : offsets.bottom;
                for (const direction of [1, -1]) {
                    const offset = direction > 0 ? high : low;
                    const before = (previous[axis] + offset - oldLine) * direction;
                    const after = (state[axis] + offset - edge.line) * direction;
                    if (before > 0.001 || after <= 0.001) continue;
                    const fraction = Math.max(0, Math.min(1, -before / (after - before)));
                    const t = previous[tangent] + (state[tangent] - previous[tangent]) * fraction;
                    const tLow = axis === 'x' ? offsets.top : offsets.left;
                    const tHigh = axis === 'x' ? offsets.bottom : offsets.right;
                    if (t + tHigh <= edge.start || t + tLow >= edge.end) continue;
                    if (!first || fraction < first.fraction) first = { edge, direction, offset, fraction, oldLine };
                }
            }
            if (!first) break;
            const { edge, direction, offset, oldLine } = first;
            const velocity = edge.axis === 'x' ? 'vx' : 'vy';
            const wallSpeed = moving && oldWindows.has(edge.key) && dt >= 1 / 240 && dt <= MAX_SAMPLE_SECONDS
                ? clampSpeed((edge.line - oldLine) / dt) : 0;
            const implied = dt > 0 ? (state[edge.axis] - previous[edge.axis] - edge.line + oldLine) / dt : 0;
            const speed = Math.max(0, (state[velocity] - wallSpeed) * direction, implied * direction);
            state[edge.axis] = edge.line - offset;
            state[velocity] = clampSpeed(wallSpeed - (state.reduceMotion ? 0 : RESTITUTION) * speed * direction);
            impact(state, speed, edge.axis === 'y');
            if (edge.axis === 'y' && direction > 0 && speed < REST_SPEED && Math.abs(wallSpeed) < REST_SPEED) {
                state.vy = 0;
                state.grounded = true;
            }
        }
        // If moving windows squeeze away all clearance, the containing window
        // remains the final boundary; never push the cat outside its box.
        const b = state.bounds;
        if (state.x < b.left || state.x > b.right || state.y < b.top || state.y > b.bottom) collide(state);
    }

    function getCandidate(admission) {
        if (disposed || !playgroundActive || current || !latest || !admission) return null;
        const button = admission.button;
        const container = button && _getNekoIdleReturnContainerFromButton(button);
        const art = button && button.querySelector('.neko-idle-return-art');
        if (!isCat(button, container) || !art || isReturning(button)) return null;
        const dragging = container.getAttribute('data-dragging');
        const body = getBody(button);
        if (admission.body && body !== admission.body) return null;
        if ((dragging === 'true' || dragging === 'pending' || (body && body.dragging))
            && admission.kind !== 'drag') return null;
        const measured = rect(container.getBoundingClientRect());
        if (!measured) return null;
        // A gesture sample, not a later scene refresh, determines admission.
        const size = {
            ...measured,
            x: Number.isFinite(admission.x) ? admission.x : measured.x,
            y: Number.isFinite(admission.y) ? admission.y : measured.y,
        };
        const scene = sceneFor(latest);
        for (const target of scene) {
            if (target.collisionOnly === true) continue;
            const bounds = boundsFor(target.rect, size, body);
            if (!contains(bounds, size)) continue;
            return {
                targetKind: KIND, sessionId: latest.sessionId, revision: latest.revision,
                windowKey: target.key, scene, sceneSignature: sceneSignature(scene),
                borders: buildBorders(scene, target.key),
                button, container, size, bounds, hull: body && body.visibleInsetRatios || insets,
            };
        }
        return null;
    }

    function render(state, options = {}) {
        // Identical writes still notify MutationObservers in Chromium. Avoid
        // feeding an endless layout/journey loop after the cat has settled.
        for (const [key, value] of Object.entries({ left: `${Number(state.x.toFixed(3))}px`,
            top: `${Number(state.y.toFixed(3))}px`, right: '', bottom: '', transform: 'none' })) {
            if (state.container.style[key] !== value) state.container.style[key] = value;
        }
        for (const [axis, scale] of [['x', 1 + state.squash], ['y', 1 - state.squash]]) {
            const key = `--neko-window-gravity-scale-${axis}`;
            if (state.button.style.getPropertyValue(key) !== scale.toFixed(4)) {
                state.button.style.setProperty(key, scale.toFixed(4));
            }
        }
        const body = getBody(state.button);
        if (body && !body.dragging) {
            Object.assign(body, { x: state.x, y: state.y, vx: state.vx, vy: state.vy,
                grounded: state.grounded, floorY: state.bounds.bottom,
                wallLeft: state.bounds.left, wallRight: state.bounds.right });
            if (!options.fromPlayground && (!state.grounded || state.vx !== 0)
                && typeof _startNekoIdleCat1PlaygroundPhysics === 'function') {
                _startNekoIdleCat1PlaygroundPhysics(state.button);
            }
        }
        publishMovementState(state);
    }

    function canWalk(button) {
        return !current || (button && current.button !== button)
            || (current.grounded && current.vx === 0 && current.squash === 0 && !paused(current));
    }

    function isMoving(button) {
        return !!(current && (!button || current.button === button) && !paused(current)
            && isCat(current.button, current.container) && !isReturning(current.button)
            && (!current.grounded || current.vx !== 0 || current.vy !== 0 || current.squash !== 0));
    }

    function publishMotionState(state) {
        const moving = isMoving(state.button);
        if (state.motionActive === moving) return;
        state.motionActive = moving;
        window.dispatchEvent(new CustomEvent('neko:desktop-window-gravity-motion', {
            detail: { button: state.button, moving },
        }));
    }

    function publishMovementState(state) {
        publishMotionState(state);
        const allowed = canWalk(state.button);
        if (state.walkAllowed === allowed) return;
        state.walkAllowed = allowed;
        window.dispatchEvent(new CustomEvent('neko:desktop-window-gravity-state', {
            detail: { button: state.button, canWalk: allowed },
        }));
    }

    function usesContinuousDrag(container) {
        // Keep the Windows carrier fixed during Playground physics. Native shrink/restore
        // hides both the DOM and BrowserWindow while awaiting IPC/resize, leaving
        // a visible gap before a throw. All avatar providers have a DOM drag path.
        if (disposed || !playgroundActive || window.__NEKO_DESKTOP_RUNTIME__?.platform !== 'win32') return false;
        return [...document.querySelectorAll('.neko-idle-return-btn')].some(button =>
            _getNekoIdleReturnContainerFromButton(button) === container && isCat(button, container));
    }

    function impact(state, relativeSpeed, vertical) {
        if (relativeSpeed < REST_SPEED || state.reduceMotion) return;
        const amount = Math.min(0.12, relativeSpeed / 7000);
        state.squash = vertical ? amount : -amount;
    }

    function collide(state, walls = {}) {
        const b = state.bounds;
        for (const axis of [
            { position: 'x', velocity: 'vx', low: 'left', high: 'right', vertical: false },
            { position: 'y', velocity: 'vy', low: 'top', high: 'bottom', vertical: true },
        ]) {
            const { position, velocity, low, high, vertical } = axis;
            for (const side of [low, high]) {
                const sign = side === low ? 1 : -1;
                if ((state[position] - b[side]) * sign > CONTACT_EPSILON) continue;
                const wallSpeed = walls[side] || 0;
                const relative = (state[velocity] - wallSpeed) * sign;
                state[position] = b[side];
                if (relative < 0) {
                    const restitution = state.reduceMotion ? 0 : RESTITUTION;
                    state[velocity] = clampSpeed(wallSpeed - restitution * (state[velocity] - wallSpeed));
                    impact(state, -relative, vertical);
                    if (side === 'bottom' && -relative < REST_SPEED && Math.abs(wallSpeed) < REST_SPEED) {
                        state.vy = 0;
                    }
                }
            }
        }
        const foot = state.y + state.size.height * (1 - state.hull.bottom);
        const support = state.borders.find(edge => edge.axis === 'y' && Math.abs(foot - edge.line) <= CONTACT_EPSILON
            && state.x + state.size.width * (1 - state.hull.right) > edge.start
            && state.x + state.size.width * state.hull.left < edge.end);
        const floor = support ? support.line - state.size.height * (1 - state.hull.bottom) : b.bottom;
        state.grounded = (support || state.y >= b.bottom - CONTACT_EPSILON) && Math.abs(state.vy) < REST_SPEED;
        if (state.grounded) {
            state.y = floor;
            state.vy = 0;
        }
    }

    function step(state, dt) {
        const previous = { x: state.x, y: state.y };
        state.vy = clampSpeed(state.vy + GRAVITY * dt);
        state.vx *= Math.exp(-(state.grounded ? 7 : 0.35) * dt);
        state.x += state.vx * dt;
        state.y += state.vy * dt;
        collide(state);
        collideBorders(state, previous, dt);
        state.squash *= Math.exp(-18 * dt);
        if (Math.abs(state.squash) < 0.001) state.squash = 0;
        if (state.grounded && Math.abs(state.vx) < 2) state.vx = 0;
    }

    function paused(state) {
        const dragging = state.container.getAttribute('data-dragging');
        const body = getBody(state.button);
        return state.pointerHeld || dragging === 'pending' || dragging === 'true' || !!(body && body.dragging);
    }

    function requestFrame(state) {
        if (current !== state || state.frame || paused(state)) return;
        if (getBody(state.button)) {
            if (typeof _startNekoIdleCat1PlaygroundPhysics === 'function') {
                _startNekoIdleCat1PlaygroundPhysics(state.button);
            }
            return;
        }
        state.frame = window.requestAnimationFrame((timestamp) => tick(state, timestamp));
    }

    function advancePhysics(state, timestamp) {
        // Native facts and RAF callbacks share one monotonic simulation clock.
        // A fact can arrive after a RAF timestamp was assigned but before its
        // callback runs; never move the clock backwards or integrate twice.
        if (timestamp <= state.lastStepAt) return;
        const body = getBody(state.button);
        if (body && !body.dragging) {
            state.x = body.x; state.y = body.y;
            state.vx = clampSpeed(body.vx); state.vy = clampSpeed(body.vy);
            state.grounded = body.grounded;
        }
        let remaining = Math.min(MAX_FRAME_SECONDS, (timestamp - state.lastStepAt) / 1000);
        state.lastStepAt = timestamp;
        while (remaining > 0.000001) {
            const dt = Math.min(MAX_STEP_SECONDS, remaining);
            step(state, dt);
            remaining -= dt;
        }
    }

    function tick(state, timestamp) {
        state.frame = 0;
        if (current !== state) return;
        if (!isCat(state.button, state.container) || isReturning(state.button)) {
            cancel(state.button);
            return;
        }
        if (paused(state)) return;
        advancePhysics(state, timestamp);
        if (!state.windowKey) adoptWindow(state);
        render(state);
        if (finishDesktopLanding(state)) return;
        // Resting needs no RAF and never blocks another action or its animation.
        // A new native-window fact wakes it when the box actually changes.
        if (!state.grounded || state.vx !== 0 || state.squash !== 0) requestFrame(state);
    }

    function finishDesktopLanding(state) {
        if (state.windowKey || !state.grounded || state.y !== state.bounds.bottom
            || state.vx !== 0 || state.squash !== 0) return false;
        // An interrupted window interaction ends at the desktop floor.
        cancel(state.button);
        return true;
    }

    function cancel(button) {
        const state = current;
        if (!state || (button && state.button !== button)) return false;
        current = null;
        if (state.frame) window.cancelAnimationFrame(state.frame);
        if (removalObserver) removalObserver.disconnect();
        removalObserver = null;
        state.button.classList.remove(ACTIVE_CLASS);
        state.button.style.removeProperty('--neko-window-gravity-scale-x');
        state.button.style.removeProperty('--neko-window-gravity-scale-y');
        const body = getBody(state.button);
        if (body && !body.dragging && !disposed && isCat(state.button, state.container)
            && !isReturning(state.button)) {
            // If a window disappears under a resting playground cat, hand it
            // back to the playground floor instead of leaving it suspended.
            body.grounded = false;
            if (typeof _startNekoIdleCat1PlaygroundPhysics === 'function') {
                _startNekoIdleCat1PlaygroundPhysics(state.button);
            }
        }
        // Leave the actual landing position for the next owner (drag/return/tier).
        publishMotionState(state);
        if (!disposed && isCat(state.button, state.container) && !isReturning(state.button)) {
            publishMovementState(state);
        }
        return true;
    }

    function resumePendingAdmission(admission) {
        if (!admission || !playgroundActive) return;
        const body = getBody(admission.button);
        if (admission.body && body !== admission.body) return;
        if (body && !body.dragging && typeof _startNekoIdleCat1PlaygroundPhysics === 'function') {
            _startNekoIdleCat1PlaygroundPhysics(admission.button);
        }
    }

    function clearPendingAdmission(resume = true) {
        const admission = pendingAdmission;
        pendingAdmission = null;
        if (resume) resumePendingAdmission(admission);
        return admission;
    }

    function attemptPendingAdmission() {
        const admission = pendingAdmission;
        if (!admission || !playgroundActive || !latest) return false;
        if (admission.kind === 'drag') {
            const body = getBody(admission.button);
            const drag = admission.container && dragMotions.get(admission.container);
            if ((!body || !body.dragging) && (!drag || !drag.moved)) {
                clearPendingAdmission(false);
                return false;
            }
        }
        // The admission point is the explicit pointer-release fact. The
        // Playground may advance the body while native sensing is busy; using
        // the body's later position here would reject a valid handoff or let
        // a throw claim a window after it has moved into one.
        const expected = {
            targetKind: KIND,
            sessionId: latest.sessionId,
            revision: latest.revision,
        };
        const started = startCandidate(expected, admission);
        pendingAdmission = null;
        if (!started) resumePendingAdmission(admission);
        return started;
    }

    function requestAdmission(button, body = null, options = {}) {
        if (disposed || !playgroundActive || current || !button) return false;
        // A release after a confirmed empty scene is a completed failed
        // handoff, not a promise to retry when a future window appears. A new
        // drag remains eligible and will replace this fact once it starts.
        if ((options.kind || 'release') !== 'drag' && sceneUnavailable) {
            clearPendingAdmission(false);
            return false;
        }
        const container = _getNekoIdleReturnContainerFromButton(button);
        const measured = container && typeof container.getBoundingClientRect === 'function'
            ? rect(container.getBoundingClientRect()) : null;
        if (!container || !measured || (body && body !== getBody(button))) return false;
        pendingAdmission = {
            button, body, container,
            kind: options.kind || 'release',
            x: Number.isFinite(options.x) ? options.x
                : body && Number.isFinite(Number(body.x)) ? Number(body.x) : measured.x,
            y: Number.isFinite(options.y) ? options.y
                : body && Number.isFinite(Number(body.y)) ? Number(body.y) : measured.y,
        };
        if (latest) attemptPendingAdmission();
        return true;
    }

    function requestContainerAdmission(container, options = {}) {
        if (!container) return false;
        for (const button of document.querySelectorAll('.neko-idle-return-btn')) {
            if (_getNekoIdleReturnContainerFromButton(button) !== container) continue;
            return requestAdmission(button, getBody(button), options);
        }
        return false;
    }

    function dragBody(button, body) {
        if (!body || !body.dragging || body !== getBody(button)) return false;
        // Playground owns every pointer position while the pointer is held.
        // Gravity must not inspect the start point, path, or current window
        // bounds during that gesture. The final position is checked once on
        // releaseBody, after the body has left dragging state.
        if (current) {
            if (current.button === button) cancel(button);
        }
        return false;
    }

    function startCandidate(expected, admission) {
        const candidate = getCandidate(admission);
        if (!candidate || (expected && (candidate.sessionId !== expected.sessionId
            || candidate.revision !== expected.revision || expected.targetKind !== KIND))) return false;
        const state = {
            ...candidate, x: candidate.size.x, y: candidate.size.y, vx: 0, vy: 0,
            grounded: false, squash: 0, frame: 0, pointerHeld: false,
            origin: screenOrigin(),
            lastStepAt: now(), lastSampleAt: now(),
            reduceMotion: !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches),
        };
        current = state;
        pendingAdmission = null;
        state.button.classList.add(ACTIVE_CLASS);
        if (typeof MutationObserver === 'function' && state.container.parentNode) {
            removalObserver = new MutationObserver(() => {
                if (current !== state) return;
                if (!isCat(state.button, state.container)) { cancel(state.button); return; }
                if (paused(state)) return;
                const actual = rect(state.container.getBoundingClientRect());
                if (!actual) return;
                const body = getBody(state.button);
                if (actual.width !== state.size.width || actual.height !== state.size.height
                    || (body && body.visibleInsetRatios && body.visibleInsetRatios !== state.hull)) {
                    if (latest) updateBounds(state, latest);
                }
                // Tier/layout changes can bypass the shared movement helper.
                // Keep physics in control until landing, then allow horizontal walks.
                if (Math.abs(actual.x - state.x) > 0.6 || Math.abs(actual.y - state.y) > 0.6) {
                    applyPosition(state.container, actual.x, actual.y);
                }
            });
            removalObserver.observe(state.container.parentNode, { childList: true });
            removalObserver.observe(state.container, {
                attributes: true, attributeFilter: ['style', 'class', 'src', 'data-neko-idle-tier'], subtree: true,
            });
        }
        const body = getBody(state.button);
        if (body) { state.vx = clampSpeed(body.vx || 0); state.vy = clampSpeed(body.vy || 0); }
        collide(state);
        // Commit the explicit admission point before the Playground loop can
        // sample the body's pre-admission position on its next frame.
        render(state, { fromPlayground: true });
        requestFrame(state);
        return true;
    }

    function containerAt(scene, state, size = null, origin = null) {
        const measured = size || state.size;
        for (const target of scene) {
            if (target.collisionOnly === true) continue;
            const bounds = boundsFor(target.rect, measured, getBody(state.button), origin);
            if (!contains(bounds, state)) continue;
            return { target, bounds };
        }
        return null;
    }

    function adoptWindow(state) {
        const container = containerAt(state.scene, state);
        if (!container) return false;
        state.windowKey = container.target.key;
        state.bounds = container.bounds;
        state.borders = buildBorders(state.scene, state.windowKey);
        state.lastSampleAt = now();
        collide(state);
        return true;
    }

    function continueAfterWindowClosed(state, result) {
        if (!isCat(state.button, state.container) || isReturning(state.button)) {
            cancel(state.button);
            return;
        }
        const size = rect(state.container.getBoundingClientRect());
        const origin = screenOrigin();
        const body = getBody(state.button);
        const workTop = typeof _getNekoIdleCat1PlaygroundViewportTopPx === 'function'
            ? _getNekoIdleCat1PlaygroundViewportTopPx() : 0;
        const workBottom = typeof _getNekoIdleCat1PlaygroundViewportBottomPx === 'function'
            ? _getNekoIdleCat1PlaygroundViewportBottomPx() : window.innerHeight;
        const workLeft = typeof _getNekoIdleCat1PlaygroundViewportLeftPx === 'function'
            ? _getNekoIdleCat1PlaygroundViewportLeftPx() : 0;
        const workRight = typeof _getNekoIdleCat1PlaygroundViewportRightPx === 'function'
            ? _getNekoIdleCat1PlaygroundViewportRightPx() : window.innerWidth;
        const bounds = boundsFor({
            x: origin.x + workLeft,
            y: origin.y + workTop,
            width: Math.max(0, workRight - workLeft),
            height: Math.max(0, workBottom - workTop),
        }, size, body, origin);
        if (!bounds) { cancel(state.button); return; }
        const scene = sceneFor(result);
        const signature = sceneSignature(scene);
        const hull = body && body.visibleInsetRatios || insets;
        state.revision = result.revision;
        state.lastSampleAt = now();
        if (!state.windowKey && signature === state.sceneSignature && hull === state.hull
            && origin.x === state.origin.x && origin.y === state.origin.y
            && size.width === state.size.width && size.height === state.size.height
            && Object.keys(bounds).every(side => bounds[side] === state.bounds[side])) return;
        if (body && !body.dragging) {
            state.x = body.x; state.y = body.y;
            state.vx = clampSpeed(body.vx); state.vy = clampSpeed(body.vy);
        }
        state.x += state.origin.x - origin.x;
        state.y += state.origin.y - origin.y;
        Object.assign(state, { origin, size, bounds, hull,
            windowKey: '', grounded: false, scene, sceneSignature: signature });
        state.borders = buildBorders(state.scene, '');
        // Rebind the existing body rather than cancelling and rebuilding it:
        // positions, velocity, squash and the simulation clock survive closure.
        adoptWindow(state);
        if (paused(state)) return;
        // Remove the old box before advancing. Otherwise a late native update
        // can still bounce the cat off a window that has already disappeared.
        collide(state);
        render(state);
        advancePhysics(state, now());
        render(state);
        requestFrame(state);
    }

    function updateBounds(state, result) {
        const sampleAt = now();
        // Time spent at rest belongs to the old support. It must not become
        // accumulated falling time when that support is removed or replaced.
        if (state.grounded && state.vx === 0 && state.squash === 0 && !state.frame) state.lastStepAt = sampleAt;
        const scene = sceneFor(result);
        let target = scene.find(item => item.key === state.windowKey && item.collisionOnly !== true);
        if (!target) { continueAfterWindowClosed(state, result); return; }
        const size = rect(state.container.getBoundingClientRect());
        // One snapshot has to correct the cat and measure every window. The
        // old origin would otherwise miss the front window after the pet moves.
        // Physics still steps in the previous frame; only the containment probe
        // uses the corrected position.
        const origin = screenOrigin();
        const originDx = state.origin.x - origin.x;
        const originDy = state.origin.y - origin.y;
        const probe = originDx || originDy
            ? { ...state, x: state.x + originDx, y: state.y + originDy }
            : state;
        // Scene order is front to back. A window raised over the cat becomes
        // the container; the buried window must not keep the floor.
        const front = paused(state) ? null : containerAt(scene, probe, size, origin);
        const ownerChanged = !!(front && front.target.key !== state.windowKey);
        if (ownerChanged) {
            target = front.target;
            state.windowKey = target.key;
        }
        const next = ownerChanged ? front.bounds : boundsFor(target.rect, size, getBody(state.button), origin);
        if (!next || !isCat(state.button, state.container) || isReturning(state.button)) {
            cancel(state.button);
            return;
        }
        const elapsed = (sampleAt - state.lastSampleAt) / 1000;
        state.lastSampleAt = sampleAt;
        state.revision = result.revision;
        const body = getBody(state.button);
        const hull = body && body.visibleInsetRatios || insets;
        const hullChanged = hull !== state.hull;
        const coordinateChange = origin.x !== state.origin.x || origin.y !== state.origin.y
            || size.width !== state.size.width || size.height !== state.size.height || hullChanged;
        const previous = state.bounds;
        const signature = sceneSignature(scene);
        const sceneChanged = signature !== state.sceneSignature || coordinateChange;
        const geometryChanged = coordinateChange || Object.keys(next).some((side) => next[side] !== previous[side]);
        if (sceneChanged) {
            const remainingKeys = new Set(scene.map(item => item.key));
            const borders = state.borders.filter(edge => remainingKeys.has(edge.key));
            if (borders.length !== state.borders.length) {
                state.borders = borders;
                if (!paused(state)) {
                    collide(state);
                    if (body) body.grounded = state.grounded;
                }
            }
        }
        // Account for elapsed motion against the previous walls before applying
        // a fresh sample. Resetting lastStepAt here without stepping discards
        // most falling time when window updates arrive as often as RAFs.
        if (hullChanged && body && !body.dragging) {
            // The Playground already anchored the new art to the same support.
            // Do not integrate that corrected position against the old hull.
            state.x = body.x;
            state.y = body.y;
            state.vx = clampSpeed(body.vx);
            state.vy = clampSpeed(body.vy);
            state.grounded = body.grounded;
            state.lastStepAt = sampleAt;
        } else if ((geometryChanged || sceneChanged) && !paused(state)) advancePhysics(state, sampleAt);
        if (originDx || originDy) {
            state.x += originDx;
            state.y += originDy;
            const alignedBody = body && !body.dragging ? body : null;
            if (alignedBody) {
                if (Number.isFinite(alignedBody.x)) alignedBody.x += originDx;
                if (Number.isFinite(alignedBody.y)) alignedBody.y += originDy;
            }
        }
        state.origin = origin;
        state.size = size;
        state.hull = hull;
        state.bounds = next;
        const previousScene = state.scene;
        state.scene = scene;
        state.sceneSignature = signature;
        if (sceneChanged || ownerChanged) state.borders = buildBorders(scene, state.windowKey);
        if (paused(state)) return;
        if (!geometryChanged && !sceneChanged) return;
        const walls = {};
        for (const side of Object.keys(next)) {
            // A delayed reader or monitor/DPI jump is a bounds correction, never
            // a huge throw. Repeated unchanged samples cannot inject energy.
            walls[side] = !ownerChanged && !coordinateChange && elapsed >= 1 / 240 && elapsed <= MAX_SAMPLE_SECONDS
                ? clampSpeed((next[side] - previous[side]) / elapsed) : 0;
        }
        if (state.grounded && geometryChanged && !ownerChanged) {
            const floorVx = (walls.left + walls.right) / 2;
            state.vx = clampSpeed(state.vx + (floorVx - state.vx) * 0.45);
        }
        const before = { x: state.x, y: state.y };
        collide(state, walls);
        collideBorders(state, before, elapsed, coordinateChange ? null : previousScene, !coordinateChange);
        render(state);
        requestFrame(state);
    }

    function handleSensingResult(result) {
        if (disposed) return false;
        if (result && latest && result.sessionId === latest.sessionId
            && Number(result.revision) <= latest.revision) return false;
        const pendingRead = result && result.status === 'pending' && result.reason === 'busy';
        const noWindows = result && result.status === 'unavailable'
            && ['no-window', 'no-window-on-model-display'].includes(result.reason);
        if (!result || !result.sessionId || !Number.isFinite(Number(result.revision))
            || (!pendingRead && !noWindows
                && (!['current', 'changed'].includes(result.status) || !rect(result.rect)))) {
            latest = null;
            if (result && result.sessionId) sceneUnavailable = true;
            clearPendingAdmission();
            cancel(null);
            releaseUnsupportedDesktopCats();
            return false;
        }
        if (pendingRead) {
            // Keep the explicit drag/release fact. The native reader is busy,
            // not empty; the eventual scene or no-window result decides it.
            if (!current) latest = null;
            sceneUnavailable = false;
            return false;
        }
        // A first gravity sample can legitimately report no windows while the
        // native enumeration is still in flight. Keep an explicit drag handoff
        // alive until a real scene arrives; consuming it here makes entry
        // timing-dependent and strands the cat outside the window.
        if (noWindows) {
            sceneUnavailable = true;
            if (current) {
                // An existing owner must keep its physical continuation when
                // the supporting window disappears. Only an unowned drag
                // treats the same sample as an in-flight read placeholder.
                latest = result;
                updateBounds(current, result);
            } else {
                latest = null;
                // A completed release cannot be replayed against a window
                // that appears later. An active drag remains eligible until
                // its own release event arrives.
                if (!pendingAdmission || pendingAdmission.kind !== 'drag') {
                    clearPendingAdmission(false);
                }
                // The desktop cat was never admitted. A minimized window must
                // not leave it grounded on the floor that just disappeared.
                releaseUnsupportedDesktopCats();
            }
            return false;
        }
        // Playground gravity requires the scene-capable session. A legacy
        // focused-window result must not be silently converted into a fake
        // gravity container.
        if (!noWindows && !Array.isArray(result.windows)) {
            latest = null;
            clearPendingAdmission();
            cancel(null);
            releaseUnsupportedDesktopCats();
            return false;
        }
        const previousScene = latest && Array.isArray(latest.windows) ? sceneFor(latest) : null;
        latest = result;
        sceneUnavailable = false;
        const state = current;
        if (!state) {
            attemptPendingAdmission();
            collideFreeDesktopCats(previousScene);
            releaseUnsupportedDesktopCats();
            return false;
        }
        if (state.sessionId !== result.sessionId
            || (!Array.isArray(result.windows) && Array.isArray(result.changes) && result.changes.includes('identity'))) {
            cancel(state.button);
            return false;
        }
        updateBounds(state, result);
        return false;
    }

    function recordDragPoint(drag, detail) {
        _recordNekoIdleDragSample(drag, detail);
    }

    function releaseVelocity(drag, detail) {
        if (!drag || !drag.moved || detail.reason !== 'return-ball-drag-end' || detail.dragCancelled) return null;
        // Completion can wait for RAFs or native viewport restoration. Measure
        // the throw at pointer release, not when that asynchronous work finishes.
        const releasedAt = Number.isFinite(detail.releasedAt) ? detail.releasedAt : Date.now();
        return _getNekoIdleDragReleaseVelocity(drag, releasedAt, false);
    }

    function reconcilePausedOwner(state) {
        if (!state || !Array.isArray(state.scene)) return false;
        const body = getBody(state.button);
        const probe = body && !body.dragging ? { ...state, x: body.x, y: body.y } : state;
        const front = containerAt(state.scene, probe, state.size);
        if (!front || front.target.key === state.windowKey) return false;
        state.windowKey = front.target.key;
        state.bounds = front.bounds;
        state.borders = buildBorders(state.scene, state.windowKey);
        if (body && !body.dragging) {
            body.floorY = state.bounds.bottom;
            body.wallLeft = state.bounds.left;
            body.wallRight = state.bounds.right;
        }
        return true;
    }

    function handleManualMove(event) {
        const detail = event && event.detail;
        if (!detail || !detail.container) return;
        const { container, reason } = detail;
        let drag = dragMotions.get(container);
        if (reason === 'return-ball-drag-start') {
            drag = { ..._createNekoIdleDragSampleSession(), sessionId: detail.dragSessionId };
            dragMotions.set(container, drag);
            recordDragPoint(drag, detail);
        } else if (drag && drag.sessionId !== detail.dragSessionId) {
            return;
        }
        if (reason === 'return-ball-drag-motion') {
            if (drag) {
                drag.moved = true;
                recordDragPoint(drag, detail);
            }
            return;
        }
        if (reason === 'return-ball-drag-end' || reason === 'return-ball-drag-cancel') {
            const velocity = releaseVelocity(drag, detail);
            dragMotions.delete(container);
            if (!current && reason === 'return-ball-drag-end' && drag && drag.moved && !detail.dragCancelled) {
                requestContainerAdmission(container);
            }
            const state = current;
            if (!state || state.container !== container) return;
            state.pointerHeld = false;
            state.lastStepAt = now();
            // Playground already supplies its own pointer-release impulse.
            if (velocity && !getBody(state.button)) {
                Object.assign(state, velocity, { grounded: false });
            }
            // A window raised while the press froze owner selection is already
            // in the committed scene. Adopt it before the next sample arrives.
            reconcilePausedOwner(state);
            // Also reconcile a box moved during a stationary press, without
            // treating the accumulated displacement as a shake.
            collide(state);
            render(state);
            requestFrame(state);
            return;
        }
        const state = current;
        if (!state || container !== state.container) return;
        if (reason === 'return-ball-drag-start') {
            state.pointerHeld = true;
            if (state.frame) window.cancelAnimationFrame(state.frame);
            state.frame = 0;
            publishMovementState(state);
        } else if (reason === 'return-ball-drag-active') {
            cancel(state.button);
        }
    }

    function handlePlayground(event) {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
        const wasActive = playgroundActive;
        playgroundActive = detail.active === true;
        if (!playgroundActive) {
            latest = null;
            sceneUnavailable = false;
            dragMotions = new WeakMap();
            clearPendingAdmission(false);
            cancel(null);
            return;
        }
        if (!wasActive) {
            // Drop any legacy rect delivered before the sensing owner switches
            // the session to gravity; wait for the first gravity scene result.
            latest = null;
            sceneUnavailable = false;
            dragMotions = new WeakMap();
            clearPendingAdmission(false);
            if (current) cancel(null);
            return;
        }
        if (current && latest) updateBounds(current, latest);
        else attemptPendingAdmission();
    }

    function handleLifecycle() {
        if (_getNekoGoodbyeIdleAppearance() !== _NEKO_GOODBYE_IDLE_APPEARANCE_CAT || !playgroundActive) {
            dragMotions = new WeakMap();
        }
        if (_getNekoGoodbyeIdleAppearance() !== _NEKO_GOODBYE_IDLE_APPEARANCE_CAT) clearPendingAdmission(false);
        if (current && (!playgroundActive || !isCat(current.button, current.container)
            || isReturning(current.button))) cancel(null);
    }

    // A pinned app panel also blocks ordinary desktop walks. This is a position
    // constraint only: no gravity body, animation change, velocity or RAF starts.
    function constrainObstaclePosition(container, left, top) {
        if (current || disposed || !latest || ![left, top].every(Number.isFinite)) return null;
        const scene = sceneFor(latest);
        const obstacles = new Set(scene.filter(item => item.collisionOnly === true).map(item => item.key));
        if (!obstacles.size) return null;
        const button = [...document.querySelectorAll('.neko-idle-return-btn')]
            .find(item => _getNekoIdleReturnContainerFromButton(item) === container);
        if (!isCat(button, container) || isReturning(button) || getBody(button)
            || ['true', 'pending'].includes(container.getAttribute('data-dragging'))) return null;
        const size = rect(container.getBoundingClientRect());
        if (!size) return null;
        const probe = { size, hull: insets, x: left, y: top, vx: 0, vy: 0, reduceMotion: true,
            bounds: { left: -Infinity, right: Infinity, top: -Infinity, bottom: Infinity },
            borders: buildBorders(scene, '').filter(edge => obstacles.has(edge.key)) };
        collideBorders(probe, size, 0);
        return { left: probe.x, top: probe.y, distance: Math.hypot(probe.x - size.x, probe.y - size.y) };
    }

    function applyPosition(container, left, top) {
        const state = current;
        if (!state) {
            const position = constrainObstaclePosition(container, left, top);
            if (!position) return false;
            Object.assign(container.style, { left: `${position.left}px`, top: `${position.top}px`,
                right: '', bottom: '', transform: 'none' });
            return true;
        }
        if (!state || state.container !== container || paused(state) || getBody(state.button)) return false;
        advancePhysics(state, now());
        const previous = { x: state.x, y: state.y };
        if (canWalk(state.button) && Number.isFinite(left)) state.x = left;
        collide(state);
        collideBorders(state, previous, 1 / 60);
        render(state);
        if (!state.grounded || state.vx !== 0 || state.squash !== 0) requestFrame(state);
        return true;
    }

    function constrainTarget(container, target) {
        const state = current;
        if (!state && target) {
            const position = constrainObstaclePosition(container, target.left, target.top);
            return position ? { ...target, ...position } : target;
        }
        if (!state || state.container !== container || !target || paused(state)) return target;
        const targetLeft = Number(target.left);
        const targetTop = Number(target.top);
        const probe = {
            ...state,
            x: Math.max(state.bounds.left, Math.min(state.bounds.right, Number.isFinite(targetLeft) ? targetLeft : state.x)),
            y: Math.max(state.bounds.top, Math.min(state.bounds.bottom, Number.isFinite(targetTop) ? targetTop : state.y)),
            vx: 0,
            vy: 0,
            grounded: false,
            squash: 0,
        };
        collideBorders(probe, state, 0);
        return {
            ...target,
            left: probe.x,
            top: probe.y,
            distance: Math.hypot(probe.x - state.x, probe.y - state.y),
        };
    }

    // Playground body collisions and throws retain their impulses; its normal
    // gravity integrator is skipped for this body while window physics owns it.
    function stepBody(button, body, timestamp) {
        if (!playgroundActive) return false;
        const state = current;
        if (!state || state.button !== button || body !== getBody(button) || paused(state)) return false;
        if (body.visibleInsetRatios && body.visibleInsetRatios !== state.hull && latest) {
            updateBounds(state, latest);
        }
        const previous = { x: state.x, y: state.y };
        const elapsed = Math.max(0, (timestamp - state.lastStepAt) / 1000);
        state.x = body.x; state.y = body.y;
        state.vx = clampSpeed(body.vx); state.vy = clampSpeed(body.vy);
        state.grounded = body.grounded;
        advancePhysics(state, timestamp);
        collide(state);
        collideBorders(state, previous, elapsed);
        render(state, { fromPlayground: true });
        finishDesktopLanding(state);
        return true;
    }

    function syncBody(body) {
        const state = current;
        if (!state || body !== getBody(state.button)) return false;
        if (body.dragging) return false;
        if (body.visibleInsetRatios && body.visibleInsetRatios !== state.hull && latest) {
            updateBounds(state, latest);
        }
        const previous = { x: state.x, y: state.y };
        state.x = body.x; state.y = body.y;
        state.vx = clampSpeed(body.vx); state.vy = clampSpeed(body.vy);
        collide(state);
        collideBorders(state, previous, 1 / 60);
        render(state);
        if (!state.grounded || state.vx !== 0 || state.squash !== 0) requestFrame(state);
        return true;
    }

    // A cat resting on the desktop still meets window edges. Crossing one
    // bounces the body; it does not hand the cat to that window.
    function collideDesktopBody(button, body, dt, previous, previousScene = null) {
        if (!playgroundActive || disposed || !body || body.dragging || body.id !== 'cat') return false;
        if (current && current.button === button) return false;
        if (!latest || !Array.isArray(latest.windows)) return false;
        const scene = sceneFor(latest);
        if (!scene.length) return false;
        const width = Number(body.width);
        const height = Number(body.height);
        if (!(width > 0) || !(height > 0)) return false;
        const before = previous && Number.isFinite(Number(previous.x)) && Number.isFinite(Number(previous.y))
            ? { x: Number(previous.x), y: Number(previous.y) }
            : { x: body.x, y: body.y };
        const probe = {
            x: body.x,
            y: body.y,
            vx: Number(body.vx) || 0,
            vy: Number(body.vy) || 0,
            size: { width, height },
            hull: body.visibleInsetRatios || insets,
            borders: buildBorders(scene, null),
            bounds: { left: -Infinity, right: Infinity, top: -Infinity, bottom: Infinity },
            grounded: !!body.grounded,
            reduceMotion: !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches),
        };
        const elapsed = Number.isFinite(Number(dt)) && Number(dt) > 0 ? Number(dt) : 1 / 60;
        collideBorders(probe, before, elapsed, previousScene, !!previousScene);
        const hit = probe.x !== body.x || probe.y !== body.y || probe.vx !== body.vx || probe.vy !== body.vy;
        if (!hit) return false;
        body.x = probe.x;
        body.y = probe.y;
        body.vx = probe.vx;
        body.vy = probe.vy;
        if (probe.grounded) body.grounded = true;
        return true;
    }

    function supportedByScene(body, scene) {
        const width = Number(body.width);
        const height = Number(body.height);
        if (!(width > 0) || !(height > 0)) return false;
        const hull = body.visibleInsetRatios || insets;
        const foot = body.y + height * (1 - hull.bottom);
        const left = body.x + width * hull.left;
        const right = body.x + width * (1 - hull.right);
        return buildBorders(scene, null).some(edge => edge.axis === 'y'
            && Math.abs(foot - edge.line) <= CONTACT_EPSILON
            && right > edge.start && left < edge.end);
    }

    // A desktop cat can rest on a window without being admitted into it.
    // Minimizing that window removes the edge; grounded must not keep the cat
    // suspended above the work-area floor.
    function releaseUnsupportedDesktopCats() {
        if (!playgroundActive || current || typeof document === 'undefined'
            || typeof document.querySelectorAll !== 'function') return;
        const scene = latest && Array.isArray(latest.windows) ? sceneFor(latest) : [];
        document.querySelectorAll('.neko-idle-return-btn').forEach((button) => {
            const body = getBody(button);
            if (!body || !body.grounded) return;
            releaseUnsupportedDesktopCat(button, body, scene);
        });
    }

    function releaseUnsupportedDesktopCat(button, body, scene) {
        const floor = Number(body.floorY);
        const unsupported = Number.isFinite(floor) && body.y < floor - CONTACT_EPSILON
            && !supportedByScene(body, scene);
        if (!unsupported) {
            lostDesktopSupport.delete(body);
            return;
        }
        if (body.dragging) {
            lostDesktopSupport.add(body);
            return;
        }
        lostDesktopSupport.delete(body);
        body.grounded = false;
        if (typeof _startNekoIdleCat1PlaygroundPhysics === 'function') {
            _startNekoIdleCat1PlaygroundPhysics(button);
        }
    }

    function releaseDeferredDesktopSupport(button, body) {
        if (!body || body.dragging || !lostDesktopSupport.has(body)) return;
        lostDesktopSupport.delete(body);
        if (!body.grounded) return;
        const scene = latest && Array.isArray(latest.windows) ? sceneFor(latest) : [];
        const floor = Number(body.floorY);
        if (!Number.isFinite(floor) || body.y >= floor - CONTACT_EPSILON) return;
        if (supportedByScene(body, scene)) return;
        body.grounded = false;
        if (typeof _startNekoIdleCat1PlaygroundPhysics === 'function') {
            _startNekoIdleCat1PlaygroundPhysics(button);
        }
    }

    function collideFreeDesktopCats(previousScene) {
        if (!playgroundActive || current || typeof document === 'undefined'
            || typeof document.querySelectorAll !== 'function') return;
        const sampleAt = now();
        const elapsed = lastFreeDesktopSceneAt > 0 ? (sampleAt - lastFreeDesktopSceneAt) / 1000 : 0;
        lastFreeDesktopSceneAt = sampleAt;
        document.querySelectorAll('.neko-idle-return-btn').forEach((button) => {
            const body = getBody(button);
            if (!body || body.dragging) return;
            const before = { x: body.x, y: body.y };
            if (!collideDesktopBody(button, body, elapsed, before, previousScene)) return;
            if (typeof _setNekoIdleCat1PlaygroundBodyPosition === 'function') {
                _setNekoIdleCat1PlaygroundBodyPosition(body, body.x, body.y, { force: true });
            }
            if (typeof _startNekoIdleCat1PlaygroundPhysics === 'function') {
                _startNekoIdleCat1PlaygroundPhysics(button);
            }
        });
    }

    function releaseBody(button, body, options = {}) {
        if (!playgroundActive || body !== getBody(button)) return false;
        if (current && current.button === button
            && (!current.windowKey || !contains(current.bounds, body))) {
            // The body was free during the gesture. Drop the old owner before
            // evaluating the final release point, otherwise collide() snaps
            // an out-of-window release back into the stale box.
            cancel(button);
        }
        if (!current && options.admit === true) {
            requestAdmission(button, body);
        } else if (options.admit !== true) {
            if (pendingAdmission && pendingAdmission.body === body) clearPendingAdmission(false);
        }
        if (current && current.button === button) {
            current.lastStepAt = now();
            reconcilePausedOwner(current);
        } else if (!current) {
            releaseDeferredDesktopSupport(button, body);
        }
        return syncBody(body);
    }

    function getBounds(container) {
        return current && current.container === container ? { ...current.bounds } : null;
    }

    function isDesktopSupported(body) {
        if (!body || !latest || !Array.isArray(latest.windows)) return null;
        return supportedByScene(body, sceneFor(latest));
    }

    function isActive(button) {
        return !!(current && (!button || current.button === button));
    }

    function getState() {
        if (!current) return Object.freeze({ phase: 'idle', targetKind: KIND });
        return Object.freeze({
            phase: current.grounded ? 'resting' : 'falling', targetKind: KIND,
            sessionId: current.sessionId, revision: current.revision,
            windowKey: current.windowKey,
            x: current.x, y: current.y, vx: current.vx, vy: current.vy,
            bounds: Object.freeze({ ...current.bounds }), pointerHeld: current.pointerHeld,
        });
    }

    window.NekoDesktopWindowGravity = Object.freeze({
        isActive, isMoving, cancel, getState, applyPosition, constrainTarget, stepBody, syncBody, releaseBody, dragBody, getBounds,
        collideDesktopBody, isDesktopSupported,
        requestAdmission,
        canWalk, usesContinuousDrag,
        createDragSampleSession: _createNekoIdleDragSampleSession,
        recordDragSample: _recordNekoIdleDragSample,
        getReleaseVelocity: _getNekoIdleDragReleaseVelocity,
    });
    const unsubscribe = sensingContext.subscribe(handleSensingResult);
    handleSensingResult(sensingContext.getCurrent());
    window.addEventListener(MANUAL_MOVE_EVENT, handleManualMove);
    window.addEventListener(PLAYGROUND_EVENT, handlePlayground);
    window.addEventListener('neko:auto-goodbye:state-change', handleLifecycle);
    window.addEventListener('neko:cat-return-commit', cancelAll);
    window.addEventListener('neko:goodbye-state-cleared', cancelAll);
    window.addEventListener('pagehide', dispose);
    window.addEventListener('beforeunload', dispose);

    function cancelAll() {
        dragMotions = new WeakMap();
        sceneUnavailable = false;
        clearPendingAdmission(false);
        cancel(null);
    }

    function dispose() {
        if (disposed) return;
        disposed = true;
        cancelAll();
        latest = null;
        unsubscribe();
        window.removeEventListener(MANUAL_MOVE_EVENT, handleManualMove);
        window.removeEventListener(PLAYGROUND_EVENT, handlePlayground);
        window.removeEventListener('neko:auto-goodbye:state-change', handleLifecycle);
        window.removeEventListener('neko:cat-return-commit', cancelAll);
        window.removeEventListener('neko:goodbye-state-cleared', cancelAll);
        window.removeEventListener('pagehide', dispose);
        window.removeEventListener('beforeunload', dispose);
        delete window.NekoDesktopWindowGravity;
    }
})();
