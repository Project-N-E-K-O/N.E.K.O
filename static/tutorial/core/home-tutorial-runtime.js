(function () {
    'use strict';

    const HOME_TUTORIAL_AGENT_RESTORE_STORAGE_KEY = 'neko.homeTutorial.agentRestoreSnapshot.v1';
    const HOME_TUTORIAL_AGENT_RESTORE_MAX_AGE_MS = 24 * 60 * 60 * 1000;
    const HOME_TUTORIAL_AGENT_RESTORE_MAX_FUTURE_SKEW_MS = 60 * 1000;
    const HOME_TUTORIAL_AGENT_RESTORE_RETRY_DELAYS_MS = Object.freeze([1000, 2000, 4000, 8000, 15000]);
    const HOME_TUTORIAL_AGENT_FLAG_KEYS = Object.freeze([
        'agent_enabled',
        'computer_use_enabled',
        'browser_use_enabled',
        'user_plugin_enabled',
    ]);

    const mod = {};
    let interruptedRestoreRetryTimer = null;
    let interruptedRestoreRetryCancel = null;
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

    // 纯运行时抑制：绝不改写任何用户设置的内存镜像——与小剧场
    // lockProactiveChatForTheater 的约定一致（见 app-proactive.js「transient
    // suppression」段注释）。这十个 proactive 键由 saveSettings 持久化并经
    // storage 事件、周期同步、server merge 扩散，临时改写会在抑制期间的任意
    // 一次保存里被当成用户设置固化。抑制只做三件事：登记标志、停运行时、
    // 派发事件；触发面由 app-proactive 的 isHomeTutorialFeatureSuppressed
    // 统一拦截，释放时由事件监听负责重排与补挂。
    function stopProactiveRuntime() {
        [
            'stopProactiveChatSchedule',
            'stopProactiveVisionDuringSpeech',
            'releaseProactiveVisionStream'
        ].forEach(function (methodName) {
            if (typeof window[methodName] === 'function') {
                try {
                    window[methodName]();
                } catch (error) {
                    console.warn('[HomeTutorialRuntime] failed to stop proactive runtime:', methodName, error);
                }
            }
        });
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

    function getCurrentLanlanName() {
        const fromConfig = window.lanlan_config && typeof window.lanlan_config.lanlan_name === 'string'
            ? window.lanlan_config.lanlan_name
            : '';
        const fromState = window.appState && typeof window.appState.lanlan_name === 'string'
            ? window.appState.lanlan_name
            : '';
        return String(fromConfig || fromState || '').trim();
    }

    async function waitForStorageBarrier() {
        if (typeof window.waitForStorageLocationStartupBarrier === 'function') {
            await window.waitForStorageLocationStartupBarrier();
        } else if (window.__nekoStorageLocationStartupBarrier
            && typeof window.__nekoStorageLocationStartupBarrier.then === 'function') {
            await window.__nekoStorageLocationStartupBarrier;
        }
    }

    function cancelInterruptedRestoreRetry() {
        if (interruptedRestoreRetryTimer !== null) {
            clearTimeout(interruptedRestoreRetryTimer);
            interruptedRestoreRetryTimer = null;
        }
        if (typeof interruptedRestoreRetryCancel === 'function') {
            const cancel = interruptedRestoreRetryCancel;
            interruptedRestoreRetryCancel = null;
            cancel(false);
        }
    }

    function hasAnyEnabledAgentFlag(flags) {
        if (!flags || typeof flags !== 'object') {
            return false;
        }
        return HOME_TUTORIAL_AGENT_FLAG_KEYS.some(function (key) {
            return flags[key] === true;
        });
    }

    function hasUnexpectedChildFlagEnabled(currentFlags, expectedFlags, options) {
        if (!currentFlags || typeof currentFlags !== 'object') {
            return false;
        }
        const allowMasterPluginSideEffect = !!(options && options.allowMasterPluginSideEffect);
        return HOME_TUTORIAL_AGENT_FLAG_KEYS.some(function (key) {
            if (key === 'agent_enabled') {
                return false;
            }
            if (allowMasterPluginSideEffect && key === 'user_plugin_enabled') {
                return false;
            }
            return currentFlags[key] === true
                && !(expectedFlags && expectedFlags[key] === true);
        });
    }

    function inspectPersistedAgentRestoreSnapshot() {
        try {
            const raw = localStorage.getItem(HOME_TUTORIAL_AGENT_RESTORE_STORAGE_KEY);
            if (!raw) {
                return { status: 'empty', payload: null };
            }
            const payload = JSON.parse(raw);
            if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
                return { status: 'invalid', payload: null };
            }
            if (payload.version !== 1) {
                return { status: 'invalid', payload: null };
            }
            if (!payload.agentFlags || typeof payload.agentFlags !== 'object' || Array.isArray(payload.agentFlags)) {
                return { status: 'invalid', payload: null };
            }
            if (typeof payload.createdAt !== 'number' || !Number.isFinite(payload.createdAt)) {
                return { status: 'invalid', payload: null };
            }
            const now = Date.now();
            if (payload.createdAt > now + HOME_TUTORIAL_AGENT_RESTORE_MAX_FUTURE_SKEW_MS) {
                return { status: 'invalid', payload: null };
            }
            if (now - payload.createdAt > HOME_TUTORIAL_AGENT_RESTORE_MAX_AGE_MS) {
                return { status: 'invalid', payload: null };
            }
            const snapshotOwner = typeof payload.lanlanName === 'string' ? payload.lanlanName.trim() : '';
            const currentOwner = getCurrentLanlanName();
            if (snapshotOwner) {
                if (!currentOwner) {
                    return { status: 'owner_pending', payload: payload };
                }
                if (snapshotOwner !== currentOwner) {
                    return { status: 'owner_mismatch', payload: payload };
                }
            }
            return { status: 'valid', payload: payload };
        } catch (error) {
            console.warn('[HomeTutorialRuntime] failed to read persisted agent restore snapshot:', error);
            return { status: 'invalid', payload: null };
        }
    }

    function readPersistedAgentRestoreSnapshot() {
        const inspected = inspectPersistedAgentRestoreSnapshot();
        return inspected.status === 'valid' ? inspected.payload : null;
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
                lanlanName: getCurrentLanlanName(),
                agentFlags: flags,
            }));
        } catch (error) {
            console.warn('[HomeTutorialRuntime] failed to persist agent restore snapshot:', error);
        }
    }

    function clearPersistedAgentRestoreSnapshot(token) {
        try {
            if (token !== undefined) {
                const inspected = inspectPersistedAgentRestoreSnapshot();
                if (inspected.payload && inspected.payload.token !== token) {
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

    function matchesSnapshotOwner(expectedOwner) {
        const normalizedOwner = typeof expectedOwner === 'string' ? expectedOwner.trim() : '';
        if (!normalizedOwner) {
            return true;
        }
        const currentOwner = getCurrentLanlanName();
        return !!currentOwner && currentOwner === normalizedOwner;
    }

    function canRestoreAgentSnapshot(restoreToken, expectedOwner) {
        return !state.featureSuppression.active
            && state.featureSuppression.token === restoreToken
            && matchesSnapshotOwner(expectedOwner);
    }

    function isFeatureSuppressionTokenActive(token) {
        return state.featureSuppression.active
            && state.featureSuppression.token === token;
    }

    async function restoreAgentSnapshot(flags, restoreToken, attemptState, expectedOwner) {
        if (!flags) {
            return false;
        }
        if (restoreToken !== undefined && !canRestoreAgentSnapshot(restoreToken, expectedOwner)) {
            return false;
        }
        if (Object.prototype.hasOwnProperty.call(flags, 'agent_enabled')) {
            if (attemptState && flags.agent_enabled) {
                attemptState.masterAttempted = true;
            }
            await postAgentCommand('set_agent_enabled', {
                enabled: !!flags.agent_enabled,
            });
            if (attemptState && flags.agent_enabled) {
                attemptState.masterApplied = true;
            }
        }
        if (restoreToken !== undefined && !canRestoreAgentSnapshot(restoreToken, expectedOwner)) {
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
        if (restoreToken !== undefined && !canRestoreAgentSnapshot(restoreToken, expectedOwner)) {
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
        // 开关本身由 agent_ui_v2 按后端推来的状态快照刷新，这里只需要让任务 HUD 跟上。
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
        cancelInterruptedRestoreRetry();
        const token = Date.now() + Math.random();
        const snapshot = {
            galgameEnabled: snapshotGalgameState(),
            agentFlags: null,
            reason: reason || 'tutorial-started',
        };
        suppression.active = true;
        suppression.token = token;
        suppression.snapshot = snapshot;

        setGalgameState(false);
        stopProactiveRuntime();
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
        stopProactiveRuntime();
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
        cancelInterruptedRestoreRetry();
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
        // 主动搭话没有任何设置值需要恢复（抑制从不改写内存）：下方派发的
        // active:false 事件由 app-proactive 监听，负责重排调度、补挂视觉，
        // 情境弹窗模块负责重放暂存提示。头像重载层若仍持有运行时 hold，
        // 触发闸门会继续拦截到模型恢复完成，并由它自己的释放事件再补一轮。
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

    async function restoreInterruptedAgentSuppression(reason) {
        if (state.featureSuppression.active) {
            return false;
        }
        cancelInterruptedRestoreRetry();
        const restoreToken = Date.now() + Math.random();
        state.featureSuppression.token = restoreToken;

        try {
            await waitForStorageBarrier();
        } catch (barrierError) {
            console.warn('[HomeTutorialRuntime] storage barrier failed before interrupted agent restore:', barrierError);
            return false;
        }
        if (!canRestoreAgentSnapshot(restoreToken)) {
            return false;
        }

        const inspected = inspectPersistedAgentRestoreSnapshot();
        if (inspected.status === 'empty' || inspected.status === 'owner_mismatch') {
            return false;
        }
        if (inspected.status !== 'valid' && inspected.status !== 'owner_pending') {
            clearPersistedAgentRestoreSnapshot();
            return false;
        }
        if (!inspected.payload) {
            clearPersistedAgentRestoreSnapshot();
            return false;
        }
        const persisted = inspected.payload;
        const expectedOwner = typeof persisted.lanlanName === 'string' ? persisted.lanlanName.trim() : '';
        if (!hasAnyEnabledAgentFlag(persisted.agentFlags)) {
            clearPersistedAgentRestoreSnapshot(persisted.token);
            return true;
        }

        const attemptState = { masterAttempted: false, masterApplied: false };

        async function runRestoreAttempt(retryIndex) {
            if (!canRestoreAgentSnapshot(restoreToken)) {
                return false;
            }
            const latestInspected = inspectPersistedAgentRestoreSnapshot();
            if (latestInspected.status === 'owner_mismatch' || latestInspected.status === 'empty') {
                return false;
            }
            if (latestInspected.status !== 'valid' && latestInspected.status !== 'owner_pending') {
                clearPersistedAgentRestoreSnapshot();
                return false;
            }
            const latestPersisted = latestInspected.payload;
            if (!latestPersisted || latestPersisted.token !== persisted.token) {
                return false;
            }

            try {
                if (latestInspected.status === 'owner_pending') {
                    throw new Error('agent_restore_owner_pending');
                }
                const currentFlags = await fetchAgentFlagSnapshot();
                if (!canRestoreAgentSnapshot(restoreToken, expectedOwner)) {
                    return false;
                }
                const postFetchInspected = inspectPersistedAgentRestoreSnapshot();
                if (
                    postFetchInspected.status !== 'valid'
                    || !postFetchInspected.payload
                    || postFetchInspected.payload.token !== persisted.token
                ) {
                    return false;
                }
                const expectsMasterOn = persisted.agentFlags.agent_enabled === true;

                if (!attemptState.masterAttempted) {
                    if (hasAnyEnabledAgentFlag(currentFlags)) {
                        clearPersistedAgentRestoreSnapshot(persisted.token);
                        return false;
                    }
                } else if (attemptState.masterApplied) {
                    if (expectsMasterOn && currentFlags.agent_enabled !== true) {
                        clearPersistedAgentRestoreSnapshot(persisted.token);
                        return false;
                    }
                    if (hasUnexpectedChildFlagEnabled(currentFlags, persisted.agentFlags, { allowMasterPluginSideEffect: expectsMasterOn })) {
                        clearPersistedAgentRestoreSnapshot(persisted.token);
                        return false;
                    }
                } else {
                    if (currentFlags.agent_enabled === true) {
                        if (hasUnexpectedChildFlagEnabled(currentFlags, persisted.agentFlags, { allowMasterPluginSideEffect: expectsMasterOn })) {
                            clearPersistedAgentRestoreSnapshot(persisted.token);
                            return false;
                        }
                        attemptState.masterApplied = true;
                    } else if (hasAnyEnabledAgentFlag(currentFlags)) {
                        clearPersistedAgentRestoreSnapshot(persisted.token);
                        return false;
                    }
                }

                const restored = await restoreAgentSnapshot(
                    persisted.agentFlags,
                    restoreToken,
                    attemptState,
                    expectedOwner
                );
                if (restored && canRestoreAgentSnapshot(restoreToken, expectedOwner)) {
                    clearPersistedAgentRestoreSnapshot(persisted.token);
                    syncAgentFlagsUi();
                    return true;
                }
                return false;
            } catch (error) {
                if (!canRestoreAgentSnapshot(restoreToken)) {
                    return false;
                }
                if (inspectPersistedAgentRestoreSnapshot().status === 'owner_mismatch') {
                    return false;
                }
                if (retryIndex < HOME_TUTORIAL_AGENT_RESTORE_RETRY_DELAYS_MS.length) {
                    const delayMs = HOME_TUTORIAL_AGENT_RESTORE_RETRY_DELAYS_MS[retryIndex];
                    return await new Promise(function (resolve) {
                        let settled = false;
                        const finish = function (result) {
                            if (settled) {
                                return;
                            }
                            settled = true;
                            if (interruptedRestoreRetryCancel === finish) {
                                interruptedRestoreRetryCancel = null;
                            }
                            resolve(result);
                        };
                        interruptedRestoreRetryCancel = finish;
                        interruptedRestoreRetryTimer = setTimeout(function () {
                            interruptedRestoreRetryTimer = null;
                            if (interruptedRestoreRetryCancel === finish) {
                                interruptedRestoreRetryCancel = null;
                            }
                            void runRestoreAttempt(retryIndex + 1).then(finish);
                        }, delayMs);
                    });
                }
                console.warn('[HomeTutorialRuntime] failed to restore interrupted agent suppression:', reason || '', error);
                return false;
            }
        }

        return await runRestoreAttempt(0);
    }

    mod.beginHomeTutorialFeatureSuppression = beginHomeTutorialFeatureSuppression;
    mod.enforceHomeTutorialFeatureSuppression = enforceHomeTutorialFeatureSuppression;
    mod.endHomeTutorialFeatureSuppression = endHomeTutorialFeatureSuppression;
    mod.restoreInterruptedAgentSuppression = restoreInterruptedAgentSuppression;
    mod.isHomeTutorialFeatureSuppressionActive = function () {
        return !!state.featureSuppression.active;
    };
    window.NekoHomeTutorialFeatureController = {
        begin: beginHomeTutorialFeatureSuppression,
        enforce: enforceHomeTutorialFeatureSuppression,
        end: endHomeTutorialFeatureSuppression,
        restoreInterrupted: restoreInterruptedAgentSuppression,
        isActive: mod.isHomeTutorialFeatureSuppressionActive,
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

    void restoreInterruptedAgentSuppression('init');
    emitHomeTutorialLockIfChanged('init');
})();
