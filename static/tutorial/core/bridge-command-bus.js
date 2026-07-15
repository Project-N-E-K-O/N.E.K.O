(function (root, factory) {
    'use strict';

    const api = factory(root);
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialBridgeCommandBus = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function (root) {
    'use strict';

    const DEFAULT_BRIDGE_QUEUE_KEY = 'neko_yui_guide_chat_bridge_queue_v1';
    const DEFAULT_BRIDGE_QUEUE_LIMIT = 160;
    const DEFAULT_PC_OVERLAY_RUN_ID_KEY = 'yuiGuidePcOverlayRunId';
    const QUEUED_BRIDGE_ACTIONS = Object.freeze({
        yui_guide_append_chat_message: true,
        yui_guide_update_chat_message: true,
        yui_guide_clear_chat_messages: true,
        yui_guide_set_chat_input_locked: true,
        tutorial_chat_identity_override: true
    });

    function getConsole(win) {
        return (win && win.console) || (root && root.console) || {
            warn() {}
        };
    }

    function getBridgeTimestamp(win) {
        const dateProvider = win && win.Date && typeof win.Date.now === 'function'
            ? win.Date
            : Date;
        return dateProvider.now();
    }

    function normalizeBridgeMessage(message, win, runIdStorageKey, options) {
        if (!message || typeof message !== 'object' || !message.action) {
            return null;
        }
        const normalizedOptions = options || {};
        const outgoingMessage = Object.assign({}, message);
        if (!outgoingMessage.timestamp) {
            outgoingMessage.timestamp = getBridgeTimestamp(win);
        }
        if (normalizedOptions.bypassDedup === true) {
            outgoingMessage.bypassDedup = true;
        }
        try {
            const tutorialRunId = win && win.localStorage
                ? win.localStorage.getItem(runIdStorageKey)
                : '';
            if (tutorialRunId && !outgoingMessage.tutorialRunId) {
                outgoingMessage.tutorialRunId = tutorialRunId;
            }
        } catch (_) {}
        return outgoingMessage;
    }

    function createTutorialBridgeCommandBus(options) {
        const normalizedOptions = options || {};
        const win = normalizedOptions.window || root;
        const consoleApi = getConsole(win);
        const storageKey = normalizedOptions.storageKey || DEFAULT_BRIDGE_QUEUE_KEY;
        const queueLimit = Number.isFinite(normalizedOptions.queueLimit)
            ? Math.max(1, Math.floor(normalizedOptions.queueLimit))
            : DEFAULT_BRIDGE_QUEUE_LIMIT;
        const runIdStorageKey = normalizedOptions.runIdStorageKey || DEFAULT_PC_OVERLAY_RUN_ID_KEY;
        const channelProvider = typeof normalizedOptions.channelProvider === 'function'
            ? normalizedOptions.channelProvider
            : () => null;
        const nativeRelayProvider = typeof normalizedOptions.nativeRelayProvider === 'function'
            ? normalizedOptions.nativeRelayProvider
            : () => null;
        const subscriptions = [];

        function readQueue() {
            try {
                const raw = win && win.localStorage ? win.localStorage.getItem(storageKey) : '';
                const parsed = raw ? JSON.parse(raw) : [];
                return Array.isArray(parsed) ? parsed.filter(Boolean) : [];
            } catch (_) {
                return [];
            }
        }

        function enqueue(message) {
            if (!message || typeof message !== 'object' || !QUEUED_BRIDGE_ACTIONS[message.action]) {
                return;
            }
            try {
                const queue = readQueue();
                queue.push(message);
                const trimmed = queue.slice(-queueLimit);
                if (win && win.localStorage) {
                    win.localStorage.setItem(storageKey, JSON.stringify(trimmed));
                }
            } catch (error) {
                consoleApi.warn('[YuiGuide] 缓存教程聊天消息失败:', error);
            }
        }

        function clearQueue() {
            try {
                if (win && win.localStorage) {
                    win.localStorage.removeItem(storageKey);
                }
            } catch (error) {
                consoleApi.warn('[YuiGuide] 清理教程聊天消息缓存失败:', error);
            }
        }

        function post(message, options) {
            const outgoingMessage = normalizeBridgeMessage(message, win, runIdStorageKey, options);
            if (!outgoingMessage) {
                return false;
            }

            enqueue(outgoingMessage);

            let posted = false;
            const channel = channelProvider();
            if (channel && typeof channel.postMessage === 'function') {
                try {
                    channel.postMessage(outgoingMessage);
                    posted = true;
                } catch (error) {
                    consoleApi.warn('[YuiGuide] BroadcastChannel 转发独立聊天窗消息失败:', error);
                }
            }

            const nativeRelay = nativeRelayProvider();
            if (nativeRelay && typeof nativeRelay.relayToChat === 'function') {
                try {
                    nativeRelay.relayToChat(outgoingMessage);
                    posted = true;
                } catch (error) {
                    consoleApi.warn('[YuiGuide] PC 原生转发独立聊天窗消息失败:', error);
                }
            }

            return posted;
        }

        function createBridgeMessage(action, payload) {
            if (action && typeof action === 'object') {
                return Object.assign({}, action);
            }
            if (typeof action !== 'string' || !action) {
                return null;
            }
            return Object.assign({}, payload || {}, {
                action
            });
        }

        function postToPet(action, payload, options) {
            const outgoingMessage = normalizeBridgeMessage(
                createBridgeMessage(action, payload),
                win,
                runIdStorageKey,
                options
            );
            if (!outgoingMessage) {
                return false;
            }
            let posted = false;
            const channel = channelProvider();
            if (channel && typeof channel.postMessage === 'function') {
                try {
                    channel.postMessage(outgoingMessage);
                    posted = true;
                } catch (error) {
                    consoleApi.warn('[YuiGuide] BroadcastChannel 转发 Pet 教程消息失败:', error);
                }
            }
            const nativeRelay = nativeRelayProvider();
            if (nativeRelay && typeof nativeRelay.relayToPet === 'function') {
                try {
                    nativeRelay.relayToPet(outgoingMessage);
                    posted = true;
                } catch (error) {
                    consoleApi.warn('[YuiGuide] PC 原生转发 Pet 教程消息失败:', error);
                }
            }
            return posted;
        }

        function on(action, handler) {
            const normalizedAction = typeof action === 'string' ? action : '';
            const normalizedHandler = typeof handler === 'function' ? handler : null;
            const channel = channelProvider();
            if (!normalizedAction || !normalizedHandler || !channel) {
                return function noopUnsubscribe() {};
            }
            const listener = function handleBridgeMessage(event) {
                const message = event && event.data ? event.data : event;
                if (message && message.action === normalizedAction) {
                    normalizedHandler(message);
                }
            };
            if (typeof channel.addEventListener === 'function') {
                channel.addEventListener('message', listener);
                const unsubscribe = function unsubscribeBridgeMessage() {
                    if (typeof channel.removeEventListener === 'function') {
                        channel.removeEventListener('message', listener);
                    }
                };
                subscriptions.push(unsubscribe);
                return unsubscribe;
            }
            const previousHandler = channel.onmessage;
            const wrappedHandler = function handleOnMessage(event) {
                if (typeof previousHandler === 'function') {
                    previousHandler.call(channel, event);
                }
                listener(event);
            };
            channel.onmessage = wrappedHandler;
            const unsubscribe = function unsubscribeBridgeOnMessage() {
                if (channel.onmessage === wrappedHandler) {
                    channel.onmessage = previousHandler || null;
                }
            };
            subscriptions.push(unsubscribe);
            return unsubscribe;
        }

        function destroy() {
            while (subscriptions.length) {
                const unsubscribe = subscriptions.pop();
                try {
                    unsubscribe();
                } catch (error) {
                    consoleApi.warn('[YuiGuide] cleanup bridge subscription failed:', error);
                }
            }
        }

        return {
            readQueue,
            enqueue,
            clearQueue,
            post,
            postToChat: post,
            postToPet,
            on,
            destroy,
            dispose: destroy
        };
    }

    return {
        createTutorialBridgeCommandBus
    };
});
