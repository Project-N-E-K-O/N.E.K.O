/**
 * app-widget-mode.js -- browser client for the Widget Mode enabled state.
 */
(function () {
    'use strict';

    const API_STATE = '/api/widget-mode/state';
    const API_ENABLED = '/api/widget-mode/enabled';
    const API_STEALTH_ENABLED = '/api/widget-mode/stealth-enabled';

    const clientState = {
        enabled: false,
        stealthEnabled: false,
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
            stealthEnabled: clientState.stealthEnabled,
            backendState: clientState.backendState,
        };
    }

    function dispatchState() {
        try {
            window.dispatchEvent(new CustomEvent('neko:widget-mode-state-changed', {
                detail: getState(),
            }));
        } catch (_) {}
    }

    function applyBackendState(state) {
        if (!state || typeof state !== 'object') return;
        clientState.backendState = state;
        clientState.enabled = state.enabled === true;
        clientState.stealthEnabled = state.stealthEnabled === true;
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

    async function setEnabled(enabled, options) {
        const next = enabled === true;
        const silent = !!(options && options.silent === true);
        try {
            const data = await postJson(API_ENABLED, { enabled: next });
            if (!data || data.success !== true) throw new Error('invalid response');
            applyBackendState(data.state);
            if (!silent) {
                showNotice(next
                    ? t('settings.widgetMode.enabledNotice', '贴边探身已开启。')
                    : t('settings.widgetMode.disabledNotice', '贴边探身已关闭。'));
            }
            return true;
        } catch (error) {
            console.warn('[WidgetMode] toggle failed:', error);
            if (!silent) {
                showNotice(t('settings.widgetMode.toggleFailed', '贴边探身切换失败，请稍后重试。'));
            }
            await refreshState();
            return false;
        }
    }

    async function setStealthEnabled(enabled, options) {
        const next = enabled === true;
        const silent = !!(options && options.silent === true);
        try {
            const data = await postJson(API_STEALTH_ENABLED, { enabled: next });
            if (!data || data.success !== true) throw new Error('invalid response');
            applyBackendState(data.state);
            if (!silent) {
                showNotice(next
                    ? t('settings.widgetMode.stealthEnabledNotice', '隐身模式已开启。')
                    : t('settings.widgetMode.stealthDisabledNotice', '隐身模式已关闭。'));
            }
            return true;
        } catch (error) {
            console.warn('[WidgetMode] stealth toggle failed:', error);
            if (!silent) {
                showNotice(t('settings.widgetMode.toggleFailed', '贴边探身切换失败，请稍后重试。'));
            }
            await refreshState();
            return false;
        }
    }

    window.nekoWidgetMode = {
        refreshState: refreshState,
        setEnabled: setEnabled,
        setStealthEnabled: setStealthEnabled,
        isEnabled: function () { return clientState.enabled === true; },
        isStealthEnabled: function () { return clientState.stealthEnabled === true; },
        getState: getState,
    };

    window.addEventListener('DOMContentLoaded', function () {
        void refreshState();
    }, { once: true });
    if (document.readyState !== 'loading') {
        void refreshState();
    }
})();
