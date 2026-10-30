/**
 * app-widget-mode.js -- browser client for the Widget Mode enabled state.
 */
(function () {
    'use strict';

    const API_STATE = '/api/widget-mode/state';
    const API_ENABLED = '/api/widget-mode/enabled';
    const API_STEALTH_ENABLED = '/api/widget-mode/stealth-enabled';
    // Shown once, on the first edge peek, so a model that just slid into the
    // screen edge is not mistaken for a missing one.
    const FIRST_PEEK_NOTICE_STORAGE_KEY = 'neko_widget_mode_first_peek_notice_seen';
    const FIRST_PEEK_NOTICE_DURATION_MS = 10000;

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
        // The pre-init fallback t() echoes the key back; never show a raw key.
        if (text === key) text = fallback;
        if (!params) return text || fallback;
        return String(text || fallback).replace(/\{(\w+)\}/g, function (_, name) {
            return Object.prototype.hasOwnProperty.call(params, name) ? params[name] : _;
        });
    }

    function showNotice(message, durationMs) {
        if (!message) return;
        if (typeof window.showStatusToast === 'function') {
            window.showStatusToast(message, durationMs || 6000, { priority: 70 });
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

    let firstPeekNoticeShown = false;

    function hasSeenFirstPeekNotice() {
        try {
            return window.localStorage.getItem(FIRST_PEEK_NOTICE_STORAGE_KEY) === '1';
        } catch (_) {
            return false;
        }
    }

    function isStatusToastShowing(message) {
        if (typeof window.showStatusToast !== 'function') return true;
        try {
            const textEl = document.getElementById('status-toast-text');
            return !!(textEl && textEl.textContent === message);
        } catch (_) {
            return false;
        }
    }

    function showFirstPeekNotice(event) {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        // Only a freshly created anchor is a real peek; visibility toggles of an
        // existing anchor (stealth, interaction) are not.
        if (!detail || detail.active !== true || detail.reason !== 'anchor-created') return;
        if (firstPeekNoticeShown || hasSeenFirstPeekNotice()) return;
        const message = t(
            'settings.widgetMode.firstPeekNotice',
            '她躲到屏幕边上了，把她拖回来就能恢复。不需要的话，可以在托盘菜单的“设置”里关闭“启用贴边探身”。'
        );
        showNotice(message, FIRST_PEEK_NOTICE_DURATION_MS);
        // showStatusToast silently drops a notice while a higher-priority one is
        // on screen; only count it as seen once it actually rendered, otherwise
        // the next peek tries again.
        if (!isStatusToastShowing(message)) return;
        firstPeekNoticeShown = true;
        try { window.localStorage.setItem(FIRST_PEEK_NOTICE_STORAGE_KEY, '1'); } catch (_) {}
    }

    window.addEventListener('neko:live2d-peek-changed', showFirstPeekNotice);

    window.nekoWidgetMode = {
        refreshState: refreshState,
        setEnabled: setEnabled,
        setStealthEnabled: setStealthEnabled,
        isEnabled: function () { return clientState.enabled === true; },
        isStealthEnabled: function () { return clientState.enabled === true && clientState.stealthEnabled === true; },
        getState: getState,
    };

    window.addEventListener('DOMContentLoaded', function () {
        void refreshState();
    }, { once: true });
    if (document.readyState !== 'loading') {
        void refreshState();
    }
})();
