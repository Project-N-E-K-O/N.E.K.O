/**
 * app-widget-mode.js -- browser client for the Widget Mode enabled state.
 */
(function () {
    'use strict';

    const API_STATE = '/api/widget-mode/state';
    const API_ENABLED = '/api/widget-mode/enabled';

    const clientState = {
        enabled: false,
        backendState: null,
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

    function getState() {
        return {
            enabled: clientState.enabled,
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

    async function postJson(url, payload) {
        const response = await fetch(url, {
            method: 'POST',
            headers: await getMutationHeaders(),
            body: JSON.stringify(payload || {}),
        });
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return await response.json().catch(function () { return {}; });
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

    async function setEnabled(enabled) {
        const next = enabled === true;
        try {
            const data = await postJson(API_ENABLED, { enabled: next });
            if (!data || data.success !== true) throw new Error('invalid response');
            applyBackendState(data.state);
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

    window.nekoWidgetMode = {
        refreshState: refreshState,
        setEnabled: setEnabled,
        isEnabled: function () { return clientState.enabled === true; },
        getState: getState,
    };

    window.addEventListener('DOMContentLoaded', function () {
        void refreshState();
    }, { once: true });
    if (document.readyState !== 'loading') {
        void refreshState();
    }
})();
