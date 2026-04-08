(function () {
    'use strict';

    const DESKTOP_PROVIDER_NAME = 'neko-pc';
    const BACKEND_PROVIDER_NAME = 'backend';
    const AUTOSTART_CSRF_HEADER_NAME = 'X-CSRF-Token';

    function getNavigatorPlatform() {
        if (navigator.userAgentData && navigator.userAgentData.platform) {
            return String(navigator.userAgentData.platform);
        }
        if (navigator.platform) {
            return String(navigator.platform);
        }
        return 'unknown';
    }

    async function parseJsonResponse(response) {
        if (!response || response.status === 204) {
            return null;
        }

        const contentType = response.headers.get('content-type') || '';
        if (!contentType.toLowerCase().includes('application/json')) {
            return null;
        }

        try {
            return await response.json();
        } catch (_) {
            return null;
        }
    }

    function buildRequestError(response, payload) {
        const error = new Error(
            payload && typeof payload.error === 'string' && payload.error
                ? payload.error
                : ('HTTP ' + response.status)
        );
        error.status = response.status;
        if (payload && typeof payload === 'object') {
            Object.assign(error, payload);
            error.payload = payload;
        }
        return error;
    }

    function requestJson(url, options) {
        const requestOptions = options || {};
        const hasJsonBody = Object.prototype.hasOwnProperty.call(requestOptions, 'json');
        const headers = Object.assign({}, requestOptions.headers);
        if (hasJsonBody && !headers['Content-Type']) {
            headers['Content-Type'] = 'application/json';
        }

        return fetch(url, {
            method: requestOptions.method || 'GET',
            headers: headers,
            body: hasJsonBody ? JSON.stringify(requestOptions.json || {}) : requestOptions.body,
            keepalive: !!requestOptions.keepalive,
            cache: requestOptions.cache,
        }).then(async function (response) {
            const payload = await parseJsonResponse(response);
            if (!response.ok) {
                throw buildRequestError(response, payload);
            }
            return payload;
        });
    }

    function waitForPageConfig() {
        if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
            return window.pageConfigReady.catch(function () {
                return null;
            });
        }
        return Promise.resolve(null);
    }

    function getAutostartCsrfToken() {
        return waitForPageConfig().then(function (pageConfig) {
            if (
                pageConfig
                && typeof pageConfig === 'object'
                && typeof pageConfig.autostart_csrf_token === 'string'
                && pageConfig.autostart_csrf_token
            ) {
                return pageConfig.autostart_csrf_token;
            }
            return '';
        });
    }

    function getAutostartMutationHeaders() {
        return getAutostartCsrfToken().then(function (csrfToken) {
            if (!csrfToken) {
                return {};
            }
            const headers = {};
            headers[AUTOSTART_CSRF_HEADER_NAME] = csrfToken;
            return headers;
        });
    }

    const mutationSecurityApi = {
        getCsrfToken: getAutostartCsrfToken,
        getMutationHeaders: getAutostartMutationHeaders,
    };
    window.nekoAutostartSecurity = mutationSecurityApi;
    window.nekoLocalMutationSecurity = mutationSecurityApi;

    // Desktop shells can inject window.nekoAutostart with getStatus/enable/disable methods.
    function getDesktopBridge() {
        const bridge = window.nekoAutostart;
        if (!bridge || typeof bridge !== 'object') {
            return null;
        }
        if (typeof bridge.getStatus !== 'function' || typeof bridge.enable !== 'function') {
            return null;
        }
        return bridge;
    }

    function normalizeResult(result, defaults) {
        const normalized = Object.assign({}, defaults);
        if (result && typeof result === 'object') {
            Object.assign(normalized, result);
        }

        normalized.provider = String(normalized.provider || defaults.provider || '');
        normalized.platform = String(normalized.platform || defaults.platform || getNavigatorPlatform());
        normalized.mechanism = String(normalized.mechanism || defaults.mechanism || '');
        normalized.supported = normalized.supported !== false;
        normalized.enabled = normalized.enabled === true;
        normalized.authoritative = normalized.authoritative === true;
        if (typeof normalized.ok !== 'boolean') {
            normalized.ok = true;
        }
        if (!normalized.supported) {
            normalized.enabled = false;
        }
        return normalized;
    }

    function getBackendDefaults() {
        return {
            ok: true,
            supported: false,
            enabled: false,
            provider: BACKEND_PROVIDER_NAME,
            mechanism: 'backend-api',
            platform: getNavigatorPlatform(),
            authoritative: true,
        };
    }

    function getDesktopDefaults() {
        return {
            ok: true,
            supported: true,
            enabled: false,
            provider: DESKTOP_PROVIDER_NAME,
            mechanism: 'desktop-bridge',
            platform: getNavigatorPlatform(),
            authoritative: true,
        };
    }

    function getBackendStatus() {
        return requestJson('/api/system/autostart/status', {
            cache: 'no-store',
        }).then(function (result) {
            return normalizeResult(result, getBackendDefaults());
        });
    }

    function enableBackendAutostart() {
        return getAutostartMutationHeaders().then(function (headers) {
            return requestJson('/api/system/autostart/enable', {
                method: 'POST',
                headers: headers,
                json: {},
            });
        }).then(function (result) {
            return normalizeResult(result, getBackendDefaults());
        });
    }

    function disableBackendAutostart() {
        return getAutostartMutationHeaders().then(function (headers) {
            return requestJson('/api/system/autostart/disable', {
                method: 'POST',
                headers: headers,
                json: {},
            });
        }).then(function (result) {
            return normalizeResult(result, getBackendDefaults());
        });
    }

    function getStatus() {
        const bridge = getDesktopBridge();
        if (!bridge) {
            return getBackendStatus();
        }

        return Promise.resolve().then(function () {
            return bridge.getStatus();
        }).then(function (result) {
            return normalizeResult(result, getDesktopDefaults());
        });
    }

    function enable() {
        const bridge = getDesktopBridge();
        if (!bridge) {
            return enableBackendAutostart();
        }

        return Promise.resolve().then(function () {
            return bridge.enable();
        }).then(function (result) {
            return normalizeResult(result, getDesktopDefaults());
        });
    }

    function disable() {
        const bridge = getDesktopBridge();
        if (!bridge) {
            return disableBackendAutostart();
        }
        if (typeof bridge.disable !== 'function') {
            return Promise.reject(new Error('autostart_disable_not_supported'));
        }

        return Promise.resolve().then(function () {
            return bridge.disable();
        }).then(function (result) {
            return normalizeResult(result, getDesktopDefaults());
        });
    }

    const existingProvider = window.nekoAutostartProvider;
    if (
        existingProvider
        && typeof existingProvider.getStatus === 'function'
        && typeof existingProvider.enable === 'function'
    ) {
        return;
    }

    window.nekoAutostartProvider = {
        getStatus: getStatus,
        enable: enable,
        disable: disable,
    };
})();
