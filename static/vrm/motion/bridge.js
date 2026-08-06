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

    function consumed(entry) {
        if (!entry) return '';
        entry.consumed = true;
        return entry.text;
    }

    function peekUserText(requestIdValue) {
        prunePendingContexts();
        const requestId = normalizedRequestId(requestIdValue);
        if (requestId) {
            return consumed(pendingByRequest.get(requestId));
        }
        if (pendingWithoutRequest) {
            return consumed(pendingWithoutRequest);
        }
        if (pendingByRequest.size === 1) {
            const only = pendingByRequest.entries().next().value;
            return consumed(only[1]);
        }
        return '';
    }

    function dropConsumedContexts() {
        pendingByRequest.forEach(function (entry, requestId) {
            if (!entry || entry.consumed) pendingByRequest.delete(requestId);
        });
        if (pendingWithoutRequest && pendingWithoutRequest.consumed) {
            pendingWithoutRequest = null;
        }
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

    function relaySpeechCancel(event) {
        const detail = event && event.detail || {};
        relay('neko-assistant-speech-cancel', detail);
        // 用户打断（user_activity / user_activity_delayed）这一轮就是没了，后端不会
        // 用同一个 request 再跑，而 app-websocket 这条路径只发 speech-cancel、不发
        // neko:assistant-response-cancelled，于是已被取用的用户文本会在 TTL 内继续
        // 挂着，被下一条不带 requestId 的主动回复捡走 —— 一句“好的”就能把上一轮被
        // 打断的指令重放出来。只丢已经被 turn-start 取用过的那部分：speech-cancel
        // 不带 requestId，整表清会连刚发出、还没等到回复的那条一起抹掉（打断本身
        // 通常就是新消息触发的），下一轮反而拿不到用户文本。
        // 其余来源（response_discarded 可能 will_retry，重试轮的 turn-start 还要用
        // 原文；socket_close / character_switch 另有清空入口）一律保持原样。
        if (!/^user_activity(?:_delayed)?$/.test(String(detail.source || ''))) return;
        dropConsumedContexts();
    }

    window.addEventListener('neko:user-content-sent', rememberUserText);
    window.addEventListener('neko:user-voice-content-received', rememberUserText);
    window.addEventListener('neko:assistant-response-cancelled', clearPendingContext);
    window.addEventListener('neko:session-ended-by-server', clearAllPendingContexts);
    window.addEventListener('neko:websocket-disconnected', clearAllPendingContexts);
    window.addEventListener('neko-assistant-turn-start', relayTurnStart);
    window.addEventListener('neko-assistant-turn-end', relayTurnEnd);
    window.addEventListener('neko-assistant-emotion-ready', relayDetail('neko-assistant-emotion-ready'));
    window.addEventListener('neko-assistant-speech-cancel', relaySpeechCancel);
    window.addEventListener('neko-compact-caption-update', relayClosedStage);

    window.NekoMotionBridge = Object.freeze({
        clearPendingContexts: clearAllPendingContexts
    });
})();
