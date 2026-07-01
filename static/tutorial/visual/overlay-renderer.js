(function (root, factory) {
    'use strict';

    const api = factory();
    const TutorialOverlayRenderer = api.TutorialOverlayRenderer;
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialOverlayRenderer = TutorialOverlayRenderer;
        root.TutorialOverlayRendererApi = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const DEFAULT_CURSOR_CLICK_VISIBLE_MS = 420;
    const PC_OVERLAY_CURSOR_EASE = Object.freeze([0.22, 1, 0.36, 1]);

    function sampleCubicBezier(progress, x1, y1, x2, y2) {
        const targetX = Math.max(0, Math.min(1, Number(progress) || 0));
        const ax = 3 * x1 - 3 * x2 + 1;
        const bx = -6 * x1 + 3 * x2;
        const cx = 3 * x1;
        const ay = 3 * y1 - 3 * y2 + 1;
        const by = -6 * y1 + 3 * y2;
        const cy = 3 * y1;
        let low = 0;
        let high = 1;
        let t = targetX;
        for (let index = 0; index < 12; index += 1) {
            t = (low + high) / 2;
            const x = ((ax * t + bx) * t + cx) * t;
            if (x < targetX) {
                low = t;
            } else {
                high = t;
            }
        }
        return Math.max(0, Math.min(1, ((ay * t + by) * t + cy) * t));
    }

    function easePcOverlayCursorProgress(progress) {
        return sampleCubicBezier(
            progress,
            PC_OVERLAY_CURSOR_EASE[0],
            PC_OVERLAY_CURSOR_EASE[1],
            PC_OVERLAY_CURSOR_EASE[2],
            PC_OVERLAY_CURSOR_EASE[3]
        );
    }

    function withoutTransientCursorEffect(cursor) {
        if (!cursor) {
            return null;
        }
        const nextCursor = Object.assign({}, cursor);
        delete nextCursor.effect;
        delete nextCursor.effectDurationMs;
        return nextCursor;
    }

    function getCursorEffectDurationMs(cursor, defaultClickVisibleMs) {
        if (!cursor || cursor.visible === false) {
            return 0;
        }
        const effect = typeof cursor.effect === 'string' ? cursor.effect : '';
        if (effect !== 'click' && effect !== 'wobble') {
            return 0;
        }
        const effectDurationMs = Number.isFinite(cursor.effectDurationMs)
            ? Math.max(0, Math.floor(cursor.effectDurationMs))
            : 0;
        if (effectDurationMs > 0) {
            return effectDurationMs;
        }
        return effect === 'click'
            ? Math.max(0, Math.floor(Number(defaultClickVisibleMs) || DEFAULT_CURSOR_CLICK_VISIBLE_MS))
            : 2000;
    }

    function createPcOverlayCompleteStateStore(options) {
        const normalizedOptions = options || {};
        const now = typeof normalizedOptions.now === 'function'
            ? normalizedOptions.now
            : () => Date.now();
        const defaultCursorClickVisibleMs = Number.isFinite(Number(normalizedOptions.defaultCursorClickVisibleMs))
            ? Math.max(0, Math.floor(Number(normalizedOptions.defaultCursorClickVisibleMs)))
            : DEFAULT_CURSOR_CLICK_VISIBLE_MS;
        let currentSpotlights = [];
        let currentCursor = null;
        let currentCursorEffectPayload = null;
        let currentCursorEffectSuppressUntil = 0;
        let currentPetal = null;

        function hasOwn(value, key) {
            return !!value && Object.prototype.hasOwnProperty.call(value, key);
        }

        function isCursorEffectActive() {
            return currentCursorEffectSuppressUntil > 0 && now() < currentCursorEffectSuppressUntil;
        }

        function applyPatch(patch) {
            const hasCursor = hasOwn(patch, 'cursor');
            const hasPetal = hasOwn(patch, 'petal');
            if (hasOwn(patch, 'spotlights')) {
                currentSpotlights = Array.isArray(patch.spotlights) ? patch.spotlights : [];
            }
            if (hasCursor) {
                currentCursor = withoutTransientCursorEffect(patch.cursor);
                const cursorEffectDurationMs = getCursorEffectDurationMs(patch.cursor, defaultCursorClickVisibleMs);
                if (cursorEffectDurationMs > 0) {
                    currentCursorEffectPayload = patch.cursor || null;
                    currentCursorEffectSuppressUntil = now() + cursorEffectDurationMs;
                } else {
                    currentCursorEffectPayload = null;
                    currentCursorEffectSuppressUntil = 0;
                }
            }
            if (hasPetal) {
                currentPetal = patch.petal || null;
            }
            const payload = {};
            if (hasOwn(patch, 'spotlights') || currentSpotlights.length > 0) {
                payload.spotlights = currentSpotlights;
            }
            if (hasCursor) {
                payload.cursor = patch.cursor || null;
            } else if (currentCursorEffectPayload && isCursorEffectActive()) {
                payload.cursor = currentCursorEffectPayload;
            } else if (currentCursor && !isCursorEffectActive()) {
                payload.cursor = currentCursor;
            }
            if (currentPetal || hasPetal) {
                payload.petal = currentPetal;
            }
            return payload;
        }

        function reset() {
            currentSpotlights = [];
            currentCursor = null;
            currentCursorEffectPayload = null;
            currentCursorEffectSuppressUntil = 0;
            currentPetal = null;
        }

        return {
            applyPatch,
            reset,
            clearCursorCache() {
                currentCursor = null;
                currentCursorEffectPayload = null;
                currentCursorEffectSuppressUntil = 0;
            },
            getPetal() {
                return currentPetal;
            }
        };
    }

    class OverlayCursorStateStore {
        constructor(options) {
            const normalizedOptions = options || {};
            this.now = typeof normalizedOptions.now === 'function'
                ? normalizedOptions.now
                : () => {
                    if (typeof performance !== 'undefined' && typeof performance.now === 'function') {
                        return performance.now();
                    }
                    return Date.now();
                };
            this.setTimeout = typeof normalizedOptions.setTimeout === 'function'
                ? normalizedOptions.setTimeout
                : (callback, delayMs) => {
                    const root = typeof window !== 'undefined' ? window : globalThis;
                    return root.setTimeout(callback, delayMs);
                };
            this.clearTimeout = typeof normalizedOptions.clearTimeout === 'function'
                ? normalizedOptions.clearTimeout
                : (timerId) => {
                    const root = typeof window !== 'undefined' ? window : globalThis;
                    root.clearTimeout(timerId);
                };
            this.position = null;
            this.visible = false;
            this.motion = null;
        }

        hasPosition() {
            return !!this.position;
        }

        isVisible() {
            return !!this.visible;
        }

        getPosition() {
            if (!this.position) {
                return null;
            }
            return {
                x: this.position.x,
                y: this.position.y
            };
        }

        getRawPosition() {
            return this.position;
        }

        setRawPosition(position) {
            this.position = position;
        }

        getRawVisible() {
            return this.visible;
        }

        setRawVisible(visible) {
            this.visible = !!visible;
        }

        getRawMotion() {
            return this.motion;
        }

        setRawMotion(motion) {
            this.motion = motion || null;
        }

        syncPosition(x, y, visible) {
            const normalizedX = Number(x);
            const normalizedY = Number(y);
            if (!Number.isFinite(normalizedX) || !Number.isFinite(normalizedY)) {
                return false;
            }
            this.updateMotion();
            this.finishMotion(false);
            this.position = { x: normalizedX, y: normalizedY };
            this.visible = visible !== false;
            return true;
        }

        clear() {
            this.finishMotion(false);
            this.position = null;
            this.visible = false;
        }

        markVisible() {
            this.visible = true;
        }

        finishMotion(completed) {
            const motion = this.motion;
            if (!motion) {
                return;
            }
            this.motion = null;
            if (motion.timerId) {
                this.clearTimeout(motion.timerId);
                motion.timerId = 0;
            }
            if (completed) {
                this.position = { x: motion.endX, y: motion.endY };
                this.visible = true;
            }
            if (typeof motion.resolve === 'function') {
                motion.resolve(completed !== false);
            }
        }

        updateMotion(now) {
            const motion = this.motion;
            if (!motion) {
                return null;
            }
            const currentNow = Number.isFinite(Number(now)) ? Number(now) : this.now();
            if (motion.pauseCheck && motion.pauseCheck()) {
                if (!motion.pausedAt) {
                    motion.pausedAt = currentNow;
                }
                return this.position;
            }
            if (motion.pausedAt) {
                motion.pausedTotalMs += Math.max(0, currentNow - motion.pausedAt);
                motion.pausedAt = 0;
            }
            const progress = motion.durationMs <= 0
                ? 1
                : Math.max(0, Math.min(1, (currentNow - motion.startedAt - motion.pausedTotalMs) / motion.durationMs));
            const easedProgress = easePcOverlayCursorProgress(progress);
            this.position = {
                x: motion.startX + ((motion.endX - motion.startX) * easedProgress),
                y: motion.startY + ((motion.endY - motion.startY) * easedProgress)
            };
            this.visible = true;
            if (progress >= 1) {
                this.finishMotion(true);
            }
            return this.position;
        }

        scheduleMotionTick() {
            const motion = this.motion;
            if (!motion) {
                return;
            }
            if (motion.timerId) {
                this.clearTimeout(motion.timerId);
                motion.timerId = 0;
            }
            motion.timerId = this.setTimeout(() => {
                this.tickMotion(motion);
            }, 48);
        }

        tickMotion(expectedMotion) {
            const activeMotion = this.motion;
            if (!activeMotion || (expectedMotion && activeMotion !== expectedMotion)) {
                return;
            }
            if (activeMotion.cancelCheck && activeMotion.cancelCheck()) {
                this.finishMotion(false);
                return;
            }
            this.updateMotion(this.now());
            if (this.motion === activeMotion) {
                this.scheduleMotionTick();
            }
        }

        animateTo(x, y, durationMs, options) {
            const normalizedOptions = options || {};
            const pauseCheck = typeof normalizedOptions.pauseCheck === 'function'
                ? normalizedOptions.pauseCheck
                : null;
            const cancelCheck = typeof normalizedOptions.cancelCheck === 'function'
                ? normalizedOptions.cancelCheck
                : null;
            const startPoint = this.position;
            if (!startPoint) {
                this.position = { x: x, y: y };
                this.visible = true;
                return Promise.resolve(true);
            }

            const normalizedDurationMs = Math.max(0, Math.round(Number(durationMs) || 0));
            if (normalizedDurationMs <= 0) {
                this.finishMotion(false);
                this.position = { x: x, y: y };
                this.visible = true;
                return Promise.resolve(true);
            }

            this.finishMotion(false);
            return new Promise((resolve) => {
                this.motion = {
                    startX: startPoint.x,
                    startY: startPoint.y,
                    endX: x,
                    endY: y,
                    durationMs: normalizedDurationMs,
                    startedAt: this.now(),
                    pausedAt: 0,
                    pausedTotalMs: 0,
                    pauseCheck: pauseCheck,
                    cancelCheck: cancelCheck,
                    timerId: 0,
                    resolve: resolve
                };
                this.scheduleMotionTick();
            });
        }

        getSmoothShowDurationMs(x, y, smoothDurationMs) {
            if (!this.position || !this.isVisible()) {
                return 0;
            }
            const distance = Math.hypot(x - this.position.x, y - this.position.y);
            if (distance < 2) {
                return 0;
            }
            return Math.max(0, Math.round(Number(smoothDurationMs) || 0));
        }
    }

    class OverlaySpotlightStateStore {
        constructor() {
            this.persistent = null;
            this.action = null;
            this.secondaryAction = null;
            this.extra = [];
            this.highlightedElements = new Set();
            this.suppressed = false;
        }

        isValidTarget(element) {
            return !!element && typeof element.getBoundingClientRect === 'function';
        }

        isSuppressed() {
            return !!this.suppressed;
        }

        setSuppressed(active) {
            this.suppressed = active === true;
        }

        getRawPersistent() {
            return this.persistent;
        }

        setRawPersistent(element) {
            this.persistent = element || null;
        }

        getRawAction() {
            return this.action;
        }

        setRawAction(element) {
            this.action = element || null;
        }

        getRawSecondaryAction() {
            return this.secondaryAction;
        }

        setRawSecondaryAction(element) {
            this.secondaryAction = element || null;
        }

        getRawExtra() {
            return this.extra;
        }

        setRawExtra(elements) {
            this.extra = Array.isArray(elements) ? elements : [];
        }

        getRawHighlightedElements() {
            return this.highlightedElements;
        }

        setRawHighlightedElements(elements) {
            this.highlightedElements = elements instanceof Set ? elements : new Set();
        }

        setPersistent(element) {
            this.persistent = element || null;
            this.syncHighlightedElementClasses();
        }

        setAction(element) {
            this.action = element || null;
            this.secondaryAction = null;
            this.syncHighlightedElementClasses();
        }

        setSecondaryAction(element) {
            this.secondaryAction = element || null;
            this.syncHighlightedElementClasses();
        }

        setExtra(elements) {
            this.extra = (Array.isArray(elements) ? elements : [])
                .filter((element) => this.isValidTarget(element));
        }

        getExtraElements() {
            return this.extra.slice();
        }

        clearExtra() {
            this.extra = [];
        }

        clearAction() {
            this.action = null;
            this.secondaryAction = null;
            this.syncHighlightedElementClasses();
        }

        clearPersistent() {
            this.persistent = null;
            this.syncHighlightedElementClasses();
        }

        clearAll() {
            this.persistent = null;
            this.action = null;
            this.secondaryAction = null;
            this.extra = [];
            this.syncHighlightedElementClasses();
        }

        hasAny() {
            return !!(
                this.persistent
                || this.action
                || this.secondaryAction
                || this.extra.length > 0
            );
        }

        getTargets() {
            return {
                persistent: this.persistent,
                action: this.action,
                secondaryAction: this.secondaryAction,
                extra: this.extra.slice()
            };
        }

        syncHighlightedElementClasses() {
            const nextElements = new Set();
            if (this.persistent) {
                nextElements.add(this.persistent);
            }

            this.highlightedElements.forEach((element) => {
                if (!nextElements.has(element) && element && element.classList) {
                    element.classList.remove('yui-guide-chat-target');
                }
            });

            nextElements.forEach((element) => {
                if (!this.highlightedElements.has(element) && element && element.classList) {
                    element.classList.add('yui-guide-chat-target');
                }
            });

            this.highlightedElements = nextElements;
        }
    }

    class OverlaySpotlightDomRenderer {
        constructor(options) {
            const normalizedOptions = options || {};
            this.document = normalizedOptions.document || (typeof document !== 'undefined' ? document : null);
            this.backdropCutoutInset = Number.isFinite(Number(normalizedOptions.backdropCutoutInset))
                ? Math.max(0, Math.floor(Number(normalizedOptions.backdropCutoutInset)))
                : 4;
            this.defaultSpotlightPadding = Number.isFinite(Number(normalizedOptions.defaultSpotlightPadding))
                ? Math.max(0, Number(normalizedOptions.defaultSpotlightPadding))
                : 6;
            this.shouldSuppressDom = typeof normalizedOptions.shouldSuppressDom === 'function'
                ? normalizedOptions.shouldSuppressDom
                : () => false;
            this.getWindow = typeof normalizedOptions.getWindow === 'function'
                ? normalizedOptions.getWindow
                : () => {
                    if (this.document && this.document.defaultView) {
                        return this.document.defaultView;
                    }
                    return typeof window !== 'undefined' ? window : null;
                };
            this.isCircularElement = typeof normalizedOptions.isCircularElement === 'function'
                ? normalizedOptions.isCircularElement
                : () => false;
        }

        createElement(tagName, className) {
            const doc = this.document || (typeof document !== 'undefined' ? document : null);
            if (!doc || typeof doc.createElement !== 'function') {
                return null;
            }
            const element = doc.createElement(tagName);
            if (className) {
                element.className = className;
            }
            return element;
        }

        readSpotlightNumberAttr(element, attributeName) {
            if (!element || typeof element.getAttribute !== 'function' || !attributeName) {
                return null;
            }

            const rawValue = element.getAttribute(attributeName);
            const value = Number.parseFloat(rawValue || '');
            return Number.isFinite(value) ? value : null;
        }

        getSpotlightRadius(element, padding) {
            const radiusPadding = Number.isFinite(padding) ? padding : this.defaultSpotlightPadding;
            const radiusOverride = this.readSpotlightNumberAttr(element, 'data-yui-guide-spotlight-radius');
            if (radiusOverride != null) {
                return Math.max(0, radiusOverride);
            }

            const win = this.getWindow();
            if (!element || !win || typeof win.getComputedStyle !== 'function') {
                return 24;
            }

            try {
                const computed = win.getComputedStyle(element);
                const radius = parseFloat(computed.borderTopLeftRadius || computed.borderRadius || '');
                if (Number.isFinite(radius) && radius > 0) {
                    return Math.max(0, radius + radiusPadding);
                }
            } catch (_) {}

            return 24;
        }

        getSpotlightRect(element) {
            if (!element || typeof element.getBoundingClientRect !== 'function') {
                return null;
            }

            const rect = element.getBoundingClientRect();
            if (!rect || rect.width <= 0 || rect.height <= 0) {
                return null;
            }

            const win = this.getWindow() || {};
            const viewportWidth = Number.isFinite(Number(win.innerWidth)) ? Number(win.innerWidth) : rect.right;
            const viewportHeight = Number.isFinite(Number(win.innerHeight)) ? Number(win.innerHeight) : rect.bottom;
            const paddingValue = this.readSpotlightNumberAttr(element, 'data-yui-guide-spotlight-padding');
            const padding = paddingValue == null ? this.defaultSpotlightPadding : paddingValue;
            const geometryHint = typeof element.getAttribute === 'function'
                ? (element.getAttribute('data-yui-guide-spotlight-geometry') || '').trim().toLowerCase()
                : '';
            const left = Math.max(0, Math.floor(rect.left - padding));
            const top = Math.max(0, Math.floor(rect.top - padding));
            const right = Math.min(viewportWidth, Math.ceil(rect.right + padding));
            const bottom = Math.min(viewportHeight, Math.ceil(rect.bottom + padding));
            const radius = this.getSpotlightRadius(element, padding);

            return {
                left: left,
                top: top,
                right: right,
                bottom: bottom,
                width: Math.max(0, right - left),
                height: Math.max(0, bottom - top),
                radius: radius,
                padding: padding,
                isCircular: geometryHint === 'circle' || this.isCircularElement(element)
            };
        }

        getFrameVariantFromElement(element) {
            if (!element || typeof element.getAttribute !== 'function') {
                return '';
            }

            const variant = (element.getAttribute('data-yui-guide-spotlight-variant') || '').trim().toLowerCase();
            if (variant) {
                return variant;
            }

            const geometry = (element.getAttribute('data-yui-guide-spotlight-geometry') || '').trim().toLowerCase();
            if (geometry === 'circle' || this.isCircularElement(element)) {
                return 'circle-image';
            }
            return '';
        }

        resolveSpotlightTarget(element) {
            return {
                element: element || null,
                rect: this.getSpotlightRect(element),
                variant: this.getFrameVariantFromElement(element)
            };
        }

        resolveSpotlightTargets(targets) {
            const normalizedTargets = targets || {};
            const extraTargets = Array.isArray(normalizedTargets.extra) ? normalizedTargets.extra : [];
            return {
                persistent: this.resolveSpotlightTarget(normalizedTargets.persistent),
                action: this.resolveSpotlightTarget(normalizedTargets.action),
                secondaryAction: this.resolveSpotlightTarget(normalizedTargets.secondaryAction),
                extra: extraTargets.map((element) => this.resolveSpotlightTarget(element))
            };
        }

        buildPcSpotlights(resolvedTargets) {
            const targets = resolvedTargets || {};
            const pcSpotlights = [];
            const addSpotlight = (kind, target) => {
                if (!target || !target.rect) {
                    return;
                }
                pcSpotlights.push({
                    kind: kind,
                    rect: target.rect,
                    variant: target.variant || ''
                });
            };

            addSpotlight('persistent', targets.persistent);
            addSpotlight('primary', targets.action);
            addSpotlight('secondary', targets.secondaryAction);
            (Array.isArray(targets.extra) ? targets.extra : []).forEach((target) => {
                addSpotlight('extra', target);
            });
            return pcSpotlights;
        }

        hasAnySpotlightRect(resolvedTargets) {
            const targets = resolvedTargets || {};
            return !!(
                (targets.persistent && targets.persistent.rect)
                || (targets.action && targets.action.rect)
                || (targets.secondaryAction && targets.secondaryAction.rect)
                || (Array.isArray(targets.extra) && targets.extra.some((target) => !!(target && target.rect)))
            );
        }

        renderDomSpotlights(options) {
            const normalizedOptions = options || {};
            const targets = normalizedOptions.targets || {};
            const frames = normalizedOptions.frames || {};
            const cutouts = normalizedOptions.cutouts || {};
            const extraTargets = Array.isArray(targets.extra) ? targets.extra : [];
            const extraEntries = Array.isArray(normalizedOptions.extraEntries)
                ? normalizedOptions.extraEntries
                : [];
            const ensureExtraSpotlightEntry = typeof normalizedOptions.ensureExtraSpotlightEntry === 'function'
                ? normalizedOptions.ensureExtraSpotlightEntry
                : () => null;
            const persistentTarget = targets.persistent || {};
            const actionTarget = targets.action || {};
            const secondaryActionTarget = targets.secondaryAction || {};

            this.updateSpotlightFrame(frames.persistent, persistentTarget.rect || null, {
                allowMask: true,
                variant: persistentTarget.variant || ''
            });
            this.updateSpotlightFrame(frames.action, actionTarget.rect || null, {
                allowMask: true,
                variant: actionTarget.variant || ''
            });
            this.updateSpotlightFrame(frames.secondaryAction, secondaryActionTarget.rect || null, {
                allowMask: true,
                variant: secondaryActionTarget.variant || ''
            });
            this.updateBackdropCutout(cutouts.persistent, persistentTarget.rect || null);
            this.updateBackdropCutout(cutouts.action, actionTarget.rect || null);
            this.updateBackdropCutout(cutouts.secondaryAction, secondaryActionTarget.rect || null);

            extraTargets.forEach((target, index) => {
                const entry = ensureExtraSpotlightEntry(index);
                if (!entry) {
                    return;
                }
                const spotlightTarget = target || {};
                this.updateBackdropCutout(entry.cutout, spotlightTarget.rect || null);
                this.updateSpotlightFrame(entry.frame, spotlightTarget.rect || null, {
                    allowMask: true,
                    variant: spotlightTarget.variant || ''
                });
            });

            for (let index = extraTargets.length; index < extraEntries.length; index += 1) {
                const entry = extraEntries[index];
                if (!entry) {
                    continue;
                }
                this.updateBackdropCutout(entry.cutout, null);
                this.updateSpotlightFrame(entry.frame, null);
            }
        }

        ensureSpotlightFrameDecorations(frame) {
            if (!frame || typeof frame.querySelector !== 'function') {
                return;
            }

            if (!frame.querySelector('.yui-guide-spotlight-chrome')) {
                frame.appendChild(this.createElement('div', 'yui-guide-spotlight-chrome'));
            }
            if (!frame.querySelector('.yui-guide-spotlight-sweep')) {
                frame.appendChild(this.createElement('span', 'yui-guide-spotlight-sweep'));
            }
            if (!frame.querySelector('.yui-guide-spotlight-circle-skin')) {
                frame.appendChild(this.createElement('div', 'yui-guide-spotlight-circle-skin'));
            }
        }

        ensureSpotlightImageDecorations(frame) {
            if (!frame || typeof frame.querySelector !== 'function') {
                return;
            }

            if (!frame.querySelector('.yui-guide-spotlight-ear-left')) {
                frame.appendChild(this.createElement('div', 'yui-guide-spotlight-decoration yui-guide-spotlight-ear-left'));
            }
            if (!frame.querySelector('.yui-guide-spotlight-ear-right')) {
                frame.appendChild(this.createElement('div', 'yui-guide-spotlight-decoration yui-guide-spotlight-ear-right'));
            }
            if (!frame.querySelector('.yui-guide-spotlight-paw')) {
                frame.appendChild(this.createElement('div', 'yui-guide-spotlight-decoration yui-guide-spotlight-paw'));
            }
        }

        removeSpotlightImageDecorations(frame) {
            if (!frame || typeof frame.querySelectorAll !== 'function') {
                return;
            }

            frame.querySelectorAll(
                '.yui-guide-spotlight-ear-left, .yui-guide-spotlight-ear-right, .yui-guide-spotlight-paw'
            ).forEach((element) => {
                if (element && element.parentNode && typeof element.parentNode.removeChild === 'function') {
                    element.parentNode.removeChild(element);
                }
            });
        }

        applySpotlightFrameDecorationMode(frame, useCircleImage) {
            if (!frame || typeof frame.querySelector !== 'function') {
                return;
            }

            const chrome = frame.querySelector('.yui-guide-spotlight-chrome');
            const circleSkin = frame.querySelector('.yui-guide-spotlight-circle-skin');

            if (useCircleImage) {
                this.removeSpotlightImageDecorations(frame);
            } else {
                this.ensureSpotlightImageDecorations(frame);
            }

            if (chrome && chrome.style) {
                chrome.style.display = useCircleImage ? 'none' : '';
            }

            if (circleSkin && circleSkin.style) {
                circleSkin.style.display = useCircleImage ? 'block' : '';
            }
        }

        applySpotlightPlainCircleMode(frame) {
            if (!frame || typeof frame.querySelector !== 'function') {
                return;
            }

            this.removeSpotlightImageDecorations(frame);
            const chrome = frame.querySelector('.yui-guide-spotlight-chrome');
            const circleSkin = frame.querySelector('.yui-guide-spotlight-circle-skin');

            if (chrome && chrome.style) {
                chrome.style.display = '';
            }

            if (circleSkin && circleSkin.style) {
                circleSkin.style.display = 'none';
            }
        }

        updateBackdropCutout(cutout, spotlightRect) {
            if (!cutout) {
                return;
            }

            if (!spotlightRect) {
                cutout.hidden = true;
                cutout.setAttribute('x', '0');
                cutout.setAttribute('y', '0');
                cutout.setAttribute('width', '0');
                cutout.setAttribute('height', '0');
                cutout.setAttribute('rx', '0');
                cutout.setAttribute('ry', '0');
                cutout.style.display = 'none';
                return;
            }

            cutout.hidden = false;
            cutout.style.removeProperty('display');
            const maxInset = spotlightRect.padding == null
                ? this.backdropCutoutInset
                : Math.max(0, spotlightRect.padding);
            const inset = Math.max(0, Math.min(
                this.backdropCutoutInset,
                maxInset,
                Math.floor(spotlightRect.width / 2),
                Math.floor(spotlightRect.height / 2)
            ));
            const x = spotlightRect.left + inset;
            const y = spotlightRect.top + inset;
            const width = Math.max(0, spotlightRect.width - (inset * 2));
            const height = Math.max(0, spotlightRect.height - (inset * 2));
            const radius = Math.max(0, spotlightRect.radius - inset);
            cutout.setAttribute('x', String(x));
            cutout.setAttribute('y', String(y));
            cutout.setAttribute('width', String(width));
            cutout.setAttribute('height', String(height));
            cutout.setAttribute('rx', String(radius));
            cutout.setAttribute('ry', String(radius));
        }

        updateSpotlightFrame(frame, spotlightRect, options) {
            if (!frame) {
                return;
            }

            const normalizedOptions = options || {};
            const allowMask = normalizedOptions.allowMask !== false;
            const variant = normalizedOptions.variant || '';
            const forceCircleImage = variant === 'circle-image';
            const forcePlainCircle = variant === 'plain-circle';

            if (this.shouldSuppressDom()) {
                frame.hidden = true;
                frame.classList.remove('is-visible');
                return;
            }

            if (!spotlightRect) {
                frame.hidden = true;
                frame.classList.remove('is-visible');
                frame.classList.remove('is-circular-mask');
                frame.classList.remove('is-circle-image');
                frame.classList.remove('is-plain-circle');
                frame.classList.remove('is-thin-variant');
                this.removeSpotlightImageDecorations(frame);
                return;
            }

            frame.hidden = false;
            frame.classList.add('is-visible');
            frame.classList.toggle('is-circular-mask', !!spotlightRect.isCircular && allowMask && !forcePlainCircle);
            frame.classList.toggle('is-circle-image', forceCircleImage);
            frame.classList.toggle('is-plain-circle', forcePlainCircle);
            frame.classList.toggle('is-thin-variant', variant === 'thin');
            if (forcePlainCircle) {
                this.applySpotlightPlainCircleMode(frame);
            } else if (forceCircleImage) {
                this.applySpotlightFrameDecorationMode(frame, true);
            } else {
                this.applySpotlightFrameDecorationMode(frame, !!spotlightRect.isCircular);
            }
            frame.style.left = spotlightRect.left + 'px';
            frame.style.top = spotlightRect.top + 'px';
            frame.style.width = spotlightRect.width + 'px';
            frame.style.height = spotlightRect.height + 'px';
            frame.style.borderRadius = spotlightRect.radius + 'px';
        }
    }

    class TutorialOverlayRenderer {
        constructor(pcOverlayBridge) {
            this.pcOverlayBridge = pcOverlayBridge || null;
        }

        isAvailable() {
            return !!(
                this.pcOverlayBridge
                && typeof this.pcOverlayBridge.isAvailable === 'function'
                && this.pcOverlayBridge.isAvailable()
            );
        }

        shouldSuppressDom() {
            return !!(
                this.pcOverlayBridge
                && typeof this.pcOverlayBridge.shouldSuppressDom === 'function'
                && this.pcOverlayBridge.shouldSuppressDom()
            );
        }

        canRenderPetalTransition() {
            return !!(
                this.pcOverlayBridge
                && typeof this.pcOverlayBridge.canRenderPetalTransition === 'function'
                && this.pcOverlayBridge.canRenderPetalTransition()
            );
        }

        setSpotlights(rects) {
            if (this.pcOverlayBridge && typeof this.pcOverlayBridge.setSpotlights === 'function') {
                this.pcOverlayBridge.setSpotlights(rects);
            }
        }

        showCursorAt(x, y) {
            if (this.pcOverlayBridge && typeof this.pcOverlayBridge.showCursorAt === 'function') {
                this.pcOverlayBridge.showCursorAt(x, y);
            }
        }

        moveCursorTo(x, y, durationMs, effect, effectDurationMs) {
            if (this.pcOverlayBridge && typeof this.pcOverlayBridge.moveCursorTo === 'function') {
                this.pcOverlayBridge.moveCursorTo(x, y, durationMs, effect, effectDurationMs);
            }
        }

        hideCursor() {
            if (this.pcOverlayBridge && typeof this.pcOverlayBridge.hideCursor === 'function') {
                this.pcOverlayBridge.hideCursor();
            }
        }

        clearCursorCache() {
            if (this.pcOverlayBridge && typeof this.pcOverlayBridge.clearCursorCache === 'function') {
                this.pcOverlayBridge.clearCursorCache();
            }
        }

        playPetalTransition(origin, options) {
            if (this.pcOverlayBridge && typeof this.pcOverlayBridge.playPetalTransition === 'function') {
                this.pcOverlayBridge.playPetalTransition(origin, options || {});
                return true;
            }
            return false;
        }

        clear() {
            if (this.pcOverlayBridge && typeof this.pcOverlayBridge.clear === 'function') {
                this.pcOverlayBridge.clear();
            }
        }
    }

    TutorialOverlayRenderer.createPcOverlayCompleteStateStore = createPcOverlayCompleteStateStore;
    TutorialOverlayRenderer.OverlayCursorStateStore = OverlayCursorStateStore;
    TutorialOverlayRenderer.OverlaySpotlightStateStore = OverlaySpotlightStateStore;
    TutorialOverlayRenderer.OverlaySpotlightDomRenderer = OverlaySpotlightDomRenderer;

    return {
        TutorialOverlayRenderer,
        OverlayCursorStateStore,
        OverlaySpotlightStateStore,
        OverlaySpotlightDomRenderer,
        createPcOverlayCompleteStateStore
    };
});
