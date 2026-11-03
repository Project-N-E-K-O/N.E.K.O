/* Pure collision squash/stretch calculation shared by cat gravity renderers. */
(function installNekoCollisionDeformation(global) {
    'use strict';

    const REST_SPEED_PX_PER_SECOND = 38;
    const IMPACT_SPEED_SCALE_PX_PER_SECOND = 720;
    // The GIFs contain generous transparent padding, so sub-20% scaling is
    // barely perceptible on the actual cat pixels. Keep the response short,
    // but make the impact pose unmistakable inside that window.
    const MIN_COMPRESSION = 0.10;
    const MAX_COMPRESSION = 0.38;
    const HOLD_MS = 70;
    const DURATION_MS = 160;
    const ACTIVE_CLASS = 'is-neko-collision-deforming';
    const activeDeformations = new WeakMap();
    const IDENTITY = Object.freeze({
        active: false,
        scaleX: 1,
        scaleY: 1,
        originX: 50,
        originY: 50,
    });
    const ORIGINS = Object.freeze({
        up: Object.freeze({ x: 50, y: 0 }),
        right: Object.freeze({ x: 100, y: 50 }),
        down: Object.freeze({ x: 50, y: 100 }),
        left: Object.freeze({ x: 0, y: 50 }),
    });

    function clamp01(value) {
        return Math.max(0, Math.min(1, value));
    }

    function recoveryFactor(elapsedMs) {
        if (elapsedMs <= HOLD_MS) return 1;
        const progress = clamp01((elapsedMs - HOLD_MS) / (DURATION_MS - HOLD_MS));
        const smoothProgress = progress * progress * (3 - 2 * progress);
        return 1 - smoothProgress;
    }

    function resolveOrigin(direction, visibleInsetRatios) {
        const fallback = ORIGINS[direction];
        if (!fallback || !visibleInsetRatios) return fallback;
        const keys = ['left', 'top', 'right', 'bottom'];
        if (!keys.every(key => Number.isFinite(Number(visibleInsetRatios[key])))) return fallback;
        const left = Math.max(-1, Math.min(0.49, Number(visibleInsetRatios.left)));
        const top = Math.max(-1, Math.min(0.49, Number(visibleInsetRatios.top)));
        const right = Math.max(-1, Math.min(0.49, Number(visibleInsetRatios.right)));
        const bottom = Math.max(-1, Math.min(0.49, Number(visibleInsetRatios.bottom)));
        if (left + right >= 1 || top + bottom >= 1) return fallback;
        const centerX = (left + 1 - right) * 50;
        const centerY = (top + 1 - bottom) * 50;
        if (direction === 'up') return { x: centerX, y: top * 100 };
        if (direction === 'down') return { x: centerX, y: (1 - bottom) * 100 };
        if (direction === 'left') return { x: left * 100, y: centerY };
        return { x: (1 - right) * 100, y: centerY };
    }

    /**
     * Calculate one collision deformation sample without touching DOM or physics state.
     * Direction means the contact side: a floor impact is "down", for example.
     * Origins are percentages so a renderer can apply `${originX}% ${originY}%`.
     */
    function computeCollisionDeformation(input = {}) {
        const { direction, impactVelocityChange, elapsedMs, reducedMotion = false,
            visibleInsetRatios = null } = input;
        const origin = resolveOrigin(direction, visibleInsetRatios);
        if (reducedMotion || !origin
            || !Number.isFinite(impactVelocityChange) || impactVelocityChange <= REST_SPEED_PX_PER_SECOND
            || !Number.isFinite(elapsedMs) || elapsedMs < 0 || elapsedMs >= DURATION_MS) {
            return IDENTITY;
        }

        // An asymptotic response avoids a hard speed bucket: two finite impact
        // speeds always produce different amplitudes, while MAX_COMPRESSION
        // remains an asymptotic visual safety limit.
        const impactSpeed = impactVelocityChange - REST_SPEED_PX_PER_SECOND;
        const speedProgress = 1 - Math.exp(
            -impactSpeed / IMPACT_SPEED_SCALE_PX_PER_SECOND
        );
        const vertical = direction === 'up' || direction === 'down';
        const compression = (MIN_COMPRESSION
            + (MAX_COMPRESSION - MIN_COMPRESSION) * speedProgress) * recoveryFactor(elapsedMs);

        return Object.freeze({
            active: true,
            scaleX: vertical ? 1 + compression : 1 - compression,
            scaleY: vertical ? 1 - compression : 1 + compression,
            originX: origin.x,
            originY: origin.y,
        });
    }

    function applyCollisionDeformation(button, sample) {
        if (!button || !button.style || !button.classList) return false;
        button.classList.add(ACTIVE_CLASS);
        button.style.setProperty('--neko-collision-scale-x', sample.scaleX.toFixed(4));
        button.style.setProperty('--neko-collision-scale-y', sample.scaleY.toFixed(4));
        button.style.setProperty('--neko-collision-origin-x', `${sample.originX}%`);
        button.style.setProperty('--neko-collision-origin-y', `${sample.originY}%`);
        return true;
    }

    function clearCollisionDeformation(button) {
        if (!button) return false;
        const existed = activeDeformations.delete(button);
        if (button.classList) button.classList.remove(ACTIVE_CLASS);
        if (button.style) {
            button.style.removeProperty('--neko-collision-scale-x');
            button.style.removeProperty('--neko-collision-scale-y');
            button.style.removeProperty('--neko-collision-origin-x');
            button.style.removeProperty('--neko-collision-origin-y');
        }
        return existed;
    }

    function beginCollisionDeformation(button, input = {}) {
        const { direction, impactVelocityChange, startedAt, reducedMotion = false,
            visibleInsetRatios = null } = input;
        if (reducedMotion) {
            clearCollisionDeformation(button);
            return false;
        }
        const sample = computeCollisionDeformation({
            direction,
            impactVelocityChange,
            elapsedMs: 0,
            visibleInsetRatios,
        });
        if (!button || !sample.active || !Number.isFinite(startedAt)) return false;
        activeDeformations.set(button, {
            direction,
            impactVelocityChange,
            startedAt,
            visibleInsetRatios,
        });
        return applyCollisionDeformation(button, sample);
    }

    function renderCollisionDeformation(button, timestamp, reducedMotion = false,
        visibleInsetRatios = null) {
        const state = button && activeDeformations.get(button);
        if (!state) return false;
        if (reducedMotion || !Number.isFinite(timestamp)) {
            clearCollisionDeformation(button);
            return false;
        }
        if (visibleInsetRatios) state.visibleInsetRatios = visibleInsetRatios;
        const sample = computeCollisionDeformation({
            direction: state.direction,
            impactVelocityChange: state.impactVelocityChange,
            elapsedMs: Math.max(0, timestamp - state.startedAt),
            visibleInsetRatios: state.visibleInsetRatios,
        });
        if (!sample.active) {
            clearCollisionDeformation(button);
            return false;
        }
        return applyCollisionDeformation(button, sample);
    }

    const api = Object.freeze({
        computeCollisionDeformation,
        beginCollisionDeformation,
        renderCollisionDeformation,
        clearCollisionDeformation,
    });
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (global) global.NekoCollisionDeformation = api;
})(typeof window !== 'undefined' ? window : globalThis);
