(function () {
    'use strict';

    const FLOW_LOG_PREFIX_FALLBACK = '[PromptFlow]';
    const PROMPT_DISPLAY_BATCH_WINDOW_MS = 80;

    function createPromptDisplayCoordinator(defaultBatchWindowMs) {
        const queuedRequests = [];
        const keyedRequests = new Map();
        let activeRequest = null;
        let drainTimer = null;
        let drainInProgress = false;
        let nextSequence = 1;
        let queueVersion = 0;

        function normalizePriority(value) {
            const number = Number(value);
            return Number.isFinite(number) ? number : 0;
        }

        function normalizeDelay(value) {
            const number = Number(value);
            return Number.isFinite(number) && number >= 0 ? number : 0;
        }

        function clearRequestKey(request) {
            if (!request || !request.key) {
                return;
            }
            if (keyedRequests.get(request.key) === request) {
                keyedRequests.delete(request.key);
            }
        }

        function bumpQueueVersion() {
            queueVersion += 1;
        }

        function settleRequest(request, method, value) {
            if (!request || request.settled) {
                return;
            }
            request.settled = true;
            clearRequestKey(request);
            request[method](value);
        }

        function removeQueuedRequest(request) {
            const index = queuedRequests.indexOf(request);
            if (index < 0) {
                return false;
            }
            queuedRequests.splice(index, 1);
            bumpQueueVersion();
            return true;
        }

        function sortQueuedRequests() {
            queuedRequests.sort(function (left, right) {
                if (right.priority !== left.priority) {
                    return right.priority - left.priority;
                }
                return left.sequence - right.sequence;
            });
        }

        function peekNextRequest() {
            if (!queuedRequests.length) {
                return null;
            }
            sortQueuedRequests();
            return queuedRequests[0] || null;
        }

        function isRequestCurrentAfterGuard(request, revisionSnapshot, versionSnapshot) {
            if (!request || request.settled || request.revision !== revisionSnapshot) {
                return false;
            }

            if (queueVersion !== versionSnapshot && peekNextRequest() !== request) {
                return false;
            }

            return true;
        }

        function scheduleDrain(delayMs) {
            if (activeRequest || drainTimer || !queuedRequests.length) {
                return;
            }

            const waitMs = normalizeDelay(
                typeof delayMs === 'number'
                    ? delayMs
                    : defaultBatchWindowMs
            );
            drainTimer = setTimeout(function () {
                drainTimer = null;
                void drainQueue();
            }, waitMs);
        }

        async function drainQueue() {
            if (activeRequest || drainInProgress) {
                return;
            }

            drainInProgress = true;
            try {
                while (!activeRequest && queuedRequests.length) {
                    const nextRequest = peekNextRequest();
                    if (!nextRequest) {
                        return;
                    }

                    const versionBeforeCheck = queueVersion;
                    const revisionBeforeCheck = nextRequest.revision;
                    let canDisplay = true;
                    try {
                        if (typeof nextRequest.shouldDisplay === 'function') {
                            canDisplay = await nextRequest.shouldDisplay();
                        }
                    } catch (error) {
                        removeQueuedRequest(nextRequest);
                        settleRequest(nextRequest, 'reject', error);
                        continue;
                    }

                    // Re-run arbitration after async guards so stale guard results
                    // cannot authorize a newer request revision or skip a higher-priority
                    // request that arrived while the guard was in flight.
                    if (!isRequestCurrentAfterGuard(nextRequest, revisionBeforeCheck, versionBeforeCheck)) {
                        continue;
                    }

                    if (!removeQueuedRequest(nextRequest)) {
                        continue;
                    }
                    if (!canDisplay) {
                        settleRequest(nextRequest, 'resolve', null);
                        continue;
                    }

                    activeRequest = nextRequest;
                    try {
                        const result = await nextRequest.display();
                        settleRequest(nextRequest, 'resolve', result);
                    } catch (error) {
                        settleRequest(nextRequest, 'reject', error);
                    } finally {
                        activeRequest = null;
                    }
                }
            } finally {
                drainInProgress = false;
                if (queuedRequests.length) {
                    scheduleDrain(0);
                }
            }
        }

        function requestPromptDisplay(options) {
            const requestOptions = options || {};
            if (typeof requestOptions.display !== 'function') {
                return Promise.reject(new Error('prompt_display_handler_required'));
            }

            const key = typeof requestOptions.key === 'string' && requestOptions.key
                ? requestOptions.key
                : '';
            if (key) {
                const existingRequest = keyedRequests.get(key);
                if (existingRequest) {
                    if (!existingRequest.settled && existingRequest !== activeRequest) {
                        existingRequest.priority = normalizePriority(requestOptions.priority);
                        existingRequest.revision += 1;
                        existingRequest.shouldDisplay = requestOptions.shouldDisplay;
                        existingRequest.display = requestOptions.display;
                        bumpQueueVersion();
                        scheduleDrain(0);
                    }
                    return existingRequest.promise;
                }
            }

            let resolvePromise;
            let rejectPromise;
            const request = {
                key: key,
                priority: normalizePriority(requestOptions.priority),
                sequence: nextSequence++,
                revision: 1,
                shouldDisplay: requestOptions.shouldDisplay,
                display: requestOptions.display,
                settled: false,
                promise: new Promise(function (resolve, reject) {
                    resolvePromise = resolve;
                    rejectPromise = reject;
                }),
                resolve: resolvePromise,
                reject: rejectPromise,
            };

            if (key) {
                keyedRequests.set(key, request);
            }
            queuedRequests.push(request);
            bumpQueueVersion();
            scheduleDrain();
            return request.promise;
        }

        return {
            requestPromptDisplay: requestPromptDisplay,
        };
    }

    const promptDisplayCoordinator = createPromptDisplayCoordinator(PROMPT_DISPLAY_BATCH_WINDOW_MS);

    // ---------------------------------------------------------------------
    // Local-mutation CSRF security helper.
    //
    // Backend `_validate_local_mutation_request` (main_routers/system_router.py)
    // rejects POSTs to /api/(tutorial|autostart)-prompt/* without a valid
    // X-CSRF-Token header. The token is shipped to the page via the
    // `autostart_csrf_token` field of /api/config/page_config.
    //
    // Contract exercised by tests/frontend/test_home_prompt_flow.py:
    //   - On first mutation, read token from `window.pageConfigReady`.
    //   - If the server responds 403 with `error_code: 'csrf_validation_failed'`,
    //     refetch /api/config/page_config once, pick up the fresh token, and
    //     retry the original request exactly once. Subsequent requests reuse
    //     the refreshed token.
    // ---------------------------------------------------------------------
    const CSRF_HEADER_NAME = 'X-CSRF-Token';
    const PAGE_CONFIG_URL = '/api/config/page_config';

    function extractAutostartCsrfToken(source) {
        if (!source || typeof source !== 'object') {
            return '';
        }
        const token = source.autostart_csrf_token;
        return typeof token === 'string' ? token : '';
    }

    function createLocalMutationSecurity() {
        let cachedToken = '';
        let initialTokenPromise = null;
        let refreshPromise = null;
        let warnedMissingPageConfig = false;

        function resolveLanlanNameParam() {
            try {
                const urlParams = new URLSearchParams(window.location.search);
                const name = urlParams.get('lanlan_name') || '';
                return name ? ('?lanlan_name=' + encodeURIComponent(name)) : '';
            } catch (_) {
                return '';
            }
        }

        async function fetchPageConfigToken() {
            try {
                const response = await fetch(PAGE_CONFIG_URL + resolveLanlanNameParam(), {
                    cache: 'no-store',
                });
                if (!response.ok) {
                    return '';
                }
                const data = await response.json();
                return extractAutostartCsrfToken(data);
            } catch (_) {
                return '';
            }
        }

        function readInitialToken() {
            if (initialTokenPromise) {
                return initialTokenPromise;
            }
            initialTokenPromise = (async function () {
                const ready = window.pageConfigReady;
                if (ready && typeof ready.then === 'function') {
                    try {
                        const data = await ready;
                        const token = extractAutostartCsrfToken(data);
                        if (token) {
                            cachedToken = token;
                            return token;
                        }
                    } catch (_) {
                        // fall through to explicit refetch below
                    }
                } else if (!warnedMissingPageConfig) {
                    warnedMissingPageConfig = true;
                    console.warn(
                        '[LocalMutationSecurity] window.pageConfigReady missing; '
                        + 'will refetch /api/config/page_config for CSRF token.'
                    );
                }
                // pageConfigReady yielded nothing usable — try a direct fetch.
                const token = await fetchPageConfigToken();
                if (token) {
                    cachedToken = token;
                }
                return cachedToken;
            })();
            return initialTokenPromise;
        }

        async function getToken() {
            if (cachedToken) {
                return cachedToken;
            }
            return readInitialToken();
        }

        async function refreshToken() {
            if (refreshPromise) {
                return refreshPromise;
            }
            refreshPromise = (async function () {
                try {
                    const token = await fetchPageConfigToken();
                    if (token) {
                        cachedToken = token;
                    }
                    return cachedToken;
                } finally {
                    refreshPromise = null;
                }
            })();
            return refreshPromise;
        }

        async function getMutationHeaders() {
            const token = await getToken();
            const headers = {};
            if (token) {
                headers[CSRF_HEADER_NAME] = token;
            }
            return headers;
        }

        return {
            getMutationHeaders: getMutationHeaders,
            refreshToken: refreshToken,
        };
    }

    if (!window.nekoLocalMutationSecurity) {
        window.nekoLocalMutationSecurity = createLocalMutationSecurity();
    }

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
            // window.safeT 仅按 typeof === 'string' 判断，i18next 缺 key 时
            // 会把原始 key 作为字符串返回，导致 'tutorialPrompt.title' 这种
            // 裸 key 被当成翻译显示给用户。这里显式检测 key-literal 回退，
            // 保证 call site 给的中文 fallback 能真正生效。
            const fallbackText = typeof fallback === 'string' ? fallback : key;
            if (typeof window.t === 'function') {
                const translated = window.t(key);
                if (translated && typeof translated === 'string' && translated !== key) {
                    return translated;
                }
            }
            return fallbackText;
        }

        function normalizeMs(value) {
            const number = Number(value);
            return Number.isFinite(number) && number > 0 ? number : 0;
        }

        function isMutationMethod(method) {
            const upper = String(method || 'GET').toUpperCase();
            return upper !== 'GET' && upper !== 'HEAD' && upper !== 'OPTIONS';
        }

        async function mergeMutationHeaders(headers, method) {
            if (!isMutationMethod(method)) {
                return headers;
            }
            const helper = window.nekoLocalMutationSecurity;
            if (!helper || typeof helper.getMutationHeaders !== 'function') {
                return headers;
            }
            try {
                const extra = await helper.getMutationHeaders();
                if (extra && typeof extra === 'object') {
                    return Object.assign({}, headers, extra);
                }
            } catch (error) {
                console.warn(
                    '[' + loggerName + '] getMutationHeaders failed, sending request without CSRF header:',
                    error
                );
            }
            return headers;
        }

        async function readCsrfFailureMarker(response) {
            // We need to inspect the 403 body without consuming it for callers.
            // Callers never receive the raw Response for failed requests
            // (requestJson just throws), so it is safe to read the body here.
            try {
                const cloned = typeof response.clone === 'function' ? response.clone() : response;
                const body = await cloned.json();
                if (body && body.error_code === 'csrf_validation_failed') {
                    return true;
                }
            } catch (_) {
                // Not JSON, not our structured rejection — fall through.
            }
            return false;
        }

        async function requestJson(url, options) {
            const requestOptions = options || {};
            const hasJsonBody = Object.prototype.hasOwnProperty.call(requestOptions, 'json');
            const method = requestOptions.method || 'GET';
            const baseHeaders = Object.assign({}, requestOptions.headers);
            if (hasJsonBody && !baseHeaders['Content-Type']) {
                baseHeaders['Content-Type'] = 'application/json';
            }

            const body = hasJsonBody ? JSON.stringify(requestOptions.json || {}) : requestOptions.body;

            async function sendOnce(headers) {
                return fetch(url, {
                    method: method,
                    headers: headers,
                    body: body,
                    keepalive: !!requestOptions.keepalive,
                    cache: requestOptions.cache,
                });
            }

            const headersWithCsrf = await mergeMutationHeaders(baseHeaders, method);
            let response = await sendOnce(headersWithCsrf);

            if (
                response.status === 403
                && isMutationMethod(method)
                && window.nekoLocalMutationSecurity
                && typeof window.nekoLocalMutationSecurity.refreshToken === 'function'
                && await readCsrfFailureMarker(response)
            ) {
                await window.nekoLocalMutationSecurity.refreshToken();
                const retryHeaders = await mergeMutationHeaders(baseHeaders, method);
                response = await sendOnce(retryHeaders);
            }

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
            requestPromptDisplay: promptDisplayCoordinator.requestPromptDisplay,
            isWeakHomePointerTarget: isWeakHomePointerTarget,
            isWeakHomeFocusTarget: isWeakHomeFocusTarget,
            isWeakHomeChangeTarget: isWeakHomeChangeTarget,
        };
    }

    window.nekoPromptShared = Object.assign({}, window.nekoPromptShared, {
        createPromptTools: createPromptTools,
    });
})();
