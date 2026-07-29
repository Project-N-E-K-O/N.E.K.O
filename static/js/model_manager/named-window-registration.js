(function registerModelManagerNamedWindow() {
    'use strict';

    const MODEL_MANAGER_SINGLETON_WINDOW_NAME = 'neko_model_manager_singleton';
    if (window.opener === null || window.name !== MODEL_MANAGER_SINGLETON_WINDOW_NAME) return;

    const registryKey = `neko:named-window:${MODEL_MANAGER_SINGLETON_WINDOW_NAME}`;
    const focusKey = `neko:named-window-focus:${MODEL_MANAGER_SINGLETON_WINDOW_NAME}`;
    const channelName = 'neko:named-window';
    const MODEL_MANAGER_VISIBILITY_HEARTBEAT_MS = 400;
    const MODEL_MANAGER_ELECTRON_VISIBILITY_HEARTBEAT_MS = 1000;
    const MODEL_MANAGER_WINDOW_INSTANCE_ID = `model-manager-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    let heartbeat = null;
    let channel = null;
    let visibilityHeartbeat = null;
    let visibilityTrackingActive = false;

    function markModelManagerNamedWindowActive() {
        try {
            window.localStorage.setItem(registryKey, JSON.stringify({
                url: window.location.href,
                timestamp: Date.now(),
            }));
        } catch (_) {}
    }

    function clearModelManagerNamedWindowActive() {
        try {
            window.localStorage.removeItem(registryKey);
        } catch (_) {}
    }

    function restoreModelManagerNamedWindowIfMinimized() {
        const api = window.nekoWindowControl;
        if (api && typeof api.restoreIfMinimized === 'function') {
            Promise.resolve(api.restoreIfMinimized()).catch(() => {});
            return;
        }
        try {
            if (document.hidden === true) window.focus();
        } catch (_) {}
    }

    function handleModelManagerNamedWindowMessage(data) {
        if (!data || data.windowName !== MODEL_MANAGER_SINGLETON_WINDOW_NAME) return;
        if (data.type === 'neko:named-window-focus' ||
            data.type === 'neko:named-window-message') {
            restoreModelManagerNamedWindowIfMinimized();
        }
    }

    function startModelManagerNamedWindowRegistration() {
        markModelManagerNamedWindowActive();
        if (!heartbeat) {
            heartbeat = setInterval(markModelManagerNamedWindowActive, 1000);
        }
        if (!channel && typeof BroadcastChannel !== 'undefined') {
            try {
                channel = new BroadcastChannel(channelName);
                channel.onmessage = event => handleModelManagerNamedWindowMessage(event.data);
            } catch (_) {
                channel = null;
            }
        }
    }

    function stopModelManagerNamedWindowRegistration() {
        clearModelManagerNamedWindowActive();
        if (heartbeat) {
            clearInterval(heartbeat);
            heartbeat = null;
        }
        if (channel) {
            try {
                channel.close();
            } catch (_) {}
            channel = null;
        }
    }

    function getModelManagerWindowScreenBounds() {
        const x = Number(window.screenX);
        const y = Number(window.screenY);
        const width = Number(window.outerWidth);
        const height = Number(window.outerHeight);
        if (!Number.isFinite(x) || !Number.isFinite(y) ||
            !Number.isFinite(width) || !Number.isFinite(height) ||
            width <= 0 || height <= 0) {
            return null;
        }
        return { x, y, width, height };
    }

    function isModelManagerWindowForeground() {
        return !document.hidden &&
            (typeof document.hasFocus !== 'function' || document.hasFocus());
    }

    function publishModelManagerWindowState(active) {
        const hostBridge = window.nekoModelManagerVisibility;
        if (hostBridge && typeof hostBridge.setActive === 'function') {
            try {
                hostBridge.setActive(!!active);
                return;
            } catch (error) {
                console.warn('[模型管理] Electron 遮挡状态桥接失败，改用页面通信:', error);
            }
        }
        if (typeof window.sendMessageToMainPage !== 'function') return;
        window.sendMessageToMainPage('model_manager_window_state', {
            instanceId: MODEL_MANAGER_WINDOW_INSTANCE_ID,
            active: !!active,
            visible: !!active && isModelManagerWindowForeground(),
            bounds: active ? getModelManagerWindowScreenBounds() : null,
        });
    }

    function stopModelManagerVisibilityTracking() {
        if (!visibilityTrackingActive) return;
        visibilityTrackingActive = false;
        if (visibilityHeartbeat) {
            clearInterval(visibilityHeartbeat);
            visibilityHeartbeat = null;
        }
        publishModelManagerWindowState(false);
    }

    function startModelManagerVisibilityTracking() {
        if (visibilityTrackingActive) return;
        visibilityTrackingActive = true;
        publishModelManagerWindowState(true);
        const hasElectronBridge = !!(
            window.nekoModelManagerVisibility
            && typeof window.nekoModelManagerVisibility.setActive === 'function'
        );
        visibilityHeartbeat = setInterval(() => {
            if (!visibilityTrackingActive) return;
            publishModelManagerWindowState(true);
        }, hasElectronBridge
            ? MODEL_MANAGER_ELECTRON_VISIBILITY_HEARTBEAT_MS
            : MODEL_MANAGER_VISIBILITY_HEARTBEAT_MS);
    }

    window.addEventListener('storage', event => {
        if (event.key !== focusKey || !event.newValue) return;
        try {
            handleModelManagerNamedWindowMessage(JSON.parse(event.newValue));
        } catch (_) {}
    });
    window.addEventListener('pageshow', () => {
        startModelManagerNamedWindowRegistration();
        startModelManagerVisibilityTracking();
    });
    window.addEventListener('pagehide', () => {
        stopModelManagerVisibilityTracking();
        stopModelManagerNamedWindowRegistration();
    });
    document.addEventListener('visibilitychange', () => {
        if (visibilityTrackingActive) publishModelManagerWindowState(true);
    });
    window.addEventListener('focus', () => {
        if (visibilityTrackingActive) publishModelManagerWindowState(true);
    });
    window.addEventListener('blur', () => {
        if (visibilityTrackingActive) publishModelManagerWindowState(true);
    });
    startModelManagerNamedWindowRegistration();
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', startModelManagerVisibilityTracking, { once: true });
    } else {
        startModelManagerVisibilityTracking();
    }
})();
