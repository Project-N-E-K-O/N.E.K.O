(function () {
    'use strict';

    const HEARTBEAT_INTERVAL_MS = 15000;
    const FAST_HEARTBEAT_DELAY_MS = 1200;
    const HOME_TUTORIAL_START_WAIT_TIMEOUT_MS = 15000;
    const HOME_TUTORIAL_STORAGE_KEY = 'neko_tutorial_home_yui_v1';
    const HOME_TUTORIAL_RESET_EVENT = 'neko:home-tutorial-reset';
    const HOME_TUTORIAL_RESET_STORAGE_EVENT_KEY = 'neko_home_tutorial_reset_event';
    const HOME_TUTORIAL_RESET_CHANNEL = 'neko_tutorial_events';
    const HOME_TUTORIAL_PROMPT_SEEN_FLAG = '__nekoHomeTutorialPromptSeenInTab';
    const HEARTBEAT_ENDPOINT = '/api/tutorial-prompt/heartbeat';
    const TUTORIAL_PROMPT_COORDINATION_KEY = 'home-tutorial-prompt';
    const TUTORIAL_PROMPT_PRIORITY = 200;
    const LOCAL_PROMPT_LATER_SUPPRESS_MS = 60 * 60 * 1000;
    const HOME_TUTORIAL_AGENT_RESTORE_STORAGE_KEY = 'neko.homeTutorial.agentRestoreSnapshot.v1';
    const HOME_TUTORIAL_AGENT_FLAG_KEYS = Object.freeze([
        'agent_enabled',
        'computer_use_enabled',
        'browser_use_enabled',
        'user_plugin_enabled',
        'openclaw_enabled',
        'openfang_enabled',
    ]);
    const HOME_TUTORIAL_PROACTIVE_KEYS = Object.freeze([
        'proactiveChatEnabled',
        'proactiveVisionEnabled',
        'proactiveVisionChatEnabled',
        'proactiveNewsChatEnabled',
        'proactiveVideoChatEnabled',
        'proactivePersonalChatEnabled',
        'proactiveMusicEnabled',
        'proactiveMemeEnabled',
        'proactiveMiniGameInviteEnabled',
    ]);

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
        promptDisplayPending: false,
        tutorialRunning: false,
        tutorialStartRequested: false,
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
        localPromptSuppressedUntil: 0,
        lastPromptTokenSeen: null,
        promptDrivenTutorialToken: null,
        tutorialRunToken: null,
        pendingTutorialStartPersistence: null,
        pendingTutorialStartPayload: null,
        resetGeneration: 0,
        resetBroadcastChannel: null,
        pendingResetAfterHeartbeatReason: '',
        pendingResetAfterLifecycleReason: '',
        inFlightResetSensitiveLifecycleCount: 0,
        userCohort: 'unknown',
        mobileResizeRetryBound: false,
        featureSuppression: {
            active: false,
            token: 0,
            snapshot: null,
            agentSuppressionPromise: null,
        },
    };

    const TutorialLifecycleStores = window.TutorialLifecycleStores || {};
    const HomeTutorialPromptLifecycleStateStore = TutorialLifecycleStores.HomeTutorialPromptLifecycleStateStore;
    if (typeof HomeTutorialPromptLifecycleStateStore !== 'function') {
        console.error('[TutorialPrompt] lifecycle state store unavailable');
        window.appTutorialPrompt = {
            init: function () { },
        };
        return;
    }

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

    function isMobileTutorialDisabled() {
        return window.innerWidth <= 768;
    }

    function bindMobileTutorialPromptResizeRetry() {
        if (state.mobileResizeRetryBound) return;
        state.mobileResizeRetryBound = true;

        window.addEventListener('resize', function retryTutorialPromptInit() {
            if (isMobileTutorialDisabled()) return;
            window.removeEventListener('resize', retryTutorialPromptInit);
            state.mobileResizeRetryBound = false;
            mod.init();
        });
    }

    function createHeartbeatToken() {
        if (window.crypto && typeof window.crypto.randomUUID === 'function') {
            return window.crypto.randomUUID();
        }
        return 'heartbeat-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
    }

    const promptLifecycleStateStore = new HomeTutorialPromptLifecycleStateStore(state, createHeartbeatToken);

    function getReactChatWindowHost() {
        return window.reactChatWindowHost || null;
    }

    function getStoredGalgamePreference() {
        try {
            const raw = localStorage.getItem('neko.reactChatWindow.galgameMode');
            if (raw === null) return true;
            return raw === 'true';
        } catch (_) {
            return true;
        }
    }

    function snapshotGalgameState() {
        const host = getReactChatWindowHost();
        if (host && typeof host.isGalgameModeEnabled === 'function') {
            try {
                return !!host.isGalgameModeEnabled();
            } catch (_) {}
        }
        return getStoredGalgamePreference();
    }

    function setGalgameState(enabled, options) {
        const requestOptions = options || {};
        const host = getReactChatWindowHost();
        if (host && typeof host.setGalgameModeEnabled === 'function') {
            try {
                host.setGalgameModeEnabled(!!enabled, {
                    persist: false,
                    suppressRefetch: true,
                    force: !!requestOptions.force,
                });
            } catch (error) {
                console.warn('[TutorialPrompt] failed to set GalGame state:', error);
            }
        }
    }

    function snapshotProactiveState() {
        const snapshot = {};
        const appState = window.appState || null;
        HOME_TUTORIAL_PROACTIVE_KEYS.forEach(function (key) {
            if (typeof window[key] !== 'undefined') {
                snapshot[key] = !!window[key];
            } else if (appState && typeof appState[key] !== 'undefined') {
                snapshot[key] = !!appState[key];
            } else {
                snapshot[key] = false;
            }
        });
        return snapshot;
    }

    function applyProactiveState(values) {
        const appState = window.appState || null;
        HOME_TUTORIAL_PROACTIVE_KEYS.forEach(function (key) {
            if (Object.prototype.hasOwnProperty.call(values, key)) {
                const next = !!values[key];
                window[key] = next;
                if (appState && typeof appState[key] !== 'undefined') {
                    appState[key] = next;
                }
            }
        });
        if (typeof window.stopProactiveChatSchedule === 'function') {
            try {
                window.stopProactiveChatSchedule();
            } catch (error) {
                console.warn('[TutorialPrompt] failed to stop proactive schedule:', error);
            }
        }
        if (typeof window.stopProactiveVisionDuringSpeech === 'function') {
            try {
                window.stopProactiveVisionDuringSpeech();
            } catch (error) {
                console.warn('[TutorialPrompt] failed to stop proactive vision:', error);
            }
        }
        if (typeof window.releaseProactiveVisionStream === 'function') {
            try {
                window.releaseProactiveVisionStream();
            } catch (error) {
                console.warn('[TutorialPrompt] failed to release proactive vision stream:', error);
            }
        }
    }

    function maybeRestartProactiveSchedule(snapshot) {
        if (!snapshot || !snapshot.proactiveChatEnabled) {
            return;
        }
        const hasMode = HOME_TUTORIAL_PROACTIVE_KEYS.some(function (key) {
            return key !== 'proactiveChatEnabled' && !!snapshot[key];
        });
        if (!hasMode) {
            return;
        }
        const scheduler = window.appProactive && typeof window.appProactive.scheduleProactiveChat === 'function'
            ? window.appProactive.scheduleProactiveChat
            : window.scheduleProactiveChat;
        if (typeof scheduler === 'function') {
            try {
                scheduler();
            } catch (error) {
                console.warn('[TutorialPrompt] failed to restart proactive schedule:', error);
            }
        }
    }

    async function fetchAgentFlagSnapshot() {
        const response = await fetch('/api/agent/flags', {
            method: 'GET',
            cache: 'no-store',
        });
        if (!response || !response.ok) {
            throw new Error('agent_flags_get_failed');
        }
        const payload = await response.json();
        if (!payload || payload.success === false) {
            throw new Error('agent_flags_payload_invalid');
        }
        const flags = Object.assign({}, payload.agent_flags || {});
        if (typeof payload.analyzer_enabled === 'boolean') {
            flags.agent_enabled = payload.analyzer_enabled;
        } else if (Object.prototype.hasOwnProperty.call(flags, 'agent_enabled')) {
            flags.agent_enabled = !!flags.agent_enabled;
        }
        return flags;
    }

    async function postAgentFlags(flags) {
        const response = await fetch('/api/agent/flags', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ flags: flags }),
        });
        if (!response || !response.ok) {
            throw new Error('agent_flags_post_failed');
        }
        const payload = await response.json().catch(function () { return null; });
        if (payload && payload.success === false) {
            throw new Error(payload.error || 'agent_flags_post_rejected');
        }
    }

    async function postAgentCommand(command, payload) {
        const response = await fetch('/api/agent/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(Object.assign({
                request_id: 'home-tutorial-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8),
                command: command,
            }, payload || {})),
        });
        if (!response || !response.ok) {
            throw new Error('agent_command_post_failed');
        }
        const result = await response.json().catch(function () { return null; });
        if (result && result.success === false) {
            throw new Error(result.error || 'agent_command_rejected');
        }
    }

    function readPersistedAgentRestoreSnapshot() {
        try {
            const raw = localStorage.getItem(HOME_TUTORIAL_AGENT_RESTORE_STORAGE_KEY);
            if (!raw) {
                return null;
            }
            const payload = JSON.parse(raw);
            if (!payload || !payload.agentFlags || typeof payload.agentFlags !== 'object') {
                return null;
            }
            return payload;
        } catch (error) {
            console.warn('[TutorialPrompt] failed to read persisted agent restore snapshot:', error);
            return null;
        }
    }

    function persistAgentRestoreSnapshot(flags, token, reason) {
        if (!flags) {
            return;
        }
        try {
            localStorage.setItem(HOME_TUTORIAL_AGENT_RESTORE_STORAGE_KEY, JSON.stringify({
                version: 1,
                token: token,
                createdAt: Date.now(),
                reason: reason || 'home-tutorial-suppression',
                agentFlags: flags,
            }));
        } catch (error) {
            console.warn('[TutorialPrompt] failed to persist agent restore snapshot:', error);
        }
    }

    function clearPersistedAgentRestoreSnapshot(token) {
        try {
            if (token !== undefined) {
                const payload = readPersistedAgentRestoreSnapshot();
                if (payload && payload.token !== token) {
                    return;
                }
            }
            localStorage.removeItem(HOME_TUTORIAL_AGENT_RESTORE_STORAGE_KEY);
        } catch (error) {
            console.warn('[TutorialPrompt] failed to clear persisted agent restore snapshot:', error);
        }
    }

    function buildAgentChildFlags(values) {
        const flags = {};
        HOME_TUTORIAL_AGENT_FLAG_KEYS.forEach(function (key) {
            if (key !== 'agent_enabled' && Object.prototype.hasOwnProperty.call(values, key)) {
                flags[key] = !!values[key];
            }
        });
        return flags;
    }

    function buildDisabledAgentChildFlags() {
        const flags = {};
        HOME_TUTORIAL_AGENT_FLAG_KEYS.forEach(function (key) {
            if (key !== 'agent_enabled') {
                flags[key] = false;
            }
        });
        return flags;
    }

    function canRestoreAgentSnapshot(restoreToken) {
        return !state.featureSuppression.active
            && state.featureSuppression.token === restoreToken;
    }

    function isFeatureSuppressionTokenActive(token) {
        return state.featureSuppression.active
            && state.featureSuppression.token === token;
    }

    async function restoreAgentSnapshot(flags, restoreToken) {
        if (!flags) {
            return false;
        }
        if (restoreToken !== undefined && !canRestoreAgentSnapshot(restoreToken)) {
            return false;
        }
        if (Object.prototype.hasOwnProperty.call(flags, 'agent_enabled')) {
            await postAgentCommand('set_agent_enabled', {
                enabled: !!flags.agent_enabled,
            });
        }
        if (restoreToken !== undefined && !canRestoreAgentSnapshot(restoreToken)) {
            if (state.featureSuppression.active && flags.agent_enabled) {
                try {
                    await postAgentCommand('set_agent_enabled', { enabled: false });
                } catch (error) {
                    console.warn('[TutorialPrompt] failed to re-suppress stale agent master restore:', error);
                }
            }
            return false;
        }
        const childFlags = buildAgentChildFlags(flags);
        if (Object.keys(childFlags).length > 0) {
            await postAgentFlags(childFlags);
        }
        if (restoreToken !== undefined && !canRestoreAgentSnapshot(restoreToken)) {
            if (state.featureSuppression.active && Object.keys(childFlags).length > 0) {
                try {
                    await postAgentFlags(buildDisabledAgentChildFlags());
                } catch (error) {
                    console.warn('[TutorialPrompt] failed to re-suppress stale agent child flag restore:', error);
                }
            }
            return false;
        }
        return true;
    }

    function syncAgentFlagsUi() {
        if (typeof window.syncAgentFlagsFromBackend === 'function') {
            try {
                Promise.resolve(window.syncAgentFlagsFromBackend()).catch(function (error) {
                    console.warn('[TutorialPrompt] agent flag UI sync failed:', error);
                });
            } catch (error) {
                console.warn('[TutorialPrompt] agent flag UI sync failed:', error);
            }
        }
        if (typeof window.checkAndToggleTaskHUD === 'function') {
            try {
                window.checkAndToggleTaskHUD();
            } catch (error) {
                console.warn('[TutorialPrompt] agent HUD sync failed:', error);
            }
        }
    }

    async function snapshotAndDisableAgentFlags(token) {
        try {
            const flags = await fetchAgentFlagSnapshot();
            if (!state.featureSuppression.active || state.featureSuppression.token !== token) {
                return;
            }
            if (state.featureSuppression.snapshot) {
                state.featureSuppression.snapshot.agentFlags = flags;
                state.featureSuppression.snapshot.agentRestoreToken = token;
            }
            persistAgentRestoreSnapshot(flags, token, 'tutorial-suppression');
            await postAgentCommand('set_agent_enabled', { enabled: false });
            if (!state.featureSuppression.active || state.featureSuppression.token !== token) {
                const restoreToken = state.featureSuppression.token;
                if (state.featureSuppression.active || !canRestoreAgentSnapshot(restoreToken)) {
                    return;
                }
                try {
                    const restored = await restoreAgentSnapshot(flags, restoreToken);
                    if (restored && canRestoreAgentSnapshot(restoreToken)) {
                        clearPersistedAgentRestoreSnapshot(token);
                        syncAgentFlagsUi();
                    }
                } catch (restoreError) {
                    console.warn('[TutorialPrompt] failed to restore agent flags after stale master suppress:', restoreError);
                }
                return;
            }
            await postAgentFlags(buildDisabledAgentChildFlags());
            if (!state.featureSuppression.active || state.featureSuppression.token !== token) {
                const restoreToken = state.featureSuppression.token;
                if (state.featureSuppression.active || !canRestoreAgentSnapshot(restoreToken)) {
                    return;
                }
                try {
                    const restored = await restoreAgentSnapshot(flags, restoreToken);
                    if (restored && canRestoreAgentSnapshot(restoreToken)) {
                        clearPersistedAgentRestoreSnapshot(token);
                        syncAgentFlagsUi();
                    }
                } catch (restoreError) {
                    console.warn('[TutorialPrompt] failed to restore agent flags after stale suppress:', restoreError);
                }
                return;
            }
            syncAgentFlagsUi();
        } catch (error) {
            console.warn('[TutorialPrompt] failed to suppress agent flags:', error);
        }
    }

    async function reapplySuppressedAgentFlags(token, reason) {
        try {
            if (!isFeatureSuppressionTokenActive(token)) {
                return false;
            }
            await postAgentCommand('set_agent_enabled', { enabled: false });
            if (!isFeatureSuppressionTokenActive(token)) {
                return false;
            }
            await postAgentFlags(buildDisabledAgentChildFlags());
            if (!isFeatureSuppressionTokenActive(token)) {
                return false;
            }
            syncAgentFlagsUi();
            return true;
        } catch (error) {
            console.warn('[TutorialPrompt] failed to enforce agent suppression:', reason || '', error);
            return false;
        }
    }

    function setAgentSuppressionPromise(promise) {
        const suppression = state.featureSuppression;
        suppression.agentSuppressionPromise = promise;
        void promise.finally(function () {
            if (suppression.agentSuppressionPromise === promise) {
                suppression.agentSuppressionPromise = null;
            }
        });
    }

    function queueSuppressedAgentFlagsReapply(token, reason) {
        const suppression = state.featureSuppression;
        const previous = suppression.agentSuppressionPromise || Promise.resolve();
        const next = previous.catch(function () {}).then(function () {
            return reapplySuppressedAgentFlags(token, reason);
        });
        setAgentSuppressionPromise(next);
    }

    function beginHomeTutorialFeatureSuppression(reason) {
        if (!isHomePage()) {
            return;
        }
        const suppression = state.featureSuppression;
        if (suppression.active) {
            return;
        }
        const token = Date.now() + Math.random();
        const snapshot = {
            galgameEnabled: snapshotGalgameState(),
            proactive: snapshotProactiveState(),
            agentFlags: null,
            reason: reason || 'tutorial-started',
        };
        suppression.active = true;
        suppression.token = token;
        suppression.snapshot = snapshot;

        setGalgameState(false);
        const proactiveOff = {};
        HOME_TUTORIAL_PROACTIVE_KEYS.forEach(function (key) {
            proactiveOff[key] = false;
        });
        applyProactiveState(proactiveOff);
        setAgentSuppressionPromise(snapshotAndDisableAgentFlags(token));

        window.dispatchEvent(new CustomEvent('neko:home-tutorial-features-suppressed', {
            detail: { active: true, reason: reason || 'tutorial-started' },
        }));
    }

    function enforceHomeTutorialFeatureSuppression(reason) {
        if (!isHomePage()) {
            return;
        }
        const suppression = state.featureSuppression;
        if (!suppression.active) {
            beginHomeTutorialFeatureSuppression(reason || 'tutorial-enforced');
            return;
        }

        setGalgameState(false);
        const proactiveOff = {};
        HOME_TUTORIAL_PROACTIVE_KEYS.forEach(function (key) {
            proactiveOff[key] = false;
        });
        applyProactiveState(proactiveOff);
        queueSuppressedAgentFlagsReapply(
            suppression.token,
            reason || (suppression.snapshot && suppression.snapshot.reason) || 'tutorial-enforced'
        );

        window.dispatchEvent(new CustomEvent('neko:home-tutorial-features-suppressed', {
            detail: {
                active: true,
                enforced: true,
                reason: reason || (suppression.snapshot && suppression.snapshot.reason) || 'tutorial-enforced',
            },
        }));
    }

    function endHomeTutorialFeatureSuppression(reason) {
        const suppression = state.featureSuppression;
        if (!suppression.active && !suppression.snapshot) {
            return;
        }
        const snapshot = suppression.snapshot || {};
        suppression.active = false;
        suppression.token = Date.now() + Math.random();
        const restoreToken = suppression.token;
        suppression.snapshot = null;

        setGalgameState(!!snapshot.galgameEnabled, { force: true });
        setTimeout(function () {
            if (state.featureSuppression.active || state.featureSuppression.token !== restoreToken) {
                return;
            }
            setGalgameState(!!snapshot.galgameEnabled, { force: true });
        }, 0);
        if (snapshot.proactive) {
            applyProactiveState(snapshot.proactive);
            maybeRestartProactiveSchedule(snapshot.proactive);
        }
        if (snapshot.agentFlags) {
            void restoreAgentSnapshot(snapshot.agentFlags, restoreToken)
                .then(function (restored) {
                    if (restored && canRestoreAgentSnapshot(restoreToken)) {
                        clearPersistedAgentRestoreSnapshot(snapshot.agentRestoreToken);
                        syncAgentFlagsUi();
                    }
                })
                .catch(function (error) {
                    console.warn('[TutorialPrompt] failed to restore agent flags:', error);
                });
        }

        window.dispatchEvent(new CustomEvent('neko:home-tutorial-features-suppressed', {
            detail: { active: false, reason: reason || 'tutorial-ended' },
        }));
    }

    function restoreInterruptedAgentSuppression(reason) {
        if (state.featureSuppression.active) {
            return;
        }
        const persisted = readPersistedAgentRestoreSnapshot();
        if (!persisted) {
            clearPersistedAgentRestoreSnapshot();
            return;
        }
        const restoreToken = Date.now() + Math.random();
        state.featureSuppression.token = restoreToken;
        void restoreAgentSnapshot(persisted.agentFlags, restoreToken)
            .then(function (restored) {
                if (restored && canRestoreAgentSnapshot(restoreToken)) {
                    clearPersistedAgentRestoreSnapshot(persisted.token);
                    syncAgentFlagsUi();
                }
            })
            .catch(function (error) {
                console.warn('[TutorialPrompt] failed to restore interrupted agent suppression:', error);
            });
    }

    mod.beginHomeTutorialFeatureSuppression = beginHomeTutorialFeatureSuppression;
    mod.enforceHomeTutorialFeatureSuppression = enforceHomeTutorialFeatureSuppression;
    mod.endHomeTutorialFeatureSuppression = endHomeTutorialFeatureSuppression;
    mod.isHomeTutorialFeatureSuppressionActive = function () {
        return !!state.featureSuppression.active;
    };
    window.NekoHomeTutorialFeatureController = {
        begin: beginHomeTutorialFeatureSuppression,
        enforce: enforceHomeTutorialFeatureSuppression,
        end: endHomeTutorialFeatureSuppression,
        isActive: mod.isHomeTutorialFeatureSuppressionActive,
    };

    function getHomeTutorialStorageKeys() {
        const keys = [];
        const manager = window.universalTutorialManager || null;
        const addKey = function (value) {
            const key = typeof value === 'string' ? value.trim() : '';
            if (key && !keys.includes(key)) {
                keys.push(key);
            }
        };

        if (manager && typeof manager.getStorageKeysForPage === 'function') {
            try {
                const managerKeys = manager.getStorageKeysForPage('home');
                if (Array.isArray(managerKeys)) {
                    managerKeys.forEach(addKey);
                }
            } catch (error) {
                console.warn('[TutorialPrompt] failed to read home tutorial storage keys:', error);
            }
        }
        if (manager && typeof manager.getStorageKey === 'function' && manager.currentPage === 'home') {
            try {
                addKey(manager.getStorageKey());
            } catch (error) {
                console.warn('[TutorialPrompt] failed to read preferred home tutorial storage key:', error);
            }
        }
        if (manager && typeof manager.getPreferredStoragePageKey === 'function'
            && typeof window.getTutorialStorageKeyForPage === 'function') {
            try {
                addKey(window.getTutorialStorageKeyForPage(manager.getPreferredStoragePageKey('home')));
            } catch (error) {
                console.warn('[TutorialPrompt] failed to read versioned home tutorial storage key:', error);
            }
        }
        if (typeof window.getTutorialStorageKeyForPage === 'function') {
            addKey(window.getTutorialStorageKeyForPage('home_yui_v1'));
        }
        addKey(HOME_TUTORIAL_STORAGE_KEY);

        return keys;
    }

    function getHomeTutorialStorageKey() {
        const keys = getHomeTutorialStorageKeys();
        return keys.length ? keys[0] : HOME_TUTORIAL_STORAGE_KEY;
    }

    function markHomeTutorialStorageSeen() {
        getHomeTutorialStorageKeys().forEach(function (storageKey) {
            localStorage.setItem(storageKey, 'true');
        });
    }

    function clearHomeTutorialStorageSeen() {
        getHomeTutorialStorageKeys().forEach(function (storageKey) {
            localStorage.removeItem(storageKey);
        });
    }

    function isHomeTutorialSeen() {
        return getHomeTutorialStorageKeys().some(function (storageKey) {
            return localStorage.getItem(storageKey) === 'true';
        });
    }

    function isHomePage() {
        if (window.location && typeof window.location.pathname === 'string') {
            const path = window.location.pathname || '/';
            return path === '/' || path === '/index.html';
        }
        const manager = window.universalTutorialManager || null;
        return !!(manager && manager.currentPage === 'home');
    }

    function computeHomeTutorialInteractionLocked() {
        if (!isHomePage()) {
            return false;
        }
        const manager = window.universalTutorialManager || null;
        return !!(
            state.promptDisplayPending
            || state.promptOpen
            || state.tutorialStartRequested
            || state.tutorialRunning
            || window.isInTutorial === true
            || (manager && manager.currentPage === 'home' && manager.isTutorialRunning)
        );
    }

    function emitHomeTutorialLockIfChanged(reason) {
        const locked = computeHomeTutorialInteractionLocked();
        if (state.lastInteractionLocked === locked) {
            return locked;
        }
        state.lastInteractionLocked = locked;
        window.dispatchEvent(new CustomEvent('neko:home-tutorial-lock-changed', {
            detail: {
                locked: locked,
                reason: reason || 'state-change',
            },
        }));
        return locked;
    }

    function isPromptSuppressedInThisTab() {
        const now = Date.now();
        return state.deferredUntil > now || state.localPromptSuppressedUntil > now;
    }

    function hasPromptBeenSeenInThisTab() {
        return window[HOME_TUTORIAL_PROMPT_SEEN_FLAG] === true || !!state.lastPromptTokenSeen;
    }

    mod.isHomeTutorialInteractionLocked = computeHomeTutorialInteractionLocked;
    mod.isHomeTutorialBlockingGreeting = computeHomeTutorialInteractionLocked;
    mod.shouldSuppressAutomaticHomeTutorialStart = function () {
        return !!(
            state.promptDisplayPending
            || state.promptOpen
            || state.tutorialStartRequested
            || state.tutorialRunning
            || hasPromptBeenSeenInThisTab()
            || state.homeTutorialCompleted
            || state.neverRemind
            || isPromptSuppressedInThisTab()
        );
    };
    window.isNekoHomeTutorialInteractionLocked = computeHomeTutorialInteractionLocked;
    window.isNekoHomeTutorialBlockingGreeting = computeHomeTutorialInteractionLocked;

    function markMeaningfulActionTaken() {
        if (!state.meaningfulActionTaken) {
            state.meaningfulActionTaken = true;
        }
    }

    function isStaleAfterClientReset(requestResetGeneration) {
        return typeof requestResetGeneration === 'number'
            && requestResetGeneration !== state.resetGeneration;
    }

    function applyServerState(serverState, source, requestResetGeneration) {
        if (!serverState || typeof serverState !== 'object') {
            return false;
        }
        if (isStaleAfterClientReset(requestResetGeneration)) {
            logFlow('state-sync-stale-ignored', {
                source: source || 'unknown',
            });
            return false;
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
            promptLifecycleStateStore.markManualHomeTutorialViewed();
        }
        if (serverState.never_remind === true) {
            state.neverRemind = true;
        }
        const preserveLocalDeferred = (source === 'heartbeat' || source === 'shown')
            && previous.deferredUntil > Date.now()
            && deferredUntil <= Date.now()
            && status !== 'deferred'
            && status !== 'never'
            && status !== 'completed';
        if (!preserveLocalDeferred) {
            state.deferredUntil = deferredUntil;
        }
        if (status === 'started' || status === 'completed' || startedAt > 0 || completedAt > 0) {
            state.tutorialStarted = true;
        }
        if (status === 'completed' || completedAt > 0) {
            promptLifecycleStateStore.markHomeTutorialCompleted();
            promptLifecycleStateStore.clearTutorialRunToken();
            state.tutorialRunning = false;
            markHomeTutorialStorageSeen();
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
        return true;
    }

    async function loadInitialServerState() {
        const requestResetGeneration = state.resetGeneration;
        try {
            const response = await requestJson('/api/tutorial-prompt/state', {
                cache: 'no-store',
            });
            if (response && response.state) {
                applyServerState(response.state, 'initial-state', requestResetGeneration);
            }
        } catch (error) {
            console.warn('[TutorialPrompt] failed to load initial state:', error);
        }
    }

    function isHomeTutorialResetSensitiveLifecycle(url, payload) {
        return (url === '/api/tutorial-prompt/tutorial-started'
                || url === '/api/tutorial-prompt/tutorial-completed')
            && payload
            && payload.page === 'home';
    }

    async function persistTutorialLifecycle(url, payload, flowStep, options) {
        const requestOptions = options || {};
        const requestResetGeneration = state.resetGeneration;
        const isResetSensitiveLifecycle = isHomeTutorialResetSensitiveLifecycle(url, payload);
        if (isResetSensitiveLifecycle) {
            state.inFlightResetSensitiveLifecycleCount += 1;
        }
        try {
            const response = requestOptions.fireAndForget
                ? await fireAndForgetJson(url, payload)
                : await requestJson(url, {
                    method: 'POST',
                    json: payload,
                    keepalive: !!requestOptions.keepalive,
                });
            if (isStaleAfterClientReset(requestResetGeneration)) {
                logFlow('lifecycle-stale-ignored', {
                    source: flowStep || 'unknown',
                });
                if (isResetSensitiveLifecycle && state.pendingResetAfterLifecycleReason) {
                    const resetReason = state.pendingResetAfterLifecycleReason;
                    state.pendingResetAfterLifecycleReason = '';
                    await persistResetAfterStaleMutation(resetReason, 'reset-after-stale-lifecycle');
                }
                return response;
            }
            if (response && response.state) {
                applyServerState(response.state, flowStep, requestResetGeneration);
            }
            if (response && response.tutorial_run_token) {
                promptLifecycleStateStore.setTutorialRunToken(response.tutorial_run_token);
            }
            if (requestOptions.clearRunTokenOnSuccess && response && response.ok) {
                promptLifecycleStateStore.clearTutorialRunToken();
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
        } finally {
            if (isResetSensitiveLifecycle) {
                state.inFlightResetSensitiveLifecycleCount = Math.max(
                    0,
                    state.inFlightResetSensitiveLifecycleCount - 1
                );
            }
        }
    }

    async function postDecision(payload) {
        const requestResetGeneration = state.resetGeneration;
        try {
            const response = await requestJson('/api/tutorial-prompt/decision', {
                method: 'POST',
                json: payload,
            });
            if (response && response.state) {
                applyServerState(response.state, 'decision', requestResetGeneration);
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
        const requestResetGeneration = state.resetGeneration;
        try {
            const response = await requestJson('/api/tutorial-prompt/shown', {
                method: 'POST',
                json: { prompt_token: promptToken },
            });
            if (response && response.state) {
                applyServerState(response.state, 'shown', requestResetGeneration);
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

    async function persistHomeTutorialCompletion(event, flowStep, persistedStep) {
        const source = event.detail.source || 'manual';
        const tutorialRunToken = await waitForTutorialRunToken(2000);
        logFlow(flowStep, {
            source: source,
            promptToken: shortPromptToken(state.promptDrivenTutorialToken),
            tutorialRunToken: shortTutorialRunToken(tutorialRunToken),
        });

        if (!tutorialRunToken) {
            logFlow(`${flowStep}-skipped`, {
                source: source,
                reason: 'missing_run_token',
            });
            promptLifecycleStateStore.clearPromptDrivenTutorialToken();
            return;
        }

        await persistTutorialLifecycle('/api/tutorial-prompt/tutorial-completed', {
            page: 'home',
            source: source,
            tutorial_run_token: tutorialRunToken,
        }, persistedStep, {
            clearRunTokenOnSuccess: true,
        });
        promptLifecycleStateStore.clearPromptDrivenTutorialToken();
    }

    function takeHeartbeatSnapshot() {
        return promptLifecycleStateStore.createHeartbeatSnapshot({
            foregroundMsDelta: consumeForegroundDelta(),
            homeInteractionsDelta: state.pendingWeakHomeInteractions,
            chatTurnsDelta: state.pendingChatTurns,
            voiceSessionsDelta: state.pendingVoiceSessions,
        });
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
        return promptLifecycleStateStore.hasReplaySensitiveHeartbeatMetrics(snapshot);
    }

    function shouldFlushHeartbeatSnapshot(snapshot) {
        return promptLifecycleStateStore.shouldFlushHeartbeatSnapshot(snapshot);
    }

    function resetHomeTutorialClientState(reason) {
        state.resetGeneration += 1;
        const snapshot = state.inFlightHeartbeatSnapshot;
        const resetReason = reason || 'manual_home_tutorial_reset';
        if (
            state.requestInFlight
            && snapshot
            && (snapshot.homeTutorialCompleted || snapshot.manualHomeTutorialViewed)
        ) {
            state.pendingResetAfterHeartbeatReason = resetReason;
        }
        if (state.inFlightResetSensitiveLifecycleCount > 0) {
            state.pendingResetAfterLifecycleReason = resetReason;
        }
        if (snapshot) {
            promptLifecycleStateStore.clearHeartbeatCompletionMarkers(snapshot);
        }

        promptLifecycleStateStore.resetTutorialCompletionState();
        state.neverRemind = false;
        state.deferredUntil = 0;
        state.localPromptSuppressedUntil = 0;
        state.lastPromptTokenSeen = null;
        window[HOME_TUTORIAL_PROMPT_SEEN_FLAG] = false;
        promptLifecycleStateStore.clearPromptDrivenTutorialToken();
        promptLifecycleStateStore.clearTutorialRunToken();
        state.pendingTutorialStartPayload = null;
        endHomeTutorialFeatureSuppression('home-tutorial-reset');
        emitHomeTutorialLockIfChanged('home-tutorial-reset');
        clearHomeTutorialStorageSeen();
        logFlow('home-tutorial-reset', {
            reason: resetReason,
        });
    }

    function handleHomeTutorialResetDetail(detail) {
        const payload = detail && typeof detail === 'object' ? detail : {};
        const page = String(payload.page || '').trim();
        if (page && page !== 'home' && page !== 'all') {
            return;
        }
        resetHomeTutorialClientState(String(payload.source || payload.reason || '').trim());
    }

    function buildHeartbeatPayload(snapshot) {
        return promptLifecycleStateStore.buildHeartbeatPayload(snapshot);
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

    function closeResetBroadcastChannel() {
        if (!state.resetBroadcastChannel) {
            return;
        }
        try {
            state.resetBroadcastChannel.close();
        } catch (_) {}
        state.resetBroadcastChannel = null;
    }

    async function persistResetAfterStaleMutation(reason, flowStep) {
        const source = flowStep || 'reset-after-stale-mutation';
        const requestResetGeneration = state.resetGeneration;
        try {
            const response = await requestJson('/api/tutorial-prompt/reset', {
                method: 'POST',
                json: {
                    reason: reason || 'manual_home_tutorial_reset',
                },
            });
            if (response && response.state) {
                applyServerState(response.state, source, requestResetGeneration);
            }
            logFlow(source, {
                reason: reason || 'manual_home_tutorial_reset',
            });
        } catch (error) {
            console.warn('[TutorialPrompt] failed to re-apply reset after stale mutation:', error);
        }
    }

    async function sendHeartbeat() {
        if (!state.initialized) return;
        if (state.requestInFlight) {
            state.pendingHeartbeatAfterFlight = true;
            return;
        }

        state.requestInFlight = true;
        const requestResetGeneration = state.resetGeneration;
        const snapshot = takeHeartbeatSnapshot();
        const payload = buildHeartbeatPayload(snapshot);
        state.inFlightHeartbeatSnapshot = snapshot;
        let data = null;

        try {
            clearHeartbeatSnapshot();

            if (state.pendingTutorialStartPayload && !state.pendingTutorialStartPersistence) {
                const retryPersistence = persistTutorialLifecycle(
                    '/api/tutorial-prompt/tutorial-started',
                    state.pendingTutorialStartPayload,
                    'tutorial-started-persisted'
                );
                state.pendingTutorialStartPersistence = retryPersistence;
                const retryResponse = await retryPersistence;
                if (retryResponse && retryResponse.ok !== false) {
                    state.pendingTutorialStartPayload = null;
                }
                if (state.pendingTutorialStartPersistence === retryPersistence) {
                    state.pendingTutorialStartPersistence = null;
                }
            }

            data = await requestJson(HEARTBEAT_ENDPOINT, {
                method: 'POST',
                json: payload,
                keepalive: true,
            });
            if (data && data.state) {
                applyServerState(data.state, 'heartbeat', requestResetGeneration);
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
            if (!isStaleAfterClientReset(requestResetGeneration) && data && data.should_prompt) {
                await maybeShowPrompt(data.prompt_token);
            }
        } catch (error) {
            console.warn('[TutorialPrompt] failed to render tutorial prompt:', error);
        } finally {
            const shouldReapplyReset = isStaleAfterClientReset(requestResetGeneration)
                && !!state.pendingResetAfterHeartbeatReason;
            const resetReason = state.pendingResetAfterHeartbeatReason;
            if (shouldReapplyReset) {
                state.pendingResetAfterHeartbeatReason = '';
            }
            if (state.inFlightHeartbeatSnapshot === snapshot) {
                state.inFlightHeartbeatSnapshot = null;
            }
            state.requestInFlight = false;
            if (shouldReapplyReset) {
                await persistResetAfterStaleMutation(resetReason, 'reset-after-stale-heartbeat');
            }
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
            || hasPromptBeenSeenInThisTab()
            || state.neverRemind
            || isPromptSuppressedInThisTab()
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
        promptLifecycleStateStore.setPromptDrivenTutorialToken(promptToken);
        state.tutorialStartRequested = true;
        emitHomeTutorialLockIfChanged('tutorial-start-requested');
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
            promptLifecycleStateStore.clearTutorialRunToken();
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
            promptLifecycleStateStore.clearPromptDrivenTutorialToken();
            state.tutorialStartRequested = false;
            emitHomeTutorialLockIfChanged('tutorial-start-failed');
        } finally {
            if (!state.tutorialRunning) {
                state.tutorialStartRequested = false;
                emitHomeTutorialLockIfChanged('tutorial-start-settled');
            }
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
        const promptResetGeneration = state.resetGeneration;
        state.promptOpen = true;
        emitHomeTutorialLockIfChanged('prompt-open');
        state.lastPromptTokenSeen = promptToken;
        window[HOME_TUTORIAL_PROMPT_SEEN_FLAG] = true;
        logFlow('prompt-open', { token: shortPromptToken(promptToken) });
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
            if (isStaleAfterClientReset(promptResetGeneration)) {
                logFlow('prompt-decision-stale-ignored', {
                    token: shortPromptToken(promptToken),
                    decision: decision || null,
                });
                return;
            }

            if (decision === 'never') {
                const decisionResetGeneration = state.resetGeneration;
                promptLifecycleStateStore.clearPromptDrivenTutorialToken();
                state.neverRemind = true;
                state.deferredUntil = 0;
                state.localPromptSuppressedUntil = 0;
                emitHomeTutorialLockIfChanged('prompt-never-local');
                await postDecision({ decision: 'never', prompt_token: promptToken });
                if (isStaleAfterClientReset(decisionResetGeneration)) {
                    return;
                }
                state.neverRemind = true;
                state.deferredUntil = 0;
                state.localPromptSuppressedUntil = 0;
                emitHomeTutorialLockIfChanged('prompt-never-local-confirmed');
                return;
            }
            if (decision === 'later') {
                const decisionResetGeneration = state.resetGeneration;
                promptLifecycleStateStore.clearPromptDrivenTutorialToken();
                const localSuppressUntil = Date.now() + LOCAL_PROMPT_LATER_SUPPRESS_MS;
                state.deferredUntil = localSuppressUntil;
                state.localPromptSuppressedUntil = localSuppressUntil;
                emitHomeTutorialLockIfChanged('prompt-later-local');
                await postDecision({ decision: 'later', prompt_token: promptToken });
                if (isStaleAfterClientReset(decisionResetGeneration)) {
                    return;
                }
                if (!isPromptSuppressedInThisTab()) {
                    const confirmedSuppressUntil = Date.now() + LOCAL_PROMPT_LATER_SUPPRESS_MS;
                    state.deferredUntil = confirmedSuppressUntil;
                    state.localPromptSuppressedUntil = confirmedSuppressUntil;
                    emitHomeTutorialLockIfChanged('prompt-later-local-confirmed');
                }
                return;
            }
            if (decision === 'accept') {
                await handlePromptAcceptance(promptToken);
            }
        } finally {
            state.promptOpen = false;
            emitHomeTutorialLockIfChanged('prompt-closed');
        }
    }

    async function maybeShowPrompt(promptToken) {
        if (!promptToken) {
            return;
        }

        state.promptDisplayPending = true;
        emitHomeTutorialLockIfChanged('prompt-display-pending');
        try {
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
        } finally {
            state.promptDisplayPending = false;
            emitHomeTutorialLockIfChanged('prompt-display-settled');
        }
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
            state.tutorialStartRequested = false;
            state.tutorialStarted = true;
            promptLifecycleStateStore.markHomeTutorialCompleted();
            endHomeTutorialFeatureSuppression('tutorial-completed');
            emitHomeTutorialLockIfChanged('tutorial-completed');
            void (async function () {
                await persistHomeTutorialCompletion(
                    event,
                    'tutorial-completed',
                    'tutorial-completed-persisted'
                );
            })();
            scheduleFastHeartbeat();
        });

        window.addEventListener('neko:tutorial-started', function (event) {
            if (!event || !event.detail || event.detail.page !== 'home') {
                return;
            }
            beginHomeTutorialFeatureSuppression('tutorial-started');
            state.tutorialRunning = true;
            state.tutorialStartRequested = false;
            state.tutorialStarted = true;
            emitHomeTutorialLockIfChanged('tutorial-started');
            if (event.detail.source !== 'idle_prompt') {
                promptLifecycleStateStore.clearPromptDrivenTutorialToken();
            }
            if (event.detail.source === 'manual') {
                promptLifecycleStateStore.markManualHomeTutorialViewed();
            }
            logFlow('tutorial-started', {
                source: event.detail.source || 'unknown',
                promptToken: shortPromptToken(state.promptDrivenTutorialToken || state.lastPromptTokenSeen),
                tutorialRunToken: shortTutorialRunToken(state.tutorialRunToken),
            });
            const startPayload = {
                page: 'home',
                source: event.detail.source || 'manual',
                prompt_token: event.detail.source === 'idle_prompt'
                    ? state.promptDrivenTutorialToken
                    : undefined,
            };
            state.pendingTutorialStartPayload = startPayload;
            const startPersistence = persistTutorialLifecycle(
                '/api/tutorial-prompt/tutorial-started',
                startPayload,
                'tutorial-started-persisted'
            );
            state.pendingTutorialStartPersistence = startPersistence;
            void startPersistence.then(function (response) {
                if (response && response.ok !== false) {
                    state.pendingTutorialStartPayload = null;
                }
                return response;
            }).finally(function () {
                if (state.pendingTutorialStartPersistence === startPersistence) {
                    state.pendingTutorialStartPersistence = null;
                }
            });
            scheduleFastHeartbeat();
        });

        window.addEventListener('neko:tutorial-skipped', function (event) {
            if (!event || !event.detail || event.detail.page !== 'home') {
                return;
            }
            state.tutorialRunning = false;
            state.tutorialStartRequested = false;
            state.tutorialStarted = true;
            promptLifecycleStateStore.markHomeTutorialCompleted();
            markHomeTutorialStorageSeen();
            endHomeTutorialFeatureSuppression('tutorial-skipped');
            emitHomeTutorialLockIfChanged('tutorial-skipped');
            void (async function () {
                await persistHomeTutorialCompletion(
                    event,
                    'tutorial-skipped',
                    'tutorial-skipped-persisted'
                );
            })();
            scheduleFastHeartbeat();
        });

        // Keep this recovery bridge paired with handlePromptAcceptance:
        // its catch path clears state.tutorialStartRequested, calls
        // emitHomeTutorialLockIfChanged('tutorial-start-failed'), and this
        // listener then rolls back beginHomeTutorialFeatureSuppression via
        // endHomeTutorialFeatureSuppression. Removing it would leave feature
        // suppression active for the early 'tutorial-start-requested' lock.
        window.addEventListener('neko:home-tutorial-lock-changed', function (event) {
            const detail = event && event.detail ? event.detail : {};
            if (detail.locked === true && detail.reason === 'tutorial-start-requested') {
                beginHomeTutorialFeatureSuppression('tutorial-start-requested');
            } else if (detail.locked === false && !state.tutorialRunning && !state.tutorialStartRequested) {
                endHomeTutorialFeatureSuppression(detail.reason || 'lock-released');
            }
        });

        window.addEventListener(HOME_TUTORIAL_RESET_EVENT, function (event) {
            handleHomeTutorialResetDetail(event && event.detail ? event.detail : {});
        });

        window.addEventListener('storage', function (event) {
            if (!event || event.key !== HOME_TUTORIAL_RESET_STORAGE_EVENT_KEY || !event.newValue) {
                return;
            }
            try {
                handleHomeTutorialResetDetail(JSON.parse(event.newValue));
            } catch (error) {
                console.warn('[TutorialPrompt] failed to parse home tutorial reset storage event:', error);
            }
        });

        if (typeof BroadcastChannel === 'function') {
            try {
                state.resetBroadcastChannel = new BroadcastChannel(HOME_TUTORIAL_RESET_CHANNEL);
                state.resetBroadcastChannel.addEventListener('message', function (event) {
                    const message = event && event.data ? event.data : {};
                    if (!message || message.type !== HOME_TUTORIAL_RESET_EVENT) {
                        return;
                    }
                    handleHomeTutorialResetDetail(message.detail || {});
                });
            } catch (error) {
                console.warn('[TutorialPrompt] failed to listen for home tutorial reset broadcasts:', error);
            }
        }

        window.addEventListener('beforeunload', closeResetBroadcastChannel);
        window.addEventListener('beforeunload', flushHeartbeatOnUnload);
    }

    mod.init = function init() {
        if (state.initialized) return;
        if (isMobileTutorialDisabled()) {
            bindMobileTutorialPromptResizeRetry();
            return;
        }

        promptLifecycleStateStore.setHomeTutorialCompleted(isHomeTutorialSeen());
        state.initialized = true;
        syncForegroundWindow();
        bindEvents();
        restoreInterruptedAgentSuppression('init');

        state.heartbeatTimer = setInterval(function () {
            void sendHeartbeat();
        }, HEARTBEAT_INTERVAL_MS);

        void loadInitialServerState().finally(function () {
            void sendHeartbeat();
        });
    };

    window.appTutorialPrompt = mod;
})();
