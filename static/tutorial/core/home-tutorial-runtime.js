(function () {
    'use strict';

    const HOME_TUTORIAL_AGENT_RESTORE_STORAGE_KEY = 'neko.homeTutorial.agentRestoreSnapshot.v1';
    const HOME_TUTORIAL_AGENT_RESTORE_MAX_AGE_MS = 24 * 60 * 60 * 1000;
    const HOME_TUTORIAL_AGENT_RESTORE_MAX_FUTURE_SKEW_MS = 60 * 1000;
    const HOME_TUTORIAL_AGENT_RESTORE_RETRY_DELAYS_MS = Object.freeze([1000, 2000, 4000, 8000, 15000]);
    // 等后端启动时放回开关：约 45 秒，长于后端电脑/浏览器控制探测的最长用时（约 32 秒）。
    const HOME_TUTORIAL_AGENT_BACKEND_REPLAY_WAIT_DELAYS_MS = Object.freeze([1000, 2000, 4000, 8000, 15000, 15000]);
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

    async function fetchAgentFlagState() {
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
        return {
            flags: flags,
            // 后端启动后按记录的意图放回开关这一步是否已做完；没有这个字段时当作还没做完。
            intentRestoreFinished: payload.intent_restore_finished === true,
            // 后端明确说还没放回完（字段缺失时不算）。
            intentRestorePending: payload.intent_restore_finished === false,
            // 后端记录的开关意图：启动失败被后端关掉的开关意图不变，用户关掉的意图为 false。
            intentFlags: payload.intent && typeof payload.intent === 'object' && !Array.isArray(payload.intent)
                ? payload.intent
                : null,
        };
    }

    let _homeTutorialCsrfToken = '';

    async function getHomeTutorialMutationHeaders(forceRefresh) {
        try {
            const security = window.nekoLocalMutationSecurity;
            if (security && typeof security.getMutationHeaders === 'function') {
                if (forceRefresh && typeof security.refreshToken === 'function') {
                    await security.refreshToken();
                }
                const headers = await security.getMutationHeaders();
                if (headers && typeof headers === 'object' && (headers['X-CSRF-Token'] || headers['x-csrf-token'])) {
                    return headers;
                }
            }
        } catch (_) { /* fall back to page_config */ }
        if (forceRefresh) _homeTutorialCsrfToken = '';
        if (_homeTutorialCsrfToken) {
            return { 'X-CSRF-Token': _homeTutorialCsrfToken };
        }
        try {
            const resp = await fetch('/api/config/page_config', { cache: 'no-store' });
            const cfg = resp && resp.ok ? await resp.json() : null;
            const token = cfg && cfg.autostart_csrf_token;
            if (typeof token === 'string' && token) {
                _homeTutorialCsrfToken = token;
                return { 'X-CSRF-Token': token };
            }
        } catch (_) { /* guarded POST will surface rejection */ }
        return {};
    }

    async function postJsonWithCsrf(url, bodyObj, options) {
        const bodyJson = JSON.stringify(bodyObj);
        const shouldSend = options && typeof options.shouldSend === 'function' ? options.shouldSend : null;
        const onBeforeSend = options && typeof options.onBeforeSend === 'function' ? options.onBeforeSend : null;
        async function sendOnce(forceRefresh) {
            if (shouldSend && !shouldSend()) {
                return { abortedByGuard: true };
            }
            const csrfHeaders = await getHomeTutorialMutationHeaders(forceRefresh);
            if (shouldSend && !shouldSend()) {
                return { abortedByGuard: true };
            }
            if (onBeforeSend) {
                onBeforeSend();
            }
            return fetch(url, {
                method: 'POST',
                headers: Object.assign({ 'Content-Type': 'application/json' }, csrfHeaders),
                body: bodyJson,
            });
        }
        const response = await sendOnce(false);
        if (!response || response.abortedByGuard || response.status !== 403 || typeof response.clone !== 'function') {
            return response;
        }
        try {
            const payload = await response.clone().json();
            if (!payload || payload.error_code !== 'csrf_validation_failed') return response;
        } catch (_) {
            return response;
        }
        return sendOnce(true);
    }

    async function postAgentFlags(flags, options) {
        const response = await postJsonWithCsrf('/api/agent/flags', { flags: flags }, options);
        if (response && response.abortedByGuard) {
            return false;
        }
        if (!response || !response.ok) {
            throw new Error('agent_flags_post_failed');
        }
        const payload = await response.json().catch(function () { return null; });
        if (payload && payload.success === false) {
            throw new Error(payload.error || 'agent_flags_post_rejected');
        }
        return true;
    }

    async function postAgentCommand(command, payload, options) {
        const response = await postJsonWithCsrf('/api/agent/command', Object.assign({
            request_id: 'home-tutorial-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8),
            command: command,
        }, payload || {}), options);
        if (response && response.abortedByGuard) {
            return false;
        }
        if (!response || !response.ok) {
            throw new Error('agent_command_post_failed');
        }
        const result = await response.json().catch(function () { return null; });
        if (result && result.success === false) {
            throw new Error(result.error || 'agent_command_rejected');
        }
        return true;
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

    // 后端打开总开关时会顺带打开插件开关。总开关已由恢复流程打开后，
    // 如果快照要的是插件开、此刻插件却是关的，可能是用户自己关的，
    // 也可能是插件启动失败，前端分不清。两种情况都不该由恢复流程改它：
    // 重发总开关会把插件重新打开，写回插件开关会替用户记下一个意图。
    function isPluginOffAfterMasterOn(currentFlags, expectedFlags) {
        return !!(currentFlags && expectedFlags)
            && currentFlags.agent_enabled === true
            && expectedFlags.agent_enabled === true
            && expectedFlags.user_plugin_enabled === true
            && currentFlags.user_plugin_enabled === false;
    }

    // 总开关已开、插件状态交给后端现状时，只补其余子开关。
    function buildRestoreFlagsKeepingPluginState(expectedFlags) {
        const flags = Object.assign({}, expectedFlags);
        delete flags.agent_enabled;
        delete flags.user_plugin_enabled;
        return flags;
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

    function persistAgentRestoreSnapshot(flags, token, reason, owner) {
        if (!flags) {
            return;
        }
        try {
            localStorage.setItem(HOME_TUTORIAL_AGENT_RESTORE_STORAGE_KEY, JSON.stringify({
                version: 1,
                token: token,
                createdAt: Date.now(),
                reason: reason || 'home-tutorial-suppression',
                lanlanName: typeof owner === 'string' ? owner : getCurrentLanlanName(),
                agentFlags: flags,
            }));
        } catch (error) {
            console.warn('[HomeTutorialRuntime] failed to persist agent restore snapshot:', error);
        }
    }

    // 此刻的开关是否仍是「上一轮恢复没做完」的样子：全关（上一轮教程关掉的状态），
    // 或者恢复流程已开过总开关、开着的开关没超出旧快照。否则就是用户自己改过，以此刻为准。
    function isUnfinishedRecoveryState(currentFlags, payload) {
        if (!hasAnyEnabledAgentFlag(currentFlags)) {
            return true;
        }
        const expectedFlags = payload.agentFlags;
        const masterTouchedByRestore = payload.masterApplied === true || payload.masterAttempted === true;
        return masterTouchedByRestore
            && expectedFlags.agent_enabled === true
            && currentFlags.agent_enabled === true
            && !hasUnexpectedChildFlagEnabled(currentFlags, expectedFlags, { allowMasterPluginSideEffect: true });
    }

    // 教程开始时决定这一轮要恢复成什么。上一轮被打断的恢复可能还没做完（启动时要等存储就绪、
    // 冷启动还会退避重试），此刻读到的开关是上一轮关掉的「全关」，不能拿它覆盖旧快照。
    // 旧快照属于别的角色、此刻又是全关时，原样留着它，这一轮不写快照；
    // 此刻已有开着的开关，说明用户之后自己调过（开关是全局的），旧快照已过时，按此刻的存。
    function resolveAgentRestoreTarget(currentFlags, intentFlags) {
        const inspected = inspectPersistedAgentRestoreSnapshot();
        if (inspected.status === 'owner_mismatch') {
            return { flags: currentFlags, persist: hasAnyEnabledAgentFlag(currentFlags) };
        }
        if (
            (inspected.status === 'valid' || inspected.status === 'owner_pending')
            && inspected.payload
            && hasAnyEnabledAgentFlag(inspected.payload.agentFlags)
            && isUnfinishedRecoveryState(currentFlags, inspected.payload)
        ) {
            const flags = Object.assign({}, inspected.payload.agentFlags);
            // 恢复已开总开关、插件却是关的：用户关的（意图为 false）就记成关，免得教程结束时
            // 重开总开关又把插件顺带打开；启动失败被后端关掉的（意图仍是开）保留快照里的开。
            if (
                isPluginOffAfterMasterOn(currentFlags, inspected.payload.agentFlags)
                && intentFlags
                && intentFlags.user_plugin_enabled === false
            ) {
                flags.user_plugin_enabled = false;
            }
            return {
                flags: flags,
                owner: inspected.payload.lanlanName,
                persist: true,
            };
        }
        return { flags: currentFlags, persist: true };
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

    // 把「总开关已发出 / 已打开」写进持久化快照。恢复分两步（先总开关、后子开关），
    // 两步之间切换角色或关掉页面时，下次恢复要知道总开关是我们自己开的，
    // 否则会把它当成用户手动开的、清掉快照，子开关就再也补不回来。
    function markPersistedAgentRestoreProgress(token, field) {
        if (token === undefined || token === null) {
            return;
        }
        try {
            const raw = localStorage.getItem(HOME_TUTORIAL_AGENT_RESTORE_STORAGE_KEY);
            if (!raw) {
                return;
            }
            const payload = JSON.parse(raw);
            if (!payload || typeof payload !== 'object' || Array.isArray(payload) || payload.token !== token) {
                return;
            }
            if (payload[field] === true) {
                return;
            }
            payload[field] = true;
            localStorage.setItem(HOME_TUTORIAL_AGENT_RESTORE_STORAGE_KEY, JSON.stringify(payload));
        } catch (error) {
            console.warn('[HomeTutorialRuntime] failed to persist agent restore progress:', error);
        }
    }

    function createAgentRestoreAttemptState(snapshotToken, persisted) {
        const masterApplied = !!(persisted && persisted.masterApplied === true);
        return {
            snapshotToken: snapshotToken,
            masterAttempted: masterApplied || !!(persisted && persisted.masterAttempted === true),
            masterApplied: masterApplied,
            // 只有本页亲手打开或亲眼看到总开关开着，之后再看到它关着才算用户改动；
            // 上一页留下的进度遇到总开关关着，也可能是后端刚重启、还没把意图放回去。
            masterAppliedThisPage: false,
        };
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
        const isRestoreStillValid = function () {
            return restoreToken === undefined || canRestoreAgentSnapshot(restoreToken, expectedOwner);
        };
        if (!isRestoreStillValid()) {
            return false;
        }
        if (Object.prototype.hasOwnProperty.call(flags, 'agent_enabled')) {
            const masterSent = await postAgentCommand('set_agent_enabled', {
                enabled: !!flags.agent_enabled,
            }, {
                shouldSend: isRestoreStillValid,
                onBeforeSend: function () {
                    if (attemptState && flags.agent_enabled) {
                        attemptState.masterAttempted = true;
                        markPersistedAgentRestoreProgress(attemptState.snapshotToken, 'masterAttempted');
                    }
                },
            });
            if (!masterSent) {
                return false;
            }
            if (attemptState && flags.agent_enabled) {
                attemptState.masterApplied = true;
                attemptState.masterAppliedThisPage = true;
                markPersistedAgentRestoreProgress(attemptState.snapshotToken, 'masterApplied');
            }
        }
        if (!isRestoreStillValid()) {
            if (state.featureSuppression.active && flags.agent_enabled) {
                try {
                    await postAgentCommand('set_agent_enabled', { enabled: false }, {
                        shouldSend: function () { return !!state.featureSuppression.active; },
                    });
                } catch (error) {
                    console.warn('[HomeTutorialRuntime] failed to re-suppress stale agent master restore:', error);
                }
            }
            return false;
        }
        const childFlags = buildAgentChildFlags(flags);
        if (Object.keys(childFlags).length > 0) {
            const childSent = await postAgentFlags(childFlags, {
                shouldSend: isRestoreStillValid,
            });
            if (!childSent) {
                return false;
            }
        }
        if (!isRestoreStillValid()) {
            if (state.featureSuppression.active && Object.keys(childFlags).length > 0) {
                try {
                    await postAgentFlags(buildDisabledAgentChildFlags(), {
                        shouldSend: function () { return !!state.featureSuppression.active; },
                    });
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
            const currentState = await fetchAgentFlagState();
            const currentFlags = currentState.flags;
            if (!state.featureSuppression.active || state.featureSuppression.token !== token) {
                return;
            }
            const target = resolveAgentRestoreTarget(currentFlags, currentState.intentFlags);
            const flags = target.flags;
            if (state.featureSuppression.snapshot) {
                state.featureSuppression.snapshot.agentFlags = flags;
                state.featureSuppression.snapshot.agentRestoreToken = token;
                state.featureSuppression.snapshot.agentOwner = target.owner;
            }
            if (target.persist) {
                persistAgentRestoreSnapshot(flags, token, 'tutorial-suppression', target.owner);
            }
            const isSuppressionActive = function () {
                return isFeatureSuppressionTokenActive(token);
            };
            const masterSuppressed = await postAgentCommand('set_agent_enabled', { enabled: false }, {
                shouldSend: isSuppressionActive,
            });
            if (!masterSuppressed || !isSuppressionActive()) {
                const restoreToken = state.featureSuppression.token;
                if (state.featureSuppression.active || !canRestoreAgentSnapshot(restoreToken)) {
                    return;
                }
                if (masterSuppressed) {
                    try {
                        const restored = await restoreAgentSnapshot(flags, restoreToken, createAgentRestoreAttemptState(token), target.owner);
                        if (restored && canRestoreAgentSnapshot(restoreToken)) {
                            clearPersistedAgentRestoreSnapshot(token);
                            syncAgentFlagsUi();
                        }
                    } catch (restoreError) {
                        console.warn('[HomeTutorialRuntime] failed to restore agent flags after stale master suppress:', restoreError);
                    }
                }
                return;
            }
            const childSuppressed = await postAgentFlags(buildDisabledAgentChildFlags(), {
                shouldSend: isSuppressionActive,
            });
            if (!childSuppressed || !isSuppressionActive()) {
                const restoreToken = state.featureSuppression.token;
                if (state.featureSuppression.active || !canRestoreAgentSnapshot(restoreToken)) {
                    return;
                }
                try {
                    const restored = await restoreAgentSnapshot(flags, restoreToken, createAgentRestoreAttemptState(token), target.owner);
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
            const isSuppressionActive = function () {
                return isFeatureSuppressionTokenActive(token);
            };
            if (!isSuppressionActive()) {
                return false;
            }
            const masterSent = await postAgentCommand('set_agent_enabled', { enabled: false }, {
                shouldSend: isSuppressionActive,
            });
            if (!masterSent || !isSuppressionActive()) {
                return false;
            }
            const flagsSent = await postAgentFlags(buildDisabledAgentChildFlags(), {
                shouldSend: isSuppressionActive,
            });
            if (!flagsSent || !isSuppressionActive()) {
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
            void restoreAgentSnapshot(
                snapshot.agentFlags,
                restoreToken,
                createAgentRestoreAttemptState(snapshot.agentRestoreToken),
                snapshot.agentOwner
            )
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

        const attemptState = createAgentRestoreAttemptState(persisted.token, persisted);

        // 等后端放回和请求失败重试各用一份额度：等待用完不能让之后真正发请求时没有重试机会。
        let backendReplayWaits = 0;

        function scheduleRestoreAttempt(nextRetryIndex, delayMs) {
            return new Promise(function (resolve) {
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
                    void runRestoreAttempt(nextRetryIndex).then(finish);
                }, delayMs);
            });
        }

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
                const currentState = await fetchAgentFlagState();
                const currentFlags = currentState.flags;
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
                // 后端刚启动、还在按记录的意图放回开关时先不写：它的后台任务会晚于我们写入，
                // 把刚恢复的值盖掉，而快照那时已删。最多等约 45 秒（单独计数），之后仍按原来的方式恢复，
                // 免得 greeting_check 迟迟不来时一直恢复不了。
                if (currentState.intentRestorePending && backendReplayWaits < HOME_TUTORIAL_AGENT_BACKEND_REPLAY_WAIT_DELAYS_MS.length) {
                    const waitMs = HOME_TUTORIAL_AGENT_BACKEND_REPLAY_WAIT_DELAYS_MS[backendReplayWaits];
                    backendReplayWaits += 1;
                    return await scheduleRestoreAttempt(retryIndex, waitMs);
                }
                const expectsMasterOn = persisted.agentFlags.agent_enabled === true;

                if (!attemptState.masterAttempted) {
                    if (hasAnyEnabledAgentFlag(currentFlags)) {
                        clearPersistedAgentRestoreSnapshot(persisted.token);
                        return false;
                    }
                } else if (attemptState.masterApplied) {
                    if (hasUnexpectedChildFlagEnabled(currentFlags, persisted.agentFlags, { allowMasterPluginSideEffect: expectsMasterOn })) {
                        clearPersistedAgentRestoreSnapshot(persisted.token);
                        return false;
                    }
                    if (expectsMasterOn && currentFlags.agent_enabled !== true) {
                        // 本页亲手打开或亲眼看到开着、之后又关了，是用户改的。上一页留下的进度：
                        // 后端启动时所有开关都是关的，要等 greeting_check 按记录的意图放回去；
                        // 放回已做完还是关的，也是用户改的。
                        if (attemptState.masterAppliedThisPage || currentState.intentRestoreFinished) {
                            clearPersistedAgentRestoreSnapshot(persisted.token);
                            return false;
                        }
                        // 后端还没放回：不重开总开关，也不删快照，退避后再看。
                        throw new Error('agent_restore_master_pending');
                    }
                    attemptState.masterAppliedThisPage = true;
                } else {
                    if (currentFlags.agent_enabled === true) {
                        if (hasUnexpectedChildFlagEnabled(currentFlags, persisted.agentFlags, { allowMasterPluginSideEffect: expectsMasterOn })) {
                            clearPersistedAgentRestoreSnapshot(persisted.token);
                            return false;
                        }
                        attemptState.masterApplied = true;
                        attemptState.masterAppliedThisPage = true;
                        markPersistedAgentRestoreProgress(attemptState.snapshotToken, 'masterApplied');
                    } else if (hasAnyEnabledAgentFlag(currentFlags)) {
                        clearPersistedAgentRestoreSnapshot(persisted.token);
                        return false;
                    }
                }

                const flagsToRestore = attemptState.masterApplied && isPluginOffAfterMasterOn(currentFlags, persisted.agentFlags)
                    ? buildRestoreFlagsKeepingPluginState(persisted.agentFlags)
                    : persisted.agentFlags;
                const restored = await restoreAgentSnapshot(
                    flagsToRestore,
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
                    return await scheduleRestoreAttempt(
                        retryIndex + 1,
                        HOME_TUTORIAL_AGENT_RESTORE_RETRY_DELAYS_MS[retryIndex]
                    );
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
