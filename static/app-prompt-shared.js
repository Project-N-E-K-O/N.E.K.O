(function () {
    'use strict';

    const FLOW_LOG_PREFIX_FALLBACK = '[PromptFlow]';

    function createPromptTools(options) {
        const toolOptions = options || {};
        const flowPrefix = typeof toolOptions.flowPrefix === 'string' && toolOptions.flowPrefix
            ? toolOptions.flowPrefix
            : FLOW_LOG_PREFIX_FALLBACK;
        const loggerName = typeof toolOptions.loggerName === 'string' && toolOptions.loggerName
            ? toolOptions.loggerName
            : 'Prompt';

        function shortToken(value, length) {
            if (!value) return 'none';
            const sliceLength = Number.isFinite(length) && length > 0 ? length : 8;
            return String(value).slice(0, sliceLength);
        }

        function describeTarget(target) {
            if (!(target instanceof Element)) {
                return 'unknown';
            }
            const tag = target.tagName ? target.tagName.toLowerCase() : 'unknown';
            const id = target.id ? ('#' + target.id) : '';
            const className = typeof target.className === 'string'
                ? target.className.trim().split(/\s+/).filter(Boolean).slice(0, 2).join('.')
                : '';
            return tag + id + (className ? ('.' + className) : '');
        }

        function logFlow(step, details) {
            const payload = details || {};
            if (
                window.universalTutorialManager
                && typeof window.universalTutorialManager.logPromptFlow === 'function'
            ) {
                window.universalTutorialManager.logPromptFlow(step, payload);
                return;
            }
            if (typeof window.logTutorialPromptFlow === 'function') {
                window.logTutorialPromptFlow(step, payload);
                return;
            }
            console.log(flowPrefix + ' ' + step, payload);
        }

        function translate(key, fallback) {
            if (typeof window.safeT === 'function') {
                return window.safeT(key, fallback);
            }
            return typeof fallback === 'string' ? fallback : key;
        }

        function normalizeMs(value) {
            const number = Number(value);
            return Number.isFinite(number) && number > 0 ? number : 0;
        }

        async function requestJson(url, options) {
            const requestOptions = options || {};
            const hasJsonBody = Object.prototype.hasOwnProperty.call(requestOptions, 'json');
            const headers = Object.assign({}, requestOptions.headers);
            if (hasJsonBody && !headers['Content-Type']) {
                headers['Content-Type'] = 'application/json';
            }

            const response = await fetch(url, {
                method: requestOptions.method || 'GET',
                headers: headers,
                body: hasJsonBody ? JSON.stringify(requestOptions.json || {}) : requestOptions.body,
                keepalive: !!requestOptions.keepalive,
                cache: requestOptions.cache,
            });
            if (!response.ok) {
                throw new Error('HTTP ' + response.status);
            }
            return response.json();
        }

        function fireAndForgetJson(url, payload) {
            const body = JSON.stringify(payload || {});
            try {
                if (navigator.sendBeacon && typeof Blob === 'function') {
                    const queued = navigator.sendBeacon(
                        url,
                        new Blob([body], { type: 'application/json' })
                    );
                    if (queued) {
                        return Promise.resolve({ ok: true, beaconQueued: true });
                    }
                }
            } catch (error) {
                console.warn('[' + loggerName + '] sendBeacon failed:', error);
            }
            return requestJson(url, {
                method: 'POST',
                json: payload,
                keepalive: true,
            });
        }

        function isForegroundActive() {
            if (document.visibilityState !== 'visible') return false;
            if (typeof document.hasFocus === 'function') {
                try {
                    return document.hasFocus();
                } catch (_) {
                    return true;
                }
            }
            return true;
        }

        function attachForegroundTracker(state) {
            function syncForegroundWindow() {
                const now = Date.now();
                if (isForegroundActive()) {
                    if (state.foregroundStartedAt === null) {
                        state.foregroundStartedAt = now;
                        return;
                    }
                    state.pendingForegroundMs += Math.max(0, now - state.foregroundStartedAt);
                    state.foregroundStartedAt = now;
                    return;
                }
                if (state.foregroundStartedAt !== null) {
                    state.pendingForegroundMs += Math.max(0, now - state.foregroundStartedAt);
                    state.foregroundStartedAt = null;
                }
            }

            function consumeForegroundDelta() {
                syncForegroundWindow();
                const delta = state.pendingForegroundMs;
                state.pendingForegroundMs = 0;
                return delta;
            }

            return {
                syncForegroundWindow: syncForegroundWindow,
                consumeForegroundDelta: consumeForegroundDelta,
            };
        }

        function createFastHeartbeatScheduler(state, sendHeartbeat, delayMs) {
            const scheduleDelay = normalizeMs(delayMs);
            return function scheduleFastHeartbeat() {
                if (!state.initialized) return;
                if (state.fastHeartbeatTimer) return;
                state.fastHeartbeatTimer = setTimeout(function () {
                    state.fastHeartbeatTimer = null;
                    void sendHeartbeat();
                }, scheduleDelay);
            };
        }

        function isPromptOverlayTarget(target) {
            if (!(target instanceof Element)) {
                return false;
            }
            return Boolean(target.closest('.modal-overlay, .driver-popover, .driver-overlay'));
        }

        function isWeakHomePointerTarget(target) {
            if (!(target instanceof Element) || isPromptOverlayTarget(target)) {
                return false;
            }

            return Boolean(target.closest(
                'button, a[href], summary, [role="button"], [data-home-action]'
            ));
        }

        function isWeakHomeFocusTarget(target) {
            if (!(target instanceof Element) || isPromptOverlayTarget(target)) {
                return false;
            }

            return Boolean(target.closest('input, select, textarea, [contenteditable="true"]'));
        }

        function isWeakHomeChangeTarget(target) {
            if (!(target instanceof Element) || isPromptOverlayTarget(target)) {
                return false;
            }

            return Boolean(target.closest('input, select, textarea'));
        }

        return {
            shortToken: shortToken,
            describeTarget: describeTarget,
            logFlow: logFlow,
            translate: translate,
            normalizeMs: normalizeMs,
            requestJson: requestJson,
            fireAndForgetJson: fireAndForgetJson,
            attachForegroundTracker: attachForegroundTracker,
            createFastHeartbeatScheduler: createFastHeartbeatScheduler,
            isWeakHomePointerTarget: isWeakHomePointerTarget,
            isWeakHomeFocusTarget: isWeakHomeFocusTarget,
            isWeakHomeChangeTarget: isWeakHomeChangeTarget,
        };
    }

    window.nekoPromptShared = Object.assign({}, window.nekoPromptShared, {
        createPromptTools: createPromptTools,
    });
})();
