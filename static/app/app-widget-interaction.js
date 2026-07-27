/**
 * app-widget-interaction.js
 *
 * Renderer-owned semantic lifecycle for user-initiated Yui interactions.
 * The pet window consumes the derived active state; chat windows relay the
 * same lifecycle through BroadcastChannel without treating generic input,
 * focus, hover, or proactive assistant output as an interaction.
 */
(function () {
    'use strict';

    if (window.NekoWidgetInteraction) return;

    const CHANNEL_NAME = 'neko-widget-interaction-v1';
    const SETTLE_DELAY_MS = 1000;
    const WATCHDOG_MS = 120000;
    const MESSAGE_HISTORY_LIMIT = 256;
    const instanceId = `widget-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
    const state = {
        active: false,
        phase: 'idle',
        lease: null,
        settleTimer: 0,
        watchdogTimer: 0,
    };
    let messageSequence = 0;
    let channel = null;
    const seenMessageIds = new Set();

    function normalizeId(value) {
        if (value === undefined || value === null) return '';
        return String(value).trim();
    }

    function getMetaRequestId(detail) {
        const meta = detail && detail.meta && typeof detail.meta === 'object'
            ? detail.meta
            : {};
        return normalizeId(
            detail && (detail.requestId || detail.request_id)
            || meta.requestId
            || meta.request_id
            || meta.interactionId
            || meta.interaction_id
        );
    }

    function isExcludedUserContentSource(source) {
        const normalized = normalizeId(source).toLowerCase();
        return normalized === 'agent'
            || normalized.startsWith('agent-')
            || normalized === 'game'
            || normalized.startsWith('game-')
            || normalized === 'plugin'
            || normalized.startsWith('plugin-')
            || normalized === 'system'
            || normalized.startsWith('system-')
            || normalized === 'proactive'
            || normalized.startsWith('proactive-');
    }

    function getSnapshot(reason) {
        const lease = state.lease;
        return {
            active: state.active,
            phase: state.phase,
            leaseId: lease ? lease.leaseId : '',
            requestId: lease ? lease.requestId : '',
            turnId: lease ? lease.turnId : '',
            source: lease ? lease.source : '',
            reason: reason || '',
            timestamp: Date.now(),
        };
    }

    function dispatchLifecycle(kind, reason) {
        const detail = getSnapshot(reason);
        try {
            window.dispatchEvent(new CustomEvent(`neko:widget-interaction-${kind}`, { detail }));
        } catch (_) {}
        try {
            window.dispatchEvent(new CustomEvent('neko:widget-interaction-state-changed', { detail }));
        } catch (_) {}
    }

    function clearSettleTimer() {
        if (!state.settleTimer) return;
        clearTimeout(state.settleTimer);
        state.settleTimer = 0;
    }

    function clearWatchdogTimer() {
        if (!state.watchdogTimer) return;
        clearTimeout(state.watchdogTimer);
        state.watchdogTimer = 0;
    }

    function cancelInteraction(reason) {
        if (!state.active && !state.lease) return false;
        clearSettleTimer();
        clearWatchdogTimer();
        state.active = false;
        state.phase = 'idle';
        state.lease = null;
        dispatchLifecycle('cancel', reason || 'cancel');
        return true;
    }

    function armWatchdog() {
        clearWatchdogTimer();
        state.watchdogTimer = setTimeout(function () {
            state.watchdogTimer = 0;
            cancelInteraction('watchdog-timeout');
        }, WATCHDOG_MS);
    }

    function startInteraction(detail) {
        const requestId = getMetaRequestId(detail);
        const source = normalizeId(detail && detail.source) || 'user';
        const wasActive = state.active;
        clearSettleTimer();
        state.active = true;
        state.phase = 'waiting';
        state.lease = {
            leaseId: requestId || normalizeId(detail && detail.leaseId)
                || `${instanceId}-${Date.now()}`,
            requestId,
            turnId: '',
            source,
            turnEnded: false,
            speechActive: false,
        };
        armWatchdog();
        dispatchLifecycle(wasActive ? 'extend' : 'start', source);
    }

    function matchesActiveLease(detail, allowUnboundRequestless) {
        const lease = state.lease;
        if (!state.active || !lease) return false;
        const requestId = getMetaRequestId(detail);
        const turnId = normalizeId(detail && (detail.turnId || detail.turn_id));
        if (lease.requestId) {
            if (requestId && requestId !== lease.requestId) return false;
            if (!requestId && (!lease.turnId || !turnId || lease.turnId !== turnId)) {
                return false;
            }
        } else if (requestId) {
            return false;
        } else if (!allowUnboundRequestless && !lease.turnId) {
            return false;
        }
        if (lease.turnId && turnId && lease.turnId !== turnId) return false;
        return true;
    }

    function noteAssistantTurnStart(detail) {
        if (!matchesActiveLease(detail, true)) return;
        const turnId = normalizeId(detail && (detail.turnId || detail.turn_id));
        if (!turnId) return;
        clearSettleTimer();
        state.lease.turnId = turnId;
        state.lease.turnEnded = false;
        state.phase = 'reply';
        armWatchdog();
        dispatchLifecycle('extend', 'assistant-turn-start');
    }

    function scheduleInteractionEnd(reason) {
        clearSettleTimer();
        state.phase = 'settling';
        dispatchLifecycle('extend', reason || 'settling');
        state.settleTimer = setTimeout(function () {
            state.settleTimer = 0;
            if (!state.active || !state.lease) return;
            if (!state.lease.turnEnded || state.lease.speechActive) return;
            clearWatchdogTimer();
            state.active = false;
            state.phase = 'idle';
            state.lease = null;
            dispatchLifecycle('end', reason || 'complete');
        }, SETTLE_DELAY_MS);
    }

    function noteAssistantTurnEnd(detail) {
        if (!matchesActiveLease(detail, false)) return;
        state.lease.turnEnded = true;
        armWatchdog();
        if (!state.lease.speechActive) {
            scheduleInteractionEnd('assistant-turn-end');
        }
    }

    function noteAssistantSpeechStart(detail) {
        if (!matchesActiveLease(detail, false)) return;
        const turnId = normalizeId(detail && (detail.turnId || detail.turn_id));
        if (turnId && !state.lease.turnId) state.lease.turnId = turnId;
        clearSettleTimer();
        state.lease.speechActive = true;
        state.phase = 'speaking';
        armWatchdog();
        dispatchLifecycle('extend', 'assistant-speech-start');
    }

    function noteAssistantSpeechEnd(detail) {
        if (!matchesActiveLease(detail, false)) return;
        state.lease.speechActive = false;
        armWatchdog();
        if (state.lease.turnEnded) {
            scheduleInteractionEnd('assistant-speech-end');
        }
    }

    function noteAssistantSpeechCancel(detail) {
        if (!matchesActiveLease(detail, false)) return;
        cancelInteraction('assistant-speech-cancel');
    }

    function applySemanticMessage(message) {
        if (!message || typeof message !== 'object') return;
        const messageId = normalizeId(message.id);
        if (messageId) {
            if (seenMessageIds.has(messageId)) return;
            seenMessageIds.add(messageId);
            if (seenMessageIds.size > MESSAGE_HISTORY_LIMIT) {
                seenMessageIds.delete(seenMessageIds.values().next().value);
            }
        }
        const detail = message.detail && typeof message.detail === 'object'
            ? message.detail
            : {};
        if (message.type === 'user-start') {
            startInteraction(detail);
        } else if (message.type === 'turn-start') {
            noteAssistantTurnStart(detail);
        } else if (message.type === 'turn-end') {
            noteAssistantTurnEnd(detail);
        } else if (message.type === 'speech-start') {
            noteAssistantSpeechStart(detail);
        } else if (message.type === 'speech-end') {
            noteAssistantSpeechEnd(detail);
        } else if (message.type === 'speech-cancel') {
            noteAssistantSpeechCancel(detail);
        } else if (message.type === 'cancel') {
            if (getMetaRequestId(detail) && !matchesActiveLease(detail, false)) {
                return;
            }
            cancelInteraction(normalizeId(detail.reason) || 'remote-cancel');
        }
    }

    function publish(type, detail) {
        const message = {
            id: `${instanceId}-${++messageSequence}`,
            type,
            detail: detail && typeof detail === 'object' ? detail : {},
        };
        applySemanticMessage(message);
        if (channel) {
            try { channel.postMessage(message); } catch (_) {}
        }
        const electronBridge = window.nekoElectronWidgetInteraction;
        if (electronBridge && typeof electronBridge.send === 'function') {
            try { electronBridge.send(message); } catch (_) {}
        }
    }

    function bindLocalEvents() {
        window.addEventListener('neko:user-content-sent', function (event) {
            const detail = Object.assign({ source: 'text' }, event && event.detail || {});
            if (!isExcludedUserContentSource(detail.source)) {
                publish('user-start', detail);
            }
        });
        window.addEventListener('neko:user-voice-content-received', function (event) {
            publish('user-start', Object.assign({ source: 'voice' }, event && event.detail || {}));
        });
        window.addEventListener('neko:avatar-interaction-sent', function (event) {
            publish('user-start', Object.assign({ source: 'avatar-tool' }, event && event.detail || {}));
        });
        window.addEventListener('neko-assistant-turn-start', function (event) {
            publish('turn-start', event && event.detail || {});
        });
        window.addEventListener('neko-assistant-turn-end', function (event) {
            publish('turn-end', event && event.detail || {});
        });
        window.addEventListener('neko-assistant-speech-start', function (event) {
            publish('speech-start', event && event.detail || {});
        });
        window.addEventListener('neko-assistant-speech-end', function (event) {
            publish('speech-end', event && event.detail || {});
        });
        window.addEventListener('neko-assistant-speech-unavailable', function (event) {
            publish('speech-end', event && event.detail || {});
        });
        window.addEventListener('neko-assistant-speech-cancel', function (event) {
            publish('speech-cancel', event && event.detail || {});
        });
        window.addEventListener('neko:session-ended-by-server', function () {
            publish('cancel', { reason: 'session-ended' });
        });
        window.addEventListener('neko:websocket-disconnected', function () {
            publish('cancel', { reason: 'socket-disconnected' });
        });
        window.addEventListener('neko:assistant-response-cancelled', function (event) {
            const detail = event && event.detail && typeof event.detail === 'object'
                ? event.detail
                : {};
            publish('cancel', Object.assign({}, detail, {
                reason: normalizeId(detail.reason) || 'response-cancelled'
            }));
        });
        window.addEventListener('neko:character-left', function () {
            publish('cancel', { reason: 'character-left' });
        });
        window.addEventListener('live2d-goodbye-click', function () {
            publish('cancel', { reason: 'goodbye' });
        });
        window.addEventListener('neko:widget-mode-state-changed', function (event) {
            const detail = event && event.detail && typeof event.detail === 'object'
                ? event.detail
                : {};
            if (detail.enabled === false) {
                publish('cancel', { reason: 'widget-mode-disabled' });
            }
        });
        window.addEventListener('beforeunload', function () {
            cancelInteraction('page-unload');
            if (channel) {
                try { channel.close(); } catch (_) {}
                channel = null;
            }
        }, { once: true });
    }

    if (typeof BroadcastChannel === 'function') {
        try {
            channel = new BroadcastChannel(CHANNEL_NAME);
            channel.addEventListener('message', function (event) {
                applySemanticMessage(event && event.data);
            });
        } catch (_) {
            channel = null;
        }
    }

    window.addEventListener('neko:electron-widget-interaction', function (event) {
        applySemanticMessage(event && event.detail);
    });

    window.NekoWidgetInteraction = {
        isActive: function () { return state.active === true; },
        getState: function () { return getSnapshot('snapshot'); },
        cancel: function (reason) {
            publish('cancel', { reason: normalizeId(reason) || 'manual' });
        },
    };

    bindLocalEvents();
})();
