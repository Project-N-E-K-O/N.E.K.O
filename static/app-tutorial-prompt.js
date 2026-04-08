(function () {
    'use strict';

    const HEARTBEAT_INTERVAL_MS = 15000;
    const FAST_HEARTBEAT_DELAY_MS = 1200;
    const AUTOSTART_STATUS_MAX_AGE_MS = HEARTBEAT_INTERVAL_MS;
    const TUTORIAL_START_WAIT_TIMEOUT_MS = 15000;
    const FLOW_LOG_PREFIX_FALLBACK = '[AutostartPromptFlow]';

    function createMetricFlowState() {
        return {
            requestInFlight: false,
            pendingHeartbeatAfterFlight: false,
            pendingForegroundMs: 0,
            pendingWeakHomeInteractions: 0,
            pendingChatTurns: 0,
            pendingVoiceSessions: 0,
            promptOpen: false,
        };
    }

    const mod = {};
    const state = Object.assign(createMetricFlowState(), {
        initialized: false,
        heartbeatTimer: null,
        fastHeartbeatTimer: null,
        foregroundStartedAt: null,
        neverRemind: false,
        deferredUntil: 0,
        autostartEnabled: false,
        autostartSupported: true,
        autostartStatusLoaded: false,
        autostartStatusRequestInFlight: null,
        autostartStatusAuthoritative: false,
        autostartProvider: '',
        autostartStatusUpdatedAt: 0,
    });
    const tutorialState = Object.assign(createMetricFlowState(), {
        stateLoaded: false,
        neverRemind: false,
        deferredUntil: 0,
        manualHomeTutorialViewed: false,
        homeTutorialCompleted: false,
        promptDrivenTutorialToken: '',
        activeTutorialRunToken: '',
        activeTutorialRunSource: 'manual',
    });

    function shortPromptToken(promptToken) {
        if (!promptToken) return 'none';
        return String(promptToken).slice(0, 8);
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
        console.log(FLOW_LOG_PREFIX_FALLBACK + ' ' + step, payload);
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

    async function parseJsonResponse(response) {
        if (!response || response.status === 204) {
            return null;
        }

        const contentType = response.headers.get('content-type') || '';
        if (!contentType.toLowerCase().includes('application/json')) {
            return null;
        }

        try {
            return await response.json();
        } catch (_) {
            return null;
        }
    }

    function buildRequestError(response, payload) {
        const error = new Error(
            payload && typeof payload.error === 'string' && payload.error
                ? payload.error
                : ('HTTP ' + response.status)
        );
        error.status = response.status;
        if (payload && typeof payload === 'object') {
            Object.assign(error, payload);
            error.payload = payload;
        }
        return error;
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
        const payload = await parseJsonResponse(response);
        if (!response.ok) {
            throw buildRequestError(response, payload);
        }
        return payload;
    }

    async function getMutationHeaders() {
        const security = window.nekoLocalMutationSecurity || window.nekoAutostartSecurity;
        if (!security || typeof security.getMutationHeaders !== 'function') {
            return {};
        }

        try {
            const headers = await security.getMutationHeaders();
            return headers && typeof headers === 'object' ? headers : {};
        } catch (_) {
            return {};
        }
    }

    async function requestMutationJson(url, options) {
        const requestOptions = options || {};
        const method = String(requestOptions.method || 'GET').toUpperCase();
        if (method === 'GET' || method === 'HEAD') {
            return requestJson(url, requestOptions);
        }

        const headers = Object.assign(
            {},
            await getMutationHeaders(),
            requestOptions.headers || {},
        );
        return requestJson(url, Object.assign({}, requestOptions, { headers: headers }));
    }

    function isAnyPromptOpen() {
        return state.promptOpen || tutorialState.promptOpen;
    }

    function addPendingForegroundDelta(delta) {
        if (delta <= 0) {
            return;
        }
        state.pendingForegroundMs += delta;
        tutorialState.pendingForegroundMs += delta;
    }

    function addPendingCounter(fieldName, delta) {
        if (delta <= 0) {
            return;
        }
        state[fieldName] += delta;
        tutorialState[fieldName] += delta;
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

    function syncForegroundWindow() {
        const now = Date.now();
        if (isForegroundActive()) {
            if (state.foregroundStartedAt === null) {
                state.foregroundStartedAt = now;
                return;
            }
            addPendingForegroundDelta(Math.max(0, now - state.foregroundStartedAt));
            state.foregroundStartedAt = now;
            return;
        }
        if (state.foregroundStartedAt !== null) {
            addPendingForegroundDelta(Math.max(0, now - state.foregroundStartedAt));
            state.foregroundStartedAt = null;
        }
    }

    function consumeForegroundDelta(flowState) {
        syncForegroundWindow();
        const delta = flowState.pendingForegroundMs;
        flowState.pendingForegroundMs = 0;
        return delta;
    }

    function restoreHeartbeatDeltas(flowState, deltas) {
        flowState.pendingForegroundMs += deltas.foregroundDelta;
        flowState.pendingWeakHomeInteractions += deltas.homeInteractionsDelta;
        flowState.pendingChatTurns += deltas.chatTurnsDelta;
        flowState.pendingVoiceSessions += deltas.voiceSessionsDelta;
    }

    function buildAutostartCapabilitySnapshot() {
        return {
            supported: state.autostartSupported,
            enabled: state.autostartEnabled,
            authoritative: state.autostartStatusAuthoritative,
            provider: state.autostartProvider,
        };
    }

    function isAutostartStatusFresh(maxAgeMs) {
        if (!state.autostartStatusLoaded || state.autostartStatusUpdatedAt <= 0) {
            return false;
        }
        return (Date.now() - state.autostartStatusUpdatedAt) < maxAgeMs;
    }

    function getAutostartProvider() {
        const provider = window.nekoAutostartProvider;
        if (
            provider
            && typeof provider.getStatus === 'function'
            && typeof provider.enable === 'function'
        ) {
            return provider;
        }
        return null;
    }

    function getTutorialManager() {
        const manager = window.universalTutorialManager;
        if (!manager || typeof manager !== 'object') {
            return null;
        }
        if (
            typeof manager.requestTutorialStart !== 'function'
            || typeof manager.hasSeenTutorial !== 'function'
        ) {
            return null;
        }
        return manager;
    }

    function currentHomeTutorialSeenLocally() {
        const manager = getTutorialManager();
        if (manager) {
            try {
                return manager.hasSeenTutorial('home');
            } catch (_) {
                // Ignore and fall back to localStorage below.
            }
        }

        try {
            const storageKey = typeof window.getTutorialStorageKeyForPage === 'function'
                ? window.getTutorialStorageKeyForPage('home')
                : 'neko_tutorial_home';
            return localStorage.getItem(storageKey) === 'true';
        } catch (_) {
            return false;
        }
    }

    function applyAutostartServerState(serverState, source) {
        if (!serverState || typeof serverState !== 'object') {
            return;
        }

        const previous = {
            autostartEnabled: state.autostartEnabled,
            neverRemind: state.neverRemind,
            deferredUntil: state.deferredUntil,
        };
        const status = serverState.status ? String(serverState.status).toLowerCase() : '';
        const serverAutostartEnabled = serverState.autostart_enabled === true;
        const completedAt = normalizeMs(serverState.completed_at);

        state.neverRemind = serverState.never_remind === true;
        state.deferredUntil = normalizeMs(serverState.deferred_until);
        if (!state.autostartStatusAuthoritative) {
            state.autostartEnabled = serverAutostartEnabled || status === 'completed' || completedAt > 0;
        }

        const changed = previous.autostartEnabled !== state.autostartEnabled
            || previous.neverRemind !== state.neverRemind
            || previous.deferredUntil !== state.deferredUntil;

        if (changed || source === 'initial-state') {
            logFlow('autostart-state-sync', {
                source: source || 'unknown',
                status: status || null,
                autostartEnabled: state.autostartEnabled,
                neverRemind: state.neverRemind,
                deferredUntil: state.deferredUntil || 0,
            });
        }
    }

    function applyTutorialServerState(serverState, source) {
        if (!serverState || typeof serverState !== 'object') {
            return;
        }

        const previous = {
            neverRemind: tutorialState.neverRemind,
            deferredUntil: tutorialState.deferredUntil,
            manualHomeTutorialViewed: tutorialState.manualHomeTutorialViewed,
            homeTutorialCompleted: tutorialState.homeTutorialCompleted,
        };
        const status = serverState.status ? String(serverState.status).toLowerCase() : '';
        const startedAt = normalizeMs(serverState.started_at);
        const completedAt = normalizeMs(serverState.completed_at);
        const manualViewedAt = normalizeMs(serverState.manual_home_tutorial_viewed_at);

        tutorialState.stateLoaded = true;
        tutorialState.neverRemind = serverState.never_remind === true;
        tutorialState.deferredUntil = normalizeMs(serverState.deferred_until);
        tutorialState.manualHomeTutorialViewed = !!(
            serverState.manual_home_tutorial_viewed === true
            || manualViewedAt > 0
            || status === 'started'
            || startedAt > 0
        );
        tutorialState.homeTutorialCompleted = !!(
            serverState.home_tutorial_completed === true
            || status === 'completed'
            || completedAt > 0
        );

        if (tutorialState.homeTutorialCompleted) {
            tutorialState.activeTutorialRunToken = '';
        }

        const changed = previous.neverRemind !== tutorialState.neverRemind
            || previous.deferredUntil !== tutorialState.deferredUntil
            || previous.manualHomeTutorialViewed !== tutorialState.manualHomeTutorialViewed
            || previous.homeTutorialCompleted !== tutorialState.homeTutorialCompleted;

        if (changed || source === 'initial-state') {
            logFlow('tutorial-state-sync', {
                source: source || 'unknown',
                status: status || null,
                neverRemind: tutorialState.neverRemind,
                deferredUntil: tutorialState.deferredUntil || 0,
                manualHomeTutorialViewed: tutorialState.manualHomeTutorialViewed,
                homeTutorialCompleted: tutorialState.homeTutorialCompleted,
            });
        }
    }

    function applyAutostartCapabilityState(response) {
        state.autostartStatusLoaded = true;
        state.autostartSupported = response && response.supported !== false;
        state.autostartEnabled = !!(response && response.enabled);
        state.autostartStatusAuthoritative = !!(response && response.authoritative);
        state.autostartProvider = response && response.provider ? String(response.provider) : '';
        state.autostartStatusUpdatedAt = Date.now();
    }

    async function loadInitialAutostartState() {
        try {
            const response = await requestJson('/api/autostart-prompt/state', {
                cache: 'no-store',
            });
            if (response && response.state) {
                applyAutostartServerState(response.state, 'initial-state');
            }
        } catch (error) {
            console.warn('[AutostartPrompt] failed to load initial state:', error);
        }
    }

    async function loadInitialTutorialState() {
        try {
            const response = await requestJson('/api/tutorial-prompt/state', {
                cache: 'no-store',
            });
            if (response && response.state) {
                applyTutorialServerState(response.state, 'initial-state');
            }
        } catch (error) {
            console.warn('[TutorialPrompt] failed to load initial state:', error);
        }
    }

    async function loadAutostartStatus(options) {
        const requestOptions = options || {};
        if (state.autostartStatusRequestInFlight) {
            return state.autostartStatusRequestInFlight;
        }

        const provider = getAutostartProvider();
        if (!provider) {
            return Promise.reject(new Error('autostart_provider_unavailable'));
        }

        const request = provider.getStatus().then(function (response) {
            applyAutostartCapabilityState(response);
            logFlow('autostart-status', {
                source: requestOptions.source || 'unknown',
                provider: response && response.provider,
                authoritative: !!(response && response.authoritative),
                supported: state.autostartSupported,
                enabled: state.autostartEnabled,
                platform: response && response.platform,
                mechanism: response && response.mechanism,
            });
            return response;
        }).catch(function (error) {
            if (!requestOptions.silent) {
                console.warn('[AutostartPrompt] failed to load autostart status:', error);
            }
            throw error;
        }).finally(function () {
            if (state.autostartStatusRequestInFlight === request) {
                state.autostartStatusRequestInFlight = null;
            }
        });

        state.autostartStatusRequestInFlight = request;
        return request;
    }

    async function ensureAutostartStatusFresh(options) {
        const requestOptions = options || {};
        const maxAgeMs = normalizeMs(requestOptions.maxAgeMs) || AUTOSTART_STATUS_MAX_AGE_MS;
        if (!requestOptions.force && isAutostartStatusFresh(maxAgeMs)) {
            return buildAutostartCapabilitySnapshot();
        }

        try {
            return await loadAutostartStatus(requestOptions);
        } catch (error) {
            if (requestOptions.invalidateAuthoritative !== false) {
                state.autostartStatusAuthoritative = false;
            }
            throw error;
        }
    }

    async function getAutostartStatusForHeartbeat() {
        try {
            const response = await ensureAutostartStatusFresh({
                source: 'heartbeat',
                silent: true,
            });
            return {
                supported: response && response.supported !== false,
                enabled: !!(response && response.enabled),
                authoritative: !!(response && response.authoritative),
                provider: response && response.provider ? String(response.provider) : state.autostartProvider,
            };
        } catch (_) {
            return {
                supported: state.autostartSupported,
                enabled: false,
                authoritative: false,
                provider: state.autostartProvider,
            };
        }
    }

    async function enableAutostart() {
        const provider = getAutostartProvider();
        if (!provider) {
            throw new Error('autostart_provider_unavailable');
        }

        const response = await provider.enable();
        applyAutostartCapabilityState(response);
        logFlow('autostart-enabled', {
            provider: response && response.provider,
            authoritative: !!(response && response.authoritative),
            enabled: state.autostartEnabled,
            platform: response && response.platform,
            mechanism: response && response.mechanism,
        });
        return response;
    }

    function scheduleFastHeartbeat() {
        if (!state.initialized) return;
        if (state.fastHeartbeatTimer) return;
        state.fastHeartbeatTimer = setTimeout(function () {
            state.fastHeartbeatTimer = null;
            flushHeartbeats();
        }, FAST_HEARTBEAT_DELAY_MS);
    }

    function flushHeartbeats() {
        void sendTutorialHeartbeat();
        void sendAutostartHeartbeat();
    }

    function isAutostartPromptSuppressedLocally() {
        return state.autostartEnabled
            || state.neverRemind
            || state.deferredUntil > Date.now();
    }

    function isHomeTutorialRunning() {
        const manager = getTutorialManager();
        return !!(
            manager
            && manager.currentPage === 'home'
            && manager.isTutorialRunning
        );
    }

    function isTutorialPromptSuppressedLocally() {
        return tutorialState.homeTutorialCompleted
            || tutorialState.manualHomeTutorialViewed
            || tutorialState.neverRemind
            || tutorialState.deferredUntil > Date.now()
            || currentHomeTutorialSeenLocally()
            || isHomeTutorialRunning();
    }

    function isWeakHomePointerTarget(target) {
        if (!(target instanceof Element)) {
            return false;
        }

        if (target.closest('.modal-overlay, .driver-popover, .driver-overlay')) {
            return false;
        }

        return Boolean(target.closest(
            'button, a[href], summary, [role="button"], [data-home-action]'
        ));
    }

    function isWeakHomeFocusTarget(target) {
        if (!(target instanceof Element)) {
            return false;
        }

        if (target.closest('.modal-overlay, .driver-popover, .driver-overlay')) {
            return false;
        }

        return Boolean(target.closest('input, select, textarea, [contenteditable="true"]'));
    }

    function isWeakHomeChangeTarget(target) {
        if (!(target instanceof Element)) {
            return false;
        }

        if (target.closest('.modal-overlay, .driver-popover, .driver-overlay')) {
            return false;
        }

        return Boolean(target.closest('input, select, textarea'));
    }

    function noteWeakHomeInteraction(source, target) {
        addPendingCounter('pendingWeakHomeInteractions', 1);
        logFlow('weak-action', {
            source: source,
            target: describeTarget(target),
            autostartPendingWeakHomeInteractions: state.pendingWeakHomeInteractions,
            tutorialPendingWeakHomeInteractions: tutorialState.pendingWeakHomeInteractions,
        });
        scheduleFastHeartbeat();
    }

    async function postAutostartDecision(payload) {
        try {
            const response = await requestMutationJson('/api/autostart-prompt/decision', {
                method: 'POST',
                json: payload,
            });
            if (response && response.state) {
                applyAutostartServerState(response.state, 'decision');
            }
            logFlow('autostart-decision', {
                decision: payload && payload.decision,
                result: payload && payload.result,
                token: shortPromptToken(payload && payload.prompt_token),
                status: response && response.state ? response.state.status : null,
            });
        } catch (error) {
            console.warn('[AutostartPrompt] failed to persist decision:', error);
        }
    }

    async function postAutostartShownAck(promptToken) {
        if (!promptToken) return;
        try {
            const response = await requestMutationJson('/api/autostart-prompt/shown', {
                method: 'POST',
                json: { prompt_token: promptToken },
            });
            if (response && response.state) {
                applyAutostartServerState(response.state, 'shown');
            }
            logFlow('autostart-shown', {
                token: shortPromptToken(promptToken),
                alreadyAcknowledged: !!(response && response.already_acknowledged),
            });
        } catch (error) {
            console.warn('[AutostartPrompt] failed to ack prompt shown:', error);
        }
    }

    async function handleAutostartPromptAcceptance(promptToken) {
        try {
            const response = await enableAutostart();
            if (!response || response.enabled !== true) {
                throw new Error(
                    response && response.error
                        ? response.error
                        : 'autostart_enable_failed'
                );
            }
            await postAutostartDecision({
                decision: 'accept',
                result: 'enabled',
                autostart_provider: response && response.provider,
                prompt_token: promptToken,
            });
            scheduleFastHeartbeat();
        } catch (error) {
            const message = error && error.message ? error.message : String(error);
            console.warn('[AutostartPrompt] failed to enable autostart:', error);
            await postAutostartDecision({
                decision: 'accept',
                result: 'failed',
                error: message,
                prompt_token: promptToken,
            });

            if (typeof window.showStatusToast === 'function') {
                window.showStatusToast(
                    translate(
                        'autostartPrompt.enableFailed',
                        '暂时无法开启开机自启动，请稍后再试'
                    ),
                    3500
                );
            }
        }
    }

    async function maybeShowAutostartPrompt(promptToken) {
        if (state.promptOpen) {
            return;
        }
        if (!promptToken) {
            return;
        }
        try {
            await ensureAutostartStatusFresh({
                source: 'prompt-check',
                silent: true,
            });
        } catch (_) {
            return;
        }
        if (!state.autostartSupported || isAutostartPromptSuppressedLocally()) {
            return;
        }
        if (typeof window.showDecisionPrompt !== 'function') {
            return;
        }

        state.promptOpen = true;
        logFlow('autostart-prompt-open', { token: shortPromptToken(promptToken) });
        try {
            const decision = await window.showDecisionPrompt({
                title: translate('autostartPrompt.title', '要不要让 N.E.K.O 开机自动启动？'),
                message: translate(
                    'autostartPrompt.message',
                    '这样下次打开电脑后，N.E.K.O 会自动准备好，不用你再手动启动。'
                ),
                note: translate(
                    'autostartPrompt.note',
                    '只会为当前用户开启，之后也可以随时关闭。'
                ),
                dismissValue: null,
                closeOnClickOutside: false,
                closeOnEscape: false,
                onShown: function () {
                    return postAutostartShownAck(promptToken);
                },
                buttons: [
                    {
                        value: 'never',
                        text: translate('autostartPrompt.never', '不再提示'),
                        variant: 'secondary'
                    },
                    {
                        value: 'later',
                        text: translate('autostartPrompt.later', '稍后再说'),
                        variant: 'secondary'
                    },
                    {
                        value: 'accept',
                        text: translate('autostartPrompt.startNow', '开启自启动'),
                        variant: 'primary'
                    }
                ]
            });

            if (decision === 'never') {
                await postAutostartDecision({ decision: 'never', prompt_token: promptToken });
                return;
            }
            if (decision === 'later') {
                await postAutostartDecision({ decision: 'later', prompt_token: promptToken });
                return;
            }
            if (decision === 'accept') {
                await handleAutostartPromptAcceptance(promptToken);
            }
        } finally {
            state.promptOpen = false;
        }
    }

    async function postTutorialDecision(payload) {
        try {
            const response = await requestMutationJson('/api/tutorial-prompt/decision', {
                method: 'POST',
                json: payload,
            });
            if (response && response.state) {
                applyTutorialServerState(response.state, 'decision');
            }
            logFlow('tutorial-decision', {
                decision: payload && payload.decision,
                result: payload && payload.result,
                token: shortPromptToken(payload && payload.prompt_token),
                status: response && response.state ? response.state.status : null,
            });
        } catch (error) {
            console.warn('[TutorialPrompt] failed to persist decision:', error);
        }
    }

    async function postTutorialShownAck(promptToken) {
        if (!promptToken) return;
        try {
            const response = await requestMutationJson('/api/tutorial-prompt/shown', {
                method: 'POST',
                json: { prompt_token: promptToken },
            });
            if (response && response.state) {
                applyTutorialServerState(response.state, 'shown');
            }
            logFlow('tutorial-shown', {
                token: shortPromptToken(promptToken),
                alreadyAcknowledged: !!(response && response.already_acknowledged),
            });
        } catch (error) {
            console.warn('[TutorialPrompt] failed to ack prompt shown:', error);
        }
    }

    function normalizeTutorialSource(source) {
        return source === 'idle_prompt' ? 'idle_prompt' : 'manual';
    }

    function waitForTutorialManager(timeoutMs) {
        const immediateManager = getTutorialManager();
        if (immediateManager) {
            return Promise.resolve(immediateManager);
        }

        return new Promise(function (resolve, reject) {
            const startedAt = Date.now();
            const poll = function () {
                const manager = getTutorialManager();
                if (manager) {
                    resolve(manager);
                    return;
                }
                if ((Date.now() - startedAt) >= timeoutMs) {
                    reject(new Error('tutorial_manager_unavailable'));
                    return;
                }
                setTimeout(poll, 100);
            };
            poll();
        });
    }

    function waitForTutorialStartedSignal(source, timeoutMs) {
        const expectedSource = normalizeTutorialSource(source);
        return new Promise(function (resolve, reject) {
            let timeoutId = null;

            const cleanup = function () {
                window.removeEventListener('neko:tutorial-started', onStarted);
                if (timeoutId !== null) {
                    clearTimeout(timeoutId);
                    timeoutId = null;
                }
            };

            const onStarted = function (event) {
                const detail = event && event.detail ? event.detail : {};
                if (detail.page !== 'home') {
                    return;
                }
                if (normalizeTutorialSource(detail.source) !== expectedSource) {
                    return;
                }
                cleanup();
                resolve(detail);
            };

            timeoutId = setTimeout(function () {
                cleanup();
                reject(new Error('tutorial_start_timeout'));
            }, timeoutMs);

            window.addEventListener('neko:tutorial-started', onStarted);
        });
    }

    function buildTutorialEventPayload(detail, extraPayload) {
        const source = normalizeTutorialSource(detail && detail.source);
        const payload = Object.assign(
            {
                page: 'home',
                source: source,
            },
            extraPayload || {},
        );

        if (source === 'idle_prompt' && tutorialState.promptDrivenTutorialToken) {
            payload.prompt_token = tutorialState.promptDrivenTutorialToken;
        }
        return payload;
    }

    async function syncTutorialStarted(detail) {
        if (!detail || detail.page !== 'home') {
            return '';
        }

        const payload = buildTutorialEventPayload(detail);
        const response = await requestMutationJson('/api/tutorial-prompt/tutorial-started', {
            method: 'POST',
            json: payload,
        });
        if (response && response.state) {
            applyTutorialServerState(response.state, 'tutorial-started');
        }
        tutorialState.activeTutorialRunToken = response && response.tutorial_run_token
            ? String(response.tutorial_run_token)
            : '';
        tutorialState.activeTutorialRunSource = payload.source;
        if (payload.source === 'idle_prompt') {
            tutorialState.promptDrivenTutorialToken = '';
        }
        logFlow('tutorial-started-sync', {
            source: payload.source,
            tutorialRunToken: shortPromptToken(tutorialState.activeTutorialRunToken),
            promptToken: shortPromptToken(payload.prompt_token),
        });
        scheduleFastHeartbeat();
        return tutorialState.activeTutorialRunToken;
    }

    async function ensureTutorialRunToken(source) {
        const normalizedSource = normalizeTutorialSource(source);
        if (
            tutorialState.activeTutorialRunToken
            && tutorialState.activeTutorialRunSource === normalizedSource
        ) {
            return tutorialState.activeTutorialRunToken;
        }

        return syncTutorialStarted({
            page: 'home',
            source: normalizedSource,
        });
    }

    async function syncTutorialCompleted(detail) {
        if (!detail || detail.page !== 'home') {
            return;
        }

        const source = normalizeTutorialSource(detail.source);
        try {
            const tutorialRunToken = await ensureTutorialRunToken(source);
            if (!tutorialRunToken) {
                throw new Error('tutorial_run_token_unavailable');
            }

            const response = await requestMutationJson('/api/tutorial-prompt/tutorial-completed', {
                method: 'POST',
                json: {
                    page: 'home',
                    source: source,
                    tutorial_run_token: tutorialRunToken,
                },
            });
            if (response && response.state) {
                applyTutorialServerState(response.state, 'tutorial-completed');
            }
            tutorialState.activeTutorialRunToken = '';
            tutorialState.activeTutorialRunSource = source;
            if (source === 'idle_prompt') {
                tutorialState.promptDrivenTutorialToken = '';
            }
            logFlow('tutorial-completed-sync', {
                source: source,
                tutorialRunToken: shortPromptToken(tutorialRunToken),
            });
            scheduleFastHeartbeat();
        } catch (error) {
            console.warn('[TutorialPrompt] failed to persist tutorial completion:', error);
        }
    }

    async function startHomeTutorialFromPrompt(promptToken) {
        tutorialState.promptDrivenTutorialToken = promptToken || '';
        const manager = await waitForTutorialManager(5000);
        const startedSignal = waitForTutorialStartedSignal(
            'idle_prompt',
            TUTORIAL_START_WAIT_TIMEOUT_MS,
        );
        const started = await manager.requestTutorialStart('idle_prompt', 0);
        if (started !== true) {
            throw new Error('tutorial_start_rejected');
        }
        return startedSignal;
    }

    async function handleTutorialPromptAcceptance(promptToken) {
        try {
            await startHomeTutorialFromPrompt(promptToken);
            await postTutorialDecision({
                decision: 'accept',
                result: 'started',
                prompt_token: promptToken,
            });
            scheduleFastHeartbeat();
        } catch (error) {
            const message = error && error.message ? error.message : String(error);
            tutorialState.promptDrivenTutorialToken = '';
            console.warn('[TutorialPrompt] failed to start tutorial from prompt:', error);
            await postTutorialDecision({
                decision: 'accept',
                result: 'failed',
                error: message,
                prompt_token: promptToken,
            });

            if (typeof window.showStatusToast === 'function') {
                window.showStatusToast(
                    translate(
                        'tutorialPrompt.startFailed',
                        '暂时无法开始新手引导，请稍后再试'
                    ),
                    3500
                );
            }
        }
    }

    async function maybeShowTutorialPrompt(promptToken) {
        if (tutorialState.promptOpen) {
            return;
        }
        if (!promptToken || isTutorialPromptSuppressedLocally()) {
            return;
        }
        if (typeof window.showDecisionPrompt !== 'function') {
            return;
        }

        tutorialState.promptOpen = true;
        logFlow('tutorial-prompt-open', { token: shortPromptToken(promptToken) });
        try {
            const decision = await window.showDecisionPrompt({
                title: translate('tutorialPrompt.title', '要不要开始主页新手引导？'),
                message: translate(
                    'tutorialPrompt.message',
                    '我可以带你快速认识主页上的核心入口，用最短路径上手 N.E.K.O。'
                ),
                note: translate(
                    'tutorialPrompt.note',
                    '整个过程随时都可以跳过，也可以之后再从记忆浏览里重新打开。'
                ),
                dismissValue: null,
                closeOnClickOutside: false,
                closeOnEscape: false,
                onShown: function () {
                    return postTutorialShownAck(promptToken);
                },
                buttons: [
                    {
                        value: 'never',
                        text: translate('tutorialPrompt.never', '不再提示'),
                        variant: 'secondary'
                    },
                    {
                        value: 'later',
                        text: translate('tutorialPrompt.later', '稍后再说'),
                        variant: 'secondary'
                    },
                    {
                        value: 'accept',
                        text: translate('tutorialPrompt.startNow', '开始引导'),
                        variant: 'primary'
                    }
                ]
            });

            if (decision === 'never') {
                await postTutorialDecision({ decision: 'never', prompt_token: promptToken });
                return;
            }
            if (decision === 'later') {
                await postTutorialDecision({ decision: 'later', prompt_token: promptToken });
                return;
            }
            if (decision === 'accept') {
                await handleTutorialPromptAcceptance(promptToken);
            }
        } finally {
            tutorialState.promptOpen = false;
        }
    }

    async function sendTutorialHeartbeat() {
        if (!state.initialized) return;
        if (tutorialState.requestInFlight) {
            tutorialState.pendingHeartbeatAfterFlight = true;
            return;
        }

        tutorialState.requestInFlight = true;
        const deltas = {
            foregroundDelta: 0,
            homeInteractionsDelta: 0,
            chatTurnsDelta: 0,
            voiceSessionsDelta: 0,
        };

        try {
            deltas.foregroundDelta = consumeForegroundDelta(tutorialState);
            deltas.homeInteractionsDelta = tutorialState.pendingWeakHomeInteractions;
            deltas.chatTurnsDelta = tutorialState.pendingChatTurns;
            deltas.voiceSessionsDelta = tutorialState.pendingVoiceSessions;

            tutorialState.pendingWeakHomeInteractions = 0;
            tutorialState.pendingChatTurns = 0;
            tutorialState.pendingVoiceSessions = 0;

            const localTutorialSeen = currentHomeTutorialSeenLocally();
            const payload = {
                foreground_ms_delta: deltas.foregroundDelta,
                home_interactions_delta: deltas.homeInteractionsDelta,
                chat_turns_delta: deltas.chatTurnsDelta,
                voice_sessions_delta: deltas.voiceSessionsDelta,
                manual_home_tutorial_viewed: localTutorialSeen,
                home_tutorial_completed: localTutorialSeen,
            };

            const data = await requestMutationJson('/api/tutorial-prompt/heartbeat', {
                method: 'POST',
                json: payload,
            });
            if (data && data.state) {
                applyTutorialServerState(data.state, 'heartbeat');
            }
            logFlow('tutorial-heartbeat', {
                foregroundMsDelta: deltas.foregroundDelta,
                weakHomeInteractionsDelta: deltas.homeInteractionsDelta,
                chatTurnsDelta: deltas.chatTurnsDelta,
                voiceSessionsDelta: deltas.voiceSessionsDelta,
                shouldPrompt: !!(data && data.should_prompt),
                reason: data && data.prompt_reason,
                token: shortPromptToken(data && data.prompt_token),
                localTutorialSeen: localTutorialSeen,
            });

            if (data && data.should_prompt) {
                try {
                    await maybeShowTutorialPrompt(data.prompt_token);
                } catch (error) {
                    console.warn('[TutorialPrompt] prompt display failed:', error);
                }
            }
        } catch (error) {
            restoreHeartbeatDeltas(tutorialState, deltas);
            console.warn('[TutorialPrompt] heartbeat failed:', error);
        } finally {
            tutorialState.requestInFlight = false;
            if (tutorialState.pendingHeartbeatAfterFlight) {
                tutorialState.pendingHeartbeatAfterFlight = false;
                scheduleFastHeartbeat();
            }
        }
    }

    async function sendAutostartHeartbeat() {
        if (!state.initialized) return;
        if (state.requestInFlight) {
            state.pendingHeartbeatAfterFlight = true;
            return;
        }

        state.requestInFlight = true;
        const deltas = {
            foregroundDelta: 0,
            homeInteractionsDelta: 0,
            chatTurnsDelta: 0,
            voiceSessionsDelta: 0,
        };

        try {
            const autostartStatus = await getAutostartStatusForHeartbeat();
            if (
                !autostartStatus.authoritative
                && !autostartStatus.supported
                && !autostartStatus.enabled
            ) {
                return;
            }

            deltas.foregroundDelta = consumeForegroundDelta(state);
            deltas.homeInteractionsDelta = state.pendingWeakHomeInteractions;
            deltas.chatTurnsDelta = state.pendingChatTurns;
            deltas.voiceSessionsDelta = state.pendingVoiceSessions;

            state.pendingWeakHomeInteractions = 0;
            state.pendingChatTurns = 0;
            state.pendingVoiceSessions = 0;

            const payload = {
                foreground_ms_delta: deltas.foregroundDelta,
                home_interactions_delta: deltas.homeInteractionsDelta,
                chat_turns_delta: deltas.chatTurnsDelta,
                voice_sessions_delta: deltas.voiceSessionsDelta,
                autostart_enabled: autostartStatus.enabled,
                autostart_supported: autostartStatus.supported,
                autostart_status_authoritative: autostartStatus.authoritative,
                autostart_provider: autostartStatus.provider,
            };

            const data = await requestMutationJson('/api/autostart-prompt/heartbeat', {
                method: 'POST',
                json: payload,
            });
            if (data && data.state) {
                applyAutostartServerState(data.state, 'heartbeat');
            }
            logFlow('autostart-heartbeat', {
                foregroundMsDelta: deltas.foregroundDelta,
                weakHomeInteractionsDelta: deltas.homeInteractionsDelta,
                chatTurnsDelta: deltas.chatTurnsDelta,
                voiceSessionsDelta: deltas.voiceSessionsDelta,
                shouldPrompt: !!(data && data.should_prompt),
                reason: data && data.prompt_reason,
                token: shortPromptToken(data && data.prompt_token),
                autostartEnabled: state.autostartEnabled,
                provider: autostartStatus.provider || null,
                authoritative: autostartStatus.authoritative,
            });

            if (data && data.should_prompt) {
                try {
                    await maybeShowAutostartPrompt(data.prompt_token);
                } catch (error) {
                    console.warn('[AutostartPrompt] prompt display failed:', error);
                }
            }
        } catch (error) {
            restoreHeartbeatDeltas(state, deltas);
            console.warn('[AutostartPrompt] heartbeat failed:', error);
        } finally {
            state.requestInFlight = false;
            if (state.pendingHeartbeatAfterFlight) {
                state.pendingHeartbeatAfterFlight = false;
                scheduleFastHeartbeat();
            }
        }
    }

    function bindEvents() {
        document.addEventListener('visibilitychange', syncForegroundWindow);
        window.addEventListener('focus', syncForegroundWindow);
        window.addEventListener('blur', syncForegroundWindow);
        document.addEventListener('pointerdown', function (event) {
            if (isAnyPromptOpen()) {
                return;
            }
            if (isWeakHomePointerTarget(event.target)) {
                noteWeakHomeInteraction('pointer', event.target);
            }
        }, true);
        document.addEventListener('focusin', function (event) {
            if (isAnyPromptOpen()) {
                return;
            }
            if (isWeakHomeFocusTarget(event.target)) {
                noteWeakHomeInteraction('focus', event.target);
            }
        }, true);
        document.addEventListener('change', function (event) {
            if (isAnyPromptOpen()) {
                return;
            }
            if (isWeakHomeChangeTarget(event.target)) {
                noteWeakHomeInteraction('change', event.target);
            }
        }, true);

        window.addEventListener('neko:user-content-sent', function () {
            addPendingCounter('pendingChatTurns', 1);
            logFlow('strong-action', {
                type: 'chat_turn',
                autostartPendingChatTurns: state.pendingChatTurns,
                tutorialPendingChatTurns: tutorialState.pendingChatTurns,
            });
            scheduleFastHeartbeat();
        });

        window.addEventListener('neko:voice-session-started', function () {
            addPendingCounter('pendingVoiceSessions', 1);
            logFlow('strong-action', {
                type: 'voice_session',
                autostartPendingVoiceSessions: state.pendingVoiceSessions,
                tutorialPendingVoiceSessions: tutorialState.pendingVoiceSessions,
            });
            scheduleFastHeartbeat();
        });

        window.addEventListener('neko:tutorial-started', function (event) {
            void syncTutorialStarted(event && event.detail ? event.detail : {});
        });

        window.addEventListener('neko:tutorial-completed', function (event) {
            void syncTutorialCompleted(event && event.detail ? event.detail : {});
        });

        window.addEventListener('beforeunload', syncForegroundWindow);
    }

    mod.init = function init() {
        if (state.initialized) return;

        state.initialized = true;
        syncForegroundWindow();
        bindEvents();

        state.heartbeatTimer = setInterval(function () {
            flushHeartbeats();
        }, HEARTBEAT_INTERVAL_MS);

        void Promise.allSettled([
            loadInitialTutorialState(),
            loadInitialAutostartState(),
            ensureAutostartStatusFresh({ source: 'init', silent: true }),
        ]).finally(function () {
            flushHeartbeats();
        });
    };

    window.appTutorialPrompt = mod;
    window.appAutostartPrompt = mod;
})();
