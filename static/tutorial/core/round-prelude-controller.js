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
            this.waitForAvatarReady = normalizedOptions.waitForAvatarReady || noop;
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
            const cancelCheck = normalizedOptions.cancelCheck || noop;
            const source = normalizedOptions.source || 'manual';
            const delayMs = Number.isFinite(normalizedOptions.delayMs)
                ? Math.max(0, Math.round(normalizedOptions.delayMs))
                : this.defaultDelayMs;
            const sceneId = 'avatar_floating_day' + day;
            const deferRevealPrepared = normalizedOptions.deferRevealPrepared === true;
            const skipSourceModelFade = normalizedOptions.skipSourceModelFade === true;

            if (cancelCheck()) return false;
            await toPromise(() => this.beginAvatarOverride({
                deferRevealPrepared,
                skipSourceModelFade
            })).catch((error) => {
                if (cancelCheck()) return;
                this.warn('[Tutorial] 悬浮窗教程临时切换 YUI 失败，中止教程:', error);
                return toPromise(() => this.revealPrepared()).then(() => {
                    throw error;
                });
            });
            if (cancelCheck()) return false;
            if (!deferRevealPrepared) {
                await toPromise(() => this.revealPrepared());
                if (cancelCheck()) return false;
            }

            await toPromise(() => this.ensureVisible(sceneId, {
                deferRevealPrepared
            })).catch((error) => {
                if (cancelCheck()) return;
                this.warn('[Tutorial] 悬浮窗教程确认 YUI 模型失败，中止教程:', error);
                return toPromise(() => this.revealPrepared()).then(() => {
                    throw error;
                });
            });
            if (cancelCheck()) return false;
            await toPromise(() => this.waitForAvatarReady(sceneId, {
                deferRevealPrepared
            })).catch((error) => {
                if (cancelCheck()) return;
                this.warn('[Tutorial] 等待 YUI 模型视觉就绪失败，继续启动教程:', error);
            });
            if (cancelCheck()) return false;

            await toPromise(() => this.sleep(delayMs));
            if (cancelCheck()) return false;
            this.beginTakingOver({
                day: day,
                source: source,
                director: normalizedOptions.director || null
            });
            if (cancelCheck()) return false;
            this.setLifecycleActive(true);
            this.showSkipButton();
            this.dispatchStarted({
                day: day,
                source: source
            });
            return true;
        }
    }

    return {
        TutorialRoundPreludeController,
        createController(options) {
            return new TutorialRoundPreludeController(options);
        }
    };
});
