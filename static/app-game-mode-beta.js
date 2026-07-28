/**
 * app-game-mode-beta.js -- frontend bridge for game resource protection.
 */
(function () {
    'use strict';

    const API_STATE = '/api/game-mode-beta/state';
    const API_ENABLED = '/api/game-mode-beta/enabled';
    const API_SETTINGS = '/api/game-mode-beta/settings';
    const API_REGISTER_WINDOW = '/api/game-mode-beta/windows/register';
    const API_UNREGISTER_WINDOW = '/api/game-mode-beta/windows/unregister';

    const clientState = {
        enabled: false,
        backendState: null,
        settings: {
            resource_protection_on_game: true,
            compact_pet_window_enabled: true,
        },
        hostContract: null,
        registrationTimer: 0,
    };

    function t(key, fallback, params) {
        let text = fallback;
        try {
            if (typeof window.t === 'function') {
                text = window.t(key, Object.assign({ defaultValue: fallback }, params || {}));
            }
        } catch (_) {}
        if (params) {
            return String(text || fallback).replace(/\{(\w+)\}/g, function (_, name) {
                return Object.prototype.hasOwnProperty.call(params, name) ? params[name] : _;
            });
        }
        return text || fallback;
    }

    function showNotice(message) {
        if (!message) return;
        if (typeof window.showStatusToast === 'function') {
            window.showStatusToast(message, 6000, { priority: 70 });
            return;
        }
        try { window.alert(message); } catch (_) {}
    }

    function normalizeSettings(settings) {
        const source = settings && typeof settings === 'object' ? settings : {};
        return {
            resource_protection_on_game: source.resource_protection_on_game !== false,
            compact_pet_window_enabled: source.compact_pet_window_enabled !== false,
        };
    }

    function dispatchState() {
        try {
            window.dispatchEvent(new CustomEvent('neko:game-mode-beta-state', {
                detail: getState(),
            }));
        } catch (_) {}
        syncVisibleToggles();
    }

    function syncVisibleToggles() {
        try {
            document.querySelectorAll('input[id$="-game-mode-beta"]').forEach(function (checkbox) {
                if (!checkbox) return;
                const item = checkbox.closest('[role="switch"]');
                if (item && typeof item._nekoUpdateGameModeBetaStatus === 'function') {
                    item._nekoUpdateGameModeBetaStatus();
                }
                if (checkbox.checked === clientState.enabled) return;
                checkbox.checked = clientState.enabled;
                if (item && typeof item._nekoUpdateSettingsToggleStyle === 'function') {
                    item._nekoUpdateSettingsToggleStyle();
                }
            });
        } catch (_) {}
    }

    function applyBackendState(state) {
        if (!state || typeof state !== 'object') return;
        clientState.backendState = state;
        clientState.enabled = state.enabled === true;
        if (state.settings && typeof state.settings === 'object') {
            clientState.settings = normalizeSettings(state.settings);
        }
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
            console.warn('[GameModeBeta] mutation security headers unavailable:', error);
        }
        return headers;
    }

    async function postJson(url, payload, options) {
        const response = await fetch(url, {
            method: 'POST',
            headers: await getMutationHeaders(),
            body: JSON.stringify(payload || {}),
            keepalive: !!(options && options.keepalive),
        });
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return await response.json().catch(function () { return {}; });
    }

    async function refreshSettings() {
        try {
            const response = await fetch(API_SETTINGS, { cache: 'no-store' });
            if (!response.ok) return null;
            clientState.settings = normalizeSettings(await response.json());
            dispatchState();
            return Object.assign({}, clientState.settings);
        } catch (error) {
            console.warn('[GameModeBeta] settings refresh failed:', error);
            return null;
        }
    }

    async function setSettings(settings) {
        const next = normalizeSettings(Object.assign({}, clientState.settings, settings || {}));
        try {
            const response = await fetch(API_SETTINGS, {
                method: 'POST',
                headers: await getMutationHeaders(),
                body: JSON.stringify(next),
            });
            if (!response.ok) throw new Error('HTTP ' + response.status);
            clientState.settings = normalizeSettings(await response.json());
            dispatchState();
            return true;
        } catch (error) {
            console.warn('[GameModeBeta] settings update failed:', error);
            await refreshSettings();
            return false;
        }
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
            console.warn('[GameModeBeta] state refresh failed:', error);
        }
        return null;
    }

    async function setEnabled(enabled) {
        const next = enabled === true;
        try {
            const response = await fetch(API_ENABLED, {
                method: 'POST',
                headers: await getMutationHeaders(),
                body: JSON.stringify({ enabled: next }),
            });
            const data = await response.json().catch(function () { return null; });
            if (!response.ok || !data || data.success !== true) {
                throw new Error((data && (data.error || data.detail)) || ('HTTP ' + response.status));
            }
            applyBackendState(data.state);
            showNotice(t(
                next ? 'settings.gameModeBeta.enabledNotice' : 'settings.gameModeBeta.disabledNotice',
                next
                    ? '游戏资源保护已开启；Side Mode 仍可通过拖动 Live2D 到屏幕边缘使用。'
                    : '游戏资源保护已关闭。'
            ));
            return true;
        } catch (error) {
            console.warn('[GameModeBeta] toggle failed:', error);
            showNotice(t('settings.gameModeBeta.toggleFailed', '游戏资源保护切换失败，请稍后重试。'));
            await refreshState();
            return false;
        }
    }

    function getStatusText() {
        const status = clientState.enabled
            ? t('settings.gameModeBeta.statusOn', '开启')
            : t('settings.gameModeBeta.statusOff', '关闭');
        return t('settings.gameModeBeta.statusOnly', '{status}', { status: status });
    }

    async function registerHostWindow() {
        if (!window.nekoGameModeHost || typeof window.nekoGameModeHost.getContract !== 'function') return null;
        try {
            const contract = await window.nekoGameModeHost.getContract();
            if (!contract || contract.windowType !== 'pet' || !contract.petInstanceId) return null;
            clientState.hostContract = contract;
            const registration = await postJson(API_REGISTER_WINDOW, {
                pet_instance_id: contract.petInstanceId,
                window_type: contract.windowType,
                signal_capabilities: contract.signalCapabilities || {},
                host_capabilities: contract.hostCapabilities || {},
            });
            try {
                window.dispatchEvent(new CustomEvent('neko:game-mode-resource-registration', {
                    detail: Object.assign({ pet_instance_id: contract.petInstanceId }, registration || {}),
                }));
            } catch (_) {}
            return contract;
        } catch (error) {
            console.warn('[GameModeBeta] host registration failed:', error);
            return null;
        }
    }

    function startHostRegistration() {
        if (!window.nekoGameModeHost) return;
        void registerHostWindow();
        if (clientState.registrationTimer) clearInterval(clientState.registrationTimer);
        clientState.registrationTimer = setInterval(registerHostWindow, 10000);
        if (typeof window.nekoGameModeHost.onSystemResume === 'function') {
            window.nekoGameModeHost.onSystemResume(function () { void registerHostWindow(); });
        }
    }

    function bindEvents() {
        window.addEventListener('neko:websocket-connection-state', function (event) {
            const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            if (detail.connected === true) void registerHostWindow();
        });
        window.addEventListener('beforeunload', function () {
            const host = clientState.hostContract;
            if (!host) return;
            void postJson(API_UNREGISTER_WINDOW, {
                pet_instance_id: host.petInstanceId,
            }, { keepalive: true }).catch(function () {});
        });
    }

    function getState() {
        return {
            enabled: clientState.enabled,
            settings: Object.assign({}, clientState.settings),
            hostContract: clientState.hostContract,
            backendState: clientState.backendState,
        };
    }

    window.nekoGameModeBeta = {
        refreshState: refreshState,
        setEnabled: setEnabled,
        isEnabled: function () { return clientState.enabled === true; },
        getStatusText: getStatusText,
        refreshSettings: refreshSettings,
        setSettings: setSettings,
        getSettings: function () { return Object.assign({}, clientState.settings); },
        registerHostWindow: registerHostWindow,
        getState: getState,
    };

    bindEvents();
    const initGameModeBeta = function () {
        void refreshState();
        startHostRegistration();
    };
    if (document.readyState === 'loading') {
        window.addEventListener('DOMContentLoaded', initGameModeBeta, { once: true });
    } else {
        initGameModeBeta();
    }
})();
