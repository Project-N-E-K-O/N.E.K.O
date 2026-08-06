(function () {
    'use strict';

    if (window.NekoMotionBridge) return;

    const USER_TEXT_LIMIT = 1000;
    const ASSISTANT_TEXT_LIMIT = 24000;
    const CONTEXT_TTL_MS = 60000;
    const MAX_PENDING_CONTEXTS = 16;
    const pendingByRequest = new Map();
    let pendingWithoutRequest = null;
    let lastClosedStageText = '';
    let lastTimestamp = 0;

    function normalizedRequestId(value) {
        return value === undefined || value === null || value === '' ? '' : String(value);
    }

    function prunePendingContexts() {
        const cutoff = Date.now() - CONTEXT_TTL_MS;
        pendingByRequest.forEach(function (entry, requestId) {
            if (!entry || entry.at < cutoff) pendingByRequest.delete(requestId);
        });
        if (pendingWithoutRequest && pendingWithoutRequest.at < cutoff) {
            pendingWithoutRequest = null;
        }
        while (pendingByRequest.size > MAX_PENDING_CONTEXTS) {
            pendingByRequest.delete(pendingByRequest.keys().next().value);
        }
    }

    function rememberUserText(event) {
        const detail = event && event.detail || {};
        const text = String(detail.text || '').trim().slice(0, USER_TEXT_LIMIT);
        if (!text) return;
        const entry = { text: text, at: Date.now() };
        const requestId = normalizedRequestId(detail.requestId);
        if (requestId) pendingByRequest.set(requestId, entry);
        else pendingWithoutRequest = entry;
        prunePendingContexts();
    }

    function peekUserText(requestIdValue) {
        prunePendingContexts();
        const requestId = normalizedRequestId(requestIdValue);
        if (requestId) {
            const entry = pendingByRequest.get(requestId);
            return entry ? entry.text : '';
        }
        if (pendingWithoutRequest) {
            return pendingWithoutRequest.text;
        }
        if (pendingByRequest.size === 1) {
            const only = pendingByRequest.entries().next().value;
            return only[1].text;
        }
        return '';
    }

    function finishUserText(event) {
        const detail = event && event.detail || {};
        const requestId = normalizedRequestId(detail.requestId);
        if (requestId) {
            pendingByRequest.delete(requestId);
        } else if (pendingWithoutRequest) {
            pendingWithoutRequest = null;
        } else if (pendingByRequest.size === 1) {
            pendingByRequest.delete(pendingByRequest.keys().next().value);
        }
    }

    function clearPendingContext(event) {
        const detail = event && event.detail || {};
        const requestId = normalizedRequestId(detail.requestId);
        if (requestId) pendingByRequest.delete(requestId);
        else {
            pendingByRequest.clear();
            pendingWithoutRequest = null;
        }
    }

    function clearAllPendingContexts() {
        pendingByRequest.clear();
        pendingWithoutRequest = null;
        lastClosedStageText = '';
    }

    function relay(eventName, detail) {
        const payload = Object.assign({}, detail || {});
        payload.lanlan_name = String(
            window.lanlan_config && window.lanlan_config.lanlan_name || ''
        );
        if (typeof payload.text === 'string') {
            payload.text = payload.text.slice(0, ASSISTANT_TEXT_LIMIT);
        }
        lastTimestamp = Math.max(Date.now(), lastTimestamp + 1);
        const message = {
            action: 'motion_lifecycle',
            eventName: eventName,
            detail: payload,
            timestamp: lastTimestamp
        };
        window.dispatchEvent(new CustomEvent('neko:motion-lifecycle-relay', {
            detail: message
        }));
        const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
        if (channel && typeof channel.postMessage === 'function') channel.postMessage(message);
    }

    function relayTurnStart(event) {
        lastClosedStageText = '';
        const detail = Object.assign({}, event && event.detail || {});
        const userText = peekUserText(detail.requestId);
        if (userText) detail.userText = userText;
        relay('neko-assistant-turn-start', detail);
    }

    function relayTurnEnd(event) {
        const detail = Object.assign({}, event && event.detail || {});
        detail.structured = detail.structured === true || window._turnIsStructured === true;
        detail.text = typeof window._geminiTurnFullText === 'string'
            ? window._geminiTurnFullText : '';
        relay('neko-assistant-turn-end', detail);
        finishUserText(event);
    }

    function relayClosedStage(event) {
        const hasEventText = !!(event && event.detail && typeof event.detail.text === 'string');
        const text = hasEventText ? event.detail.text
            : (typeof window._geminiTurnFullText === 'string' ? window._geminiTurnFullText : '');
        if (!text) {
            lastClosedStageText = '';
            return;
        }
        const closedAt = Math.max(text.lastIndexOf(')'), text.lastIndexOf('）'));
        if (closedAt < 0) return;
        const closedText = text.slice(0, closedAt + 1);
        if (closedText === lastClosedStageText) return;
        lastClosedStageText = closedText;
        relay('neko-assistant-text-update', {
            turnId: window._nekoAssistantTurnId || null,
            text: closedText,
            structured: window._turnIsStructured === true
        });
    }

    function relayDetail(eventName) {
        return function (event) {
            relay(eventName, event && event.detail || {});
        };
    }

    window.addEventListener('neko:user-content-sent', rememberUserText);
    window.addEventListener('neko:user-voice-content-received', rememberUserText);
    window.addEventListener('neko:assistant-response-cancelled', clearPendingContext);
    window.addEventListener('neko:session-ended-by-server', clearAllPendingContexts);
    window.addEventListener('neko:websocket-disconnected', clearAllPendingContexts);
    window.addEventListener('neko-assistant-turn-start', relayTurnStart);
    window.addEventListener('neko-assistant-turn-end', relayTurnEnd);
    window.addEventListener('neko-assistant-emotion-ready', relayDetail('neko-assistant-emotion-ready'));
    window.addEventListener('neko-assistant-speech-cancel', relayDetail('neko-assistant-speech-cancel'));
    window.addEventListener('neko-compact-caption-update', relayClosedStage);

    window.NekoMotionBridge = Object.freeze({
        clearPendingContexts: clearAllPendingContexts
    });
})();
