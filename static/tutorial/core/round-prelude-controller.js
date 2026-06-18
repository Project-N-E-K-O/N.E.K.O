(function (root, factory) {
    'use strict';

    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialRoundPrelude = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    function noop() {}

    function toPromise(callback, fallbackValue) {
        if (typeof callback !== 'function') {
            return Promise.resolve(fallbackValue);
        }
        try {
            return Promise.resolve(callback());
        } catch (error) {
            return Promise.reject(error);
        }
    }

    class TutorialRoundPreludeController {
        constructor(options) {
            const normalizedOptions = options || {};
            this.beginAvatarOverride = normalizedOptions.beginAvatarOverride || noop;
            this.revealPrepared = normalizedOptions.revealPrepared || noop;
            this.ensureVisible = normalizedOptions.ensureVisible || noop;
            this.sleep = normalizedOptions.sleep || noop;
            this.beginTakingOver = normalizedOptions.beginTakingOver || noop;
            this.setLifecycleActive = normalizedOptions.setLifecycleActive || noop;
            this.showSkipButton = normalizedOptions.showSkipButton || noop;
            this.dispatchStarted = normalizedOptions.dispatchStarted || noop;
            this.warn = normalizedOptions.warn || noop;
            this.defaultDelayMs = Number.isFinite(normalizedOptions.delayMs)
                ? Math.max(0, Math.round(normalizedOptions.delayMs))
                : 1500;
        }

        async play(day, options) {
            const normalizedOptions = options || {};
            const source = normalizedOptions.source || 'manual';
            const delayMs = Number.isFinite(normalizedOptions.delayMs)
                ? Math.max(0, Math.round(normalizedOptions.delayMs))
                : this.defaultDelayMs;
            const sceneId = 'avatar_floating_day' + day;

            await toPromise(() => this.beginAvatarOverride()).catch((error) => {
                this.warn('[Tutorial] 悬浮窗教程临时切换 YUI 失败，继续教程:', error);
            }).finally(() => {
                return toPromise(() => this.revealPrepared());
            });

            await toPromise(() => this.ensureVisible(sceneId)).catch((error) => {
                this.warn('[Tutorial] 悬浮窗教程确认 YUI 模型失败，继续教程:', error);
                return toPromise(() => this.revealPrepared());
            });

            await toPromise(() => this.sleep(delayMs));
            this.beginTakingOver({
                day: day,
                source: source,
                director: normalizedOptions.director || null
            });
            this.setLifecycleActive(true);
            this.showSkipButton();
            this.dispatchStarted({
                day: day,
                source: source
            });
        }
    }

    return {
        TutorialRoundPreludeController,
        createController(options) {
            return new TutorialRoundPreludeController(options);
        }
    };
});
