/**
 * app-game-mode-beta.js -- frontend bridge for Game Mode Beta resource guard.
 */
(function () {
    'use strict';

    const API_STATE = '/api/game-mode-beta/state';
    const API_ENABLED = '/api/game-mode-beta/enabled';
    const API_MANUAL_RESTORE = '/api/game-mode-beta/manual-restore';
    const API_SETTINGS = '/api/game-mode-beta/settings';
    const API_REGISTER_WINDOW = '/api/game-mode-beta/windows/register';
    const API_UNREGISTER_WINDOW = '/api/game-mode-beta/windows/unregister';
    const API_ACK = '/api/game-mode-beta/ack';
    const API_DEEP_SLEEP_ACK = '/api/game-mode-beta/deep-sleep-ack';
    const API_RESET_CANDIDATE = '/api/game-mode-beta/reset-candidate';

    const clientState = {
        enabled: false,
        lastReason: null,
        autoSwitched: false,
        alreadyCatWhenTriggered: false,
        manualOverride: false,
        restoringFromDisable: false,
        promptedThisCycle: false,
        backendState: null,
        settings: {
            auto_cat_on_game: false,
            game_trigger_mode: 'smart',
        },
        hostContract: null,
        currentCycleId: null,
        cycleTriggerSource: null,
        deepSleeping: false,
        returnBallMoved: false,
        restoreAnchor: null,
        modelLoadInvalidated: false,
        registrationTimer: 0,
    };

    function t(key, fallback, params) {
        let text = fallback;
        try {
            if (typeof window.t === 'function') {
                text = window.t(key, Object.assign({ defaultValue: fallback }, params || {}));
            }
        } catch (_) {}
        if (params) {
            return String(text || fallback).replace(/\{(\w+)\}/g, function (_, name) {
                return Object.prototype.hasOwnProperty.call(params, name) ? params[name] : _;
            });
        }
        return text || fallback;
    }

    function showNotice(message) {
        if (!message) return;
        if (typeof window.showStatusToast === 'function') {
            window.showStatusToast(message, 6000, { priority: 70 });
            return;
        }
        try {
            window.alert(message);
        } catch (_) {}
    }

    function isCatFormActive() {
        try {
            return !!(
                (window.live2dManager && window.live2dManager._goodbyeClicked)
                || (window.vrmManager && window.vrmManager._goodbyeClicked)
                || (window.mmdManager && window.mmdManager._goodbyeClicked)
                || (window.pngtuberManager && window.pngtuberManager._isInReturnState)
            );
        } catch (_) {
            return false;
        }
    }

    function getActiveModelType() {
        const config = window.lanlan_config || {};
        const modelType = String(config.model_type || 'live2d').trim().toLowerCase();
        if (modelType === 'live3d') {
            const subType = String(config.live3d_sub_type || '').trim().toLowerCase();
            if (subType === 'mmd' || subType === 'vrm') return subType;
        }
        if (modelType === 'vrm' || modelType === 'mmd' || modelType === 'pngtuber') {
            return modelType;
        }
        return 'live2d';
    }

    function dispatchState() {
        try {
            window.dispatchEvent(new CustomEvent('neko:game-mode-beta-state', {
                detail: getState(),
            }));
        } catch (_) {}
        syncVisibleToggles();
    }

    function syncVisibleToggles() {
        try {
            document.querySelectorAll('input[id$="-game-mode-beta"]').forEach(function (checkbox) {
                if (!checkbox) return;
                const item = checkbox.closest('[role="switch"]');
                if (item && typeof item._nekoUpdateGameModeBetaStatus === 'function') {
                    item._nekoUpdateGameModeBetaStatus();
                }
                if (checkbox.checked === clientState.enabled) return;
                checkbox.checked = clientState.enabled;
                if (item && typeof item._nekoUpdateSettingsToggleStyle === 'function') {
                    item._nekoUpdateSettingsToggleStyle();
                }
            });
        } catch (_) {}
    }

    function applyBackendState(state) {
        if (!state || typeof state !== 'object') return;
        clientState.backendState = state;
        clientState.enabled = state.enabled === true;
        clientState.lastReason = state.trigger_reason || null;
        if (state.settings && typeof state.settings === 'object') {
            clientState.settings = normalizeSettings(state.settings);
        }
        dispatchState();
    }

    function normalizeSettings(settings) {
        const source = settings && typeof settings === 'object' ? settings : {};
        return {
            auto_cat_on_game: source.auto_cat_on_game === true,
            game_trigger_mode: source.game_trigger_mode === 'instant' ? 'instant' : 'smart',
        };
    }

    async function getMutationHeaders() {
        const headers = { 'Content-Type': 'application/json' };
        const security = window.nekoLocalMutationSecurity;
        if (!security) return headers;
        try {
            if (typeof security.peekCachedToken === 'function') {
                const token = security.peekCachedToken();
                if (token) {
                    headers['X-CSRF-Token'] = token;
                    return headers;
                }
            }
            if (typeof security.getMutationHeaders === 'function') {
                Object.assign(headers, await security.getMutationHeaders());
            }
        } catch (error) {
            console.warn('[GameModeBeta] mutation security headers unavailable:', error);
        }
        return headers;
    }

    async function refreshSettings() {
        try {
            const response = await fetch(API_SETTINGS, { cache: 'no-store' });
            if (!response.ok) return null;
            clientState.settings = normalizeSettings(await response.json());
            dispatchState();
            return Object.assign({}, clientState.settings);
        } catch (error) {
            console.warn('[GameModeBeta] settings refresh failed:', error);
            return null;
        }
    }

    async function setSettings(settings) {
        const next = normalizeSettings(Object.assign({}, clientState.settings, settings || {}));
        try {
            const response = await fetch(API_SETTINGS, {
                method: 'POST',
                headers: await getMutationHeaders(),
                body: JSON.stringify(next),
            });
            if (!response.ok) throw new Error('HTTP ' + response.status);
            clientState.settings = normalizeSettings(await response.json());
            dispatchState();
            return true;
        } catch (error) {
            console.warn('[GameModeBeta] settings update failed:', error);
            await refreshSettings();
            return false;
        }
    }

    async function postJson(url, payload, options) {
        const response = await fetch(url, {
            method: 'POST',
            headers: await getMutationHeaders(),
            body: JSON.stringify(payload || {}),
            keepalive: !!(options && options.keepalive),
        });
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return await response.json().catch(function () { return {}; });
    }

    function waitForCondition(predicate, timeoutMs) {
        const startedAt = Date.now();
        return new Promise(function (resolve) {
            function check() {
                let ready = false;
                try { ready = predicate() === true; } catch (_) {}
                if (ready) return resolve(true);
                if (Date.now() - startedAt >= timeoutMs) return resolve(false);
                setTimeout(check, 50);
            }
            check();
        });
    }

    function captureRestoreAnchor() {
        try {
            const api = window.nekoLive2DGameModeEdgePeek;
            if (api && typeof api.captureRestoreAnchor === 'function') {
                return api.captureRestoreAnchor();
            }
        } catch (_) {}
        return null;
    }

    function invalidatePendingModelLoads() {
        let invalidated = false;
        const live2d = window.live2dManager;
        if (live2d && typeof live2d.cancelActiveModelLoadForGameMode === 'function') {
            invalidated = live2d.cancelActiveModelLoadForGameMode('game-mode-protection') || invalidated;
        }
        [window.vrmManager, window.mmdManager].forEach(function (manager) {
            if (!manager) return;
            if (typeof manager.cancelActiveModelLoadForGameMode === 'function') {
                invalidated = manager.cancelActiveModelLoadForGameMode('game-mode-protection') || invalidated;
                return;
            }
            const loading = manager._isLoadingModel === true
                || manager._modelLoadState === 'loading'
                || manager._loadState === 'preparing'
                || manager._loadState === 'settling'
                || manager._modelLoadState === 'preparing';
            if (!loading) return;
            manager._activeLoadToken = (manager._activeLoadToken || 0) + 1;
            manager._nekoGameModeReloadRequired = true;
            invalidated = true;
        });
        clientState.modelLoadInvalidated = invalidated;
        return invalidated;
    }

    async function sendSwitchAck(status) {
        const host = clientState.hostContract;
        if (!host || !clientState.currentCycleId) return false;
        try {
            await postJson(API_ACK, {
                cycle_id: clientState.currentCycleId,
                pet_instance_id: host.petInstanceId,
                status: status,
            });
            return true;
        } catch (error) {
            console.warn('[GameModeBeta] switch ACK failed:', error);
            return false;
        }
    }

    async function verifyAndAckProtection() {
        const protectedReady = await waitForCondition(function () {
            if (!isCatFormActive()) return false;
            if (typeof window.isNekoGoodbyeResourceSuspended === 'function') {
                return window.isNekoGoodbyeResourceSuspended();
            }
            return true;
        }, 4500);
        await sendSwitchAck(protectedReady ? 'protected' : 'failed');
    }

    async function refreshState() {
        try {
            const response = await fetch(API_STATE);
            if (!response.ok) return null;
            const data = await response.json();
            if (data && data.success && data.state) {
                applyBackendState(data.state);
                if (!window.nekoGameModeHost) {
                    joinActiveCycle(data.state, null);
                }
                return data.state;
            }
        } catch (error) {
            console.warn('[GameModeBeta] state refresh failed:', error);
        }
        return null;
    }

    async function setEnabled(enabled) {
        const next = enabled === true;
        try {
            const response = await fetch(API_ENABLED, {
                method: 'POST',
                headers: await getMutationHeaders(),
                body: JSON.stringify({ enabled: next }),
            });
            const data = await response.json().catch(function () { return null; });
            if (!response.ok || !data || data.success !== true) {
                throw new Error((data && (data.error || data.detail)) || ('HTTP ' + response.status));
            }
            const wasEnabled = clientState.enabled;
            applyBackendState(data.state);
            if (next) {
                clientState.promptedThisCycle = false;
                showNotice(t(
                    'settings.gameModeBeta.enabledNotice',
                    '侧边模式已开启。拖动 Live2D 到屏幕边缘即可进入探身状态；资源压力只记录状态，不会自动变猫。'
                ));
            } else {
                await handleDisabledRestore();
                showNotice(t(
                    'settings.gameModeBeta.disabledNotice',
                    '侧边模式已关闭。Live2D 将恢复普通边缘吸附行为。'
                ));
            }
            if (wasEnabled !== next) dispatchState();
            return true;
        } catch (error) {
            console.warn('[GameModeBeta] toggle failed:', error);
            showNotice(t('settings.gameModeBeta.toggleFailed', '游戏模式切换失败，请稍后重试。'));
            await refreshState();
            return false;
        }
    }

    function clearModelReloadProtection() {
        [window.live2dManager, window.vrmManager, window.mmdManager].forEach(function (manager) {
            if (!manager) return;
            manager._nekoGameModeReloadRequired = false;
            manager._nekoGameModeLoadCancelReason = '';
        });
        clientState.modelLoadInvalidated = false;
    }

    async function handleDisabledRestore() {
        const shouldRestore = clientState.autoSwitched
            && !clientState.alreadyCatWhenTriggered
            && !clientState.manualOverride
            && isCatFormActive();
        clientState.restoringFromDisable = true;
        try {
            leaveDeepSleep();
            if (shouldRestore) {
                const modelReady = await ensureInvalidatedModelReloaded();
                if (modelReady) {
                    clearModelReloadProtection();
                    window.dispatchEvent(new CustomEvent('live2d-return-click', {
                        detail: {
                            source: 'game_mode_auto',
                            reason: 'game-mode-disabled',
                        },
                    }));
                } else {
                    showNotice(t(
                        'settings.gameModeBeta.restoreFailed',
                        '模型恢复失败，仍保持猫猫形态。请再次点击猫猫重试。'
                    ));
                }
            } else {
                clearModelReloadProtection();
            }
        } finally {
            clientState.restoringFromDisable = false;
            clientState.autoSwitched = false;
            clientState.alreadyCatWhenTriggered = false;
            clientState.manualOverride = false;
            clientState.promptedThisCycle = false;
            clientState.currentCycleId = null;
            clientState.restoreAnchor = null;
            clientState.returnBallMoved = false;
        }
    }

    function metricLabel(metric) {
        if (metric === 'cpu') return 'CPU';
        if (metric === 'memory') return t('settings.gameModeBeta.memoryMetric', '内存');
        if (metric === 'gpu') return 'GPU';
        return metric || t('settings.gameModeBeta.resourceMetric', '资源');
    }

    function formatPercent(value) {
        if (typeof value !== 'number' || !Number.isFinite(value)) return '';
        return Math.round(value) + '%';
    }

    function formatDuration(value) {
        if (typeof value !== 'number' || !Number.isFinite(value)) return '';
        return Math.round(value) + 's';
    }

    function getStatusText() {
        const status = clientState.enabled
            ? t('settings.gameModeBeta.statusOn', '开启')
            : t('settings.gameModeBeta.statusOff', '关闭');
        const reason = clientState.lastReason;
        if (reason && reason.metric) {
            return t(
                'settings.gameModeBeta.statusWithReason',
                '{status} · 最近：{metric} {percent} / {duration}',
                {
                    status: status,
                    metric: metricLabel(reason.metric),
                    percent: formatPercent(reason.percent) || '-',
                    duration: formatDuration(reason.duration_seconds) || '-',
                }
            );
        }
        return t('settings.gameModeBeta.statusOnly', '{status}', { status: status });
    }

    function handleAutoSwitchEvent(payload) {
        if (!payload || payload.source !== 'game_mode_auto') return;
        if (payload.cycle_id && clientState.currentCycleId === payload.cycle_id) return;
        clientState.lastReason = {
            metric: payload.reason || '',
            percent: payload.percent,
            duration_seconds: payload.duration_seconds,
        };
        clientState.currentCycleId = payload.cycle_id || null;
        clientState.cycleTriggerSource = payload.trigger_source || null;
        clientState.returnBallMoved = false;

        if (getActiveModelType() === 'live2d') {
            clientState.alreadyCatWhenTriggered = isCatFormActive();
            clientState.autoSwitched = false;
            clientState.manualOverride = false;
            clientState.restoreAnchor = null;
            const host = clientState.hostContract;
            if (host && clientState.currentCycleId) {
                void sendSwitchAck('already_protected');
            } else {
                void postJson(API_MANUAL_RESTORE, {}).catch(function (error) {
                    console.warn('[GameModeBeta] failed to release ignored Live2D cycle:', error);
                });
            }
            dispatchState();
            return;
        }

        clientState.restoreAnchor = captureRestoreAnchor();
        invalidatePendingModelLoads();

        if (isCatFormActive()) {
            clientState.alreadyCatWhenTriggered = true;
            clientState.autoSwitched = false;
            void sendSwitchAck('already_protected');
        } else {
            clientState.alreadyCatWhenTriggered = false;
            clientState.autoSwitched = true;
            clientState.manualOverride = false;
            try {
                if (window.nekoLive2DGameModeEdgePeek &&
                    typeof window.nekoLive2DGameModeEdgePeek.clear === 'function') {
                    window.nekoLive2DGameModeEdgePeek.clear('game-mode-auto');
                }
            } catch (_) {}
            try {
                window.dispatchEvent(new CustomEvent('live2d-goodbye-click', {
                    detail: {
                        autoGoodbye: true,
                        gameModeAuto: true,
                        source: 'game_mode_auto',
                        reason: payload.trigger_source || 'game-mode-pressure',
                        cycleId: clientState.currentCycleId,
                        edgeAnchor: clientState.restoreAnchor,
                    },
                }));
            } catch (_) {}
            if (clientState.hostContract && clientState.currentCycleId) {
                void verifyAndAckProtection();
            }
        }

        if (!clientState.hostContract && !clientState.promptedThisCycle) {
            clientState.promptedThisCycle = true;
            showNotice(t(
                'settings.gameModeBeta.autoSwitchNotice',
                '检测到 {metric} 占用持续较高，已切换到猫猫形态以降低资源占用。',
                { metric: metricLabel(payload.reason) }
            ));
        }
        dispatchState();
    }

    function leaveDeepSleep() {
        if (!clientState.deepSleeping) return;
        clientState.deepSleeping = false;
        try { document.body && document.body.classList.remove('neko-game-mode-deep-sleep'); } catch (_) {}
        try {
            if (window.nekoActivitySignalClient && typeof window.nekoActivitySignalClient.start === 'function') {
                window.nekoActivitySignalClient.start();
            }
        } catch (_) {}
    }

    async function ensureInvalidatedModelReloaded() {
        if (!clientState.modelLoadInvalidated) return true;
        const managers = [window.live2dManager, window.vrmManager, window.mmdManager];
        managers.forEach(function (manager) {
            if (!manager) return;
            manager._nekoGameModeReloadRequired = false;
            manager._nekoGameModeLoadCancelReason = '';
        });
        const loadsSettled = await waitForCondition(function () {
            return managers.every(function (manager) {
                return !manager || manager._isLoadingModel !== true;
            });
        }, 3000);
        if (!loadsSettled) return false;
        try {
            if (typeof window.showCurrentModel === 'function') {
                const restored = await window.showCurrentModel();
                if (restored === false) return false;
            }
            clientState.modelLoadInvalidated = false;
            return true;
        } catch (error) {
            console.warn('[GameModeBeta] model reload after protection failed:', error);
            return false;
        }
    }

    async function restoreSavedEdgeAnchor() {
        if (!clientState.restoreAnchor || clientState.returnBallMoved) return true;
        try {
            const api = window.nekoLive2DGameModeEdgePeek;
            if (!api || typeof api.restoreAnchor !== 'function') return false;
            return await api.restoreAnchor(clientState.restoreAnchor);
        } catch (error) {
            console.warn('[GameModeBeta] edge anchor restore failed:', error);
            return false;
        }
    }

    function keepProtectedAfterRestoreFailure() {
        clientState.manualOverride = false;
        clientState.autoSwitched = true;
        try {
            window.dispatchEvent(new CustomEvent('live2d-goodbye-click', {
                detail: { gameModeAuto: true, source: 'game_mode_auto', reason: 'restore-failed' },
            }));
        } catch (_) {}
        showNotice(t(
            'settings.gameModeBeta.restoreFailed',
            '模型恢复失败，仍保持猫猫形态。请再次点击猫猫重试。'
        ));
        dispatchState();
    }

    async function notifyManualRestore() {
        if (!clientState.enabled || clientState.restoringFromDisable) return;
        if (!clientState.autoSwitched) return;
        const returnCompleted = await waitForCondition(function () {
            return !isCatFormActive();
        }, 4500);
        if (!returnCompleted) return;
        clientState.manualOverride = true;
        leaveDeepSleep();
        const modelReady = await ensureInvalidatedModelReloaded();
        if (!modelReady) {
            keepProtectedAfterRestoreFailure();
            return;
        }
        await restoreSavedEdgeAnchor();
        try {
            const payload = clientState.hostContract
                ? { pet_instance_id: clientState.hostContract.petInstanceId }
                : {};
            const response = await fetch(API_MANUAL_RESTORE, {
                method: 'POST',
                headers: await getMutationHeaders(),
                body: JSON.stringify(payload),
            });
            const data = await response.json().catch(function () { return null; });
            if (!response.ok || !data || data.success !== true) {
                throw new Error((data && (data.error || data.detail)) || ('HTTP ' + response.status));
            }
            if (data.state) {
                applyBackendState(data.state);
            }
            clientState.autoSwitched = false;
            clientState.currentCycleId = null;
            clientState.restoreAnchor = null;
            dispatchState();
        } catch (error) {
            console.warn('[GameModeBeta] manual restore notification failed:', error);
            keepProtectedAfterRestoreFailure();
        }
    }

    async function enterDeepSleep(payload) {
        if (!payload || payload.cycle_id !== clientState.currentCycleId) return;
        if (!clientState.autoSwitched || !isCatFormActive()) return;
        const targetIds = Array.isArray(payload.pet_instance_ids) ? payload.pet_instance_ids : [];
        const host = clientState.hostContract;
        if (targetIds.length && host && !targetIds.includes(host.petInstanceId)) return;
        let success = true;
        const runIdempotentStep = function (label, step) {
            for (let attempt = 0; attempt < 2; attempt += 1) {
                try {
                    step();
                    return true;
                } catch (error) {
                    if (attempt === 1) {
                        console.warn('[GameModeBeta] deep sleep step failed:', label, error);
                    }
                }
            }
            return false;
        };
        clientState.deepSleeping = true;
        success = runIdempotentStep('window-throttle-class', function () {
            document.body && document.body.classList.add('neko-game-mode-deep-sleep');
        }) && success;
        if (window.nekoAutoGoodbye && typeof window.nekoAutoGoodbye.setVisualTier === 'function') {
            success = runIdempotentStep('visual-tier', function () {
                window.nekoAutoGoodbye.setVisualTier('cat3', {
                    source: 'game-mode-deep-sleep',
                    reason: 'stable-90-seconds',
                });
            }) && success;
        }
        [window.live2dManager, window.vrmManager, window.mmdManager].forEach(function (manager, index) {
            if (!manager || typeof manager.pauseRendering !== 'function') return;
            success = runIdempotentStep('pause-rendering-' + index, function () {
                manager.pauseRendering();
            }) && success;
        });
        if (window.nekoActivitySignalClient && typeof window.nekoActivitySignalClient.stop === 'function') {
            success = runIdempotentStep('activity-signal-stop', function () {
                window.nekoActivitySignalClient.stop();
            }) && success;
        }
        if (host) {
            try {
                await postJson(API_DEEP_SLEEP_ACK, {
                    cycle_id: clientState.currentCycleId,
                    pet_instance_id: host.petInstanceId,
                    success: success,
                });
            } catch (error) {
                console.warn('[GameModeBeta] deep sleep ACK failed:', error);
            }
        }
        dispatchState();
    }

    async function restoreAfterBackendLifecycle(reason) {
        if (!clientState.autoSwitched || !isCatFormActive()) return true;
        clientState.restoringFromDisable = true;
        try {
            leaveDeepSleep();
            const modelReady = await ensureInvalidatedModelReloaded();
            if (!modelReady) {
                keepProtectedAfterRestoreFailure();
                return false;
            }
            clearModelReloadProtection();
            window.dispatchEvent(new CustomEvent('live2d-return-click', {
                detail: { source: 'game_mode_auto', reason: reason },
            }));
            clientState.autoSwitched = false;
            clientState.currentCycleId = null;
            return true;
        } finally {
            clientState.restoringFromDisable = false;
        }
    }

    async function handleLifecycleMessage(payload) {
        if (!payload || payload.source !== 'game_mode_auto') return;
        if (payload.type === 'game_mode_switch_confirmed') {
            if (payload.cycle_id !== clientState.currentCycleId) return;
            if (!clientState.promptedThisCycle) {
                clientState.promptedThisCycle = true;
                showNotice(t(
                    'settings.gameModeBeta.autoSwitchNotice',
                    '游戏保护已生效，NEKO 已切换到猫猫形态。'
                ));
            }
        } else if (payload.type === 'game_mode_switch_failed') {
            if (payload.cycle_id !== clientState.currentCycleId) return;
            let restored = true;
            if (clientState.autoSwitched && isCatFormActive()) {
                restored = await restoreAfterBackendLifecycle('game-mode-switch-failed');
            }
            if (restored) {
                clientState.autoSwitched = false;
                clientState.currentCycleId = null;
            }
            showNotice(t(
                'settings.gameModeBeta.switchFailed',
                '切换到猫猫形态失败，稍后会重新完整确认。'
            ));
        } else if (payload.type === 'game_mode_deep_sleep') {
            void enterDeepSleep(payload);
        } else if (payload.type === 'game_mode_restore') {
            const targetIds = Array.isArray(payload.pet_instance_ids) ? payload.pet_instance_ids : [];
            const host = clientState.hostContract;
            if (targetIds.length && host && !targetIds.includes(host.petInstanceId)) return;
            if (clientState.autoSwitched && isCatFormActive()) {
                await restoreAfterBackendLifecycle(payload.reason || 'game-mode-restore');
            }
        } else if (payload.type === 'game_mode_semantic_signal_unavailable') {
            showNotice(t(
                'settings.gameModeBeta.signalUnavailable',
                '暂时无法获得可靠的游戏识别信号；资源压力保护仍然有效。'
            ));
        }
        dispatchState();
    }

    async function resetCandidate(reason) {
        try {
            await postJson(API_RESET_CANDIDATE, { reason: reason || 'frontend-reset' });
        } catch (_) {}
    }

    function joinActiveCycle(registration, petInstanceId) {
        const phase = registration && registration.cycle_phase;
        const active = registration && (
            registration.cycle_active === true
            || phase === 'switching'
            || phase === 'protected'
            || phase === 'deep_sleep'
        );
        if (!active || !registration.cycle_id || registration.join_as_cat === false) return;
        handleAutoSwitchEvent({
            type: 'game_mode_auto_switch',
            source: 'game_mode_auto',
            cycle_id: registration.cycle_id,
            trigger_source: 'join-active-cycle',
            reason: 'active_cycle',
            duration_seconds: 0,
        });
        if (phase === 'deep_sleep') {
            void waitForCondition(isCatFormActive, 4500).then(function (ready) {
                if (!ready) return;
                return enterDeepSleep({
                    type: 'game_mode_deep_sleep',
                    source: 'game_mode_auto',
                    cycle_id: registration.cycle_id,
                    pet_instance_ids: petInstanceId ? [petInstanceId] : [],
                });
            });
        }
    }

    async function registerHostWindow() {
        if (!window.nekoGameModeHost || typeof window.nekoGameModeHost.getContract !== 'function') return null;
        try {
            const contract = await window.nekoGameModeHost.getContract();
            if (!contract || contract.windowType !== 'pet' || !contract.petInstanceId) return null;
            clientState.hostContract = contract;
            const registration = await postJson(API_REGISTER_WINDOW, {
                pet_instance_id: contract.petInstanceId,
                window_type: contract.windowType,
                signal_capabilities: contract.signalCapabilities || {},
            });
            joinActiveCycle(registration, contract.petInstanceId);
            return contract;
        } catch (error) {
            console.warn('[GameModeBeta] host registration failed:', error);
            return null;
        }
    }

    function startHostRegistration() {
        if (!window.nekoGameModeHost) return;
        void registerHostWindow();
        if (clientState.registrationTimer) clearInterval(clientState.registrationTimer);
        clientState.registrationTimer = setInterval(registerHostWindow, 10000);
        if (typeof window.nekoGameModeHost.onSystemResume === 'function') {
            window.nekoGameModeHost.onSystemResume(function () {
                void resetCandidate('system-resume');
            });
        }
    }

    function bindEvents() {
        window.addEventListener('neko:game-mode-beta-auto-switch', function (event) {
            handleAutoSwitchEvent(event && event.detail);
        });
        window.addEventListener('neko:game-mode-beta-message', function (event) {
            void handleLifecycleMessage(event && event.detail);
        });
        window.addEventListener('live2d-goodbye-click', function (event) {
            const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            if (clientState.enabled && detail.source !== 'game_mode_auto' && detail.gameModeAuto !== true) {
                clientState.manualOverride = true;
            }
        });
        ['live2d-return-click', 'vrm-return-click', 'mmd-return-click', 'pngtuber-return-click'].forEach(function (eventName) {
            window.addEventListener(eventName, function () {
                Promise.resolve().then(function () { return notifyManualRestore(); });
            });
        });
        window.addEventListener('neko:return-ball-manual-move', function (event) {
            const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            if (detail.reason === 'return-ball-drag-end' && detail.dragCancelled !== true) {
                clientState.returnBallMoved = true;
            }
        });
        window.addEventListener('neko:character-switch-start', function () {
            void resetCandidate('character-switch');
        });
        window.addEventListener('neko:websocket-connection-state', function (event) {
            const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            if (detail.connected === false) void resetCandidate('connection-interrupted');
            if (detail.connected === true) void registerHostWindow();
        });
        window.addEventListener('beforeunload', function () {
            const host = clientState.hostContract;
            if (!host) return;
            void postJson(API_UNREGISTER_WINDOW, {
                pet_instance_id: host.petInstanceId,
            }, { keepalive: true }).catch(function () {});
        });
        window.addEventListener('DOMContentLoaded', function () {
            refreshState();
            startHostRegistration();
        }, { once: true });
    }

    function getState() {
        return {
            enabled: clientState.enabled,
            lastReason: clientState.lastReason,
            autoSwitched: clientState.autoSwitched,
            alreadyCatWhenTriggered: clientState.alreadyCatWhenTriggered,
            manualOverride: clientState.manualOverride,
            settings: Object.assign({}, clientState.settings),
            currentCycleId: clientState.currentCycleId,
            deepSleeping: clientState.deepSleeping,
            returnBallMoved: clientState.returnBallMoved,
            hostContract: clientState.hostContract,
            backendState: clientState.backendState,
        };
    }

    window.nekoGameModeBeta = {
        refreshState: refreshState,
        setEnabled: setEnabled,
        isEnabled: function () { return clientState.enabled === true; },
        handleAutoSwitchEvent: handleAutoSwitchEvent,
        getStatusText: getStatusText,
        refreshSettings: refreshSettings,
        setSettings: setSettings,
        getSettings: function () { return Object.assign({}, clientState.settings); },
        registerHostWindow: registerHostWindow,
        handleLifecycleMessage: handleLifecycleMessage,
        getState: getState,
    };

    bindEvents();
    if (document.readyState !== 'loading') {
        refreshState();
        startHostRegistration();
    }
})();
