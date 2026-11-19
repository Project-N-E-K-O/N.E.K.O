/** Market installs the selected package; the local selector owns starting and continuing. */
(function () {
    'use strict';
    var transport = window.nekoTheaterTransport;
    if (!transport) return;
    var base = '/api/theater-market/launch';
    var MARKET_WINDOW_NAME = 'neko_theater_market';
    var SELECTOR_WINDOW_NAME = 'neko_theater';
    var NAMED_WINDOW_KEY_PREFIX = 'neko:named-window:';
    var NAMED_WINDOW_FOCUS_KEY_PREFIX = 'neko:named-window-focus:';
    var NAMED_WINDOW_CHANNEL = 'neko:named-window';
    var NAMED_WINDOW_TTL_MS = 5000;
    var NAMED_WINDOW_FOCUS_ACK = 'neko:named-window-focus-ack';
    var NAMED_WINDOW_ACK_TIMEOUT_MS = 800;
    var connecting = false;
    var channel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('neko_page_channel') : null;
    function label(key) { return window.t ? window.t('theater.' + key) : key; }
    function post(data) {
        var message = transport.createMessage('theater-market', data);
        if (channel) channel.postMessage(message);
        window.postMessage(message, window.location.origin);
        if (window.opener && !window.opener.closed) window.opener.postMessage(message, window.location.origin);
    }
    function openMarket() {
        var popup = window.open('/theater?market=browse', MARKET_WINDOW_NAME);
        if (!popup && typeof window.showStatusToast === 'function') window.showStatusToast(label('marketConnectError'), 5000);
        return !!popup;
    }
    function openSelector() {
        var popup = typeof window.openOrFocusWindow === 'function'
            ? window.openOrFocusWindow('/theater', SELECTOR_WINDOW_NAME, 'width=1100,height=760,menubar=no,toolbar=no,location=no,status=no')
            : window.open('/theater', SELECTOR_WINDOW_NAME);
        if (!popup && typeof window.showStatusToast === 'function') window.showStatusToast(label('storyListFailed'), 5000);
        return !!popup;
    }
    async function redirectSelector() {
        var query = new URLSearchParams(window.location.search);
        if (!query.has('market') || connecting) return;
        var status = document.getElementById('theater-selector-status');
        var retry = document.getElementById('theater-market-retry-btn');
        var connection = document.getElementById('theater-market-connection');
        connecting = true;
        if (retry) retry.disabled = true;
        if (connection) connection.setAttribute('aria-busy', 'true');
        try {
            if (['127.0.0.1', 'localhost', '[::1]'].indexOf(window.location.hostname) < 0) {
                if (status) {
                    status.setAttribute('data-i18n', 'theater.marketLocalOnly');
                    status.textContent = label('marketLocalOnly');
                }
                return;
            }
            if (status) {
                status.setAttribute('data-i18n', 'theater.marketPreparing');
                status.textContent = label('marketPreparing');
            }
            var body = {random: query.get('market') === 'random'};
            if (query.get('share_id')) {
                body.share_id = Number(query.get('share_id'));
                body.revision = Number(query.get('market_revision'));
                body.file_sha256 = query.get('file_sha256');
            }
            var result = await transport.requestJson(base, {method: 'POST', body: body});
            if (!result.ok) throw new Error(result.reason);
            post({action: 'theater:market-opened', request_id: result.request_id});
            window.location.replace(result.url);
        } catch (_) {
            if (status) {
                status.setAttribute('data-i18n', 'theater.marketConnectError');
                status.textContent = label('marketConnectError');
            }
            if (retry) retry.hidden = false;
        } finally {
            connecting = false;
            if (retry) retry.disabled = false;
            if (connection) connection.setAttribute('aria-busy', 'false');
        }
    }
    // A market page in its own named window must not turn into a second selector;
    // one opened in place from the selector keeps the plain link navigation.
    function isLiveSelector(target) {
        try {
            return !!(target && !target.closed && target.document.querySelector('[data-theater-selector-app]'));
        } catch (_) {
            return false;
        }
    }
    function focusExistingSelector() {
        var opener = window.opener && !window.opener.closed ? window.opener : null;
        if (!opener) return false;
        try {
            if (isLiveSelector(opener)) {
                opener.postMessage({type: 'neko:restore-window'}, window.location.origin);
                opener.focus();
                return true;
            }
            // The host app keeps handles for the windows it opened through openOrFocusWindow.
            var handle = opener._openedWindows && opener._openedWindows[SELECTOR_WINDOW_NAME];
            if (!isLiveSelector(handle)) return false;
            if (typeof opener.openOrFocusWindow === 'function') {
                opener.openOrFocusWindow('/theater', SELECTOR_WINDOW_NAME, undefined, {preserveGeometryOnReuse: true});
            } else {
                handle.postMessage({type: 'neko:restore-window'}, window.location.origin);
                handle.focus();
            }
            return true;
        } catch (_) {
            return false;
        }
    }
    // Same shared named-window protocol as common_dialogs.js openOrFocusWindow: a selector
    // opened by another window (e.g. main page while the invite came from /chat) is only
    // reachable through its registry heartbeat and focus broadcast. A heartbeat alone may be
    // stale, so the handoff only counts once the selector acknowledges the request.
    function isRegisteredSelectorFresh() {
        try {
            var record = JSON.parse(window.localStorage.getItem(NAMED_WINDOW_KEY_PREFIX + SELECTOR_WINDOW_NAME) || 'null');
            return !!record && Date.now() - Number(record.timestamp) < NAMED_WINDOW_TTL_MS;
        } catch (_) {
            return false;
        }
    }
    function requestRegisteredSelectorFocus() {
        return new Promise(function (resolve) {
            if (typeof BroadcastChannel === 'undefined') { resolve(false); return; }
            var requestId = 'theater_back_' + Date.now() + '_' + Math.random().toString(36).slice(2);
            var message = {type: 'neko:named-window-focus', windowName: SELECTOR_WINDOW_NAME, payload: null, requestId: requestId, timestamp: Date.now()};
            var namedChannel = null;
            var timer = 0;
            function finish(acknowledged) {
                window.clearTimeout(timer);
                try { namedChannel.close(); } catch (_) {}
                resolve(acknowledged);
            }
            try {
                namedChannel = new BroadcastChannel(NAMED_WINDOW_CHANNEL);
                namedChannel.onmessage = function (event) {
                    var data = event && event.data;
                    if (data && data.type === NAMED_WINDOW_FOCUS_ACK && data.requestId === requestId) finish(true);
                };
                timer = window.setTimeout(function () { finish(false); }, NAMED_WINDOW_ACK_TIMEOUT_MS);
                namedChannel.postMessage(message);
            } catch (_) {
                finish(false);
                return;
            }
            try { window.localStorage.setItem(NAMED_WINDOW_FOCUS_KEY_PREFIX + SELECTOR_WINDOW_NAME, JSON.stringify(message)); } catch (_) {}
        });
    }
    function closeMarketWindow() {
        // If the window refuses to close, still leave the user on a working selector.
        window.setTimeout(function () {
            if (!window.closed) window.location.assign('/theater');
        }, 1500);
        var host = window.nekoHost;
        if (!host || typeof host.closeWindow !== 'function') {
            window.close();
            return;
        }
        Promise.resolve().then(function () { return host.closeWindow(); }).then(function (result) {
            if (!(result && result.ok)) window.close();
        }, function () { window.close(); });
    }
    var returning = false;
    function returnToSelector(event) {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        if (window.name !== MARKET_WINDOW_NAME) return;
        if (returning) { event.preventDefault(); return; }
        if (focusExistingSelector()) {
            event.preventDefault();
            returning = true;
            closeMarketWindow();
            return;
        }
        if (!isRegisteredSelectorFresh()) return;
        event.preventDefault();
        returning = true;
        requestRegisteredSelectorFocus().then(function (acknowledged) {
            if (acknowledged) closeMarketWindow();
            else window.location.assign('/theater');
        });
    }
    document.addEventListener('DOMContentLoaded', function () {
        var retry = document.getElementById('theater-market-retry-btn');
        if (retry) retry.addEventListener('click', redirectSelector);
        var back = document.getElementById('theater-market-back-link');
        if (back) back.addEventListener('click', returnToSelector);
    });
    window.nekoTheaterMarket = Object.freeze({open: openSelector, browse: openMarket, redirectSelector: redirectSelector});
})();
