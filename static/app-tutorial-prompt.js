(function () {
    'use strict';

    const HEARTBEAT_INTERVAL_MS = 15000;
    const FAST_HEARTBEAT_DELAY_MS = 1200;
    const HOME_TUTORIAL_START_WAIT_TIMEOUT_MS = 15000;
    const HOME_TUTORIAL_STORAGE_KEY_FALLBACK = 'neko_tutorial_home';
    const HEARTBEAT_ENDPOINT = '/api/tutorial-prompt/heartbeat';
    const TUTORIAL_PROMPT_COORDINATION_KEY = 'home-tutorial-prompt';
    const TUTORIAL_PROMPT_PRIORITY = 200;

    const promptShared = window.nekoPromptShared;
    if (!promptShared || typeof promptShared.createPromptTools !== 'function') {
        console.error('[TutorialPrompt] prompt helpers unavailable');
        window.appTutorialPrompt = {
            init: function () { },
        };
        return;
    }

    const promptTools = promptShared.createPromptTools({
        flowPrefix: '[TutorialPromptFlow]',
        loggerName: 'TutorialPrompt',
    });

    const mod = {};
    const state = {
        initialized: false,
        heartbeatTimer: null,
        fastHeartbeatTimer: null,
        requestInFlight: false,
        inFlightHeartbeatSnapshot: null,
        pendingHeartbeatAfterFlight: false,
        promptOpen: false,
        tutorialRunning: false,
        pendingForegroundMs: 0,
        foregroundStartedAt: null,
        pendingWeakHomeInteractions: 0,
        pendingChatTurns: 0,
        pendingVoiceSessions: 0,
        meaningfulActionTaken: false,
        homeTutorialCompleted: false,
        manualHomeTutorialViewed: false,
        tutorialStarted: false,
        neverRemind: false,
        deferredUntil: 0,
        lastPromptTokenSeen: null,
        promptDrivenTutorialToken: null,
        tutorialRunToken: null,
        pendingTutorialStartPersistence: null,
        userCohort: 'unknown',
    };

    const shortPromptToken = promptTools.shortToken;
    const describeTarget = promptTools.describeTarget;
    const logFlow = promptTools.logFlow;
    const translate = promptTools.translate;
    const normalizeMs = promptTools.normalizeMs;
    const requestJson = promptTools.requestJson;
    const fireAndForgetJson = promptTools.fireAndForgetJson;
    const requestPromptDisplay = promptTools.requestPromptDisplay;
    const isWeakHomePointerTarget = promptTools.isWeakHomePointerTarget;
    const isWeakHomeFocusTarget = promptTools.isWeakHomeFocusTarget;
    const isWeakHomeChangeTarget = promptTools.isWeakHomeChangeTarget;
    const foregroundTracker = promptTools.attachForegroundTracker(state);
    const syncForegroundWindow = foregroundTracker.syncForegroundWindow;
    const consumeForegroundDelta = foregroundTracker.consumeForegroundDelta;

    function shortTutorialRunToken(tutorialRunToken) {
        return shortPromptToken(tutorialRunToken);
    }

    function createHeartbeatToken() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID();
        }
        return 'heartbeat-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
    }

    function getHomeTutorialStorageKey() {
        if (typeof window.getTutorialStorageKeyForPage === 'function') {
            return window.getTutorialStorageKeyForPage('home');
        }
        if (window.universalTutorialManager && window.universalTutorialManager.STORAGE_KEY_PREFIX) {
            return window.universalTutorialManager.STORAGE_KEY_PREFIX + 'home';
        }
        return HOME_TUTORIAL_STORAGE_KEY_FALLBACK;
    }

    function isHomeTutorialSeen() {
        return localStorage.getItem(getHomeTutorialStorageKey()) === 'true';
    }

    function markMeaningfulActionTaken() {
        if (!state.meaningfulActionTaken) {
            state.meaningfulActionTaken = true;
        }
    }

    function applyServerState(serverState, source) {
        if (!serverState || typeof serverState !== 'object') {
            return;
        }

        const previous = {
            tutorialStarted: state.tutorialStarted,
            homeTutorialCompleted: state.homeTutorialCompleted,
            manualHomeTutorialViewed: state.manualHomeTutorialViewed,
            neverRemind: state.neverRemind,
            deferredUntil: state.deferredUntil,
            userCohort: state.userCohort,
            meaningfulActionTaken: state.meaningfulActionTaken,
        };
        const status = serverState.status ? String(serverState.status).toLowerCase() : '';
        const startedAt = normalizeMs(serverState.started_at);
        const completedAt = normalizeMs(serverState.completed_at);
        const deferredUntil = normalizeMs(serverState.deferred_until);
        const userCohort = serverState.user_cohort ? String(serverState.user_cohort).toLowerCase() : '';
        const chatTurns = Number(serverState.chat_turns);
        const voiceSessions = Number(serverState.voice_sessions);

        if (userCohort) {
            state.userCohort = userCohort;
        }
        if (serverState.manual_home_tutorial_viewed === true) {
            state.manualHomeTutorialViewed = true;
        }
        if (serverState.never_remind === true) {
            state.neverRemind = true;
        }
        state.deferredUntil = deferredUntil;
        if (status === 'started' || status === 'completed' || startedAt > 0 || completedAt > 0) {
            state.tutorialStarted = true;
        }
        if (status === 'completed' || completedAt > 0) {
            state.homeTutorialCompleted = true;
            state.tutorialRunToken = null;
            state.tutorialRunning = false;
        }
        if ((Number.isFinite(chatTurns) && chatTurns > 0) || (Number.isFinite(voiceSessions) && voiceSessions > 0)) {
            state.meaningfulActionTaken = true;
        }

        const changed = previous.tutorialStarted !== state.tutorialStarted
            || previous.homeTutorialCompleted !== state.homeTutorialCompleted
            || previous.manualHomeTutorialViewed !== state.manualHomeTutorialViewed
            || previous.neverRemind !== state.neverRemind
            || previous.deferredUntil !== state.deferredUntil
            || previous.userCohort !== state.userCohort
            || previous.meaningfulActionTaken !== state.meaningfulActionTaken;

        if (changed || source === 'initial-state') {
            logFlow('state-sync', {
                source: source || 'unknown',
                status: status || null,
                tutorialStarted: state.tutorialStarted,
                homeTutorialCompleted: state.homeTutorialCompleted,
                manualHomeTutorialViewed: state.manualHomeTutorialViewed,
                neverRemind: state.neverRemind,
                deferredUntil: state.deferredUntil || 0,
                userCohort: state.userCohort,
                meaningfulActionTaken: state.meaningfulActionTaken,
            });
        }
    }

    async function loadInitialServerState() {
        try {
            const response = await requestJson('/api/tutorial-prompt/state', {
                cache: 'no-store',
            });
            if (response && response.state) {
                applyServerState(response.state, 'initial-state');
            }
        } catch (error) {
            console.warn('[TutorialPrompt] failed to load initial state:', error);
        }
    }

    async function persistTutorialLifecycle(url, payload, flowStep, options) {
        const requestOptions = options || {};
        try {
            const response = requestOptions.fireAndForget
                ? await fireAndForgetJson(url, payload)
                : await requestJson(url, {
                    method: 'POST',
                    json: payload,
                    keepalive: !!requestOptions.keepalive,
                });
            if (response && response.state) {
                applyServerState(response.state, flowStep);
            }
            if (response && response.tutorial_run_token) {
                state.tutorialRunToken = response.tutorial_run_token;
            }
            if (requestOptions.clearRunTokenOnSuccess && response && response.ok) {
                state.tutorialRunToken = null;
            }
            logFlow(flowStep, {
                page: payload && payload.page,
                source: payload && payload.source,
                promptToken: shortPromptToken(payload && payload.prompt_token),
                tutorialRunToken: shortTutorialRunToken(
                    (response && response.tutorial_run_token)
                    || (payload && payload.tutorial_run_token)
                    || state.tutorialRunToken
                ),
                beaconQueued: !!(response && response.beaconQueued),
            });
            return response;
        } catch (error) {
            console.warn('[TutorialPrompt] failed to persist lifecycle event:', error);
            return null;
        }
    }

    async function postDecision(payload) {
        try {
            const response = await requestJson('/api/tutorial-prompt/decision', {
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
            console.warn('[TutorialPrompt] failed to persist decision:', error);
        }
    }

    async function postShownAck(promptToken) {
        if (!promptToken) return;
        try {
            const response = await requestJson('/api/tutorial-prompt/shown', {
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
            console.warn('[TutorialPrompt] failed to ack prompt shown:', error);
        }
    }

    async function waitForTutorialRunToken(timeoutMs) {
        if (state.tutorialRunToken) {
            return state.tutorialRunToken;
        }

        const pendingStartPersistence = state.pendingTutorialStartPersistence;
        if (pendingStartPersistence) {
            await pendingStartPersistence;
        }
        if (state.tutorialRunToken) {
            return state.tutorialRunToken;
        }

        const waitMs = typeof timeoutMs === 'number' ? timeoutMs : 2000;
        const deadline = Date.now() + Math.max(0, waitMs);

        while (!state.tutorialRunToken && Date.now() < deadline) {
            await new Promise(function (resolve) {
                setTimeout(resolve, 50);
            });
        }

        return state.tutorialRunToken;
    }

    function takeHeartbeatSnapshot() {
        const snapshot = {
            foregroundMsDelta: consumeForegroundDelta(),
            homeInteractionsDelta: state.pendingWeakHomeInteractions,
            chatTurnsDelta: state.pendingChatTurns,
            voiceSessionsDelta: state.pendingVoiceSessions,
            homeTutorialCompleted: state.homeTutorialCompleted,
            manualHomeTutorialViewed: state.manualHomeTutorialViewed,
            unloadQueued: false,
        };

        if (hasReplaySensitiveHeartbeatMetrics(snapshot)) {
            snapshot.heartbeatToken = createHeartbeatToken();
        }

        return snapshot;
    }

    function clearHeartbeatSnapshot() {
        state.pendingWeakHomeInteractions = 0;
        state.pendingChatTurns = 0;
        state.pendingVoiceSessions = 0;
    }

    function restoreHeartbeatSnapshot(snapshot) {
        state.pendingForegroundMs += snapshot.foregroundMsDelta;
        state.pendingWeakHomeInteractions += snapshot.homeInteractionsDelta;
        state.pendingChatTurns += snapshot.chatTurnsDelta;
        state.pendingVoiceSessions += snapshot.voiceSessionsDelta;
    }

    function hasReplaySensitiveHeartbeatMetrics(snapshot) {
        if (!snapshot) {
            return false;
        }

        return snapshot.foregroundMsDelta > 0
            || snapshot.homeInteractionsDelta > 0
            || snapshot.chatTurnsDelta > 0
            || snapshot.voiceSessionsDelta > 0;
    }

    function shouldFlushHeartbeatSnapshot(snapshot) {
        if (!snapshot) {
            return false;
        }

        return hasReplaySensitiveHeartbeatMetrics(snapshot)
            || snapshot.homeTutorialCompleted
            || snapshot.manualHomeTutorialViewed;
    }

    function buildHeartbeatPayload(snapshot) {
        const payload = {
            heartbeat_token: snapshot.heartbeatToken,
            foreground_ms_delta: snapshot.foregroundMsDelta,
            home_interactions_delta: snapshot.homeInteractionsDelta,
            chat_turns_delta: snapshot.chatTurnsDelta,
            voice_sessions_delta: snapshot.voiceSessionsDelta,
            home_tutorial_completed: snapshot.homeTutorialCompleted,
            manual_home_tutorial_viewed: snapshot.manualHomeTutorialViewed,
        };

        if (!snapshot.heartbeatToken) {
            delete payload.heartbeat_token;
        }

        return payload;
    }

    function queueHeartbeatSnapshotForUnload(snapshot) {
        if (!shouldFlushHeartbeatSnapshot(snapshot)) {
            return;
        }

        snapshot.unloadQueued = true;
        void fireAndForgetJson(HEARTBEAT_ENDPOINT, buildHeartbeatPayload(snapshot)).catch(function (error) {
            snapshot.unloadQueued = false;
            if (state.inFlightHeartbeatSnapshot !== snapshot) {
                restoreHeartbeatSnapshot(snapshot);
            }
            console.warn('[TutorialPrompt] failed to flush heartbeat on unload:', error);
        });
    }

    function flushHeartbeatOnUnload() {
        if (!state.initialized) {
            return;
        }

        const snapshotsToFlush = [];
        if (shouldFlushHeartbeatSnapshot(state.inFlightHeartbeatSnapshot)) {
            snapshotsToFlush.push(state.inFlightHeartbeatSnapshot);
        }

        const snapshot = takeHeartbeatSnapshot();
        if (shouldFlushHeartbeatSnapshot(snapshot)) {
            snapshotsToFlush.push(snapshot);
        }

        if (!snapshotsToFlush.length) {
            return;
        }

        clearHeartbeatSnapshot();
        snapshotsToFlush.forEach(queueHeartbeatSnapshotForUnload);
    }

    async function sendHeartbeat() {
        if (!state.initialized) return;
        if (state.requestInFlight) {
            state.pendingHeartbeatAfterFlight = true;
            return;
        }

        state.requestInFlight = true;
        const snapshot = takeHeartbeatSnapshot();
        const payload = buildHeartbeatPayload(snapshot);
        state.inFlightHeartbeatSnapshot = snapshot;
        let data = null;

        try {
            clearHeartbeatSnapshot();

            data = await requestJson(HEARTBEAT_ENDPOINT, {
                method: 'POST',
                json: payload,
                keepalive: true,
            });
            if (data && data.state) {
                applyServerState(data.state, 'heartbeat');
            }
            logFlow('heartbeat', {
                foregroundMsDelta: snapshot.foregroundMsDelta,
                weakHomeInteractionsDelta: snapshot.homeInteractionsDelta,
                chatTurnsDelta: snapshot.chatTurnsDelta,
                voiceSessionsDelta: snapshot.voiceSessionsDelta,
                shouldPrompt: !!(data && data.should_prompt),
                reason: data && data.prompt_reason,
                token: shortPromptToken(data && data.prompt_token),
            });
        } catch (error) {
            if (!snapshot.unloadQueued) {
                restoreHeartbeatSnapshot(snapshot);
            }
            console.warn('[TutorialPrompt] heartbeat failed:', error);
        }

        try {
            if (data && data.should_prompt) {
                await maybeShowPrompt(data.prompt_token);
            }
        } catch (error) {
            console.warn('[TutorialPrompt] failed to render tutorial prompt:', error);
        } finally {
            if (state.inFlightHeartbeatSnapshot === snapshot) {
                state.inFlightHeartbeatSnapshot = null;
            }
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

    function hasPromptBlockingInteractionPending() {
        return state.pendingWeakHomeInteractions > 0
            || state.pendingChatTurns > 0
            || state.pendingVoiceSessions > 0
            || state.meaningfulActionTaken
            || state.tutorialStarted
            || state.homeTutorialCompleted
            || state.manualHomeTutorialViewed
            || state.neverRemind
            || state.deferredUntil > Date.now()
            || state.userCohort === 'existing';
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

    function createHomeTutorialStartWaiter(timeoutMs) {
        const waitMs = timeoutMs || 5000;
        let settled = false;
        let resolvePromise;
        let rejectPromise;

        const handler = function (event) {
            if (!event || !event.detail || event.detail.page !== 'home') {
                return;
            }
            if (event.detail.source !== 'idle_prompt') {
                return;
            }
            cleanup();
            resolvePromise(true);
        };

        const timer = setTimeout(function () {
            cleanup();
            rejectPromise(new Error('tutorial_start_timeout'));
        }, waitMs);

        function cleanup() {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            window.removeEventListener('neko:tutorial-started', handler);
        }

        const promise = new Promise(function (resolve, reject) {
            resolvePromise = resolve;
            rejectPromise = reject;
        });

        window.addEventListener('neko:tutorial-started', handler);

        return {
            promise: promise,
            cancel: cleanup,
        };
    }

    async function startHomeTutorialFromPrompt() {
        const manager = window.universalTutorialManager;
        if (!manager || typeof manager.requestTutorialStart !== 'function') {
            throw new Error('tutorial_manager_unavailable');
        }
        await manager.requestTutorialStart('idle_prompt', 0);
    }

    async function handlePromptAcceptance(promptToken) {
        const startWaiter = createHomeTutorialStartWaiter(HOME_TUTORIAL_START_WAIT_TIMEOUT_MS);
        state.promptDrivenTutorialToken = promptToken;
        try {
            await startHomeTutorialFromPrompt();
            await startWaiter.promise;
            await postDecision({
                decision: 'accept',
                result: 'accepted',
                prompt_token: promptToken,
            });
        } catch (error) {
            startWaiter.cancel();
            state.tutorialRunToken = null;
            const message = error && error.message ? error.message : String(error);
            console.warn('[TutorialPrompt] failed to start tutorial:', error);
            await postDecision({
                decision: 'accept',
                result: 'failed',
                error: message,
                prompt_token: promptToken,
            });

            if (typeof window.showStatusToast === 'function') {
                window.showStatusToast(
                    translate('tutorialPrompt.startFailed', '新手引导暂时无法启动，请稍后再试'),
                    3500
                );
            }
            state.promptDrivenTutorialToken = null;
        }
    }

    function canShowPrompt(promptToken) {
        if (state.promptOpen || state.tutorialRunning) {
            return false;
        }
        if (!promptToken) {
            return false;
        }
        if (promptToken === state.lastPromptTokenSeen) {
            return false;
        }
        if (isHomeTutorialSeen() || hasPromptBlockingInteractionPending()) {
            return false;
        }
        return typeof window.showDecisionPrompt === 'function';
    }

    async function showPrompt(promptToken) {
        state.promptOpen = true;
        state.lastPromptTokenSeen = promptToken;
        logFlow('prompt-open', { token: shortPromptToken(promptToken) });
        try {
            const decision = await window.showDecisionPrompt({
                title: translate('tutorialPrompt.title', '要不要先看一下新手引导？'),
                message: translate(
                    'tutorialPrompt.message',
                    '看起来你刚刚打开 N.E.K.O，还没有开始操作。要不要先带你快速认识一下主页里的核心功能？'
                ),
                note: translate(
                    'tutorialPrompt.note',
                    '引导会从主页开始，介绍常用按钮和交互入口。'
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
                state.promptDrivenTutorialToken = null;
                await postDecision({ decision: 'never', prompt_token: promptToken });
                return;
            }
            if (decision === 'later') {
                state.promptDrivenTutorialToken = null;
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

    async function maybeShowPrompt(promptToken) {
        if (!promptToken) {
            return;
        }

        await requestPromptDisplay({
            key: TUTORIAL_PROMPT_COORDINATION_KEY,
            priority: TUTORIAL_PROMPT_PRIORITY,
            shouldDisplay: function () {
                return canShowPrompt(promptToken);
            },
            display: function () {
                return showPrompt(promptToken);
            },
        });
    }

    function bindEvents() {
        document.addEventListener('visibilitychange', syncForegroundWindow);
        window.addEventListener('focus', syncForegroundWindow);
        window.addEventListener('blur', syncForegroundWindow);
        document.addEventListener('pointerdown', function (event) {
            if (state.promptOpen || state.tutorialRunning) {
                return;
            }
            if (isWeakHomePointerTarget(event.target)) {
                noteWeakHomeInteraction('pointer', event.target);
            }
        }, true);
        document.addEventListener('focusin', function (event) {
            if (state.promptOpen || state.tutorialRunning) {
                return;
            }
            if (isWeakHomeFocusTarget(event.target)) {
                noteWeakHomeInteraction('focus', event.target);
            }
        }, true);
        document.addEventListener('change', function (event) {
            if (state.promptOpen || state.tutorialRunning) {
                return;
            }
            if (isWeakHomeChangeTarget(event.target)) {
                noteWeakHomeInteraction('change', event.target);
            }
        }, true);

        window.addEventListener('neko:user-content-sent', function () {
            state.pendingChatTurns += 1;
            markMeaningfulActionTaken();
            logFlow('strong-action', {
                type: 'chat_turn',
                pendingChatTurns: state.pendingChatTurns,
            });
            scheduleFastHeartbeat();
        });

        window.addEventListener('neko:voice-session-started', function () {
            state.pendingVoiceSessions += 1;
            markMeaningfulActionTaken();
            logFlow('strong-action', {
                type: 'voice_session',
                pendingVoiceSessions: state.pendingVoiceSessions,
            });
            scheduleFastHeartbeat();
        });

        window.addEventListener('neko:tutorial-completed', function (event) {
            if (!event || !event.detail || event.detail.page !== 'home') {
                return;
            }
            state.tutorialRunning = false;
            state.tutorialStarted = true;
            state.homeTutorialCompleted = true;
            void (async function () {
                const source = event.detail.source || 'manual';
                const tutorialRunToken = await waitForTutorialRunToken(2000);
                logFlow('tutorial-completed', {
                    source: source,
                    promptToken: shortPromptToken(state.promptDrivenTutorialToken),
                    tutorialRunToken: shortTutorialRunToken(tutorialRunToken),
                });

                if (!tutorialRunToken) {
                    logFlow('tutorial-completed-skipped', {
                        source: source,
                        reason: 'missing_run_token',
                    });
                    state.promptDrivenTutorialToken = null;
                    return;
                }

                await persistTutorialLifecycle('/api/tutorial-prompt/tutorial-completed', {
                    page: 'home',
                    source: source,
                    tutorial_run_token: tutorialRunToken,
                }, 'tutorial-completed-persisted', {
                    clearRunTokenOnSuccess: true,
                });
                state.promptDrivenTutorialToken = null;
            })();
            scheduleFastHeartbeat();
        });

        window.addEventListener('neko:tutorial-started', function (event) {
            if (!event || !event.detail || event.detail.page !== 'home') {
                return;
            }
            state.tutorialRunning = true;
            state.tutorialStarted = true;
            if (event.detail.source !== 'idle_prompt') {
                state.promptDrivenTutorialToken = null;
            }
            if (event.detail.source === 'manual') {
                state.manualHomeTutorialViewed = true;
            }
            logFlow('tutorial-started', {
                source: event.detail.source || 'unknown',
                promptToken: shortPromptToken(state.promptDrivenTutorialToken || state.lastPromptTokenSeen),
                tutorialRunToken: shortTutorialRunToken(state.tutorialRunToken),
            });
            const startPersistence = persistTutorialLifecycle('/api/tutorial-prompt/tutorial-started', {
                page: 'home',
                source: event.detail.source || 'manual',
                prompt_token: event.detail.source === 'idle_prompt'
                    ? state.promptDrivenTutorialToken
                    : undefined,
            }, 'tutorial-started-persisted');
            state.pendingTutorialStartPersistence = startPersistence;
            void startPersistence.finally(function () {
                if (state.pendingTutorialStartPersistence === startPersistence) {
                    state.pendingTutorialStartPersistence = null;
                }
            });
            scheduleFastHeartbeat();
        });

        window.addEventListener('beforeunload', flushHeartbeatOnUnload);
    }

    mod.init = function init() {
        if (state.initialized) return;

        state.homeTutorialCompleted = isHomeTutorialSeen();
        state.initialized = true;
        syncForegroundWindow();
        bindEvents();

        state.heartbeatTimer = setInterval(function () {
            void sendHeartbeat();
        }, HEARTBEAT_INTERVAL_MS);

        void loadInitialServerState().finally(function () {
            void sendHeartbeat();
        });
    };

    window.appTutorialPrompt = mod;
})();
