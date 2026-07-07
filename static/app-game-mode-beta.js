/**
 * app-game-mode-beta.js -- frontend bridge for Game Mode Beta resource guard.
 */
(function () {
    'use strict';

    const API_STATE = '/api/game-mode-beta/state';
    const API_ENABLED = '/api/game-mode-beta/enabled';
    const API_MANUAL_RESTORE = '/api/game-mode-beta/manual-restore';

    const clientState = {
        enabled: false,
        lastReason: null,
        autoSwitched: false,
        alreadyCatWhenTriggered: false,
        manualOverride: false,
        restoringFromDisable: false,
        promptedThisCycle: false,
        backendState: null,
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
        dispatchState();
    }

    async function refreshState() {
        try {
            const response = await fetch(API_STATE);
            if (!response.ok) return null;
            const data = await response.json();
            if (data && data.success && data.state) {
                applyBackendState(data.state);
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
                headers: { 'Content-Type': 'application/json' },
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
                    '游戏模式 Beta（测试版）已开启。资源占用持续较高时，NEKO 会自动切换到猫猫形态以降低占用；部分插件、视觉/OCR 功能首次使用可能加载较慢。'
                ));
            } else {
                handleDisabledRestore();
                showNotice(t(
                    'settings.gameModeBeta.disabledNotice',
                    '游戏模式 Beta（测试版）已关闭。NEKO 已恢复普通资源策略。'
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

    function handleDisabledRestore() {
        const shouldRestore = clientState.autoSwitched
            && !clientState.alreadyCatWhenTriggered
            && !clientState.manualOverride
            && isCatFormActive();
        clientState.restoringFromDisable = true;
        try {
            if (shouldRestore) {
                window.dispatchEvent(new CustomEvent('live2d-return-click', {
                    detail: {
                        source: 'game_mode_auto',
                        reason: 'game-mode-disabled',
                    },
                }));
            }
        } finally {
            clientState.restoringFromDisable = false;
            clientState.autoSwitched = false;
            clientState.alreadyCatWhenTriggered = false;
            clientState.manualOverride = false;
            clientState.promptedThisCycle = false;
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
        clientState.lastReason = {
            metric: payload.reason || '',
            percent: payload.percent,
            duration_seconds: payload.duration_seconds,
        };

        if (isCatFormActive()) {
            clientState.alreadyCatWhenTriggered = true;
            clientState.autoSwitched = false;
        } else {
            clientState.alreadyCatWhenTriggered = false;
            clientState.autoSwitched = true;
            clientState.manualOverride = false;
            try {
                window.dispatchEvent(new CustomEvent('live2d-goodbye-click', {
                    detail: {
                        autoGoodbye: true,
                        gameModeAuto: true,
                        source: 'game_mode_auto',
                        reason: 'game-mode-pressure',
                    },
                }));
            } catch (_) {}
        }

        if (!clientState.promptedThisCycle) {
            clientState.promptedThisCycle = true;
            showNotice(t(
                'settings.gameModeBeta.autoSwitchNotice',
                '检测到 {metric} 占用持续较高，已切换到猫猫形态以降低资源占用。',
                { metric: metricLabel(payload.reason) }
            ));
        }
        dispatchState();
    }

    async function notifyManualRestore() {
        if (!clientState.enabled || clientState.restoringFromDisable) return;
        if (!clientState.autoSwitched) return;
        clientState.manualOverride = true;
        clientState.autoSwitched = false;
        try {
            const response = await fetch(API_MANUAL_RESTORE, { method: 'POST' });
            if (response.ok) {
                const data = await response.json().catch(function () { return null; });
                if (data && data.success && data.state) {
                    applyBackendState(data.state);
                }
            }
        } catch (error) {
            console.warn('[GameModeBeta] manual restore notification failed:', error);
        }
    }

    function bindEvents() {
        window.addEventListener('neko:game-mode-beta-auto-switch', function (event) {
            handleAutoSwitchEvent(event && event.detail);
        });
        window.addEventListener('live2d-goodbye-click', function (event) {
            const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            if (clientState.enabled && detail.source !== 'game_mode_auto' && detail.gameModeAuto !== true) {
                clientState.manualOverride = true;
            }
        });
        ['live2d-return-click', 'vrm-return-click', 'mmd-return-click', 'pngtuber-return-click'].forEach(function (eventName) {
            window.addEventListener(eventName, function () {
                notifyManualRestore();
            });
        });
        window.addEventListener('DOMContentLoaded', function () {
            refreshState();
        }, { once: true });
    }

    function getState() {
        return {
            enabled: clientState.enabled,
            lastReason: clientState.lastReason,
            autoSwitched: clientState.autoSwitched,
            alreadyCatWhenTriggered: clientState.alreadyCatWhenTriggered,
            manualOverride: clientState.manualOverride,
            backendState: clientState.backendState,
        };
    }

    window.nekoGameModeBeta = {
        refreshState: refreshState,
        setEnabled: setEnabled,
        isEnabled: function () { return clientState.enabled === true; },
        handleAutoSwitchEvent: handleAutoSwitchEvent,
        getStatusText: getStatusText,
        getState: getState,
    };

    bindEvents();
    if (document.readyState !== 'loading') {
        refreshState();
    }
})();
