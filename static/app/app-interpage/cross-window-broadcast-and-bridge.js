/**
 * app-interpage/cross-window-broadcast-and-bridge.js
 * Inter-page / cross-tab communication.
 *
 * Handles BroadcastChannel dispatch, postMessage listeners, model hot-reload, UI commands, and overlay cleanup.
 * Dependencies loaded before these parts:
 * - app-state.js -> window.appState, window.appConst
 * Runtime dependencies available by the time handlers fire:
 * - window.showStatusToast
 * - window.stopMicCapture / window.clearAudioQueue
 * - window.live2dManager / window.vrmManager
 * - initLive2DModel / initVRMModel globals
 * Load all parts in filename order; this is a classic global script (no import/export).
 */
(function () {
    'use strict';

    window.appInterpage = window.appInterpage || {};
    const I = window.__appInterpageParts || (window.__appInterpageParts = {});
    I.nekoBroadcastChannel = null;
    I._isRelayingYuiGuideHandoffSent = false;
    var _pendingYuiGuideChatMessages = [];
    var _yuiGuideChatFlushTimer = null;
    var _yuiGuideChatFlushAttempts = 0;
    var YUI_GUIDE_CHAT_FLUSH_MAX_ATTEMPTS = 50;
    var IDLE_CHAT_COMPACT_SURFACE_HEARTBEAT_MS = 1000;
    var idleChatCompactSurfaceHeartbeatTimer = 0;
    var idleChatCompactSurfaceLastPayload = null;

    I.postInterpageMessage = function postInterpageMessage(message, options) {
        if (!message || typeof message !== 'object') {
            return false;
        }
        var normalizedOptions = options || {};
        if (I.nekoBroadcastChannel && typeof I.nekoBroadcastChannel.postMessage === 'function') {
            try {
                I.nekoBroadcastChannel.postMessage(message);
                return true;
            } catch (_) {
                // Fall through to opener fallback when BroadcastChannel is temporarily unavailable.
            }
        }
        if (
            normalizedOptions.openerFallback === true
            && window.opener
            && !window.opener.closed
            && typeof window.opener.postMessage === 'function'
        ) {
            try {
                window.opener.postMessage(message, window.location.origin);
                return true;
            } catch (_) {
                return false;
            }
        }
        return false;
    }

    I.stopIdleChatCompactSurfaceHeartbeat = function stopIdleChatCompactSurfaceHeartbeat() {
        if (!idleChatCompactSurfaceHeartbeatTimer) return;
        I.yuiGuideInterpageResources.clearInterval(idleChatCompactSurfaceHeartbeatTimer);
        idleChatCompactSurfaceHeartbeatTimer = 0;
    }

    function startIdleChatCompactSurfaceHeartbeat() {
        if (idleChatCompactSurfaceHeartbeatTimer) return;
        idleChatCompactSurfaceHeartbeatTimer = I.yuiGuideInterpageResources.setInterval(function () {
            if (!I.nekoBroadcastChannel ||
                !idleChatCompactSurfaceLastPayload ||
                !idleChatCompactSurfaceLastPayload.visible ||
                !idleChatCompactSurfaceLastPayload.screenRect) {
                I.stopIdleChatCompactSurfaceHeartbeat();
                return;
            }
            I.postInterpageMessage(Object.assign({}, idleChatCompactSurfaceLastPayload, {
                lanlan_name: I.getCurrentLanlanName(),
                timestamp: Date.now(),
                heartbeat: true
            }));
        }, IDLE_CHAT_COMPACT_SURFACE_HEARTBEAT_MS);
    }

    function syncIdleChatCompactSurfaceHeartbeat(payload) {
        idleChatCompactSurfaceLastPayload = payload || null;
        if (payload && payload.visible && payload.screenRect) {
            startIdleChatCompactSurfaceHeartbeat();
            return;
        }
        I.stopIdleChatCompactSurfaceHeartbeat();
    }

    I.postIdleChatCompactSurfaceState = function postIdleChatCompactSurfaceState(detail) {
        var screenRect = detail && detail.screenRect ? detail.screenRect : null;
        var payload = {
            action: 'idle_chat_compact_surface_state',
            source: 'chat-window',
            lanlan_name: I.getCurrentLanlanName(),
            visible: !!screenRect,
            screenRect: screenRect,
            resizeActive: !!(detail && detail.resizeActive),
            dragging: !!(detail && detail.dragging),
            timestamp: Date.now()
        };
        I.postInterpageMessage(payload);
        syncIdleChatCompactSurfaceHeartbeat(payload);
    }

    function scheduleYuiGuideChatMessageFlush(delay) {
        if (_yuiGuideChatFlushTimer) return;
        _yuiGuideChatFlushTimer = I.yuiGuideInterpageResources.setTimeout(
            flushPendingYuiGuideChatMessages,
            typeof delay === 'number' ? delay : 0
        );
    }

    I.clearYuiGuideChatFlushTimer = function clearYuiGuideChatFlushTimer() {
        if (!_yuiGuideChatFlushTimer) return;
        I.yuiGuideInterpageResources.clearTimeout(_yuiGuideChatFlushTimer);
        _yuiGuideChatFlushTimer = null;
    }

    function flushPendingYuiGuideChatMessages() {
        _yuiGuideChatFlushTimer = null;
        if (!_pendingYuiGuideChatMessages.length) {
            _yuiGuideChatFlushAttempts = 0;
            return;
        }

        var host = window.reactChatWindowHost;
        if (!host || typeof host.appendMessage !== 'function') {
            if (_yuiGuideChatFlushAttempts < YUI_GUIDE_CHAT_FLUSH_MAX_ATTEMPTS) {
                _yuiGuideChatFlushAttempts += 1;
                scheduleYuiGuideChatMessageFlush(100);
            } else {
                console.warn('[YuiGuide] Chat host was not ready; dropped guide chat messages:', _pendingYuiGuideChatMessages.length);
                _pendingYuiGuideChatMessages = [];
                _yuiGuideChatFlushAttempts = 0;
            }
            return;
        }

        _yuiGuideChatFlushAttempts = 0;
        var batch = _pendingYuiGuideChatMessages.splice(0);
        batch.forEach(function (message) {
            try {
                host.appendMessage(message);
            } catch (error) {
                console.warn('[YuiGuide] Failed to append guide chat message:', error);
            }
        });

        if (typeof host.openWindow === 'function') {
            try {
                host.openWindow();
            } catch (error) {
                console.warn('[YuiGuide] Failed to open guide chat window:', error);
            }
        }
    }

    I.appendYuiGuideChatMessage = function appendYuiGuideChatMessage(message) {
        if (!I.isStandaloneChatPage()) return;
        if (!message || typeof message !== 'object') return;
        _pendingYuiGuideChatMessages.push(message);
        scheduleYuiGuideChatMessageFlush(0);
    }

    function updatePendingYuiGuideChatMessage(messageId, patch) {
        var targetId = String(messageId || '');
        if (!targetId || !patch || typeof patch !== 'object') {
            return false;
        }

        var updated = false;
        _pendingYuiGuideChatMessages = _pendingYuiGuideChatMessages.map(function (message) {
            if (!message || String(message.id) !== targetId) {
                return message;
            }
            updated = true;
            return Object.assign({}, message, patch);
        });
        return updated;
    }

    I.updateYuiGuideChatMessage = function updateYuiGuideChatMessage(messageId, patch) {
        if (!I.isStandaloneChatPage()) return;
        if (!messageId || !patch || typeof patch !== 'object') return;

        var host = window.reactChatWindowHost;
        if (host && typeof host.updateMessage === 'function') {
            try {
                var handled = host.updateMessage(messageId, patch);
                if (handled) {
                    return;
                }
            } catch (error) {
                console.warn('[YuiGuide] Failed to update guide chat message:', error);
            }
        }

        if (updatePendingYuiGuideChatMessage(messageId, patch)) {
            scheduleYuiGuideChatMessageFlush(0);
        }
    }

    I.clearYuiGuideChatMessages = function clearYuiGuideChatMessages() {
        if (!I.isStandaloneChatPage()) return;
        _pendingYuiGuideChatMessages = [];
        if (_yuiGuideChatFlushTimer) {
            I.clearYuiGuideChatFlushTimer();
        }
        var host = window.reactChatWindowHost;
        if (host && typeof host.clearGuideMessages === 'function') {
            try {
                host.clearGuideMessages();
            } catch (error) {
                console.warn('[YuiGuide] Failed to clear guide chat messages:', error);
            }
        }
    }
    function handleYuiGuideChatBridgeData(data) {
        if (!data || !data.action) return false;
        switch (data.action) {
            case 'yui_guide_append_chat_message':
                if (I.isDuplicateMessage(data.action, data.timestamp)) return true;
                I.appendYuiGuideChatMessage(data.message);
                return true;
            case 'yui_guide_update_chat_message':
                if (I.isDuplicateMessage(data.action, data.timestamp)) return true;
                I.updateYuiGuideChatMessage(data.messageId, data.patch);
                return true;
            case 'yui_guide_clear_chat_messages':
                if (I.isDuplicateMessage(data.action, data.timestamp)) return true;
                I.clearYuiGuideChatMessages();
                return true;
            case 'yui_guide_set_chat_input_locked':
                if (I.isDuplicateMessage(data.action, data.timestamp)) return true;
                I.applyYuiGuideChatInputLocked(data.locked === true, data.reason || '');
                return true;
            case 'tutorial_chat_identity_override':
                if (I.isDuplicateMessage(data.action, data.timestamp)) return true;
                I.applyTutorialChatIdentityOverride(data);
                return true;
            default:
                return false;
        }
    }

    I.drainPendingYuiGuideChatBridgeQueue = function drainPendingYuiGuideChatBridgeQueue() {
        if (!I.isStandaloneChatPage()) return;
        var queue = [];
        try {
            var raw = localStorage.getItem(I.YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY);
            var parsed = raw ? JSON.parse(raw) : [];
            queue = Array.isArray(parsed) ? parsed.filter(Boolean) : [];
            localStorage.removeItem(I.YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY);
        } catch (error) {
            console.warn('[YuiGuide] 读取教程聊天消息缓存失败:', error);
            try {
                localStorage.removeItem(I.YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY);
            } catch (_) {}
        }
        queue.forEach(function (message) {
            handleYuiGuideChatBridgeData(message);
        });
    }

    function handleYuiGuideChatBridgeStorageEvent(event) {
        if (!event || event.key !== I.YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY || !event.newValue) return;
        I.drainPendingYuiGuideChatBridgeQueue();
    }

    var _pendingIcebreakerBridgeActions = [];
    var _icebreakerBridgeFlushTimer = null;
    var _icebreakerBridgeFlushAttempts = 0;
    var ICEBREAKER_BRIDGE_FLUSH_MAX_ATTEMPTS = 50;

    function scheduleIcebreakerBridgeFlush(delay) {
        if (_icebreakerBridgeFlushTimer) return;
        _icebreakerBridgeFlushTimer = I.yuiGuideInterpageResources.setTimeout(
            flushPendingIcebreakerBridgeActions,
            typeof delay === 'number' ? delay : 0
        );
    }

    I.clearIcebreakerBridgeFlushTimer = function clearIcebreakerBridgeFlushTimer() {
        if (!_icebreakerBridgeFlushTimer) return;
        I.yuiGuideInterpageResources.clearTimeout(_icebreakerBridgeFlushTimer);
        _icebreakerBridgeFlushTimer = null;
    }

    function queueIcebreakerBridgeAction(action) {
        if (!action || !action.type) return;
        _pendingIcebreakerBridgeActions.push(action);
        scheduleIcebreakerBridgeFlush(0);
    }

    function flushPendingIcebreakerBridgeActions() {
        _icebreakerBridgeFlushTimer = null;
        if (!_pendingIcebreakerBridgeActions.length) {
            _icebreakerBridgeFlushAttempts = 0;
            return;
        }

        var host = window.reactChatWindowHost;
        if (!host || typeof host.appendMessage !== 'function') {
            if (_icebreakerBridgeFlushAttempts < ICEBREAKER_BRIDGE_FLUSH_MAX_ATTEMPTS) {
                _icebreakerBridgeFlushAttempts += 1;
                scheduleIcebreakerBridgeFlush(100);
            } else {
                console.warn('[NewUserIcebreaker] Chat host was not ready; dropped bridge actions:', _pendingIcebreakerBridgeActions.length);
                _pendingIcebreakerBridgeActions = [];
                _icebreakerBridgeFlushAttempts = 0;
            }
            return;
        }

        _icebreakerBridgeFlushAttempts = 0;
        var batch = _pendingIcebreakerBridgeActions.splice(0);
        var shouldOpenHost = false;
        batch.forEach(function (action) {
            try {
                if (action.type === 'append' && action.message) {
                    shouldOpenHost = true;
                    return Promise.resolve(host.appendMessage(action.message)).then(function (result) {
                        if (!result) return result;
                        return waitForIcebreakerChatHostMounted(host).then(function () {
                            syncIcebreakerAssistantCompactCaption(action.message);
                            finalizeIcebreakerAssistantSubtitleTranslation(action.message);
                            return result;
                        });
                    }).catch(function (error) {
                        console.warn('[NewUserIcebreaker] Failed to append bridge message:', error);
                    });
                } else if (action.type === 'set_prompt' && action.prompt && typeof host.setIcebreakerChoicePrompt === 'function') {
                    host.setIcebreakerChoicePrompt(action.prompt);
                    shouldOpenHost = true;
                } else if (action.type === 'clear_prompt' && action.sessionId && typeof host.clearIcebreakerChoicePrompt === 'function') {
                    host.clearIcebreakerChoicePrompt(action.sessionId);
                } else if (action.type === 'clear_prompt_source'
                        && action.source === 'new_user_icebreaker'
                        && typeof host.clearChoicePromptBySource === 'function') {
                    host.clearChoicePromptBySource(action.source, action.reason || 'icebreaker-bridge');
                }
            } catch (error) {
                console.warn('[NewUserIcebreaker] Failed to apply bridge action:', action.type, error);
            }
        });
        if (shouldOpenHost && typeof host.openWindow === 'function') {
            try {
                host.openWindow();
            } catch (error) {
                console.warn('[NewUserIcebreaker] Failed to open chat host for bridge action:', error);
            }
        }
    }

    function appendIcebreakerChatMessage(message) {
        if (!I.isStandaloneChatPage()) return;
        queueIcebreakerBridgeAction({ type: 'append', message: message });
    }

    function setIcebreakerChoicePromptFromBroadcast(prompt) {
        if (!I.isStandaloneChatPage()) return;
        queueIcebreakerBridgeAction({ type: 'set_prompt', prompt: prompt });
    }

    function clearIcebreakerChoicePromptFromBroadcast(sessionId) {
        if (!I.isStandaloneChatPage()) return;
        queueIcebreakerBridgeAction({ type: 'clear_prompt', sessionId: String(sessionId || '') });
    }

    function clearIcebreakerChoicePromptSourceFromBroadcast(source, reason) {
        if (!I.isStandaloneChatPage()) return;
        if (String(source || '') !== 'new_user_icebreaker') return;
        queueIcebreakerBridgeAction({
            type: 'clear_prompt_source',
            source: String(source || ''),
            reason: String(reason || '')
        });
    }

    // Also defined in new-user-icebreaker.js for the local chat host path.
    function getIcebreakerMessageText(message) {
        var blocks = message && Array.isArray(message.blocks) ? message.blocks : [];
        for (var i = 0; i < blocks.length; i++) {
            if (blocks[i] && blocks[i].type === 'text') {
                var text = String(blocks[i].text || '').trim();
                if (text) return text;
            }
        }
        return '';
    }

    function syncIcebreakerAssistantCompactCaption(message) {
        if (!I.isStandaloneChatPage() || !message || message.role !== 'assistant') return;
        var line = getIcebreakerMessageText(message);
        if (!line) return;
        var turnId = String(message.turnId || message.id || ('icebreaker-turn-' + Date.now()));
        try {
            window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
                detail: {
                    turnId: turnId,
                    source: 'new_user_icebreaker'
                }
            }));
            window.dispatchEvent(new CustomEvent('neko-compact-caption-update', {
                detail: {
                    turnId: turnId,
                    segmentId: turnId + ':icebreaker',
                    text: line,
                    source: 'new_user_icebreaker'
                }
            }));
        } catch (error) {
            console.warn('[NewUserIcebreaker] compact caption sync failed:', error);
        }
    }

    function finalizeIcebreakerAssistantSubtitleTranslation(message) {
        if (!I.isStandaloneChatPage() || !message || message.role !== 'assistant') return;
        var line = getIcebreakerMessageText(message);
        if (!line) return;
        try {
            var bridge = window.subtitleBridge;
            if (!bridge || typeof bridge.finalizeTurnWithTranslation !== 'function') {
                return;
            }
            if (typeof bridge.beginTurn === 'function') {
                bridge.beginTurn({ latch: false });
            }
            var result = bridge.finalizeTurnWithTranslation(line);
            if (result && typeof result.catch === 'function') {
                result.catch(function (error) {
                    console.warn('[NewUserIcebreaker] subtitle translation failed:', error);
                });
            }
        } catch (error) {
            console.warn('[NewUserIcebreaker] subtitle translation failed:', error);
        }
    }

    function waitForIcebreakerChatHostMounted(host) {
        return new Promise(function (resolve) {
            var attempts = 0;
            function checkMounted() {
                var isMounted = false;
                try {
                    isMounted = !!(host && typeof host.isMounted === 'function' && host.isMounted());
                } catch (_) {}
                if (isMounted || attempts >= 100) {
                    I.yuiGuideInterpageResources.setTimeout(resolve, 0);
                    return;
                }
                attempts += 1;
                I.yuiGuideInterpageResources.setTimeout(checkMounted, 50);
            }
            checkMounted();
        });
    }

    I.isIcebreakerBridgeAction = function isIcebreakerBridgeAction(action) {
        return action === 'icebreaker_append_chat_message'
            || action === 'icebreaker_set_choice_prompt'
            || action === 'icebreaker_clear_choice_prompt'
            || action === 'icebreaker_clear_choice_prompt_source'
            || action === 'icebreaker_choice_selected'
            || action === 'icebreaker_free_text_submitted';
    }

    function isIcebreakerBridgeForCurrentLanlan(data) {
        if (!data || !data.lanlan_name) return false;
        var currentName = I.getCurrentLanlanName();
        return !!currentName && data.lanlan_name === currentName;
    }

    I.handleIcebreakerBridgeData = function handleIcebreakerBridgeData(data) {
        if (!data || !data.action) return false;
        if (!isIcebreakerBridgeForCurrentLanlan(data)) return false;
        switch (data.action) {
            case 'icebreaker_append_chat_message':
                if (I.isDuplicateMessage(data.action, data.timestamp)) return true;
                appendIcebreakerChatMessage(data.message);
                return true;
            case 'icebreaker_set_choice_prompt':
                if (I.isDuplicateMessage(data.action, data.timestamp)) return true;
                setIcebreakerChoicePromptFromBroadcast(data.prompt);
                return true;
            case 'icebreaker_clear_choice_prompt':
                if (I.isDuplicateMessage(data.action, data.timestamp)) return true;
                clearIcebreakerChoicePromptFromBroadcast(data.sessionId);
                return true;
            case 'icebreaker_clear_choice_prompt_source':
                if (I.isDuplicateMessage(data.action, data.timestamp)) return true;
                clearIcebreakerChoicePromptSourceFromBroadcast(data.source, data.reason);
                return true;
            case 'icebreaker_choice_selected':
                if (I.isDuplicateMessage(data.action, data.timestamp)) return true;
                if (!I.isStandaloneChatPage()) {
                    window.dispatchEvent(new CustomEvent('neko:icebreaker-choice-selected', {
                        detail: data.detail || data
                    }));
                }
                return true;
            case 'icebreaker_free_text_submitted':
                if (I.isDuplicateMessage(data.action, data.timestamp)) return true;
                if (!I.isStandaloneChatPage()) {
                    window.dispatchEvent(new CustomEvent('neko:icebreaker-free-text-submitted', {
                        detail: data.detail || data
                    }));
                }
                return true;
            default:
                return false;
        }
    }

    function handleIcebreakerStorageBridgeEvent(event) {
        if (!event || event.key !== I.ICEBREAKER_BRIDGE_STORAGE_KEY || !event.newValue) return;
        try {
            I.handleIcebreakerBridgeData(JSON.parse(event.newValue));
        } catch (error) {
            console.warn('[NewUserIcebreaker] storage bridge parse failed:', error);
        }
    }

    I.postIcebreakerBridgeEvent = function postIcebreakerBridgeEvent(action, payload) {
        var message = Object.assign({
            action: action,
            lanlan_name: I.getCurrentLanlanName(),
            timestamp: Date.now()
        }, payload || {});
        if (I.nekoBroadcastChannel && typeof I.nekoBroadcastChannel.postMessage === 'function') {
            try {
                I.nekoBroadcastChannel.postMessage(message);
            } catch (error) {
                console.warn('[NewUserIcebreaker] BroadcastChannel bridge post failed:', action, error);
            }
        }
        try {
            localStorage.setItem(I.ICEBREAKER_BRIDGE_STORAGE_KEY, JSON.stringify(message));
            setTimeout(function () {
                try {
                    localStorage.removeItem(I.ICEBREAKER_BRIDGE_STORAGE_KEY);
                } catch (_) {}
            }, 0);
        } catch (error) {
            console.warn('[NewUserIcebreaker] storage bridge post failed:', action, error);
        }
    }

    I.postIcebreakerChoiceSelected = function postIcebreakerChoiceSelected(payload) {
        I.postIcebreakerBridgeEvent('icebreaker_choice_selected', payload || {});
    }

    I.postIcebreakerFreeTextSubmitted = function postIcebreakerFreeTextSubmitted(payload) {
        I.postIcebreakerBridgeEvent('icebreaker_free_text_submitted', payload || {});
    }

    function relayYuiGuideMessageToNative(target, message) {
        var bridge = window.nekoTutorialOverlay;
        if (!bridge || !message || typeof message !== 'object') {
            return false;
        }

        try {
            if (target === 'pet' && typeof bridge.relayToPet === 'function') {
                bridge.relayToPet(message);
                return true;
            }
            if (target === 'chat' && typeof bridge.relayToChat === 'function') {
                bridge.relayToChat(message);
                return true;
            }
        } catch (_) {}
        return false;
    }

    function getYuiGuideBridgeCommandBus() {
        if (I.yuiGuideBridgeCommandBus) {
            return I.yuiGuideBridgeCommandBus;
        }
        if (
            window.YuiGuideCommon
            && typeof window.YuiGuideCommon.createTutorialBridgeCommandBus === 'function'
        ) {
            I.yuiGuideBridgeCommandBus = window.YuiGuideCommon.createTutorialBridgeCommandBus({
                window: window,
                channelProvider: function () {
                    return I.nekoBroadcastChannel || null;
                },
                nativeRelayProvider: function () {
                    return window.nekoTutorialOverlay || null;
                }
            });
        }
        return I.yuiGuideBridgeCommandBus;
    }

    function normalizeYuiGuideBridgeMessage(action, payload) {
        var message = action && typeof action === 'object'
            ? Object.assign({}, action)
            : Object.assign({}, payload || {}, {
                action: action
            });
        if (!message || !message.action) {
            return null;
        }
        if (!Number.isFinite(message.timestamp)) {
            message.timestamp = Date.now();
        }
        try {
            var canonicalRunId = I.resolveCanonicalYuiGuideBridgeRunId(message);
            if (canonicalRunId) {
                message.tutorialRunId = canonicalRunId;
                message.pcOverlayRunId = canonicalRunId;
            }
        } catch (_) {}
        return message;
    }

    I.postYuiGuideMessageToChat = function postYuiGuideMessageToChat(action, payload, options) {
        var bus = getYuiGuideBridgeCommandBus();
        var message = normalizeYuiGuideBridgeMessage(action, payload);
        if (!message) {
            return false;
        }
        if (bus && typeof bus.post === 'function') {
            return bus.post(message, options || {});
        }

        var posted = false;
        try {
            if (I.nekoBroadcastChannel && typeof I.nekoBroadcastChannel.postMessage === 'function') {
                I.nekoBroadcastChannel.postMessage(message);
                posted = true;
            }
        } catch (_) {}
        try {
            if (relayYuiGuideMessageToNative('chat', message)) {
                posted = true;
            }
        } catch (_) {}
        return posted;
    }

    I.postYuiGuideMessageToPet = function postYuiGuideMessageToPet(action, payload, options) {
        var bus = getYuiGuideBridgeCommandBus();
        if (bus && typeof bus.postToPet === 'function') {
            return bus.postToPet(action, payload, options || {});
        }

        var message = normalizeYuiGuideBridgeMessage(action, payload);
        if (!message) {
            return false;
        }
        var posted = false;
        try {
            if (I.nekoBroadcastChannel && typeof I.nekoBroadcastChannel.postMessage === 'function') {
                I.nekoBroadcastChannel.postMessage(message);
                posted = true;
            }
        } catch (_) {}
        try {
            if (relayYuiGuideMessageToNative('pet', message)) {
                posted = true;
            }
        } catch (_) {}
        return posted;
    }

    function handleYuiGuideRelayedMessage(message) {
        if (!message || !message.action) {
            return false;
        }
        if (I.isYuiGuideLifecycleStartAction(message.action)) {
            I.openYuiGuidePcOverlayLifecycle(message);
        }
        if (
            I.yuiGuidePcOverlayLifecycleClosed
            && I.isYuiGuideLifecycleScopedAction(message.action)
        ) {
            return true;
        }
        if (!I.isYuiGuideMessageForCurrentLifecycle(message)) {
            return true;
        }
        if (
            message.action !== 'yui_guide_tutorial_lifecycle_ended'
            && I.isYuiGuideLifecycleScopedAction(message.action)
            && I.isYuiGuidePcOverlayRunEnded(message.tutorialRunId)
        ) {
            I.clearYuiGuidePcOverlayBridgeState('stale-after-lifecycle-ended', message.tutorialRunId || '');
            return true;
        }
        if (message.tutorialRunId && message.action !== 'yui_guide_tutorial_lifecycle_ended') {
            I.rememberYuiGuidePcOverlayRunId(message.tutorialRunId);
        }

        switch (message.action) {
            case 'yui_guide_append_chat_message': {
                I.appendYuiGuideChatMessage(message.message);
                return true;
            }
            case 'yui_guide_update_chat_message': {
                I.updateYuiGuideChatMessage(message.messageId, message.patch);
                return true;
            }
            case 'yui_guide_clear_chat_messages': {
                I.clearYuiGuideChatMessages();
                return true;
            }
            case 'yui_guide_tutorial_lifecycle_ended': {
                I.clearYuiGuidePcOverlayBridgeState(message.reason || 'tutorial-ended', message.tutorialRunId || '');
                return true;
            }
            case 'yui_guide_set_chat_buttons_disabled': {
                if (!I.isStandaloneChatPage() || !document.body) return true;
                I.applyYuiGuideChatLockState(message.disabled !== false);
                return true;
            }
            case 'yui_guide_set_chat_input_locked': {
                if (!I.isStandaloneChatPage()) return true;
                I.applyYuiGuideChatInputLocked(message.locked === true, message.reason || '');
                return true;
            }
            case 'yui_guide_set_compact_chat_fixed_layout': {
                if (!I.isStandaloneChatPage()) return true;
                I.applyYuiGuideCompactChatFixedLayout(message.fixed === true);
                return true;
            }
            case 'yui_guide_prepare_compact_chat': {
                if (!I.isStandaloneChatPage()) return true;
                // 原生 relay 与 BroadcastChannel 共用同一准备函数，保证两条传输路径行为一致。
                I.prepareYuiGuideCompactChatSurface(message);
                return true;
            }
            case 'yui_guide_restore_compact_chat': {
                if (!I.isStandaloneChatPage()) return true;
                // 形态恢复留在胶囊窗口执行，避免主页猜测 Electron 的真实折叠状态。
                I.restoreYuiGuideCompactChatSurface(message);
                return true;
            }
            case 'yui_guide_set_chat_spotlight': {
                if (!I.isStandaloneChatPage() || !document.body) return true;
                I.ensureYuiGuideExternalChatExpanded();
                var preserveSpotlightDuringResistance = message.preserveDuringResistance === true;
                I.applyYuiGuideChatSpotlight(message.kind || '', {
                    variant: typeof message.variant === 'string' ? message.variant : '',
                    preserveDuringResistance: preserveSpotlightDuringResistance,
                    pcOverlayRunId: I.getYuiGuidePcOverlayRunIdFromMessage(message)
                });
                I.scheduleYuiGuideChatInputSpotlightRetry(message.kind || '', I.getYuiGuidePcOverlayRunIdFromMessage(message));
                return true;
            }
            case 'yui_guide_set_chat_cursor': {
                if (!I.isStandaloneChatPage() || !document.body) return true;
                var expandedForCursor = I.ensureYuiGuideExternalChatExpanded();
                var cursorKind = message.kind || '';
                var cursorOptions = {
                    effect: message.effect || '',
                    effectDurationMs: Number.isFinite(message.effectDurationMs)
                        ? Math.max(0, Math.floor(message.effectDurationMs))
                        : 0,
                    durationMs: Number.isFinite(message.durationMs)
                        ? Math.max(0, Math.floor(message.durationMs))
                        : null,
                    targetIndex: Number.isFinite(message.targetIndex)
                        ? message.targetIndex
                        : 0,
                    freezePoint: message.freezePoint === true,
                    preservePcOverlayCursor: message.preservePcOverlayCursor === true,
                    pcOverlayRunId: I.getYuiGuidePcOverlayRunIdFromMessage(message),
                    timestamp: I.getYuiGuideBridgeMessageTimestamp(message)
                };
                I.applyYuiGuideChatCursor(cursorKind, cursorOptions);
                // applyYuiGuideChatCursor owns the request generation. Capture its resulting token so
                // the post-expansion retry remains current until a newer cursor command supersedes it.
                var cursorRequestToken = I.yuiGuideChatCursorRequestToken;
                var retryCursorOptions = Object.assign({}, cursorOptions, {
                    effect: '',
                    effectDurationMs: 0
                });
                if (expandedForCursor && cursorOptions.freezePoint !== true) {
                    window.setTimeout(function () {
                        if (cursorRequestToken !== I.yuiGuideChatCursorRequestToken) {
                            return;
                        }
                        I.applyYuiGuideChatCursor(cursorKind, retryCursorOptions);
                    }, 720);
                }
                return true;
            }
            case 'yui_guide_chat_cursor_anchor': {
                if (I.isStandaloneChatPage()) return true;
                var anchorX = Number(message.x);
                var anchorY = Number(message.y);
                if (!Number.isFinite(anchorX) || !Number.isFinite(anchorY)) {
                    return true;
                }
                try {
                    window.localStorage.setItem(I.YUI_GUIDE_EXTERNAL_CHAT_CURSOR_SCREEN_POINT_KEY, JSON.stringify({
                        x: anchorX,
                        y: anchorY,
                        kind: typeof message.kind === 'string' ? message.kind : '',
                        effect: typeof message.effect === 'string' ? message.effect : '',
                        source: message.source || 'external-chat',
                        settled: message.settled === true,
                        at: message.timestamp || Date.now()
                    }));
                } catch (_) {}
                window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                    detail: {
                        x: anchorX,
                        y: anchorY,
                        kind: typeof message.kind === 'string' ? message.kind : '',
                        effect: typeof message.effect === 'string' ? message.effect : '',
                        source: message.source || 'external-chat',
                        settled: message.settled === true,
                        timestamp: message.timestamp || Date.now()
                    }
                }));
                return true;
            }
            case 'yui_guide_set_avatar_tool_menu_open': {
                if (!I.isStandaloneChatPage()) return true;
                I.ensureYuiGuideExternalChatExpanded();
                I.applyYuiGuideAvatarToolMenuOpen(message.open === true, message.reason || '');
                return true;
            }
            case 'yui_guide_click_avatar_tool_button': {
                if (!I.isStandaloneChatPage()) return true;
                I.ensureYuiGuideExternalChatExpanded();
                clickYuiGuideAvatarToolButton(message.reason || '');
                return true;
            }
            case 'yui_guide_set_compact_history_open': {
                if (!I.isStandaloneChatPage()) return true;
                I.ensureYuiGuideExternalChatExpanded();
                I.applyYuiGuideCompactHistoryOpen(message.open === true, message.reason || '');
                return true;
            }
            case 'yui_guide_set_compact_tool_fan_open': {
                if (!I.isStandaloneChatPage()) return true;
                I.ensureYuiGuideExternalChatExpanded();
                I.applyYuiGuideCompactToolFanOpen(message.open === true, message.reason || '');
                return true;
            }
            case 'yui_guide_rotate_compact_tool_wheel': {
                if (!I.isStandaloneChatPage()) return true;
                I.ensureYuiGuideExternalChatExpanded();
                I.applyYuiGuideCompactToolWheelRotate(message);
                return true;
            }
            case 'yui_guide_set_compact_tool_wheel_index': {
                if (!I.isStandaloneChatPage()) return true;
                I.ensureYuiGuideExternalChatExpanded();
                I.applyYuiGuideCompactToolWheelIndex(message);
                return true;
            }
            case 'yui_guide_drag_chat_cursor': {
                if (!I.isStandaloneChatPage()) return true;
                I.ensureYuiGuideExternalChatExpanded();
                I.applyYuiGuideChatCursorDrag(message.kind || '', {
                    deltaX: Number(message.deltaX || 0),
                    deltaY: Number(message.deltaY || 0),
                    durationMs: Number.isFinite(Number(message.durationMs)) ? Number(message.durationMs) : undefined,
                    effect: message.effect || '',
                    effectDurationMs: Number(message.effectDurationMs || 0),
                    targetIndex: Number(message.targetIndex || 0)
                });
                return true;
            }
            case 'yui_guide_arc_chat_cursor': {
                if (!I.isStandaloneChatPage()) return true;
                I.ensureYuiGuideExternalChatExpanded();
                I.applyYuiGuideChatCursorArc(message.kind || '', {
                    direction: Number(message.direction) < 0 ? -1 : 1,
                    fraction: Number.isFinite(Number(message.fraction)) ? Number(message.fraction) : 0.2,
                    durationMs: Number.isFinite(Number(message.durationMs)) ? Number(message.durationMs) : undefined,
                    effect: message.effect || '',
                    effectDurationMs: Number(message.effectDurationMs || 0),
                    targetIndex: Number(message.targetIndex || 0),
                    timestamp: I.getYuiGuideBridgeMessageTimestamp(message)
                });
                return true;
            }
            case 'yui_guide_chat_ready': {
                if (I.isStandaloneChatPage()) return true;
                window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-ready', {
                    detail: {
                        timestamp: message.timestamp || Date.now()
                    }
                }));
                return true;
            }
            case 'yui_guide_compact_chat_ready': {
                if (I.isStandaloneChatPage()) return true;
                // 主页只接受当前 requestId 的回执，重启教程时不会误用上一轮迟到消息。
                window.dispatchEvent(new CustomEvent('neko:yui-guide:compact-chat-ready', {
                    detail: message
                }));
                return true;
            }
            case 'yui_guide_request_termination': {
                window.dispatchEvent(new CustomEvent('neko:yui-guide:remote-termination-request', {
                    detail: {
                        sourcePage: message.sourcePage || '',
                        targetPage: message.targetPage || '',
                        reason: message.reason || 'skip',
                        tutorialReason: message.tutorialReason || message.reason || 'skip',
                        timestamp: message.timestamp || Date.now()
                    }
                }));
                return true;
            }
            default:
                return false;
        }
    }

    I.handleYuiGuideRelayedCustomEvent = function handleYuiGuideRelayedCustomEvent(event) {
        var message = event && event.detail;
        if (
            message
            && !I.shouldBypassYuiGuideMessageDedup(message.action, message)
            && I.isDuplicateMessage(message.action, message.timestamp)
        ) {
            return;
        }
        handleYuiGuideRelayedMessage(message);
    };

    I.handleYuiGuideRelayedWindowMessage = function handleYuiGuideRelayedWindowMessage(event) {
        var data = event && event.data;
        if (!data || data.__nekoTutorialOverlayRelay !== true) {
            return;
        }
        if (event.origin !== window.location.origin) {
            return;
        }
        var message = data.payload;
        if (
            message
            && !I.shouldBypassYuiGuideMessageDedup(message.action, message)
            && I.isDuplicateMessage(message.action, message.timestamp)
        ) {
            return;
        }
        handleYuiGuideRelayedMessage(message);
    };

    I.yuiGuideInterpageResources.addEventListener(window, 'storage', handleIcebreakerStorageBridgeEvent);
    I.yuiGuideInterpageResources.addEventListener(window, 'storage', handleYuiGuideChatBridgeStorageEvent);

    Object.assign(window.appInterpage, I.mod || {});
})();
