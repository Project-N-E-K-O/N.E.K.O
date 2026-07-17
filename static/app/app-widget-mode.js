/**
 * app-widget-mode.js -- browser lifecycle client for Widget Mode.
 */
(function () {
    'use strict';

    const PROTOCOL_VERSION = 1;
    const SOURCE = 'widget_mode_activity_compaction';
    const API_STATE = '/api/widget-mode/state';
    const API_ENABLED = '/api/widget-mode/enabled';
    const API_SETTINGS = '/api/widget-mode/settings';
    const API_USER_RESTORE = '/api/widget-mode/user-restore';
    const API_REGISTER_WINDOW = '/api/widget-mode/windows/register';
    const API_UNREGISTER_WINDOW = '/api/widget-mode/windows/unregister';
    const API_COMPACTION_ACK = '/api/widget-mode/compaction/ack';
    const API_RENDERER_ACK = '/api/widget-mode/renderer-suspension/ack';
    const API_ACTIVITY_RESET = '/api/widget-mode/activity/reset';
    const ACTIVITY_POLICIES = new Set(['disabled', 'observe_only', 'compact_on_confirm']);

    const clientState = {
        enabled: false,
        backendState: null,
        settings: { activity_response: 'disabled' },
        hostContract: null,
        hostCompatible: false,
        currentCycleId: null,
        compactedByCycle: false,
        alreadyCompacted: false,
        rendererSuspended: false,
        suspendedManagers: [],
        restoring: false,
        returnBallMoved: false,
        restoreAnchor: null,
        modelLoadInvalidated: false,
        compactLeaseActive: false,
        compactLeaseSuspended: false,
        compactLeaseOperation: Promise.resolve(),
        seenCycleIds: new Set(),
        registrationTimer: 0,
    };

    function t(key, fallback, params) {
        let text = fallback;
        try {
            if (typeof window.t === 'function') {
                text = window.t(key, Object.assign({ defaultValue: fallback }, params || {}));
            }
        } catch (_) {}
        if (!params) return text || fallback;
        return String(text || fallback).replace(/\{(\w+)\}/g, function (_, name) {
            return Object.prototype.hasOwnProperty.call(params, name) ? params[name] : _;
        });
    }

    function showNotice(message) {
        if (!message) return;
        if (typeof window.showStatusToast === 'function') {
            window.showStatusToast(message, 6000, { priority: 70 });
            return;
        }
        try { window.alert(message); } catch (_) {}
    }

    function normalizeSettings(settings) {
        const source = settings && typeof settings === 'object' ? settings : {};
        const policy = ACTIVITY_POLICIES.has(source.activity_response)
            ? source.activity_response
            : 'disabled';
        return { activity_response: policy };
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

    function getActiveRendererManager() {
        const type = getActiveModelType();
        if (type === 'vrm') return window.vrmManager || null;
        if (type === 'mmd') return window.mmdManager || null;
        if (type === 'pngtuber') return window.pngtuberManager || null;
        return window.live2dManager || null;
    }

    function getState() {
        return {
            enabled: clientState.enabled,
            settings: Object.assign({}, clientState.settings),
            currentCycleId: clientState.currentCycleId,
            compactedByCycle: clientState.compactedByCycle,
            alreadyCompacted: clientState.alreadyCompacted,
            rendererSuspended: clientState.rendererSuspended,
            returnBallMoved: clientState.returnBallMoved,
            compactLeaseActive: clientState.compactLeaseActive,
            compactLeaseSuspended: clientState.compactLeaseSuspended,
            hostCompatible: clientState.hostCompatible,
            hostContract: clientState.hostContract,
            backendState: clientState.backendState,
        };
    }

    function dispatchState() {
        try {
            window.dispatchEvent(new CustomEvent('neko:widget-mode-state-changed', {
                detail: getState(),
            }));
        } catch (_) {}
        try {
            document.querySelectorAll('input[id$="-widget-mode"]').forEach(function (checkbox) {
                const item = checkbox && checkbox.closest('[role="switch"]');
                if (item && typeof item._nekoUpdateWidgetModeStatus === 'function') {
                    item._nekoUpdateWidgetModeStatus();
                }
                if (!checkbox || checkbox.checked === clientState.enabled) return;
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
        if (state.settings && typeof state.settings === 'object') {
            clientState.settings = normalizeSettings(state.settings);
        }
        dispatchState();
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
            console.warn('[WidgetMode] mutation headers unavailable:', error);
        }
        return headers;
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

    function isCompatibleHostContract(contract) {
        return !!(
            contract
            && contract.windowType === 'pet'
            && contract.petInstanceId
            && contract.widgetModeProtocolVersion === PROTOCOL_VERSION
            && contract.signalCapabilities
            && typeof contract.signalCapabilities === 'object'
            && contract.widgetModeCompactionLeaseV1 === true
        );
    }

    function compactLeasePayload(visibleScreenBounds) {
        const host = clientState.hostContract;
        if (!host || !clientState.currentCycleId) return null;
        const payload = {
            sessionId: clientState.currentCycleId,
            petInstanceId: host.petInstanceId,
        };
        if (visibleScreenBounds) payload.visibleScreenBounds = visibleScreenBounds;
        return payload;
    }

    function captureVisibleReturnBallScreenBounds(container) {
        let target = container || null;
        try {
            if (!target) {
                target = document.querySelector(
                    '[id$="-return-button-container"][data-neko-return-visible="true"]'
                );
            }
            if (!target || typeof target.getBoundingClientRect !== 'function') return null;
            const rect = target.getBoundingClientRect();
            if (!rect || rect.width <= 0 || rect.height <= 0) return null;
            const screenLeft = Number.isFinite(window.screenX) ? window.screenX : 0;
            const screenTop = Number.isFinite(window.screenY) ? window.screenY : 0;
            return {
                x: Math.round(screenLeft + rect.left),
                y: Math.round(screenTop + rect.top),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
            };
        } catch (_) {
            return null;
        }
    }

    function waitForVisibleReturnBallScreenBounds(container, timeoutMs) {
        const startedAt = Date.now();
        return new Promise(function (resolve) {
            function check() {
                const bounds = captureVisibleReturnBallScreenBounds(container);
                if (bounds) return resolve(bounds);
                if (Date.now() - startedAt >= timeoutMs) return resolve(null);
                setTimeout(check, 50);
            }
            check();
        });
    }

    function queueCompactLeaseOperation(operation) {
        const run = function () { return operation(); };
        clientState.compactLeaseOperation = clientState.compactLeaseOperation.then(run, run);
        return clientState.compactLeaseOperation;
    }

    async function acquireCompactWindowLease(cycleId) {
        const bridge = window.nekoWidgetModeHost;
        if (
            !clientState.hostCompatible
            || !bridge
            || typeof bridge.acquireCompactLease !== 'function'
        ) return false;
        const visibleScreenBounds = await waitForVisibleReturnBallScreenBounds(null, 2000);
        if (!visibleScreenBounds || cycleId !== clientState.currentCycleId) return false;
        const payload = compactLeasePayload(visibleScreenBounds);
        if (!payload) return false;
        try {
            const result = await queueCompactLeaseOperation(function () {
                return bridge.acquireCompactLease(payload);
            });
            if (!result || result.ok !== true) return false;
            if (cycleId !== clientState.currentCycleId) {
                try {
                    await queueCompactLeaseOperation(function () {
                        return bridge.releaseCompactLease(payload);
                    });
                } catch (releaseError) {
                    console.warn('[WidgetMode] obsolete compact window lease release failed:', releaseError);
                }
                return false;
            }
            clientState.compactLeaseActive = true;
            clientState.compactLeaseSuspended = false;
            return true;
        } catch (error) {
            console.warn('[WidgetMode] compact window lease acquire failed:', error);
            return false;
        }
    }

    async function releaseCompactWindowLease() {
        if (!clientState.compactLeaseActive) return true;
        const bridge = window.nekoWidgetModeHost;
        const payload = compactLeasePayload();
        if (!bridge || typeof bridge.releaseCompactLease !== 'function' || !payload) return false;
        try {
            const result = await queueCompactLeaseOperation(function () {
                return bridge.releaseCompactLease(payload);
            });
            if (!result || result.ok !== true) return false;
            clientState.compactLeaseActive = false;
            clientState.compactLeaseSuspended = false;
            return true;
        } catch (error) {
            console.warn('[WidgetMode] compact window lease release failed:', error);
            return false;
        }
    }

    async function suspendCompactWindowLeaseForDrag() {
        if (!clientState.compactLeaseActive || clientState.compactLeaseSuspended) return;
        const bridge = window.nekoWidgetModeHost;
        const payload = compactLeasePayload();
        if (!bridge || typeof bridge.suspendCompactLeaseForDrag !== 'function' || !payload) return;
        try {
            const result = await queueCompactLeaseOperation(function () {
                return bridge.suspendCompactLeaseForDrag(payload);
            });
            clientState.compactLeaseSuspended = !!(result && result.ok === true);
            dispatchState();
        } catch (error) {
            console.warn('[WidgetMode] compact window lease drag suspend failed:', error);
        }
    }

    async function resumeCompactWindowLeaseAfterDrag(container) {
        if (!clientState.compactLeaseActive || !clientState.compactLeaseSuspended) return;
        const bridge = window.nekoWidgetModeHost;
        if (!bridge || typeof bridge.resumeCompactLeaseAfterDrag !== 'function') return;
        const visibleScreenBounds = await waitForVisibleReturnBallScreenBounds(container, 2000);
        const payload = compactLeasePayload(visibleScreenBounds);
        if (!payload || !visibleScreenBounds) return;
        try {
            const result = await queueCompactLeaseOperation(function () {
                return bridge.resumeCompactLeaseAfterDrag(payload);
            });
            if (result && result.ok === true) clientState.compactLeaseSuspended = false;
            dispatchState();
        } catch (error) {
            console.warn('[WidgetMode] compact window lease drag resume failed:', error);
        }
    }

    function captureRestoreAnchor() {
        try {
            const api = window.nekoLive2DPeek;
            if (api && typeof api.captureRestoreAnchor === 'function') {
                return api.captureRestoreAnchor();
            }
        } catch (_) {}
        return null;
    }

    function invalidatePendingModelLoads() {
        let invalidated = false;
        [window.live2dManager, window.vrmManager, window.mmdManager].forEach(function (manager) {
            if (!manager || typeof manager.cancelActiveModelLoadForWidgetMode !== 'function') return;
            invalidated = manager.cancelActiveModelLoadForWidgetMode('widget-mode-compaction') || invalidated;
        });
        clientState.modelLoadInvalidated = invalidated;
        return invalidated;
    }

    function clearModelReloadState() {
        [window.live2dManager, window.vrmManager, window.mmdManager].forEach(function (manager) {
            if (!manager) return;
            manager._nekoWidgetModeReloadRequired = false;
            manager._nekoWidgetModeLoadCancelReason = '';
        });
        clientState.modelLoadInvalidated = false;
    }

    async function ensureInvalidatedModelReloaded() {
        if (!clientState.modelLoadInvalidated) return true;
        const managers = [window.live2dManager, window.vrmManager, window.mmdManager];
        managers.forEach(function (manager) {
            if (!manager) return;
            manager._nekoWidgetModeReloadRequired = false;
            manager._nekoWidgetModeLoadCancelReason = '';
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
            console.warn('[WidgetMode] model reload after compaction failed:', error);
            return false;
        }
    }

    function keepCompactedAfterRestoreFailure() {
        try {
            window.dispatchEvent(new CustomEvent('live2d-goodbye-click', {
                detail: {
                    autoGoodbye: true,
                    widgetModeCompaction: true,
                    source: SOURCE,
                    reason: 'restore-failed',
                    compactionCycleId: clientState.currentCycleId,
                },
            }));
        } catch (_) {}
        showNotice(t(
            'settings.widgetMode.restoreFailed',
            '模型恢复失败，当前保持猫形态，请点击重试。'
        ));
        dispatchState();
    }

    async function sendCompactionAck(status) {
        const host = clientState.hostContract;
        if (!host || !clientState.currentCycleId) return false;
        try {
            await postJson(API_COMPACTION_ACK, {
                compaction_cycle_id: clientState.currentCycleId,
                pet_instance_id: host.petInstanceId,
                status: status,
            });
            return true;
        } catch (error) {
            console.warn('[WidgetMode] compaction ACK failed:', error);
            return false;
        }
    }

    async function sendRendererSuspensionAck(success) {
        const host = clientState.hostContract;
        if (!host || !clientState.currentCycleId) return false;
        try {
            await postJson(API_RENDERER_ACK, {
                compaction_cycle_id: clientState.currentCycleId,
                pet_instance_id: host.petInstanceId,
                success: success === true,
            });
            return true;
        } catch (error) {
            console.warn('[WidgetMode] renderer suspension ACK failed:', error);
            return false;
        }
    }

    function resetLocalCycle() {
        clearModelReloadState();
        clientState.currentCycleId = null;
        clientState.compactedByCycle = false;
        clientState.alreadyCompacted = false;
        clientState.returnBallMoved = false;
        clientState.restoreAnchor = null;
        clientState.compactLeaseActive = false;
        clientState.compactLeaseSuspended = false;
    }

    function resumeSuspendedRenderers() {
        const managers = clientState.suspendedManagers.splice(0);
        clientState.rendererSuspended = false;
        managers.forEach(function (manager) {
            if (!manager || typeof manager.resumeRendering !== 'function') return;
            try { manager.resumeRendering(); } catch (error) {
                console.warn('[WidgetMode] renderer resume failed:', error);
            }
        });
        dispatchState();
    }

    async function restoreLocalCycle(reason) {
        if (!clientState.currentCycleId) return true;
        if (clientState.restoring) return false;
        clientState.restoring = true;
        try {
            const leaseReleased = await releaseCompactWindowLease();
            if (!leaseReleased) {
                console.warn('[WidgetMode] restore blocked because compact window lease is still active');
                return false;
            }
            resumeSuspendedRenderers();
            if (clientState.compactedByCycle && isCatFormActive()) {
                const modelReady = await ensureInvalidatedModelReloaded();
                if (!modelReady) {
                    keepCompactedAfterRestoreFailure();
                    return false;
                }
                window.dispatchEvent(new CustomEvent('live2d-return-click', {
                    detail: {
                        source: SOURCE,
                        reason: reason || 'widget-mode-restore',
                        widgetModeRestore: true,
                    },
                }));
                const restored = await waitForCondition(function () {
                    return !isCatFormActive();
                }, 4500);
                if (!restored) {
                    showNotice(t(
                        'settings.widgetMode.restoreFailed',
                        '模型恢复失败，当前保持猫形态，请点击重试。'
                    ));
                    return false;
                }
            }
            resetLocalCycle();
            dispatchState();
            return true;
        } finally {
            clientState.restoring = false;
        }
    }

    async function verifyAndAcknowledgeCompaction(cycleId) {
        const ready = await waitForCondition(isCatFormActive, 4500);
        if (cycleId !== clientState.currentCycleId) return;
        if (!ready) {
            clientState.compactedByCycle = false;
            await sendCompactionAck('failed');
            return;
        }
        const leaseAcquired = await acquireCompactWindowLease(cycleId);
        if (!leaseAcquired) {
            clientState.compactedByCycle = true;
            await sendCompactionAck('failed');
            await restoreLocalCycle('compact-window-lease-failed');
            return;
        }
        clientState.compactedByCycle = true;
        await sendCompactionAck('compacted');
        dispatchState();
    }

    async function handleCompactionRequested(payload) {
        if (!payload || payload.source !== SOURCE || !payload.compaction_cycle_id) return;
        const cycleId = payload.compaction_cycle_id;
        if (clientState.seenCycleIds.has(cycleId)) return;
        if (!clientState.hostCompatible) {
            clientState.currentCycleId = cycleId;
            await sendCompactionAck('failed');
            resetLocalCycle();
            return;
        }
        clientState.seenCycleIds.add(cycleId);
        if (clientState.seenCycleIds.size > 64) {
            clientState.seenCycleIds.delete(clientState.seenCycleIds.values().next().value);
        }
        clientState.currentCycleId = cycleId;
        clientState.returnBallMoved = false;
        clientState.restoreAnchor = captureRestoreAnchor();
        clientState.compactedByCycle = false;
        clientState.alreadyCompacted = false;

        if (getActiveModelType() === 'live2d' || isCatFormActive()) {
            clientState.alreadyCompacted = true;
            await sendCompactionAck('already_compacted');
            dispatchState();
            return;
        }

        invalidatePendingModelLoads();
        try {
            window.dispatchEvent(new CustomEvent('live2d-goodbye-click', {
                detail: {
                    autoGoodbye: true,
                    widgetModeCompaction: true,
                    source: SOURCE,
                    reason: payload.reason || 'activity-confirmed',
                    compactionCycleId: cycleId,
                    edgeAnchor: clientState.restoreAnchor,
                },
            }));
        } catch (_) {}
        await verifyAndAcknowledgeCompaction(cycleId);
    }

    async function handleRendererSuspensionRequested(payload) {
        if (
            !payload
            || payload.source !== SOURCE
            || payload.compaction_cycle_id !== clientState.currentCycleId
            || !clientState.compactedByCycle
        ) return;
        const host = clientState.hostContract;
        const targets = Array.isArray(payload.pet_instance_ids) ? payload.pet_instance_ids : [];
        if (targets.length && host && !targets.includes(host.petInstanceId)) return;
        const manager = getActiveRendererManager();
        let success = false;
        if (manager && typeof manager.pauseRendering === 'function') {
            try {
                manager.pauseRendering();
                clientState.suspendedManagers = [manager];
                clientState.rendererSuspended = true;
                success = true;
            } catch (error) {
                console.warn('[WidgetMode] renderer suspension failed:', error);
            }
        }
        await sendRendererSuspensionAck(success);
        dispatchState();
    }

    async function notifyUserRestore() {
        if (
            clientState.restoring
            || !clientState.enabled
            || !clientState.compactedByCycle
            || !clientState.currentCycleId
        ) return;
        const restored = await waitForCondition(function () { return !isCatFormActive(); }, 4500);
        if (!restored) return;
        resumeSuspendedRenderers();
        const modelReady = await ensureInvalidatedModelReloaded();
        if (!modelReady) {
            keepCompactedAfterRestoreFailure();
            return;
        }
        const host = clientState.hostContract;
        try {
            const data = await postJson(API_USER_RESTORE, host
                ? { pet_instance_id: host.petInstanceId }
                : {});
            if (data && data.state) applyBackendState(data.state);
            resetLocalCycle();
            dispatchState();
        } catch (error) {
            console.warn('[WidgetMode] user restore notification failed:', error);
        }
    }

    async function handleLifecycleMessage(payload) {
        if (!payload || payload.source !== SOURCE) return;
        if (payload.type === 'widget_mode_compaction_requested') {
            await handleCompactionRequested(payload);
            return;
        }
        if (payload.type === 'widget_mode_activity_signal_unavailable') {
            showNotice(t(
                'settings.widgetMode.signalUnavailable',
                '活动信号暂时不可用；当前候选不会被误判为结束。'
            ));
            return;
        }
        if (payload.compaction_cycle_id !== clientState.currentCycleId) return;
        if (payload.type === 'widget_mode_compaction_confirmed') {
            showNotice(t(
                'settings.widgetMode.compactionConfirmed',
                '挂边模式 Beta 已将当前模型收缩为猫形态。'
            ));
        } else if (payload.type === 'widget_mode_compaction_failed') {
            await restoreLocalCycle('compaction-failed');
            showNotice(t(
                'settings.widgetMode.compactionFailed',
                '模型收缩未完成，已恢复当前模型。'
            ));
        } else if (payload.type === 'widget_mode_renderer_suspension_requested') {
            await handleRendererSuspensionRequested(payload);
        } else if (payload.type === 'widget_mode_compaction_restore_requested') {
            const host = clientState.hostContract;
            const targets = Array.isArray(payload.pet_instance_ids) ? payload.pet_instance_ids : [];
            if (targets.length && host && !targets.includes(host.petInstanceId)) return;
            await restoreLocalCycle(payload.reason || 'restore-requested');
        }
        dispatchState();
    }

    async function refreshState() {
        try {
            const response = await fetch(API_STATE, { cache: 'no-store' });
            if (!response.ok) return null;
            const data = await response.json();
            if (data && data.success && data.state) {
                applyBackendState(data.state);
                return data.state;
            }
        } catch (error) {
            console.warn('[WidgetMode] state refresh failed:', error);
        }
        return null;
    }

    async function refreshSettings() {
        try {
            const response = await fetch(API_SETTINGS, { cache: 'no-store' });
            if (!response.ok) return null;
            clientState.settings = normalizeSettings(await response.json());
            dispatchState();
            return Object.assign({}, clientState.settings);
        } catch (error) {
            console.warn('[WidgetMode] settings refresh failed:', error);
            return null;
        }
    }

    async function setSettings(settings) {
        const next = normalizeSettings(Object.assign({}, clientState.settings, settings || {}));
        try {
            clientState.settings = normalizeSettings(await postJson(API_SETTINGS, next));
            dispatchState();
            return true;
        } catch (error) {
            console.warn('[WidgetMode] settings update failed:', error);
            await refreshSettings();
            return false;
        }
    }

    async function setEnabled(enabled) {
        const next = enabled === true;
        try {
            const data = await postJson(API_ENABLED, { enabled: next });
            if (!data || data.success !== true) throw new Error('invalid response');
            applyBackendState(data.state);
            if (!next) await restoreLocalCycle('widget-mode-disabled');
            showNotice(next
                ? t('settings.widgetMode.enabledNotice', '挂边模式 Beta 已开启。')
                : t('settings.widgetMode.disabledNotice', '挂边模式 Beta 已关闭。'));
            return true;
        } catch (error) {
            console.warn('[WidgetMode] toggle failed:', error);
            showNotice(t('settings.widgetMode.toggleFailed', '挂边模式 Beta 切换失败，请稍后重试。'));
            await refreshState();
            return false;
        }
    }

    function metricLabel(metric) {
        if (metric === 'cpu') return 'CPU';
        if (metric === 'memory') return t('settings.widgetMode.memoryMetric', '内存');
        if (metric === 'gpu') return 'GPU';
        return metric || t('settings.widgetMode.resourceMetric', '资源');
    }

    function getStatusText() {
        const status = clientState.enabled
            ? t('settings.widgetMode.statusOn', '开启')
            : t('settings.widgetMode.statusOff', '关闭');
        const reason = clientState.backendState && clientState.backendState.last_resource_reason;
        if (!reason || !reason.metric) {
            return t('settings.widgetMode.statusOnly', '{status}', { status: status });
        }
        const percent = Number.isFinite(reason.percent) ? Math.round(reason.percent) + '%' : '-';
        return t(
            'settings.widgetMode.statusWithResource',
            '{status} · 诊断：{metric} {percent}',
            { status: status, metric: metricLabel(reason.metric), percent: percent }
        );
    }

    async function resetActivityCandidate(reason) {
        try {
            await postJson(API_ACTIVITY_RESET, { reason: reason || 'frontend-reset' });
        } catch (_) {}
    }

    function joinActiveCompactionCycle(registration) {
        if (
            !registration
            || registration.widget_mode_capable !== true
            || registration.join_as_compacted !== true
            || !registration.compaction_cycle_id
            || registration.compaction_cycle_id === clientState.currentCycleId
        ) return;
        void handleCompactionRequested({
            type: 'widget_mode_compaction_requested',
            source: SOURCE,
            compaction_cycle_id: registration.compaction_cycle_id,
            reason: 'late-join',
            duration_seconds: 0,
        });
    }

    function setHostWindowRegistered(registered) {
        try {
            const host = window.nekoWidgetModeHost;
            if (host && typeof host.setRegistered === 'function') {
                host.setRegistered(registered === true);
            }
        } catch (_) {}
    }

    async function registerHostWindow() {
        const host = window.nekoWidgetModeHost;
        if (!host || typeof host.getContract !== 'function') return null;
        try {
            const contract = await host.getContract();
            if (!contract || contract.windowType !== 'pet' || !contract.petInstanceId) return null;
            clientState.hostContract = contract;
            clientState.hostCompatible = isCompatibleHostContract(contract);
            const registration = await postJson(API_REGISTER_WINDOW, {
                pet_instance_id: contract.petInstanceId,
                window_type: contract.windowType,
                signal_capabilities: contract.signalCapabilities || {},
                widget_mode_protocol_version: contract.widgetModeProtocolVersion,
                widget_mode_compaction_lease_v1: contract.widgetModeCompactionLeaseV1 === true,
            });
            clientState.hostCompatible = !!(
                clientState.hostCompatible
                && registration
                && registration.protocol_compatible === true
                && registration.widget_mode_capable === true
            );
            setHostWindowRegistered(true);
            joinActiveCompactionCycle(registration);
            dispatchState();
            return contract;
        } catch (error) {
            clientState.hostCompatible = false;
            console.warn('[WidgetMode] host registration failed:', error);
            return null;
        }
    }

    function startHostRegistration() {
        const host = window.nekoWidgetModeHost;
        if (!host) return;
        void registerHostWindow();
        if (clientState.registrationTimer) clearInterval(clientState.registrationTimer);
        clientState.registrationTimer = setInterval(registerHostWindow, 10000);
        if (typeof host.onSystemResume === 'function') {
            host.onSystemResume(function () {
                resumeSuspendedRenderers();
                void sendRendererSuspensionAck(false);
                void resetActivityCandidate('system-resume');
                void registerHostWindow();
            });
        }
        if (typeof host.onCompactLeaseInvalidated === 'function') {
            host.onCompactLeaseInvalidated(function (payload) {
                if (!clientState.compactLeaseActive || !clientState.currentCycleId) return;
                const petInstanceId = clientState.hostContract && clientState.hostContract.petInstanceId;
                clientState.compactLeaseActive = false;
                clientState.compactLeaseSuspended = false;
                void restoreLocalCycle(
                    payload && payload.reason ? payload.reason : 'compact-window-lease-invalidated'
                ).then(function (restored) {
                    if (!restored || !petInstanceId) return;
                    return postJson(API_USER_RESTORE, { pet_instance_id: petInstanceId });
                }).then(function (data) {
                    if (data && data.state) applyBackendState(data.state);
                }).catch(function (error) {
                    console.warn('[WidgetMode] compact window lease invalidation recovery failed:', error);
                });
            });
        }
    }

    function bindEvents() {
        window.addEventListener('neko:widget-mode-message', function (event) {
            void handleLifecycleMessage(event && event.detail);
        });
        ['live2d-return-click', 'vrm-return-click', 'mmd-return-click', 'pngtuber-return-click']
            .forEach(function (eventName) {
                window.addEventListener(eventName, function () {
                    Promise.resolve().then(notifyUserRestore);
                });
            });
        window.addEventListener('neko:return-ball-manual-move', function (event) {
            const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            if (detail.reason === 'return-ball-drag-start') {
                void suspendCompactWindowLeaseForDrag();
            } else if (detail.reason === 'return-ball-drag-end') {
                void resumeCompactWindowLeaseAfterDrag(detail.container || null);
            }
            if (detail.reason === 'return-ball-drag-end' && detail.dragCancelled !== true) {
                clientState.returnBallMoved = true;
            }
        });
        window.addEventListener('neko:character-switch-start', function () {
            void resetActivityCandidate('character-switch');
        });
        window.addEventListener('neko:websocket-connection-state', function (event) {
            const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            if (detail.connected === false) void resetActivityCandidate('connection-interrupted');
            if (detail.connected === true) void registerHostWindow();
        });
        window.addEventListener('beforeunload', function () {
            const host = clientState.hostContract;
            if (!host) return;
            void releaseCompactWindowLease();
            setHostWindowRegistered(false);
            void postJson(API_UNREGISTER_WINDOW, {
                pet_instance_id: host.petInstanceId,
            }, { keepalive: true }).catch(function () {});
        });
        window.addEventListener('DOMContentLoaded', function () {
            void refreshState();
            startHostRegistration();
        }, { once: true });
    }

    window.nekoWidgetMode = {
        refreshState: refreshState,
        setEnabled: setEnabled,
        isEnabled: function () { return clientState.enabled === true; },
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
        void refreshState();
        startHostRegistration();
    }
})();
