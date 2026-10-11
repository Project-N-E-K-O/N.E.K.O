(function () {
    'use strict';

    const HOME_TUTORIAL_AGENT_RESTORE_STORAGE_KEY = 'neko.homeTutorial.agentRestoreSnapshot.v1';
    const HOME_TUTORIAL_AGENT_FLAG_KEYS = Object.freeze([
        'agent_enabled',
        'computer_use_enabled',
        'browser_use_enabled',
        'user_plugin_enabled',
        'openclaw_enabled',
    ]);
    const HOME_TUTORIAL_PROACTIVE_KEYS = Object.freeze([
        'proactiveChatEnabled',
        'proactiveVisionEnabled',
        'proactiveVisionChatEnabled',
        'proactiveNewsChatEnabled',
        'proactiveCommunityChatEnabled',
        'proactiveVideoChatEnabled',
        'proactivePersonalChatEnabled',
        'proactiveMusicEnabled',
        'proactiveMemeEnabled',
        'proactiveMiniGameInviteEnabled',
    ]);

    const mod = {};
    const state = {
        featureSuppression: {
            active: false,
            token: 0,
            snapshot: null,
            agentSuppressionPromise: null,
        },
    };

    function isHomePage() {
        if (window.location && typeof window.location.pathname === 'string') {
            const path = window.location.pathname || '/';
            return path === '/' || path === '/index.html';
        }
        const manager = window.universalTutorialManager || null;
        return !!(manager && manager.currentPage === 'home');
    }

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
                console.warn('[HomeTutorialRuntime] failed to set GalGame state:', error);
            }
        }
    }

    function readPersistedProactiveSettings() {
        // app-settings 的「接受真值」优先（含跨窗口出处校验结果，竞态中被
        // 拒绝的陈旧持久值不会被当成用户设置）；未加载时回退原始持久层。
        try {
            const appSettings = window.appSettings;
            if (appSettings && typeof appSettings.getProactiveUserTruth === 'function') {
                const truth = appSettings.getProactiveUserTruth();
                if (truth && typeof truth === 'object') return truth;
            }
        } catch (_) { }
        try {
            const storage = window.localStorage || null;
            const raw = storage ? storage.getItem('project_neko_settings') : null;
            if (!raw) return null;
            const settings = JSON.parse(raw);
            if (!settings || typeof settings !== 'object') return null;
            return settings;
        } catch (_) {
            return null;
        }
    }

    function snapshotProactiveState() {
        const snapshot = {};
        const appState = window.appState || null;
        // 持久化设置是用户意图的权威来源：教程抑制是多层的（头像重载控制器会先于
        // feature controller begin 把这十个键在内存里关掉），直接快照内存会把上一层
        // 的临时抑制当成用户设置，end() 时"恢复"出全关。教程期间设置面板被锁、
        // 落盘又有护栏，localStorage 里的值就是教程前的用户真值。
        const persisted = readPersistedProactiveSettings();
        HOME_TUTORIAL_PROACTIVE_KEYS.forEach(function (key) {
            if (persisted && typeof persisted[key] === 'boolean') {
                snapshot[key] = persisted[key];
            } else if (typeof window[key] !== 'undefined') {
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
                console.warn('[HomeTutorialRuntime] failed to stop proactive schedule:', error);
            }
        }
        if (typeof window.stopProactiveVisionDuringSpeech === 'function') {
            try {
                window.stopProactiveVisionDuringSpeech();
            } catch (error) {
                console.warn('[HomeTutorialRuntime] failed to stop proactive vision:', error);
            }
        }
        if (typeof window.releaseProactiveVisionStream === 'function') {
            try {
                window.releaseProactiveVisionStream();
            } catch (error) {
                console.warn('[HomeTutorialRuntime] failed to release proactive vision stream:', error);
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
                console.warn('[HomeTutorialRuntime] failed to restart proactive schedule:', error);
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
            console.warn('[HomeTutorialRuntime] failed to read persisted agent restore snapshot:', error);
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
            console.warn('[HomeTutorialRuntime] failed to persist agent restore snapshot:', error);
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
            console.warn('[HomeTutorialRuntime] failed to clear persisted agent restore snapshot:', error);
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
                    console.warn('[HomeTutorialRuntime] failed to re-suppress stale agent master restore:', error);
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
                    console.warn('[HomeTutorialRuntime] failed to re-suppress stale agent child flag restore:', error);
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
                    console.warn('[HomeTutorialRuntime] agent flag UI sync failed:', error);
                });
            } catch (error) {
                console.warn('[HomeTutorialRuntime] agent flag UI sync failed:', error);
            }
        }
        if (typeof window.checkAndToggleTaskHUD === 'function') {
            try {
                window.checkAndToggleTaskHUD();
            } catch (error) {
                console.warn('[HomeTutorialRuntime] agent HUD sync failed:', error);
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
                    console.warn('[HomeTutorialRuntime] failed to restore agent flags after stale master suppress:', restoreError);
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
                    console.warn('[HomeTutorialRuntime] failed to restore agent flags after stale suppress:', restoreError);
                }
                return;
            }
            syncAgentFlagsUi();
        } catch (error) {
            console.warn('[HomeTutorialRuntime] failed to suppress agent flags:', error);
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
            console.warn('[HomeTutorialRuntime] failed to enforce agent suppression:', reason || '', error);
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
        emitHomeTutorialLockIfChanged(reason || 'tutorial-started');
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

    function resolveRestoredProactiveState(snapshot) {
        // 教程期间被兄弟窗口修改、且经本窗口出处校验接受的新值（经
        // applySharedRuntimeSettings 传播：已进接受真值登记表与持久层）是比
        // begin 快照更新的用户意图：恢复前逐键重读 app-settings 的真值链，
        // begin 快照只兜真值链缺失的键，避免用旧快照覆盖教程期间的新改动。
        if (!snapshot || typeof snapshot !== 'object') {
            return snapshot;
        }
        try {
            const appSettings = window.appSettings;
            if (!appSettings || typeof appSettings.getProactiveUserTruth !== 'function') {
                return snapshot;
            }
            const truth = appSettings.getProactiveUserTruth();
            if (!truth || typeof truth !== 'object') {
                return snapshot;
            }
            const resolved = {};
            HOME_TUTORIAL_PROACTIVE_KEYS.forEach(function (key) {
                if (typeof truth[key] === 'boolean') {
                    resolved[key] = truth[key];
                } else if (typeof snapshot[key] === 'boolean') {
                    resolved[key] = snapshot[key];
                }
            });
            return resolved;
        } catch (_) {
            return snapshot;
        }
    }

    function isAvatarReloadProactiveSuppressed() {
        // 头像重载层是否仍持有自己的抑制：prelude 里它先于本 controller
        // 快照并关闭内存，其 restoreOverride 会在异步模型恢复完成后用
        // 自己（更早、更纯净）的快照恢复并重排调度。
        try {
            const manager = window.universalTutorialManager;
            const reloadController = manager ? manager._tutorialAvatarReloadController : null;
            return !!(reloadController
                && typeof reloadController.isProactiveSuppressed === 'function'
                && reloadController.isProactiveSuppressed());
        } catch (_) {
            return false;
        }
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
        // 头像重载层仍持有抑制时不抢先恢复：skip/angry-exit 的收口顺序是
        // 本 end() 先跑、restoreTutorialAvatarOverride 后跑（内部要 await
        // 模型重载）。若在这里就还原真值并重启调度器，模型恢复完成前的
        // 异步窗口里主动搭话可能对着仍在展示的临时教程模型开口。让位后
        // 由重载层 restoreOverride 的 finally 统一恢复并重排（其快照更早、
        // 对持久层缺失键也更纯净）。
        if (snapshot.proactive && !isAvatarReloadProactiveSuppressed()) {
            const restoredProactive = resolveRestoredProactiveState(snapshot.proactive);
            applyProactiveState(restoredProactive);
            maybeRestartProactiveSchedule(restoredProactive);
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
                    console.warn('[HomeTutorialRuntime] failed to restore agent flags:', error);
                });
        }

        window.dispatchEvent(new CustomEvent('neko:home-tutorial-features-suppressed', {
            detail: { active: false, reason: reason || 'tutorial-ended' },
        }));
        emitHomeTutorialLockIfChanged(reason || 'tutorial-ended');
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
                console.warn('[HomeTutorialRuntime] failed to restore interrupted agent suppression:', error);
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
        getSuppressedUserValues: function () {
            // 抑制快照（= 教程开始前的用户真值，持久层缺失的键由 begin 时的
            // 内存真值兜底）：供 app-settings 的持久化护栏在持久层缺键时回退；
            // 非抑制期返回 null。
            const suppression = state.featureSuppression;
            if (!suppression.active || !suppression.snapshot || !suppression.snapshot.proactive) {
                return null;
            }
            return suppression.snapshot.proactive;
        },
    };

    function computeHomeTutorialInteractionLocked() {
        if (!isHomePage()) {
            return false;
        }
        const manager = window.universalTutorialManager || null;
        return !!(
            window.isNekoHomeTutorialPending === true
            || window.isInTutorial === true
            || state.featureSuppression.active
            || (manager && manager.currentPage === 'home' && manager.isTutorialRunning)
        );
    }

    let lastInteractionLocked = null;
    function emitHomeTutorialLockIfChanged(reason) {
        const locked = computeHomeTutorialInteractionLocked();
        if (lastInteractionLocked === locked) {
            return locked;
        }
        lastInteractionLocked = locked;
        window.dispatchEvent(new CustomEvent('neko:home-tutorial-lock-changed', {
            detail: {
                locked: locked,
                reason: reason || 'state-change',
            },
        }));
        return locked;
    }

    window.isNekoHomeTutorialInteractionLocked = computeHomeTutorialInteractionLocked;
    window.isNekoHomeTutorialBlockingGreeting = computeHomeTutorialInteractionLocked;
    window.NekoHomeTutorialRuntime = Object.freeze({
        isInteractionLocked: computeHomeTutorialInteractionLocked,
        refreshInteractionLock: emitHomeTutorialLockIfChanged,
    });

    restoreInterruptedAgentSuppression('init');
    emitHomeTutorialLockIfChanged('init');
})();
