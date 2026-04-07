(function () {
    'use strict';

    const HEARTBEAT_INTERVAL_MS = 15000;
    const FAST_HEARTBEAT_DELAY_MS = 1200;
    const AUTOSTART_STATUS_MAX_AGE_MS = HEARTBEAT_INTERVAL_MS;

    const promptShared = window.nekoPromptShared;
    if (!promptShared || typeof promptShared.createPromptTools !== 'function') {
        console.error('[AutostartPrompt] prompt helpers unavailable');
        window.appAutostartPrompt = {
            init: function () { },
        };
        return;
    }

    const promptTools = promptShared.createPromptTools({
        flowPrefix: '[AutostartPromptFlow]',
        loggerName: 'AutostartPrompt',
    });

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

    const shortPromptToken = promptTools.shortToken;
    const describeTarget = promptTools.describeTarget;
    const logFlow = promptTools.logFlow;
    const translate = promptTools.translate;
    const normalizeMs = promptTools.normalizeMs;
    const requestJson = promptTools.requestJson;
    const isWeakHomePointerTarget = promptTools.isWeakHomePointerTarget;
    const isWeakHomeFocusTarget = promptTools.isWeakHomeFocusTarget;
    const isWeakHomeChangeTarget = promptTools.isWeakHomeChangeTarget;
    const foregroundTracker = promptTools.attachForegroundTracker(state);
    const syncForegroundWindow = foregroundTracker.syncForegroundWindow;
    const consumeForegroundDelta = foregroundTracker.consumeForegroundDelta;

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

    const scheduleFastHeartbeat = promptTools.createFastHeartbeatScheduler(
        state,
        sendHeartbeat,
        FAST_HEARTBEAT_DELAY_MS
    );

    function isPromptSuppressedLocally() {
        return state.autostartEnabled
            || state.neverRemind
            || state.deferredUntil > Date.now();
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

    window.appAutostartPrompt = mod;
})();
