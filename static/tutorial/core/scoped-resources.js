(function (root, factory) {
    'use strict';

    const api = factory(root);
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialScopedResources = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function (root) {
    'use strict';

    function createScopedTutorialResources(options) {
        const normalizedOptions = options || {};
        const win = normalizedOptions.window || root;
        const listeners = [];
        const timers = [];
        const intervals = [];
        const animationFrames = [];
        let destroyed = false;

        function addEventListener(target, type, handler, listenerOptions) {
            if (destroyed || !target || typeof target.addEventListener !== 'function') {
                return null;
            }
            target.addEventListener(type, handler, listenerOptions);
            listeners.push({
                target,
                type,
                handler,
                options: listenerOptions
            });
            return handler;
        }

        function setScopedTimeout(callback, delayMs) {
            if (destroyed || !win || typeof win.setTimeout !== 'function') {
                return 0;
            }
            const timerId = win.setTimeout(function scopedTimeoutCallback() {
                const index = timers.indexOf(timerId);
                if (index !== -1) {
                    timers.splice(index, 1);
                }
                return callback.apply(this, arguments);
            }, delayMs);
            timers.push(timerId);
            return timerId;
        }

        function clearScopedTimeout(timerId) {
            if (!timerId || !win || typeof win.clearTimeout !== 'function') {
                return;
            }
            win.clearTimeout(timerId);
            const index = timers.indexOf(timerId);
            if (index !== -1) {
                timers.splice(index, 1);
            }
        }

        function setScopedInterval(callback, delayMs) {
            if (destroyed || !win || typeof win.setInterval !== 'function') {
                return 0;
            }
            const intervalId = win.setInterval(callback, delayMs);
            intervals.push(intervalId);
            return intervalId;
        }

        function clearScopedInterval(intervalId) {
            if (!intervalId || !win || typeof win.clearInterval !== 'function') {
                return;
            }
            win.clearInterval(intervalId);
            const index = intervals.indexOf(intervalId);
            if (index !== -1) {
                intervals.splice(index, 1);
            }
        }

        function requestScopedAnimationFrame(callback) {
            if (destroyed || !win || typeof win.requestAnimationFrame !== 'function') {
                return 0;
            }
            const frameId = win.requestAnimationFrame(function scopedAnimationFrameCallback() {
                const index = animationFrames.indexOf(frameId);
                if (index !== -1) {
                    animationFrames.splice(index, 1);
                }
                return callback.apply(this, arguments);
            });
            animationFrames.push(frameId);
            return frameId;
        }

        function cancelScopedAnimationFrame(frameId) {
            if (!frameId || !win || typeof win.cancelAnimationFrame !== 'function') {
                return;
            }
            win.cancelAnimationFrame(frameId);
            const index = animationFrames.indexOf(frameId);
            if (index !== -1) {
                animationFrames.splice(index, 1);
            }
        }

        function destroy() {
            if (destroyed) {
                return;
            }
            destroyed = true;
            while (animationFrames.length) {
                cancelScopedAnimationFrame(animationFrames.pop());
            }
            while (intervals.length) {
                clearScopedInterval(intervals.pop());
            }
            while (timers.length) {
                clearScopedTimeout(timers.pop());
            }
            while (listeners.length) {
                const listener = listeners.pop();
                if (listener.target && typeof listener.target.removeEventListener === 'function') {
                    listener.target.removeEventListener(
                        listener.type,
                        listener.handler,
                        listener.options
                    );
                }
            }
        }

        return {
            addEventListener,
            setTimeout: setScopedTimeout,
            clearTimeout: clearScopedTimeout,
            setInterval: setScopedInterval,
            clearInterval: clearScopedInterval,
            requestAnimationFrame: requestScopedAnimationFrame,
            cancelAnimationFrame: cancelScopedAnimationFrame,
            destroy,
            isDestroyed() {
                return destroyed;
            }
        };
    }

    return {
        createScopedTutorialResources
    };
});
