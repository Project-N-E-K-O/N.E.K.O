(function () {
    'use strict';

    const HEARTBEAT_INTERVAL_MS = 15000;
    const FAST_HEARTBEAT_DELAY_MS = 1200;
    const AUTOSTART_STATUS_MAX_AGE_MS = HEARTBEAT_INTERVAL_MS;
    const FLOW_LOG_PREFIX_FALLBACK = '[AutostartPromptFlow]';

    const mod = {};
    const state = {
        initialized: false,
        heartbeatTimer: null,
        fastHeartbeatTimer: null,
        requestInFlight: false,
        pendingHeartbeatAfterFlight: false,
        promptOpen: false,
        pendingForegroundMs: 0,
        foregroundStartedAt: null,
        pendingWeakHomeInteractions: 0,
        pendingChatTurns: 0,
        pendingVoiceSessions: 0,
        neverRemind: false,
        deferredUntil: 0,
        autostartEnabled: false,
        autostartSupported: true,
        autostartStatusLoaded: false,
        autostartStatusRequestInFlight: null,
        autostartStatusAuthoritative: false,
        autostartProvider: '',
        autostartStatusUpdatedAt: 0,
    };

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

    function applyServerState(serverState, source) {
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
            logFlow('state-sync', {
                source: source || 'unknown',
                status: status || null,
                autostartEnabled: state.autostartEnabled,
                neverRemind: state.neverRemind,
                deferredUntil: state.deferredUntil || 0,
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

    async function postDecision(payload) {
        try {
            const response = await requestJson('/api/autostart-prompt/decision', {
                method: 'POST',
                json: payload,
            });
            if (response && response.state) {
                applyServerState(response.state, 'decision');
            }
            logFlow('decision', {
                decision: payload && payload.decision,
                result: payload && payload.result,
                token: shortPromptToken(payload && payload.prompt_token),
                status: response && response.state ? response.state.status : null,
            });
        } catch (error) {
            console.warn('[AutostartPrompt] failed to persist decision:', error);
        }
    }

    async function postShownAck(promptToken) {
        if (!promptToken) return;
        try {
            const response = await requestJson('/api/autostart-prompt/shown', {
                method: 'POST',
                json: { prompt_token: promptToken },
            });
            if (response && response.state) {
                applyServerState(response.state, 'shown');
            }
            logFlow('shown', {
                token: shortPromptToken(promptToken),
                alreadyAcknowledged: !!(response && response.already_acknowledged),
            });
        } catch (error) {
            console.warn('[AutostartPrompt] failed to ack prompt shown:', error);
        }
    }

    async function loadInitialServerState() {
        try {
            const response = await requestJson('/api/autostart-prompt/state', {
                cache: 'no-store',
            });
            if (response && response.state) {
                applyServerState(response.state, 'initial-state');
            }
        } catch (error) {
            console.warn('[AutostartPrompt] failed to load initial state:', error);
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
            void sendHeartbeat();
        }, FAST_HEARTBEAT_DELAY_MS);
    }

    function isPromptSuppressedLocally() {
        return state.autostartEnabled
            || state.neverRemind
            || state.deferredUntil > Date.now();
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
        state.pendingWeakHomeInteractions += 1;
        logFlow('weak-action', {
            source: source,
            target: describeTarget(target),
            pendingWeakHomeInteractions: state.pendingWeakHomeInteractions,
        });
        scheduleFastHeartbeat();
    }

    async function handlePromptAcceptance(promptToken) {
        try {
            const response = await enableAutostart();
            if (!response || response.enabled !== true) {
                throw new Error(
                    response && response.error
                        ? response.error
                        : 'autostart_enable_failed'
                );
            }
            await postDecision({
                decision: 'accept',
                result: 'enabled',
                autostart_provider: response && response.provider,
                prompt_token: promptToken,
            });
            scheduleFastHeartbeat();
        } catch (error) {
            const message = error && error.message ? error.message : String(error);
            console.warn('[AutostartPrompt] failed to enable autostart:', error);
            await postDecision({
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

    async function maybeShowPrompt(promptToken) {
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
        if (!state.autostartSupported || isPromptSuppressedLocally()) {
            return;
        }
        if (typeof window.showDecisionPrompt !== 'function') {
            return;
        }

        state.promptOpen = true;
        logFlow('prompt-open', { token: shortPromptToken(promptToken) });
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
                    return postShownAck(promptToken);
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
                await postDecision({ decision: 'never', prompt_token: promptToken });
                return;
            }
            if (decision === 'later') {
                await postDecision({ decision: 'later', prompt_token: promptToken });
                return;
            }
            if (decision === 'accept') {
                await handlePromptAcceptance(promptToken);
            }
        } finally {
            state.promptOpen = false;
        }
    }

    async function sendHeartbeat() {
        if (!state.initialized) return;
        if (state.requestInFlight) {
            state.pendingHeartbeatAfterFlight = true;
            return;
        }

        state.requestInFlight = true;
        let foregroundDelta = 0;
        let homeInteractionsDelta = 0;
        let chatTurnsDelta = 0;
        let voiceSessionsDelta = 0;

        try {
            const autostartStatus = await getAutostartStatusForHeartbeat();
            if (
                !autostartStatus.authoritative
                && !autostartStatus.supported
                && !autostartStatus.enabled
            ) {
                return;
            }

            foregroundDelta = consumeForegroundDelta();
            homeInteractionsDelta = state.pendingWeakHomeInteractions;
            chatTurnsDelta = state.pendingChatTurns;
            voiceSessionsDelta = state.pendingVoiceSessions;

            const payload = {
                foreground_ms_delta: foregroundDelta,
                home_interactions_delta: homeInteractionsDelta,
                chat_turns_delta: chatTurnsDelta,
                voice_sessions_delta: voiceSessionsDelta,
                autostart_enabled: autostartStatus.enabled,
                autostart_supported: autostartStatus.supported,
                autostart_status_authoritative: autostartStatus.authoritative,
                autostart_provider: autostartStatus.provider,
            };

            state.pendingWeakHomeInteractions = 0;
            state.pendingChatTurns = 0;
            state.pendingVoiceSessions = 0;

            const data = await requestJson('/api/autostart-prompt/heartbeat', {
                method: 'POST',
                json: payload,
            });
            if (data && data.state) {
                applyServerState(data.state, 'heartbeat');
            }
            logFlow('heartbeat', {
                foregroundMsDelta: foregroundDelta,
                weakHomeInteractionsDelta: homeInteractionsDelta,
                chatTurnsDelta: chatTurnsDelta,
                voiceSessionsDelta: voiceSessionsDelta,
                shouldPrompt: !!(data && data.should_prompt),
                reason: data && data.prompt_reason,
                token: shortPromptToken(data && data.prompt_token),
                autostartEnabled: state.autostartEnabled,
                provider: autostartStatus.provider || null,
                authoritative: autostartStatus.authoritative,
            });
            if (data && data.should_prompt) {
                try {
                    await maybeShowPrompt(data.prompt_token);
                } catch (error) {
                    console.warn('[AutostartPrompt] prompt display failed:', error);
                }
            }
        } catch (error) {
            state.pendingForegroundMs += foregroundDelta;
            state.pendingWeakHomeInteractions += homeInteractionsDelta;
            state.pendingChatTurns += chatTurnsDelta;
            state.pendingVoiceSessions += voiceSessionsDelta;
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
            if (state.promptOpen) {
                return;
            }
            if (isWeakHomePointerTarget(event.target)) {
                noteWeakHomeInteraction('pointer', event.target);
            }
        }, true);
        document.addEventListener('focusin', function (event) {
            if (state.promptOpen) {
                return;
            }
            if (isWeakHomeFocusTarget(event.target)) {
                noteWeakHomeInteraction('focus', event.target);
            }
        }, true);
        document.addEventListener('change', function (event) {
            if (state.promptOpen) {
                return;
            }
            if (isWeakHomeChangeTarget(event.target)) {
                noteWeakHomeInteraction('change', event.target);
            }
        }, true);

        window.addEventListener('neko:user-content-sent', function () {
            state.pendingChatTurns += 1;
            logFlow('strong-action', {
                type: 'chat_turn',
                pendingChatTurns: state.pendingChatTurns,
            });
            scheduleFastHeartbeat();
        });

        window.addEventListener('neko:voice-session-started', function () {
            state.pendingVoiceSessions += 1;
            logFlow('strong-action', {
                type: 'voice_session',
                pendingVoiceSessions: state.pendingVoiceSessions,
            });
            scheduleFastHeartbeat();
        });

        window.addEventListener('beforeunload', syncForegroundWindow);
    }

    mod.init = function init() {
        if (state.initialized) return;

        state.initialized = true;
        syncForegroundWindow();
        bindEvents();

        state.heartbeatTimer = setInterval(function () {
            void sendHeartbeat();
        }, HEARTBEAT_INTERVAL_MS);

        void Promise.allSettled([
            loadInitialServerState(),
            ensureAutostartStatusFresh({ source: 'init', silent: true }),
        ]).finally(function () {
            void sendHeartbeat();
        });
    };

    window.appTutorialPrompt = mod;
    window.appAutostartPrompt = mod;
})();
