/**
 * app-widget-mode.js -- browser client for the Widget Mode enabled state.
 */
(function () {
    'use strict';

    const API_STATE = '/api/widget-mode/state';
    const API_ENABLED = '/api/widget-mode/enabled';
    const API_STEALTH_ENABLED = '/api/widget-mode/stealth-enabled';
    // Shown on the first edge peek of each pet window, so a model that just
    // slid into the screen edge is not mistaken for a missing one, even by a
    // user who missed the notice last time. Kept in sessionStorage, which is
    // scoped to the pet window: it survives a reload of that window but starts
    // empty in a new one. The desktop shell has a single pet window and only
    // creates a new one on an app restart or when it rebuilds its windows, so
    // in practice this is once per app launch.
    const FIRST_PEEK_NOTICE_SESSION_KEY = 'neko_widget_mode_first_peek_notice_shown';
    // Earlier builds remembered the notice forever in localStorage; that flag
    // must no longer suppress it.
    const LEGACY_FIRST_PEEK_NOTICE_STORAGE_KEY = 'neko_widget_mode_first_peek_notice_seen';
    const FIRST_PEEK_NOTICE_DURATION_MS = 10000;
    // The desktop shell's toast window (templates/toast.html) posts the text of
    // each status notice it shows on this same-origin channel. Behind the shell's
    // one-way IPC bridge that receipt is the only proof the notice was shown.
    const STATUS_TOAST_SHOWN_CHANNEL = 'neko-status-toast-shown';
    const FIRST_PEEK_NOTICE_ACK_TIMEOUT_MS = 1500;
    // Peeks that may try (send, then resend once) before giving up for this
    // launch, so a toast window that never confirms (or keeps dropping the
    // notice) cannot bring back a notice on every peek.
    const FIRST_PEEK_NOTICE_MAX_ATTEMPTS = 2;
    // Kept next to the shown flag so a reload of the same pet window cannot
    // reset the attempt budget; a new pet window (app restart) starts at zero.
    const FIRST_PEEK_NOTICE_ATTEMPTS_SESSION_KEY = 'neko_widget_mode_first_peek_notice_attempts';

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

    try { window.localStorage.removeItem(LEGACY_FIRST_PEEK_NOTICE_STORAGE_KEY); } catch (_) {}

    function hasShownFirstPeekNoticeThisLaunch() {
        try {
            return window.sessionStorage.getItem(FIRST_PEEK_NOTICE_SESSION_KEY) === '1';
        } catch (_) {
            return false;
        }
    }

    let firstPeekNoticePending = false;
    // Falls back to this page's own count when sessionStorage is unavailable.
    let firstPeekNoticeAttempts = readFirstPeekNoticeAttempts();

    function readFirstPeekNoticeAttempts() {
        try {
            const count = parseInt(window.sessionStorage.getItem(FIRST_PEEK_NOTICE_ATTEMPTS_SESSION_KEY), 10);
            return Number.isFinite(count) && count > 0 ? count : 0;
        } catch (_) {
            return 0;
        }
    }

    function recordFirstPeekNoticeAttempt() {
        firstPeekNoticeAttempts += 1;
        try {
            window.sessionStorage.setItem(FIRST_PEEK_NOTICE_ATTEMPTS_SESSION_KEY, String(firstPeekNoticeAttempts));
        } catch (_) {}
    }

    function markFirstPeekNoticeShown() {
        firstPeekNoticeShown = true;
        try { window.sessionStorage.setItem(FIRST_PEEK_NOTICE_SESSION_KEY, '1'); } catch (_) {}
    }

    // Only the page's own DOM toast renders into #status-toast-text. A
    // replacement (the desktop shell's IPC bridge) shows the notice in another
    // window, so this page's DOM can never confirm it.
    function isDesktopToastBridge() {
        const showStatusToast = window.showStatusToast;
        return typeof showStatusToast === 'function' && showStatusToast.__nekoDomStatusToast !== true;
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

    // Sends the notice through the bridge and waits for the toast window's
    // receipt. Resolves true once confirmed, false on timeout, and null when the
    // receipt channel is unavailable (nothing can confirm the hand-off).
    function showBridgeNoticeAndAwaitReceipt(message) {
        let channel = null;
        try {
            if (typeof BroadcastChannel === 'function') {
                channel = new BroadcastChannel(STATUS_TOAST_SHOWN_CHANNEL);
            }
        } catch (_) {
            channel = null;
        }
        if (!channel) {
            showNotice(message, FIRST_PEEK_NOTICE_DURATION_MS);
            return Promise.resolve(null);
        }
        return new Promise((resolve) => {
            let timer = null;
            const finish = (value) => {
                if (timer !== null) clearTimeout(timer);
                timer = null;
                try { channel.close(); } catch (_) {}
                resolve(value);
            };
            // Listen before sending: the receipt can arrive right after the send.
            channel.onmessage = (event) => {
                const data = event && event.data;
                if (data && data.message === message) finish(true);
            };
            // Some Linux shells draw the notice inline in this pet window instead,
            // where this page's DOM can confirm it.
            timer = setTimeout(() => {
                timer = null;
                finish(isStatusToastShowing(message));
            }, FIRST_PEEK_NOTICE_ACK_TIMEOUT_MS);
            showNotice(message, FIRST_PEEK_NOTICE_DURATION_MS);
        });
    }

    async function showFirstPeekNoticeViaBridge(message) {
        firstPeekNoticePending = true;
        // Counted before sending, so a reload mid-attempt still spends it.
        recordFirstPeekNoticeAttempt();
        try {
            let receipt = await showBridgeNoticeAndAwaitReceipt(message);
            // No receipt: the toast window lost or dropped it (for example the
            // first notice into a freshly rebuilt toast window). Resend once.
            if (receipt === false) receipt = await showBridgeNoticeAndAwaitReceipt(message);
            if (receipt !== false || firstPeekNoticeAttempts >= FIRST_PEEK_NOTICE_MAX_ATTEMPTS) {
                markFirstPeekNoticeShown();
            }
        } finally {
            firstPeekNoticePending = false;
        }
    }

    function showFirstPeekNotice(event) {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        // Only a freshly created anchor is a real peek; visibility toggles of an
        // existing anchor (stealth, interaction) are not.
        if (!detail || detail.active !== true || detail.reason !== 'anchor-created') return;
        if (firstPeekNoticeShown || firstPeekNoticePending || hasShownFirstPeekNoticeThisLaunch()) return;
        const message = t(
            'settings.widgetMode.firstPeekNotice',
            '她躲到屏幕边上了，把她拖回来就能恢复。不需要的话，可以在托盘菜单的“设置”里关闭“启用贴边探身”。'
        );
        if (isDesktopToastBridge()) {
            void showFirstPeekNoticeViaBridge(message);
            return;
        }
        showNotice(message, FIRST_PEEK_NOTICE_DURATION_MS);
        // showStatusToast silently drops a notice while a higher-priority one is
        // on screen; only count it as shown once it actually rendered, otherwise
        // the next peek tries again.
        if (!isStatusToastShowing(message)) return;
        markFirstPeekNoticeShown();
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
