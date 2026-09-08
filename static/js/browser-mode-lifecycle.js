/**
 * Keep a standalone ``--open-browser`` backend tied to its real browser pages.
 *
 * Reload and close both emit pagehide/beforeunload. Each document therefore
 * owns a short-lived lease: a replacement document registers before the
 * server's close grace period expires, while a genuinely closed page does not.
 * The endpoint is inert in Electron/Steam mode.
 */
(function registerBrowserModeLifecycle() {
    'use strict';

    if (window.parent !== window || window.__nekoBrowserModeLifecycleInstalled) {
        return;
    }
    window.__nekoBrowserModeLifecycleInstalled = true;

    const endpoint = '/api/beacon/shutdown';
    const clientId = (window.crypto && typeof window.crypto.randomUUID === 'function')
        ? window.crypto.randomUUID()
        : 'page-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
    let active = false;
    let browserModeDisabled = false;
    let heartbeatTimer = null;

    function sendSignal(action, useBeacon) {
        const body = JSON.stringify({ action: action, client_id: clientId });
        if (useBeacon && navigator.sendBeacon) {
            const blob = new Blob([body], { type: 'application/json' });
            if (navigator.sendBeacon(endpoint, blob)) return null;
        }
        return fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: body,
            keepalive: useBeacon === true
        });
    }

    function stopHeartbeat() {
        if (heartbeatTimer !== null) {
            window.clearInterval(heartbeatTimer);
            heartbeatTimer = null;
        }
    }

    function registerPage() {
        if (active || browserModeDisabled) return;
        active = true;
        sendSignal('register', false).then(function (response) {
            return response.json().catch(function () { return {}; });
        }).then(function (payload) {
            // Electron/Steam deliberately returns ignored=true. Stop here so
            // packaged clients do not keep sending browser-mode heartbeats.
            if (payload && payload.ignored === true) {
                browserModeDisabled = true;
                active = false;
                stopHeartbeat();
            }
        }).catch(function () {
            // A later pageshow or full reload can retry registration. Failure
            // to reach the already-local backend should not disturb the UI.
        });
        heartbeatTimer = window.setInterval(function () {
            if (active) {
                sendSignal('heartbeat', false).catch(function () { });
            }
        }, 15000);
    }

    function releasePage() {
        if (!active) return;
        active = false;
        stopHeartbeat();
        sendSignal('release', true);
    }

    registerPage();
    window.addEventListener('pageshow', registerPage);
    window.addEventListener('pagehide', releasePage);
    window.addEventListener('beforeunload', releasePage);
})();
