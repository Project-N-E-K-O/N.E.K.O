(function (root, factory) {
    'use strict';

    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialGhostCursorController = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const DEFAULT_CURSOR_MOVE_SLOWDOWN_MS = 900;
    const SHORT_CURSOR_MOVE_EXTRA_MS = 2400;

    function clamp(value, min, max) {
        return Math.min(Math.max(value, min), max);
    }

    class YuiGuideGhostCursor {
        constructor(overlay) {
            this.overlay = overlay;
            this.lastTarget = null;
        }
    }

    class GhostCursorController {
        constructor(legacyCursor, options) {
            const normalizedOptions = options || {};
            this.legacyCursor = legacyCursor;
            this.registry = normalizedOptions.registry || null;
            this.motionToken = 0;
            this.reactionToken = 0;
            this.resistanceToken = 0;
            this.resistanceRestPoint = null;
            this.transientMotionCount = 0;
            this.transientMotionWaiters = [];
        }

        resolveTargetEntry(targetKey) {
            if (!this.registry || typeof this.registry.resolve !== 'function') {
                return null;
            }
            return this.registry.resolve(targetKey) || null;
        }

        getExternalKind(targetKey) {
            if (this.registry && typeof this.registry.getExternalKind === 'function') {
                return this.registry.getExternalKind(targetKey) || '';
            }
            const entry = this.resolveTargetEntry(targetKey);
            return entry ? entry.externalKind || '' : '';
        }

        getLocalSelectors(targetKey) {
            if (this.registry && typeof this.registry.getLocalSelectors === 'function') {
                const selectors = this.registry.getLocalSelectors(targetKey);
                return Array.isArray(selectors) ? selectors.slice() : [];
            }
            const entry = this.resolveTargetEntry(targetKey);
            return entry && Array.isArray(entry.localSelectors) ? entry.localSelectors.slice() : [];
        }

        get overlay() {
            return this.legacyCursor ? this.legacyCursor.overlay : undefined;
        }

        set overlay(value) {
            if (this.legacyCursor) {
                this.legacyCursor.overlay = value;
            }
        }

        get lastTarget() {
            return this.legacyCursor ? this.legacyCursor.lastTarget : undefined;
        }

        set lastTarget(value) {
            if (this.legacyCursor) {
                this.legacyCursor.lastTarget = value;
            }
        }

        hasPosition() {
            const overlay = this.overlay;
            if (overlay && typeof overlay.hasCursorPosition === 'function') {
                return overlay.hasCursorPosition();
            }
            return !!(
                overlay
                && typeof overlay.getCursorPosition === 'function'
                && overlay.getCursorPosition()
            );
        }

        isVisible() {
            const overlay = this.overlay;
            if (overlay && typeof overlay.isCursorVisible === 'function') {
                return overlay.isCursorVisible();
            }
            return this.hasPosition();
        }

        hasVisiblePosition() {
            return this.hasPosition() && this.isVisible();
        }

        showAt(x, y) {
            this.clearResistanceRestPoint();
            this.overlay.showCursorAt(x, y);
        }

        clearResistanceRestPoint() {
            this.resistanceRestPoint = null;
            this.resistanceToken += 1;
        }

        beginTransientMotion() {
            this.transientMotionCount += 1;
        }

        endTransientMotion() {
            this.transientMotionCount = Math.max(0, this.transientMotionCount - 1);
            if (this.transientMotionCount > 0) {
                return;
            }
            const waiters = this.transientMotionWaiters.slice();
            this.transientMotionWaiters = [];
            waiters.forEach((resolve) => {
                try {
                    resolve();
                } catch (_) {}
            });
        }

        isTransientMotionActive() {
            return this.transientMotionCount > 0;
        }

        waitForTransientMotion() {
            if (!this.isTransientMotionActive()) {
                return Promise.resolve();
            }
            return new Promise((resolve) => {
                this.transientMotionWaiters.push(resolve);
            });
        }

        getResistanceRestPoint(current) {
            if (
                !this.resistanceRestPoint
                || !Number.isFinite(this.resistanceRestPoint.x)
                || !Number.isFinite(this.resistanceRestPoint.y)
            ) {
                this.resistanceRestPoint = {
                    x: current.x,
                    y: current.y
                };
            }
            return {
                x: this.resistanceRestPoint.x,
                y: this.resistanceRestPoint.y
            };
        }

        normalizeMoveOptions(options) {
            const normalizedOptions = Object.assign({}, options || {});
            const rawDurationMs = Number.isFinite(normalizedOptions.durationMs)
                ? normalizedOptions.durationMs
                : 480;
            const rawEffectDurationMs = Number.isFinite(normalizedOptions.effectDurationMs)
                ? normalizedOptions.effectDurationMs
                : null;
            if (normalizedOptions.exactDuration === true) {
                normalizedOptions.durationMs = Math.max(0, rawDurationMs);
                if (rawEffectDurationMs !== null) {
                    normalizedOptions.effectDurationMs = Math.max(0, rawEffectDurationMs);
                }
                return normalizedOptions;
            }
            normalizedOptions.durationMs = rawDurationMs
                + DEFAULT_CURSOR_MOVE_SLOWDOWN_MS
                + (rawDurationMs < 1000 ? SHORT_CURSOR_MOVE_EXTRA_MS : 0);
            if (rawEffectDurationMs !== null) {
                normalizedOptions.effectDurationMs = rawEffectDurationMs
                    + DEFAULT_CURSOR_MOVE_SLOWDOWN_MS
                    + (rawEffectDurationMs < 1000 ? SHORT_CURSOR_MOVE_EXTRA_MS : 0);
            }
            return normalizedOptions;
        }

        moveToPoint(x, y, options) {
            this.clearResistanceRestPoint();
            const normalizedOptions = this.normalizeMoveOptions(options);
            const token = ++this.motionToken;
            this.lastTarget = { x: x, y: y };
            return this.overlay.moveCursorTo(x, y, Object.assign({}, normalizedOptions, {
                cancelCheck: () => {
                    if (token !== this.motionToken) {
                        return true;
                    }

                    return typeof normalizedOptions.cancelCheck === 'function'
                        ? !!normalizedOptions.cancelCheck()
                        : false;
                }
            }));
        }

        moveToRect(rect, options) {
            if (!rect) {
                return Promise.resolve();
            }

            const point = {
                x: rect.left + (rect.width / 2),
                y: rect.top + (rect.height / 2)
            };

            return this.moveToPoint(point.x, point.y, options);
        }

        async moveCursorAlongPoints(points, options) {
            if (!Array.isArray(points) || points.length === 0) {
                return false;
            }
            this.clearResistanceRestPoint();
            const normalizedOptions = options || {};
            const token = ++this.motionToken;
            const durationMs = Number.isFinite(normalizedOptions.durationMs)
                ? Math.max(0, normalizedOptions.durationMs)
                : 480;
            const segmentDurationMs = Math.max(0, Math.round(durationMs / Math.max(1, points.length)));
            for (let index = 0; index < points.length; index += 1) {
                const point = points[index];
                if (
                    !point
                    || !Number.isFinite(point.x)
                    || !Number.isFinite(point.y)
                ) {
                    continue;
                }
                if (token !== this.motionToken) {
                    return false;
                }
                if (
                    typeof normalizedOptions.cancelCheck === 'function'
                    && normalizedOptions.cancelCheck()
                ) {
                    return false;
                }
                this.lastTarget = { x: point.x, y: point.y };
                const moved = await this.overlay.moveCursorTo(point.x, point.y, {
                    durationMs: segmentDurationMs,
                    effect: normalizedOptions.effect || '',
                    effectDurationMs: Number.isFinite(normalizedOptions.effectDurationMs)
                        ? Math.max(0, normalizedOptions.effectDurationMs)
                        : undefined,
                    pauseCheck: normalizedOptions.pauseCheck,
                    cancelCheck: () => {
                        if (token !== this.motionToken) {
                            return true;
                        }
                        return typeof normalizedOptions.cancelCheck === 'function'
                            ? !!normalizedOptions.cancelCheck()
                            : false;
                    }
                });
                if (!moved) {
                    return false;
                }
            }
            return true;
        }

        click(durationMs) {
            this.overlay.clickCursor(durationMs);
        }

        wobble(durationMs) {
            this.overlay.wobbleCursor(durationMs);
        }

        async reactToUserMotion(userX, userY, options) {
            if (!this.hasVisiblePosition()) {
                return;
            }
            const current = this.overlay.getCursorPosition();
            if (!current) {
                return;
            }

            const normalizedOptions = options || {};
            const motionDx = Number.isFinite(normalizedOptions.motionDx) ? normalizedOptions.motionDx : 0;
            const motionDy = Number.isFinite(normalizedOptions.motionDy) ? normalizedOptions.motionDy : 0;
            const hasMotionVector = Math.hypot(motionDx, motionDy) > 0;
            const returnTarget = normalizedOptions.returnPoint && Number.isFinite(normalizedOptions.returnPoint.x) && Number.isFinite(normalizedOptions.returnPoint.y)
                ? {
                    x: normalizedOptions.returnPoint.x,
                    y: normalizedOptions.returnPoint.y
                }
                : this.getResistanceRestPoint(current);
            const reactionDx = hasMotionVector ? -motionDx : returnTarget.x - userX;
            const reactionDy = hasMotionVector ? -motionDy : returnTarget.y - userY;
            const dx = reactionDx;
            const dy = reactionDy;
            const distance = Math.max(1, Math.hypot(dx, dy));
            const reactionDistance = clamp(
                distance * (Number.isFinite(normalizedOptions.scale) ? normalizedOptions.scale : 0.12),
                18,
                40
            );
            const targetX = returnTarget.x + ((dx / distance) * reactionDistance);
            const targetY = returnTarget.y + ((dy / distance) * reactionDistance);
            const token = ++this.reactionToken;

            this.beginTransientMotion();
            try {
                await this.overlay.moveCursorTo(targetX, targetY, {
                    durationMs: Number.isFinite(normalizedOptions.outDurationMs) ? normalizedOptions.outDurationMs : 140,
                    forcePcOverlay: normalizedOptions.forcePcOverlay === true
                });
                if (token !== this.reactionToken) {
                    return;
                }

                await this.overlay.moveCursorTo(returnTarget.x, returnTarget.y, {
                    durationMs: Number.isFinite(normalizedOptions.backDurationMs) ? normalizedOptions.backDurationMs : 240,
                    forcePcOverlay: normalizedOptions.forcePcOverlay === true
                });
                if (token === this.reactionToken) {
                    this.resistanceRestPoint = null;
                }
            } finally {
                this.endTransientMotion();
            }
        }

        async resistTo(userX, userY, options) {
            if (!this.hasVisiblePosition()) {
                return;
            }
            const current = this.overlay.getCursorPosition();
            if (!current) {
                return;
            }

            const normalizedOptions = options || {};
            const motionDx = Number.isFinite(normalizedOptions.motionDx) ? normalizedOptions.motionDx : 0;
            const motionDy = Number.isFinite(normalizedOptions.motionDy) ? normalizedOptions.motionDy : 0;
            const hasMotionVector = Math.hypot(motionDx, motionDy) > 0;
            const returnTarget = normalizedOptions.returnPoint && Number.isFinite(normalizedOptions.returnPoint.x) && Number.isFinite(normalizedOptions.returnPoint.y)
                ? {
                    x: normalizedOptions.returnPoint.x,
                    y: normalizedOptions.returnPoint.y
                }
                : this.getResistanceRestPoint(current);
            const pullDx = hasMotionVector ? -motionDx : returnTarget.x - userX;
            const pullDy = hasMotionVector ? -motionDy : returnTarget.y - userY;
            const dx = pullDx;
            const dy = pullDy;
            const distance = Math.max(1, Math.hypot(dx, dy));
            const pullDistance = clamp(distance * 0.3, 18, 36);
            const pullX = returnTarget.x + ((dx / distance) * pullDistance);
            const pullY = returnTarget.y + ((dy / distance) * pullDistance);
            const token = ++this.resistanceToken;

            this.beginTransientMotion();
            try {
                await this.overlay.moveCursorTo(pullX, pullY, {
                    durationMs: 180,
                    forcePcOverlay: normalizedOptions.forcePcOverlay === true
                });
                await this.overlay.moveCursorTo(returnTarget.x, returnTarget.y, {
                    durationMs: 260,
                    forcePcOverlay: normalizedOptions.forcePcOverlay === true
                });
                if (token === this.resistanceToken) {
                    this.resistanceRestPoint = null;
                }
            } finally {
                this.endTransientMotion();
            }
        }

        runPauseAwareEllipse(centerX, centerY, radiusX, radiusY, cycleMs, abortCheck, pauseCheck, cancelCheck) {
            this.clearResistanceRestPoint();
            const normalizedCancelCheck = typeof cancelCheck === 'function' ? cancelCheck : null;
            const token = ++this.motionToken;
            return this.overlay.runEllipseAnimation(
                centerX,
                centerY,
                radiusX,
                radiusY,
                cycleMs,
                abortCheck,
                pauseCheck,
                () => {
                    if (token !== this.motionToken) {
                        return true;
                    }

                    return normalizedCancelCheck ? !!normalizedCancelCheck() : false;
                }
            );
        }

        clearPosition() {
            this.clearResistanceRestPoint();
            if (this.overlay && typeof this.overlay.clearCursorPosition === 'function') {
                this.overlay.clearCursorPosition();
            }
        }

        hide() {
            this.clearResistanceRestPoint();
            this.overlay.hideCursor();
        }

        cancel(...args) {
            this.motionToken += 1;
            this.reactionToken += 1;
            this.clearResistanceRestPoint();
            if (!this.legacyCursor || typeof this.legacyCursor.cancel !== 'function') {
                return undefined;
            }
            return this.legacyCursor.cancel(...args);
        }

        pause(...args) {
            if (!this.legacyCursor || typeof this.legacyCursor.pause !== 'function') {
                return undefined;
            }
            return this.legacyCursor.pause(...args);
        }

        resume(...args) {
            if (!this.legacyCursor || typeof this.legacyCursor.resume !== 'function') {
                return undefined;
            }
            return this.legacyCursor.resume(...args);
        }
    }

    return {
        YuiGuideGhostCursor,
        GhostCursorController
    };
});
