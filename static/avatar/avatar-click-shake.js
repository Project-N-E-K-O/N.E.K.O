/* Click shake feedback shared by the Live2D, VRM and MMD interaction adapters. */
(function (global) {
    'use strict';

    // 与 Live2D 点击判定同一口径：移动不超过 10px、按住不超过 300ms 才算点击
    const TAP_MAX_DISTANCE = 10;
    const TAP_MAX_DURATION_MS = 300;
    const SHAKE_DURATION_MS = 280;
    // 横向衰减振荡（px）。走独立的 translate 属性而不是 transform，
    // 不会覆盖画布 CSS 里的 translateZ(0) 合成层和退场动画的 transform
    const SHAKE_OFFSETS = [0, 4, -4, 3, -2, 1, 0];
    const running = new WeakMap();

    function isEnabled() {
        if (typeof global.modelClickShakeEnabled === 'boolean') return global.modelClickShakeEnabled;
        // 模型管理、小游戏等页面不加载 app-settings.js，直接读主界面存下的设置
        try {
            const saved = JSON.parse(global.localStorage.getItem('project_neko_settings') || 'null');
            const value = saved && saved.modelClickShakeEnabled;
            if (value === false || value === 'false') return false;
        } catch (_) {}
        return true;
    }

    function prefersReducedMotion() {
        try {
            return !!(global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches);
        } catch (_) {
            return false;
        }
    }

    function tapStart(point) {
        if (!point || !Number.isFinite(point.clientX) || !Number.isFinite(point.clientY)) return null;
        return { x: point.clientX, y: point.clientY, time: Date.now() };
    }

    function isTap(start, point) {
        if (!start || !point || !Number.isFinite(point.clientX) || !Number.isFinite(point.clientY)) return false;
        if (Date.now() - start.time >= TAP_MAX_DURATION_MS) return false;
        return Math.hypot(point.clientX - start.x, point.clientY - start.y) <= TAP_MAX_DISTANCE;
    }

    // 画布整体原地抖一下；只动 CSS 合成层，不改模型坐标，也就不会被位置保存/回弹读到
    function play(element) {
        if (!isEnabled() || prefersReducedMotion() || !element || typeof element.animate !== 'function') return null;
        const previous = running.get(element);
        if (previous) previous.cancel();
        let animation;
        try {
            animation = element.animate(
                SHAKE_OFFSETS.map(x => ({ translate: `${x}px 0` })),
                { duration: SHAKE_DURATION_MS, easing: 'ease-out' }
            );
        } catch (_) {
            return null;
        }
        running.set(element, animation);
        const clear = () => {
            if (running.get(element) === animation) running.delete(element);
        };
        animation.onfinish = clear;
        animation.oncancel = clear;
        return animation;
    }

    global.NekoAvatarClickShake = Object.freeze({ isEnabled, tapStart, isTap, play });
})(window);
